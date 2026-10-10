/**
 * Bounded TTL cache with in-flight request coalescing, an outbound request
 * limiter and an inbound fixed-window rate limiter. No external dependencies.
 */
export function createCache({ maxEntries = 500, now = () => Date.now() } = {}) {
  if (!Number.isSafeInteger(maxEntries) || maxEntries < 1) throw new RangeError('INVALID_CACHE_LIMIT');
  const entries = new Map();
  const inflight = new Map();
  const stats = { hits: 0, misses: 0, coalesced: 0, evictions: 0 };

  function remember(key, value, ttlMs) {
    entries.delete(key);
    entries.set(key, { value, expiresAt: now() + ttlMs });
    while (entries.size > maxEntries) {
      entries.delete(entries.keys().next().value);
      stats.evictions += 1;
    }
  }

  async function get(key, ttlMs, producer) {
    const current = entries.get(key);
    if (current && current.expiresAt > now()) {
      stats.hits += 1;
      entries.delete(key);
      entries.set(key, current);
      return current.value;
    }
    if (current) entries.delete(key);
    const pending = inflight.get(key);
    if (pending) {
      stats.coalesced += 1;
      return pending;
    }
    stats.misses += 1;
    // Failures are never cached; every waiter of this attempt sees the same error.
    const promise = Promise.resolve().then(producer).then((value) => {
      remember(key, value, ttlMs);
      return value;
    }).finally(() => inflight.delete(key));
    inflight.set(key, promise);
    return promise;
  }

  return {
    get,
    clear: () => { entries.clear(); inflight.clear(); },
    get size() { return entries.size; },
    stats: () => ({ ...stats, size: entries.size, inflight: inflight.size })
  };
}

export function createLimiter({ concurrency = 4, minIntervalMs = 100, now = () => Date.now() } = {}) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || !Number.isFinite(minIntervalMs) || minIntervalMs < 0) throw new RangeError('INVALID_UPSTREAM_LIMIT');
  let active = 0;
  let nextStart = 0;
  const queue = [];

  function pump() {
    if (active >= concurrency || !queue.length) return;
    const wait = Math.max(0, nextStart - now());
    if (wait > 0) {
      setTimeout(pump, wait);
      return;
    }
    const { task, resolve, reject } = queue.shift();
    active += 1;
    nextStart = now() + minIntervalMs;
    Promise.resolve().then(task).then(resolve, reject).finally(() => {
      active -= 1;
      pump();
    });
    pump();
  }

  return {
    schedule(task) {
      return new Promise((resolve, reject) => {
        queue.push({ task, resolve, reject });
        pump();
      });
    },
    get pending() { return queue.length; },
    get active() { return active; }
  };
}

export function createRateLimiter({ windowMs = 60_000, max = 120, maxKeys = 10_000, now = () => Date.now() } = {}) {
  if (!Number.isSafeInteger(max) || max < 1 || !Number.isSafeInteger(maxKeys) || maxKeys < 1 || !Number.isFinite(windowMs) || windowMs <= 0) throw new RangeError('INVALID_RATE_LIMIT');
  const windows = new Map();
  return function check(key) {
    const time = now();
    let entry = windows.get(key);
    if (!entry || entry.resetAt <= time) {
      windows.delete(key);
      entry = { count: 0, resetAt: time + windowMs };
      windows.set(key, entry);
      while (windows.size > maxKeys) windows.delete(windows.keys().next().value);
    }
    entry.count += 1;
    if (entry.count > max) {
      return { allowed: false, retryAfterSec: Math.max(1, Math.ceil((entry.resetAt - time) / 1000)) };
    }
    return { allowed: true, remaining: max - entry.count };
  };
}
