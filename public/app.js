const state = {
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
  if (value <= -3) return 'support';
  if (value >= 3) return 'drift';
  return '';
}

function movementArrow(value) {
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
  el.refreshButton.disabled = active;
  el.refreshButton.classList.toggle('loading', active);
  if (!state.race) {
    el.loadingView.hidden = !active;
    el.dashboard.hidden = active;
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
    showError(error.message);
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
  if ((!force && state.loading) || !el.dateInput.value || !el.venueSelect.value || !el.raceSelect.value) return;
  if (!silent) showError('');
  setLoading(true);
  try {
    const query = new URLSearchParams({
      date: el.dateInput.value,
      venue: el.venueSelect.value,
      race: el.raceSelect.value
    });
    const data = await api(`/api/race?${query}`);
    const selectedNumber = state.selectedRunner?.number;
    state.race = data;
    state.selectedRunner = data.runners.find((runner) => runner.number === selectedNumber) || data.runners[0] || null;
    renderDashboard();
    el.loadingView.hidden = true;
    el.dashboard.hidden = false;
    setConnected(true, data.race.status === 'AÇIK' ? 'Canlı' : data.race.status);
  } catch (error) {
    showError(error.message);
    setConnected(false);
  } finally {
    setLoading(false);
  }
}

function renderDashboard() {
  const data = state.race;
  const leader = data.analysis.picks.leader;
  const steam = data.analysis.picks.steam;
  const value = data.analysis.picks.value;
  const surprise = data.analysis.picks.surprise;

  el.venueLabel.textContent = data.venue.name;
  el.raceTitle.textContent = `${data.race.number}. Koşu · ${data.race.time}`;
  el.raceMeta.textContent = [data.race.type, data.race.condition, data.race.distance, data.race.surface].filter(Boolean).join(' · ');
  el.raceStatus.textContent = data.race.status;
  el.updatedTime.textContent = `Güncellendi ${new Date(data.updatedAt).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;

  el.confidenceChip.textContent = `${data.analysis.confidenceLabel} güven`;
  el.leaderNumber.textContent = leader?.number ?? '—';
  el.leaderName.textContent = leader?.name ?? 'Veri yok';
  el.leaderSummary.textContent = data.analysis.summary;
  el.confidenceValue.textContent = `${data.analysis.confidence}/100`;
  el.confidenceBar.style.width = `${data.analysis.confidence}%`;

  el.steamPick.textContent = steam ? `${steam.number} ${steam.name}` : '—';
  el.steamMove.textContent = steam ? `${movementArrow(steam.movement)} Açılıştan ${pct(steam.movement, true)}` : 'Yeterli geçmiş yok';
  el.valuePick.textContent = value ? `${value.number} ${value.name}` : '—';
  el.valueEdge.textContent = value ? `Model farkı ${pct(value.edge, true)}` : 'Değer adayı yok';
  el.surprisePick.textContent = surprise ? `${surprise.number} ${surprise.name}` : 'Net sürpriz yok';
  el.surpriseDetail.textContent = surprise ? `${fmt(surprise.odds)} Gny · ${pct(surprise.probability)}` : 'Piyasa dengeli';

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
  tags.push(`<span class="tag ${movementClass(runner.movementPercent)}">${escapeHtml(runner.supportSignal)}</span>`);
  return tags.join('');
}

function renderRunners() {
  const selected = state.selectedRunner?.number;
  el.runnerList.innerHTML = state.race.runners.map((runner) => `
    <article class="runner-card ${runner.number === selected ? 'selected' : ''}" data-runner="${runner.number}" tabindex="0" role="button" aria-label="${escapeHtml(runner.number)} numara ${escapeHtml(runner.name)} detayını aç">
      <div class="rank-badge">${runner.number}<small>${runner.modelRank}</small></div>
      <div class="runner-name">
        <h3>${escapeHtml(runner.name)}</h3>
        <div class="runner-tags">${runnerTags(runner)}</div>
      </div>
      <div class="metric mobile-odds"><span>Ganyan</span><strong>${fmt(runner.currentOdds)}</strong></div>
      <div class="metric optional"><span>Açılış</span><strong>${fmt(runner.openingOdds)}</strong></div>
      <div class="metric mobile-move"><span>Hareket</span><strong class="${movementClass(runner.movementPercent)}">${movementArrow(runner.movementPercent)} ${pct(runner.movementPercent, true)}</strong></div>
      <div class="metric optional"><span>AGF</span><strong>${pct(runner.agfLatest)}</strong></div>
      <div class="metric optional"><span>Piyasa payı</span><strong>${pct(runner.marketProbability)}</strong></div>
      <div class="metric mobile-model"><span>Model</span><strong>${pct(runner.modelProbability)}</strong><div class="probability-track"><i style="width:${Math.min(100, runner.modelProbability * 2.5)}%"></i></div></div>
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
  if (!runner) return;
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
  const raw = [...(runner.history || [])];
  if (!raw.length || raw.at(-1)?.odds !== runner.currentOdds) {
    raw.push({ time: 'Şimdi', odds: runner.currentOdds });
  }
  const points = raw.filter((point) => Number.isFinite(Number(point.odds)));
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
resetAutoRefresh();
loadDay({ preserveVenue: false });
