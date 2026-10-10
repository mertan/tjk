import { programCsvRecords } from '../server.mjs';

const num = (value) => {
  const text = String(value ?? '').trim().replace(',', '.');
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
};

export function parseTime(value) {
  const match = String(value ?? '').trim().match(/^(?:(\d+)[:.])?(\d{1,2})\.(\d{2})$/);
  if (!match) return null;
  return Number(match[1] || 0) * 60 + Number(match[2]) + Number(match[3]) / 100;
}

/** Splits an official program or results CSV into races with header-keyed runner rows. */
export function parseRaceTables(csvText) {
  const races = [];
  let race = null;
  let headers = null;
  let prizeNext = false;
  for (const cols of programCsvRecords(csvText || '')) {
    const boundary = cols[0]?.match(/^(\d+)\.\s*ko[şs]u\s*:?\s*(?:.*?\s)?(\d{1,2})[.:](\d{2})\s*$/iu);
    if (boundary) {
      const distance = cols.find((c, i) => i >= 3 && /^\d+\s*m$/i.test(c));
      race = {
        number: Number(boundary[1]),
        time: `${boundary[2].padStart(2, '0')}:${boundary[3]}`,
        type: cols[1] || '',
        condition: cols[2] || '',
        distance: distance ? parseInt(distance, 10) : null,
        surface: cols.find((c, i) => i >= 3 && /^(Çim|Kum|Sentetik)$/i.test(c)) || null,
        prize1: null,
        rows: []
      };
      races.push(race);
      headers = null;
      continue;
    }
    if (!race) continue;
    if (cols[0] === 'İkramiye') { prizeNext = true; continue; }
    if (prizeNext) {
      prizeNext = false;
      const match = String(cols[0] || '').match(/^1\.\)([\d.]+)/);
      if (match) race.prize1 = Number(match[1].replaceAll('.', ''));
      continue;
    }
    if (cols[0] === 'At No') { headers = cols; continue; }
    if (!headers) continue;
    if (!/^\d+$/.test(cols[0] || '')) { if (cols[0]) headers = null; continue; }
    race.rows.push(Object.fromEntries(headers.map((h, i) => [h, cols[i] ?? ''])));
  }
  return races;
}

/** Stable horse identity across days: sire|dam|birth year (Turkish ages roll on 1 January). */
export function horseKey(row, year) {
  const age = parseInt(row['Yaş'], 10);
  return [row['Orijin(Baba)'], row['Orijin(Anne)'], Number.isFinite(age) ? year - age : '?']
    .map((part) => String(part ?? '').trim().toLocaleUpperCase('tr'))
    .join('|');
}

export function runnerFromProgram(row, year) {
  const kilo = String(row['Kilo'] || '').match(/^([\d.,]+)(?:\s*\+\s*([\d.,]+))?/);
  const agf = String(row['AGF'] || '').match(/%([\d.]+)/);
  return {
    number: Number(row['At No']),
    name: row['At İsmi'],
    key: horseKey(row, year),
    age: parseInt(row['Yaş'], 10) || null,
    sex: String(row['Yaş'] || '').trim().split(/\s+/)[2] || null,
    weight: kilo ? num(kilo[1]) : null,
    extraWeight: kilo?.[2] ? num(kilo[2]) : 0,
    jockey: row['Jokey Adı'] || null,
    trainer: row['Antrenör Adı'] || null,
    stall: num(row['St']),
    rating: num(row['H']),
    last6: row['Son 6 Yarış'] || '',
    daysOff: num(row['KGS']),
    s20: num(row['s20']),
    bestTime: parseTime(row['EnİyiDerece']),
    agf: agf ? Number(agf[1]) : null,
    scratched: /\(ko[şs]maz\)/iu.test(row['At İsmi'] || '')
  };
}

/** Joins finishing order from the results CSV (row order = finish; first column is the finish slot). */
export function joinResults(programRace, resultRace, year) {
  const byKey = new Map(resultRace.rows.map((row, index) => [horseKey(row, year), { row, index }]));
  const runners = programRace.rows.map((row) => runnerFromProgram(row, year));
  let matched = 0;
  for (const runner of runners) {
    const hit = byKey.get(runner.key);
    if (!hit) { runner.position = null; continue; }
    matched += 1;
    const time = parseTime(hit.row['Derece']);
    runner.position = time === null ? null : hit.index + 1;
    runner.finishTime = time;
    runner.closingOdds = num(hit.row['Ganyan']);
  }
  return { runners, matched };
}
