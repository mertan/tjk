import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeRunners } from './analysis.mjs';
export { analyzeRunners } from './analysis.mjs';
import { isWithdrawn, raceDataIssues } from './race-gates.mjs';

const ROOT_DIR = fileURLToPath(new URL('.', import.meta.url));
const PUBLIC_DIR = join(ROOT_DIR, 'public');
const PORT = Number(process.env.PORT || 4173);

const STATIC_BASE = 'https://vhs-medya-cdn.tjk.org/muhtemeller/s';
const LIVE_BASE = 'https://vhs-medya.tjk.org/muhtemeller/s';
const HISTORY_BASE = 'https://vhs.tjk.org/muhtemeller/data/history';
const REPORT_BASE = 'https://medya-cdn.tjk.org/raporftp/TJKPDF';

const cache = new Map();

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function round(value, digits = 2) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function parseDecimal(value) {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim().replace(',', '.');
  if (!normalized) return null;
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function normalizeDate(value) {
  const date = String(value || '');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new HttpError(400, 'Tarih YYYY-AA-GG biçiminde olmalı.');
  }
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    throw new HttpError(400, 'Geçersiz tarih.');
  }
  return date;
}

function formatDateParts(date) {
  const [year, month, day] = normalizeDate(date).split('-');
  return { year, month, day, slash: `${year}/${month}/${day}`, display: `${day}.${month}.${year}` };
}

function safeKey(value) {
  const key = String(value || '').toUpperCase();
  if (!/^[A-Z0-9_-]{2,24}$/.test(key)) {
    throw new HttpError(400, 'Geçersiz hipodrom kodu.');
  }
  return key;
}

function safeRaceNumber(value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1 || number > 30) {
    throw new HttpError(400, 'Geçersiz koşu numarası.');
  }
  return String(number);
}

class HttpError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function cached(key, ttlMs, producer) {
  const current = cache.get(key);
  const now = Date.now();
  if (current && current.expiresAt > now) return current.value;
  const value = await producer();
  cache.set(key, { value, expiresAt: now + ttlMs });
  return value;
}

async function fetchText(url, { timeout = 12_000, optional = false } = {}) {
  try {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(timeout),
      headers: {
        accept: 'application/json,text/plain,text/csv,*/*',
        'user-agent': 'TJK-Canli-Radar/1.0'
      }
    });
    if (!response.ok) {
      if (optional && response.status === 404) return null;
      throw new HttpError(502, `TJK veri kaynağı ${response.status} yanıtı verdi.`);
    }
    return response.text();
  } catch (error) {
    if (optional) return null;
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, 'TJK veri kaynağına ulaşılamadı.', error.message);
  }
}

async function fetchJson(url, options) {
  const text = await fetchText(url, options);
  if (text === null) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(502, 'TJK kaynağından geçersiz veri geldi.');
  }
}

async function getToday() {
  const payload = await cached('today', 30_000, () =>
    fetchJson(`${STATIC_BASE}/date/checksum.json`)
  );
  if (!payload?.success || !payload.date) throw new HttpError(502, 'Güncel TJK tarihi alınamadı.');
  return normalizeDate(payload.date);
}

async function getChecksum(date) {
  const normalized = normalizeDate(date);
  const { slash } = formatDateParts(normalized);
  const payload = await cached(`checksum:${normalized}`, 4_000, () =>
    fetchJson(`${LIVE_BASE}/${slash}/checksum.json`)
  );
  if (!payload?.success || !payload.day) throw new HttpError(404, 'Bu tarihte yarış verisi bulunamadı.');
  return payload;
}

async function getDay(date) {
  const normalized = normalizeDate(date);
  const checksum = await getChecksum(normalized);
  const { slash } = formatDateParts(normalized);
  const payload = await cached(`day:${normalized}:${checksum.day}`, 20_000, () =>
    fetchJson(`${STATIC_BASE}/${slash}/day-${checksum.day}.json`)
  );
  if (!payload?.success || !Array.isArray(payload?.data?.yarislar)) {
    throw new HttpError(404, 'Günlük yarış programı bulunamadı.');
  }
  return { checksum, payload };
}

function summarizeDay(date, checksum, payload) {
  const venues = payload.data.yarislar.map((venue) => {
    const races = (venue.kosular || []).map((race) => ({
      number: Number(race.NO),
      time: race.SAAT,
      surface: race.PIST,
      status: race.DURUM
    }));
    return {
      key: venue.KEY,
      name: venue.HIPODROM || venue.YER || venue.KEY,
      place: venue.YER || venue.HIPODROM || venue.KEY,
      selectedRace: Number(venue.selected || races[0]?.number || 1),
      races,
      agfTables: (venue.agf || []).map((table) => ({
        name: table.name,
        official: Boolean(table.RESMI),
        legs: (table.kosular || []).map((leg) => ({ race: Number(leg.NO), horses: leg.ATLAR }))
      }))
    };
  });
  return {
    date,
    sourceTime: checksum.datetime || checksum.time || null,
    updatedAt: new Date().toISOString(),
    venues
  };
}

async function getRaceFeed(date, venueKey, raceNumber) {
  const normalized = normalizeDate(date);
  const key = safeKey(venueKey);
  const no = safeRaceNumber(raceNumber);
  const { checksum, payload: dayPayload } = await getDay(normalized);
  const venue = dayPayload.data.yarislar.find((item) => item?.KEY === key);
  if (!venue) throw new HttpError(404, 'Hipodrom bulunamadı.');
  if (!Array.isArray(venue.kosular)) throw new HttpError(502, 'Geçersiz koşu listesi.');
  const race = venue.kosular.find((item) => item && String(item.NO) === no);
  if (!race) throw new HttpError(404, 'Koşu bulunamadı.');

  const hashes = checksum.runs?.[`${key}-${no}`];
  if (!Array.isArray(hashes) || !hashes[0]) throw new HttpError(404, 'Koşu oranları henüz açılmadı.');
  const { slash } = formatDateParts(normalized);
  const racePayload = await cached(`race:${normalized}:${key}:${no}:${hashes[0]}`, 3_000, () =>
    fetchJson(`${STATIC_BASE}/${slash}/${key}-${no}-${hashes[0]}.json`)
  );
  if (!racePayload?.success || !racePayload?.data?.muhtemeller) {
    throw new HttpError(404, 'Koşu oranları bulunamadı.');
  }
  return { checksum, dayPayload, venue, race, racePayload, key, no };
}

function parseAgf(value) {
  const matches = [...String(value || '').matchAll(/%\s*([\d.,]+)\s*\((\d+)\)/g)];
  return matches.map((match) => ({
    percentage: parseDecimal(match[1]),
    rank: Number(match[2])
  })).filter((item) => item.percentage !== null);
}

// TJK uses semicolon-delimited CSV. Quoted cells may contain separators,
// escaped quotes or line breaks, so splitting a physical line is unsafe.
function programCsvRecords(csvText) {
  const records = [];
  let record = [];
  let field = '';
  let quoted = false;
  const text = String(csvText).replace(/^\uFEFF/, '');
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"' && !field.trim()) {
      quoted = true;
    } else if (char === ';' || char === '\n' || char === '\r') {
      record.push(field.trim());
      field = '';
      if (char !== ';') {
        records.push(record);
        record = [];
        if (char === '\r' && text[index + 1] === '\n') index += 1;
      }
    } else {
      field += char;
    }
  }
  // An incomplete quoted record can swallow later race boundaries. Fail closed.
  if (quoted) return [];
  if (field || record.length) records.push([...record, field.trim()]);
  return records;
}

export function parseProgramCsv(csvText) {
  if (!csvText) return [];
  const races = [];
  const seenRaceNumbers = new Set();
  const invalidRaceNumbers = new Set();
  let current = null;
  let headers = null;
  let headerSeen = false;
  let runnerNumbers = new Set();

  function invalidateCurrent() {
    invalidRaceNumbers.add(current.number);
    current = null;
    headers = null;
  }

  for (const columns of programCsvRecords(csvText)) {
    // Reset on every race-like boundary, even when its metadata is malformed.
    // Otherwise the following runners silently become part of the previous race.
    const boundary = columns[0]?.match(/^(\d+)\.?\s*ko[şs]u(?=\s|:|$)/iu);
    if (boundary) {
      current = null;
      headers = null;
      headerSeen = false;
      runnerNumbers = new Set();
      const number = Number(boundary[1]);
      if (seenRaceNumbers.has(number)) {
        // Neither copy of an ambiguous race may supply prediction inputs.
        invalidRaceNumbers.add(number);
        continue;
      }
      seenRaceNumbers.add(number);
      const raceMatch = columns[0].match(/^(\d+)\.\s*ko[şs]u(?=\s|:|$)\s*:?\s*(.*?)\s*$/iu);
      const timeMatch = raceMatch?.[2].match(/^(?:(.*?)\s+)?(\d{1,2})[.:](\d{2})$/u);
      if (!timeMatch || number < 1 || number > 30 || Number(timeMatch[2]) > 23 || Number(timeMatch[3]) > 59) continue;
      const distanceIndex = columns.findIndex((item, index) => index >= 3 && /^\d+\s*m$/i.test(item));
      const surfaceIndex = columns.findIndex((item, index) => index >= 3 && /^(Çim|Kum|Sentetik)$/i.test(item));
      current = {
        number,
        name: timeMatch[1]?.trim() || null,
        time: `${timeMatch[2].padStart(2, '0')}:${timeMatch[3]}`,
        type: columns[1] || null,
        condition: columns[2] || null,
        weightRule: distanceIndex > 3 ? columns.slice(3, distanceIndex).filter(Boolean).join(' · ') : columns[3] || null,
        distance: distanceIndex >= 0 ? columns[distanceIndex] : null,
        surface: surfaceIndex >= 0 ? columns[surfaceIndex] : null,
        runners: []
      };
      races.push(current);
      headers = null;
      continue;
    }
    if (!current) continue;
    if (columns.includes('At No')) {
      const labelled = columns.filter(Boolean);
      if (headerSeen || columns[0] !== 'At No' || !columns.includes('At İsmi') || new Set(labelled).size !== labelled.length) {
        invalidateCurrent();
        continue;
      }
      headers = columns;
      headerSeen = true;
      continue;
    }
    if (!columns.some(Boolean)) continue;
    // Official betting/coupling footers finish the runner table; they are not
    // runner IDs. Other malformed records within a table invalidate that race.
    if (/^(?:\[\(|(?:\d+\.\s*)?\d+['’]|(?:\d+\.\s*)?(?:ÇİFTE|GANYAN|İKİLİ|SIRALI İKİLİ|ÜÇLÜ BAHİS|PLASE|SABİT)(?:\s|$))/iu.test(columns[0] || '')) {
      headers = null;
      continue;
    }
    if (!headers) {
      if (/^\d+$/.test(columns[0] || '')) invalidateCurrent();
      continue;
    }
    const runnerNumber = Number(columns[0]);
    if (!/^\d+$/.test(columns[0] || '') || !Number.isSafeInteger(runnerNumber) || runnerNumber < 1 || runnerNumber > 30 || runnerNumbers.has(runnerNumber) || headers.some((header, index) => header && index >= columns.length)) {
      invalidateCurrent();
      continue;
    }
    runnerNumbers.add(runnerNumber);

    const row = Object.fromEntries(headers.flatMap((header, index) => header ? [[header, columns[index] ?? '']] : []));
    current.runners.push({
      number: Number(row['At No']),
      rawName: row['At İsmi'] || '',
      age: row['Yaş'] || null,
      sire: row['Orijin(Baba)'] || null,
      dam: row['Orijin(Anne)'] || null,
      weight: row['Kilo'] || null,
      jockey: row['Jokey Adı'] || null,
      owner: row['Sahip Adı'] || null,
      trainer: row['Antrenör Adı'] || null,
      stall: row.St || null,
      agf: parseAgf(row.AGF),
      rating: parseDecimal(row.H),
      lastSix: row['Son 6 Yarış'] || null,
      daysSinceRun: parseDecimal(row.KGS),
      last20: parseDecimal(row.s20),
      bestTime: row.EnİyiDerece || null
    });
  }
  return races.filter((race) => !invalidRaceNumbers.has(race.number));
}

export function buildProgramCsvUrl(date, venue) {
  const normalized = normalizeDate(date);
  const { year, display } = formatDateParts(normalized);
  const rawPlace = String(venue?.YER || '').normalize('NFC');
  if (/[\u0000-\u001f\u007f]/u.test(rawPlace)) return null;
  // Official report names join the display venue words (Belmont Park ABD ->
  // BelmontParkABD). Preserve Unicode: transliterating can change a real name.
  const place = rawPlace.replace(/\s+/gu, '');
  if (!place || place.length > 120 || !/^[\p{L}\p{N}][\p{L}\p{N}.'()_-]*$/u.test(place) || place.includes('..')) return null;
  const fileName = `${display}-${place}-GunlukYarisProgrami-TR.csv`;
  return `${REPORT_BASE}/${year}/${normalized}/CSV/GunlukYarisProgrami/${encodeURIComponent(fileName)}`;
}

async function getProgramForVenue(date, venue) {
  const normalized = normalizeDate(date);
  const url = buildProgramCsvUrl(normalized, venue);
  if (!url) return [];
  const csv = await cached(`csv:${normalized}:${venue.KEY}`, 30_000, () => fetchText(url, { optional: true }));
  if (!csv) return [];
  const envelope = programCsvRecords(csv)[0];
  const { year, month, day } = formatDateParts(normalized);
  const placeKey = (value) => String(value || '').normalize('NFC').replace(/\s+/gu, '');
  if (!envelope || envelope[2] !== `${day}/${month}/${year}` || placeKey(envelope[0]) !== placeKey(venue.YER)) return [];
  return parseProgramCsv(csv);
}

async function getHistory(date, venueKey, raceNumber, horseNumber) {
  const normalized = normalizeDate(date);
  const key = safeKey(venueKey);
  const no = safeRaceNumber(raceNumber);
  const horse = safeRaceNumber(horseNumber);
  const query = new URLSearchParams({
    date: normalized,
    hipodromkey: key,
    no,
    bet: 'GANYAN',
    horse
  });
  const payload = await cached(`history:${normalized}:${key}:${no}:${horse}`, 8_000, () =>
    fetchJson(`${HISTORY_BASE}?${query.toString()}`, { optional: true })
  );
  if (!payload?.success || !Array.isArray(payload?.data?.labels)) return [];
  const values = payload.data.datasets?.[0]?.data || [];
  return payload.data.labels.map((label, index) => ({
    label,
    time: String(label).slice(11, 16),
    odds: parseDecimal(values[index])
  })).filter((point) => point.odds && point.odds > 0 && point.odds < 900);
}

function historyMetrics(points, currentOdds) {
  const valid = points.filter((point) => Number.isFinite(point.odds));
  const opening = valid[0]?.odds ?? null;
  const recorded = valid.at(-1)?.odds ?? currentOdds;
  const previous = valid.length > 1 ? valid.at(-2).odds : recorded;
  const values = [...valid.map((point) => point.odds), currentOdds].filter(Number.isFinite);
  return {
    openingOdds: opening,
    recordedOdds: round(recorded),
    currentOdds: round(currentOdds),
    lowOdds: round(Math.min(...values)),
    highOdds: round(Math.max(...values)),
    movementPercent: opening ? round(((currentOdds - opening) / opening) * 100, 1) : null,
    shortMovementPercent: previous ? round(((currentOdds - previous) / previous) * 100, 1) : 0,
    points: valid
  };
}

function formatMarketItems(bet, horseNames, nextRaceHorseNames) {
  return (bet?.muhtemeller || []).slice(0, 24).map((item) => {
    const first = String(item.S1 || '');
    const second = String(item.S2 || '');
    const firstName = horseNames[first] || first;
    const secondNames = bet.B === 'ÇİFTE' ? nextRaceHorseNames : horseNames;
    const secondName = secondNames?.[second] || second;
    return {
      selection: second ? `${first}-${second}` : first,
      label: second ? `${firstName} / ${secondName}` : firstName,
      odds: parseDecimal(item.G)
    };
  }).filter((item) => item.odds);
}

async function collectRaceAnalysis(date, venueKey, raceNumber) {
  const feed = await getRaceFeed(date, venueKey, raceNumber);
  const programRaces = await getProgramForVenue(date, feed.venue);
  const programRace = programRaces.find((race) => String(race.number) === feed.no) || null;
  const programByNumber = new Map((programRace?.runners || []).map((runner) => [String(runner.number), runner]));
  const horseNames = feed.venue.atlar?.[feed.no] || {};
  const nextRaceHorseNames = feed.venue.atlar?.[String(Number(feed.no) + 1)] || {};
  const bets = feed.racePayload.data.muhtemeller.bahisler;
  if (!Array.isArray(bets) || bets.some((bet) => !bet || typeof bet.B !== 'string' || !Array.isArray(bet.muhtemeller) || bet.muhtemeller.some((row) => !row || typeof row !== 'object'))) {
    throw new HttpError(502, 'Geçersiz oran tablosu.');
  }
  const ganyan = bets.find((bet) => bet.B === 'GANYAN');
  if (!ganyan || !Array.isArray(ganyan.muhtemeller)) throw new HttpError(404, 'Bu koşuda ganyan oranı bulunamadı.');
  if (ganyan.muhtemeller.some((row) => !/^\d{1,2}$/u.test(String(row.S1)) || Number(row.S1) < 1 || Number(row.S1) > 30)) {
    throw new HttpError(502, 'Geçersiz kaynak at numarası.');
  }

  const historyResults = await Promise.all((ganyan.muhtemeller || []).map(async (item) => {
    const number = String(item.S1);
    const currentOdds = parseDecimal(item.G);
    if (!currentOdds || isWithdrawn(item.KOSMAZ)) return null;
    const history = await getHistory(date, feed.key, feed.no, number);
    return { number, currentOdds, history, ...historyMetrics(history, currentOdds) };
  }));
  const historyByNumber = new Map(historyResults.filter(Boolean).map((item) => [item.number, item]));

  const runners = (ganyan.muhtemeller || []).map((item) => {
    const number = String(item.S1);
    const currentOdds = parseDecimal(item.G);
    const program = programByNumber.get(number) || {};
    const historyData = historyByNumber.get(number) || historyMetrics([], currentOdds);
    const agfSeries = program.agf || [];
    return {
      number: Number(number),
      name: horseNames[number] || program.rawName || '',
      currentOdds,
      openingOdds: historyData.openingOdds,
      lowOdds: historyData.lowOdds,
      highOdds: historyData.highOdds,
      movementPercent: historyData.movementPercent,
      shortMovementPercent: historyData.shortMovementPercent,
      history: historyData.points,
      agf: agfSeries,
      agfLatest: agfSeries.at(-1)?.percentage ?? null,
      agfRank: agfSeries.at(-1)?.rank ?? null,
      rating: program.rating ?? null,
      jockey: program.jockey ?? null,
      trainer: program.trainer ?? null,
      owner: program.owner ?? null,
      weight: program.weight ?? null,
      stall: program.stall ?? null,
      age: program.age ?? null,
      lastSix: program.lastSix ?? null,
      bestTime: program.bestTime ?? null,
      out: isWithdrawn(item.KOSMAZ)
    };
  }).filter((runner) => !runner.out);

  const analysis = analyzeRunners(runners, { reasonCodes: raceDataIssues(feed, programRace) });
  const marketMap = Object.fromEntries(bets.map((bet) => [bet.B, formatMarketItems(bet, horseNames, nextRaceHorseNames)]));
  const raceInfo = feed.racePayload.data.muhtemeller;
  return {
    date: normalizeDate(date),
    updatedAt: new Date().toISOString(),
    sourceTime: feed.checksum.datetime || raceInfo.timestamp || null,
    venue: {
      key: feed.key,
      name: feed.venue.HIPODROM || feed.venue.YER || feed.key,
      place: feed.venue.YER || null
    },
    race: {
      number: Number(feed.no),
      time: raceInfo.SAAT || feed.race.SAAT,
      surface: raceInfo.PIST || feed.race.PIST,
      status: raceInfo.DURUM || feed.race.DURUM,
      type: programRace?.type || null,
      name: programRace?.name || null,
      condition: programRace?.condition || null,
      distance: programRace?.distance || null,
      weightRule: programRace?.weightRule || null
    },
    analysis: {
      status: analysis.status,
      reasonCodes: analysis.reasonCodes,
      modelVersion: 'market-no-agf-v2',
      scoreKind: analysis.scoreKind,
      confidence: analysis.confidence,
      confidenceLabel: analysis.confidenceLabel,
      summary: analysis.summary,
      agreement: analysis.agreement,
      picks: {
        leader: pickSummary(analysis.leader),
        oddsLeader: pickSummary(analysis.oddsLeader),
        agfLeader: pickSummary(analysis.agfLeader),
        steam: pickSummary(analysis.steam),
        value: pickSummary(analysis.value),
        surprise: pickSummary(analysis.surprise)
      }
    },
    // Legacy Python consumers score `runners` themselves and ignore status.
    // A PAS response must never expose a candidate set to those consumers.
    runners: analysis.status === 'OK' ? analysis.runners : [],
    observations: { runners: analysis.status === 'PAS' ? analysis.runners : [] },
    markets: {
      ganyan: marketMap.GANYAN || [],
      ikili: marketMap['İKİLİ'] || [],
      siraliIkili: marketMap['SIRALI İKİLİ'] || [],
      cifte: marketMap['ÇİFTE'] || []
    },
    methodology: 'AGF puana, sıralamaya ve sinyal gücüne katılmaz. Ganyan, handikap ve gerçek oran geçmişi 55:10:7 oranında normalize edilir; zorunlu veri eksikse PAS. Model puanı ve sinyal gücü kalibre edilmiş kazanma olasılığı değildir.',
    warning: 'Puanlar sezgisel karşılaştırmadır; kesin sonuç veya kazanç garantisi değildir. Oran düşüşü para yönünü gösterir ancak yatırılan kesin TL tutarı TJK akışında bulunmaz.'
  };
}

export async function buildRaceAnalysis(date, venueKey, raceNumber) {
  const normalized = normalizeDate(date);
  const key = safeKey(venueKey);
  const no = safeRaceNumber(raceNumber);
  try {
    return await collectRaceAnalysis(normalized, key, no);
  } catch (error) {
    if (!(error instanceof HttpError) || ![404, 502].includes(error.status)) throw error;
    const analysis = analyzeRunners([], { reasonCodes: ['SOURCE_UNAVAILABLE'] });
    return {
      date: normalized, updatedAt: new Date().toISOString(), sourceTime: null,
      venue: { key, name: key, place: null },
      race: { number: Number(no), time: null, status: 'VERİ YOK' },
      analysis: { status: 'PAS', reasonCodes: analysis.reasonCodes,
        modelVersion: 'market-no-agf-v2', scoreKind: analysis.scoreKind,
        confidence: 0, confidenceLabel: analysis.confidenceLabel,
        summary: analysis.summary, agreement: false,
        picks: { leader: null, oddsLeader: null, agfLeader: null, steam: null, value: null, surprise: null } },
      runners: [], observations: { runners: [] },
      markets: { ganyan: [], ikili: [], siraliIkili: [], cifte: [] },
      methodology: 'AGF puana katılmaz. Zorunlu kaynak verisi olmadan aday üretilmez.',
      warning: 'PAS: kaynak verisi alınamadı; önceki tahmin güncel sonuç olarak kullanılmaz.'
    };
  }
}

function pickSummary(runner) {
  if (!runner) return null;
  return {
    number: runner.number,
    name: runner.name,
    odds: runner.currentOdds,
    probability: runner.modelProbability,
    agf: runner.agfLatest,
    movement: runner.movementPercent,
    edge: runner.edge
  };
}

function json(res, status, body) {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff'
  });
  res.end(JSON.stringify(body));
}

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8'
};

async function serveStatic(pathname, res) {
  const requested = pathname === '/' ? 'index.html' : decodeURIComponent(pathname.slice(1));
  const filePath = resolve(PUBLIC_DIR, requested);
  if (relative(PUBLIC_DIR, filePath).startsWith('..')) throw new HttpError(403, 'Erişim reddedildi.');
  const info = await stat(filePath).catch(() => null);
  if (!info?.isFile()) throw new HttpError(404, 'Sayfa bulunamadı.');
  const data = await readFile(filePath);
  res.writeHead(200, {
    'content-type': mimeTypes[extname(filePath)] || 'application/octet-stream',
    'cache-control': filePath.endsWith('index.html') ? 'no-cache' : 'public, max-age=3600',
    'content-security-policy': "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff'
  });
  res.end(data);
}

async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  if (req.method !== 'GET') throw new HttpError(405, 'Yalnızca GET destekleniyor.');

  if (url.pathname === '/api/status') {
    return json(res, 200, { ok: true, today: await getToday(), time: new Date().toISOString() });
  }
  if (url.pathname === '/api/day') {
    const date = normalizeDate(url.searchParams.get('date') || await getToday());
    const { checksum, payload } = await getDay(date);
    return json(res, 200, summarizeDay(date, checksum, payload));
  }
  if (url.pathname === '/api/race') {
    const date = normalizeDate(url.searchParams.get('date') || await getToday());
    const venue = url.searchParams.get('venue');
    const race = url.searchParams.get('race');
    return json(res, 200, await buildRaceAnalysis(date, venue, race));
  }
  if (url.pathname === '/api/history') {
    const date = normalizeDate(url.searchParams.get('date') || await getToday());
    const points = await getHistory(date, url.searchParams.get('venue'), url.searchParams.get('race'), url.searchParams.get('horse'));
    return json(res, 200, { date, points });
  }
  return serveStatic(url.pathname, res);
}

export function startServer({ port = PORT, host = '0.0.0.0' } = {}) {
  const server = createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      const status = error instanceof HttpError ? error.status : 500;
      json(res, status, {
        error: error.message || 'Beklenmeyen sunucu hatası.',
        ...(process.env.NODE_ENV === 'development' && error.detail ? { detail: error.detail } : {})
      });
    });
  });
  server.listen(port, host, () => {
    console.log(`TJK Canlı Radar http://localhost:${port} adresinde çalışıyor.`);
  });
  return server;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) startServer();
