import { buildExamples, FEATURE_NAMES } from '../model-v2/features.mjs';
import { buildExtras, EXTRA_NAMES } from './history-features.mjs';
import { marketProbabilities } from './market.mjs';

/** Research/training column order; AGF is deliberately absent. */
export const FEATURE_NAMES_V3 = Object.freeze([...FEATURE_NAMES, ...EXTRA_NAMES, 'logMarketProbT5']);

/**
 * Reuse the research transformations, including within-active-field z-scores.
 * Callers must validate provenance, dates, runner identity and quote quality first.
 * Neither current-day results nor an inferred live form-token order enter this path.
 */
export function buildLiveV3Features({ history, race, quote, newestLast }) {
  const races = [...history, race];
  const id = [race.date, race.venue, race.number].join('|');
  const example = buildExamples(races, { newestLast }).find((item) => item.id === id);
  const extra = buildExtras(races).get(id);
  if (!example || !extra || extra.length !== example.numbers.length) {
    throw new Error('V3_FEATURE_ALIGNMENT_INVALID');
  }
  const q = marketProbabilities(example.numbers.map((number) => quote.runners[number].odds));
  return {
    numbers: example.numbers,
    X: example.X.map((row, i) => [...row, ...extra[i], Math.log(q[i])]),
    marketProbabilities: q
  };
}
