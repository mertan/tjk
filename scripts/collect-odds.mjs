#!/usr/bin/env node
/**
 * Read-only: per race, one TJK GANYAN history request (all horses). Keeps, per
 * runner, the last quote at/before post-5min (point-in-time) and the last
 * pre-off quote (only for drift reporting, never a model input).
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { createLimiter } from '../tjk-cache.mjs';
import { parseSourceTime } from '../race-gates.mjs';

const [racesFile = '.v2-data/races.json', out = '.v2-data/odds.json', cutoffMin = '5', from = '', to = '9999'] = process.argv.slice(2);
const cacheDir = '.backtest-cache';
const limiter = createLimiter({ concurrency: 2, minIntervalMs: 250 });
// No filesystem side effects when imported by tests or other modules.
export function istanbulMs(label) {
  if (typeof label !== 'string') return null;
  const match = label.trim().match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$/);
  if (!match) return null;
  const [, y, mo, d, h, mi, s = '0'] = match;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  if (date.getUTCFullYear() !== Number(y) || date.getUTCMonth() !== Number(mo) - 1
      || date.getUTCDate() !== Number(d) || Number(h) > 23 || Number(mi) > 59 || Number(s) > 59) return null;
  return parseSourceTime(label);
}

const validTimestamp = (value) => Number.isSafeInteger(value) && value > 0 && value <= 8.64e15;
const parseOdds = (value) => {
  if (value === null || value === undefined || value === '' || value === '-') return { missing: true };
  if (typeof value !== 'number' && typeof value !== 'string') return { invalid: true };
  const text = String(value).trim();
  if (!text || text === '-') return { missing: true };
  if (!/^\d+(?:[.,]\d+)?$/.test(text)) return { invalid: true };
  const odds = Number(text.replace(',', '.'));
  if (odds === 0) return { missing: true };
  return Number.isFinite(odds) && odds >= 1.01 && odds < 900 ? { odds } : { invalid: true };
};

async function get(url) {
  const file = join(cacheDir, `${createHash('sha1').update(url).digest('hex')}.json`);
  let text = await readFile(file, 'utf8').catch(() => null);
  if (text === null) {
    text = await limiter.schedule(async () => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const res = await fetch(url, { signal: AbortSignal.timeout(20_000), headers: { 'user-agent': 'TJK-Canli-Radar-Backtest/1.0' } });
          if (res.ok) return await res.text();
          if (res.status === 404) return null;
        } catch {}
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }
      return null;
    });
    if (text === null) return null;
    await writeFile(file, text).catch(() => {});
  }
  try { return JSON.parse(text); } catch { return null; }
}

export function oddsAtCutoff(history, postMs, cutoffMs) {
  const invalid = (reason) => ({
    runners: {}, lastLabelAt: null, labels: 0, historyValid: false, reasonCodes: [reason]
  });
  if (!validTimestamp(postMs) || !validTimestamp(cutoffMs) || cutoffMs > postMs) {
    return invalid('INVALID_HISTORY_CUTOFF');
  }
  if (!history || typeof history !== 'object' || Array.isArray(history)
      || !Array.isArray(history.labels) || !history.labels.length
      || !Array.isArray(history.datasets) || !history.datasets.length) {
    return invalid('INVALID_HISTORY_SHAPE');
  }
  const labels = Array.from(history.labels, istanbulMs);
  if (labels.some((at) => !validTimestamp(at))) return invalid('INVALID_HISTORY_TIMESTAMP');
  if (labels.some((at, index) => index > 0 && at <= labels[index - 1])) {
    return invalid('NON_MONOTONIC_HISTORY');
  }
  const runners = {};
  const seen = new Set();
  for (const set of history.datasets) {
    const label = typeof set?.label === 'number' || typeof set?.label === 'string'
      ? String(set.label).trim() : '';
    const number = Number(label);
    if (!/^\d{1,2}$/.test(label) || !Number.isSafeInteger(number) || number < 1 || number > 30) {
      return invalid('INVALID_HISTORY_RUNNER');
    }
    if (seen.has(number)) return invalid('DUPLICATE_HISTORY_RUNNER');
    seen.add(number);
    if (!Array.isArray(set.data) || set.data.length !== labels.length) {
      return invalid('HISTORY_LENGTH_MISMATCH');
    }
    let atCut = null;
    let final = null;
    for (let i = 0; i < set.data.length; i += 1) {
      const parsed = parseOdds(set.data[i]);
      if (parsed.invalid) return invalid('INVALID_HISTORY_ODDS');
      if (parsed.missing) continue;
      const at = labels[i];
      const odds = parsed.odds;
      if (at <= cutoffMs) atCut = { odds, at };
      if (at <= postMs) final = { odds, at };
    }
    runners[String(number)] = { odds: atCut?.odds ?? null, at: atCut?.at ?? null, final: final?.odds ?? null };
  }
  const lastLabel = labels.filter((at) => at <= cutoffMs).at(-1) ?? null;
  return { runners, lastLabelAt: lastLabel, labels: labels.length, historyValid: true, reasonCodes: [] };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await mkdir(cacheDir, { recursive: true });
  const { races } = JSON.parse(await readFile(racesFile, 'utf8'));
  const selected = races.filter((race) => race.date >= from && race.date <= to);
  const result = {};
  let done = 0;
  for (let i = 0; i < selected.length; i += 20) {
    await Promise.all(selected.slice(i, i + 20).map(async (race) => {
      const postMs = istanbulMs(`${race.date} ${race.time}:00`);
      const cutoffMs = postMs - Number(cutoffMin) * 60_000;
      const history = await get(`https://vhs.tjk.org/muhtemeller/data/history?date=${race.date}&hipodromkey=${encodeURIComponent(race.venue)}&no=${race.number}&bet=GANYAN`);
      result[`${race.date}|${race.venue}|${race.number}`] = history?.data ? { postMs, cutoffMs, ...oddsAtCutoff(history.data, postMs, cutoffMs) } : null;
    }));
    done += Math.min(20, selected.length - i);
    if (done % 400 === 0) process.stderr.write(`${done}/${selected.length} ${selected[Math.min(i + 19, selected.length - 1)].date}\n`);
  }
  await writeFile(out, JSON.stringify({ cutoffMin: Number(cutoffMin), races: result }));
  console.log(`races ${selected.length}, with history ${Object.values(result).filter(Boolean).length}`);
}
