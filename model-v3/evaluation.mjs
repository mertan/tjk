/** Research accounting only. Each bootstrap draw resamples whole races. */
const validRecords = (records) => Array.isArray(records) && Array.from(records).every((record) =>
  record && Number.isSafeInteger(record.stake) && record.stake >= 0
  && Number.isFinite(record.ret) && record.ret >= 0
  && Number.isSafeInteger(record.wins ?? 0) && (record.wins ?? 0) >= 0 && (record.wins ?? 0) <= record.stake
  && Number.isSafeInteger(record.missingDividend ?? 0) && (record.missingDividend ?? 0) >= 0
  && (record.missingDividend ?? 0) <= (record.wins ?? 0)
  && (record.retAtQuote === undefined || (Number.isFinite(record.retAtQuote) && record.retAtQuote >= 0)))
  && Number.isSafeInteger(records.reduce((sum, record) => sum + record.stake, 0))
  && Number.isFinite(records.reduce((sum, record) => sum + record.ret, 0));
const pct = (value) => (value === null ? null : Math.round(value * 1000) / 10);

/**
 * Cluster bootstrap for the headline estimand: sum(return) / sum(stake) - 1.
 * Zero-stake races remain in the same sampling universe. At least minSample
 * races with a stake are required; missing winning dividends suppress the CI.
 */
export function bootstrapRoiCI(records, { minSample = 30, iterations = 2000, seed = 11 } = {}) {
  if (!Number.isSafeInteger(minSample) || minSample < 2
      || !Number.isSafeInteger(iterations) || iterations < 100 || iterations > 100_000
      || !Number.isSafeInteger(seed) || !validRecords(records)
      || records.filter((record) => record.stake > 0).length < minSample
      || records.some((record) => (record.missingDividend ?? 0) > 0)) return null;
  let state = seed >>> 0;
  const random = () => ((state = (state * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  const samples = [];
  for (let i = 0; i < iterations; i += 1) {
    let stake = 0;
    let ret = 0;
    for (let j = 0; j < records.length; j += 1) {
      const race = records[Math.floor(random() * records.length)];
      stake += race.stake;
      ret += race.ret;
    }
    if (!stake) return null;
    samples.push(ret / stake - 1);
  }
  samples.sort((a, b) => a - b);
  return [samples[Math.floor(iterations * 0.025)], samples[Math.ceil(iterations * 0.975) - 1]];
}

export function summarizeWagers(records, { minSample = 30 } = {}) {
  if (!validRecords(records) || !Number.isSafeInteger(minSample) || minSample < 2) {
    return { status: 'INVALID_SETTLEMENT', bets: 0, winners: 0, racesWithBet: 0, missingDividend: null,
      roiDividend: null, roiDividendCI95: null, roiDividendHaircut5: null,
      roiDividendHaircut10: null, roiAtT5QuoteOptimistic: null };
  }
  const stake = records.reduce((sum, record) => sum + record.stake, 0);
  const ret = records.reduce((sum, record) => sum + record.ret, 0);
  const missingDividend = records.reduce((sum, record) => sum + (record.missingDividend ?? 0), 0);
  const racesWithBet = records.filter((record) => record.stake > 0).length;
  const verified = stake > 0 && missingDividend === 0;
  const hasQuoteReturn = records.every((record) => Number.isFinite(record.retAtQuote));
  return {
    status: !stake ? 'NO_BETS' : missingDividend ? 'DIVIDEND_MISSING' : racesWithBet < minSample ? 'INSUFFICIENT_SAMPLE' : 'OK',
    bets: stake,
    winners: records.reduce((sum, record) => sum + (record.wins ?? 0), 0),
    racesWithBet,
    missingDividend,
    roiDividend: verified ? pct(ret / stake - 1) : null,
    roiDividendCI95: verified ? bootstrapRoiCI(records, { minSample })?.map(pct) ?? null : null,
    roiDividendHaircut5: verified ? pct(ret * 0.95 / stake - 1) : null,
    roiDividendHaircut10: verified ? pct(ret * 0.9 / stake - 1) : null,
    roiAtT5QuoteOptimistic: stake && hasQuoteReturn
      ? pct(records.reduce((sum, record) => sum + record.retAtQuote, 0) / stake - 1) : null
  };
}
