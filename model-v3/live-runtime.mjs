import { readFile } from 'node:fs/promises';
import { runnerFromProgram } from '../model-v2/dataset.mjs';
import { predictLiveV3 } from './live-inference.mjs';

const pass = (reasonCodes) => ({
  status: 'PAS',
  reasonCodes: [...new Set(reasonCodes)],
  runners: [],
  model: { version: 'V3', featureCount: 33 }
});

/**
 * Normalize the exact header-keyed official row using the training dataset
 * parser. Do not rebuild values from presentation fields: that loses extra
 * weight, pedigree identity, missingness and the training form token order.
 */
export function liveRaceFromProgram({ programRace, date, venueKey }) {
  if (!programRace || !/^\d{4}-\d{2}-\d{2}$/u.test(date || '') ||
      !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date ||
      typeof venueKey !== 'string' || !venueKey ||
      !Number.isInteger(programRace.number) || programRace.number < 1 ||
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/u.test(programRace.time || '') ||
      !Array.isArray(programRace.runners) || !programRace.runners.length) {
    throw new Error('V3_PROGRAM_INVALID');
  }
  const distanceText = String(programRace.distance ?? '').trim();
  const distance = /^\d+(?:\s*m)?$/iu.test(distanceText) ? parseInt(distanceText, 10) : null;
  const surfaces = new Map([['çim', 'Çim'], ['kum', 'Kum'], ['sentetik', 'Sentetik']]);
  const surface = surfaces.get(String(programRace.surface || '').toLocaleLowerCase('tr'));
  if (!(distance > 0) || !surface) throw new Error('V3_PROGRAM_INVALID');

  const year = Number(date.slice(0, 4));
  const seen = new Set();
  const runners = programRace.runners.map((program) => {
    const row = program.programRow;
    if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('V3_PROGRAM_INVALID');
    const runner = runnerFromProgram(row, year);
    if (!Number.isInteger(runner.number) || runner.number < 1 || runner.number > 30 ||
        runner.number !== program.number || seen.has(runner.number)) throw new Error('V3_PROGRAM_INVALID');
    seen.add(runner.number);
    return {
      ...runner,
      // Never override an official Koşmaz marker with a market-source flag.
      scratched: runner.scratched || program.scratched === true
    };
  });
  return {
    date, venue: venueKey, number: programRace.number, time: programRace.time,
    distance, surface,
    prize1: Number.isFinite(programRace.prize1) && programRace.prize1 > 0 ? programRace.prize1 : null,
    runners
  };
}

/**
 * Read local inputs without generating, overwriting or guessing private model
 * files. A legacy array of weights is not a verified live-model manifest.
 */
export async function analyzeLiveV3({
  programRace, date, venueKey, quote, reasonCodes = [],
  modelPath = process.env.V3_MODEL_PATH || '.v2-data/v3-model.json',
  historyPath = process.env.V3_HISTORY_PATH || '.v2-data/races-yer.json',
  now = Date.now()
} = {}) {
  if (reasonCodes.length) return pass(reasonCodes);
  let race;
  try {
    race = liveRaceFromProgram({ programRace, date, venueKey });
  } catch {
    return pass(['V3_PROGRAM_INVALID']);
  }
  let artifact;
  try {
    artifact = JSON.parse(await readFile(modelPath, 'utf8'));
  } catch {
    return pass(['V3_MODEL_UNAVAILABLE']);
  }
  let history;
  try {
    const document = JSON.parse(await readFile(historyPath, 'utf8'));
    history = Array.isArray(document) ? document : document?.races;
    if (!Array.isArray(history) || !history.length) return pass(['V3_HISTORY_UNAVAILABLE']);
  } catch {
    return pass(['V3_HISTORY_UNAVAILABLE']);
  }
  try {
    return predictLiveV3({ artifact, history, race, quote, reasonCodes, now });
  } catch {
    // A corrupt history row or incompatible private artifact cannot escape
    // into a stale prediction or an automatic V2 fallback.
    return pass(['V3_INFERENCE_FAILED']);
  }
}
