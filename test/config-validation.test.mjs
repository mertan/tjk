import test from 'node:test';
import assert from 'node:assert/strict';
import { createCache, createLimiter, createRateLimiter } from '../tjk-cache.mjs';

test('invalid cache and limiter settings fail instead of hanging or removing limits', () => {
  for (const value of [-1, 0, NaN, Infinity, 1.5]) {
    assert.throws(() => createCache({ maxEntries: value }), /INVALID_CACHE_LIMIT/);
    assert.throws(() => createLimiter({ concurrency: value }), /INVALID_UPSTREAM_LIMIT/);
    assert.throws(() => createRateLimiter({ max: value }), /INVALID_RATE_LIMIT/);
    assert.throws(() => createRateLimiter({ maxKeys: value }), /INVALID_RATE_LIMIT/);
  }
  for (const value of [-1, NaN, Infinity]) assert.throws(() => createLimiter({ minIntervalMs: value }), /INVALID_UPSTREAM_LIMIT/);
});
