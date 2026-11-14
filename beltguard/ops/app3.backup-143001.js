// BeltGuard dashboard v3 (14:03 product focus): tool checkout → live vibration monitoring → exposure verdict.
// Live mode: WebSocket /ws + REST /api/*. No backend: a clearly labelled demo replay.
// The band indicator in the header belongs to bandlink.js (S0); band pairing lives in the Band link tab (bridge.html).
import { StripChart, Sparkline } from './charts.js?v=7';
import { DemoReplay, DEMO_STATIONS } from './replay.js?v=7';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nowS = () => Date.now() / 1000;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtPts = (v) => { const n = Number(v); if (!isFinite(n)) return '—'; return Math.abs(n) < 10 ? n.toFixed(2) : n.toFixed(1); };
const fmtClock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const fmtHM = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
const fmtSigned = (v, d) => { const n = Number(v) || 0; return `${n < 0 ? '−' : '+'}${Math.abs(n).toFixed(d)}`; };
const fmtMS = (s) => { s = Math.max(0, Math.round(Number(s) || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const cap = (s) => { s = String(s || ''); return s ? s[0].toUpperCase() + s.slice(1) : s; };
const len = (o, k) => ((o && Array.isArray(o[k])) ? o[k].length : 0);
const latest = (arr) => (Array.isArray(arr) && arr.length ? arr[arr.length - 1] : null);
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
const WS_HOST = qs.get('ws');
const FORCE_DEMO = qs.has('demo');
const STATIC_MODE = /\/frontend\//.test(location.pathname) && !WS_HOST;
const HTTP_BASE = WS_HOST ? `${location.protocol}//${WS_HOST}` : '';

const STEPS = ['Unlock tool', 'Assigned', 'Using tool', 'Exposure verdict', 'Return tool'];
const ACT_LABEL = { idle: 'Idle', walking: 'Walking', gesture: 'Hand gesture', candidate: 'Possible tool use', tool_active: 'Tool running', unresolved: 'Unresolved' };
const ACT_BADGE = { tool_active: 'tool', candidate: 'outline wait', unresolved: 'warning' };
const LANES = [
  { id: 'walking', label: 'Walking' },
  { id: 'inspection', label: 'Inspection' },
  { id: 'tool', label: 'Tool use' },
  { id: 'exposure', label: 'Exposure' },
  { id: 'work_order', label: 'Work orders' },
];
const LEAD = {
  tool: 'Unlock a tool, and BeltGuard times its vibration from your wrist band and tells you when to stop.',
  inspection: 'Walk CV-102 station by station. Say what you see; BeltGuard validates it and drafts the work order.',
};

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

// The checkout is remembered per browser until S2's /api/tool/checkout reports it in the snapshot.
const CK_KEY = 'beltguard.checkout.v1';
function loadCk() { try { return JSON.parse(localStorage.getItem(CK_KEY) || 'null'); } catch { return null; } }
function saveCk(c) { try { if (c) localStorage.setItem(CK_KEY, JSON.stringify(c)); else localStorage.removeItem(CK_KEY); } catch { /* storage blocked */ } }

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
  mode: 'tool',
  step: 1,
  pick: null,
  ck: loadCk(),
  returned: null,
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
function toolById(id) {
  if (!id) return null;
  const t = (app.state.tools || []).find((x) => x.id === id);
  if (t) return t;
  return app.state.active_tool && app.state.active_tool.id === id ? app.state.active_tool : null;
}
const shortTool = (t) => String((t && t.name) || 'Tool').replace(/\s*\([^)]*\)\s*/g, ' ').trim();
const fmtMag = (v) => { const n = Number(v); return isFinite(n) ? n.toFixed(1) : '—'; };
function srcShort(t) {
  const s = String((t && t.source) || '');
  if (t && (t.id === 'demo_tool' || /demo/i.test(t.name || ''))) return 'Demo stand-in';
  if (/^HSE/i.test(s)) return 'HSE guidance';
  if (/conservative/i.test(s)) return 'Conservative estimate';
  if (/manufactur|declared/i.test(s)) return 'Manufacturer data';
  if (!s) return 'Source not recorded';
  return s.split(/[,:;(]/)[0].trim();
}
function workerLabel() {
  const w = app.state.worker || {};
  if (w.name) return w.id && !String(w.name).includes(w.id) ? `${w.name} (${w.id})` : w.name;
  return `Technician ${w.id || 'W-01'}`;
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
const totalPts = (s = app.state) => Number((s.verdict && s.verdict.total_points) ?? (s.exposure || {}).total_points) || 0;
const asgTool = () => (app.state.assignment && app.state.assignment.tool) || null;
const ckTool = () => asgTool() || (app.ck ? toolById(app.ck.tool_id) : null) || app.state.active_tool;
const aOf = (t) => (t ? Number(t.vibration_m_s2) || 0 : 0);
const livePts = (sec, t = ckTool()) => 2 * aOf(t) ** 2 * (sec / 3600);

function ckStats(ck = app.ck, s = app.state) {
  if (!ck) return { trig: 0, pts: 0, sessions: [], recs: [], active: null, live: 0 };
  const seen = new Set(ck.seen || []);
  const sessions = (s.tool_sessions || []).filter((x) => x && !seen.has(x.id) && x.status !== 'rejected');
  const ids = new Set(sessions.map((x) => x.id));
  const recs = ((s.exposure || {}).records || []).filter((r) => r && ids.has(r.tool_session_id));
  const active = sessions.find((x) => x.status === 'active') || null;
  const live = active ? Math.max(0, nowS() - (Number(active.ts) || nowS())) : 0;
  const trig = recs.reduce((acc, r) => acc + (Number(r.trigger_s) || 0), 0) + live;
  const pts = Math.max(0, totalPts(s) - (Number(ck.points0) || 0)) + (active ? livePts(live, toolById(ck.tool_id)) : 0);
  return { trig, pts, sessions, recs, active, live };
}
// Prefer the server's live numbers (S2 pushes a "verdict" message every second while a session runs).
function serverLive() {
  const a = app.state.assignment, v = app.state.verdict;
  if (!a || !v) return null;
  const live = a.active_session || null;
  return {
    trig: Number(a.trigger_s_including_live ?? a.trigger_s_today_with_this_tool) || 0,
    toolPts: (Number(a.points_with_this_tool) || 0) + (live && live.provisional_points != null ? Number(live.provisional_points) : 0),
    total: (Number(v.total_points) || 0) + (v.provisional_points != null ? Number(v.provisional_points) : 0),
    running: !!live,
  };
}
const liveTotal = () => { const sv = serverLive(); if (sv) return sv.total; const st = ckStats(); return totalPts() + (st.active ? livePts(st.live) : 0); };
function minutesLeft(goal, total = liveTotal()) {
  const t = ckTool();
  if (t && aOf(t) > 0) return Math.max(0, (goal - total) / ((2 * aOf(t) ** 2) / 60));
  const ex = app.state.exposure || {};
  const key = goal === (ex.action || 100) ? 'minutes_to_action' : 'minutes_to_limit';
  return ex[key] != null && isFinite(ex[key]) ? Math.max(0, Number(ex[key])) : null;
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
      safe(renderStepTool);
      break;
    case 'transcript':
      app.transcript = { text: m.text || '', final: m.final !== false, at: nowS() };
      safe(renderVoiceLog);
      break;
    case 'clarify':
      app.clarify = { question: m.question || 'Please clarify the report.' };
      safe(renderClarify);
      setMode('inspection');
      toast('Clarification needed', app.clarify.question, 'warn');
      break;
    case 'event': addEvent(m.event, false); break;
    case 'snapshot': applySnapshot(m.state); break;
    case 'verdict':
      if ('assignment' in m) app.state.assignment = m.assignment;
      if (m.verdict) app.state.verdict = m.verdict;
      safe(updateLive);
      break;
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
  safe(renderActivity);
  if (model) model.setActivity(st);
}

function syncCheckout(next, first) {
  const asg = next.assignment && next.assignment.tool ? next.assignment : null;   // S2: open tool assignment
  const tid = asg ? asg.tool.id : (next.active_tool ? next.active_tool.id : null);
  const srvTs = asg && asg.since_ts ? Number(asg.since_ts) : null;
  if (tid) {
    if (!app.ck || app.ck.tool_id !== tid) {
      app.ck = {
        tool_id: tid,
        ts: srvTs || nowS(),
        unknownStart: !!(first && !srvTs),
        points0: totalPts(next),
        seen: (next.tool_sessions || []).map((x) => x && x.id),
      };
      app.returned = null;
      saveCk(app.ck);
      return first ? null : 'assigned';
    }
    if (srvTs && srvTs !== app.ck.ts) { app.ck.ts = srvTs; app.ck.unknownStart = false; saveCk(app.ck); }
    return null;
  }
  if (app.ck) {
    const st = ckStats(app.ck, app.state);                        // the last snapshot that still had the tool
    const t = toolById(app.ck.tool_id);
    app.returned = { tool_id: app.ck.tool_id, name: shortTool(t), ts: app.ck.ts, unknownStart: app.ck.unknownStart, at: nowS(), trig: st.trig, pts: st.pts, sessions: st.sessions, recs: st.recs };
    app.ck = null;
    saveCk(null);
    return first ? null : 'returned';
  }
  return null;
}

function applySnapshot(s) {
  if (!s || typeof s !== 'object') return;
  const prev = app.state;
  const first = !app.gotSnapshot;
  const ckEvent = syncCheckout(s, first);
  const base = emptyState();
  app.state = {
    ...base,
    ...s,
    exposure: { ...base.exposure, ...(s.exposure || {}) },
    connection: { ...base.connection, ...(s.connection || {}) },
    mic: { ...base.mic, ...(s.mic || {}) },
  };
  if (len(app.state, 'defects') > len(prev, 'defects')) { app.clarify = null; safe(renderClarify); }
  if (Array.isArray(s.timeline)) for (const e of s.timeline) addEvent(e, true);
  if (s.connection && s.connection.rate) setRate(s.connection.rate);
  if (first) { app.gotSnapshot = true; app.step = initialStep(); showStep(); }
  else autoAdvance(prev, app.state, ckEvent);
  renderAll();
}

function initialStep() {
  if (!app.state.active_tool) return 1;
  const st = ckStats();
  if (st.active || st.sessions.some((x) => x.status === 'unresolved')) return 3;
  if (st.recs.length) return 4;
  return 2;
}

// Live events move the tool flow forward: checkout → tool running → exposure record → return.
function autoAdvance(prev, next, ckEvent) {
  let t = 0;
  if (ckEvent === 'assigned') t = 2;
  if (ckEvent === 'returned') t = 5;
  const seen = new Set((prev.tool_sessions || []).map((x) => x && x.id));
  const fresh = (next.tool_sessions || []).filter((x) => x && !seen.has(x.id));
  const nowActive = (next.tool_sessions || []).some((x) => x && x.status === 'active') && !(prev.tool_sessions || []).some((x) => x && x.status === 'active');
  if (nowActive || fresh.some((x) => x.status === 'active' || x.status === 'unresolved')) t = 3;
  if (len(next.exposure, 'records') > len(prev.exposure, 'records') && next.active_tool) t = 4;
  if (t) { setMode('tool'); goStep(t); }
}

function addEvent(e, quiet) {
  if (!e || typeof e !== 'object') return;
  const id = String(e.id ?? `${e.ts}-${e.kind}-${e.title}`);
  const isNew = !app.events.has(id);
  app.events.set(id, { ...e, id, ts: Number(e.ts) || nowS() });
  if (app.events.size > 600) app.events.delete(app.events.keys().next().value);
  if (isNew && e.kind === 'transcript') safe(renderVoiceLog);
  if (isNew && !quiet) maybeToast(e);
}

function maybeToast(e) {
  const k = String(e.kind || '');
  if (k === 'transcript') {                         // a spoken report while the tool flow is on screen: say what happened
    const u = parseUtterance(e);
    if (app.mode !== 'inspection' && u.voice && u.cls) toast(`Heard: “${u.words.slice(0, 80)}${u.words.length > 80 ? '…' : ''}”`, u.result, u.cls === 'ask' ? 'warn' : 'ok');
    return;
  }
  let cls = null;
  if (/elv|limit/.test(k)) cls = 'alarm';
  else if (/eav|action/.test(k)) cls = 'warn';
  else if (/defect/.test(k) && app.mode !== 'inspection') cls = 'alarm';
  if (cls) toast(e.title, e.detail, cls);
}

// ---------- flow ----------
function setMode(mode) {
  if (mode !== 'tool' && mode !== 'inspection') return;
  if (mode === app.mode) return;
  app.mode = mode;
  showStep();
  focusModel();
}

function goStep(n) {
  n = clamp(Number(n) || 1, 1, 5);
  if (n === app.step && app.mode === 'tool') return;
  app.step = n;
  app.mode = 'tool';
  showStep();
  focusModel();
}

function focusModel() {
  if (!model || typeof model.focusStation !== 'function') return;
  try { model.focusStation(app.state.active_station || null, app.mode === 'tool' ? app.step : 0); } catch (err) { /* the scene owns its camera */ }
}

function showStep() {
  const tool = app.mode === 'tool';
  for (let i = 1; i <= 5; i += 1) $(`#step-${i}`).hidden = !(tool && i === app.step);
  $('#insp').hidden = tool;
  $('#stepper').hidden = !tool;
  $('#stepNav').hidden = !tool;
  for (const t of $$('.mode-tabs .tab')) t.setAttribute('aria-selected', String(t.dataset.mode === app.mode));
  setText($('#flowLead'), LEAD[app.mode]);
  $('#stepPrev').disabled = app.step === 1;
  $('#stepNext').disabled = app.step === 5;
  safe(renderFoot);
  safe(renderStepper);
  safe(renderStepTool);
}

function renderFoot() {
  if (app.mode === 'tool') { setText($('#stepCount'), `Step ${app.step} of 5 · ${STEPS[app.step - 1]}`); return; }
  const n = stationList().filter((st) => stationStatus(st)[0]).length;
  setText($('#stepCount'), `${n} of 5 stations inspected · ${len(app.state, 'defects')} defects this shift`);
}

const CHECK = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 5 8.6l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function doneFlags() {
  const out = !!app.state.active_tool;
  const st = ckStats();
  const r = app.returned;
  return { 1: out || !!r, 2: out || !!r, 3: st.recs.length > 0 || !!(r && r.recs.length), 4: st.recs.length > 0 || !!r, 5: !!r && !out };
}
function renderStepper() {
  const m = doneFlags();
  const html = STEPS.map((label, i) => {
    const n = i + 1;
    const cur = n === app.step;
    const done = !cur && m[n];
    const cls = cur ? 'current' : done ? 'done' : '';
    const word = cur ? 'current step' : done ? 'done' : 'not started';
    const line = n < 5 ? `<span class="line${m[n] ? ' done' : ''}" aria-hidden="true"></span>` : '';
    return `<li><button type="button" class="step-btn ${cls}" data-action="go-step" data-step="${n}" aria-current="${cur ? 'step' : 'false'}" aria-label="Step ${n}, ${label}, ${word}">
      <span class="num">${done ? CHECK : n}</span><span>${label}</span></button>${line}</li>`;
  }).join('');
  setHtml($('#stepper'), html);
}

// ---------- rendering ----------
function renderAll() {
  [renderHeader, renderFoot, renderStepper, renderCrib, renderStepTool, renderStations, renderInspection,
    renderListening, renderVoiceLog, renderRecords].forEach(safe);
  if (model) safe(function applyModel() { model.apply(app.state); });
}

function renderHeader() {
  const conv = app.state.conveyor || {};
  setText($('#assetName'), conv.id || 'CV-102');
  $('#assetName').title = conv.name || '';
  setText($('#workerName'), workerLabel());
}

function renderMode() {
  const pill = $('#modePill');
  $('#demoBanner').hidden = !app.demo;
  if (app.demo || app.live) { pill.hidden = true; return; }
  pill.hidden = false;
  if (app.everLive) setBadge(pill, 'destructive', 'Backend offline · retrying');
  else setBadge(pill, 'outline wait', 'Connecting…');
}

function cribRank(t) {
  const n = `${t.id} ${t.name}`.toLowerCase();
  if (t.id === 'demo_tool' || n.includes('demo')) return 99;
  if (n.includes('drill') && !/hammer|impact/.test(n)) return 0;
  if (n.includes('drill')) return 1;
  if (/impact[ _-]?wrench/.test(n)) return 2;
  if (/angle[ _-]?grinder/.test(n)) return 3;
  if (/jack/.test(n)) return 4;
  if (/breaker|chipping/.test(n)) return 5;
  return 50;
}
// The crib shows the six tools the site issues (plus the demo stand-in); every other profile sits under "More tools".
const CRIB_IDS = ['drill', 'hammer_drill', 'impact_wrench', 'angle_grinder', 'power_jack', 'breaker_hammer', 'demo_tool'];
function cribTools() {
  const all = (app.state.tools || []).map((t, i) => ({ t, i }));
  let main = CRIB_IDS.map((id) => all.find((x) => x.t.id === id)).filter(Boolean);
  if (main.length < 4) main = [...all].sort((a, b) => cribRank(a.t) - cribRank(b.t) || a.i - b.i).slice(0, 7);
  const ids = new Set(main.map((x) => x.t.id));
  return { main: main.map((x) => x.t), more: all.filter((x) => !ids.has(x.t.id)).map((x) => x.t) };
}

function toolCard(t, outId, short = false) {
  const sel = app.pick === t.id;
  const name = short ? shortTool(t) : t.name;
  return `<button type="button" class="tool-card${sel ? ' sel' : ''}" role="radio" aria-checked="${sel}" data-action="pick-tool" data-id="${esc(t.id)}" title="${esc(`${t.name} · ${t.source || 'source not recorded'}`)}">
    <span class="tc-name">${esc(name)}</span>
    <span class="tc-meta"><b>${esc(fmtMag(t.vibration_m_s2))}</b> m/s² · ${esc(outId === t.id ? 'checked out' : srcShort(t))}</span></button>`;
}

function renderCrib() {
  const s = app.state;
  const { main, more } = cribTools();
  const outId = s.active_tool ? s.active_tool.id : null;
  const all = main.concat(more);
  if (!app.pick || !all.find((t) => t.id === app.pick)) app.pick = outId || null;
  const moreOpen = !!more.find((t) => t.id === app.pick || t.id === outId) || !!(app.moreOpen);
  const html = all.length
    ? `${main.map((t) => toolCard(t, outId, true)).join('')}${more.length ? `<details class="disclosure crib-more" data-more${moreOpen ? ' open' : ''}><summary>More tools (${more.length})</summary><div class="crib inner">${more.map((t) => toolCard(t, outId)).join('')}</div></details>` : ''}`
    : '<p class="empty">The tool crib is loading from the backend.</p>';
  setHtml($('#crib'), html);
  const ex = s.exposure || {};
  setText($('#s1Why'), `Today so far: ${fmtPts(totalPts())} points. Exposure action value ${ex.action || 100}, limit value ${ex.limit || 400}.`);
  const b = $('#unlockBtn');
  const pick = toolById(app.pick);
  b.disabled = !pick;
  setText(b, !pick ? 'Select a tool to unlock' : outId === pick.id ? `Continue with the ${shortTool(pick).toLowerCase()}` : `Unlock ${shortTool(pick).toLowerCase()}`);
}

function renderStepTool() {
  if (app.mode !== 'tool') return;
  [renderCrib, renderAssigned, renderUsing, renderVerdict, renderReturn].forEach(safe);
}

function bandAction(label) {
  return `<a class="btn primary" href="bridge.html" target="beltguard-bandlink" rel="noopener">${esc(label)}</a>`;
}

function renderAssigned() {
  const s = app.state;
  const t = s.active_tool;
  if (!t) {
    setText($('#s2Title'), 'No tool assigned yet');
    setText($('#s2Desc'), 'Unlock a tool from the crib first.');
    for (const id of ['#s2Mag', '#s2Rate', '#s2ToAction']) setText($(id), '—');
    setText($('#s2Why'), 'The tool is known from the checkout, never guessed from motion.');
    setHtml($('#s2Action'), '<button class="btn primary" type="button" data-action="go-step" data-step="1">Go to the tool crib</button>');
    return;
  }
  const ck = app.ck;
  setText($('#s2Title'), `${shortTool(t)} assigned to ${workerLabel()}${ck && !ck.unknownStart ? ` at ${fmtHM(ck.ts)}` : ''}`);
  setText($('#s2Desc'), 'BeltGuard now watches wrist vibration to time trigger use. The tool is known from this checkout, never guessed from motion.');
  const a = aOf(t);
  setText($('#s2Mag'), fmtMag(a));
  setText($('#s2Rate'), a ? String(Math.round(2 * a * a)) : '—');
  const ta = minutesLeft((s.exposure || {}).action || 100);
  setText($('#s2ToAction'), ta == null ? '—' : String(Math.round(ta)));
  setText($('#s2Why'), `Magnitude source: ${t.source || 'not recorded'}.`);
  setHtml($('#s2Action'), bandLive()
    ? '<button class="btn primary" type="button" data-action="go-step" data-step="3">Start using the tool</button><button class="btn outline" type="button" data-action="go-step" data-step="5">Return it</button>'
    : bandAction('Connect band 07 in the Band link tab'));
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

function renderUsing() {
  const s = app.state;
  const t = ckTool();
  setText($('#s3Title'), t && s.active_tool ? `Using the ${shortTool(t).toLowerCase()}` : 'No tool checked out');
  const st = ckStats();
  const sv = serverLive();
  const running = sv ? sv.running : !!st.active;
  setText($('#s3TrigLabel'), running ? 'Trigger time, running' : 'Trigger time with this tool');
  setText($('#s3Trig'), fmtMS(sv ? sv.trig : st.trig));
  setText($('#s3Pts'), `+${fmtPts(sv ? sv.toolPts : st.pts)}`);
  setText($('#s3Today'), fmtPts(liveTotal()));
  const unresolved = latest(st.sessions.filter((x) => x.status === 'unresolved'))
    || latest((s.tool_sessions || []).filter((x) => x && x.status === 'unresolved'));
  let html;
  if (unresolved) {
    html = `<button class="btn primary" type="button" data-action="confirm" data-id="${esc(unresolved.id)}" data-trigger="${esc(unresolved.trigger_s ?? '')}">Confirm ${fmtDur(unresolved.trigger_s)} of tool use</button>
      <button class="btn outline" type="button" data-action="reject" data-id="${esc(unresolved.id)}">Reject</button>`;
  } else if (!bandLive()) {
    html = bandAction('Connect band 07 in the Band link tab');
  } else if (!s.active_tool) {
    html = '<button class="btn primary" type="button" data-action="go-step" data-step="1">Unlock a tool first</button>';
  } else {
    html = '<button class="btn primary" type="button" data-action="go-step" data-step="4">See the exposure verdict</button>';
  }
  setHtml($('#s3Action'), html);
  setText($('#s3Why'), unresolved
    ? `The band was not sure about this session (confidence ${Math.round((unresolved.confidence || 0) * 100)}%). Only confirmed trigger time counts toward exposure.`
    : t ? `Points rise only while the band detects the tool running: 2 · a² · T, with a = ${fmtMag(aOf(t))} m/s² from the tool profile.`
      : 'Points rise only while the band detects the tool running: 2 · a² · T.');
  safe(renderActivity);
  safe(updateEmpty);
}

function verdict(total) {
  const ex = app.state.exposure || {};
  const sv = app.state.verdict || null;
  const action = (sv && sv.action) || ex.action || 100, limit = (sv && sv.limit) || ex.limit || 400;
  const level = sv && !serverLive()?.running && sv.state
    ? ({ within: 'ok', action: 'action', limit: 'limit' }[sv.state] || 'ok')
    : total >= limit ? 'limit' : total >= action ? 'action' : 'ok';
  const head = sv && sv.headline ? sv.headline : null;
  if (level === 'limit') return { level, title: 'Limit reached: stop using vibrating tools today', sub: head || `Today's estimate is ${fmtPts(total)} points, at or over the ${limit}-point exposure limit value.` };
  if (level === 'action') return { level, title: 'Action value reached: take a break or rotate', sub: head || `Today's estimate is ${fmtPts(total)} points, over the ${action}-point exposure action value.` };
  return { level, title: 'Within the safe limit', sub: head || `Below the ${action}-point exposure action value.` };
}

function renderVerdict() {
  const ex = app.state.exposure || {};
  const total = liveTotal();
  const limit = ex.limit || 400;
  const v = verdict(total);
  const box = $('#verdict');
  if (box.dataset.level !== v.level) box.dataset.level = v.level;
  setText($('#vTitle'), v.title);
  setText($('#vSub'), v.sub);
  setText($('#expTotal'), total < 10 ? total.toFixed(2) : total.toFixed(1));
  const fill = $('#expFill');
  const fcls = v.level === 'limit' ? 'alarm' : v.level === 'action' ? 'warn' : '';
  fill.className = `fill ${fcls}${total > 0 ? ' nz' : ''}`;
  fill.style.width = `${clamp((total / limit) * 100, 0, 100)}%`;
  $('#expBar').setAttribute('aria-valuenow', String(Math.round(total * 10) / 10));
  const ta = minutesLeft(ex.action || 100, total), tl = minutesLeft(limit, total);
  setText($('#vToAction'), ta == null ? '—' : String(Math.round(ta)));
  setText($('#vToLimit'), tl == null ? '—' : String(Math.round(tl)));
  const t = ckTool();
  setText($('#vWhy'), t
    ? `Minutes left assume the ${shortTool(t).toLowerCase()} at ${fmtMag(aOf(t))} m/s². The band times trigger use; it does not measure vibration at the hand.`
    : "Minutes left use the backend's basis tool. The band times trigger use; it does not measure vibration at the hand.");
}

function sessionRows(sessions, recs) {
  const byId = new Map((recs || []).map((r) => [r.tool_session_id, r]));
  return sessions.slice(-6).reverse().map((x) => {
    const r = byId.get(x.id);
    const sec = x.status === 'active' ? Math.max(0, nowS() - (Number(x.ts) || nowS())) : Number((r && r.trigger_s) ?? x.trigger_s) || 0;
    const pts = r ? r.points : livePts(sec);
    const word = x.status === 'active' ? 'running' : x.status === 'unresolved' ? 'to confirm' : '';
    return `<li><span class="mono">${fmtHM(Number(x.ts) || nowS())}</span><span>${fmtDur(sec)}${word ? ` · ${word}` : ''}</span><span class="num">+${fmtPts(pts)} pts</span></li>`;
  }).join('');
}

function renderReturn() {
  const s = app.state;
  if (s.active_tool && app.ck) {
    const t = ckTool();
    const st = ckStats();
    setText($('#s5Title'), `Return the ${shortTool(t).toLowerCase()}`);
    setText($('#s5Desc'), app.ck.unknownStart ? 'Session summary for this checkout.' : `Checked out at ${fmtHM(app.ck.ts)}, ${fmtDur(nowS() - app.ck.ts)} ago.`);
    setText($('#s5Trig'), (st.trig / 60).toFixed(1));
    setText($('#s5Pts'), `+${fmtPts(st.pts)}`);
    setText($('#s5Today'), fmtPts(liveTotal()));
    setHtml($('#s5Sessions'), st.sessions.length ? sessionRows(st.sessions, st.recs) : '<li><span class="muted">No tool use detected during this checkout.</span></li>');
    setText($('#s5Why'), "Returning the tool closes the checkout. Today's points stay on the technician's record.");
    setHtml($('#s5Action'), `<button class="btn primary" type="button" data-action="return-tool">Return the ${esc(shortTool(t).toLowerCase())}</button>`);
    return;
  }
  const r = app.returned;
  if (r) {
    setText($('#s5Title'), `${r.name} returned at ${fmtHM(r.at)}`);
    setText($('#s5Desc'), r.unknownStart ? 'Checkout closed.' : `Checked out at ${fmtHM(r.ts)}, returned ${fmtDur(r.at - r.ts)} later.`);
    setText($('#s5Trig'), (r.trig / 60).toFixed(1));
    setText($('#s5Pts'), `+${fmtPts(r.pts)}`);
    setText($('#s5Today'), fmtPts(totalPts()));
    setHtml($('#s5Sessions'), r.sessions.length ? sessionRows(r.sessions, r.recs) : '<li><span class="muted">No tool use was detected during this checkout.</span></li>');
    setText($('#s5Why'), "The checkout is closed. Today's points stay on the technician's record.");
    setHtml($('#s5Action'), '<button class="btn primary" type="button" data-action="go-step" data-step="1">Unlock another tool</button>');
    return;
  }
  setText($('#s5Title'), 'No tool checked out');
  setText($('#s5Desc'), 'Unlock a tool from the crib to start a checkout.');
  for (const id of ['#s5Trig', '#s5Pts']) setText($(id), '—');
  setText($('#s5Today'), fmtPts(totalPts()));
  setHtml($('#s5Sessions'), '');
  setText($('#s5Why'), "Today's points stay on the technician's record.");
  setHtml($('#s5Action'), '<button class="btn primary" type="button" data-action="go-step" data-step="1">Go to the tool crib</button>');
}

// ---------- inspection (secondary tab) ----------
function stationStatus(st) {
  const s = app.state;
  const defect = (s.defects || []).some((d) => d && d.station_id === st.id) || st.status === 'defect';
  if (defect) return ['defect', 'Defect reported'];
  if (st.status === 'inspected') return ['inspected', 'Inspected, no issues'];
  if (st.status === 'active') return ['', 'Inspection in progress'];
  return ['', 'Not inspected'];
}

function renderStations() {
  const s = app.state;
  const rows = stationList().map((st) => {
    const [cls, word] = stationStatus(st);
    const sel = s.active_station === st.id;
    return `<li><button type="button" class="st-row ${cls}${sel ? ' sel' : ''}" data-action="station" data-id="${esc(st.id)}" aria-pressed="${sel}">
      <span class="st-num" aria-hidden="true">${esc(st.number)}</span>
      <span class="st-text"><span class="st-name">${esc(st.name)}</span><span class="st-status"><i class="sdot ${cls}" aria-hidden="true"></i>${word}</span></span>
      ${sel ? '<span class="badge outline here">You are here</span>' : ''}</button></li>`;
  }).join('');
  setHtml($('#stationList'), rows);
}

const STATUS_WORD = { draft: 'Draft', planned: 'Planned', in_progress: 'In progress', awaiting_verification: 'Awaiting verification' };
// One card for the newest result: the work order and the defect it came from.
function renderInspection() {
  const s = app.state;
  const wo = latest(s.work_orders);
  const d = wo ? ((s.defects || []).find((x) => x && x.id === wo.defect_id) || latest(s.defects)) : latest(s.defects);
  if (!wo && !d) {
    setHtml($('#inspWo'), '<div class="sum-head"><span class="sum-label">Latest work order</span></div><p class="sum-meta">No defects reported this shift. Each validated report becomes a work-order draft here.</p>');
    return;
  }
  const sev = d ? String(d.severity || '').toLowerCase() : '';
  const sevCls = sev === 'critical' || sev === 'high' ? 'destructive' : sev === 'medium' ? 'warning' : 'secondary';
  const dst = d ? stationById(d.station_id) : null;
  const defectLine = d ? `${cap(d.component || 'Component')} — ${String(d.condition || '').toLowerCase()} · ${cap(sev) || 'Unrated'} severity · ${dst ? `station ${dst.number}, ${dst.name}` : d.station_id}` : '';
  if (!wo) {
    setHtml($('#inspWo'), `<div class="sum-head"><span class="sum-label">Latest defect</span><span class="badge ${sevCls}">${esc(cap(sev) || 'Unrated')} severity</span></div>
      <div class="sum-title">${esc(cap(d.component || 'Component'))} — ${esc(String(d.condition || '').toLowerCase())}</div>
      <div class="sum-meta">${esc(dst ? `Station ${dst.number}, ${dst.name}` : d.station_id)} · ${fmtClock(d.ts || nowS())}</div>`);
    return;
  }
  const tool = toolById(wo.tool_id);
  const steps = Array.isArray(wo.steps) ? wo.steps : [];
  const rec = s.recommendation && s.recommendation.work_order_id === wo.id ? s.recommendation : null;
  const [title] = String(wo.title || 'Repair').split(/\s+—\s+/);
  setHtml($('#inspWo'), `<div class="sum-head"><span class="sum-label">Latest work order · ${esc(wo.id)} · ${esc(STATUS_WORD[wo.status] || cap(wo.status || 'draft'))}</span>${wo.priority ? `<span class="badge outline">${esc(wo.priority)}</span>` : ''}</div>
    <div class="sum-title">${esc(title)}</div>
    ${defectLine ? `<div class="sum-meta">${esc(defectLine)}</div>` : ''}
    <div class="sum-meta">${esc(tool ? tool.name : 'Tool to be chosen')}${wo.est_trigger_min != null ? ` · ${esc(wo.est_trigger_min)} min trigger time` : ''} · isolation must be verified by an authorized person</div>
    ${rec ? `<p class="sum-rec">Projected ${fmtPts(rec.projected_points)} points today after this job (+${fmtPts(rec.added_points)}). Supervisor review required.</p>` : ''}
    <div class="row-btns"><button class="btn outline sm" type="button" data-action="plan-wo" data-id="${esc(wo.id)}">${rec ? 'Re-check exposure' : 'Check exposure for this job'}</button></div>
    ${steps.length ? `<details class="disclosure"><summary>Show the ${steps.length} repair steps</summary><ol class="steps">${steps.map((x) => `<li>${esc(x)}</li>`).join('')}</ol></details>` : ''}`);
}

// ---------- always-on band mic: listening status + the latest utterances with what BeltGuard did ----------
function bandConnected() {
  const c = app.state.connection || {};
  const bl = app.bandlink || (window.BandLink && window.BandLink.status);
  return (c.state === 'connected' && !c.simulated) || !!(bl && bl.state === 'connected');
}
const isVoice = (e) => e.source === 'band' || e.source === 'band-mic' || /^Heard/.test(String(e.title || ''));
function renderListening() {
  const mic = app.state.mic || {};
  const reported = mic.via != null || mic.since_ts != null;                         // set once the Band link page reports its mic
  const connected = bandConnected();
  const recentVoice = [...app.events.values()].some((e) => e.kind === 'transcript' && isVoice(e) && nowS() - e.ts < 180);
  const listening = mic.on === true || recentVoice || (!reported && connected);
  const el = $('#listen');
  el.classList.toggle('on', listening);
  setHtml($('#listenText'), listening
    ? 'Listening on band 07 <span class="hint-inline">· speak naturally, every sentence is checked</span>'
    : connected ? 'Band 07 connected · mic is off in the Band link tab' : 'Band 07 not connected · type the report below');
}
function parseUtterance(e) {
  const t = String(e.title || '');
  const m = t.match(/^(Heard|Typed):\s*[“"](.*)[”"]\s*$/);
  const words = m ? m[2] : t;
  const voice = m ? m[1] === 'Heard' : e.source === 'band' || e.source === 'band-mic';
  const result = String(e.detail || '');
  const cls = /^defect/i.test(result) ? 'ok' : /^question back/i.test(result) ? 'ask' : '';
  return { words, voice, result, cls, ts: e.ts };
}
// The mic is always on, so most of what it hears is conversation. Show only what BeltGuard acted on (and anything typed);
// other sentences are counted, never shown word for word.
function renderVoiceLog() {
  const all = [...app.events.values()].filter((e) => e.kind === 'transcript').sort((a, b) => b.ts - a.ts).map(parseUtterance);
  const shown = all.filter((u) => !u.voice || u.cls).slice(0, 2);
  const quiet = all.filter((u) => u.voice && !u.cls).length;
  const tr = app.transcript;
  const pending = tr && tr.text && tr.final && tr.at && nowS() - tr.at < 30 && !all.some((u) => u.ts >= tr.at - 5);
  const rows = [];
  if (pending) rows.push('<li><div class="vl-result pending" style="padding-left:0">Checking the last sentence…</div></li>');
  for (const u of shown) {
    rows.push(`<li><div class="vl-text"><span class="vl-time">${fmtHM(u.ts)}</span><span class="vl-words" title="${esc(u.words)}">${u.voice ? '' : 'Typed: '}“${esc(u.words)}”</span></div>
      <div class="vl-result ${u.cls}">${esc(u.result || 'Recorded')}</div></li>`);
  }
  if (quiet) rows.push(`<li class="vl-empty">${quiet} other ${quiet === 1 ? 'sentence' : 'sentences'} heard this shift, not defect reports.</li>`);
  if (!rows.length) rows.push('<li class="vl-empty">Nothing reported yet this shift. Say the station, the part and what is wrong.</li>');
  setHtml($('#voiceLog'), rows.join(''));
}

function renderClarify() {
  const el = $('#clarify');
  if (!app.clarify) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = `Clarification needed: ${app.clarify.question}`;
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
}

function updateLive() {
  const s = app.state;
  const active = (s.tool_sessions || []).find((x) => x && x.status === 'active');
  if (app.mode === 'tool' && (active || app.step === 5)) {
    if (app.step === 3) safe(renderUsing);
    if (app.step === 4) safe(renderVerdict);
    if (app.step === 5) safe(renderReturn);
  }
  const row = document.querySelector('[data-live-session]');
  if (!row || !active) return;
  const tl = toolById(active.tool_id) || s.active_tool;
  const el = Math.max(0, nowS() - (Number(active.ts) || nowS()));
  row.querySelector('[data-live-dur]').textContent = fmtDur(el);
  row.querySelector('[data-live-pts]').textContent = `+${fmtPts(2 * aOf(tl) ** 2 * (el / 3600))} pts`;
}

function updateEmpty() {
  const fresh = motionFresh();
  const msg = bandLive() ? 'Waiting for motion data from the band…' : 'No band connected. Open the Band link tab to connect band 07.';
  const se = $('#sparkEmpty');
  se.hidden = !!fresh;
  setText(se, msg);
  for (const el of $$('[data-empty]')) { el.hidden = !!fresh; setText(el, bandLive() ? 'Waiting for motion data…' : 'No band connected.'); }
}

// ---------- details sheet ----------
function openSheet(open) {
  app.sheet = !!open;
  $('#sheet').hidden = !open;
  $('#sheetOverlay').hidden = !open;
  if (open) { safe(renderRecords); setDtab(app.dtab); }
}
function setDtab(name) {
  app.dtab = name;
  for (const t of $$('.sheet .tab')) t.setAttribute('aria-selected', String(t.dataset.tab === name));
  for (const p of $$('.dpanel')) p.hidden = p.id !== `dpanel-${name}`;
  if (name === 'timeline') safe(renderTimeline);
  safe(updateTabMeta);
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
  safe(updateTabMeta);
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
  if (!box) return;
  const el = document.createElement('div');
  el.className = `toast ${cls}`;
  el.innerHTML = `<b>${esc(title)}</b>${detail ? `<span>${esc(detail)}</span>` : ''}`;
  box.appendChild(el);
  while (box.children.length > 2) box.removeChild(box.firstChild);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 350); }, 4200);
}

// ---------- actions (REST, or the demo replay when no backend) ----------
async function post(path, body, { optional = false } = {}) {
  if (!app.live) return replay.running ? replay.handleApi(path, body || {}) : null;
  try {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(HTTP_BASE + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.detail || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return data;
  } catch (err) {
    if (optional && (err.status === 404 || err.status === 405)) return { missing: true };
    toast('Request failed', `${path}: ${err.message}`, 'alarm');
    return null;
  }
}
const api = (path, body) => post(path, body);

async function unlockTool(tid) {
  if (!tid) return;
  if (!app.live) { if (replay.running) replay.handleApi('/api/tool', { tool_id: tid }); return; }
  const w = (app.state.worker || {}).id || 'W-01';
  const r = await post('/api/tool/checkout', { tool_id: tid, worker_id: w }, { optional: true });
  if (r && r.missing) await post('/api/tool', { tool_id: tid });
}

async function returnTool() {
  const tid = app.ck ? app.ck.tool_id : (app.state.active_tool && app.state.active_tool.id);
  if (!app.live) { if (replay.running) replay.handleApi('/api/tool', { tool_id: null }); return; }
  const w = (app.state.worker || {}).id || 'W-01';
  const r = await post('/api/tool/return', { tool_id: tid, worker_id: w }, { optional: true });
  if (r && r.missing) await post('/api/tool', { tool_id: null });
}

function selectStation(id) { if (id) api('/api/station', { station_id: id }); }

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-action]');
  if (!b) return;
  const id = b.dataset.id;
  switch (b.dataset.action) {
    case 'mode': setMode(b.dataset.mode); break;
    case 'go-step': goStep(b.dataset.step); break;
    case 'step-prev': goStep(app.step - 1); break;
    case 'step-next': goStep(app.step + 1); break;
    case 'pick-tool': app.pick = id; app.moreOpen = !!b.closest('[data-more]'); safe(renderCrib); break;
    case 'unlock': {
      const pick = app.pick;
      if (!pick) break;
      if (app.state.active_tool && app.state.active_tool.id === pick) goStep(2);
      else unlockTool(pick);
      break;
    }
    case 'return-tool': returnTool(); break;
    case 'station': selectStation(id); break;
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
  const tab = e.target.closest && e.target.closest('.tab');
  if (tab && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    const tabs = [...tab.parentElement.querySelectorAll('.tab')];
    const i = tabs.indexOf(tab);
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    next.click();
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

on('#reportForm', 'submit', (e) => {
  e.preventDefault();
  const input = $('#reportInput');
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  app.clarify = null;
  safe(renderClarify);
  api('/api/report', { text });
  input.value = '';
});
window.addEventListener('bandlink:status', (e) => {
  app.bandlink = e.detail || null;
  safe(renderStepTool);
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
  safe(renderVoiceLog);
  safe(renderClarify);
}

function startDemo() {
  if (app.demo) return;
  app.demo = true;
  app.gotSnapshot = false;
  replay.start();
  safe(renderMode);
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
    safe(renderMode);
    renderAll();
  };
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    safe(function onWsMessage() { handleMessage(m); });
  };
  ws.onclose = () => {
    ws = null;
    app.live = false;
    if (!app.everLive) startDemo();
    safe(renderMode);
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
    if (app.mode === 'tool' && app.step === 3) spark.draw();
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
