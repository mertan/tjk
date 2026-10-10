#!/usr/bin/env node
/** Read-only manifest audit. Does not train, publish, bet or start any service. */
import { readFile } from 'node:fs/promises';
import { auditBundle } from '../model-v3/training-bundle.mjs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ARTIFACT_SCHEMA_V3, FEATURE_NAMES_V3, validateArtifact, validateHistory } from '../model-v3/live-inference.mjs';

const safeDate = (value) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
const safeCount = (value) => Number.isInteger(value) && value > 0 ? value : null;
const summarizeWindow = (value) => ({
  from: safeDate(value?.from), through: safeDate(value?.through), races: safeCount(value?.races)
});

export function auditLiveV3(artifact, history, ioReasons = []) {
  const model = validateArtifact(artifact);
  const snapshot = validateHistory(history, { expectedSha256: artifact?.historySha256 });
  const reasonCodes = [...new Set([...ioReasons, ...model.reasonCodes, ...snapshot.reasonCodes])];
  return {
    status: reasonCodes.length ? 'PAS' : 'VALID_MANIFEST',
    reasonCodes,
    schema: artifact?.schemaVersion === ARTIFACT_SCHEMA_V3 ? ARTIFACT_SCHEMA_V3 : null,
    expectedFeatureCount: FEATURE_NAMES_V3.length,
    featureOrderMatches: Array.isArray(artifact?.featureNames)
      && artifact.featureNames.length === FEATURE_NAMES_V3.length
      && FEATURE_NAMES_V3.every((name, i) => artifact.featureNames[i] === name),
    weightsCount: Array.isArray(artifact?.weights) ? artifact.weights.length : null,
    weightsFinite: Array.isArray(artifact?.weights) && artifact.weights.length > 0 && artifact.weights.every(Number.isFinite),
    formNewestLast: typeof artifact?.formOrder?.newestLast === 'boolean' ? artifact.formOrder.newestLast : null,
    training: summarizeWindow(artifact?.training),
    calibration: {
      ...summarizeWindow(artifact?.calibration),
      temperature: Number.isFinite(artifact?.calibration?.temperature) ? artifact.calibration.temperature : null,
      scoreTransform: artifact?.calibration?.scoreTransform === 'multiply' ? 'multiply' : null
    },
    history: {
      races: Array.isArray(history) ? history.length : 0,
      expectedSha256: /^[a-f0-9]{64}$/.test(artifact?.historySha256 || '') ? artifact.historySha256 : null,
      actualSha256: snapshot.sha256,
      digestMatches: snapshot.sha256 !== null && snapshot.sha256 === artifact?.historySha256
    },
    trainingReproduced: false,
    backtestVerified: false,
    verificationScope: 'Manifest schema and historical snapshot integrity only; weights, training race count and backtest claims were not independently reproduced.'
  };
}

function options(argv) {
  const opts = {
    model: process.env.V3_MODEL_PATH || '.v2-data/v3-model.json',
    history: process.env.V3_HISTORY_PATH || '.v2-data/races-yer.json'
  };
  const positional = [];
  let explicitModelOrHistory = false;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--model' || arg === '--history' || arg === '--bundle') {
      if (arg !== '--bundle') explicitModelOrHistory = true;
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw new Error('INVALID_ARGUMENTS');
      opts[arg.slice(2)] = value;
    } else if (arg.startsWith('--')) throw new Error('INVALID_ARGUMENTS');
    else positional.push(arg);
  }
  if (opts.bundle && (explicitModelOrHistory || positional.length)) throw new Error('INVALID_ARGUMENTS');
  if (positional.length > 2) throw new Error('INVALID_ARGUMENTS');
  if (positional[0]) opts.model = positional[0];
  if (positional[1]) opts.history = positional[1];
  return opts;
}

export async function runAudit(argv = process.argv.slice(2)) {
  let opts;
  try { opts = options(argv); } catch {
    return { status: 'PAS', reasonCodes: ['INVALID_ARGUMENTS'], usage: 'node scripts/check-v3-live.mjs [--model PATH] [--history PATH] | --bundle DIR' };
  }
  if (opts.bundle) return auditBundle(opts.bundle);
  const read = async (path, reason) => {
    try { return { value: JSON.parse(await readFile(path, 'utf8')), reasons: [] }; }
    catch { return { value: null, reasons: [reason] }; }
  };
  const [model, dataset] = await Promise.all([
    read(opts.model, 'V3_ARTIFACT_FILE_UNAVAILABLE'),
    read(opts.history, 'V3_HISTORY_FILE_UNAVAILABLE')
  ]);
  const history = Array.isArray(dataset.value) ? dataset.value : dataset.value?.races;
  return auditLiveV3(model.value, history, [...model.reasons, ...dataset.reasons]);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = await runAudit();
  console.log(JSON.stringify(report, null, 2));
  process.exitCode = ['VALID_MANIFEST', 'VERIFIED_TRAINING'].includes(report.status) ? 0 : 1;
}
