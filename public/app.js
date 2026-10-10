const state = {
  config: null,
  day: null,
  race: null,
  selectedRunner: null,
  selectedMarket: 'ikili',
  loading: false,
  timer: null
};

const domesticKeys = new Set(['ADANA', 'ANKARA', 'ANTALYA', 'BURSA', 'DBAKIR', 'ELAZIG', 'ISTANBUL', 'IZMIR', 'KOCAELI', 'SANLIURFA']);

const el = Object.fromEntries([
  'dateInput', 'venueSelect', 'raceSelect', 'refreshButton', 'autoRefresh', 'liveState', 'errorBanner',
  'loadingView', 'dashboard', 'venueLabel', 'raceTitle', 'raceMeta', 'raceStatus', 'updatedTime',
  'confidenceChip', 'leaderNumber', 'leaderName', 'leaderSummary', 'confidenceValue', 'confidenceBar',
  'steamPick', 'steamMove', 'valuePick', 'valueEdge', 'surprisePick', 'surpriseDetail', 'runnerList',
  'chartTitle', 'chartStat', 'historyChart', 'detailTitle', 'detailList', 'marketGrid', 'methodology', 'warningText'
].map((id) => [id, document.getElementById(id)]));

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('tr-TR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pct(value, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const number = Number(value);
  return `${signed && number > 0 ? '+' : ''}${fmt(number, 1)}%`;
}

function movementClass(value) {
  if (!Number.isFinite(value)) return '';
  if (value <= -3) return 'support';
  if (value >= 3) return 'drift';
  return '';
}

function movementArrow(value) {
  if (!Number.isFinite(value)) return '';
  if (value <= -3) return '↓';
  if (value >= 3) return '↑';
  return '→';
}

async function api(path) {
  const response = await fetch(path, { headers: { accept: 'application/json' }, cache: 'no-store' });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'Veri alınamadı.');
  return body;
}

function setLoading(active) {
  state.loading = active;
  el.refreshButton.disabled = active || !state.config;
  el.refreshButton.classList.toggle('loading', active);
  if (!state.race) {
    el.loadingView.hidden = !active;
    el.dashboard.hidden = true;
  }
}

function showError(message) {
  el.errorBanner.textContent = message || '';
  el.errorBanner.hidden = !message;
}

function setConnected(connected, label = 'Canlı veri') {
  el.liveState.classList.toggle('connected', connected);
  el.liveState.querySelector('span:last-child').textContent = connected ? label : 'Bağlantı yok';
}

function venueByKey() {
  return state.day?.venues.find((venue) => venue.key === el.venueSelect.value);
}

async function loadDay({ preserveVenue = true } = {}) {
  if (!state.config) return;
  showError('');
  setLoading(true);
  try {
    const dateQuery = el.dateInput.value ? `?date=${encodeURIComponent(el.dateInput.value)}` : '';
    const day = await api(`/api/day${dateQuery}`);
    state.day = day;
    el.dateInput.value = day.date;
    el.dateInput.max = day.date > new Date().toISOString().slice(0, 10) ? day.date : new Date().toISOString().slice(0, 10);

    const previousVenue = preserveVenue ? el.venueSelect.value : '';
    el.venueSelect.innerHTML = day.venues.map((venue) =>
      `<option value="${escapeHtml(venue.key)}">${escapeHtml(venue.place || venue.name)}</option>`
    ).join('');

    const preferred = day.venues.find((venue) => venue.key === previousVenue)
      || day.venues.find((venue) => domesticKeys.has(venue.key) && venue.races.some((race) => race.status === 'AÇIK'))
      || day.venues.find((venue) => domesticKeys.has(venue.key))
      || day.venues[0];
    if (!preferred) throw new Error('Bu tarihte yarış bulunamadı.');
    el.venueSelect.value = preferred.key;
    populateRaces(preferred);
    await loadRace({ force: true });
  } catch (error) {
    state.race = null;
    state.selectedRunner = null;
    el.dashboard.hidden = true;
    showError(`PAS — ${error.message}`);
    setConnected(false);
  } finally {
    setLoading(false);
  }
}

function populateRaces(venue, preserveRace = false) {
  const previous = preserveRace ? Number(el.raceSelect.value) : null;
  el.raceSelect.innerHTML = venue.races.map((race) =>
    `<option value="${race.number}">${race.number}. Koşu · ${escapeHtml(race.time)} · ${escapeHtml(race.status)}</option>`
  ).join('');
  const selected = venue.races.find((race) => race.number === previous)
    || venue.races.find((race) => race.status === 'AÇIK')
    || venue.races.find((race) => race.number === venue.selectedRace)
    || venue.races.at(-1);
  if (selected) el.raceSelect.value = String(selected.number);
}

async function loadRace({ silent = false, force = false } = {}) {
  if (!state.config || (!force && state.loading) || !el.dateInput.value || !el.venueSelect.value || !el.raceSelect.value) return;
  if (!silent) showError('');
  setLoading(true);
  try {
    const query = new URLSearchParams({
      date: el.dateInput.value,
      venue: el.venueSelect.value,
      race: el.raceSelect.value
    });
    const endpoint = state.config.v3Enabled ? '/api/v3/race' : '/api/race';
    const data = await api(`${endpoint}?${query}`);
    if (state.config.v3Enabled && (data.analysis?.modelVersion !== 'tjk-v3' || (data.analysis.status === 'OK' && data.analysis.scoreKind !== 'calibrated_probability'))) {
      throw new Error('V3 model yanıtı doğrulanamadı.');
    }
    const selectedNumber = state.selectedRunner?.number;
    state.race = { ...data, runners: data.analysis.status === 'PAS' ? (data.observations?.runners || []) : data.runners };
    data.runners = state.race.runners;
    state.selectedRunner = data.runners.find((runner) => runner.number === selectedNumber) || data.runners[0] || null;
    renderDashboard();
    el.loadingView.hidden = true;
    el.dashboard.hidden = false;
    setConnected(true, data.race.status === 'AÇIK' ? 'Canlı' : data.race.status);
  } catch (error) {
    state.race = null;
    state.selectedRunner = null;
    el.dashboard.hidden = true;
    showError(`PAS — ${error.message}`);
    setConnected(false);
  } finally {
    setLoading(false);
  }
}

function isV3() {
  return state.race?.analysis?.modelVersion === 'tjk-v3';
}

function renderDashboard() {
  const data = state.race;
  const calibrated = isV3() && data.analysis.scoreKind === 'calibrated_probability';
  const leader = data.analysis.picks.leader;
  const steam = data.analysis.picks.steam;
  const value = data.analysis.picks.value;
  const surprise = data.analysis.picks.surprise;

  el.venueLabel.textContent = data.venue.name;
  el.raceTitle.textContent = `${data.race.number}. Koşu · ${data.race.time || '—'}`;
  el.raceMeta.textContent = [data.race.type, data.race.condition, data.race.distance, data.race.surface].filter(Boolean).join(' · ');
  el.raceStatus.textContent = data.race.status;
  el.updatedTime.textContent = `Güncellendi ${new Date(data.updatedAt).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;

  el.confidenceChip.textContent = data.analysis.status === 'PAS' ? 'PAS' : calibrated ? 'V3 test' : `${data.analysis.confidenceLabel} sinyal`;
  el.leaderNumber.textContent = leader?.number ?? '—';
  el.leaderName.textContent = leader?.name ?? 'PAS — veri yetersiz';
  el.leaderSummary.textContent = [data.analysis.summary, ...(data.analysis.reasonCodes || [])].join(' · ');
  const displayedScore = calibrated ? leader?.probability : data.analysis.confidence;
  document.querySelector('.confidence-row span').textContent = calibrated ? 'Kalibre edilmiş kazanma olasılığı' : isV3() ? 'V3 olasılığı — PAS' : 'Sinyal gücü (olasılık değil)';
  el.confidenceValue.textContent = data.analysis.status === 'PAS' ? '—' : calibrated ? pct(displayedScore) : `${fmt(displayedScore, 0)}/100`;
  el.confidenceBar.style.width = `${Number.isFinite(displayedScore) && data.analysis.status !== 'PAS' ? Math.max(0, Math.min(100, displayedScore)) : 0}%`;

  el.steamPick.textContent = steam ? `${steam.number} ${steam.name}` : '—';
  el.steamMove.textContent = steam ? `${movementArrow(steam.movement)} İlk doğrulanmış kayıttan ${pct(steam.movement, true)}` : 'Yeterli geçmiş yok';
  el.valuePick.textContent = value ? `${value.number} ${value.name}` : '—';
  el.valueEdge.textContent = value ? `Model farkı ${pct(value.edge, true)}` : 'Değer adayı yok';
  el.surprisePick.textContent = surprise ? `${surprise.number} ${surprise.name}` : 'Net sürpriz yok';
  el.surpriseDetail.textContent = surprise ? `${fmt(surprise.odds)} Gny · ${calibrated ? 'Olasılık ' + pct(surprise.probability) : 'Puan ' + fmt(surprise.probability, 1) + '/100'}` : (data.analysis.status === 'PAS' ? 'Veri yetersiz' : 'Aday yok');

  el.methodology.textContent = data.methodology;
  el.warningText.textContent = data.warning;
  renderRunners();
  renderSelectedRunner();
  renderMarket();
}

function runnerTags(runner) {
  const tags = [];
  if (runner.modelRank === 1) tags.push('<span class="tag support">Model 1</span>');
  if (runner.agfRank === 1) tags.push('<span class="tag">AGF 1</span>');
  if (runner.supportSignal) tags.push(`<span class="tag ${movementClass(runner.movementPercent)}">${escapeHtml(runner.supportSignal)}</span>`);
  return tags.join('');
}

function renderRunners() {
  const selected = state.selectedRunner?.number;
  el.runnerList.innerHTML = state.race.runners.map((runner) => `
    <article class="runner-card ${runner.number === selected ? 'selected' : ''}" data-runner="${runner.number}" tabindex="0" role="button" aria-label="${escapeHtml(runner.number)} numara ${escapeHtml(runner.name)} detayını aç">
      <div class="rank-badge">${runner.number}<small>${runner.modelRank ?? '—'}</small></div>
      <div class="runner-name">
        <h3>${escapeHtml(runner.name)}</h3>
        <div class="runner-tags">${runnerTags(runner)}</div>
      </div>
      <div class="metric mobile-odds"><span>Ganyan</span><strong>${fmt(runner.currentOdds)}</strong></div>
      <div class="metric optional"><span>İlk doğrulanmış</span><strong>${fmt(runner.openingOdds)}</strong></div>
      <div class="metric mobile-move"><span>Hareket</span><strong class="${movementClass(runner.movementPercent)}">${movementArrow(runner.movementPercent)} ${pct(runner.movementPercent, true)}</strong></div>
      <div class="metric optional"><span>AGF</span><strong>${pct(runner.agfLatest)}</strong></div>
      <div class="metric optional"><span>Piyasa payı</span><strong>${pct(runner.marketProbability)}</strong></div>
      <div class="metric mobile-model"><span>${isV3() ? 'V3 olasılığı' : 'Model puanı'}</span><strong>${isV3() ? pct(runner.modelProbability) : fmt(runner.modelProbability, 1)}</strong><div class="probability-track"><i style="width:${Math.min(100, Math.max(0, (runner.modelProbability || 0) * (isV3() ? 1 : 2.5)))}%"></i></div></div>
    </article>
  `).join('');

  el.runnerList.querySelectorAll('.runner-card').forEach((card) => {
    const activate = () => selectRunner(Number(card.dataset.runner));
    card.addEventListener('click', activate);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
  });
}

function selectRunner(number) {
  state.selectedRunner = state.race.runners.find((runner) => runner.number === number) || state.race.runners[0];
  renderRunners();
  renderSelectedRunner();
  document.querySelector('.chart-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderSelectedRunner() {
  const runner = state.selectedRunner;
  if (!runner) {
    el.chartTitle.textContent = 'Oran hareketi';
    el.chartStat.textContent = '—';
    el.detailTitle.textContent = 'At detayı';
    el.detailList.innerHTML = '';
    el.historyChart.innerHTML = '<div class="empty-chart">Doğrulanmış at verisi yok.</div>';
    return;
  }
  el.chartTitle.textContent = `${runner.number} ${runner.name}`;
  el.chartStat.textContent = `${fmt(runner.openingOdds)} → ${fmt(runner.currentOdds)} (${pct(runner.movementPercent, true)})`;
  el.detailTitle.textContent = `${runner.number} ${runner.name}`;

  const details = [
    ['Jokey', runner.jockey],
    ['Kilo', runner.weight],
    ['Handikap', runner.rating],
    ['Start', runner.stall],
    ['AGF', runner.agfLatest !== null ? pct(runner.agfLatest) : null],
    ['Son yarışlar', runner.lastSix],
    ['En iyi derece', runner.bestTime],
    ['Model farkı', pct(runner.edge, true)]
  ];
  el.detailList.innerHTML = details.map(([label, value]) => `
    <div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? '—')}</dd></div>
  `).join('');
  renderChart(runner);
}

function renderChart(runner) {
  // Plot only timestamped, verified source observations. Appending the current
  // quote would manufacture a movement when its observation time is unknown.
  const points = runner.historyStatus === 'UNVERIFIED' ? [] : (runner.history || [])
    .filter((point) => Number.isFinite(point.odds) && point.odds > 0 && Number.isFinite(point.at));
  if (points.length < 2) {
    el.historyChart.innerHTML = '<div class="empty-chart">Bu at için henüz yeterli oran geçmişi oluşmadı.</div>';
    return;
  }

  const width = 760;
  const height = 245;
  const pad = { left: 42, right: 18, top: 16, bottom: 30 };
  const min = Math.min(...points.map((point) => Number(point.odds)));
  const max = Math.max(...points.map((point) => Number(point.odds)));
  const range = max - min || 1;
  const usableWidth = width - pad.left - pad.right;
  const usableHeight = height - pad.top - pad.bottom;
  const coords = points.map((point, index) => ({
    x: pad.left + (index / Math.max(1, points.length - 1)) * usableWidth,
    y: pad.top + ((Number(point.odds) - min) / range) * usableHeight,
    ...point
  }));
  const line = coords.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
  const area = `${pad.left},${height - pad.bottom} ${line} ${width - pad.right},${height - pad.bottom}`;
  const gridLines = [0, 0.5, 1].map((ratio) => {
    const y = pad.top + ratio * usableHeight;
    const value = min + ratio * range;
    return `<line class="chart-grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"></line><text class="chart-label" x="3" y="${y + 4}">${fmt(value)}</text>`;
  }).join('');
  const labelIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  const timeLabels = labelIndexes.map((index) => `<text class="chart-label" text-anchor="${index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}" x="${coords[index].x}" y="${height - 7}">${escapeHtml(points[index].time || '')}</text>`).join('');

  el.historyChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(runner.name)} oran hareketi">
      <defs><linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#46d6b3" stop-opacity=".28"></stop><stop offset="1" stop-color="#46d6b3" stop-opacity="0"></stop></linearGradient></defs>
      ${gridLines}
      <polygon class="chart-area" points="${area}"></polygon>
      <polyline class="chart-line" points="${line}"></polyline>
      <circle cx="${coords.at(-1).x}" cy="${coords.at(-1).y}" r="5" fill="#46d6b3"></circle>
      ${timeLabels}
    </svg>`;
}

function renderMarket() {
  document.querySelectorAll('.market-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.market === state.selectedMarket));
  const items = state.race?.markets?.[state.selectedMarket] || [];
  if (!items.length) {
    el.marketGrid.innerHTML = '<div class="market-empty">Bu pazar için henüz oran bulunmuyor.</div>';
    return;
  }
  el.marketGrid.innerHTML = items.slice(0, 16).map((item) => `
    <article class="market-item">
      <div><strong>${escapeHtml(item.selection)}</strong><span>${escapeHtml(item.label)}</span></div>
      <b class="market-odds">${fmt(item.odds)}</b>
    </article>
  `).join('');
}

function resetAutoRefresh() {
  clearInterval(state.timer);
  if (!el.autoRefresh.checked) return;
  state.timer = setInterval(() => {
    if (!document.hidden) loadRace({ silent: true });
  }, 15_000);
}

el.dateInput.addEventListener('change', () => loadDay({ preserveVenue: false }));
el.venueSelect.addEventListener('change', () => {
  populateRaces(venueByKey());
  loadRace();
});
el.raceSelect.addEventListener('change', () => loadRace());
el.refreshButton.addEventListener('click', () => loadRace());
el.autoRefresh.addEventListener('change', resetAutoRefresh);
document.querySelectorAll('.market-tab').forEach((tab) => tab.addEventListener('click', () => {
  state.selectedMarket = tab.dataset.market;
  renderMarket();
}));

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
async function initialize() {
  setLoading(true);
  try {
    const config = await api('/api/config');
    if (typeof config.v3Enabled !== 'boolean') throw new Error('Panel yapılandırması doğrulanamadı.');
    state.config = config;
    if (config.v3Enabled) {
      document.title = 'TJK V3 · Test paneli';
      document.querySelector('.brand h1').textContent = 'TJK V3 · Test paneli';
      document.querySelector('.brand .eyebrow').textContent = 'RESMÎ TJK VERİSİ · V3 TEST';
    }
    resetAutoRefresh();
    await loadDay({ preserveVenue: false });
  } catch (error) {
    state.config = null;
    state.race = null;
    setLoading(false);
    el.loadingView.hidden = true;
    showError(`PAS — ${error.message}`);
    setConnected(false);
  }
}

const ready = initialize();
