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

const STEPS = ['Pick tool', 'Locked to you', 'Using tool', 'Exposure verdict', 'Return tool'];
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
// No checkout memory in the browser: the server's snapshot.assignment is the only truth (S0 14:40). Clear old keys.
function loadCk() { try { localStorage.removeItem(CK_KEY); } catch { /* storage blocked */ } return null; }
function saveCk() { /* intentionally not persisted */ }

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
  inflight: 0,        // user-initiated requests in flight
  holdUntil: 0,       // no auto-advance before this time (ms), so the user sees the result of their own click
  pendingStep: null,
  started: false,     // "Start" pressed (or a tool already out): the crib is showing
  walking: false,     // the 3D technician is walking to the crib
  picking: null,      // tool id being checked out from the crib
  locking: false,     // lock sequence running (pickTool animation, lock card, walk to the station)
  lockSeq: 0,
  sheet: false,
  dtab: 'signals',
  tlRange: '',
  bandlink: (window.BandLink && window.BandLink.status) || null,
};

// ---------- charts and 3D ----------
const SIG = ['#18181B', '#3B82F6', '#A1A1AA'];
const accelChart = new StripChart(canvasOr('#accelChart'), { colors: SIG, minRange: 1.2 });
const gyroChart = new StripChart(canvasOr('#gyroChart'), { colors: SIG, minRange: 30 });
// Smooth live wrist chart (S0): every raw sample, drawn each animation frame over a 6 s window that glides on band time.
// Source: the Band link tab's BroadcastChannel 'beltguard-motion' (≈42 samples / 100 ms at 416 Hz); the /ws motion
// batches are the fallback when nothing has come over the channel for 2 s (e.g. the simulated band on :8799).
class SmoothLine {
  constructor(canvas, { seconds = 6, minSpan = 0.15 } = {}) {
    this.c = canvas;
    this.g = canvas.getContext('2d');
    this.seconds = seconds;
    this.minSpan = minSpan;
    this.cap = 8192;                                   // ≈19 s at 416 Hz
    this.t = new Float64Array(this.cap);
    this.v = new Float32Array(this.cap);
    this.range = minSpan;
    this.src = null;
    this.clear();
  }
  setRate() { /* the window is time-based */ }
  setColor() { /* one calm line (zinc-900) */ }
  clear() { this.head = 0; this.n = 0; this.lastT = 0; this.off = null; this.offSrc = null; this.gap = 0.1; this.lastArrive = 0; this.play = null; this.lastFrame = 0; }
  add(tSec, ax, ay, az) {
    if (!isFinite(tSec)) return;
    if (this.n && tSec <= this.lastT) {                  // overlap (WS ↔ channel hand-over): drop; a band clock reset: start over
      if (tSec < this.lastT - 10) this.clear(); else return false;
    }
    const i = this.head;
    this.t[i] = tSec;
    this.v[i] = Math.hypot(ax, ay, az) - 1;             // accel magnitude minus 1 g
    this.head = (i + 1) % this.cap;
    if (this.n < this.cap) this.n += 1;
    this.lastT = tSec;
    return true;
  }
  // Playout buffer. Batches can come 10×/s or, when Chrome throttles the Band link tab, as ~1 s chunks, so the
  // chart never draws "now": it plays the band's own timeline about one arrival gap behind the newest sample
  // (≈1.1 s with 1 s chunks, ≈0.12 s at 10×/s), advanced by the wall clock every frame.
  //   target = page clock − offset − delay. Offset = page minus band time, taken from the earliest-arriving batch
  //   (late batches can't drag it), re-learnt when the source changes, drifting up slowly.
  //   The shown time only ever moves forward at wall speed: inside ±0.3 s of target it free-runs (±3 % trim);
  //   beyond that it re-syncs softly (0.7× to 2× speed). It never jumps while visible and stops 150 ms past the
  //   newest sample when data stops.
  arrived() {
    if (!this.n) return;
    const p = performance.now() / 1000;
    if (this.lastArrive) this.gap = Math.max(Math.min(p - this.lastArrive, 2), this.gap * 0.985);
    this.lastArrive = p;
    const off = p - this.lastT;
    if (this.off == null || this.offSrc !== this.src || off < this.off || off - this.off > 2) { this.off = off; this.offSrc = this.src; }
    else this.off += (off - this.off) * 0.01;
  }
  nowT() {
    if (this.off == null) return this.lastT;
    const p = performance.now() / 1000;
    const delay = Math.min(1.5, Math.max(0.12, this.gap * 1.1));
    const target = p - this.off - delay;
    const dt = p - this.lastFrame;
    if (this.play == null || dt > 1 || Math.abs(target - this.play) > 6) this.play = target;   // chart wasn't on screen (or the band clock leapt): re-sync unseen
    else if (dt > 0.1) this.play += dt;                                                         // a short main-thread stall: catch up at wall speed
    else {
      const err = target - this.play;
      const rate = Math.abs(err) <= 0.3 ? 1 + Math.max(-0.03, Math.min(0.03, err * 0.1)) : 1 + Math.max(-0.3, Math.min(1, err * 0.5));
      this.play += dt * rate;
    }
    this.play = Math.min(this.play, this.lastT + 0.15);
    this.lastFrame = p;
    return this.play;
  }
  pushBatch(samples) {
    if (!samples.length) return;
    this.src = 'bc';
    // band time comes in ms (kit library); accept seconds too if the spacing says so
    const k = samples.length;
    const dt = k > 1 ? Math.abs(Number(samples[k - 1].t) - Number(samples[0].t)) / (k - 1) : 2.4;
    const scale = dt > 0.5 ? 1 / 1000 : 1;
    let kept = 0;
    for (const x of samples) if (x && this.add(Number(x.t) * scale, +x.ax || 0, +x.ay || 0, +x.az || 0)) kept += 1;
    if (kept) this.arrived();
  }
  pushWS(m) {
    const A = Array.isArray(m.accel) ? m.accel : [];
    if (!A.length) return;
    this.src = 'ws';
    const dt = Number(m.dt) || 1 / (Number(m.rate) || 50);
    const t0 = m.t0 != null && isFinite(Number(m.t0)) ? Number(m.t0) : (this.n ? this.lastT + dt : performance.now() / 1000);
    let kept = 0;
    A.forEach((a, j) => { if (a && this.add(t0 + j * dt, +a[0] || 0, +a[1] || 0, +a[2] || 0)) kept += 1; });
    if (kept) this.arrived();
  }
  draw() {
    const c = this.c;
    if (!c.offsetParent || !c.clientWidth || !c.clientHeight) return;
    const d = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.max(1, Math.round(c.clientWidth * d)), H = Math.max(1, Math.round(c.clientHeight * d));
    if (c.width !== W) c.width = W;
    if (c.height !== H) c.height = H;
    const g = this.g;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, W, H);
    const mid = Math.round(H / 2) + 0.5;
    g.strokeStyle = '#E4E4E7';
    g.lineWidth = Math.max(1, Math.round(d));
    g.beginPath(); g.moveTo(0, mid); g.lineTo(W, mid); g.stroke();
    if (!this.n) return;
    const now = this.nowT();
    const t0 = now - this.seconds;
    // newest → oldest until the window's left edge: the visible samples are a contiguous tail of the ring
    let first = this.n, m = 0;
    for (let j = this.n - 1; j >= 0; j -= 1) {
      const i = (this.head - this.n + j + this.cap) % this.cap;
      if (this.t[i] < t0 - 0.05) break;
      first = j;
      const a = Math.abs(this.v[i]);
      if (a > m) m = a;
    }
    if (first >= this.n) return;
    const target = Math.max(this.minSpan, m * 1.15);
    this.range += (target - this.range) * (target > this.range ? 0.25 : 0.04);
    const amp = H / 2 - 3 * d;
    const sx = W / this.seconds;
    g.strokeStyle = '#18181B';
    g.lineWidth = 1.5 * d;
    g.lineJoin = 'round';
    g.lineCap = 'round';
    g.beginPath();
    for (let j = first; j < this.n; j += 1) {
      const i = (this.head - this.n + j + this.cap) % this.cap;
      const x = (this.t[i] - t0) * sx;
      const y = mid - (Math.max(-this.range, Math.min(this.range, this.v[i])) / this.range) * amp;
      if (j === first) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.stroke();
  }
}
const spark = new SmoothLine(canvasOr('#spark'));
spark.c.__smooth = spark;                            // debug hook (like the 3D's stage.__bg3d)
app.bcLast = 0;
try {
  if (typeof BroadcastChannel !== 'undefined') {
    const motionCh = new BroadcastChannel('beltguard-motion');
    motionCh.onmessage = (ev) => safe(function onBandBatch() {
      const d = ev.data;
      const samples = Array.isArray(d) ? d : (d && (d.samples || d.batch)) || [];
      if (!samples.length) return;
      spark.pushBatch(samples);
      app.bcLast = performance.now();               // exposure gating stays on the backend's /ws motion
    });
  }
} catch { /* no channel: the /ws batches drive the chart */ }

let model = null;
import(`./model3d.js?v=${Date.now()}`)          // always fetch the newest scene (S7 owns model3d.js)
  .then((m) => {
    model = m.createModel($('#stage'), { onStationClick: selectStation, onToolClick: (id) => pickTool(id) });
    model.apply(modelState());
    model.setActivity(app.activity.state);
    focusModel();
    applyConveyor();
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
// Sim mode (S0 switched the backend to the simulated band for testing): use its data, labelled "Simulated band · testing".
const simMode = () => { const c = app.state.connection || {}; return c.simulated === true || c.state === 'simulated'; };
const isSimMsg = (m) => app.live && !simMode() && (m.source === 'sim' || (app.state.connection || {}).state === 'simulated');
function bandLive() {
  if (app.demo) return true;                                 // demo replay values are labelled in the top bar
  if (simMode()) return true;                                // simulated band: testable, labelled with the sim chip
  const bl = app.bandlink || (window.BandLink && window.BandLink.status);
  if (bl && bl.state === 'connected') return true;
  const c = app.state.connection || {};
  return c.state === 'connected' && !c.simulated;
}
const motionFresh = () => app.lastMotion && performance.now() - app.lastMotion < 2500;
// Confirmed points only (backend records). Running time is shown separately as provisional.
const totalPts = (s = app.state) => Number((s.verdict && s.verdict.total_points) ?? (s.exposure || {}).total_points) || 0;
// "Checked out" comes ONLY from the server's snapshot.assignment; active_tool alone never counts.
// (The offline demo replay has no checkout endpoint, so there the replay's active tool stands in.)
const outTool = (s = app.state) => {
  if (s && s.assignment && s.assignment.tool) return s.assignment.tool;
  return app.demo && s ? s.active_tool || null : null;
};
const asgTool = () => (app.state.assignment && app.state.assignment.tool) || null;
const ckTool = () => outTool() || (app.ck ? toolById(app.ck.tool_id) : null);
const aOf = (t) => (t ? Number(t.vibration_m_s2) || 0 : 0);
const livePts = (sec, t = ckTool()) => 2 * aOf(t) ** 2 * (sec / 3600);

// Live trigger time is counted only while the band reports tool_active with fresh motion, and freezes otherwise,
// so a session the backend still calls "active" can never add time on its own.
const gate = { sid: null, sec: 0, last: 0, on: false };
function gateTick() {
  const t = performance.now();
  const active = (app.state.tool_sessions || []).find((x) => x && x.status === 'active');
  const sid = active ? active.id : null;
  if (sid !== gate.sid) { gate.sid = sid; gate.sec = 0; }
  const was = gate.on;
  gate.on = !!sid && app.activity.state === 'tool_active' && !!app.lastMotion && t - app.lastMotion < 1500 && bandLive();
  if (gate.on && gate.last) gate.sec += Math.min(1, (t - gate.last) / 1000);
  gate.last = t;
  if (was !== gate.on && model) safe(function applyModelGate() { model.apply(modelState()); });
  if (!was && gate.on && outTool() && app.step !== 3) {               // the band sees the checked-out tool running
    if (app.inflight > 0 || Date.now() < app.holdUntil) app.pendingStep = 3;
    else { setMode('tool'); goStep(3); }
  }
}
// Live time counts only while ITS session is still active: once the server confirms it, the confirmed numbers take over
// at once (no moment where the same seconds are counted twice).
const liveNow = () => {
  const act = !!gate.sid && (app.state.tool_sessions || []).some((x) => x && x.id === gate.sid && x.status === 'active');
  return { sec: act ? gate.sec : 0, pts: act ? livePts(gate.sec) : 0, running: act && gate.on };
};
// The 3D shows "tool running" only when the band does.
function modelState() {
  const s = app.state;
  if (gate.on) return s;
  return { ...s, tool_sessions: (s.tool_sessions || []).map((x) => (x && x.status === 'active' ? { ...x, status: 'paused' } : x)) };
}

// The current checkout, all from the server: its sessions since since_ts, and today's confirmed numbers with this tool.
function ckStats(s = app.state) {
  const t = outTool(s);
  if (!t) return { trig: 0, pts: 0, sessions: [], recs: [], active: null };
  const a = s.assignment && s.assignment.tool ? s.assignment : null;
  const since = Number((a && a.since_ts) || (app.ck && app.ck.ts) || 0);
  const sessions = (s.tool_sessions || []).filter((x) => x && x.status !== 'rejected'
    && (Number(x.ts) || 0) >= since - 2 && (!x.tool_id || x.tool_id === t.id));
  const ids = new Set(sessions.map((x) => x.id));
  const recs = ((s.exposure || {}).records || []).filter((r) => r && ids.has(r.tool_session_id));
  const active = sessions.find((x) => x.status === 'active') || null;
  const trig = a ? Number(a.trigger_s_today_with_this_tool) || 0 : recs.reduce((acc, r) => acc + (Number(r.trigger_s) || 0), 0);
  const pts = a ? Number(a.points_with_this_tool) || 0 : recs.reduce((acc, r) => acc + (Number(r.points) || 0), 0);
  return { trig, pts, sessions, recs, active };
}
const realSession = (x, rec) => x && (x.status === 'active' || (Number((rec && rec.trigger_s) ?? x.trigger_s) || 0) >= 0.5);
// Confirmed numbers for the checked-out tool.
function confirmedTool() {
  const st = ckStats();
  return { trig: st.trig, pts: st.pts };
}
const liveTotal = () => totalPts() + liveNow().pts;           // only for "minutes left": conservative
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
      app.transcript = { text: m.text || '', final: m.final !== false, at: nowS(), id: m.transcript_id || null };
      safe(() => techFromTranscript(m));
      safe(renderVoiceLog);
      safe(renderLive);
      break;
    case 'agent_action': safe(() => onAgentAction(m)); safe(renderLive); break;
    case 'assistant': onAssistant(m); break;
    case 'clarify': {
      // The mic is always on: show the question in the conversation strip; only jump to Inspection when no tool is out.
      app.clarify = { question: m.question || 'Please clarify the report.' };
      safe(renderClarify);
      const key = m.transcript_id || `c:${app.clarify.question}`;
      const cur = convo.get(key) || { ts: nowS() };
      if (!cur.you && app.transcript && app.transcript.text) cur.you = app.transcript.text;
      convo.set(key, { ...cur, bg: app.clarify.question, fromAssistant: true });
      safe(renderConvo);
      if (!outTool()) setMode('inspection');
      break;
    }
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

// ---------- conversation strip + spoken replies ----------
// Lines: "You" = what the band mic heard (only when BeltGuard answered it), "BeltGuard" = the assistant's reply.
const convo = new Map();            // key (transcript id) -> { ts, you, bg, fromAssistant }
// S4's wake word went live at 14:42: nothing heard before it is shown (it was overheard chat), nor anything S2 marks ignored.
const CONVO_SINCE = (() => { const d = new Date(); d.setHours(14, 42, 0, 0); return d.getTime() / 1000; })();
const isIgnored = (e) => /no action\s*$/i.test(String(e.detail || '')) || !!(e.refs && e.refs.ignored);
function convoFromEvent(e) {
  if ((Number(e.ts) || 0) < CONVO_SINCE || isIgnored(e)) return;
  const u = parseUtterance(e);
  const intent = e.refs && e.refs.intent;
  if (u.voice && !u.cls && !intent) return;                         // conversation BeltGuard did not act on: never shown
  const key = (e.refs && e.refs.transcript_id) || e.id;
  const cur = convo.get(key) || {};
  const said = String(u.result || '').replace(/^question back:\s*/i, '');
  convo.set(key, { ts: cur.ts || e.ts, you: u.words, bg: cur.fromAssistant ? cur.bg : said, fromAssistant: !!cur.fromAssistant, voice: u.voice });
}
function onAssistant(m) {
  const text = noApproval(String(m.text || '').trim());
  if (!text) return;
  const tid = m.refs && m.refs.transcript_id;
  const key = tid || `a:${text}:${Math.round((Number(m.ts) || nowS()) / 5)}`;
  const cur = convo.get(key) || { ts: Number(m.ts) || nowS() };
  if (!cur.you && tid && app.transcript && app.transcript.id === tid) cur.you = app.transcript.text;
  convo.set(key, { ...cur, bg: text, fromAssistant: true, intent: m.intent });
  if (m.intent === 'safety_check') showSafety({ title: text, ts: Number(m.ts) || nowS(), id: key });
  safe(renderConvo);
  speak(text, key);
}
function renderConvo() {
  const lines = [];
  for (const c of [...convo.values()].sort((a, b) => a.ts - b.ts)) {
    if (c.you) lines.push({ who: 'You', cls: 'you', text: `“${c.you}”` });
    if (c.bg) lines.push({ who: 'BeltGuard', cls: 'bg', text: c.bg });
  }
  const last = lines.slice(-3);
  const box = $('#convo');
  box.hidden = !last.length;
  setHtml($('#convoList'), last.map((l) => `<li class="${l.cls}"><span class="who ${l.cls}">${l.who}</span><span class="line" title="${esc(l.text)}">${esc(l.text)}</span></li>`).join(''));
}

// ---------- LIVE panel: what the technician says · the job checklist · what BeltGuard did (S0, top priority) ----------
const tech = new Map();          // every final utterance: key → { key, ts, text, voice, result, intent, ignored, pending }
const agentLive = new Map();     // agent actions pushed on /ws (merged with snapshot.agent_actions)
const techKey = (id, text, ts) => (id ? `t:${id}` : `x:${String(text || '').slice(0, 50)}:${Math.round((Number(ts) || 0) / 20)}`);
function techFromTranscript(m) {
  if (m.final === false || !m.text) return;
  const ts = Number(m.ts) || nowS();
  if (ts < CONVO_SINCE) return;
  const key = techKey(m.transcript_id, m.text, ts);
  const cur = tech.get(key) || {};
  const voice = m.input ? m.input === 'voice' : !(m.source === 'user');
  const done = m.pending === false || (m.acted != null && m.acted !== '');
  tech.set(key, {
    ...cur, key, ts: cur.ts || ts, text: m.text, voice, acted: m.acted ? m.acted : cur.acted, reply: m.reply || cur.reply,
    pending: done ? false : !cur.result && !cur.acted, ignored: done && !m.acted && !cur.result ? true : cur.ignored,
  });
}
function techFromEvent(e) {
  if ((Number(e.ts) || 0) < CONVO_SINCE) return;
  const u = parseUtterance(e);
  const key = techKey(e.refs && e.refs.transcript_id, u.words, e.ts);
  const cur = tech.get(key) || {};
  tech.set(key, { ...cur, key, ts: cur.ts || e.ts, text: cur.text || u.words, voice: u.voice, result: String(e.detail || ''), intent: e.refs && e.refs.intent, ignored: isIgnored(e), pending: false });
}
// History from /api/transcripts (every utterance this shift with what BeltGuard did), so a reload shows the whole conversation.
function techFromRow(row) {
  const ts = Number(row.ts) || 0;
  if (ts < Math.max(CONVO_SINCE, Number(app.state.shift_start) || 0)) return;
  const key = `t:${row.id}`;
  const cur = tech.get(key) || {};
  const result = row.work_order_id ? `${row.defect_id ? `Defect ${row.defect_id} logged · ` : ''}${row.work_order_id} drafted`
    : row.result_status === 'clarify' ? `Question back: ${row.question || row.response || ''}` : (row.response || '');
  const intent = row.intent && row.intent !== 'none' && row.intent !== 'chat' ? row.intent : undefined;
  tech.set(key, {
    ...cur, key, ts: cur.ts || ts, text: row.text || cur.text || '', voice: row.input ? row.input === 'voice' : cur.voice !== false,
    result: cur.result || result, intent: cur.intent || intent, ignored: row.result_status === 'ignored' && !row.acted, pending: row.result_status === 'pending',
    acted: row.acted || cur.acted, reply: row.response || cur.reply,
  });
}
async function loadTranscripts() {
  if (replay.running) return;
  try {
    const r = await fetch(`${HTTP_BASE}/api/transcripts?limit=80`, { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    for (const row of (Array.isArray(d.rows) ? d.rows : [])) safe(() => techFromRow(row));
    safe(renderLive);
  } catch { /* the socket keeps the column current */ }
}
function agentActions() {
  const byId = new Map();
  for (const a of (Array.isArray(app.state.agent_actions) ? app.state.agent_actions : [])) if (a) byId.set(String(a.id ?? `${a.ts}-${a.kind}-${a.title}`), a);
  for (const [id, a] of agentLive) byId.set(id, { ...(byId.get(id) || {}), ...a });
  return [...byId.values()].sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0));
}
function onAgentAction(m) {
  const a = m.action && typeof m.action === 'object' ? m.action : m;
  const id = String(a.id ?? `${a.ts}-${a.kind}-${a.title}`);
  const known = agentLive.has(id) || (Array.isArray(app.state.agent_actions) && app.state.agent_actions.some((x) => x && String(x.id ?? `${x.ts}-${x.kind}-${x.title}`) === id));
  if (!known) fxMark(`a:${id}`, 'new');
  agentLive.set(id, { ...a, ts: Number(a.ts) || Number(m.ts) || nowS() });
  if (agentLive.size > 200) agentLive.delete(agentLive.keys().next().value);
}
const KIND_LABEL = {
  isolation: 'Isolation logged', job_done: 'Job done', signoff: 'Job done', missing_evidence: 'Missing evidence',
  work_order: 'Work order drafted', parts_request: 'Parts request drafted', supervisor_notified: 'Supervisor notified',
  rotation: 'Rotation suggested', verification: 'Done',
};
function agentLabel(a) {
  if (a.title) { const t = noApproval(a.title); if (t) return t; }
  const k = String(a.kind || '').toLowerCase();
  const t = String(a.text || '');
  if (k === 'exposure') return /notified/i.test(t) ? 'Supervisor notified' : /review|action value/i.test(t) ? 'Action value reached' : 'Exposure check';
  if (k === 'work_order') { const m = t.match(/WO-\d+/); return m ? `${m[0]} drafted` : KIND_LABEL.work_order; }
  if (k === 'parts_request') { const m = t.match(/PR-\d+/); return m ? `${m[0]} parts request` : KIND_LABEL.parts_request; }
  return KIND_LABEL[k] || cap(k.replace(/_/g, ' ')) || 'Action';
}
function agentKind(a) {
  const exact = { work_order: 'wo', parts_request: 'parts', isolation: 'job', job_done: 'verify', signoff: 'verify', missing_evidence: 'other', exposure: 'notify' }[String(a.kind || '').toLowerCase()];
  if (exact) return exact;
  const k = `${a.kind || a.type || ''} ${a.title || ''}`.toLowerCase();
  if (/part/.test(k)) return 'parts';
  if (/supervisor|notif/.test(k)) return 'notify';
  if (/rotat|break/.test(k)) return 'rotation';
  if (/verif/.test(k)) return 'verify';
  if (/job|step|tick|isolat/.test(k)) return 'job';
  if (/\bwo\b|wo_|work.?order/.test(k)) return 'wo';
  return 'other';
}
const AG_ICON = {
  wo: '<path d="M5 2.5h6M4 3.5h-.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H12M5.5 8h5M5.5 11h3.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  parts: '<path d="M8 1.8 13.5 4.6v6.8L8 14.2 2.5 11.4V4.6L8 1.8ZM2.8 4.8 8 7.5l5.2-2.7M8 7.5v6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  notify: '<path d="M4 11V7.2a4 4 0 0 1 8 0V11l1.2 1.4H2.8L4 11ZM6.6 14a1.5 1.5 0 0 0 2.8 0" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  rotation: '<path d="M13 6.5A5.2 5.2 0 0 0 3.3 5M3 9.5a5.2 5.2 0 0 0 9.7 1.5M3.2 2.5V5.2H6M12.8 13.5v-2.7H10" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  verify: '<circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="m5.6 8.1 1.7 1.7 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  job: '<path d="M3 4.5h1.5M3 8h1.5M3 11.5h1.5M7 4.5h6M7 8h6M7 11.5h6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  other: '<path d="M8 2.5v3M8 10.5v3M2.5 8h3M10.5 8h3M4.2 4.2l2 2M9.8 9.8l2 2M11.8 4.2l-2 2M6.2 9.8l-2 2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
};
function agentStatus(a) {
  const s = String(a.status || '').toLowerCase();
  if (/review|approv|needs|pending|awaiting|verif/.test(s)) return ['outline live', 'done'];   // no approvals: leftovers read as done
  if (/done|complete|sent|confirmed|ok\b/.test(s)) return ['outline live', 'done'];
  if (/draft/.test(s)) return ['secondary', 'draft'];
  return s ? ['secondary', s.replace(/_/g, ' ')] : null;
}
function actionShort(a) {
  if (a.chip || a.short) return a.chip || a.short;
  if (a.kind && (KIND_LABEL[String(a.kind).toLowerCase()] || String(a.kind).toLowerCase() === 'exposure')) return agentLabel(a);
  const k = agentKind(a);
  const wo = (a.refs && a.refs.work_order_id) || (String(a.title || '').match(/WO-\d+/) || [])[0];
  if (k === 'job') return `ticked ${a.step_label || a.step || a.title || 'a step'}`;
  if (k === 'wo') return `${wo || 'WO'} drafted`;
  if (k === 'parts') return 'parts request drafted';
  if (k === 'notify') return 'supervisor notified';
  if (k === 'rotation') return 'rotation suggested';
  if (k === 'verify') return 'done';
  return String(a.title || a.kind || 'done').toLowerCase();
}
const INTENT_CHIP = {
  station: 'station set', set_station: 'station set', checkout: 'tool locked', tool_checkout: 'tool locked', checkout_tool: 'tool locked',
  return: 'tool returned', return_tool: 'tool returned', tool_return: 'tool returned', exposure_question: 'answered', exposure: 'answered',
  ask_exposure: 'answered', confirm_session: 'session confirmed', confirm: 'session confirmed', job_done: 'job marked done',
  isolation: 'isolation logged', isolate: 'isolation logged', isolation_done: 'isolation logged', break: 'break logged', take_break: 'break logged',
};
function intentChip(intent) {
  const k = String(intent || '').toLowerCase();
  if (INTENT_CHIP[k]) return INTENT_CHIP[k];
  if (/station/.test(k)) return 'station set';
  if (/exposure|how_long|minutes|points/.test(k)) return 'answered';
  if (/checkout|unlock|take_tool/.test(k)) return 'tool locked';
  if (/return/.test(k)) return 'tool returned';
  if (/confirm/.test(k)) return 'session confirmed';
  if (/isolat/.test(k)) return 'isolation logged';
  if (/break|rest/.test(k)) return 'break logged';
  if (/job|step|done/.test(k)) return 'job updated';
  if (/defect|report/.test(k)) return 'defect logged';
  return k.replace(/_/g, ' ');
}
const shortStep = (label) => {
  const s0 = String(label || '').split(/\s+&\s+|\s+—\s+|\s+CV-/)[0].trim();
  const w = s0.split(/\s+/);
  return w.length > 3 ? w.slice(0, 3).join(' ') : s0;
};
function actedChips(acted, reply) {
  const out = [];
  const add = (cls, t) => { if (t && !out.some((c) => c[1] === t)) out.push([cls, t]); };
  for (const raw of String(acted || '').split(/\s+·\s+/)) {
    const part = raw.trim();
    if (!part) continue;
    let m = part.match(/^Step:\s*(.+)$/i);
    if (m) { add('', /sign-?off/i.test(m[1]) ? '✓ Job done' : `✓ ${shortStep(m[1])}`); continue; }
    if ((m = part.match(/\b(WO-\d+)\b/)) && /draft/i.test(part)) { add('', `→ ${m[1]} drafted`); continue; }
    if ((m = part.match(/\b(PR-\d+)\b/))) { add('', `→ ${m[1]} parts request`); continue; }
    if (/^Asked:/i.test(part)) { add('ask', '→ asked back'); continue; }
    if (/isolation logged/i.test(part)) { if (!out.some((c) => /Isolat/i.test(c[1]))) add('', '✓ Isolation logged'); continue; }
    if (/awaiting supervisor verification|marked complete/i.test(part)) { add('', '✓ Job done'); continue; }
    if (/supervisor notified/i.test(part)) { add('', '→ Supervisor notified'); continue; }
    if (reply && String(reply).slice(0, 90) === part) continue;
    if (part.length <= 28) add('', `→ ${part}`);
  }
  return out.slice(0, 3);
}
function techChips(l) {
  const out = [];
  const act = agentActions().find((a) => a && a.refs && a.refs.transcript_id && `t:${a.refs.transcript_id}` === l.key);
  if (act) out.push(['', `→ ${actionShort(act)}`]);
  for (const c of actedChips(l.acted, l.reply)) if (!out.some((x) => x[1] === c[1])) out.push(c);
  if (!out.length) {
    const r = l.result || '';
    const wo = r.match(/\b(WO-\d+)\s+drafted/i);
    if (wo) out.push(['', `→ ${wo[1]} drafted`]);
    else if (/^question back/i.test(r)) out.push(['ask', '→ asked back']);
    else if (l.intent && !l.ignored) out.push(['', `→ ${intentChip(l.intent)}`]);
    else if (l.pending) out.push(['wait', 'checking…']);
  }
  return out.slice(0, 3);
}
function renderTech() {
  const list = $('#lpTechList');
  const rows = [...tech.values()].sort((a, b) => a.ts - b.ts).slice(-40);
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 24;
  const html = rows.length ? rows.map((l) => {
    const chips = techChips(l);
    const chat = !chips.length && !l.pending;
    return `<li class="tx-line${chat ? ' chat' : ''}"><span class="tm">${fmtHM(l.ts)}</span><span class="tx-words">${l.voice ? '' : 'Typed: '}“${esc(l.text)}”</span>${chips.map((c) => `<span class="chip ${c[0]}">${esc(c[1])}</span>`).join('')}</li>`;
  }).join('') : '<li class="lp-empty">Every sentence the technician says appears here, with what BeltGuard did about it.</li>';
  const interim = document.getElementById('bgInterim');   // bandlink.js: live words while the technician speaks
  setHtml(list, html);
  if (interim && list.lastElementChild !== interim) list.appendChild(interim);
  if (atBottom) list.scrollTop = list.scrollHeight;
  const mic = app.state.mic || {};
  const listening = mic.on === true || (bandConnected() && !(mic.via != null || mic.since_ts != null)) || simMode();
  $('#lpLiveDot').classList.toggle('on', listening);
  setText($('#lpTechMeta'), listening ? `Listening on band 07${simMode() ? ' · simulated' : ''}` : 'Band 07 not connected');
}
function jobOf(s = app.state) { return s.job || (Array.isArray(s.jobs) ? latest(s.jobs) : null); }
function stepStatus(st) {
  const v = String(st.status || st.state || '').toLowerCase();
  if (st.done === true || /^(done|complete|completed|ticked|ok|verified)$/.test(v)) return 'done';
  if (/progress|active|doing|current|started/.test(v)) return 'progress';
  return 'left';
}
function evidenceText(st) {
  const e = st.evidence ?? st.proof ?? st.heard;
  if (!e) return '';
  if (typeof e === 'string') return e;
  if (Array.isArray(e)) return e.map((x) => (typeof x === 'string' ? x : evidenceText({ evidence: x }))).filter(Boolean).join(' · ');
  const when = e.ts ? ` ${fmtHM(Number(e.ts))}` : '';
  const what = e.text || e.detail || e.summary || '';
  const src = e.label || e.kind || e.source || '';
  const head = /speech|heard|voice|band-mic|transcript/i.test(src) ? `heard${when}` : src ? `${src}${when}` : when.trim();
  return [head, what && (/heard/.test(head) ? `“${what}”` : what)].filter(Boolean).join(': ');
}
const CHECK_SVG = '<svg width="9" height="9" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 5 8.6l4.5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
// ---------- live steps (S4 extracts the job's steps from speech: the list starts empty and grows) ----------
// A step that appears slides in with a soft green glow (1.5 s); a step ticked done flashes green (0.9 s); a new
// "BeltGuard did" row glows the same way. Effects are keyed by row and carry a negative --fx-delay, so a re-render
// mid-effect continues it instead of restarting it. Nothing animates for what the first snapshot already had.
const fxRows = new Map();                                  // 'j:<step>' | 'a:<action id>' → { kind, at }
const jobSeen = new Map();                                 // step key → 'done' | 'progress' | 'left'
let jobPrimed = false;
let agentsPrimed = false;
const stepTitle = (st) => String(st.label || st.title || st.text || st.name || '');
const NO_APPROVAL_RE = /\s*(?:[:;,\u00b7\u2014-]\s*)?(?:supervisor review required|needs (?:supervisor )?review|confirm or reject|awaiting (?:supervisor )?(?:verification|sign-?off|approval)|(?:isolation )?must be verified by an authori[sz]ed person|an authori[sz]ed person (?:must )?verif(?:y|ies)(?: (?:it|the isolation|isolation))?)\.?/gi;
function noApproval(t) { return String(t || '').replace(NO_APPROVAL_RE, '').replace(/^\s*[\u00b7:;,\u2014-]\s*/, '').replace(/\s{2,}/g, ' ').trim(); }
const isApprovalStep = (st) => String(st.id || '').toLowerCase() === 'signoff' || /sign-?off|supervisor (?:verification|approval)/i.test(stepTitle(st));
const jobSteps = (job) => (job && Array.isArray(job.steps) ? job.steps : []).filter((st) => st && !isApprovalStep(st));
const stepKey = (st, i) => String(st.id ?? st.key ?? st.step_id ?? (stepTitle(st) || `#${i}`)).toLowerCase();
const agentId = (a) => String(a.id ?? `${a.ts}-${a.kind}-${a.title}`);
function fxMark(key, kind) {
  const cur = fxRows.get(key);
  const at = performance.now();
  fxRows.set(key, cur && cur.kind.includes('new') && at - cur.at < 1500 ? { kind: `${cur.kind} ${kind}`, at: cur.at } : { kind, at });
  if (fxRows.size > 120) fxRows.delete(fxRows.keys().next().value);
}
function fxFor(key) {
  const f = fxRows.get(key);
  if (!f) return null;
  const ms = performance.now() - f.at;
  if (ms > (f.kind.includes('new') ? 1500 : 900)) { fxRows.delete(key); return null; }
  return { cls: [...new Set(f.kind.split(' '))].map((x) => ` fx-${x}`).join(''), style: ` style="--fx-delay:-${Math.round(ms)}ms"` };
}
// The conveyor in the 3D follows the spoken isolation steps (S7 v19 setConveyorRunning; the last call wins).
// Tool in hand → S7 stops the belt itself; this adds the job's lock-out on top and keeps it through a tool return.
const RE_RESTART = /remove (the )?(isolation|lock|loto)|de-?isolat|test run|restart/i;
const RE_ISOLATE = /isolat|lock ?out|loto/i;
app.isolated = false;
function applyConveyor() { if (app.isolated) modelCall('setConveyorRunning', false, { reason: 'isolated by W-01' }); }
function noteJob() {
  const job = jobOf();
  const steps = jobSteps(job);
  const keys = new Set();
  let flip = null;
  steps.forEach((st, i) => {
    const key = stepKey(st, i);
    keys.add(key);
    const k = stepStatus(st);
    const was = jobSeen.get(key);
    if (jobPrimed) {
      if (was === undefined) fxMark(`j:${key}`, 'new');
      if (k === 'done' && was !== 'done') {
        fxMark(`j:${key}`, 'done');
        const t = stepTitle(st);
        if (RE_RESTART.test(t)) flip = 'run'; else if (RE_ISOLATE.test(t)) flip = 'stop';
      }
    }
    jobSeen.set(key, k);
  });
  for (const key of [...jobSeen.keys()]) if (!keys.has(key)) jobSeen.delete(key);
  if (!steps.length) app.isolated = false;
  if (!jobPrimed) {
    jobPrimed = true;
    const doneT = steps.filter((st) => stepStatus(st) === 'done').map(stepTitle);
    if (doneT.some((t) => RE_ISOLATE.test(t) && !RE_RESTART.test(t)) && !doneT.some((t) => RE_RESTART.test(t))) { app.isolated = true; applyConveyor(); }
    return;
  }
  if (flip === 'stop') { app.isolated = true; applyConveyor(); }
  if (flip === 'run') { app.isolated = false; modelCall('setConveyorRunning', true); }
}
function noteAgents(prev) {
  const before = new Set((Array.isArray(prev && prev.agent_actions) ? prev.agent_actions : []).filter(Boolean).map(agentId));
  if (agentsPrimed) for (const a of (Array.isArray(app.state.agent_actions) ? app.state.agent_actions : [])) if (a && !before.has(agentId(a)) && !agentLive.has(agentId(a))) fxMark(`a:${agentId(a)}`, 'new');
  agentsPrimed = true;
}
function renderJob() {
  const job = jobOf();
  const steps = jobSteps(job);
  const list = $('#lpJobList');
  const meta = $('#lpJobMeta');
  if (meta) meta.title = job && (job.title || job.name) ? String(job.title || job.name) : '';
  if (!steps.length) {
    setHtml(list, '<li class="lp-empty">Speak your steps; BeltGuard writes them down as you go.</li>');
    setText(meta, '');
    return;
  }
  const done = steps.filter((st) => stepStatus(st) === 'done').length;
  setText(meta, `${steps.length} ${steps.length === 1 ? 'step' : 'steps'} · ${done} done`);
  setHtml(list, steps.map((st, i) => {
    const k = stepStatus(st);
    const word = k === 'done' ? 'done' : k === 'progress' ? 'in progress' : 'left';
    const ev = evidenceText(st);
    const fx = fxFor(`j:${stepKey(st, i)}`);
    return `<li class="js-row ${k}${fx ? fx.cls : ''}"${fx ? fx.style : ''}><span class="js-ico" role="img" aria-label="${word}">${k === 'done' ? CHECK_SVG : ''}</span><span class="js-text"><span class="js-label">${esc(stepTitle(st) || 'Step')}</span>${ev ? `<span class="js-ev" title="${esc(ev)}">${esc(ev)}</span>` : ''}</span></li>`;
  }).join(''));
  const cur = [...list.querySelectorAll('.js-row.fx-new')].pop() || list.querySelector('.js-row.progress') || list.querySelector('.js-row.left') || list.querySelector('.js-row:last-child');
  if (cur) {
    const lr = list.getBoundingClientRect(), cr = cur.getBoundingClientRect();
    if (cr.bottom > lr.bottom) list.scrollTop += cr.bottom - lr.bottom + 4;
    else if (cr.top < lr.top) list.scrollTop -= lr.top - cr.top + 4;
  }
}
function renderAgent() {
  const acts = agentActions().slice(0, 30);
  const list = $('#lpAgentList');
  setText($('#lpAgentMeta'), acts.length ? `${acts.length} ${acts.length === 1 ? 'action' : 'actions'}` : '');
  let anyFx = false;
  setHtml(list, acts.length ? acts.map((a) => {
    const st = agentStatus(a);
    const k = agentKind(a);
    const label = agentLabel(a);
    const detail = noApproval([a.detail, a.summary, a.text].find((x) => x && x !== label) || '');
    const fx = fxFor(`a:${agentId(a)}`);
    if (fx) anyFx = true;
    return `<li class="ag-row${fx ? fx.cls : ''}"${fx ? fx.style : ''}><span class="ag-ico"><svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">${AG_ICON[k] || AG_ICON.other}</svg></span>
      <span class="ag-body"><span class="ag-top"><span class="ag-title">${esc(label)}</span>${st ? `<span class="badge ${st[0]}">${esc(st[1])}</span>` : ''}</span>
      <span class="ag-meta">${a.ts ? `${fmtHM(Number(a.ts))}` : ''}${detail ? ` · ${esc(detail)}` : ''}</span></span></li>`;
  }).join('') : '<li class="lp-empty">Nothing yet. Work-order drafts, parts requests and notifications appear here as BeltGuard handles them.</li>');
  if (anyFx) list.scrollTop = 0;                           // newest first: keep the new row in view
}
const liveVisible = () => (app.mode === 'tool' && app.step >= 3) || app.mode === 'inspection';
function renderLive() {
  const on = liveVisible();
  const panel = $('#livePanel');
  if (!panel) return;
  panel.hidden = !on;
  document.body.classList.toggle('live-on', on);
  if (!on) return;
  safe(renderTech);
  safe(renderJob);
  safe(renderAgent);
}

const VOICE_KEY = 'beltguard.voice.on';
app.voiceOn = (() => { try { return localStorage.getItem(VOICE_KEY) !== '0'; } catch { return true; } })();
let voicePick = null;
function pickVoice() {
  if (voicePick || !('speechSynthesis' in window)) return voicePick;
  const vs = speechSynthesis.getVoices() || [];
  voicePick = vs.find((v) => /en-GB/i.test(v.lang) && /female|libby|sonia|serena|google uk/i.test(v.name))
    || vs.find((v) => /en-GB/i.test(v.lang)) || vs.find((v) => /^en/i.test(v.lang)) || vs[0] || null;
  return voicePick;
}
// Speak each reply once, in one tab only: a Web Lock plus a per-reply marker shared by every dashboard tab.
function speak(text, key) {
  if (!app.voiceOn || !('speechSynthesis' in window) || !text) return;
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;   // Chrome needs one click on the page first
  const mark = `beltguard.spoken.${String(key).slice(0, 120)}`;
  const go = () => {
    try { if (localStorage.getItem(mark)) return; localStorage.setItem(mark, String(Date.now())); } catch { /* storage blocked: still speak */ }
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    const v = pickVoice();
    if (v) u.voice = v;
    speechSynthesis.speak(u);
  };
  if (navigator.locks && navigator.locks.request) navigator.locks.request('beltguard-speak', go).catch(() => {});
  else go();
}
function pruneSpoken() {
  try {
    const cut = Date.now() - 15 * 60 * 1000;
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const k = localStorage.key(i);
      if (k && k.startsWith('beltguard.spoken.') && Number(localStorage.getItem(k)) < cut) localStorage.removeItem(k);
    }
  } catch { /* storage blocked */ }
}

// ---------- safety: band free-fall ----------
const ACK_KEY = 'beltguard.safety.ack';
function showSafety(e) {
  let acked = '';
  try { acked = localStorage.getItem(ACK_KEY) || ''; } catch { /* storage blocked */ }
  const id = String(e.id || e.ts);
  if (acked === id) return;
  app.safety = { id, ts: Number(e.ts) || nowS(), text: e.title || 'Possible drop or fall: check on the technician' };
  setText($('#safetyText'), app.safety.text);
  setText($('#safetyTime'), fmtHM(app.safety.ts));
  $('#safety').hidden = false;
}
function ackSafety() {
  if (app.safety) { try { localStorage.setItem(ACK_KEY, app.safety.id); } catch { /* storage blocked */ } }
  app.safety = null;
  $('#safety').hidden = true;
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
  if (!app.bcLast || performance.now() - app.bcLast > 2000) spark.pushWS(m);   // channel silent for 2 s → /ws samples
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

// Checkout transitions, derived only from the server's assignment (prev snapshot vs next snapshot).
function syncCheckout(prev, next, first) {
  const before = first ? null : outTool(prev);
  const after = outTool(next);
  const a = next.assignment && next.assignment.tool ? next.assignment : null;
  if (after && (!before || before.id !== after.id)) {
    app.ck = { tool_id: after.id, ts: Number(a && a.since_ts) || nowS() };
    return first ? null : { kind: 'assigned' };
  }
  if (after) {
    if (a && a.since_ts && app.ck) app.ck.ts = Number(a.since_ts);
    if (!app.ck) app.ck = { tool_id: after.id, ts: Number(a && a.since_ts) || nowS() };
    return null;
  }
  app.ck = null;
  if (before) {
    const pa = prev.assignment && prev.assignment.tool ? prev.assignment : null;
    const st = ckStats(prev);
    return { kind: 'returned', name: shortTool(before), trig: pa ? Number(pa.trigger_s_today_with_this_tool) || 0 : st.trig, pts: pa ? Number(pa.points_with_this_tool) || 0 : st.pts };
  }
  return null;
}

function applySnapshot(s) {
  if (!s || typeof s !== 'object') return;
  const prev = app.state;
  const first = !app.gotSnapshot;
  const base = emptyState();
  app.state = {
    ...base,
    ...s,
    exposure: { ...base.exposure, ...(s.exposure || {}) },
    connection: { ...base.connection, ...(s.connection || {}) },
    mic: { ...base.mic, ...(s.mic || {}) },
  };
  const ck = syncCheckout(prev, app.state, first);
  if (len(app.state, 'defects') > len(prev, 'defects')) { app.clarify = null; safe(renderClarify); }
  if (Array.isArray(s.timeline)) for (const e of s.timeline) addEvent(e, true);
  if (s.connection && s.connection.rate) setRate(s.connection.rate);
  safe(function liveSteps() { noteJob(); noteAgents(prev); });
  if (first) { app.gotSnapshot = true; app.started = app.started || !!outTool(); app.step = initialStep(); showStep(); if (AUTOPLAY) setTimeout(autoArm, 1500); setTimeout(loadTranscripts, 300); }
  else autoAdvance(prev, app.state, ck);
  renderAll();
}

// Page load: an open assignment shows the lock card at once (no animation; the scene's apply() puts the tool in the hand).
function initialStep() {
  return outTool() ? 2 : 1;         // gateTick moves to "Using tool" once the band actually sees the tool running
}

// Live events move the tool flow forward: checkout → tool running → exposure record → return.
// Checkout and return are the result of someone's action (click or voice), so they apply at once and always win.
// Session/record-driven moves wait while the user's own request is in flight or for 3 s after a click.
function autoAdvance(prev, next, ck) {
  if (ck && ck.kind === 'returned') {
    app.lockSeq += 1;                                      // stop any lock sequence still running
    app.locking = false;
    toast(`${ck.name} returned`, `${(ck.trig / 60).toFixed(1)} min · +${fmtPts(ck.pts)} pts`, 'ok');
    app.pendingStep = null;
    stepNote('#s5Note', '');
    setMode('tool');
    goStep(1);
    modelCall('returnTool').then(applyConveyor);           // the technician hands the tool back in the 3D; a standing isolation keeps the belt locked out
    modelCall('highlightTools', CRIB_WALL.filter((id) => (app.state.tools || []).some((t) => t.id === id)));  // queued after the walk back
    return;
  }
  if (ck && ck.kind === 'assigned') {
    app.pendingStep = null;
    stepNote('#s1Note', '');
    runLockSequence(outTool(next));
    return;
  }
  if (!outTool(next)) return;
  // "Using tool" (step 3) is entered from gateTick only: an ACTIVE session with fresh tool_active motion.
  // An ended "unresolved" session never moves the flow; it gets the confirm card on the current step.
  // A newly confirmed record moves on to the verdict only from "Using tool".
  let t = 0;
  if (app.step === 3 && len(next.exposure, 'records') > len(prev.exposure, 'records')) t = 4;
  if (!t) return;
  if (app.inflight > 0 || Date.now() < app.holdUntil) { app.pendingStep = t; return; }
  setMode('tool');
  goStep(t);
}
function holdFlow(ms = 3000) { app.holdUntil = Math.max(app.holdUntil, Date.now() + ms); }
function applyPendingStep() {
  if (!app.pendingStep || app.inflight > 0 || Date.now() < app.holdUntil) return;
  const p = app.pendingStep;
  app.pendingStep = null;
  if (outTool()) { setMode('tool'); goStep(p); }
}
async function userAction(fn) {
  app.inflight += 1;
  holdFlow(3000);
  try { return await fn(); } finally { app.inflight = Math.max(0, app.inflight - 1); holdFlow(1500); }
}

function addEvent(e, quiet) {
  if (!e || typeof e !== 'object') return;
  const id = String(e.id ?? `${e.ts}-${e.kind}-${e.title}`);
  const isNew = !app.events.has(id);
  const ev = { ...e, id, ts: Number(e.ts) || nowS() };
  app.events.set(id, ev);
  if (app.events.size > 600) app.events.delete(app.events.keys().next().value);
  if (e.kind === 'transcript') safe(() => techFromEvent(ev));       // updates too (the result arrives after the words)
  if (isNew && e.kind === 'transcript') { convoFromEvent(ev); safe(renderVoiceLog); safe(renderConvo); if (!quiet) safe(renderLive); }
  if (isNew && e.kind === 'safety' && (!quiet || nowS() - ev.ts < 120)) showSafety(ev);
  if (isNew && !quiet) maybeToast(e);
}

function maybeToast(e) {
  const k = String(e.kind || '');
  if (k === 'transcript' || k === 'safety') return;          // shown in the conversation strip / safety banner
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
  safe(renderLive);
}

const LOCK_SVG = '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
function renderFoot() {
  // While a tool is locked to the technician, the footer keeps that visible on the monitoring steps.
  const t = outTool();
  const lf = $('#lockFoot');
  const showLock = app.mode === 'tool' && t && app.step >= 3;
  if (showLock) {
    const w = (app.state.worker || {}).id || 'W-01';
    setHtml(lf, `${LOCK_SVG}<span>${esc(shortTool(t))} locked to ${esc(w)}${app.ck && app.ck.ts ? ` · since ${fmtHM(app.ck.ts)}` : ''}</span>`);
  }
  lf.hidden = !showLock;
  if (app.mode === 'tool') { setText($('#stepCount'), `Step ${app.step} of 5 · ${STEPS[app.step - 1]}`); return; }
  const n = stationList().filter((st) => stationStatus(st)[0]).length;
  setText($('#stepCount'), `${n} of 5 stations inspected · ${len(app.state, 'defects')} defects this shift`);
}

const CHECK = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 5 8.6l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function doneFlags() {
  const out = !!outTool();
  const st = ckStats();
  return { 1: out, 2: out, 3: out && st.recs.length > 0, 4: out && st.recs.length > 0, 5: false };
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
// Tool time is confirmed automatically now (S0 16:5x, user direction: no approvals anywhere), so the technician never
// sees a confirm/reject card; an unsure session resolves on its own within a few seconds.
function renderPending() {
  const card = $('#pendingCard');
  if (card) card.hidden = true;
}

function renderAll() {
  [renderHeader, renderFoot, renderStepper, renderCrib, renderStepTool, renderStations, renderInspection,
    renderListening, renderVoiceLog, renderConvo, renderPending, renderRecords, renderLive].forEach(safe);
  if (model) safe(function applyModel() { model.apply(modelState()); });
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
  if (app.everLive) setBadge(pill, 'outline wait', 'Reconnecting to BeltGuard…');
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
  const sel = app.picking === t.id || outId === t.id;
  const name = short ? shortTool(t) : t.name;
  return `<button type="button" class="tool-card${sel ? ' sel' : ''}" role="radio" aria-checked="${sel}" data-action="pick-tool" data-id="${esc(t.id)}" title="${esc(`${t.name} · ${t.source || 'source not recorded'}`)}">
    <span class="tc-name">${esc(name)}</span>
    <span class="tc-meta"><b>${esc(fmtMag(t.vibration_m_s2))}</b> m/s² · ${esc(outId === t.id ? 'checked out' : app.picking === t.id ? 'locking…' : srcShort(t))}</span></button>`;
}

// Step 1 "Pick tool": first "Start of shift" with ONE button (Start); after Start the technician walks to the crib
// and the crib cards appear ("Pick a tool"). A card click or a click on the 3D tool wall checks the tool out at once.
function renderCrib() {
  const s = app.state;
  const { main, more } = cribTools();
  const outT = outTool();
  const outId = outT ? outT.id : null;
  const ex = s.exposure || {};
  const startOfShift = totalPts() === 0 && !len(ex, 'records') && !outT;
  const showCrib = app.started || !!outT;
  const crib = $('#crib');
  crib.hidden = !showCrib;
  crib.classList.toggle('busy', !!app.picking);
  if (showCrib) {
    const moreOpen = !!more.find((t) => t.id === app.picking || t.id === outId) || !!app.moreOpen;
    setHtml(crib, main.length || more.length
      ? `${main.map((t) => toolCard(t, outId, true)).join('')}${more.length ? `<details class="disclosure crib-more" data-more${moreOpen ? ' open' : ''}><summary>More tools (${more.length})</summary><div class="crib inner">${more.map((t) => toolCard(t, outId)).join('')}</div></details>` : ''}`
      : '<p class="empty">The tool crib is loading from the backend.</p>');
  }
  setText($('#s1Title'), showCrib ? 'Pick a tool' : startOfShift ? 'Start of shift' : 'Ready for the next job');
  setText($('#s1Desc'), showCrib
    ? ((app.picking || (app.locking && outId)) ? `Locking the ${shortTool(toolById(app.picking || outId)).toLowerCase()} to you…` : 'Click a tool here or on the tool station in the 3D view. It locks to you straight away.')
    : "Pick the tool you're about to use. BeltGuard times it from your wrist band.");
  setText($('#s1Why'), startOfShift
    ? 'No vibration exposure yet today. Pick a tool when you are ready.'
    : `Today so far: ${fmtPts(totalPts())} points of the ${ex.action || 100}-point action value.`);
  // Start screen: icon + Today bar, centred. Crib: cards from the top, the bar gives way to the tools.
  $('#step-1').classList.toggle('crib-open', showCrib);
  $('#s1Ico').hidden = showCrib;
  safe(() => renderTodayBar('s1'));
  const b = $('#startBtn');
  b.hidden = showCrib;
  b.disabled = !!app.walking;
  setText(b, app.walking ? 'Walking to the tool crib…' : 'Start');
}

// Compact "Today" exposure bar (0–400, labelled 100/400 ticks, a dot at the current position): confirmed points.
function renderTodayBar(p) {
  const ex = app.state.exposure || {};
  const limit = ex.limit || 400, action = ex.action || 100;
  const total = totalPts();
  const pct = clamp((total / limit) * 100, 0, 100);
  setText($(`#${p}Today`), `${fmtPts(total)} pts${total >= limit ? ' · limit reached' : total >= action ? ' · above action value' : ''}`);
  const fill = $(`#${p}Fill`);
  fill.className = `fill${total >= limit ? ' alarm' : total >= action ? ' warn' : ''}`;
  fill.style.width = `${pct}%`;
  $(`#${p}Pos`).style.left = `${pct}%`;
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
  const t = outTool();
  if (!t) {
    setText($('#s2Title'), 'No tool locked to you yet');
    setText($('#s2Meta'), '');
    setText($('#s2Desc'), 'Pick a tool from the crib first.');
    for (const id of ['#s2Mag', '#s2Rate', '#s2ToAction']) setText($(id), '—');
    setText($('#s2Why'), 'The tool is known from the checkout, never guessed from motion.');
    setHtml($('#s2Action'), '<button class="btn primary" type="button" data-action="go-step" data-step="1">Go to the tool crib</button>');
    return;
  }
  // The LOCK card: the tool is now bound to this technician.
  const ck = app.ck;
  setText($('#s2Title'), `${shortTool(t)} locked to ${workerLabel()}`);
  setText($('#s2Meta'), `${ck && ck.ts ? `${fmtHM(ck.ts)} · ` : ''}${fmtMag(aOf(t))} m/s² (${srcShort(t)})`);
  setText($('#s2Desc'), "Only this person's trigger time counts toward this checkout.");
  const a = aOf(t);
  setText($('#s2Mag'), fmtMag(a));
  setText($('#s2Rate'), a ? String(Math.round(2 * a * a)) : '—');
  const ta = minutesLeft((s.exposure || {}).action || 100);
  setText($('#s2ToAction'), ta == null ? '—' : String(Math.round(ta)));
  setText($('#s2Why'), `Magnitude source: ${t.source || 'not recorded'}.`);
  safe(() => renderTodayBar('s2'));
  const st = stationById(s.active_station || 'S1');
  setText($('#s2Next'), app.locking
    ? `Next: walking to ${st ? `station ${st.number}, ${st.name}` : 'the station'}.`
    : `Next: start the ${shortTool(t).toLowerCase()}. BeltGuard times it from your wrist.`);
  setHtml($('#s2Action'), app.locking
    ? `<button class="btn outline" type="button" disabled>Walking to ${esc(st ? `station ${st.number}` : 'the station')}…</button>`
    : bandLive()
      ? '<button class="btn primary" type="button" data-action="go-step" data-step="3">Start using the tool</button><button class="btn outline" type="button" data-action="go-step" data-step="5">Return it</button>'
      : bandAction('Connect band 07 in the Band link tab'));
}

// ---------- opening sequence (S7's scene methods are optional: missing ones count as instant) ----------
const withTimeout = (p, ms) => Promise.race([Promise.resolve(p).catch(() => {}), sleep(ms)]);
function modelCall(name, ...args) {
  if (!model || typeof model[name] !== 'function') return Promise.resolve();
  try { return Promise.resolve(model[name](...args)).catch(() => {}); } catch { return Promise.resolve(); }
}
const CRIB_WALL = ['drill', 'hammer_drill', 'impact_wrench', 'angle_grinder', 'power_jack', 'breaker_hammer'];
async function startShift() {
  if (app.walking || app.started) return;
  app.walking = true;
  holdFlow(15000);
  safe(renderCrib);
  await withTimeout(modelCall('walkToCrib'), 15000);       // S7 snaps any animation after 15 s
  app.walking = false;
  app.started = true;
  const ids = CRIB_WALL.filter((id) => (app.state.tools || []).some((t) => t.id === id));
  modelCall('highlightTools', ids);
  safe(renderCrib);
}
// Card click or a click on the 3D tool wall (createModel onToolClick): check the tool out at once.
async function pickTool(id) {
  if (!id || app.picking || app.locking) return;
  const out = outTool();
  if (out) {
    if (out.id === id) { holdFlow(); goStep(2); } else toast(`Return the ${shortTool(out).toLowerCase()} first`, 'One tool at a time is locked to you.', '');
    return;
  }
  app.started = true;
  app.picking = id;
  safe(renderCrib);
  try { await userAction(() => unlockTool(id)); } finally { app.picking = null; safe(renderCrib); }
}
// ---------- demo-video autoplay: ONLY with ?autoplay=1 AND a simulated band (never on a real band) ----------
const AUTOPLAY = qs.get('autoplay') === '1';
const auto = { armed: false, running: false, t0: 0, marks: [], timer: null };
window.BeltGuardAutoplay = auto;                            // timing marks for whoever records the video
const autoOk = () => AUTOPLAY && app.live && simMode();
function autoChip(text, { run = false, play = false, replay = false } = {}) {
  const el = $('#autoChip');
  if (!el) return;
  el.hidden = false;
  el.classList.toggle('run', run);
  setHtml(el, `<i class="dot" aria-hidden="true"></i><span>${esc(text)}</span>${play ? '<button class="btn sm" type="button" data-action="auto-play">Play with voice</button>' : ''}${replay ? '<button class="btn sm" type="button" data-action="auto-replay">Replay</button>' : ''}`);
}
function autoMark(label) {
  auto.marks.push(`${((Date.now() - auto.t0) / 1000).toFixed(1)}s ${label}`);
  autoChip(`Demo autoplay · simulated band · ${label}`, { run: true });
}
async function autoWait(fn, ms = 20000) {
  const t = Date.now();
  while (Date.now() - t < ms) {
    if (!autoOk()) return false;
    if (safe(fn)) return true;
    await sleep(200);
  }
  return false;
}
const autoSay = (text) => post('/api/report', { text, source: 'band-mic' }, { quiet: true });
function autoArm() {
  if (!AUTOPLAY || auto.armed) return;
  auto.armed = true;
  if (!autoOk()) { autoChip('Autoplay runs only on the simulated band'); return; }
  autoChip('Demo autoplay · simulated band · starts in 5 s', { play: true });
  auto.timer = setTimeout(() => runAutoplay(), 5000);
}
async function runAutoplay() {
  clearTimeout(auto.timer);
  if (auto.running) return;
  if (!autoOk()) { autoChip('Autoplay runs only on the simulated band'); return; }
  auto.running = true;
  auto.marks = [];
  auto.t0 = Date.now();
  try {
    // clean start: no tool out, technician at station 3, the start screen
    if (outTool()) { await post('/api/tool/return', {}, { quiet: true }); await refreshState(); await sleep(1500); }
    await post('/api/station', { station_id: 'S3' }, { quiet: true });
    app.started = false;
    setMode('tool');
    goStep(1);
    safe(renderAll);
    autoMark('start of shift');
    await sleep(5000); if (!autoOk()) return;
    startShift(); autoMark('walking to the crib');
    await autoWait(() => app.started && !app.walking, 20000);
    autoMark('pick a tool');
    await sleep(4500); if (!autoOk()) return;
    pickTool('hammer_drill'); autoMark('picking the hammer drill');
    await autoWait(() => app.step === 2 && !!outTool(), 25000);
    autoMark('locked to W-01');
    await autoWait(() => app.step === 3 && !app.locking, 25000);
    autoMark('at station 3');
    await sleep(3500); if (!autoOk()) return;
    await autoSay("BeltGuard, I'm at station three"); autoMark('voice: at station three');
    await sleep(5500); if (!autoOk()) return;
    await post('/api/sim/scene', { scene: 'tool', seconds: 10 }, { quiet: true }); autoMark('drilling (simulated 10 s)');
    await autoWait(() => gate.on, 10000);
    autoMark('tool running');
    await autoWait(() => app.step === 4 || !$('#pendingCard').hidden, 30000);
    if (app.step !== 4 && !$('#pendingCard').hidden) {           // the band was unsure: confirm it like the technician would
      const b = $('#pendingCard [data-action="confirm"]');
      if (b) b.click();
      await autoWait(() => app.step === 4, 15000);
    }
    autoMark('exposure verdict');
    await sleep(7500); if (!autoOk()) return;
    await autoSay('BeltGuard, how long can I keep going?'); autoMark('voice: how long can I keep going?');
    await sleep(9000); if (!autoOk()) return;
    const wo0 = len(app.state, 'work_orders');
    await autoSay('Return idler seized, needs replacement'); autoMark('voice: defect report');
    await autoWait(() => len(app.state, 'work_orders') > wo0, 15000);
    setMode('inspection'); autoMark('defect + work order');
    await sleep(8500); if (!autoOk()) return;
    setMode('tool');
    goStep(5); autoMark('return the tool');
    await sleep(4500); if (!autoOk()) return;
    await userAction(returnTool);
    await autoWait(() => !outTool() && app.step === 1, 20000);
    autoMark('returned');
    await sleep(6000);
    app.started = false;
    safe(renderCrib);
    auto.marks.push(`${((Date.now() - auto.t0) / 1000).toFixed(1)}s end`);
    autoChip(`Demo finished · ${Math.round((Date.now() - auto.t0) / 1000)} s`, { replay: true });
  } finally {
    auto.running = false;
    if (!autoOk()) autoChip('Autoplay stopped: not on the simulated band', { replay: false });
  }
}

// After a checkout (click, 3D wall or voice): pick-up animation → LOCK card → walk to the station → monitoring.
async function runLockSequence(tool) {
  if (!tool) return;
  const seq = ++app.lockSeq;
  app.locking = true;
  app.inflight += 1;                                       // no other auto-advance while this plays
  safe(renderCrib);                                        // "Locking the hammer drill to you…" while the 3D picks it up
  try {
    await withTimeout(modelCall('pickTool', tool.id), 15000);
    modelCall('highlightTools', []);                       // the wall stops glowing once a tool is in hand
    if (seq !== app.lockSeq) return;
    setMode('tool');
    goStep(2);                                             // the LOCK card
    safe(renderAssigned);
    await sleep(AUTOPLAY ? 2600 : 900);                    // the demo video holds the LOCK card a little longer
    await withTimeout(modelCall('walkToStation', app.state.active_station || 'S1'), 15000);
    if (seq !== app.lockSeq || !outTool()) return;
    goStep(3);                                             // monitoring: the band drives the numbers
  } finally {
    if (seq === app.lockSeq) app.locking = false;
    app.inflight = Math.max(0, app.inflight - 1);
    safe(renderAssigned);
  }
}

function renderActivity() {
  { const chip = $('#simChip'); if (chip) chip.hidden = !(app.live && simMode()); }
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
  setText($('#s3Title'), t && outTool() ? `Using the ${shortTool(t).toLowerCase()}` : 'No tool checked out');
  const st = ckStats();
  const c = confirmedTool();
  const lv = liveNow();
  setText($('#s3TrigLabel'), lv.running ? 'Trigger time, running' : 'Trigger time with this tool');
  setText($('#s3Trig'), fmtMS(c.trig + lv.sec));
  setText($('#s3Pts'), `+${fmtPts(c.pts)}`);
  setText($('#s3Prov'), lv.pts >= 0.005 ? `+${fmtPts(lv.pts)} provisional` : '');
  setText($('#s3Today'), fmtPts(totalPts()));
  let html;                         // unresolved sessions get the confirm card above the footer, on every step
  if (!bandLive()) {
    html = bandAction('Connect band 07 in the Band link tab');
  } else if (!outTool()) {
    html = '<button class="btn primary" type="button" data-action="go-step" data-step="1">Unlock a tool first</button>';
  } else {
    html = '<button class="btn primary" type="button" data-action="go-step" data-step="4">See the exposure verdict</button>';
  }
  setHtml($('#s3Action'), html);
  setText($('#s3Why'), t ? `Time counts only while the band detects the tool running. Points = 2 · a² · T with a = ${fmtMag(aOf(t))} m/s²; running points are provisional until the session is confirmed.`
    : 'Time counts only while the band detects the tool running. Points = 2 · a² · T.');
  safe(renderActivity);
  safe(updateEmpty);
}

// The verdict is decided on CONFIRMED points (the backend's own verdict when it sends one).
function verdict(total) {
  const ex = app.state.exposure || {};
  const sv = app.state.verdict || null;
  const action = (sv && sv.action) || ex.action || 100, limit = (sv && sv.limit) || ex.limit || 400;
  const level = sv && sv.state
    ? ({ within: 'ok', action: 'action', limit: 'limit' }[sv.state] || 'ok')
    : total >= limit ? 'limit' : total >= action ? 'action' : 'ok';
  const head = sv && sv.headline ? noApproval(sv.headline) : null;
  if (level === 'limit') return { level, title: 'Limit reached: stop using vibrating tools today', sub: head || `Today's estimate is ${fmtPts(total)} points, at or over the ${limit}-point exposure limit value.` };
  if (level === 'action') return { level, title: 'Action value reached: take a break or rotate', sub: head || `Today's estimate is ${fmtPts(total)} points, over the ${action}-point exposure action value.` };
  return { level, title: 'Within the safe limit', sub: head || `Below the ${action}-point exposure action value.` };
}

function renderVerdict() {
  const ex = app.state.exposure || {};
  const sv = app.state.verdict || {};
  const total = totalPts();                                   // confirmed
  const lv = liveNow();                                       // provisional, gated on the band
  const action = sv.action || ex.action || 100;
  const limit = sv.limit || ex.limit || 400;
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
  $('#expProv').style.width = lv.pts >= 0.005 ? `${clamp(((total + lv.pts) / limit) * 100, 0, 100)}%` : '0';
  const prov = $('#vProv');
  if (lv.pts >= 0.005) {
    const crosses = total < action && total + lv.pts >= action ? ' · would reach the action value'
      : total < limit && total + lv.pts >= limit ? ' · would reach the limit value' : '';
    setText(prov, `+${fmtPts(lv.pts)} pts provisional from the running session, not yet confirmed${crosses}`);
    prov.hidden = false;
  } else prov.hidden = true;
  $('#expBar').setAttribute('aria-valuenow', String(Math.round(total * 10) / 10));
  const ta = minutesLeft(action, total + lv.pts), tl = minutesLeft(limit, total + lv.pts);
  setText($('#vToAction'), ta == null ? '—' : String(Math.round(ta)));
  setText($('#vToLimit'), tl == null ? '—' : String(Math.round(tl)));
  const t = ckTool();
  setText($('#vWhy'), t
    ? `Minutes left assume the ${shortTool(t).toLowerCase()} at ${fmtMag(aOf(t))} m/s². The band times trigger use; it does not measure vibration at the hand.`
    : "Minutes left use the backend's basis tool. The band times trigger use; it does not measure vibration at the hand.");
}

function sessionRows(sessions, recs) {
  const byId = new Map((recs || []).map((r) => [r.tool_session_id, r]));
  return sessions.filter((x) => realSession(x, byId.get(x.id))).slice(-6).reverse().map((x) => {
    const r = byId.get(x.id);
    const sec = x.status === 'active' ? (gate.sid === x.id ? gate.sec : 0) : Number((r && r.trigger_s) ?? x.trigger_s) || 0;
    const pts = r ? r.points : livePts(sec);
    const word = x.status === 'active' ? (gate.on && gate.sid === x.id ? 'running, provisional' : 'provisional') : x.status === 'unresolved' ? 'confirming' : '';
    return `<li><span class="mono">${fmtHM(Number(x.ts) || nowS())}</span><span>${fmtDur(sec)}${word ? ` · ${word}` : ''}</span><span class="num">+${fmtPts(pts)} pts</span></li>`;
  }).join('');
}

function renderReturn() {
  const s = app.state;
  const t = outTool();
  if (t) {
    const st = ckStats();
    const since = app.ck && app.ck.ts;
    setText($('#s5Title'), `Return the ${shortTool(t).toLowerCase()}`);
    setText($('#s5Desc'), since ? `Checked out at ${fmtHM(since)}, ${fmtDur(nowS() - since)} ago.` : 'Session summary for this checkout.');
    setText($('#s5Trig'), (st.trig / 60).toFixed(1));
    setText($('#s5Pts'), `+${fmtPts(st.pts)}`);
    setText($('#s5Today'), fmtPts(totalPts()));
    const rows = sessionRows(st.sessions, st.recs);
    setHtml($('#s5Sessions'), rows || '<li><span class="muted">No tool use detected during this checkout.</span></li>');
    setText($('#s5Why'), "Returning the tool closes the checkout. Today's points stay on the technician's record.");
    safe(() => renderTodayBar('s5t'));
    setHtml($('#s5Action'), `<button class="btn primary" type="button" data-action="return-tool">Return the ${esc(shortTool(t).toLowerCase())}</button>`);
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

const STATUS_WORD = { draft: 'Draft', planned: 'Planned', in_progress: 'In progress', awaiting_verification: 'Complete' };
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
    <div class="sum-meta">${esc(tool ? tool.name : 'Tool to be chosen')}${wo.est_trigger_min != null ? ` · ${esc(wo.est_trigger_min)} min trigger time` : ''}</div>
    ${rec ? `<p class="sum-rec">Projected ${fmtPts(rec.projected_points)} points today after this job (+${fmtPts(rec.added_points)}): ${Number(rec.projected_points) >= 400 ? 'over the limit value' : Number(rec.projected_points) >= 100 ? 'above the action value' : 'within the action value'}.</p>` : ''}
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
  const all = [...app.events.values()].filter((e) => e.kind === 'transcript' && e.ts >= CONVO_SINCE).sort((a, b) => b.ts - a.ts).map(parseUtterance);
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
    rows.push(`<div class="confirm-row"><div>Confirming automatically: ${esc(tl ? tl.name : 'no tool selected')}, <span class="num">${fmtDur(u.trigger_s)}</span> · confidence ${Math.round((u.confidence || 0) * 100)}%</div></div>`);
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
  const tl = toolById(active.tool_id) || ckTool();
  const el = gate.sid === active.id ? gate.sec : 0;          // only time the band saw the tool running
  row.querySelector('[data-live-dur]').textContent = `${fmtDur(el)}${gate.on ? '' : ' · paused'}`;
  row.querySelector('[data-live-pts]').textContent = `+${fmtPts(2 * aOf(tl) ** 2 * (el / 3600))} pts provisional`;
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
// Every click goes to the server, even while the socket is reconnecting (S0 14:42); only the offline demo replay intercepts.
async function post(path, body, { optional = false, conflictOk = false, quiet = false } = {}) {
  if (replay.running) return replay.handleApi(path, body || {});
  try {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(HTTP_BASE + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.detail || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return data;
  } catch (err) {
    if (optional && (err.status === 404 || err.status === 405)) return { missing: true };
    if (conflictOk && err.status === 409) return { conflict: true, detail: err.message };
    if (quiet) return { error: true, status: err.status || 0, detail: err.message };
    if (!err.status) toast('Reconnecting to BeltGuard…', 'That action was not sent. Try again in a moment.', 'warn');
    else toast('Request failed', `${path}: ${err.message}`, 'alarm');
    setTimeout(() => { refreshState(); }, 400);              // after any failure the screen follows the server again
    return null;
  }
}

// Re-read the server state (after a restart the server can know less than this page remembers). True if it got one.
async function refreshState() {
  if (replay.running) return false;
  try {
    const r = await fetch(`${HTTP_BASE}/api/state`, { cache: 'no-store' });
    if (!r.ok) return false;
    applySnapshot(await r.json());
    return true;
  } catch { return false; /* the socket will bring the next snapshot */ }
}
const api = (path, body) => post(path, body);
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
// One automatic retry after 2 s for server errors (5xx, e.g. a backend restart) and network errors.
async function postRetry(path, body, onRetry) {
  let r = await post(path, body, { quiet: true });
  if (r && r.error && (r.status === 0 || r.status >= 500)) {
    if (onRetry) onRetry();
    await sleep(2000);
    r = await post(path, body, { quiet: true });
  }
  return r;
}
function stepNote(sel, text) {
  const el = $(sel);
  if (!el) return;
  setText(el, text || '');
  el.hidden = !text;
}

async function unlockTool(tid) {
  if (!tid) return;
  if (replay.running) { replay.handleApi('/api/tool', { tool_id: tid }); return; }
  stepNote('#s1Note', '');
  const w = (app.state.worker || {}).id || 'W-01';
  const r = await postRetry('/api/tool/checkout', { tool_id: tid, worker_id: w },   // the checkout endpoint only
    () => stepNote('#s1Note', 'BeltGuard is reconnecting… trying again.'));
  await refreshState();                                     // the assignment, if any, moves the flow to step 2
  if (outTool()) { stepNote('#s1Note', ''); return; }
  if (r && r.error) stepNote('#s1Note', r.status >= 400 && r.status < 500 && r.detail ? `BeltGuard could not lock that tool: ${r.detail}.` : "BeltGuard didn't get that. Pick the tool again.");
}

async function returnTool() {
  if (replay.running) { replay.handleApi('/api/tool', { tool_id: null }); return; }
  const out = outTool();
  stepNote('#s5Note', '');
  const w = (app.state.worker || {}).id || 'W-01';
  const r = await postRetry('/api/tool/return', { tool_id: out && out.id, worker_id: w },
    () => stepNote('#s5Note', 'BeltGuard is reconnecting… trying again.'));
  // Only an actual null assignment means step 1: the fresh snapshot decides (its transition shows the summary toast).
  const fresh = await refreshState();
  if (!outTool()) { stepNote('#s5Note', ''); if (app.step !== 1) goStep(1); return; }
  if (!r || r.error || !fresh) stepNote('#s5Note', "BeltGuard didn't get that. Try Return again.");
}

function selectStation(id) { if (id) api('/api/station', { station_id: id }); }

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-action]');
  if (!b) return;
  const id = b.dataset.id;
  switch (b.dataset.action) {
    case 'mode': holdFlow(); setMode(b.dataset.mode); break;
    case 'go-step': holdFlow(); goStep(b.dataset.step); break;
    case 'step-prev': holdFlow(); goStep(app.step - 1); break;
    case 'step-next': holdFlow(); goStep(app.step + 1); break;
    case 'start-shift': startShift(); break;
    case 'auto-play': runAutoplay(); break;            // the click also lets Chrome speak the replies
    case 'auto-replay': runAutoplay(); break;
    case 'pick-tool': app.moreOpen = !!b.closest('[data-more]'); pickTool(id); break;
    case 'return-tool': userAction(returnTool); break;
    case 'station': selectStation(id); break;
    case 'open-sheet': openSheet(true); break;
    case 'close-sheet': openSheet(false); break;
    case 'ack-safety': ackSafety(); break;
    case 'dtab': setDtab(b.dataset.tab); break;
    case 'confirm': {
      const trig = Number(b.dataset.trigger);
      userAction(() => api(`/api/tool_session/${encodeURIComponent(id)}/confirm`, isFinite(trig) && trig > 0 ? { confirmed: true, trigger_s: trig } : { confirmed: true }));
      break;
    }
    case 'reject': userAction(() => api(`/api/tool_session/${encodeURIComponent(id)}/confirm`, { confirmed: false })); break;
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
on('#voiceToggle', 'change', (e) => {
  app.voiceOn = !!e.target.checked;
  try { localStorage.setItem(VOICE_KEY, app.voiceOn ? '1' : '0'); } catch { /* storage blocked */ }
  if (!app.voiceOn && 'speechSynthesis' in window) speechSynthesis.cancel();
});
{ const vt = document.querySelector('#voiceToggle'); if (vt) vt.checked = app.voiceOn; }
if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => { voicePick = null; };
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
    if (app.gotSnapshot) setTimeout(loadTranscripts, 800);   // after a reconnect
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
  safe(applyPendingStep);
  safe(renderPending);                // prompts age out after 10 minutes
}, 500);
setInterval(() => setText($('#clock'), new Date().toLocaleTimeString([], { hour12: false })), 1000);
setInterval(() => safe(gateTick), 200);
safe(pruneSpoken);
setInterval(() => safe(pruneSpoken), 5 * 60 * 1000);

connect();                      // the socket first, so a render problem can never leave the page on "Connecting…"
safe(renderAll);
safe(renderActivity);
safe(renderMode);
safe(showStep);
frame();
