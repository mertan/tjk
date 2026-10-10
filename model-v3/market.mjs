/** Point-in-time market probabilities, PAS gate, calibration and value-bet accounting. */
export const MARKET_LIMITS = Object.freeze({ maxQuoteAgeMs: 12 * 60_000, minField: 4 });
export const EDGES = ['0.05', '0.10', '0.20'];

/** PAS unless every active runner has a GANYAN quote <= cutoff that is at most 12 min old. */
export function marketGate(numbers, quote, limits = MARKET_LIMITS) {
  const reasons = new Set();
  if (numbers.length < limits.minField) reasons.add('FIELD_TOO_SMALL');
  if (!quote) reasons.add('ODDS_HISTORY_MISSING');
  else {
    if (!Number.isFinite(quote.lastLabelAt) || quote.cutoffMs - quote.lastLabelAt > limits.maxQuoteAgeMs) reasons.add('SOURCE_STALE');
    for (const number of numbers) {
      const runner = quote.runners?.[number];
      if (!runner || !Number.isFinite(runner.odds)) reasons.add('QUOTE_MISSING');
      else if (!Number.isFinite(runner.at) || runner.at > quote.cutoffMs || quote.cutoffMs - runner.at > limits.maxQuoteAgeMs) reasons.add('QUOTE_STALE');
    }
  }
  return { ok: reasons.size === 0, reasons: [...reasons] };
}

/** Implied win probabilities with the pool overround removed proportionally. */
export function marketProbabilities(odds) {
  const inverse = odds.map((o) => 1 / o);
  const total = inverse.reduce((a, b) => a + b, 0);
  return inverse.map((x) => x / total);
}

const softmaxT = (scores, t) => {
  const max = Math.max(...scores.map((s) => s * t));
  const exp = scores.map((s) => Math.exp(s * t - max));
  const total = exp.reduce((a, b) => a + b, 0);
  return exp.map((x) => x / total);
};

/** Single temperature fitted on the calibration window (grid search on log-loss). */
export function fitTemperature(items) {
  const usable = items.filter((item) => item.win >= 0);
  if (!usable.length) return 1;
  let best = { t: 1, loss: Infinity };
  for (let t = 0.2; t <= 3.0001; t += 0.05) {
    const loss = usable.reduce((s, item) => s - Math.log(Math.max(1e-12, softmaxT(item.s, t)[item.win])), 0) / usable.length;
    if (loss < best.loss) best = { t, loss };
  }
  return best.t;
}

/** Log-loss, Brier (summed over runners, averaged per race) and runner-level ECE over 10 bins. */
export function calibrationStats(input, bins = 10) {
  const items = input.filter(({ p, win }) => win >= 0 && Array.isArray(p) && p.every(Number.isFinite));
  if (!items.length) return null;
  let logLoss = 0;
  let brier = 0;
  const table = Array.from({ length: bins }, () => ({ n: 0, p: 0, wins: 0 }));
  let runners = 0;
  for (const { p, win } of items) {
    logLoss -= Math.log(Math.max(1e-12, p[win]));
    p.forEach((pi, i) => {
      const y = i === win ? 1 : 0;
      brier += (pi - y) ** 2;
      const bin = table[Math.min(bins - 1, Math.floor(pi * bins))];
      bin.n += 1;
      bin.p += pi;
      bin.wins += y;
      runners += 1;
    });
  }
  const ece = table.reduce((s, b) => s + (b.n ? Math.abs(b.p / b.n - b.wins / b.n) * (b.n / runners) : 0), 0);
  const round = (x) => Math.round(x * 10000) / 10000;
  return {
    logLoss: round(logLoss / items.length),
    brier: round(brier / items.length),
    ece: round(ece),
    reliability: table.filter((b) => b.n).map((b, i) => ({ meanP: round(b.p / b.n), winRate: round(b.wins / b.n), n: b.n }))
  };
}

/**
 * 1-unit win bets where model p * T-5 quote >= 1 + edge. Returns use the official
 * pari-mutuel dividend (already net of TJK commission); retAtQuote is the
 * optimistic fixed-odds counterfactual at the T-5 quote.
 */
export function valueBets({ p, quoteOdds, dividends, positions }) {
  const out = {};
  for (const edge of EDGES) {
    const bet = { stake: 0, ret: 0, retAtQuote: 0, wins: 0, missingDividend: 0 };
    p.forEach((pi, i) => {
      if (!(pi * quoteOdds[i] >= 1 + Number(edge))) return;
      bet.stake += 1;
      if (positions[i] !== 1) return;
      bet.wins += 1;
      bet.retAtQuote += quoteOdds[i];
      if (Number.isFinite(dividends[i])) bet.ret += dividends[i];
      else bet.missingDividend += 1;
    });
    out[edge] = bet;
  }
  return out;
}
