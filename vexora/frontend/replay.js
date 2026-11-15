// DEMO replay — runs only when no backend is reachable.
// Every message it emits has source "sim" and every value is illustrative.
// It speaks the same WebSocket contract as the backend (see ../CONTRACT.md),
// so the dashboard code path is identical in demo and live mode.

const PI2 = Math.PI * 2;
const SENTENCE = 'Conveyor CV-102, station three, return idler seized, needs replacement.';

export const DEMO_STATIONS = [
  { id: 'S1', number: 1, name: 'Tail pulley & loading zone', pos: 0.08, status: 'ok' },
  { id: 'S2', number: 2, name: 'Carry idlers & belt tracking', pos: 0.30, status: 'ok' },
  { id: 'S3', number: 3, name: 'Return idlers', pos: 0.52, status: 'ok' },
  { id: 'S4', number: 4, name: 'Take-up & belt tension', pos: 0.74, status: 'ok' },
  { id: 'S5', number: 5, name: 'Head/drive pulley & discharge', pos: 0.94, status: 'ok' },
];

const DEMO_SOURCE = 'Demo value (illustrative)';
export const DEMO_TOOLS = [
  { id: 'impact_wrench', name: '1/2 in impact wrench', vibration_m_s2: 5.0, source: DEMO_SOURCE },
  { id: 'impact_wrench_1in', name: '1 in impact wrench', vibration_m_s2: 10.0, source: DEMO_SOURCE },
  { id: 'angle_grinder', name: 'Angle grinder', vibration_m_s2: 7.0, source: DEMO_SOURCE },
  { id: 'needle_scaler', name: 'Needle scaler', vibration_m_s2: 19.0, source: DEMO_SOURCE },
  { id: 'demo_tool', name: 'Phone on vibrate (demo stand-in for 1/2 in impact wrench)', vibration_m_s2: 5.0, source: DEMO_SOURCE },
];

// Per-state detector features: acc_rms (g), gyro_rms (deg/s), hf_ratio, confidence
const FEATURES = {
  idle: [0.004, 0.12, 0.08, 0.92],
  walking: [0.21, 31, 0.14, 0.87],
  gesture: [0.24, 68, 0.3, 0.71],
  candidate: [0.31, 15, 0.66, 0.56],
  tool_active: [0.52, 22, 0.81, 0.86],
  unresolved: [0.2, 20, 0.5, 0.45],
};

export class DemoReplay {
  constructor(emit, { onReset } = {}) {
    this.emit = emit;
    this.onReset = onReset;
    this.timer = null;
    this.running = false;
  }

  start() {
    this.stop();
    this.running = true;
    this.t = 0;
    this.bandT = 120;
    this.n = 0;
    this.seq = 0;
    this.actClock = 0;
    this.motionState = 'idle';
    this.activeSession = null;
    this.state = this.initial();
    this.cues = this.script();
    this.ci = 0;
    if (this.onReset) this.onReset();
    this.send({ type: 'connection', state: 'simulated', band: 7, rate: 50, detail: 'Demo replay — no backend connected' });
    this.snapshot();
    this.timer = setInterval(() => this.tick(), 100);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.running = false;
  }

  now() { return Date.now() / 1000; }
  id(prefix) { this.seq += 1; return `${prefix}-${String(this.seq).padStart(4, '0')}`; }
  send(msg) { this.emit({ ts: this.now(), source: 'sim', ...msg }); }

  snapshot() {
    this.state.timeline = this.state.timeline.slice(-200);
    this.send({ type: 'snapshot', state: structuredClone(this.state) });
  }

  event(lane, kind, title, detail = '', extra = {}) {
    const e = {
      id: this.id('E'), ts: this.now(), source: 'sim', lane, kind, title, detail,
      station_id: extra.station_id !== undefined ? extra.station_id : this.state.active_station,
      refs: extra.refs || {},
    };
    this.state.timeline.push(e);
    this.send({ type: 'event', event: e });
    return e;
  }

  initial() {
    return {
      worker: { id: 'W-01', name: 'Demo technician' },
      conveyor: { id: 'CV-102', name: 'Overland conveyor CV-102' },
      stations: DEMO_STATIONS.map((s) => ({ ...s })),
      active_station: null,
      tools: DEMO_TOOLS.map((t) => ({ ...t })),
      active_tool: null,
      defects: [],
      work_orders: [],
      tool_sessions: [],
      exposure: { total_points: 0, action: 100, limit: 400, status: 'below_action', records: [] },
      recommendation: null,
      timeline: [],
      connection: { state: 'simulated', band: 7, rate: 50, lost_total: 0 },
      mic: { on: false },
      detector: { state: 'idle', confidence: 0 },
    };
  }

  // ---------- clock ----------
  tick() {
    this.t += 0.1;
    const accel = [], gyro = [];
    for (let k = 0; k < 5; k += 1) {
      const [a, g] = this.synth(this.motionState, this.bandT + k * 0.02);
      accel.push(a); gyro.push(g);
    }
    this.send({ type: 'motion', rate: 50, t0: +this.bandT.toFixed(3), dt: 0.02, n0: this.n, lost_total: 0, accel, gyro });
    this.bandT += 0.1;
    this.n += 5;

    this.actClock += 0.1;
    if (this.actClock >= 0.25) { this.actClock = 0; this.sendActivity(); }

    while (this.running && this.ci < this.cues.length && this.cues[this.ci].at <= this.t) {
      const cue = this.cues[this.ci];
      this.ci += 1;
      try { cue.fn(); } catch (err) { console.warn('demo cue failed', err); }
    }
  }

  sendActivity() {
    const f = FEATURES[this.motionState] || FEATURES.idle;
    const j = () => 1 + (Math.random() - 0.5) * 0.12;
    const conf = Math.min(0.99, f[3] * j());
    this.state.detector = { state: this.motionState, confidence: +conf.toFixed(2) };
    this.send({
      type: 'activity', state: this.motionState, confidence: +conf.toFixed(2),
      features: { acc_rms: +(f[0] * j()).toFixed(3), gyro_rms: +(f[1] * j()).toFixed(1), hf_ratio: +(f[2] * j()).toFixed(2) },
    });
  }

  synth(st, t) {
    const r = () => Math.random() * 2 - 1;
    let ax = 0.03 + r() * 0.003, ay = -0.12 + r() * 0.003, az = 0.99 + r() * 0.003;
    let gx = r() * 0.08, gy = r() * 0.08, gz = r() * 0.08;
    if (st === 'walking') {
      const w = PI2 * 1.85 * t;
      az += 0.3 * Math.sin(w) + 0.1 * Math.sin(2 * w + 0.7) + r() * 0.03;
      ax += 0.16 * Math.sin(w / 2) + r() * 0.03;
      ay += 0.08 * Math.sin(w + 1.1) + r() * 0.03;
      gx += 42 * Math.sin(w / 2) + r() * 3;
      gy += 25 * Math.sin(w / 2 + 0.8) + r() * 3;
      gz += 14 * Math.sin(w) + r() * 2;
    } else if (st === 'gesture') {
      const env = Math.max(0, Math.sin(PI2 * 0.7 * t)) ** 2;
      ax += env * 0.5 * Math.sin(PI2 * 3.2 * t);
      ay += env * 0.35 * Math.sin(PI2 * 2.5 * t + 1);
      az += env * 0.25 * Math.sin(PI2 * 2.9 * t + 2);
      gx += env * 140 * Math.sin(PI2 * 2.2 * t);
      gy += env * 90 * Math.sin(PI2 * 1.8 * t + 0.5);
      gz += env * 60 * Math.sin(PI2 * 2.6 * t + 1.3);
    } else if (st === 'candidate' || st === 'tool_active') {
      const k = st === 'candidate' ? 0.55 : 1;
      ax += k * (0.42 * r() + 0.22 * Math.sin(PI2 * 23.3 * t));
      ay += k * (0.38 * r() + 0.2 * Math.sin(PI2 * 19.1 * t + 1));
      az += k * (0.5 * r() + 0.25 * Math.sin(PI2 * 21.7 * t + 2));
      gx += k * 28 * r(); gy += k * 22 * r(); gz += k * 18 * r();
    }
    const q = (v, p) => +v.toFixed(p);
    return [[q(ax, 4), q(ay, 4), q(az, 4)], [q(gx, 2), q(gy, 2), q(gz, 2)]];
  }

  // ---------- scripted shift ----------
  script() {
    const c = [];
    const at = (t, fn) => c.push({ at: t, fn });
    at(0.4, () => this.event('system', 'shift_start', 'Shift started — demo replay', 'Illustrative values. No band is connected.', { station_id: null }));
    at(1.0, () => this.addPrior());
    at(2.0, () => { this.selectStation('S1', true); this.motionState = 'walking'; });
    at(4.6, () => { this.motionState = 'idle'; });
    at(5.4, () => this.inspectOk('S1', 'Tail pulley lagging and loading-chute liners look fine.'));
    at(6.2, () => { this.selectStation('S2', true); this.motionState = 'walking'; });
    at(9.0, () => { this.motionState = 'gesture'; });
    at(10.2, () => { this.motionState = 'idle'; this.inspectOk('S2', 'Belt tracking within tolerance.'); });
    at(11.0, () => { this.selectStation('S3', true); this.motionState = 'walking'; });
    at(14.0, () => { this.motionState = 'idle'; });
    at(14.6, () => this.send({ type: 'transcript', text: 'Conveyor CV-102, station three…', final: false }));
    at(15.6, () => this.send({ type: 'transcript', text: 'Conveyor CV-102, station three, return idler seized…', final: false }));
    at(16.8, () => this.send({ type: 'transcript', text: SENTENCE, final: true }));
    at(17.6, () => this.report(SENTENCE));
    at(20.5, () => this.selectTool('impact_wrench'));
    at(21.5, () => { this.motionState = 'candidate'; });
    at(22.6, () => { this.motionState = 'tool_active'; this.toolStart(); });
    at(33.6, () => { this.motionState = 'idle'; this.toolStop(); });
    at(37.0, () => this.confirm(null, true));
    at(40.0, () => this.planRepair({}));
    at(48.0, () => { this.selectStation('S4', true); this.motionState = 'walking'; });
    at(51.0, () => { this.motionState = 'idle'; });
    at(75.0, () => this.start());
    return c;
  }

  stationById(id) { return this.state.stations.find((s) => s.id === id); }

  selectStation(id, scripted = false) {
    const st = this.stationById(id);
    if (!st) return;
    const prev = this.stationById(this.state.active_station);
    if (prev && prev.id !== id && prev.status !== 'defect') prev.status = 'inspected';
    this.state.active_station = id;
    this.event('inspection', 'station_selected', `Station ${st.number} selected — ${st.name}`,
      scripted ? 'Selected by the technician (demo script).' : 'Selected by the technician.', { station_id: id });
    this.snapshot();
  }

  inspectOk(id, note) {
    const st = this.stationById(id);
    if (!st) return;
    if (st.status !== 'defect') st.status = 'inspected';
    this.event('inspection', 'inspection_ok', `Station ${st.number} inspected — no defect`, note, { station_id: id });
    this.snapshot();
  }

  parse(text) {
    const t = text.toLowerCase();
    const words = { one: 1, two: 2, three: 3, four: 4, five: 5, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 };
    const m = t.match(/\bstation\s*(one|two|three|four|five|[1-5])\b/) || t.match(/\bs([1-5])\b/);
    const num = m ? words[m[1]] : null;
    const conveyorOk = !/\bcv-?\s?\d+/.test(t) || /\bcv-?\s?102\b/.test(t);
    let component = 'Unspecified component';
    if (/return idler/.test(t)) component = 'Return idler';
    else if (/idler/.test(t)) component = 'Carry idler';
    else if (/pulley/.test(t)) component = 'Pulley';
    else if (/take-?up|counterweight/.test(t)) component = 'Take-up';
    else if (/chute|liner/.test(t)) component = 'Chute liner';
    else if (/belt/.test(t)) component = 'Belt';
    let condition = 'Reported defect';
    if (/seiz|stuck|not (turning|rotating)/.test(t)) condition = 'Seized, not rotating';
    else if (/noise|squeal|bearing/.test(t)) condition = 'Bearing noise';
    else if (/misalign|tracking|wander/.test(t)) condition = 'Belt mistracking';
    else if (/crack|damag|torn|rip/.test(t)) condition = 'Damaged';
    else if (/worn|wear/.test(t)) condition = 'Worn';
    return { num, conveyorOk, component, condition };
  }

  clarify(question) {
    this.send({ type: 'clarify', question });
    this.event('inspection', 'clarify', 'Clarification requested', question);
  }

  report(text) {
    const p = this.parse(text || '');
    if (!p.conveyorOk) { this.clarify('That conveyor is not on this route. Which station on CV-102 is it?'); return; }
    if (!p.num) { this.clarify('Which station on CV-102 is the defect at? Say "station one" to "station five".'); return; }
    const st = this.state.stations[p.num - 1];
    const severity = /seized/i.test(p.condition) ? 'high' : 'medium';
    const d = { id: this.id('D'), ts: this.now(), source: 'sim', station_id: st.id, component: p.component, condition: p.condition, severity, transcript: text };
    this.state.defects.push(d);
    st.status = 'defect';
    const comp = p.component.toLowerCase();
    const wo = {
      id: `WO-${24117 + this.state.work_orders.length}`, ts: this.now(), source: 'sim', defect_id: d.id, station_id: st.id,
      title: `Replace ${comp} at station ${st.number}`,
      priority: severity === 'high' ? 'P2 · within 24 h' : 'P3 · within 7 days',
      priority_reason: severity === 'high' ? 'A seized idler wears through the belt cover and can overheat.' : 'Schedule at the next planned stop.',
      status: 'draft',
      steps: [
        'Isolate, lock out and verify zero energy (authorized person)',
        `Remove the ${comp} and its frame bolts`,
        `Fit a new ${comp}; check it turns freely by hand`,
        'Remove locks, test-run and check belt tracking',
      ],
      requires_isolation: true, tool_id: 'impact_wrench_1in', est_trigger_min: 25,
    };
    this.state.work_orders.push(wo);
    this.event('inspection', 'defect', `Defect at station ${st.number}: ${p.component.toLowerCase()} — ${p.condition.toLowerCase()}`,
      `Spoken report: "${text}"`, { station_id: st.id, refs: { defect_id: d.id } });
    this.event('work_order', 'wo_draft', `${wo.id} drafted — ${wo.title}`, `${wo.priority}. Awaiting planner review.`,
      { station_id: st.id, refs: { work_order_id: wo.id } });
    this.snapshot();
  }

  selectTool(id) {
    const tool = this.state.tools.find((x) => x.id === id) || null;
    this.state.active_tool = tool;
    this.event('tool', 'tool_selected', tool ? `Tool selected by the technician: ${tool.name}` : 'Tool cleared',
      tool ? `${tool.vibration_m_s2} m/s² · ${tool.source}` : '');
    this.snapshot();
  }

  toolStart() {
    const s = {
      id: this.id('TS'), ts: this.now(), source: 'sim', t_start: +this.bandT.toFixed(2), t_end: null, trigger_s: 0,
      tool_id: this.state.active_tool ? this.state.active_tool.id : null, station_id: this.state.active_station,
      work_order_id: this.state.work_orders.length ? this.state.work_orders[this.state.work_orders.length - 1].id : null,
      confidence: 0.82, status: 'active', points: 0,
    };
    this.state.tool_sessions.push(s);
    this.activeSession = s;
    this.event('tool', 'tool_start', 'Tool activity started', 'Wrist IMU: sustained vibration with a steady grip.', { refs: { tool_session_id: s.id } });
    this.snapshot();
  }

  toolStop() {
    const s = this.activeSession;
    if (!s) return;
    this.activeSession = null;
    const observed = Math.max(1, this.bandT - s.t_start);
    s.t_end = +this.bandT.toFixed(2);
    s.observed_s = +observed.toFixed(1);
    s.trigger_s = 20 * 60; // demo time-lapse: 11 s on screen counts as 20 min
    s.confidence = 0.71;
    s.status = 'unresolved';
    this.event('tool', 'tool_stop', `Tool activity stopped — ${observed.toFixed(0)} s observed`,
      'Demo time-lapse: counted as 20 min of trigger time. Waiting for the technician to confirm.', { refs: { tool_session_id: s.id } });
    this.snapshot();
  }

  confirm(id, ok = true) {
    const s = this.state.tool_sessions.find((x) => x.id === id && x.status === 'unresolved')
      || this.state.tool_sessions.find((x) => x.status === 'unresolved');
    if (!s) return;
    if (!ok) {
      s.status = 'rejected';
      this.event('tool', 'tool_rejected', 'Technician rejected the tool session', 'No exposure recorded.', { refs: { tool_session_id: s.id } });
      this.snapshot();
      return;
    }
    s.status = 'confirmed';
    const tool = this.state.tools.find((t) => t.id === s.tool_id) || this.state.active_tool;
    const a = tool ? tool.vibration_m_s2 : 0;
    const pts = 2 * a * a * (s.trigger_s / 3600);
    s.points = +pts.toFixed(1);
    this.event('tool', 'tool_confirmed', `Technician confirmed: ${tool ? tool.name : 'tool'}, ${(s.trigger_s / 60).toFixed(0)} min`, '', { refs: { tool_session_id: s.id } });
    this.addExposure({ tool, trigger_s: s.trigger_s, points: pts, tool_session_id: s.id });
    this.snapshot();
  }

  addExposure({ tool, trigger_s, points, tool_session_id = null, note = '' }) {
    const ex = this.state.exposure;
    const before = ex.total_points;
    ex.records.push({
      id: this.id('EX'), ts: this.now(), source: 'sim', tool_session_id, tool_id: tool ? tool.id : null,
      tool_name: tool ? tool.name : 'Tool', vibration_m_s2: tool ? tool.vibration_m_s2 : null, trigger_s, points: +points.toFixed(1), note,
    });
    ex.total_points = +(before + points).toFixed(1);
    ex.status = ex.total_points >= 400 ? 'above_limit' : ex.total_points >= 100 ? 'above_action' : 'below_action';
    this.event('exposure', 'exposure_record', `+${points.toFixed(1)} pts — ${tool ? tool.name : 'tool'}`,
      `${tool ? tool.vibration_m_s2 : '?'} m/s² × ${(trigger_s / 60).toFixed(0)} min trigger time (points = 2 × a² × T). ${note}`.trim());
    if (before < 100 && ex.total_points >= 100) {
      this.event('exposure', 'eav_crossed', `Exposure action value reached — ${ex.total_points.toFixed(1)} pts`,
        'Screening estimate. Review controls before more vibrating-tool work today.');
    }
    if (before < 400 && ex.total_points >= 400) {
      this.event('exposure', 'elv_crossed', `Exposure limit value reached — ${ex.total_points.toFixed(1)} pts`,
        'Screening estimate. Stop vibrating-tool work for today.');
    }
  }

  addPrior() {
    const tool = this.state.tools.find((t) => t.id === 'angle_grinder');
    const s = {
      id: this.id('TS'), ts: this.now() - 3 * 3600, source: 'sim', t_start: 0, t_end: 3600, trigger_s: 3600,
      tool_id: tool.id, station_id: 'S5', work_order_id: null, confidence: 0.9, status: 'confirmed', points: 98,
    };
    this.state.tool_sessions.push(s);
    this.addExposure({ tool, trigger_s: 3600, points: 2 * tool.vibration_m_s2 ** 2 * 1, tool_session_id: s.id, note: 'Earlier today (demo).' });
    this.snapshot();
  }

  planRepair(body = {}) {
    const wos = this.state.work_orders;
    const wo = (body.station_id ? wos.filter((w) => w.station_id === body.station_id).pop() : null) || wos[wos.length - 1] || null;
    const plan = {
      station_id: body.station_id || (wo && wo.station_id) || this.state.active_station,
      task: body.task || (wo && wo.title) || 'Planned repair',
      tool_id: body.tool_id || (wo && wo.tool_id) || 'impact_wrench',
      est_trigger_min: Number(body.est_trigger_min || (wo && wo.est_trigger_min) || 20),
    };
    if (wo && (!body.task || body.task === wo.title)) wo.status = 'planned';
    const tool = this.state.tools.find((t) => t.id === plan.tool_id);
    const a = tool ? tool.vibration_m_s2 : 0;
    const cur = this.state.exposure.total_points;
    const add = 2 * a * a * (plan.est_trigger_min / 60);
    const proj = cur + add;
    const status = proj >= 400 ? 'over_limit' : proj >= 100 ? 'concern' : 'ok';
    const who = this.state.worker.id;
    this.state.recommendation = {
      projected_points: +proj.toFixed(1), added_points: +add.toFixed(1), status,
      headline: status === 'ok'
        ? `${plan.task} keeps ${who} below the exposure action value.`
        : `${plan.task} would take ${who} to ${proj.toFixed(0)} pts, above the exposure ${status === 'over_limit' ? 'limit' : 'action'} value.`,
      actions: status === 'ok'
        ? ['Proceed as planned. Exposure stays below 100 pts today.']
        : [
          'Review a lower-vibration method, such as a hydraulic torque wrench instead of an impact wrench.',
          'Consider qualified task reassignment to a technician with less exposure today.',
          'Or split the trigger time across shifts.',
        ],
      requires_supervisor_review: true,
      task: plan.task, tool_id: plan.tool_id, est_trigger_min: plan.est_trigger_min, station_id: plan.station_id, source: 'sim',
    };
    const st = this.stationById(plan.station_id);
    if (wo) {
      this.event('work_order', 'wo_planned', `${wo.id} planned — ${plan.est_trigger_min} min with ${tool ? tool.name : plan.tool_id}`,
        st ? `Station ${st.number} · ${st.name}` : '', { station_id: plan.station_id, refs: { work_order_id: wo.id } });
    }
    this.event('exposure', 'projection', `Projected exposure ${proj.toFixed(1)} pts (+${add.toFixed(1)})`,
      status === 'ok' ? 'Below the exposure action value.' : 'Planning concern. Supervisor review required.', { station_id: plan.station_id });
    this.snapshot();
  }

  // ---------- REST calls while in demo mode ----------
  handleApi(path, body = {}) {
    if (!this.running) return null;
    if (path === '/api/state') return structuredClone(this.state);
    if (path === '/api/station') { this.selectStation(body.station_id); return { ok: true }; }
    if (path === '/api/tool') { this.selectTool(body.tool_id); return { ok: true }; }
    if (path === '/api/report') {
      this.send({ type: 'transcript', text: body.text || '', final: true });
      this.report(body.text || '');
      return { ok: true };
    }
    if (path === '/api/mic') {
      this.state.mic = { on: !!body.on };
      this.event('system', 'mic', body.on ? 'Microphone on (demo: no audio is captured)' : 'Microphone off');
      this.snapshot();
      return { ok: true };
    }
    const m = path.match(/^\/api\/tool_session\/([^/]+)\/confirm$/);
    if (m) { this.confirm(decodeURIComponent(m[1]), body.confirmed !== false); return { ok: true }; }
    if (path === '/api/repair/plan') { this.planRepair(body); return { ok: true }; }
    return { ok: false, demo: true };
  }
}
