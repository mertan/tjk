import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { buildProgramCsvUrl, parseProgramCsv } from '../server.mjs';

const fixtureUrl = new URL('./fixtures/2026-10-08-belmont-park-program.csv', import.meta.url);
const fixture = await readFile(fixtureUrl);
const source = JSON.parse(await readFile(new URL('./fixtures/2026-10-08-belmont-park-program.source.json', import.meta.url), 'utf8'));

test('official Belmont Park fixture preserves all nine separate races', () => {
  assert.equal(createHash('sha256').update(fixture).digest('hex'), source.sha256);
  const races = parseProgramCsv(fixture.toString('utf8'));
  assert.deepEqual(races.map((race) => race.number), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.deepEqual(races.map((race) => race.runners.length), [6, 9, 9, 8, 9, 8, 10, 8, 11]);
  for (const race of races) {
    assert.equal(new Set(race.runners.map((runner) => runner.number)).size, race.runners.length);
  }
});

test('Belmont 5: named colon header, nine runners, no race 6 contamination', () => {
  const race = parseProgramCsv(fixture.toString('utf8')).find((item) => item.number === 5);
  assert.equal(race.name, 'MAIDEN SATIŞ KOŞUSU');
  assert.equal(race.time, '22:23');
  assert.equal(race.distance, '1600m');
  assert.equal(race.surface, 'Kum');
  assert.equal(race.runners.length, 9);
  assert.equal(race.runners[0].rawName, 'SALVATION (USA)');
  assert.equal(race.runners.at(-1).rawName, 'FIGHTFORALLEGIANCE (USA)');
  assert.equal(race.runners.some((runner) => runner.rawName.includes('WILLPOWERED')), false);
  assert.equal(race.runners[7].rawName, 'STREAM IT (USA) (Koşmaz)');
  assert.equal(race.runners[0].rating, 81);
  assert.equal(race.runners[0].lastSix, '24224-3');
  // The official header does not label its extra trailing cell. Do not invent KGS.
  assert.equal(race.runners[0].daysSinceRun, null);
});

test('Belmont 6: unnamed colon-free header starts its own eight-runner race', () => {
  const race = parseProgramCsv(fixture.toString('utf8')).find((item) => item.number === 6);
  assert.ok(race, 'colon-free race 6 must not disappear');
  assert.equal(race.name, null);
  assert.equal(race.time, '22:57');
  assert.equal(race.distance, '1800m');
  assert.equal(race.surface, 'Çim');
  assert.equal(race.runners.length, 8);
  assert.deepEqual(race.runners.map((runner) => runner.number), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.equal(race.runners[0].rawName, 'WILLPOWERED (USA)');
  assert.equal(race.runners[1].rawName, 'COACH RYAN (USA)');
  assert.equal(race.runners.at(-1).rawName, 'HONOREE (USA)');
  assert.equal(race.runners[0].jockey, 'FLAVIEN PRAT');
});

test('program filename joins venue words and stays on the official report host', () => {
  assert.equal(buildProgramCsvUrl('2026-10-08', source.venue), source.sourceUrl);
  assert.equal(buildProgramCsvUrl('2026-10-08', { YER: ' Belmont\u00a0Park  ABD ' }), source.sourceUrl);
  const ankara = new URL(buildProgramCsvUrl('2026-10-08', { YER: 'Ankara' }));
  assert.equal(ankara.hostname, 'medya-cdn.tjk.org');
  assert.equal(ankara.pathname, '/raporftp/TJKPDF/2026/2026-10-08/CSV/GunlukYarisProgrami/08.10.2026-Ankara-GunlukYarisProgrami-TR.csv');
  // Preserve Unicode rather than inventing an unverified ASCII venue spelling.
  const izmir = buildProgramCsvUrl('2026-10-08', { YER: 'İzmir' });
  assert.ok(decodeURIComponent(izmir).endsWith('/08.10.2026-İzmir-GunlukYarisProgrami-TR.csv'));
});

test('unsafe or missing filename input cannot create a report URL', () => {
  for (const YER of ['', '   ', '../Belmont', 'Belmont/ABD', 'Belmont\\ABD', 'https://elsewhere.example', 'Belmont?x=1', 'Belmont#ABD', 'Belmont%2fABD', 'Belmont\nABD', 'x'.repeat(121)]) {
    assert.equal(buildProgramCsvUrl('2026-10-08', { YER }), null, YER);
  }
  assert.equal(buildProgramCsvUrl('2026-10-08', undefined), null);
  assert.throws(() => buildProgramCsvUrl('2026-02-30', source.venue), /Geçersiz tarih/);
});

test('Turkish/ASCII race titles accept optional names, colons and either clock delimiter', () => {
  const cases = [
    ['5. Kosu : MAIDEN SATIŞ KOŞUSU 22.23', 'MAIDEN SATIŞ KOŞUSU', '22:23'],
    ['5. Koşu : MAIDEN SATIŞ KOŞUSU 22:23', 'MAIDEN SATIŞ KOŞUSU', '22:23'],
    ['5. KOŞU 22:23', null, '22:23'],
    ['5. Kosu : 22.23', null, '22:23'],
    ['5. Koşu:9:05', null, '09:05'],
    ['5. Kosu 00.05', null, '00:05']
  ];
  for (const [header, name, time] of cases) {
    const [race] = parseProgramCsv(`\uFEFF${header};Maiden;İngilizler;kg;1600m;Kum\r\nAt No;At İsmi\r\n1;TEST ATI`);
    assert.ok(race, header);
    assert.equal(race.number, 5);
    assert.equal(race.name, name);
    assert.equal(race.time, time);
    assert.equal(race.runners.length, 1);
  }
});

test('malformed race boundaries cannot append runners to a previous race', () => {
  for (const header of ['6. Kosu BELİRSİZ', '6. Koşu 24.01', '6. Kosu 22:99', '6 Koşu 22.57', '31. Kosu 22.57']) {
    const races = parseProgramCsv(`5. Kosu : ÖRNEK 22.23;Maiden\nAt No;At İsmi\n1;BEŞ\n${header};Şartlı\nAt No;At İsmi\n1;SIZMAMALI\n7. Kosu 23.31;Maiden\nAt No;At İsmi\n1;YEDİ`);
    assert.deepEqual(races.map((race) => race.number), [5, 7], header);
    assert.deepEqual(races[0].runners.map((runner) => runner.rawName), ['BEŞ'], header);
    assert.deepEqual(races[1].runners.map((runner) => runner.rawName), ['YEDİ'], header);
  }
});

test('every copy of a duplicated race number is omitted, even if one header is malformed', () => {
  for (const duplicate of ['5. Kosu 22.23', '5. Kosu BELİRSİZ', '5 Koşu 22.23']) {
    const races = parseProgramCsv(`5. Kosu 22.23\nAt No;At İsmi\n1;İLK\n${duplicate}\nAt No;At İsmi\n2;TEKRAR\n6. Kosu 22.57\nAt No;At İsmi\n1;ALTI`);
    assert.deepEqual(races.map((race) => race.number), [6], duplicate);
    assert.deepEqual(races[0].runners.map((runner) => runner.rawName), ['ALTI']);
  }
});

test('duplicate nonempty column labels omit the affected race instead of overwriting fields', () => {
  for (const header of ['At No;At İsmi;H;H', 'At No;At İsmi;At İsmi;H', 'At No;At No;At İsmi;H']) {
    const races = parseProgramCsv(`5. Kosu 22.23\n${header}\n1;BEŞ;70;90\n6. Kosu 22.57\nAt No;At İsmi;H\n1;ALTI;80`);
    assert.deepEqual(races.map((race) => race.number), [6], header);
  }
});

test('repeated or redefined runner headers invalidate the affected race', () => {
  for (const header of ['At No;At İsmi;H', 'At No;H;At İsmi', 'At İsmi;At No;H']) {
    const races = parseProgramCsv(`5. Kosu 22.23\nAt No;At İsmi;H\n1;İLK;70\n${header}\n2;İKİ;80`);
    assert.deepEqual(races, [], header);
  }
});

test('short rows missing labelled cells omit a race; explicit empty cells and unlabelled extras stay missing', () => {
  assert.deepEqual(parseProgramCsv('5. Kosu 22.23\nAt No;At İsmi;H\n1;EKSİK'), []);
  const [race] = parseProgramCsv('5. Kosu 22.23\nAt No;At İsmi;H;;\n1;BOŞ;\n2;EK;81;46;unused');
  assert.equal(race.runners.length, 2);
  assert.equal(race.runners[0].rating, null);
  assert.equal(race.runners[1].rating, 81);
  assert.equal(race.runners[1].daysSinceRun, null);
});

test('invalid or duplicate runner IDs omit the affected race without damaging the next race', () => {
  for (const id of ['1', '01', '0', '31', '-1', '1.5', '', 'UNKNOWN', '9007199254740993']) {
    const races = parseProgramCsv(`5. Kosu 22.23\nAt No;At İsmi;H\n1;İLK;70\n${id};GEÇERSİZ;80\n6. Kosu 22.57\nAt No;At İsmi;H\n1;ALTI;81`);
    assert.deepEqual(races.map((race) => race.number), [6], id);
  }
});

test('a runner table cannot reopen after its official betting footer', () => {
  assert.deepEqual(parseProgramCsv('5. Kosu 22.23\nAt No;At İsmi\n1;İLK\nGANYAN;İKİLİ\nAt No;At İsmi\n2;SONRA'), []);
  assert.deepEqual(parseProgramCsv('5. Kosu 22.23\nAt No;At İsmi\n1;İLK\nGANYAN;İKİLİ\n2;SONRA'), []);
});

test('semicolon CSV quotes preserve cell boundaries and escaped quotes', () => {
  const [race] = parseProgramCsv('5. Kosu 22.23;Maiden\n"At No";"At İsmi";"Jokey Adı";"H"\n1;"TEST; \"\"ALFA\"\"\nATI";"A. JOKEY";81');
  assert.equal(race.runners[0].rawName, 'TEST; "ALFA"\nATI');
  assert.equal(race.runners[0].jockey, 'A. JOKEY');
  assert.equal(race.runners[0].rating, 81);
  assert.equal(race.runners[0].daysSinceRun, null);
});

test('empty or incomplete quoted source returns no fabricated program', () => {
  assert.deepEqual(parseProgramCsv(null), []);
  assert.deepEqual(parseProgramCsv(''), []);
  assert.deepEqual(parseProgramCsv('5. Kosu 22.23\nAt No;At İsmi\n1;"incomplete'), []);
});
