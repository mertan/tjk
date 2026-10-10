import { createHash } from 'node:crypto';
import { buildExamples, inferFormOrder } from '../model-v2/features.mjs';
import { scores, softmax, train } from '../model-v2/model.mjs';
import { buildExtras } from './history-features.mjs';
import { FEATURE_NAMES_V3 } from './features.mjs';
import { marketGate, marketProbabilities, fitTemperature, calibrationStats } from './market.mjs';
import { ARTIFACT_SCHEMA_V3, historyDigest, validateArtifact, validateHistory } from './live-inference.mjs';

export const EVIDENCE_SCHEMA_V3 = 'tjk-v3-training-evidence/1';
export const DEFAULT_TRAINING_PARAMS = Object.freeze({ l2: 30, epochs: 300, lr: 0.05 });
const record = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const finite = Number.isFinite;
const date = (v) => {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const at = Date.parse(v + 'T00:00:00Z');
  return finite(at) && new Date(at).toISOString().slice(0, 10) === v;
};
const sourceDay = (at) => finite(at) && at > 0 && at < 8e15
  ? new Date(at + 3 * 3_600_000).toISOString().slice(0, 10) : null;
const raceId = (r) => [r.date, r.venue, r.number].join('|');
const unique = (values) => [...new Set(values)];
const pas = (...reasons) => ({ status: 'PAS', reasonCodes: unique(reasons.flat()) });

/** Ordered parsed JSON digest; file-byte SHA-256 belongs to the CLI envelope. */
export function jsonDigest(value) {
  const text = JSON.stringify(value);
  if (typeof text !== 'string') throw new Error('V3_TRAINING_EVIDENCE_INVALID');
  return createHash('sha256').update(text).digest('hex');
}

function parameters(value) {
  if (!record(value) || Object.keys(value).some((key) => !Object.hasOwn(DEFAULT_TRAINING_PARAMS, key))) return null;
  const result = { ...DEFAULT_TRAINING_PARAMS, ...value };
  if (!finite(result.l2) || result.l2 < 0 || result.l2 > 1e6
    || !Number.isSafeInteger(result.epochs) || result.epochs < 1 || result.epochs > 10000
    || !finite(result.lr) || result.lr <= 0 || result.lr > 1) return null;
  return result;
}

const optionalNumber = (value, minimum = 0) => value === null || value === undefined
  || (finite(value) && value >= minimum);
const optionalText = (value) => value === null || value === undefined || typeof value === 'string';
/** Reject coercible strings before feature builders perform addition or arithmetic.
 * Missing measurements remain absent; shared builders retain their missing flags.
 */
function validScalars(race) {
  if (!optionalNumber(race.distance) || !optionalNumber(race.prize1)
    || !optionalText(race.surface) || !optionalText(race.time)) return false;
  return race.runners.every((runner) => ['rating', 'weight', 'extraWeight', 'stall', 'age', 'daysOff', 'bestTime']
    .every((name) => optionalNumber(runner[name]))
    && ['last6', 'jockey', 'trainer'].every((name) => optionalText(runner[name]))
    && !(runner.scratched && runner.position !== null && runner.position !== undefined));
}

function quoteReasons(race, example, quote) {
  const reasons = [...marketGate(example.numbers, quote).reasons];
  const validProgram = finite(race.distance) && race.distance > 0
    && ['Kum', 'Çim', 'Sentetik'].includes(race.surface)
    && /^([01]\d|2[0-3]):[0-5]\d$/.test(race.time || '')
    && (race.prize1 === null || race.prize1 === undefined || (finite(race.prize1) && race.prize1 > 0));
  if (!validProgram) reasons.push('V3_PROGRAM_INVALID');
  const post = validProgram ? Date.parse(race.date + 'T' + race.time + ':00+03:00') : null;
  if (record(quote)) {
    if (post === null || quote.cutoffMs !== post - 5 * 60_000) reasons.push('V3_QUOTE_CUTOFF_MISMATCH');
    if (quote.postMs !== undefined && quote.postMs !== post) reasons.push('V3_QUOTE_POST_MISMATCH');
    if (sourceDay(quote.lastLabelAt) !== race.date) reasons.push('V3_SOURCE_TIMESTAMP_INVALID');
    if (quote.reasonCodes !== undefined
      && (!Array.isArray(quote.reasonCodes) || quote.reasonCodes.length !== 0)) reasons.push('V3_QUOTE_SOURCE_INVALID');
    const known = new Set(race.runners.map((runner) => String(runner.number)));
    if (record(quote.runners)) {
      if (Object.keys(quote.runners).some((key) => !known.has(key))) reasons.push('V3_QUOTE_RUNNER_MISMATCH');
      for (const number of example.numbers) {
        if (sourceDay(quote.runners[number]?.at) !== race.date) reasons.push('V3_QUOTE_TIMESTAMP_INVALID');
      }
    }
  }
  return unique(reasons);
}

function windowFor(examples) {
  const dates = examples.map((example) => example.date).sort();
  return { from: dates[0], through: dates.at(-1), races: examples.length };
}
function exampleDigest(examples) {
  return jsonDigest(examples.map(({ id, numbers, X, positions }) => ({ id, numbers, X, positions })));
}
function proofWindow(examples) {
  return { ...windowFor(examples), ids: examples.map((example) => example.id), examplesSha256: exampleDigest(examples) };
}

/**
 * Fresh conditional-logit fit on earlier dates, then temperature fit on a later
 * complete-date holdout. Uses the evaluate-v3 shared feature/model functions.
 * Never imports or executes an unknown local trainer, reads files or starts a service.
 * asOf is exclusive and cannot exceed the current Istanbul calendar day.
 */
export function prepareTraining({
  history, odds, asOf, calibrationFrom, now = Date.now(), params = {}
} = {}) {
  try {
    const fitParams = parameters(params);
    const today = sourceDay(now);
    if (!today || !date(asOf) || asOf > today || !date(calibrationFrom)
      || calibrationFrom >= asOf || !fitParams) return pas('V3_TRAINING_ARGUMENTS_INVALID');
    if (!Array.isArray(history) || history.length === 0) return pas('V3_HISTORY_MISSING');
    // Validate dates before filtering so malformed records cannot disappear silently.
    if (!history.every((race) => record(race) && date(race.date))) return pas('V3_HISTORY_INVALID');
    const snapshot = history.filter((race) => race.date < asOf);
    if (snapshot.length === 0) return pas('V3_HISTORY_MISSING');
    const snapshotSha256 = historyDigest(snapshot);
    const validation = validateHistory(snapshot, { expectedSha256: snapshotSha256, beforeDate: asOf });
    if (!validation.ok) return pas(validation.reasonCodes);
    if (!snapshot.every(validScalars)) return pas('V3_HISTORY_SCALARS_INVALID');

    const sourceOdds = record(odds?.races) ? odds.races : odds;
    if (!record(sourceOdds)) return pas('V3_ODDS_MISSING');
    if (record(odds?.races) && odds.cutoffMin !== undefined && odds.cutoffMin !== 5) {
      return pas('V3_MARKET_CUTOFF_MISMATCH');
    }
    // Keep only IDs present in the past snapshot. Unknown/future odds never enter a fit.
    const oddsSnapshot = { cutoffMin: 5, races: Object.fromEntries(snapshot
      .map((race) => raceId(race)).filter((id) => Object.hasOwn(sourceOdds, id))
      .map((id) => [id, sourceOdds[id]])) };
    const orderHistory = snapshot.filter((race) => race.date < calibrationFrom);
    if (orderHistory.length === 0) return pas('V3_TRAINING_UNAVAILABLE');
    const formOrder = inferFormOrder(orderHistory);
    const matches = formOrder.matches;
    if (Math.max(matches.newestLast, matches.newestFirst) === 0
      || matches.newestLast === matches.newestFirst) return pas('V3_FORM_ORDER_UNPROVEN');

    const examples = new Map(buildExamples(snapshot, { newestLast: formOrder.newestLast })
      .map((example) => [example.id, example]));
    const extras = buildExtras(snapshot);
    const usable = [];
    const excluded = { races: 0, reasons: {} };
    const exclude = (reasons) => {
      excluded.races += 1;
      for (const reason of unique(reasons)) excluded.reasons[reason] = (excluded.reasons[reason] || 0) + 1;
    };
    // Iterate in shared builder order (date order; original race order within each day).
    // The output snapshot retains input order because historyDigest binds that exact order.
    const ordered = [...snapshot].sort((a, b) => a.date.localeCompare(b.date));
    for (const race of ordered) {
      const id = raceId(race);
      const example = examples.get(id);
      if (!example) {
        exclude([race.runners.filter((runner) => !runner.scratched).length < 4
          ? 'FIELD_TOO_SMALL' : 'V3_PROGRAM_INVALID']);
        continue;
      }
      const reasons = [];
      if (example.positions.filter((position) => position === 1).length !== 1) reasons.push('V3_RESULT_NOT_SINGLE_WINNER');
      const quote = oddsSnapshot.races[id];
      reasons.push(...quoteReasons(race, example, quote));
      if (reasons.length) { exclude(reasons); continue; }
      const extra = extras.get(id);
      const active = race.runners.filter((runner) => !runner.scratched);
      const q = marketProbabilities(example.numbers.map((number) => quote.runners[number].odds));
      if (!q || !Array.isArray(extra) || extra.length !== example.numbers.length
        || active.length !== example.numbers.length
        || active.some((runner, i) => runner.number !== example.numbers[i])) {
        exclude(['V3_FEATURE_ALIGNMENT_INVALID']);
        continue;
      }
      const X = example.X.map((row, i) => [...row, ...extra[i], Math.log(q[i])]);
      if (X.some((row) => row.length !== FEATURE_NAMES_V3.length || !row.every(finite))) {
        exclude(['V3_FEATURE_ALIGNMENT_INVALID']);
        continue;
      }
      usable.push({ ...example, X });
    }
    const training = usable.filter((example) => example.date < calibrationFrom);
    const calibration = usable.filter((example) => example.date >= calibrationFrom);
    if (training.length === 0) return { ...pas('V3_TRAINING_UNAVAILABLE'), exclusions: excluded };
    if (calibration.length < 30) return { ...pas('V3_CALIBRATION_INSUFFICIENT'), exclusions: excluded };
    const weights = train(training, fitParams);
    if (weights.length !== FEATURE_NAMES_V3.length || !weights.every(finite)) return pas('V3_WEIGHTS_INVALID');
    const calibrationScores = calibration.map((example) => ({
      s: scores(weights, example.X), win: example.positions.indexOf(1)
    }));
    const temperature = fitTemperature(calibrationScores);
    if (!finite(temperature) || temperature <= 0) return pas('V3_CALIBRATION_INVALID');
    const uncalibrated = calibrationStats(calibrationScores.map(({ s, win }) => ({ p: softmax(s), win })));
    const calibrated = calibrationStats(calibrationScores.map(({ s, win }) => ({
      p: softmax(s.map((score) => score * temperature)), win
    })));
    if (!uncalibrated || !calibrated) return pas('V3_CALIBRATION_INVALID');

    const artifact = {
      schemaVersion: ARTIFACT_SCHEMA_V3, featureNames: [...FEATURE_NAMES_V3], weights,
      formOrder, training: windowFor(training),
      calibration: { ...windowFor(calibration), temperature, scoreTransform: 'multiply' },
      historySha256: snapshotSha256, quoteCutoffMinutes: 5
    };
    const manifest = validateArtifact(artifact);
    if (!manifest.ok) return pas(manifest.reasonCodes);
    const evidence = {
      schemaVersion: EVIDENCE_SCHEMA_V3, asOf, calibrationFrom, params: fitParams,
      hashes: { artifactSha256: jsonDigest(artifact), historySha256: snapshotSha256, oddsSha256: jsonDigest(oddsSnapshot) },
      training: proofWindow(training),
      calibration: { ...proofWindow(calibration), temperature, scoreTransform: 'multiply', uncalibrated, calibrated },
      formOrder, excluded,
      exclusionScope: 'Past snapshot races only; asOf and later rows are removed before all transformations.',
      method: 'Fresh Adam/L2 conditional-logit fit; 33 shared features; later-date temperature holdout; T-5 GANYAN.',
      calibrationMetricsScope: 'Temperature fitting window; these are not independent test or verified backtest metrics.',
      backtestVerified: false, localTrainerParityVerified: false,
      reasonCodes: ['ARCHIVAL_PROGRAM_PROVENANCE_UNVERIFIED', 'PRE_CUTOFF_INPUT_SNAPSHOT_UNVERIFIED', 'LOCAL_TRAINER_PARITY_UNVERIFIED']
    };
    return { status: 'TRAINED_CANDIDATE', reasonCodes: [], artifact, history: snapshot, odds: oddsSnapshot, evidence };
  } catch {
    return pas('V3_TRAINING_INPUT_INVALID');
  }
}

/**
 * Rebuild features, refit fresh weights and refit the later holdout temperature.
 * Hash equality verifies reproducibility/integrity, never original capture time
 * or the unknown Termux trainer. No manifest/evidence boolean is trusted.
 */
export function verifyTraining({ artifact, history, odds, evidence, now = Date.now() } = {}) {
  const failed = (reasons) => ({
    status: 'PAS', reasonCodes: unique(reasons), trainingReproduced: false,
    calibrationReproduced: false, backtestVerified: false, localTrainerParityVerified: false
  });
  try {
    if (!record(evidence) || evidence.schemaVersion !== EVIDENCE_SCHEMA_V3 || !record(evidence.hashes)) {
      return failed(['V3_TRAINING_EVIDENCE_MISSING']);
    }
    const manifest = validateArtifact(artifact);
    const snapshot = validateHistory(history, { expectedSha256: artifact?.historySha256, beforeDate: evidence.asOf });
    if (!manifest.ok || !snapshot.ok) return failed([...manifest.reasonCodes, ...snapshot.reasonCodes]);
    if (jsonDigest(artifact) !== evidence.hashes.artifactSha256
      || jsonDigest(history) !== evidence.hashes.historySha256
      || jsonDigest(odds) !== evidence.hashes.oddsSha256) {
      return failed(['V3_TRAINING_EVIDENCE_MISMATCH']);
    }
    const reproduced = prepareTraining({
      history, odds, asOf: evidence.asOf, calibrationFrom: evidence.calibrationFrom,
      params: evidence.params, now
    });
    if (reproduced.status !== 'TRAINED_CANDIDATE') return failed(reproduced.reasonCodes);
    if (jsonDigest(reproduced.artifact) !== jsonDigest(artifact)
      || jsonDigest(reproduced.history) !== jsonDigest(history)
      || jsonDigest(reproduced.odds) !== jsonDigest(odds)
      || jsonDigest(reproduced.evidence) !== jsonDigest(evidence)) {
      return failed(['V3_TRAINING_EVIDENCE_MISMATCH']);
    }
    return {
      status: 'VERIFIED_TRAINING', reasonCodes: [], trainingReproduced: true,
      calibrationReproduced: true, backtestVerified: false, localTrainerParityVerified: false
    };
  } catch {
    return failed(['V3_TRAINING_EVIDENCE_INVALID']);
  }
}
