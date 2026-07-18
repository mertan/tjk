import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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
  const venue = dayPayload.data.yarislar.find((item) => item.KEY === key);
  if (!venue) throw new HttpError(404, 'Hipodrom bulunamadı.');
  const race = (venue.kosular || []).find((item) => String(item.NO) === no);
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

export function parseProgramCsv(csvText) {
  if (!csvText) return [];
  const lines = String(csvText).replace(/^\uFEFF/, '').split(/\r?\n/);
  const races = [];
  let current = null;
  let headers = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    const columns = rawLine.split(';').map((item) => item.trim());
    const raceMatch = columns[0]?.match(/^(\d+)\.\s*Kosu\s*:\s*(.*?)\s*(\d{1,2}\.\d{2})\s*$/i);
    if (raceMatch) {
      const distanceIndex = columns.findIndex((item, index) => index >= 3 && /^\d+\s*m$/i.test(item));
      const surfaceIndex = columns.findIndex((item, index) => index >= 3 && /^(Çim|Kum|Sentetik)$/i.test(item));
      current = {
        number: Number(raceMatch[1]),
        name: raceMatch[2] || null,
        time: raceMatch[3].replace('.', ':'),
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
    if (columns[0] === 'At No') {
      headers = columns;
      continue;
    }
    if (!headers || !/^\d+$/.test(columns[0] || '')) continue;

    const row = Object.fromEntries(headers.map((header, index) => [header, columns[index] ?? '']));
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
  return races;
}

async function getProgramForVenue(date, venue) {
  const normalized = normalizeDate(date);
  const { year, slash, display } = formatDateParts(normalized);
  const place = String(venue.YER || '').trim();
  if (!place) return [];
  const fileName = `${display}-${place}-GunlukYarisProgrami-TR.csv`;
  const url = `${REPORT_BASE}/${year}/${normalized}/CSV/GunlukYarisProgrami/${encodeURIComponent(fileName)}`;
  const csv = await cached(`csv:${normalized}:${venue.KEY}`, 30_000, () => fetchText(url, { optional: true }));
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
  const opening = valid[0]?.odds ?? currentOdds;
  const recorded = valid.at(-1)?.odds ?? currentOdds;
  const previous = valid.length > 1 ? valid.at(-2).odds : recorded;
  const values = [...valid.map((point) => point.odds), currentOdds].filter(Number.isFinite);
  return {
    openingOdds: round(opening),
    recordedOdds: round(recorded),
    currentOdds: round(currentOdds),
    lowOdds: round(Math.min(...values)),
    highOdds: round(Math.max(...values)),
    movementPercent: opening ? round(((currentOdds - opening) / opening) * 100, 1) : 0,
    shortMovementPercent: previous ? round(((currentOdds - previous) / previous) * 100, 1) : 0,
    points: valid.slice(-40)
  };
}

function normalizeVector(values, fallbackLength) {
  const safe = values.map((value) => (Number.isFinite(value) && value > 0 ? value : 0));
  const total = safe.reduce((sum, value) => sum + value, 0);
  if (!total) return Array.from({ length: fallbackLength }, () => 1 / fallbackLength);
  return safe.map((value) => value / total);
}

export function analyzeRunners(runners) {
  if (!runners.length) return { runners: [], confidence: 0, confidenceLabel: 'Veri yok' };
  const market = normalizeVector(runners.map((runner) => 1 / runner.currentOdds), runners.length);
  const hasAgf = runners.filter((runner) => Number.isFinite(runner.agfLatest)).length >= Math.ceil(runners.length / 2);
  const agf = normalizeVector(runners.map((runner) => runner.agfLatest || 0), runners.length);
  const hasRating = runners.filter((runner) => Number.isFinite(runner.rating)).length >= Math.ceil(runners.length / 2);
  const rating = normalizeVector(runners.map((runner) => runner.rating || 0), runners.length);
  const hasHistory = runners.filter((runner) => Number.isFinite(runner.openingOdds)).length >= Math.ceil(runners.length / 2);
  const momentum = normalizeVector(runners.map((runner) => {
    if (!runner.openingOdds) return 1;
    const contraction = clamp((runner.openingOdds - runner.currentOdds) / runner.openingOdds, -0.8, 0.8);
    return Math.exp(contraction * 2);
  }), runners.length);

  const weights = [
    { weight: 0.55, values: market },
    ...(hasAgf ? [{ weight: 0.28, values: agf }] : []),
    ...(hasRating ? [{ weight: 0.10, values: rating }] : []),
    ...(hasHistory ? [{ weight: 0.07, values: momentum }] : [])
  ];
  const weightTotal = weights.reduce((sum, factor) => sum + factor.weight, 0);
  const model = runners.map((_, index) =>
    weights.reduce((sum, factor) => sum + factor.values[index] * factor.weight, 0) / weightTotal
  );

  const enriched = runners.map((runner, index) => ({
    ...runner,
    marketProbability: round(market[index] * 100, 1),
    modelProbability: round(model[index] * 100, 1),
    edge: round((model[index] - market[index]) * 100, 1)
  })).sort((a, b) => b.modelProbability - a.modelProbability);

  enriched.forEach((runner, index) => {
    runner.modelRank = index + 1;
    runner.supportSignal = runner.movementPercent <= -20
      ? 'Güçlü destek'
      : runner.movementPercent <= -8
        ? 'Destek artıyor'
        : runner.movementPercent >= 20
          ? 'Belirgin gevşeme'
          : runner.movementPercent >= 8
            ? 'Gevşiyor'
            : 'Dengeli';
  });

  const oddsLeader = [...enriched].sort((a, b) => a.currentOdds - b.currentOdds)[0];
  const agfLeader = hasAgf ? [...enriched].sort((a, b) => (b.agfLatest || 0) - (a.agfLatest || 0))[0] : null;
  const leader = enriched[0];
  const runnerUp = enriched[1];
  const marketAgreement = leader.number === oddsLeader.number;
  const agreement = Boolean(agfLeader && marketAgreement && leader.number === agfLeader.number);
  const gap = leader.modelProbability - (runnerUp?.modelProbability || 0);
  const historyCoverage = runners.filter((runner) => runner.history?.length > 2).length / runners.length;
  const confidence = Math.round(clamp(42 + gap * 1.7 + (agreement ? 10 : marketAgreement ? 4 : 0) + historyCoverage * 8, 38, 86));
  const confidenceLabel = confidence >= 72 ? 'Yüksek' : confidence >= 57 ? 'Orta' : 'Temkinli';

  const valueCandidates = enriched.filter((runner) => runner.number !== leader.number && runner.edge > 0);
  const value = valueCandidates.sort((a, b) => b.edge - a.edge)[0] || enriched[1] || leader;
  const surpriseCandidates = enriched.filter((runner) => runner.currentOdds >= 6 && runner.edge >= -0.5);
  const surprise = surpriseCandidates.sort((a, b) => b.modelProbability - a.modelProbability)[0] || null;
  const steam = [...enriched].sort((a, b) => a.movementPercent - b.movementPercent)[0];

  return {
    runners: enriched,
    confidence,
    confidenceLabel,
    leader,
    oddsLeader,
    agfLeader,
    steam,
    value,
    surprise,
    agreement,
    summary: agreement
      ? `Ganyan piyasası ve AGF ${leader.number} numarada birleşiyor.`
      : !agfLeader
        ? `${leader.number} numara piyasada önde; AGF verisi henüz yayınlanmadı.`
        : `${leader.number} numara modelde önde; piyasa sinyalleri tam olarak birleşmiyor.`
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

async function buildRaceAnalysis(date, venueKey, raceNumber) {
  const feed = await getRaceFeed(date, venueKey, raceNumber);
  const programRaces = await getProgramForVenue(date, feed.venue);
  const programRace = programRaces.find((race) => String(race.number) === feed.no) || null;
  const programByNumber = new Map((programRace?.runners || []).map((runner) => [String(runner.number), runner]));
  const horseNames = feed.venue.atlar?.[feed.no] || {};
  const nextRaceHorseNames = feed.venue.atlar?.[String(Number(feed.no) + 1)] || {};
  const bets = feed.racePayload.data.muhtemeller.bahisler || [];
  const ganyan = bets.find((bet) => bet.B === 'GANYAN');
  if (!ganyan) throw new HttpError(404, 'Bu koşuda ganyan oranı bulunamadı.');

  const historyResults = await Promise.all((ganyan.muhtemeller || []).map(async (item) => {
    const number = String(item.S1);
    const currentOdds = parseDecimal(item.G);
    if (!currentOdds) return null;
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
      name: horseNames[number] || program.rawName || `At ${number}`,
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
      out: Boolean(item.KOSMAZ)
    };
  }).filter((runner) => runner.currentOdds && !runner.out);

  const analysis = analyzeRunners(runners);
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
    runners: analysis.runners,
    markets: {
      ganyan: marketMap.GANYAN || [],
      ikili: marketMap['İKİLİ'] || [],
      siraliIkili: marketMap['SIRALI İKİLİ'] || [],
      cifte: marketMap['ÇİFTE'] || []
    },
    methodology: 'Piyasa %55, AGF %28, handikap puanı %10 ve oran hareketi %7 ağırlıkla birleştirilir. Eksik veri varsa ağırlıklar otomatik dağıtılır.',
    warning: 'Tahmin olasılıktır; kesin sonuç veya kazanç garantisi değildir. Oran düşüşü para yönünü gösterir ancak yatırılan kesin TL tutarı TJK akışında bulunmaz.'
  };
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
