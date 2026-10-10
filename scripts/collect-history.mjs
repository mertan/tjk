#!/usr/bin/env node
/** Read-only collector: official program + results CSVs for finished domestic race days. */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { buildProgramCsvUrl } from '../server.mjs';
import { createLimiter } from '../tjk-cache.mjs';
import { joinResults, parseRaceTables } from '../model-v2/dataset.mjs';

const [from, to, out = '.v2-data/races.json', cacheDir = '.backtest-cache'] = process.argv.slice(2);
const today = new Date(Date.now() + 3 * 3_600_000).toISOString().slice(0, 10);
if (!/^\d{4}-\d{2}-\d{2}$/.test(from || '') || !/^\d{4}-\d{2}-\d{2}$/.test(to || '') || to >= today) {
  console.error('Kullanım: collect-history.mjs YYYY-AA-GG YYYY-AA-GG [çıktı.json] (yalnızca bitmiş günler)');
  process.exit(2);
}
const limiter = createLimiter({ concurrency: 2, minIntervalMs: 250 });
await mkdir(cacheDir, { recursive: true });
await mkdir(out.split('/').slice(0, -1).join('/') || '.', { recursive: true });

async function get(url, json) {
  const file = join(cacheDir, `${createHash('sha1').update(url).digest('hex')}.${json ? 'json' : 'txt'}`);
  let text = await readFile(file, 'utf8').catch(() => null);
  if (text === null) {
    text = await limiter.schedule(async () => {
      for (let attempt = 0; ; attempt += 1) {
        try {
          const res = await fetch(url, { signal: AbortSignal.timeout(20_000), headers: { 'user-agent': 'TJK-Canli-Radar-Backtest/1.0' } });
          if (res.status === 404) return null;
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return await res.text();
        } catch (error) {
          if (attempt >= 2) return null;
          await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
        }
      }
    });
    if (text === null) return null;
    await writeFile(file, text);
  }
  if (!json) return text;
  try { return JSON.parse(text); } catch { return null; }
}

const races = [];
const stats = { days: 0, venueDays: 0, races: 0, noResults: 0, partialJoin: 0 };
for (let t = Date.parse(`${from}T00:00:00Z`); t <= Date.parse(`${to}T00:00:00Z`); t += 86_400_000) {
  const date = new Date(t).toISOString().slice(0, 10);
  const slash = date.replaceAll('-', '/');
  const checksum = await get(`https://vhs-medya.tjk.org/muhtemeller/s/${slash}/checksum.json`, true);
  if (!checksum?.day) continue;
  const day = await get(`https://vhs-medya-cdn.tjk.org/muhtemeller/s/${slash}/day-${checksum.day}.json`, true);
  stats.days += 1;
  const venues = (day?.data?.yarislar || []).filter((v) => !v.YURTDISI);
  await Promise.all(venues.map(async (venue) => {
    const programUrl = buildProgramCsvUrl(date, venue);
    if (!programUrl) return;
    const [program, results] = await Promise.all([
      get(programUrl, false),
      get(programUrl.replaceAll('GunlukYarisProgrami', 'GunlukYarisSonuclari'), false)
    ]);
    if (!program) return;
    stats.venueDays += 1;
    const resultRaces = new Map(parseRaceTables(results).map((r) => [r.number, r]));
    const year = Number(date.slice(0, 4));
    for (const race of parseRaceTables(program)) {
      const result = resultRaces.get(race.number);
      if (!result) { stats.noResults += 1; continue; }
      const { runners, matched } = joinResults(race, result, year);
      if (matched !== result.rows.length) stats.partialJoin += 1;
      const { rows, ...meta } = race;
      races.push({ date, venue: venue.KEY, ...meta, runners });
      stats.races += 1;
    }
  }));
  if (stats.days % 10 === 0) process.stderr.write(`${date} ${JSON.stringify(stats)}\n`);
}
races.sort((a, b) => (a.date + a.time + a.venue).localeCompare(b.date + b.time + b.venue));
await writeFile(out, JSON.stringify({ from, to, stats, races }));
console.log(JSON.stringify(stats));
