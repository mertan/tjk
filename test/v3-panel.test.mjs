import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8');

function element() {
  return {
    value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
    style: {}, classList: { toggle() {} }, addEventListener() {},
    querySelector: () => element(), querySelectorAll: () => []
  };
}

async function panel({ enabled = true, configFailure = false, raceResponse } = {}) {
  const elements = new Map();
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const document = { hidden: false, title: '', getElementById: byId, querySelector: byId, querySelectorAll: () => [] };
  const calls = [];
  const fetch = async (path) => {
    calls.push(path);
    if (path === '/api/config') {
      if (configFailure) throw new Error('config unavailable');
      return { ok: true, json: async () => ({ v3Enabled: enabled }) };
    }
    if (path.startsWith('/api/day')) return { ok: true, json: async () => ({
      date: '2026-10-10', venues: [{ key: 'ANKARA', name: 'Ankara', place: 'Ankara',
        races: [{ number: 1, time: '14:00', status: 'AÇIK' }] }]
    }) };
    return { ok: true, json: async () => raceResponse || ({
      venue: { name: 'Ankara' }, race: { number: 1, time: '14:00', status: 'AÇIK' }, updatedAt: '2026-10-10T10:59:00Z',
      analysis: { status: 'PAS', reasonCodes: ['V3_MODEL_UNAVAILABLE'], modelVersion: enabled ? 'tjk-v3' : 'market-no-agf-v2',
        scoreKind: 'calibrated_probability', picks: {}, summary: 'PAS' },
      runners: [], observations: { runners: [] }, markets: {}, methodology: '', warning: ''
    }) };
  };
  const create = new Function('document', 'navigator', 'fetch', 'setInterval', 'clearInterval',
    source + '\nreturn { ready, state, movementClass, movementArrow, renderChart, renderDashboard, renderSelectedRunner };');
  const api = create(document, {}, fetch, () => 1, () => {});
  await api.ready;
  return { ...api, elements, document, calls };
}

test('V3 config selects only the V3 endpoint and labels the test panel', async () => {
  const view = await panel();
  assert.ok(view.calls.some((path) => path.startsWith('/api/v3/race?')));
  assert.ok(!view.calls.some((path) => path.startsWith('/api/race?')));
  assert.equal(view.document.title, 'TJK V3 · Test paneli');
  assert.equal(view.elements.get('confidenceValue').textContent, '—');
  assert.equal(view.elements.get('leaderName').textContent, 'PAS — veri yetersiz');
});

test('configuration failure blocks prediction instead of reverting to legacy', async () => {
  const view = await panel({ configFailure: true });
  assert.deepEqual(view.calls, ['/api/config']);
  assert.equal(view.state.config, null);
  assert.equal(view.elements.get('refreshButton').disabled, true);
  assert.match(view.elements.get('errorBanner').textContent, /PAS/);
});

test('legacy configuration keeps the existing endpoint', async () => {
  const view = await panel({ enabled: false });
  assert.ok(view.calls.some((path) => path.startsWith('/api/race?')));
  assert.ok(!view.calls.some((path) => path.startsWith('/api/v3/race?')));
});

test('V3 response cannot silently contain legacy heuristic scores', async () => {
  const view = await panel({ raceResponse: { analysis: { status: 'OK', modelVersion: 'market-no-agf-v2', scoreKind: 'heuristic_score' } } });
  assert.equal(view.state.race, null);
  assert.match(view.elements.get('errorBanner').textContent, /V3 model yanıtı/);
});

test('unknown movement and a single recorded quote do not fabricate direction or chart history', async () => {
  const view = await panel();
  assert.equal(view.movementArrow(null), '');
  assert.equal(view.movementClass(undefined), '');
  view.renderChart({ currentOdds: 2, history: [{ time: '13:00', odds: 241.7, at: 1 }] });
  assert.match(view.elements.get('historyChart').innerHTML, /yeterli oran geçmişi/);
  view.renderChart({ currentOdds: 2, historyStatus: 'UNVERIFIED', history: [{ odds: 241.7, at: 1 }, { odds: 2, at: 2 }] });
  assert.match(view.elements.get('historyChart').innerHTML, /yeterli oran geçmişi/);
});

test('V3 displays calibrated probability rather than heuristic confidence', async () => {
  const view = await panel();
  view.state.race.analysis.status = 'OK';
  view.state.race.analysis.confidence = 99;
  view.state.race.analysis.picks.leader = { number: 1, name: 'TEST', probability: 25 };
  view.renderDashboard();
  assert.match(view.elements.get('confidenceValue').textContent, /25.*%/);
  assert.equal(view.elements.get('confidenceBar').style.width, '25%');
  assert.equal(view.elements.get('.confidence-row span').textContent, 'Kalibre edilmiş kazanma olasılığı');
});
