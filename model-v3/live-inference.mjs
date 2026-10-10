import { createHash } from 'node:crypto';
import { scores, softmax } from '../model-v2/model.mjs';
import { marketGate } from './market.mjs';
import { buildLiveV3Features, FEATURE_NAMES_V3 } from './features.mjs';

export { FEATURE_NAMES_V3 };
export const ARTIFACT_SCHEMA_V3 = 'tjk-v3-live/1';
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = Number.isFinite;
const positiveInt = (value) => Number.isInteger(value) && value > 0;
const isDate = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const at = Date.parse(value + 'T00:00:00Z');
  return finite(at) && new Date(at).toISOString().slice(0, 10) === value;
};
const validWindow = (window) => isObject(window) && isDate(window.from) && isDate(window.through)
  && window.from <= window.through && positiveInt(window.races);
const uniqueReasons = (reasons) => [...new Set(reasons.filter((reason) => typeof reason === 'string' && reason.length))];

/** Digest of the parsed ordered races array, not of raw JSON file whitespace. */
export function historyDigest(history) {
  return createHash('sha256').update(JSON.stringify(history)).digest('hex');
}

/**
 * This validates an explicit serving manifest, not the truth of reported results.
 * Legacy local models without this contract stay PAS until independently audited.
 */
export function validateArtifact(artifact) {
  if (!isObject(artifact)) return { ok: false, reasonCodes: ['V3_ARTIFACT_MISSING'] };
  const reasons = [];
  if (artifact.schemaVersion !== ARTIFACT_SCHEMA_V3) reasons.push('V3_SCHEMA_UNSUPPORTED');
  if (!Array.isArray(artifact.featureNames)
    || artifact.featureNames.length !== FEATURE_NAMES_V3.length
    || FEATURE_NAMES_V3.some((name, i) => artifact.featureNames[i] !== name)) {
    reasons.push('V3_FEATURE_SCHEMA_MISMATCH');
  }
  if (!Array.isArray(artifact.weights) || artifact.weights.length !== FEATURE_NAMES_V3.length
    || !artifact.weights.every(finite)) reasons.push('V3_WEIGHTS_INVALID');
  if (typeof artifact.formOrder?.newestLast !== 'boolean') reasons.push('V3_FORM_ORDER_MISSING');
  if (!validWindow(artifact.training)) reasons.push('V3_TRAINING_METADATA_INVALID');
  const calibration = artifact.calibration;
  if (!validWindow(calibration) || calibration.races < 30
    || !finite(calibration.temperature) || calibration.temperature <= 0
    || calibration.scoreTransform !== 'multiply'
    || (validWindow(artifact.training) && artifact.training.through >= calibration.from)) {
    reasons.push('V3_CALIBRATION_INVALID');
  }
  if (typeof artifact.historySha256 !== 'string' || !/^[a-f0-9]{64}$/.test(artifact.historySha256)) {
    reasons.push('V3_HISTORY_DIGEST_MISSING');
  }
  if (artifact.quoteCutoffMinutes !== 5) reasons.push('V3_MARKET_CUTOFF_MISMATCH');
  return { ok: reasons.length === 0, reasonCodes: reasons };
}

const raceId = (race) => [race.date, race.venue, race.number].join('|');
const validKey = (key) => typeof key === 'string' && key.split('|').length === 3
  && key.split('|').every((part) => part.trim() && part !== '?')
  && /^\d{4}$/.test(key.split('|')[2]);
const validIdentity = (race) => isObject(race) && isDate(race.date)
  && typeof race.venue === 'string' && race.venue.length > 0 && positiveInt(race.number)
  && Array.isArray(race.runners) && race.runners.length > 0
  && race.runners.every((runner) => isObject(runner) && positiveInt(runner.number)
    && validKey(runner.key) && typeof runner.scratched === 'boolean')
  && new Set(race.runners.map((runner) => runner.number)).size === race.runners.length
  && new Set(race.runners.map((runner) => runner.key)).size === race.runners.length;
const sourceDay = (at) => finite(at) && Math.abs(at) < 8e15
  ? new Date(at + 3 * 3_600_000).toISOString().slice(0, 10) : null;

/** Shared snapshot validation for inference and the read-only installation audit. */
export function validateHistory(history, { expectedSha256, beforeDate } = {}) {
  if (!Array.isArray(history) || !history.length) {
    return { ok: false, reasonCodes: ['V3_HISTORY_MISSING'], sha256: null };
  }
  if (!history.every(validIdentity) || new Set(history.map(raceId)).size !== history.length) {
    return { ok: false, reasonCodes: ['V3_HISTORY_INVALID'], sha256: null };
  }
  const reasons = [];
  if (beforeDate && history.some((item) => item.date >= beforeDate)) reasons.push('V3_HISTORY_LOOKAHEAD');
  let sha256;
  try { sha256 = historyDigest(history); } catch {
    return { ok: false, reasonCodes: ['V3_HISTORY_INVALID'], sha256: null };
  }
  if (sha256 !== expectedSha256) reasons.push('V3_HISTORY_DIGEST_MISMATCH');
  return { ok: reasons.length === 0, reasonCodes: reasons, sha256 };
}

/**
 * Research-only V3 inference at scheduled post minus five minutes.
 * Upstream heartbeat, cross-source and pre-cutoff program-snapshot gates remain
 * mandatory: pass their failures as reasonCodes. No gate can be bypassed here.
 */
export function predictLiveV3({ artifact, history, race, quote, reasonCodes = [], now = Date.now() } = {}) {
  const upstream = Array.isArray(reasonCodes) && reasonCodes.every((reason) => typeof reason === 'string' && reason.length)
    ? reasonCodes : ['V3_UPSTREAM_GATE_INVALID'];
  const reasons = [...upstream, ...validateArtifact(artifact).reasonCodes];
  const metadata = {
    version: 'V3', featureCount: FEATURE_NAMES_V3.length,
    verification: 'schema-and-history-digest-only',
    trainingRaces: positiveInt(artifact?.training?.races) ? artifact.training.races : null
  };
  const pas = (codes) => ({
    status: 'PAS', reasonCodes: uniqueReasons(codes), runners: [], model: metadata
  });
  if (reasons.length) return pas(reasons);
  if (!validIdentity(race) || !finite(race.distance) || race.distance <= 0
    || !['Kum', 'Çim', 'Sentetik'].includes(race.surface)
    || (race.prize1 !== null && (!finite(race.prize1) || race.prize1 <= 0))
    || !/^([01]\d|2[0-3]):[0-5]\d$/.test(race.time || '')) {
    return pas(['V3_PROGRAM_INVALID']);
  }
  if (artifact.training.through >= race.date || artifact.calibration.through >= race.date) {
    return pas(['V3_ARTIFACT_LOOKAHEAD']);
  }
  const snapshot = validateHistory(history, { expectedSha256: artifact.historySha256, beforeDate: race.date });
  if (!snapshot.ok) return pas(snapshot.reasonCodes);

  const postMs = Date.parse(race.date + 'T' + race.time + ':00+03:00');
  const cutoffMs = postMs - artifact.quoteCutoffMinutes * 60_000;
  if (!finite(now) || now < cutoffMs) return pas(['V3_CUTOFF_NOT_REACHED']);
  if (now >= postMs) return pas(['V3_RACE_STARTED']);
  if (!isObject(quote) || !finite(quote.cutoffMs) || quote.cutoffMs !== cutoffMs) {
    return pas(['V3_QUOTE_CUTOFF_MISMATCH']);
  }
  const active = race.runners.filter((runner) => !runner.scratched);
  const numbers = active.map((runner) => runner.number);
  const known = new Set(race.runners.map((runner) => String(runner.number)));
  if (!isObject(quote.runners)) return pas(['V3_QUOTE_MISSING']);
  if (Object.keys(quote.runners).some((number) => !known.has(number))) {
    reasons.push('V3_QUOTE_RUNNER_MISMATCH');
  }
  if (!finite(quote.lastLabelAt) || quote.lastLabelAt > cutoffMs || sourceDay(quote.lastLabelAt) !== race.date) {
    reasons.push('V3_SOURCE_TIMESTAMP_INVALID');
  }
  for (const number of numbers) {
    const point = quote.runners[number];
    if (!isObject(point) || !finite(point.odds) || point.odds < 1.01) {
      reasons.push('V3_QUOTE_MISSING');
    } else if (!finite(point.at) || point.at > cutoffMs || sourceDay(point.at) !== race.date) {
      reasons.push('V3_QUOTE_TIMESTAMP_INVALID');
    }
  }
  reasons.push(...marketGate(numbers, quote).reasons);
  if (reasons.length) return pas(reasons);
  let features;
  try {
    features = buildLiveV3Features({ history, race, quote, newestLast: artifact.formOrder.newestLast });
  } catch {
    return pas(['V3_FEATURE_ALIGNMENT_INVALID']);
  }
  if (features.numbers.length !== numbers.length || features.numbers.some((number, i) => number !== numbers[i])
    || features.X.some((row) => row.length !== FEATURE_NAMES_V3.length || !row.every(finite))) {
    return pas(['V3_FEATURE_ALIGNMENT_INVALID']);
  }
  const rawScores = scores(artifact.weights, features.X);
  const calibrated = rawScores.map((score) => score * artifact.calibration.temperature);
  if (!calibrated.every(finite)) return pas(['V3_PREDICTION_INVALID']);
  const probabilities = softmax(calibrated);
  if (probabilities.length !== numbers.length || probabilities.some((p) => !finite(p) || p < 0 || p > 1)
    || Math.abs(probabilities.reduce((sum, p) => sum + p, 0) - 1) > 1e-10) {
    return pas(['V3_PREDICTION_INVALID']);
  }
  const ranked = numbers.map((number, i) => ({ number, probability: probabilities[i] }))
    .sort((a, b) => b.probability - a.probability || a.number - b.number);
  return {
    status: 'OK', reasonCodes: [],
    runners: ranked.map((runner, i) => ({ ...runner, rank: i + 1 })),
    model: {
      ...metadata, calibrationRaces: artifact.calibration.races,
      calibrationTemperature: artifact.calibration.temperature,
      trainingThrough: artifact.training.through, calibrationThrough: artifact.calibration.through,
      historyRaces: history.length, formNewestLast: artifact.formOrder.newestLast,
      quoteCutoffMinutes: artifact.quoteCutoffMinutes, cutoffMs
    }
  };
}
