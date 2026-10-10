#!/usr/bin/env node
/**
 * Read-only point-in-time backtest over finished TJK races.
 * Usage: node scripts/backtest.mjs --from 2026-09-26 --to 2026-10-09 [--venues domestic|all|KEY,KEY]
 *        [--cutoff-min 5] [--min-sample 30] [--out backtest-report.json]
 * Only past dates are allowed. Responses are cached under .backtest-cache/.
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { buildProgramCsvUrl, programForVenueCsv } from '../server.mjs';
import { createLimiter } from '../tjk-cache.mjs';
import { isWithdrawn } from '../race-gates.mjs';
import { postTimes, predictAtCutoff, resultRanks, resultsCsvWinners, summarize } from '../backtest.mjs';

const STATIC_BASE = 'https://vhs-medya-cdn.tjk.org/muhtemeller/s';
const LIVE_BASE = 'https://vhs-medya.tjk.org/muhtemeller/s';
const HISTORY_BASE = 'https://vhs.tjk.org/muhtemeller/data/history';

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith('--')) out[argv[i].slice(2)] = argv[i + 1]?.startsWith('--') || argv[i + 1] === undefined ? 'true' : argv[++i];
  }
  return out;
}

const opts = args(process.argv.slice(2));
const today = new Date(Date.now() + 3 * 3_600_000).toISOString().slice(0, 10);
const from = opts.from;
const to = opts.to || from;
if (!/^\d{4}-\d{2}-\d{2}$/.test(from || '') || !/^\d{4}-\d{2}-\d{2}$/.test(to) || from > to) {
  console.error('Kullanım: --from YYYY-AA-GG [--to YYYY-AA-GG]');
  process.exit(2);
}
if (to >= today) {
  console.error(`Yalnızca bitmiş günler: --to ${today} tarihinden önce olmalı.`);
  process.exit(2);
}
const cutoffMin = Number(opts['cutoff-min'] || 5);
const minSample = Number(opts['min-sample'] || 30);
const venuesArg = opts.venues || 'domestic';
const cacheDir = opts['cache-dir'] || '.backtest-cache';
const limiter = createLimiter({ concurrency: Number(opts.concurrency || 2), minIntervalMs: Number(opts['interval-ms'] || 300) });
await mkdir(cacheDir, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let requests = 0;

async function get(url, { json = true } = {}) {
  const file = join(cacheDir, `${createHash('sha1').update(url).digest('hex')}.${json ? 'json' : 'txt'}`);
  let text = await readFile(file, 'utf8').catch(() => null);
  if (text === null) {
    text = await limiter.schedule(async () => {
      for (let attempt = 0; ; attempt += 1) {
        try {
          requests += 1;
          const response = await fetch(url, {
            signal: AbortSignal.timeout(20_000),
            headers: { accept: 'application/json,text/csv,*/*', 'user-agent': 'TJK-Canli-Radar-Backtest/1.0' }
          });
          if (response.status === 404) return null;
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return await response.text();
        } catch (error) {
          if (attempt >= 2) throw error;
          await sleep(1000 * (attempt + 1));
        }
      }
    }).catch(() => null);
    if (text === null) return null;
    await writeFile(file, text);
  }
  if (!json) return text;
  try { return JSON.parse(text); } catch { return null; }
}

function parseDecimal(value) {
  const number = Number(String(value ?? '').trim().replace(',', '.'));
  return String(value ?? '').trim() && Number.isFinite(number) ? number : null;
}

async function history(date, key, no, horse) {
  const query = new URLSearchParams({ date, hipodromkey: key, no: String(no), bet: 'GANYAN', horse: String(horse) });
  const payload = await get(`${HISTORY_BASE}?${query}`);
  if (!payload?.success || !Array.isArray(payload?.data?.labels)) return [];
  const values = payload.data.datasets?.[0]?.data || [];
  return payload.data.labels.map((label, index) => ({ label: String(label), odds: parseDecimal(values[index]) }));
}

function dates(start, end) {
  const list = [];
  for (let t = Date.parse(`${start}T00:00:00Z`); t <= Date.parse(`${end}T00:00:00Z`); t += 86_400_000) list.push(new Date(t).toISOString().slice(0, 10));
  return list;
}

const wanted = (venue) => venuesArg === 'all' || (venuesArg === 'domestic' ? !venue.YURTDISI : venuesArg.split(',').includes(venue.KEY));
const records = [];

for (const date of dates(from, to)) {
  const slash = date.replaceAll('-', '/');
  const checksum = await get(`${LIVE_BASE}/${slash}/checksum.json`);
  if (!checksum?.success || !checksum.day) continue;
  const day = await get(`${STATIC_BASE}/${slash}/day-${checksum.day}.json`);
  for (const venue of (day?.data?.yarislar || []).filter(wanted)) {
    const programUrl = buildProgramCsvUrl(date, venue);
    const programRaces = programUrl ? programForVenueCsv(await get(programUrl, { json: false }), date, venue) : [];
    const csvWinners = programUrl
      ? resultsCsvWinners(await get(programUrl.replaceAll('GunlukYarisProgrami', 'GunlukYarisSonuclari'), { json: false }))
      : new Map();
    const races = Array.isArray(venue.kosular) ? venue.kosular : [];
    const posts = postTimes(date, races.map((race) => race.SAAT));
    for (const [index, race] of races.entries()) {
      const no = Number(race.NO);
      const base = { date, venue: venue.KEY, race: no, foreign: Boolean(venue.YURTDISI), postTime: posts[index] ? new Date(posts[index]).toISOString() : null };
      const unsettled = (reason) => records.push({ ...base, prediction: { status: 'PAS', reasonCodes: [reason] }, winners: null, resultCheck: 'MISSING' });
      if (race.DURUM !== 'RESMİ') { unsettled('RACE_NOT_OFFICIAL'); continue; }
      const hash = checksum.runs?.[`${venue.KEY}-${no}`]?.[0];
      const payload = hash ? await get(`${STATIC_BASE}/${slash}/${venue.KEY}-${no}-${hash}.json`) : null;
      const rows = payload?.data?.muhtemeller?.bahisler?.find((bet) => bet?.B === 'GANYAN')?.muhtemeller;
      if (!Array.isArray(rows) || !rows.length || !posts[index]) { unsettled('SOURCE_UNAVAILABLE'); continue; }

      // Results: post-race ranks, cross-checked against the official results CSV.
      const ranks = resultRanks(rows);
      const payloadWinners = [...ranks].filter(([, rank]) => rank === 1).map(([number]) => number).sort((a, b) => a - b);
      const official = csvWinners.get(no)?.slice().sort((a, b) => a - b);
      let winners = payloadWinners.length ? payloadWinners : null;
      let resultCheck = winners ? 'PAYLOAD_ONLY' : 'MISSING';
      if (winners && official) {
        resultCheck = JSON.stringify(winners) === JSON.stringify(official) ? 'CONFIRMED' : 'CONFLICT';
        if (resultCheck === 'CONFLICT') winners = null;
      }

      // Prediction inputs: timestamped odds points <= cutoff, pre-race program. No result fields.
      const programRace = programRaces.find((item) => item.number === no) || null;
      const programBy = new Map((programRace?.runners || []).map((runner) => [runner.number, runner]));
      const names = venue.atlar?.[String(no)] || {};
      const runners = await Promise.all(rows.map(async (row) => {
        const number = Number(row.S1);
        const out = isWithdrawn(row.KOSMAZ);
        return {
          number,
          name: names[number] || programBy.get(number)?.rawName || '',
          out,
          history: out ? [] : await history(date, venue.KEY, no, number),
          program: programBy.get(number)
        };
      }));
      const prediction = predictAtCutoff({
        race: { time: race.SAAT, surface: race.PIST },
        programRace,
        foreign: Boolean(venue.YURTDISI),
        runners,
        cutoffMs: posts[index] - cutoffMin * 60_000
      });
      records.push({ ...base, prediction, winners, resultCheck });
    }
    process.stderr.write(`${date} ${venue.KEY}: ${races.length} koşu, toplam istek ${requests}\n`);
  }
}

const summary = summarize(records, { minSample });
const byVenue = {};
for (const record of records) {
  const entry = byVenue[record.venue] ||= { races: 0, valid: 0, leaderWins: 0 };
  entry.races += 1;
  if (record.prediction.status === 'OK' && record.winners) {
    entry.valid += 1;
    if (record.winners.includes(record.prediction.leader)) entry.leaderWins += 1;
  }
}
const report = {
  generatedAt: new Date().toISOString(),
  params: { from, to, venues: venuesArg, cutoffMinutesBeforePost: cutoffMin, minSample, modelVersion: 'market-no-agf-v2' },
  methodNotes: [
    'Tahmin girdileri: kesimden (koşu saati - cutoff) önceki zaman damgalı ganyan noktaları ve koşu öncesi program CSV (handikap).',
    'Sonuç: koşu sonrası resmi GANYAN R sırası; resmi sonuç CSV kazananıyla çapraz kontrol (CONFLICT olan koşu dışlanır).',
    'Bilinen sınır: koşmaz listesi ve program CSV içindeki AGF serisi koşu sonrası arşivden okunur; AGF yalnızca karşılaştırma tabanında kullanılır.',
    `Oranlar yalnızca en az ${minSample} geçerli tahminde verilir; altında yalnızca sayımlar raporlanır.`
  ],
  summary,
  byVenue,
  resultChecks: records.reduce((acc, record) => ({ ...acc, [record.resultCheck]: (acc[record.resultCheck] || 0) + 1 }), {}),
  records
};
if (opts.out) await writeFile(opts.out, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify({ params: report.params, summary, byVenue, resultChecks: report.resultChecks }, null, 2));
