import { postTimes } from '../backtest.mjs';

const NO_PICKS = { leader: null, oddsLeader: null, agfLeader: null, steam: null, value: null, surprise: null };
const MODEL_FIELDS = ['modelProbability', 'modelRank', 'modelScore', 'marketProbability', 'edge', 'supportSignal'];
const rawRunner = (runner) => Object.fromEntries(Object.entries(runner).filter(([key]) => !MODEL_FIELDS.includes(key)));

export function createV3SnapshotStore({ maxEntries = 100 } = {}) {
  const entries = new Map();
  return {
    observe(key, signature, at, cutoffMs) {
      if (at > cutoffMs) return;
      entries.delete(key);
      entries.set(key, { signature, at });
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value);
    },
    get(key) { return entries.get(key); }
  };
}

function programSignature(program) {
  return JSON.stringify({
    number: program.number, time: program.time, type: program.type,
    condition: program.condition, distance: program.distance, surface: program.surface,
    prize1: program.prize1,
    runners: program.runners.map((r) => ({
      number: r.number, rawName: r.rawName, scratched: r.scratched,
      age: r.age, sire: r.sire, dam: r.dam, weight: r.weight, jockey: r.jockey,
      trainer: r.trainer, stall: r.stall, rating: r.rating, lastSix: r.lastSix,
      daysSinceRun: r.daysSinceRun, last20: r.last20, bestTime: r.bestTime
    }))
  });
}

// A T-5 feature must use a T-5 quote and a program observed no later than T-5.
// A process started after the cutoff cannot reconstruct this evidence.
export function prepareV3Quote({ date, venueKey, programRace, venueRaces, runners,
  snapshots, trusted = false, now = Date.now() }) {
  const reasons = new Set();
  if (!programRace || !Array.isArray(programRace.runners)) {
    return { quote: null, reasonCodes: ['PROGRAM_UNAVAILABLE'] };
  }
  const schedule = Array.isArray(venueRaces) ? venueRaces : [];
  const times = postTimes(date, schedule.map((r) => r.SAAT));
  const index = schedule.findIndex((r) => Number(r.NO) === programRace.number);
  const postMs = times[index];
  if (!Number.isFinite(postMs)) return { quote: null, reasonCodes: ['V3_POST_TIME_INVALID'] };
  // Current training date convention cannot represent a next-calendar-day post.
  const sameDayPost = Date.parse(date + 'T' + programRace.time + ':00+03:00');
  if (sameDayPost !== postMs) reasons.add('V3_POST_DATE_AMBIGUOUS');
  const cutoffMs = postMs - 5 * 60_000;
  const key = date + ':' + venueKey + ':' + programRace.number;
  const signature = programSignature(programRace);
  if (trusted) snapshots?.observe(key, signature, now, cutoffMs);
  const snapshot = snapshots?.get(key);
  if (!snapshot || snapshot.at > cutoffMs) reasons.add('V3_PRE_CUTOFF_PROGRAM_MISSING');
  else if (snapshot.signature !== signature) reasons.add('V3_PROGRAM_CHANGED_AFTER_CUTOFF');
  if (now < cutoffMs) reasons.add('V3_CUTOFF_NOT_REACHED');
  if (now >= postMs) reasons.add('V3_RACE_STARTED');
  const quotes = {};
  for (const runner of runners || []) {
    const eligible = (runner.history || []).filter((p) => Number.isFinite(p.at) && p.at <= cutoffMs);
    const last = eligible.at(-1);
    if (last) quotes[runner.number] = { odds: last.odds, at: last.at };
  }
  const quoteTimes = Object.values(quotes).map((q) => q.at);
  return {
    quote: { cutoffMs, postMs, lastLabelAt: quoteTimes.length ? Math.max(...quoteTimes) : null,
      inputSnapshotAt: snapshot?.at ?? null, runners: quotes },
    reasonCodes: [...reasons]
  };
}

export function v3Envelope(base, result) {
  const source = base.analysis?.status === 'OK' ? base.runners : base.observations?.runners || [];
  const raw = source.map(rawRunner);
  const reasons = [...new Set([...(base.analysis?.reasonCodes || []), ...(result?.reasonCodes || [])])];
  const predicted = result?.runners || [];
  const valid = result?.status === 'OK' && !reasons.length && predicted.length === raw.length
    && predicted.length >= 4 && new Set(predicted.map((p) => p.number)).size === predicted.length
    && predicted.every((p) => raw.some((r) => r.number === p.number) && Number.isFinite(p.probability)
      && p.probability >= 0 && p.probability <= 1)
    && Math.abs(predicted.reduce((sum, p) => sum + p.probability, 0) - 1) < 1e-8;
  if (!valid && !reasons.length) reasons.push('V3_PREDICTION_INVALID');
  const runners = valid ? predicted.map((p) => ({
    ...raw.find((r) => r.number === p.number),
    modelProbability: p.probability * 100,
    modelRank: p.rank
  })).sort((a, b) => a.modelRank - b.modelRank) : [];
  const first = runners[0];
  const leader = first ? { number: first.number, name: first.name, odds: first.currentOdds,
    probability: first.modelProbability, agf: first.agfLatest, movement: null, edge: null } : null;
  return {
    ...base,
    analysis: {
      status: valid ? 'OK' : 'PAS', reasonCodes: reasons, modelVersion: 'tjk-v3',
      scoreKind: 'calibrated_probability', confidence: null,
      confidenceLabel: valid ? 'V3 test' : 'Veri yetersiz', agreement: false,
      summary: valid ? 'V3 test tahmini — T−5 oranları ve önceden kaydedilen program.'
        : 'PAS — V3 modeli veya gerekli kaynak kanıtı doğrulanamadı.',
      picks: { ...NO_PICKS, leader }, agfComparison: null
    },
    runners, observations: { runners: valid ? [] : raw },
    model: result?.model ?? null,
    methodology: 'V3: eğitimle paylaşılan 33 özellik, T−5 piyasa olasılığı ve skor × sıcaklık kalibrasyonu. Yalnız doğrulanmış model ve kesim öncesi programla test tahmini üretilir.',
    warning: '4200 test paneli. Tahmin ve kalibrasyon doğruluğu yerel model artefaktı ve bağımsız geçmiş test sonuçlarıyla ayrıca doğrulanmalıdır.'
  };
}
