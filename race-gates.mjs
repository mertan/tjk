export function isWithdrawn(value) {
  return value === true || value === 1 || value === '1' || value === 'true';
}

export const FRESHNESS_LIMITS = Object.freeze({
  heartbeatMaxAgeMs: 180_000,
  quoteMaxAgeMs: 720_000,
  maxClockSkewMs: 120_000
});

const LOCAL_TIME = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/;
const ZONED_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
const ISTANBUL_OFFSET_MS = 3 * 3_600_000;

/**
 * TJK history labels and checksum.datetime are Europe/Istanbul wall-clock
 * strings without a zone. Türkiye has used fixed UTC+3 (no DST) since 2016.
 * Explicitly zoned ISO strings are also accepted. Anything else is null.
 */
export function parseSourceTime(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  const local = text.match(LOCAL_TIME);
  if (local) {
    const [, y, mo, d, h, mi, s] = local;
    const wall = `${y}-${mo}-${d}T${h}:${mi}:${s}`;
    const ms = Date.parse(`${wall}+03:00`);
    if (!Number.isFinite(ms) || new Date(ms + ISTANBUL_OFFSET_MS).toISOString().slice(0, 19) !== wall) return null;
    return ms;
  }
  if (!ZONED_TIME.test(text)) return null;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : null;
}

const iso = (ms) => (Number.isFinite(ms) ? new Date(ms).toISOString() : null);
const ageSec = (now, ms) => (Number.isFinite(ms) ? Math.round((now - ms) / 1000) : null);

/**
 * Race-level quote freshness from real source timestamps.
 * - heartbeat: TJK checksum.datetime, proves the feed itself is being updated.
 * - quoteTimes: latest timestamped odds point of every active runner. The
 *   oldest of these must be recent; a runner without one fails the race.
 * The race schedule timestamp is never used: it is the post time, not a quote time.
 */
export function quoteFreshness({ heartbeat, quoteTimes, now = Date.now(), limits = FRESHNESS_LIMITS, requireHeartbeat = true } = {}) {
  const reasons = [];
  let heartbeatAt = null;
  if (requireHeartbeat) {
    heartbeatAt = parseSourceTime(heartbeat);
    if (heartbeatAt === null) reasons.push('SOURCE_HEARTBEAT_MISSING');
    else if (heartbeatAt - now > limits.maxClockSkewMs) reasons.push('SOURCE_CLOCK_SKEW');
    else if (now - heartbeatAt > limits.heartbeatMaxAgeMs) reasons.push('SOURCE_HEARTBEAT_STALE');
  }
  let oldest = null;
  let newest = null;
  const times = Array.isArray(quoteTimes) ? quoteTimes : [];
  if (!times.length || times.some((time) => !Number.isFinite(time))) {
    reasons.push('QUOTE_TIMESTAMP_MISSING');
  } else {
    oldest = Math.min(...times);
    newest = Math.max(...times);
    if (newest - now > limits.maxClockSkewMs) reasons.push('SOURCE_CLOCK_SKEW');
    if (now - oldest > limits.quoteMaxAgeMs) reasons.push('QUOTE_STALE');
  }
  if (reasons.length) reasons.unshift('SOURCE_FRESHNESS_UNVERIFIED');
  return {
    status: reasons.length ? 'UNVERIFIED' : 'VERIFIED',
    reasons: [...new Set(reasons)],
    checkedAt: iso(now),
    heartbeatAt: iso(heartbeatAt),
    heartbeatAgeSec: ageSec(now, heartbeatAt),
    oldestQuoteAt: iso(oldest),
    oldestQuoteAgeSec: ageSec(now, oldest),
    newestQuoteAt: iso(newest),
    limitsSec: {
      heartbeatMaxAge: limits.heartbeatMaxAgeMs / 1000,
      quoteMaxAge: limits.quoteMaxAgeMs / 1000,
      maxClockSkew: limits.maxClockSkewMs / 1000
    }
  };
}

/**
 * Foreign TJK programs write 0 for horses without a local handicap; domestic
 * programs leave the cell empty instead (observed on Oct 2026 programs). So a
 * foreign 0 is "unrated", a domestic 0 is a real zero, an empty cell is missing.
 */
export function classifyRating(value, { foreign = false } = {}) {
  if (value === null || value === undefined) return { rating: null, ratingStatus: 'missing' };
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return { rating: null, ratingStatus: 'invalid' };
  if (value === 0) return foreign ? { rating: null, ratingStatus: 'unrated' } : { rating: 0, ratingStatus: 'zero' };
  return { rating: value, ratingStatus: 'rated' };
}

export function raceGate(feed, programRace, { now = Date.now(), quoteTimes, limits = FRESHNESS_LIMITS, requireOpen = true, requireHeartbeat = true } = {}) {
  const freshness = quoteFreshness({ heartbeat: feed.checksum?.datetime, quoteTimes, now, limits, requireHeartbeat });
  const reasons = [...freshness.reasons];
  const info = feed.racePayload.data.muhtemeller;
  const rows = info.bahisler?.find((bet) => bet.B === 'GANYAN')?.muhtemeller;
  if (!Array.isArray(rows) || !rows.length) reasons.push('MARKET_UNAVAILABLE');
  if (!programRace) reasons.push('PROGRAM_UNAVAILABLE');
  else {
    if (!programRace.type || !programRace.condition || !programRace.distance || !programRace.surface) reasons.push('PROGRAM_METADATA_MISSING');
    const activeProgram = programRace.runners.filter((r) => !/\(ko[şs]maz\)/iu.test(r.rawName));
    const feedNumbers = new Set((rows || []).map((r) => Number(r.S1)));
    const programNumbers = new Set(programRace.runners.map((r) => r.number));
    if (programNumbers.size !== programRace.runners.length) reasons.push('PROGRAM_RUNNER_DUPLICATE');
    if (activeProgram.some((r) => !feedNumbers.has(r.number)) ||
        (rows || []).some((r) => !isWithdrawn(r.KOSMAZ) && !programNumbers.has(Number(r.S1)))) reasons.push('RUNNER_SET_MISMATCH');
    if ((rows || []).some((r) => !isWithdrawn(r.KOSMAZ) && programRace.runners.some((p) => p.number === Number(r.S1) && /\(ko[şs]maz\)/iu.test(p.rawName)))) reasons.push('SCRATCH_STATUS_CONFLICT');
    if (feed.race.SAAT) {
      if (typeof feed.race.SAAT !== 'string') reasons.push('INVALID_RACE_TIME');
      else if (programRace.time !== feed.race.SAAT.replace('.', ':')) reasons.push('RACE_TIME_MISMATCH');
    }
    if (feed.race.PIST && programRace.surface !== feed.race.PIST) reasons.push('RACE_SURFACE_MISMATCH');
  }
  if (requireOpen && (feed.race.DURUM !== 'AÇIK' || info.DURUM !== 'AÇIK')) reasons.push('RACE_NOT_OPEN');
  return { reasons: [...new Set(reasons)], freshness };
}

export function raceDataIssues(feed, programRace, options) {
  return raceGate(feed, programRace, options).reasons;
}
