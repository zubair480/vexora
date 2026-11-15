// BeltGuard dashboard (design v3 guided flow) — wiring between the backend contract (../CONTRACT.md) and the UI.
// Live mode: WebSocket /ws + REST /api/*. No backend: a clearly labelled demo replay.
// The band indicator and the "Connect band 07" button belong to bandlink.js (S0); this file never names a sim source on screen.
import { StripChart, Sparkline } from './charts.js?v=6';
import { DemoReplay, DEMO_STATIONS } from './replay.js?v=6';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nowS = () => Date.now() / 1000;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtPts = (v) => { const n = Number(v); if (!isFinite(n)) return '—'; return Math.abs(n) < 10 ? n.toFixed(2) : n.toFixed(1); };
const fmtClock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const fmtSigned = (v, d) => { const n = Number(v) || 0; return `${n < 0 ? '−' : '+'}${Math.abs(n).toFixed(d)}`; };
const cap = (s) => { s = String(s || ''); return s ? s[0].toUpperCase() + s.slice(1) : s; };
const len = (o, k) => ((o && Array.isArray(o[k])) ? o[k].length : 0);
function fmtDur(s) {
  s = Math.max(0, Math.round(Number(s) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`;
  if (m) return `${m} min ${String(x).padStart(2, '0')} s`;
  return `${x} s`;
}
function setHtml(el, html) { if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } }
function setText(el, txt) { if (el && el.textContent !== txt) el.textContent = txt; }
// Demo safety: one missing element or bad value must never stop the socket or the other panels.
const warned = new Set();
function safe(fn) {
  try { return fn(); } catch (err) {
    const key = fn.name || String(fn).slice(0, 40);
    if (!warned.has(key)) { warned.add(key); console.warn(`BeltGuard: ${key} skipped —`, err && err.message); }
    return undefined;
  }
}
const canvasOr = (sel) => document.querySelector(sel) || document.createElement('canvas');
const on = (sel, ev, fn) => { const el = document.querySelector(sel); if (el) el.addEventListener(ev, fn); };

// ---------- config ----------
const qs = new URLSearchParams(location.search);
const WS_HOST = qs.get('ws');                       // e.g. ?ws=127.0.0.1:8765 when served statically
const FORCE_DEMO = qs.has('demo');
const STATIC_MODE = /\/frontend\//.test(location.pathname) && !WS_HOST;
const HTTP_BASE = WS_HOST ? `${location.protocol}//${WS_HOST}` : '';

const STEPS = ['Inspect', 'Report defect', 'Work order', 'Tool use', 'Exposure', 'Next job'];
const ACT_LABEL = { idle: 'Idle', walking: 'Walking', gesture: 'Hand gesture', candidate: 'Possible tool use', tool_active: 'Tool running', unresolved: 'Unresolved' };
const ACT_BADGE = { tool_active: 'tool', candidate: 'outline wait', unresolved: 'warning' };
const LANES = [
  { id: 'walking', label: 'Walking' },
  { id: 'inspection', label: 'Inspection' },
  { id: 'tool', label: 'Tool use' },
  { id: 'exposure', label: 'Exposure' },
  { id: 'work_order', label: 'Work orders' },
];

function emptyState() {
  return {
    worker: { id: 'W-01', name: '' },
    conveyor: { id: 'CV-102', name: 'Overland conveyor CV-102' },
    stations: DEMO_STATIONS.map((s) => ({ ...s })),
    active_station: null,
    tools: [],
    active_tool: null,
    defects: [],
    work_orders: [],
    tool_sessions: [],
    exposure: { total_points: 0, action: 100, limit: 400, status: 'below_action', records: [] },
    recommendation: null,
    timeline: [],
    connection: { state: 'disconnected', band: 7, rate: 50, lost_total: 0 },
    mic: { on: false },
    detector: { state: 'idle', confidence: 0 },
  };
}

const app = {
  live: false,
  everLive: false,
  demo: false,
  gotSnapshot: false,
  state: emptyState(),
  events: new Map(),
  walkSegs: [],
  activity: { state: 'idle', confidence: 0, features: {} },
  transcript: { text: '', final: true },
  clarify: null,
  vib: { mean: 1, ms: 0, rms: 0 },
  rate: 50,
  lost: 0,
  firstTs: nowS(),
  lastMotion: 0,
  lastA: null,
  lastG: null,
  step: 1,
  sheet: false,
  dtab: 'signals',
  tlRange: '',
  bandlink: (window.BandLink && window.BandLink.status) || null,
};

// ---------- charts and 3D ----------
const SIG = ['#18181B', '#3B82F6', '#A1A1AA'];
const accelChart = new StripChart(canvasOr('#accelChart'), { colors: SIG, minRange: 1.2 });
const gyroChart = new StripChart(canvasOr('#gyroChart'), { colors: SIG, minRange: 30 });
const spark = new Sparkline(canvasOr('#spark'));

let model = null;
import(`./model3d.js?v=${Date.now()}`)          // always fetch the newest scene (S7 owns model3d.js)
  .then((m) => {
    model = m.createModel($('#stage'), { onStationClick: selectStation });
    model.apply(app.state);
    model.setActivity(app.activity.state);
    focusModel();
  })
  .catch((err) => {
    console.warn('3D model unavailable:', err);
    $('#stageFallback').hidden = false;
  });

// ---------- helpers ----------
const stationList = () => (Array.isArray(app.state.stations) && app.state.stations.length ? app.state.stations : DEMO_STATIONS);
const stationById = (id) => stationList().find((s) => s.id === id) || null;
const shortName = (st) => String((st && st.name) || '').split(/\s*[&/]\s*/)[0];
function toolById(id) {
  if (!id) return null;
  const t = (app.state.tools || []).find((x) => x.id === id);
  if (t) return t;
  return app.state.active_tool && app.state.active_tool.id === id ? app.state.active_tool : null;
}
function setBadge(el, variant, text) {
  el.className = `badge ${variant}`;
  const span = el.querySelector('span');
  if (span) setText(span, text); else setText(el, text);
}
const isSimMsg = (m) => app.live && (m.source === 'sim' || (app.state.connection || {}).state === 'simulated');
function bandLive() {
  if (app.demo) return true;                                 // demo replay values are labelled in the top bar
  const bl = app.bandlink || (window.BandLink && window.BandLink.status);
  if (bl && bl.state === 'connected') return true;
  const c = app.state.connection || {};
  return c.state === 'connected' && !c.simulated;
}
const motionFresh = () => app.lastMotion && performance.now() - app.lastMotion < 2500;
const latest = (arr) => (Array.isArray(arr) && arr.length ? arr[arr.length - 1] : null);
function milestones(s = app.state) {
  return {
    1: !!s.active_station,
    2: len(s, 'defects') > 0,
    3: len(s, 'work_orders') > 0,
    4: (s.tool_sessions || []).some((x) => x && x.status !== 'rejected'),
    5: len(s.exposure, 'records') > 0,
    6: !!s.recommendation,
  };
}
function initialStep(s) {
  const m = milestones(s);
  if (m[6]) return 6;
  if (m[5]) return 5;
  if (m[4]) return 4;
  if (m[3]) return 3;
  if (m[1]) return 2;
  return 1;
}

// ---------- incoming messages ----------
function handleMessage(m) {
  if (!m || typeof m !== 'object') return;
  switch (m.type) {
    case 'motion': if (!isSimMsg(m)) onMotion(m); break;
    case 'activity': if (!isSimMsg(m)) onActivity(m); break;
    case 'connection':
      app.state.connection = { ...app.state.connection, ...m };
      if (m.rate) setRate(m.rate);
      renderStep4();
      break;
    case 'transcript':
      app.transcript = { text: m.text || '', final: m.final !== false };
      renderTranscript();
      break;
    case 'clarify':
      app.clarify = { question: m.question || 'Please clarify the report.' };
      renderClarify();
      goStep(2);
      toast('Clarification needed', app.clarify.question, 'warn');
      break;
    case 'event': addEvent(m.event, false); break;
    case 'snapshot': applySnapshot(m.state); break;
    default: break;
  }
}

function setRate(hz) {
  if (!hz || hz === app.rate) return;
  app.rate = hz;
  accelChart.setRate(hz);
  gyroChart.setRate(hz);
  spark.setRate(hz);
}

function onMotion(m) {
  const A = Array.isArray(m.accel) ? m.accel : [];
  const G = Array.isArray(m.gyro) ? m.gyro : [];
  const v = app.vib;
  for (const a of A) {
    if (!a) continue;
    accelChart.push(a);
    spark.push(a);
    const mag = Math.hypot(a[0], a[1], a[2]);
    v.mean += (mag - v.mean) * 0.02;
    const d = mag - v.mean;
    v.ms += (d * d - v.ms) * 0.06;
    v.rms = Math.sqrt(Math.max(0, v.ms));
    app.lastA = a;
  }
  for (const g of G) { if (g) { gyroChart.push(g); app.lastG = g; } }
  if (m.rate) setRate(m.rate);
  if (m.lost_total != null) app.lost = m.lost_total;
  if (A.length) app.lastMotion = performance.now();
  if (model) model.setVibration(v.rms);
}

function onActivity(m) {
  const prev = app.activity.state;
  const st = m.state || 'idle';
  const t = m.ts || nowS();
  app.activity = { state: st, confidence: Number(m.confidence) || 0, features: m.features || {} };
  if (st === 'walking' && prev !== 'walking') app.walkSegs.push({ start: t, end: null });
  if (st !== 'walking' && prev === 'walking') {
    const seg = app.walkSegs[app.walkSegs.length - 1];
    if (seg && seg.end == null) seg.end = t;
  }
  if (app.walkSegs.length > 300) app.walkSegs.shift();
  renderActivity();
  if (model) model.setActivity(st);
}

function applySnapshot(s) {
  if (!s || typeof s !== 'object') return;
  const prev = app.state;
  const base = emptyState();
  app.state = {
    ...base,
    ...s,
    exposure: { ...base.exposure, ...(s.exposure || {}) },
    connection: { ...base.connection, ...(s.connection || {}) },
    mic: { ...base.mic, ...(s.mic || {}) },
  };
  if (len(app.state, 'defects') > len(prev, 'defects')) { app.clarify = null; renderClarify(); }
  if (Array.isArray(s.timeline)) for (const e of s.timeline) addEvent(e, true);
  if (s.connection && s.connection.rate) setRate(s.connection.rate);
  if (!app.gotSnapshot) { app.gotSnapshot = true; app.step = initialStep(app.state); showStep(); }
  else autoAdvance(prev, app.state);
  renderAll();
}

// Live events move the flow forward: station → defect + work order → tool session → exposure record → recommendation.
function autoAdvance(prev, next) {
  let t = 0;
  if (next.active_station && next.active_station !== prev.active_station) t = 2;
  if (next.mic && next.mic.on && !(prev.mic && prev.mic.on)) t = Math.max(t, 2);
  if (len(next, 'defects') > len(prev, 'defects')) t = Math.max(t, 2);
  if (len(next, 'work_orders') > len(prev, 'work_orders')) t = 3;
  const seen = new Set((prev.tool_sessions || []).map((x) => x && x.id));
  const newSession = (next.tool_sessions || []).some((x) => x && !seen.has(x.id));
  const nowActive = (next.tool_sessions || []).some((x) => x && x.status === 'active') && !(prev.tool_sessions || []).some((x) => x && x.status === 'active');
  if (newSession || nowActive) t = 4;
  if (len(next.exposure, 'records') > len(prev.exposure, 'records')) t = 5;
  const r1 = next.recommendation, r0 = prev.recommendation;
  if (r1 && (!r0 || r1.ts !== r0.ts || r1.projected_points !== r0.projected_points)) t = 6;
  if (t) goStep(t);
}

function addEvent(e, quiet) {
  if (!e || typeof e !== 'object') return;
  const id = String(e.id ?? `${e.ts}-${e.kind}-${e.title}`);
  const isNew = !app.events.has(id);
  app.events.set(id, { ...e, id, ts: Number(e.ts) || nowS() });
  if (app.events.size > 600) app.events.delete(app.events.keys().next().value);
  if (isNew && !quiet) maybeToast(e);
}

function maybeToast(e) {
  const k = String(e.kind || '');
  let cls = null;
  if (/elv|limit/.test(k)) cls = 'alarm';
  else if (/eav|action/.test(k)) cls = 'warn';
  else if (/defect/.test(k)) cls = 'alarm';
  if (cls) toast(e.title, e.detail, cls);
}

// ---------- flow ----------
function goStep(n) {
  n = clamp(Number(n) || 1, 1, 6);
  if (n === app.step) return;
  app.step = n;
  showStep();
  focusModel();
}

function focusModel() {
  if (!model || typeof model.focusStation !== 'function') return;
  const s = app.state;
  const wo = latest(s.work_orders);
  const rec = s.recommendation;
  const id = app.step === 3 && wo ? wo.station_id
    : app.step === 6 && rec ? (rec.station_id || (rec.planned && rec.planned.station_id))
      : s.active_station;
  try { model.focusStation(id || null, app.step); } catch (err) { /* the scene owns its camera */ }
}

function showStep() {
  for (let i = 1; i <= 6; i += 1) $(`#step-${i}`).hidden = i !== app.step;
  setText($('#stepCount'), `Step ${app.step} of 6 · ${STEPS[app.step - 1]}`);
  $('#stepPrev').disabled = app.step === 1;
  $('#stepNext').disabled = app.step === 6;
  renderStepper();
}

const CHECK = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 5 8.6l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function renderStepper() {
  const m = milestones();
  const html = STEPS.map((label, i) => {
    const n = i + 1;
    const cur = n === app.step;
    const done = !cur && m[n];
    const cls = cur ? 'current' : done ? 'done' : '';
    const word = cur ? 'current step' : done ? 'done' : 'not started';
    const line = n < 6 ? `<span class="line${m[n] ? ' done' : ''}" aria-hidden="true"></span>` : '';
    return `<li><button type="button" class="step-btn ${cls}" data-action="go-step" data-step="${n}" aria-current="${cur ? 'step' : 'false'}" aria-label="Step ${n}, ${label}, ${word}">
      <span class="num">${done ? CHECK : n}</span><span>${label}</span></button>${line}</li>`;
  }).join('');
  setHtml($('#stepper'), html);
}

// ---------- rendering ----------
function renderAll() {
  [renderHeader, renderStepper, renderStep1, renderStep2, renderStep3, renderTools, renderStep4,
    renderExposure, renderStep6, renderMic, renderPlanSelects, renderRecords].forEach(safe);
  if (model) safe(function applyModel() { model.apply(app.state); });
}

function renderHeader() {
  const s = app.state;
  const conv = s.conveyor || {};
  setText($('#assetName'), conv.id || 'CV-102');
  $('#assetName').title = conv.name || '';
  const st = stationById(s.active_station);
  setText($('#topStation'), st ? `Station ${st.number} · ${st.name}` : 'No station selected');
}

function renderMode() {
  const pill = $('#modePill');
  $('#demoBanner').hidden = !app.demo;
  if (app.demo || app.live) { pill.hidden = true; return; }
  pill.hidden = false;
  if (app.everLive) setBadge(pill, 'destructive', 'Backend offline · retrying');
  else setBadge(pill, 'outline wait', 'Connecting…');
}

function stationStatus(st) {
  const s = app.state;
  const defect = (s.defects || []).some((d) => d && d.station_id === st.id) || st.status === 'defect';
  if (defect) return ['defect', 'Defect reported'];
  if (st.status === 'inspected') return ['inspected', 'Inspected, no issues'];
  if (st.status === 'active') return ['', 'Inspection in progress'];
  return ['', 'Not inspected'];
}

function renderStep1() {
  const s = app.state;
  const list = stationList();
  let inspected = 0;
  const rows = list.map((st) => {
    const [cls, word] = stationStatus(st);
    if (cls) inspected += 1;
    const sel = s.active_station === st.id;
    return `<li><button type="button" class="st-row ${cls}${sel ? ' sel' : ''}" data-action="station" data-id="${esc(st.id)}" aria-pressed="${sel}">
      <span class="st-num" aria-hidden="true">${esc(st.number)}</span>
      <span class="st-text"><span class="st-name">${esc(st.name)}</span><span class="st-status"><i class="sdot ${cls}" aria-hidden="true"></i>${word}</span></span>
      ${sel ? '<span class="badge outline here">You are here</span>' : ''}</button></li>`;
  }).join('');
  setHtml($('#stationList'), rows);
  setText($('#s1Inspected'), String(inspected));
  setText($('#s1Defects'), String(len(s, 'defects')));
  const b = $('#s1Action');
  const st = stationById(s.active_station);
  b.disabled = !st;
  setText(b, st ? `Report a defect at station ${st.number}` : 'Select a station first');
}

function renderStep2() {
  const s = app.state;
  const st = stationById(s.active_station);
  setText($('#s2Title'), st ? `Report what you see at station ${st.number}` : 'Report what you see');
  setText($('#s2Desc'), st ? `${st.name}. Turn the mic on and describe the problem, or type it.` : 'Select a station first, or name the station in your report.');
  const defs = (s.defects || []).filter((d) => d && (!st || d.station_id === st.id));
  const d = latest(defs);
  setText($('#s2Component'), d ? cap(d.component || '—') : '—');
  setText($('#s2Condition'), d ? cap(String(d.condition || '—').toLowerCase()) : '—');
  const sev = d ? String(d.severity || '').toLowerCase() : '';
  const sevEl = $('#s2Severity');
  setText(sevEl, sev ? cap(sev) : '—');
  sevEl.classList.toggle('sev-high', sev === 'high' || sev === 'critical');
  const src = d ? ({ ai: 'Extracted by AI and checked against the asset register.', rules: 'Extracted by rules and checked against the asset register.', user: 'Entered by the technician.' }[d.source] || '') : '';
  setText($('#s2Why'), d
    ? `Last report ${fmtClock(d.ts || nowS())}. ${src}`.trim()
    : 'BeltGuard pulls out the component, condition and severity. If the station or part is unclear, it asks instead of guessing.');
}

const STATUS_WORD = { draft: 'Draft', planned: 'Planned', in_progress: 'In progress', awaiting_verification: 'Awaiting verification' };
function renderStep3() {
  const s = app.state;
  const wo = latest(s.work_orders);
  const b = $('#s3Action');
  if (!wo) {
    setText($('#s3Title'), 'No work order yet');
    setText($('#s3Desc'), 'A draft is created from each validated defect report.');
    setText($('#s3Priority'), '—'); setText($('#s3Min'), '—'); setText($('#s3Pts'), '—');
    setHtml($('#s3StepList'), '');
    $('#s3Steps').hidden = true;
    b.dataset.mode = 'report';
    setText(b, 'Report a defect first');
    return;
  }
  const st = stationById(wo.station_id);
  const [title] = String(wo.title || 'Repair').split(/\s+—\s+/);
  setText($('#s3Title'), title);
  setText($('#s3Desc'), `${wo.id} · ${STATUS_WORD[wo.status] || cap(wo.status || 'draft')}${st ? ` · station ${st.number}, ${st.name}` : ''}`);
  setText($('#s3Priority'), wo.priority || '—');
  const tool = toolById(wo.tool_id);
  const a = tool ? Number(tool.vibration_m_s2) : null;
  setText($('#s3Min'), wo.est_trigger_min != null ? String(wo.est_trigger_min) : '—');
  setText($('#s3Pts'), a != null && wo.est_trigger_min ? `+${fmtPts(2 * a * a * (wo.est_trigger_min / 60))}` : '—');
  const steps = Array.isArray(wo.steps) ? wo.steps : [];
  $('#s3Steps').hidden = !steps.length;
  setText($('#s3StepsLabel'), `Show the ${steps.length} repair steps${tool ? ` · planned tool: ${tool.name}` : ''}`);
  setHtml($('#s3StepList'), steps.map((x) => `<li>${esc(x)}</li>`).join(''));
  b.dataset.mode = 'start';
  b.dataset.tool = wo.tool_id || '';
  setText(b, 'Start the repair');
}

function renderTools() {
  const s = app.state;
  const sel = $('#toolSelect');
  const tools = [...(s.tools || [])];
  if (s.active_tool && !tools.find((t) => t.id === s.active_tool.id)) tools.push(s.active_tool);
  const html = ['<option value="">No tool selected</option>']
    .concat(tools.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}${t.vibration_m_s2 != null ? ` · ${esc(t.vibration_m_s2)} m/s²` : ''}</option>`))
    .join('');
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
  const cur = s.active_tool ? s.active_tool.id : '';
  if (document.activeElement !== sel) sel.value = cur;
  const t = s.active_tool;
  setText($('#toolSrc'), t ? `a = ${t.vibration_m_s2} m/s² · source: ${t.source || 'not recorded'}` : 'Select the tool you are holding. The band never guesses which tool it is.');
}

function renderActivity() {
  const a = app.activity;
  const st = a.state;
  const live = bandLive() && motionFresh();
  setBadge($('#actBadge'), live ? (ACT_BADGE[st] || 'secondary') : 'outline', live ? (ACT_LABEL[st] || st) : 'No motion data');
  spark.setColor(st === 'tool_active' ? '#F97316' : '#18181B');
  const f = a.features || {};
  const parts = [];
  if (f.acc_rms != null) parts.push(`acc rms ${Number(f.acc_rms).toFixed(3)} g`);
  if (f.gyro_rms != null) parts.push(`gyro rms ${Number(f.gyro_rms).toFixed(1)} °/s`);
  if (f.hf_ratio != null) parts.push(`hf ratio ${Number(f.hf_ratio).toFixed(2)}`);
  setText($('#actFeat'), parts.length && live
    ? `Detector: ${ACT_LABEL[st] || st}, ${Math.round((a.confidence || 0) * 100)}% · ${parts.join(' · ')}`
    : 'Detector features appear with motion data.');
}

function renderStep4() {
  const s = app.state;
  const sessions = (s.tool_sessions || []).filter((x) => x && x.status !== 'rejected');
  const active = sessions.find((x) => x.status === 'active');
  const last = active || latest(sessions);
  const tool = s.active_tool;
  let trig = null;
  if (active) trig = Math.max(0, nowS() - (Number(active.ts) || nowS()));
  else if (last) trig = Number(last.trigger_s) || 0;
  setText($('#s4TrigLabel'), active ? 'Trigger time, running' : 'Last trigger time');
  const trigEl = $('#s4Trig');
  if (trig == null) { setText(trigEl, '—'); setText(trigEl.nextElementSibling, 's'); }
  else if (trig >= 120) { setText(trigEl, (trig / 60).toFixed(1)); setText(trigEl.nextElementSibling, 'min'); }
  else { setText(trigEl, trig.toFixed(1)); setText(trigEl.nextElementSibling, 's'); }
  const conf = active ? app.activity.confidence : last ? last.confidence : null;
  setText($('#s4Conf'), conf != null && isFinite(conf) ? String(Math.round(conf * 100)) : '—');
  setText($('#s4Mag'), tool && tool.vibration_m_s2 != null ? String(tool.vibration_m_s2) : '—');

  const unresolved = latest(sessions.filter((x) => x.status === 'unresolved'));
  let html;
  if (unresolved) {
    html = `<button class="btn primary" type="button" data-action="confirm" data-id="${esc(unresolved.id)}" data-trigger="${esc(unresolved.trigger_s ?? '')}">Confirm ${fmtDur(unresolved.trigger_s)} of tool use</button>
      <button class="btn outline" type="button" data-action="reject" data-id="${esc(unresolved.id)}">Reject</button>`;
  } else if (!bandLive()) {
    html = '<button class="btn primary" type="button" data-action="connect-band">Connect band 07</button>';
  } else if (!tool) {
    html = '<button class="btn primary" type="button" data-action="focus-tool">Select the tool in hand</button>';
  } else {
    html = '<button class="btn primary" type="button" data-action="go-step" data-step="5">See today\'s exposure</button>';
  }
  setHtml($('#s4Action'), html);
  setText($('#s4Why'), unresolved
    ? `The band was not sure about this session (confidence ${Math.round((unresolved.confidence || 0) * 100)}%). Only confirmed trigger time counts toward exposure.`
    : active ? `Tool running for ${fmtDur(trig)}. Only trigger time counts toward exposure.`
      : 'Only trigger time counts toward exposure. When the band is unsure, you confirm the session.');
  renderActivity();
  updateEmpty();
}

function minutesLeft(target) {
  const s = app.state;
  const ex = s.exposure || {};
  const total = Number(ex.total_points) || 0;
  const key = target === 'action' ? 'minutes_to_action' : 'minutes_to_limit';
  if (ex[key] != null && isFinite(ex[key])) return Math.max(0, Number(ex[key]));
  const tool = s.active_tool;
  if (!tool || !tool.vibration_m_s2) return null;
  const perMin = (2 * tool.vibration_m_s2 ** 2) / 60;
  const goal = target === 'action' ? (ex.action || 100) : (ex.limit || 400);
  return Math.max(0, (goal - total) / perMin);
}

function renderExposure() {
  const s = app.state;
  const ex = s.exposure || {};
  const total = Number(ex.total_points) || 0;
  const action = ex.action || 100;
  const limit = ex.limit || 400;
  setText($('#expTotal'), total < 10 ? total.toFixed(2) : total.toFixed(1));
  const status = ex.status || (total >= limit ? 'above_limit' : total >= action ? 'above_action' : 'below_action');
  const [bcls, bword, fcls] = status === 'above_limit'
    ? ['destructive', 'Above limit value', 'alarm']
    : status === 'above_action'
      ? ['warning', 'Above action value', 'warn']
      : ['outline live', 'Below action value', ''];
  setBadge($('#expBadge'), bcls, bword);
  const fill = $('#expFill');
  fill.className = `fill ${fcls}${total > 0 ? ' nz' : ''}`;
  fill.style.width = `${clamp((total / limit) * 100, 0, 100)}%`;
  const rec = s.recommendation;
  const proj = rec && rec.projected_points != null ? Number(rec.projected_points) : null;
  $('#expProj').style.width = proj != null && proj > total ? `${clamp((proj / limit) * 100, 0, 100)}%` : '0';
  $('#expBar').title = proj != null ? `Today ${fmtPts(total)} pts · projected ${fmtPts(proj)} pts after the planned job` : `Today ${fmtPts(total)} pts`;
  $('#expBar').setAttribute('aria-valuenow', String(Math.round(total * 10) / 10));
  const trigS = (ex.records || []).reduce((acc, r) => acc + (Number(r.trigger_s ?? (r.trigger_hours != null ? r.trigger_hours * 3600 : 0)) || 0), 0);
  setText($('#s5Trig'), (trigS / 60).toFixed(1));
  const ta = minutesLeft('action'), tl = minutesLeft('limit');
  setText($('#s5ToAction'), ta == null ? '—' : String(Math.round(ta)));
  setText($('#s5ToLimit'), tl == null ? '—' : String(Math.round(tl)));
  const basis = toolById(ex.basis_tool_id) || s.active_tool;
  const a = ex.basis_vibration_m_s2 ?? (basis && basis.vibration_m_s2);
  setText($('#expSub'), basis && a != null
    ? `Minutes left assume the ${basis.name} at ${a} m/s². a comes from the tool profile and T from the band's trigger time; the band does not measure vibration at the hand.`
    : "a comes from the selected tool's profile and T from the band's trigger time. The band does not measure vibration at the hand.");
}

function renderStep6() {
  const s = app.state;
  const r = s.recommendation;
  const wo = latest(s.work_orders);
  if (!r) {
    setText($('#s6Title'), 'Next job');
    setText($('#s6Desc'), "Check the planned repair against today's exposure before it starts.");
    setText($('#s6Proj'), '—'); setText($('#s6Add'), '—');
    setText($('#s6Headline'), wo ? `Work order ${wo.id} has not been checked yet.` : 'No repair planned yet. Plan a job to project today\'s exposure.');
    setHtml($('#s6Actions'), '');
    $('#s6Review').hidden = true;
    setHtml($('#s6Action'), wo
      ? `<button class="btn primary" type="button" data-action="plan-wo" data-id="${esc(wo.id)}">Check exposure for ${esc(wo.id)}</button>`
      : '<button class="btn primary" type="button" data-action="open-plan">Plan a job</button>');
    return;
  }
  const st = stationById(r.station_id || (r.planned && r.planned.station_id));
  const tool = r.tool || toolById(r.planned && r.planned.tool_id);
  setText($('#s6Title'), `${r.task || (r.planned && r.planned.task) || 'Planned repair'}${st ? ` · station ${st.number}` : ''}`);
  setText($('#s6Desc'), `${tool && tool.name ? tool.name : 'Planned tool'}${r.est_trigger_min != null ? ` · ${r.est_trigger_min} min planned trigger time` : ''}`);
  setText($('#s6Proj'), fmtPts(r.projected_points));
  setText($('#s6Add'), `+${fmtPts(r.added_points)}`);
  setText($('#s6Headline'), r.headline || '');
  const acts = (Array.isArray(r.actions) ? r.actions : []).filter((x) => !/^supervisor review/i.test(String(x)));
  setHtml($('#s6Actions'), acts.map((x) => `<li>${esc(x)}</li>`).join(''));
  $('#s6Review').hidden = r.requires_supervisor_review === false;
  setHtml($('#s6Action'), '<a class="btn primary" href="supervisor.html">Open supervisor review</a>');
}

function renderRecords() {
  const s = app.state;
  const ex = s.exposure || {};
  const rows = [];
  const sessions = s.tool_sessions || [];
  for (const u of sessions.filter((x) => x && x.status === 'unresolved')) {
    const tl = toolById(u.tool_id);
    rows.push(`<div class="confirm-row"><div>Unresolved: ${esc(tl ? tl.name : 'no tool selected')}, <span class="num">${fmtDur(u.trigger_s)}</span> · confidence ${Math.round((u.confidence || 0) * 100)}%</div>
      <div class="row-btns"><button class="btn primary sm" type="button" data-action="confirm" data-id="${esc(u.id)}" data-trigger="${esc(u.trigger_s ?? '')}">Confirm</button>
      <button class="btn outline sm" type="button" data-action="reject" data-id="${esc(u.id)}">Reject</button></div></div>`);
  }
  const active = sessions.find((x) => x && x.status === 'active');
  if (active) {
    const tl = toolById(active.tool_id) || s.active_tool;
    rows.push(`<div class="exp-row live-row" data-live-session="${esc(active.id)}"><span class="name">Tool running · ${esc(tl ? tl.name : 'no tool selected')}</span>
      <span class="num" data-live-dur>0 s</span><span class="num" data-live-pts>+0.00 pts</span></div>`);
  }
  for (const r of [...(ex.records || [])].reverse()) {
    const tl = toolById(r.tool_id);
    const name = r.tool_name || (tl ? tl.name : r.tool_id || 'Tool');
    const trig = r.trigger_s ?? (r.trigger_hours != null ? r.trigger_hours * 3600 : r.duration_s);
    const tag = r.source === 'sim' ? '<span class="src">demo data</span>' : r.source === 'user' ? '<span class="src">entered</span>' : '';
    rows.push(`<div class="exp-row"><span class="name">${esc(name)}${tag}</span><span class="num">${fmtDur(trig)}</span><span class="num">${fmtPts(r.points)} pts</span></div>`);
  }
  if (!rows.length) rows.push('<p class="empty" style="padding:12px 0">No tool sessions recorded today. Sessions appear here when the band detects tool use.</p>');
  setHtml($('#expRows'), rows.join(''));
  updateLive();
}

function updateLive() {
  const s = app.state;
  const active = (s.tool_sessions || []).find((x) => x && x.status === 'active');
  if (active && app.step === 4) renderStep4();
  const row = document.querySelector('[data-live-session]');
  if (!row || !active) return;
  const tl = toolById(active.tool_id) || s.active_tool;
  const el = Math.max(0, nowS() - (Number(active.ts) || nowS()));
  const a = tl ? Number(tl.vibration_m_s2) || 0 : 0;
  row.querySelector('[data-live-dur]').textContent = fmtDur(el);
  row.querySelector('[data-live-pts]').textContent = `+${fmtPts(2 * a * a * (el / 3600))} pts`;
}

function updateEmpty() {
  const fresh = motionFresh();
  const msg = bandLive() ? 'Waiting for motion data from the band…' : 'No band connected. Connect band 07 to time tool use.';
  const se = $('#sparkEmpty');
  se.hidden = !!fresh;
  setText(se, msg);
  for (const el of $$('[data-empty]')) { el.hidden = !!fresh; setText(el, bandLive() ? 'Waiting for motion data…' : 'No band connected.'); }
}

function renderMic() {
  const on = !!(app.state.mic && app.state.mic.on);
  const b = $('#micBtn');
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', String(on));
  setText($('#micLabel'), on ? 'Mic on · listening' : 'Mic off');
}

function renderTranscript() {
  const t = app.transcript;
  const el = $('#transcriptText');
  if (!t.text) { el.textContent = 'Waiting for speech…'; el.className = 'tx-empty'; return; }
  el.textContent = t.text;
  el.className = t.final ? '' : 'partial';
}

function renderClarify() {
  const el = $('#clarify');
  if (!app.clarify) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = `Clarification needed: ${app.clarify.question}`;
}

function renderPlanSelects() {
  const st = $('#planStation');
  const sh = stationList().map((s) => `<option value="${esc(s.id)}">Station ${esc(s.number)} · ${esc(s.name)}</option>`).join('');
  if (st.dataset.html !== sh) {
    const v = st.value;
    st.innerHTML = sh;
    st.dataset.html = sh;
    st.value = v || app.state.active_station || 'S3';
  }
  const tl = $('#planTool');
  const th = (app.state.tools || []).map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  if (tl.dataset.html !== th) {
    const v = tl.value;
    tl.innerHTML = th;
    tl.dataset.html = th;
    if (v) tl.value = v;
  }
}

// ---------- details sheet ----------
function openSheet(open) {
  app.sheet = !!open;
  $('#sheet').hidden = !open;
  $('#sheetOverlay').hidden = !open;
  if (open) { renderRecords(); setDtab(app.dtab); }
}
function setDtab(name) {
  app.dtab = name;
  for (const t of $$('.sheet .tab')) t.setAttribute('aria-selected', String(t.dataset.tab === name));
  for (const p of $$('.dpanel')) p.hidden = p.id !== `dpanel-${name}`;
  if (name === 'timeline') renderTimeline();
  updateTabMeta();
}
function updateTabMeta() {
  let txt = '';
  if (app.dtab === 'signals') {
    const stale = app.lastMotion && performance.now() - app.lastMotion > 1500;
    txt = app.lastMotion ? `${app.rate} Hz · ${app.lost} lost${stale ? ' · no data' : ''}` : 'No motion data yet';
  } else if (app.dtab === 'timeline') txt = app.tlRange;
  else txt = 'Screening estimate';
  setText($('#tabMeta'), txt);
}

// ---------- timeline ----------
const LANE_IDS = LANES.map((l) => l.id);
function dotClass(e) {
  const k = String(e.kind || '');
  switch (e.lane) {
    case 'inspection': return /defect/.test(k) ? 'c-alarm' : /clarify/.test(k) ? 'c-warn' : /ok|inspected/.test(k) ? 'c-ok' : 'c-ink';
    case 'tool': return 'c-orange';
    case 'exposure': return /elv|limit/.test(k) ? 'c-alarm' : 'c-warn';
    case 'work_order': return 'c-ink2 sq';
    default: return 'c-ink3';
  }
}
function tickLabel(t, step) {
  const d = new Date(t * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return step < 60 ? `${hh}:${mm}:${ss}` : `${hh}:${mm}`;
}
function renderTimeline() {
  const now = nowS();
  const evs = [...app.events.values()];
  let start = app.firstTs;
  for (const e of evs) if (e.ts < start && e.ts > now - 6 * 3600) start = e.ts;
  start -= 5;
  const end = Math.max(start + 100, now + 10);
  if (end - start > 45 * 60) start = end - 45 * 60;
  const span = end - start;
  const X = (t) => ((t - start) / span) * 100;
  const steps = [10, 15, 30, 60, 120, 300, 600, 900];
  let step = 900;
  for (const s of steps) if (span / s <= 8) { step = s; break; }
  let ticks = '', grid = '';
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    const x = X(t);
    if (x < 3 || x > 97) continue;
    ticks += `<span class="tl-tick" style="left:${x.toFixed(2)}%">${tickLabel(t, step)}</span>`;
    grid += `<div class="tl-grid" style="left:${x.toFixed(2)}%"></div>`;
  }
  const lanes = Object.fromEntries(LANE_IDS.map((l) => [l, '']));
  const seg = (cls, a, b, title = '') => {
    const x0 = X(Math.max(a, start));
    const w = Math.max(0.3, X(b) - x0);
    return `<div class="tl-seg ${cls}" style="left:${x0.toFixed(2)}%;width:${w.toFixed(2)}%" title="${esc(title)}"></div>`;
  };
  for (const w of app.walkSegs) {
    const b = w.end ?? now;
    if (b >= start) lanes.walking += seg('walk', w.start, b, 'Walking (wrist activity)');
  }
  for (const ts of app.state.tool_sessions || []) {
    if (!ts || !ts.ts) continue;
    const l = ts.status === 'active' ? now - ts.ts
      : (ts.observed_s ?? ((ts.t_end != null && ts.t_start != null) ? ts.t_end - ts.t_start : ts.trigger_s || 0));
    const b = ts.ts + Math.max(0, l);
    if (b >= start) lanes.tool += seg(`tool ${ts.status === 'unresolved' ? 'unresolved' : ''}`, ts.ts, b, `Tool session · ${ts.status}`);
  }
  for (const e of evs) {
    if (!LANE_IDS.includes(e.lane) || e.ts < start) continue;
    lanes[e.lane] += `<button type="button" class="tl-dot ${dotClass(e)}" style="left:${X(e.ts).toFixed(2)}%" data-ev="${esc(e.id)}" aria-label="${esc(e.title)}"></button>`;
  }
  $('#timeline').innerHTML = `<div class="tl-labels">${LANES.map((l) => `<div>${l.label}</div>`).join('')}</div>
    <div class="tl-track">${grid}${LANE_IDS.map((l) => `<div class="tl-lane">${lanes[l]}</div>`).join('')}
    <div class="tl-axis">${ticks}</div><div class="tl-now" style="left:${X(now).toFixed(2)}%"></div></div>`;
  app.tlRange = `${fmtClock(start)} – ${fmtClock(now)}`;
  updateTabMeta();
}

function showPopover(ev, rect) {
  const pop = $('#popover');
  const st = stationById(ev.station_id);
  const src = { band: 'Band', user: 'Technician', ai: 'AI', rules: 'Rules', system: 'System', sim: 'Demo data' }[ev.source] || ev.source || '';
  pop.innerHTML = `<b>${esc(ev.title)}</b>${ev.detail ? `<div>${esc(ev.detail)}</div>` : ''}
    <span class="meta-line">${fmtClock(ev.ts)} · ${esc(src)}${st ? ` · station ${esc(st.number)}` : ''}</span>`;
  pop.hidden = false;
  const h = pop.offsetHeight;
  pop.style.left = `${clamp(rect.left + rect.width / 2 - 150, 8, window.innerWidth - 308)}px`;
  pop.style.top = `${Math.max(8, rect.top - h - 10)}px`;
}

// ---------- toasts ----------
function toast(title, detail, cls = '') {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${cls}`;
  el.innerHTML = `<b>${esc(title)}</b>${detail ? `<span>${esc(detail)}</span>` : ''}`;
  box.appendChild(el);
  while (box.children.length > 2) box.removeChild(box.firstChild);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 350); }, 4200);
}

// ---------- actions (REST, or the demo replay when no backend) ----------
async function api(path, body) {
  if (!app.live) return replay.running ? replay.handleApi(path, body || {}) : null;
  try {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(HTTP_BASE + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  } catch (err) {
    toast('Request failed', `${path}: ${err.message}`, 'alarm');
    return null;
  }
}

function selectStation(id) { if (id) api('/api/station', { station_id: id }); }

function connectBand() {
  const bl = window.BandLink;
  if (!bl || typeof bl.connect !== 'function') { toast('Band link is still loading', 'Try again in a moment.', 'warn'); return; }
  bl.connect().catch((err) => toast('Band 07 did not connect', (err && err.message) || String(err), 'warn'));
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-action]');
  if (!b) return;
  const id = b.dataset.id;
  switch (b.dataset.action) {
    case 'station': selectStation(id); break;
    case 'go-step': goStep(b.dataset.step); break;
    case 'step-prev': goStep(app.step - 1); break;
    case 'step-next': goStep(app.step + 1); break;
    case 'go-report': goStep(2); $('#reportInput').focus(); break;
    case 'start-repair': {
      if (b.dataset.mode === 'report') { goStep(2); $('#reportInput').focus(); break; }
      const tid = b.dataset.tool;
      if (tid && !(app.state.active_tool && app.state.active_tool.id === tid)) api('/api/tool', { tool_id: tid });
      goStep(4);
      break;
    }
    case 'focus-tool': $('#toolSelect').focus(); break;
    case 'connect-band': connectBand(); break;
    case 'open-plan': $('#planBox').open = true; $('#planTask').focus(); break;
    case 'open-sheet': openSheet(true); break;
    case 'close-sheet': openSheet(false); break;
    case 'dtab': setDtab(b.dataset.tab); break;
    case 'confirm': {
      const trig = Number(b.dataset.trigger);
      api(`/api/tool_session/${encodeURIComponent(id)}/confirm`, isFinite(trig) && trig > 0 ? { confirmed: true, trigger_s: trig } : { confirmed: true });
      break;
    }
    case 'reject': api(`/api/tool_session/${encodeURIComponent(id)}/confirm`, { confirmed: false }); break;
    case 'plan-wo': {
      const wo = (app.state.work_orders || []).find((w) => w.id === id);
      if (wo) {
        api('/api/repair/plan', {
          station_id: wo.station_id, task: String(wo.title || 'Repair').split(/\s+—\s+/)[0],
          tool_id: wo.tool_id || (app.state.active_tool && app.state.active_tool.id) || null,
          est_trigger_min: wo.est_trigger_min || 20,
        });
      }
      break;
    }
    default: break;
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { openSheet(false); $('#popover').hidden = true; }
  const tab = e.target.closest && e.target.closest('.sheet .tab');
  if (tab && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    const tabs = $$('.sheet .tab');
    const i = tabs.indexOf(tab);
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    setDtab(next.dataset.tab);
    next.focus();
  }
});

document.addEventListener('pointerdown', (e) => {
  const dot = e.target.closest('.tl-dot');
  const pop = $('#popover');
  if (dot) {
    const ev = app.events.get(dot.dataset.ev);
    if (ev) showPopover(ev, dot.getBoundingClientRect());
    return;
  }
  if (!e.target.closest('#popover')) pop.hidden = true;
});

on('#toolSelect', 'change', (e) => api('/api/tool', { tool_id: e.target.value || null }));
on('#micBtn', 'click', () => api('/api/mic', { on: !(app.state.mic && app.state.mic.on) }));
on('#reportForm', 'submit', (e) => {
  e.preventDefault();
  const input = $('#reportInput');
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  app.clarify = null;
  renderClarify();
  api('/api/report', { text });
  input.value = '';
});
on('#planForm', 'submit', (e) => {
  e.preventDefault();
  api('/api/repair/plan', {
    station_id: $('#planStation').value,
    task: $('#planTask').value.trim() || 'Planned repair',
    tool_id: $('#planTool').value || null,
    est_trigger_min: Number($('#planMin').value) || 20,
  });
});
window.addEventListener('bandlink:status', (e) => {
  app.bandlink = e.detail || null;
  renderStep4();
});

// ---------- connection ----------
const replay = new DemoReplay(handleMessage, { onReset: resetLocal });

function resetLocal() {
  app.events.clear();
  app.walkSegs = [];
  app.transcript = { text: '', final: true };
  app.clarify = null;
  app.firstTs = nowS();
  app.lastMotion = 0;
  accelChart.clear();
  gyroChart.clear();
  spark.clear();
  renderTranscript();
  renderClarify();
}

function startDemo() {
  if (app.demo) return;
  app.demo = true;
  app.gotSnapshot = false;
  replay.start();
  renderMode();
}

function stopDemo() {
  if (!app.demo) return;
  replay.stop();
  app.demo = false;
  app.gotSnapshot = false;
  app.state = emptyState();
  resetLocal();
}

let ws = null;
let wsTries = 0;
function connect() {
  if (FORCE_DEMO || STATIC_MODE) { startDemo(); return; }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  try {
    ws = new WebSocket(`${proto}://${WS_HOST || location.host}/ws`);
  } catch (err) {
    if (!app.everLive) startDemo();
    retry();
    return;
  }
  ws.onopen = () => {
    wsTries = 0;
    stopDemo();
    app.live = true;
    app.everLive = true;
    renderMode();
    renderAll();
  };
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    handleMessage(m);
  };
  ws.onclose = () => {
    ws = null;
    app.live = false;
    if (!app.everLive) startDemo();
    renderMode();
    retry();
  };
}
function retry() {
  wsTries += 1;
  setTimeout(connect, Math.min(10000, 1000 * 2 ** Math.min(wsTries, 3)));
}

// ---------- loops ----------
function frame() {
  requestAnimationFrame(frame);
  safe(function drawCharts() {
    if (app.step === 4) spark.draw();
    if (app.sheet && app.dtab === 'signals') { accelChart.draw(); gyroChart.draw(); }
  });
}

setInterval(() => safe(function tick100() {
  const a = app.lastA, g = app.lastG;
  if (app.sheet && app.dtab === 'signals') {
    if (a) { setText($('#ax'), fmtSigned(a[0], 2)); setText($('#ay'), fmtSigned(a[1], 2)); setText($('#az'), fmtSigned(a[2], 2)); }
    if (g) { setText($('#gx'), fmtSigned(g[0], 0)); setText($('#gy'), fmtSigned(g[1], 0)); setText($('#gz'), fmtSigned(g[2], 0)); }
  }
  const stale = app.lastMotion && performance.now() - app.lastMotion > 1500;
  setText($('#rateInfo'), app.lastMotion && bandLive() ? `${app.rate} Hz · ${app.lost} lost${stale ? ' · no data' : ''}` : 'No motion data yet');
  updateTabMeta();
}), 100);

setInterval(() => {
  if (app.sheet && app.dtab === 'timeline') safe(renderTimeline);
  safe(updateLive);
  safe(renderActivity);
  safe(updateEmpty);
}, 500);
setInterval(() => setText($('#clock'), new Date().toLocaleTimeString([], { hour12: false })), 1000);

connect();                      // the socket first, so a render problem can never leave the page on "Connecting…"
safe(renderAll);
safe(renderActivity);
safe(renderMode);
safe(showStep);
frame();
