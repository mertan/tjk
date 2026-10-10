import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { verifyTraining, jsonDigest } from './training-evidence.mjs';

export const BUNDLE_SCHEMA = 'tjk-v3-training-bundle/1';
export const SOURCE_FILES = Object.freeze([
  'model-v2/features.mjs', 'model-v2/model.mjs',
  'model-v3/history-features.mjs', 'model-v3/features.mjs',
  'model-v3/market.mjs', 'model-v3/live-inference.mjs',
  'model-v3/training-evidence.mjs', 'model-v3/training-bundle.mjs',
  'race-gates.mjs', 'scripts/prepare-v3-live.mjs', 'scripts/check-v3-live.mjs'
]);
export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
export async function sourceDigests() {
  return Object.fromEntries(await Promise.all(SOURCE_FILES.map(async (path) =>
    [path, sha256(await readFile(new URL('../' + path, import.meta.url)))])));
}

/** Re-read saved inputs and actually refit. A self-reported verified flag is never evidence. */
export async function auditBundle(directory, { now = Date.now() } = {}) {
  const failure = (reasonCodes) => ({
    status: 'PAS', reasonCodes, trainingReproduced: false,
    calibrationReproduced: false, backtestVerified: false,
    localTrainerParityVerified: false
  });
  try {
    const names = ['v3-model.json', 'history.json', 'odds.json', 'evidence.json', 'bundle.json'];
    const files = Object.fromEntries(await Promise.all(names.map(async (name) => {
      const bytes = await readFile(join(directory, name));
      return [name, { sha256: sha256(bytes), value: JSON.parse(bytes.toString('utf8')) }];
    })));
    const bundle = files['bundle.json'].value;
    if (bundle?.schemaVersion !== BUNDLE_SCHEMA) return failure(['V3_BUNDLE_SCHEMA_INVALID']);
    if (!bundle.files || names.slice(0, 4).some((name) => bundle.files[name] !== files[name].sha256)) {
      return failure(['V3_BUNDLE_FILE_DIGEST_MISMATCH']);
    }
    const code = await sourceDigests();
    if (!bundle.sources || SOURCE_FILES.some((path) => bundle.sources[path] !== code[path])) {
      return failure(['V3_TRAINING_SOURCE_DIGEST_MISMATCH']);
    }
    if (!/^[a-f0-9]{64}$/.test(bundle.inputs?.historyRawSha256 || '')
        || !/^[a-f0-9]{64}$/.test(bundle.inputs?.oddsRawSha256 || '')) {
      return failure(['V3_INPUT_DIGEST_MISSING']);
    }
    const artifact = files['v3-model.json'].value;
    const history = files['history.json'].value;
    const odds = files['odds.json'].value;
    const evidence = files['evidence.json'].value;
    const replay = verifyTraining({ artifact, history, odds, evidence, now });
    return {
      ...replay,
      historySha256: jsonDigest(history),
      retainedInputFilesVerified: replay.trainingReproduced === true,
      sourceFilesVerified: true,
      rawInputDigestsRecorded: true,
      originalInputFilesRechecked: false,
      localTrainerParityVerified: false,
      verificationScope: 'Fresh fit and held-out calibration reproduced from retained historical inputs with this source code. Original live weights and archival pre-cutoff program capture are not verified.'
    };
  } catch {
    return failure(['V3_BUNDLE_UNAVAILABLE_OR_INVALID']);
  }
}
