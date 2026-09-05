const state = { items: [], selected: null };
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function load() {
  state.items = await (await fetch('/api/observability/tree', { cache: 'no-store' })).json();
  renderList();
  if (state.items.length) selectIncident(state.items[0].incident_id);
}

function renderList() {
  const query = document.querySelector('#filter').value.toLowerCase();
  document.querySelector('#incidents').innerHTML = state.items.filter(item => item.title.toLowerCase().includes(query)).map(item => `
    <div class="incident ${state.selected?.incident_id === item.incident_id ? 'selected' : ''}" data-id="${esc(item.incident_id)}">
      ${esc(item.title)}<small>${item.sessions.length} session · ${esc(item.status)}</small>
      ${item.sessions.map((session, index) => `<small class="history-session">↳ 对话 ${index + 1} · ${esc(session.status)}</small>`).join('')}
    </div>`).join('') || '<div class="muted">没有 Incident</div>';
  document.querySelectorAll('.incident').forEach(item => item.onclick = () => selectIncident(item.dataset.id));
}

function selectIncident(id) {
  state.selected = state.items.find(item => item.incident_id === id);
  renderList();
  const incident = state.selected;
  document.querySelector('#title').textContent = incident.title;
  document.querySelector('#overview').innerHTML = `<div class="overview-grid"><div><label>Status</label><div class="stat">${esc(incident.status)}</div></div><div><label>Sessions</label><div class="stat">${incident.sessions.length}</div></div><div><label>Turns</label><div class="stat">${incident.sessions.reduce((sum, item) => sum + item.turn_count, 0)}</div></div><div><label>Tokens</label><div class="stat">${incident.sessions.reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0)}</div></div></div>`;
  document.querySelector('#tree').innerHTML = `<div class="tree-card"><div class="tree-header"><span>▾</span><strong>Incident</strong><span class="status">${esc(incident.status)}</span></div><div class="tree-body">${incident.sessions.map(session => `<div class="session"><div class="session-head" data-session="${session.session_id}"><span>›</span><strong>Session ${esc(session.session_id.slice(0, 8))}</strong><span class="muted">${session.turn_count} turns</span><span class="status">${esc(session.status)}</span></div>${session.turns.map((turn, index) => `<div class="turn">└ Turn ${index + 1} · ${esc(turn.status)} · ${turn.task_count} tasks</div>`).join('')}</div>`).join('')}</div></div>`;
  document.querySelectorAll('[data-session]').forEach(item => item.onclick = () => selectSession(item.dataset.session));
}

function renderTraceGroup(title, items, empty = '未执行') {
  return `<div class="section"><h3>${title}</h3>${items.length ? items.map(item => `<div class="span">${esc(item.name)} <span class="muted">${esc(item.request_id || '')}</span></div>`).join('') : `<div class="muted">${empty}</div>`}</div>`;
}

async function selectSession(id) {
  const data = await (await fetch(`/api/observability/sessions/${id}`, { cache: 'no-store' })).json();
  const groups = data.trace.groups || {};
  document.querySelector('#kind').textContent = 'SESSION';
  document.querySelector('#detail').innerHTML = `<div class="section"><h3>IDENTITY</h3><div class="kv"><span>Session</span><b>${esc(data.session_id.slice(0, 12))}</b></div><div class="kv"><span>Trace</span><b>${esc(data.trace_id || '—')}</b></div><div class="kv"><span>Status</span><b>${esc(data.status)}</b></div></div><div class="section"><h3>TOKEN USAGE</h3><div class="kv"><span>Input</span><b>${data.token_usage.input_tokens}</b></div><div class="kv"><span>Output</span><b>${data.token_usage.output_tokens}</b></div><div class="kv"><span>Cached input</span><b>${data.token_usage.cached_input_tokens}</b></div></div>${renderTraceGroup('CONTEXT BUILD', groups.context)}${renderTraceGroup('MEMORY RECALL / WRITE', groups.memory)}${renderTraceGroup('RAG RETRIEVAL', groups.rag)}${renderTraceGroup('MODEL COMPLETE', groups.model)}${renderTraceGroup('TOOL CALL / ATTEMPT', groups.tools)}${renderTraceGroup('WORKER', groups.worker)}<div class="section"><h3>MEMORY</h3><div class="kv"><span>Events</span><b>${data.event_count}</b></div><div class="kv"><span>Evidence refs</span><b>${data.evidence_ref_count}</b></div></div>`;
}

document.querySelector('#filter').oninput = renderList;
load();
