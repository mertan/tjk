/**
 * Point-in-time V2 features. Features for races on day D use the race program
 * (published before the race) plus results from days strictly before D.
 * AGF and market odds are never features.
 */
const SURFACE = { Ç: 'Çim', K: 'Kum', S: 'Sentetik' };
const YEAR_MS = 365 * 86_400_000;

export const FEATURES = [
  'rating', 'weight', 'stall', 'age', 'daysOff', 'bestTime',
  'formAvg', 'formLast', 'formTrend', 'formSurface', 'formStarts',
  'histDistSurf', 'histWinRate', 'classChange',
  'jockeyRate', 'trainerRate'
];
const MISSING_FLAGS = ['rating', 'bestTime', 'formAvg', 'formSurface', 'histDistSurf', 'classChange', 'daysOff'];
export const FEATURE_NAMES = [...FEATURES, ...MISSING_FLAGS.map((name) => `${name}Missing`)];

/** "Son 6 Yarış" tokens: surface letter + finish digit (0 = 10th or worse / unplaced). */
export function parseLast6(text, newestLast = true) {
  const runs = [...String(text || '').matchAll(/([ÇKS])(\d)/gu)].map((m) => ({
    surface: SURFACE[m[1]],
    position: Number(m[2]) === 0 ? 10 : Number(m[2])
  }));
  return newestLast ? runs : runs.reverse();
}

const mean = (values) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);
const personKey = (name) => String(name || '').replace(/\s+AP$/u, '').trim() || null;
const isActive = (runner) => !runner.scratched;

function rate(events, date, prior = 0.1, strength = 20) {
  const since = Date.parse(date) - YEAR_MS;
  let wins = 0;
  let rides = 0;
  for (const event of events || []) {
    if (Date.parse(event.date) < since) continue;
    rides += 1;
    wins += event.win;
  }
  return (wins + prior * strength) / (rides + strength);
}

function rawRunner(race, runner, state, field) {
  const form = parseLast6(runner.last6, state.newestLast);
  const positions = form.map((run) => run.position);
  const recent = positions.slice(-3);
  const older = positions.slice(0, -3);
  const surfaceRuns = form.filter((run) => run.surface === race.surface).map((run) => run.position);
  const history = state.horses.get(runner.key) || [];
  const similar = history.filter((run) => run.surface === race.surface && Math.abs(run.distance - race.distance) <= 200);
  const last = history.at(-1);
  return {
    rating: runner.rating,
    weight: runner.weight === null ? null : runner.weight + (runner.extraWeight || 0),
    stall: runner.stall === null ? null : runner.stall / field,
    age: runner.age,
    daysOff: runner.daysOff === null ? null : Math.log1p(runner.daysOff),
    bestTime: runner.bestTime === null ? null : -runner.bestTime,
    formAvg: positions.length ? -mean(positions) : null,
    formLast: positions.length ? -positions.at(-1) : null,
    formTrend: recent.length && older.length ? mean(older) - mean(recent) : 0,
    formSurface: surfaceRuns.length ? -mean(surfaceRuns) : null,
    formStarts: positions.length,
    histDistSurf: similar.length ? -mean(similar.map((run) => run.position / run.field)) : null,
    histWinRate: (history.filter((run) => run.position === 1).length + 0.1) / (history.length + 1),
    classChange: last?.prize1 && race.prize1 ? Math.log(race.prize1 / last.prize1) : null,
    jockeyRate: rate(state.jockeys.get(personKey(runner.jockey)), race.date),
    trainerRate: rate(state.trainers.get(personKey(runner.trainer)), race.date)
  };
}

/** Within-race z-scores; a missing value contributes 0 plus an explicit missing flag (never imputed). */
function standardize(raws) {
  const rows = raws.map(() => []);
  for (const name of FEATURES) {
    const values = raws.map((raw) => raw[name]).filter((value) => Number.isFinite(value));
    const mu = mean(values) ?? 0;
    const sd = Math.sqrt(mean(values.map((value) => (value - mu) ** 2)) ?? 0);
    raws.forEach((raw, i) => rows[i].push(Number.isFinite(raw[name]) && sd > 1e-9 ? (raw[name] - mu) / sd : 0));
  }
  for (const name of MISSING_FLAGS) raws.forEach((raw, i) => rows[i].push(Number.isFinite(raw[name]) ? 0 : 1));
  return rows;
}

function update(state, race) {
  const finishers = race.runners.filter((runner) => runner.position);
  for (const runner of finishers) {
    const run = { date: race.date, surface: race.surface, distance: race.distance, prize1: race.prize1, position: runner.position, field: finishers.length };
    if (!state.horses.has(runner.key)) state.horses.set(runner.key, []);
    state.horses.get(runner.key).push(run);
    for (const [map, name] of [[state.jockeys, personKey(runner.jockey)], [state.trainers, personKey(runner.trainer)]]) {
      if (!name) continue;
      if (!map.has(name)) map.set(name, []);
      map.get(name).push({ date: race.date, win: runner.position === 1 ? 1 : 0 });
    }
  }
}

/**
 * Returns one example per race: active runners, standardized matrix X and the
 * finishing positions (labels; never read while building X).
 */
export function buildExamples(races, { newestLast = true, minField = 4 } = {}) {
  const state = { horses: new Map(), jockeys: new Map(), trainers: new Map(), newestLast };
  const days = new Map();
  for (const race of races) {
    if (!days.has(race.date)) days.set(race.date, []);
    days.get(race.date).push(race);
  }
  const examples = [];
  for (const date of [...days.keys()].sort()) {
    const today = days.get(date);
    for (const race of today) {
      const active = race.runners.filter(isActive);
      if (active.length < minField || !race.distance || !race.surface) continue;
      const raws = active.map((runner) => rawRunner(race, runner, state, active.length));
      examples.push({
        id: `${race.date}|${race.venue}|${race.number}`,
        date: race.date,
        venue: race.venue,
        race: race.number,
        numbers: active.map((runner) => runner.number),
        X: standardize(raws),
        positions: active.map((runner) => runner.position ?? null),
        agf: active.map((runner) => runner.agf),
        closingOdds: active.map((runner) => runner.closingOdds ?? null)
      });
    }
    for (const race of today) update(state, race);
  }
  return examples;
}

/**
 * Learns the token order of "Son 6 Yarış" from data: for runners whose previous
 * race is in the dataset, which end of the string matches that finish?
 */
export function inferFormOrder(races) {
  const lastRun = new Map();
  let newestLast = 0;
  let newestFirst = 0;
  for (const race of [...races].sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time))) {
    for (const runner of race.runners) {
      const previous = lastRun.get(runner.key);
      const tokens = parseLast6(runner.last6, true);
      if (previous && tokens.length && previous.date < race.date) {
        const expected = Math.min(previous.position, 10);
        if (tokens.at(-1).position === expected) newestLast += 1;
        if (tokens[0].position === expected) newestFirst += 1;
      }
    }
    for (const runner of race.runners) if (runner.position) lastRun.set(runner.key, { date: race.date, position: runner.position });
  }
  return { newestLast: newestLast >= newestFirst, matches: { newestLast, newestFirst } };
}
