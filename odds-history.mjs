import { parseSourceTime } from './race-gates.mjs';

const validOdds = (value) => Number.isFinite(value) && value > 0 && value < 900;
const round = (value, digits = 2) => Number.isFinite(value) ? Number(value.toFixed(digits)) : null;

// This is an anomaly gate, not a claim that a large price is impossible.
// An adjacent tenfold discontinuity must be investigated before any movement signal.
export const MAX_ADJACENT_ODDS_RATIO = 10;

export function parseOddsHistory(payload, { date, expectedRunner } = {}) {
  const data = payload?.data;
  if (!payload?.success || !Array.isArray(data?.labels) || !Array.isArray(data?.datasets)
      || data.datasets.length !== 1 || !Array.isArray(data.datasets[0]?.data)
      || data.labels.length !== data.datasets[0].data.length) return [];
  const datasetLabel = data.datasets[0].label;
  const runnerMatches = expectedRunner === undefined || datasetLabel === undefined
    || String(datasetLabel).trim() === String(expectedRunner);
  return data.labels.map((label, index) => {
    const raw = data.datasets[0].data[index];
    const text = typeof raw === 'number' || typeof raw === 'string' ? String(raw).trim().replace(',', '.') : '';
    const at = parseSourceTime(label);
    const dateMatches = !date || (Number.isFinite(at) && new Date(at + 3 * 3600000).toISOString().slice(0, 10) === date);
    return { label, dateMatches, runnerMatches, time: typeof label === 'string' ? label.slice(11, 16) : '',
      at, odds: text ? Number(text) : null };
  });
}

export function historyMetrics(points, currentOdds) {
  const history = Array.isArray(points) ? points : [];
  const reasons = new Set();
  if (history.length < 2) reasons.add('INSUFFICIENT_HISTORY');
  for (let index = 0; index < history.length; index += 1) {
    const point = history[index];
    if (!point || !validOdds(point.odds) || !Number.isFinite(point.at)) reasons.add('QUOTE_HISTORY_INVALID');
    if (point?.runnerMatches === false) reasons.add('QUOTE_RUNNER_MISMATCH');
    if (point?.dateMatches === false) reasons.add('QUOTE_HISTORY_DATE_MISMATCH');
    const previous = history[index - 1];
    if (previous && point) {
      if (point.at <= previous.at) reasons.add('QUOTE_HISTORY_INVALID');
      if (validOdds(previous.odds) && validOdds(point.odds)
          && Math.max(previous.odds / point.odds, point.odds / previous.odds) >= MAX_ADJACENT_ODDS_RATIO) {
        reasons.add('ODDS_DISCONTINUITY');
      }
    }
  }
  const latest = history.at(-1);
  if (!validOdds(currentOdds) || !latest || latest.odds !== currentOdds) reasons.add('QUOTE_VALUE_MISMATCH');
  const trusted = reasons.size === 0;
  const values = history.filter((point) => validOdds(point?.odds)).map((point) => point.odds);
  // Keep raw observations for diagnosis, but never publish a computed movement
  // or opening price when the chain is invalid. Do not silently drop bad points.
  return {
    openingOdds: trusted ? history[0].odds : null,
    recordedOdds: validOdds(latest?.odds) ? latest.odds : null,
    currentOdds: round(currentOdds),
    lowOdds: trusted ? round(Math.min(...values)) : null,
    highOdds: trusted ? round(Math.max(...values)) : null,
    movementPercent: trusted ? round((currentOdds / history[0].odds - 1) * 100, 1) : null,
    shortMovementPercent: trusted ? round((currentOdds / history.at(-2).odds - 1) * 100, 1) : null,
    historyStatus: trusted ? 'VERIFIED' : 'UNVERIFIED',
    openingKind: 'first_verified_observation',
    historyReasonCodes: [...reasons],
    points: history
  };
}
