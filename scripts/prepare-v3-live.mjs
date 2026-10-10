#!/usr/bin/env node
/**
 * Offline candidate generator. Never imports/runs a local trainer, starts a
 * server, replaces the current model, or modifies input files.
 */
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { prepareTraining } from '../model-v3/training-evidence.mjs';
import { BUNDLE_SCHEMA, sha256, sourceDigests, auditBundle } from '../model-v3/training-bundle.mjs';

const pas = (reasonCodes) => ({
  status: 'PAS', reasonCodes, trainingReproduced: false,
  calibrationReproduced: false, backtestVerified: false, activated: false
});
function options(argv, now) {
  const today = new Date(now + 3 * 3600000).toISOString().slice(0, 10);
  const opts = { data: '.v2-data/races-yer.json', odds: '.v2-data/odds.json', 'as-of': today.slice(0, 7) + '-01' };
  const allowed = new Set(['data', 'odds', 'out', 'as-of', 'calibration-from', 'legacy-trainer']);
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i]?.replace(/^--/, '');
    if (!argv[i]?.startsWith('--') || !allowed.has(key) || !argv[i + 1] || argv[i + 1].startsWith('--')) {
      throw new Error('INVALID_ARGUMENTS');
    }
    opts[key] = argv[i + 1];
  }
  if (!opts['calibration-from']) {
    const date = new Date(opts['as-of'] + 'T00:00:00Z');
    if (!Number.isFinite(date.getTime())) throw new Error('INVALID_ARGUMENTS');
    date.setUTCDate(1);
    date.setUTCMonth(date.getUTCMonth() - 1);
    opts['calibration-from'] = date.toISOString().slice(0, 10);
  }
  return opts;
}

export async function runPreparation(argv = process.argv.slice(2), { now = Date.now() } = {}) {
  let opts;
  try { opts = options(argv, now); } catch { return pas(['INVALID_ARGUMENTS']); }
  let historyBytes, oddsBytes, legacyTrainerSha256 = null;
  try {
    [historyBytes, oddsBytes] = await Promise.all([readFile(opts.data), readFile(opts.odds)]);
    if (opts['legacy-trainer']) legacyTrainerSha256 = sha256(await readFile(opts['legacy-trainer']));
  } catch { return pas(['V3_TRAINING_INPUT_FILE_UNAVAILABLE']); }
  let result;
  try {
    const data = JSON.parse(historyBytes.toString('utf8'));
    const history = Array.isArray(data) ? data : data?.races;
    const odds = JSON.parse(oddsBytes.toString('utf8'));
    result = prepareTraining({
      history, odds, asOf: opts['as-of'], calibrationFrom: opts['calibration-from'], now
    });
  } catch { return pas(['V3_TRAINING_INPUT_INVALID']); }
  if (result.status !== 'TRAINED_CANDIDATE') return { ...pas(result.reasonCodes), exclusions: result.exclusions ?? null };
  let directory;
  try {
    // Explicit destinations MUST be new; no force flag or overwrite path exists.
    if (opts.out) {
      directory = resolve(opts.out);
      await mkdir(directory, { mode: 0o700 });
    } else {
      directory = await mkdtemp(join(tmpdir(), 'tjk-v3-candidate-'));
    }
  } catch { return pas(['V3_OUTPUT_DIRECTORY_EXISTS_OR_UNAVAILABLE']); }
  try {
    const values = {
      'v3-model.json': result.artifact, 'history.json': result.history,
      'odds.json': result.odds, 'evidence.json': result.evidence
    };
    const files = {};
    for (const [name, value] of Object.entries(values)) {
      const bytes = JSON.stringify(value, null, 2) + '\n';
      await writeFile(join(directory, name), bytes, { flag: 'wx', mode: 0o600 });
      files[name] = sha256(bytes);
    }
    const bundle = {
      schemaVersion: BUNDLE_SCHEMA,
      generatedAt: new Date(now).toISOString(),
      inputs: {
        historyRawSha256: sha256(historyBytes), oddsRawSha256: sha256(oddsBytes),
        legacyTrainerSha256
      },
      files, sources: await sourceDigests(),
      legacyTrainerExecuted: false, localTrainerParityVerified: false,
      backtestVerified: false
    };
    await writeFile(join(directory, 'bundle.json'), JSON.stringify(bundle, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    const audit = await auditBundle(directory, { now });
    const report = {
      ...audit, bundleDirectory: directory, activated: false,
      training: result.artifact.training, calibration: result.artifact.calibration,
      asOf: opts['as-of'], calibrationFrom: opts['calibration-from'],
      localTrainerSourceRecorded: legacyTrainerSha256 !== null,
      note: 'Candidate only. Existing model and servers are unchanged. Calibration-fit metrics are not independent test/backtest results.'
    };
    await writeFile(join(directory, 'audit.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    return report;
  } catch {
    return { ...pas(['V3_CANDIDATE_WRITE_OR_VERIFICATION_FAILED']), bundleDirectory: directory };
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = await runPreparation();
  console.log(JSON.stringify(report, null, 2));
  process.exitCode = report.status === 'VERIFIED_TRAINING' ? 0 : 1;
}
