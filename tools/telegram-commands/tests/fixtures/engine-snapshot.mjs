// Real immutable Belmont program CSV; all market data/clock here are SYNTHETIC.
import { readFile } from 'node:fs/promises';
import { buildRaceAnalysis, parseProgramCsv } from '../../../../server.mjs';
const now = Date.parse('2026-10-08T18:00:00Z');
const started = performance.now();
Date.now = () => now + performance.now() - started;
const csv = await readFile(new URL('../../../../test/fixtures/2026-10-08-belmont-park-program.csv', import.meta.url), 'utf8');
const programs = parseProgramCsv(csv);
const raceNo = Number(process.argv[2] || 6);
const program = programs.find(r => r.number === raceNo);
const key = 'BELMONT';
const race = { NO: raceNo, SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK' };
const info = { ...race, bahisler: [{ B: 'GANYAN', muhtemeller: program.runners.map(r => ({ S1: String(r.number), G: '3.20', KOSMAZ: /Koşmaz/.test(r.rawName) })) }] };
globalThis.fetch = async raw => {
  const url = String(raw);
  const response = data => new Response(JSON.stringify(data));
  if (url.endsWith('checksum.json')) return response({ success: true, day: key, datetime: '2026-10-08 21:00:00', runs: { [`${key}-${raceNo}`]: ['fixture'] } });
  if (url.includes('/day-')) return response({ success: true, data: { yarislar: [{ KEY: key, YER: 'Belmont Park ABD', HIPODROM: 'Belmont Park', YURTDISI: true, kosular: [race], atlar: { [raceNo]: Object.fromEntries(program.runners.map(r => [r.number, r.rawName])) } }] } });
  if (url.endsWith('.csv')) return new Response(csv);
  if (url.includes('/history?')) return response({ success: true, data: { labels: ['2026-10-08 20:50:00', '2026-10-08 21:00:00'], datasets: [{ data: ['4.00', '3.20'] }] } });
  return response({ success: true, data: { muhtemeller: info } });
};
console.log(JSON.stringify(await buildRaceAnalysis('2026-10-08', key, raceNo)));
