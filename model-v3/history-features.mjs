/**
 * V3 extras derived from earlier official results (never same-day or later):
 * relative speed figures, distance change and class (1st-prize) change.
 * Rows are aligned with V2 examples (active runners in program order).
 */
const mean = (values) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);
const DAY_MS = 86_400_000;

export const EXTRA_FEATURES = ['speedRel', 'speedSurf', 'distChange', 'classVsAvg', 'runs90'];
const EXTRA_FLAGS = ['speedRel', 'speedSurf', 'distChange', 'classVsAvg'];
export const EXTRA_NAMES = [...EXTRA_FEATURES, ...EXTRA_FLAGS.map((name) => `${name}Missing`)];

function raw(race, runner, horses) {
  const history = horses.get(runner.key) || [];
  const recent = history.slice(-3);
  const speeds = (runs) => runs.map((run) => run.speedRel).filter(Number.isFinite);
  const surface = history.filter((run) => run.surface === race.surface).slice(-3);
  const last = history.at(-1);
  const since = Date.parse(race.date) - 90 * DAY_MS;
  const prizes = recent.map((run) => run.prize1).filter((p) => p > 0);
  return {
    speedRel: mean(speeds(recent)),
    speedSurf: mean(speeds(surface)),
    distChange: last?.distance && race.distance ? Math.abs(Math.log(race.distance / last.distance)) : null,
    classVsAvg: prizes.length && race.prize1 ? Math.log(race.prize1 / mean(prizes)) : null,
    runs90: history.filter((run) => Date.parse(run.date) >= since).length
  };
}

function standardize(raws) {
  const rows = raws.map(() => []);
  for (const name of EXTRA_FEATURES) {
    const values = raws.map((r) => r[name]).filter(Number.isFinite);
    const mu = mean(values) ?? 0;
    const sd = Math.sqrt(mean(values.map((v) => (v - mu) ** 2)) ?? 0);
    raws.forEach((r, i) => rows[i].push(Number.isFinite(r[name]) && sd > 1e-9 ? (r[name] - mu) / sd : 0));
  }
  for (const name of EXTRA_FLAGS) raws.forEach((r, i) => rows[i].push(Number.isFinite(r[name]) ? 0 : 1));
  return rows;
}

function update(horses, race) {
  const finishers = race.runners.filter((r) => r.position && r.finishTime > 0);
  const winner = finishers.find((r) => r.position === 1);
  const winnerSpeed = winner && race.distance ? race.distance / winner.finishTime : null;
  for (const runner of race.runners.filter((r) => r.position)) {
    const speed = runner.finishTime > 0 && race.distance ? race.distance / runner.finishTime : null;
    if (!horses.has(runner.key)) horses.set(runner.key, []);
    horses.get(runner.key).push({
      date: race.date,
      surface: race.surface,
      distance: race.distance,
      prize1: race.prize1,
      speedRel: speed && winnerSpeed ? speed / winnerSpeed : null
    });
  }
}

/** Map of race id -> standardized extra rows (same runner order as V2 buildExamples). */
export function buildExtras(races) {
  const horses = new Map();
  const days = new Map();
  for (const race of races) {
    if (!days.has(race.date)) days.set(race.date, []);
    days.get(race.date).push(race);
  }
  const extras = new Map();
  for (const date of [...days.keys()].sort()) {
    for (const race of days.get(date)) {
      const active = race.runners.filter((r) => !r.scratched);
      extras.set(`${race.date}|${race.venue}|${race.number}`, standardize(active.map((r) => raw(race, r, horses))));
    }
    for (const race of days.get(date)) update(horses, race);
  }
  return extras;
}
