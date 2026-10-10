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

const [racesFile = '.v2-data/races.json', out = '.v2-data/odds.json', cutoffMin = '5', from = '', to = '9999'] = process.argv.slice(2);
const cacheDir = '.backtest-cache';
const limiter = createLimiter({ concurrency: 2, minIntervalMs: 250 });
await mkdir(cacheDir, { recursive: true });

export const istanbulMs = (label) => Date.parse(`${String(label).trim().replace(' ', 'T')}+03:00`);
const validOdds = (value) => { const n = Number(value); return Number.isFinite(n) && n >= 1.01 ? n : null; };

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
  const labels = (history?.labels || []).map(istanbulMs);
  const runners = {};
  for (const set of history?.datasets || []) {
    let atCut = null;
    let final = null;
    (set.data || []).forEach((raw, i) => {
      const at = labels[i];
      const odds = validOdds(raw);
      if (!Number.isFinite(at) || odds === null) return;
      if (at <= cutoffMs) atCut = { odds, at };
      if (at <= postMs) final = { odds, at };
    });
    runners[String(set.label)] = { odds: atCut?.odds ?? null, at: atCut?.at ?? null, final: final?.odds ?? null };
  }
  const lastLabel = labels.filter((at) => at <= cutoffMs).at(-1) ?? null;
  return { runners, lastLabelAt: lastLabel, labels: labels.length };
}

if (import.meta.url === `file://${process.argv[1]}`) {
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
