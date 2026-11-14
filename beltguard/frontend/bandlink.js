// bandlink.js — owned by S0. Two modes, one file:
//
// 1) Band link tab (bridge.html): connects band 07 over Chrome Web Bluetooth with the kit's own JS SDK
//    (copied unchanged into ./vendor/vexo) and streams every motion sample to the backend's /ws/ingest.
//    This tab is never reloaded by anyone, so the band stays connected while the dashboard reloads freely.
// 2) Dashboard (every other page): never touches Bluetooth. It shows ONE indicator in the top bar from the
//    server's real state (● Band 07 · Live / Reconnecting… / ○ Not connected [Connect]); Connect opens or
//    focuses the Band link tab.
//
// Nothing here is ever simulated.
// API (bridge mode): window.BandLink.connect(), .disconnect(), .setRate(208|416, 'both'), .led('#F97316'), .status
// Events: 'bandlink:status' {state, detail, rate}; 'bandlink:batch' {samples, sent, ingest} (bridge mode)

const BAND = 7;                                    // the team's band; never any other number
const IS_BRIDGE = /bridge\.html$/.test(location.pathname);
const BRIDGE_URL = '/bridge.html';
const BRIDGE_WINDOW = 'beltguard-band-link';

const status = { state: 'disconnected', detail: 'Band 07 not connected', rate: 0, transport: 'browser-ble' };
function emit(state, detail = status.detail) {
  status.state = state;
  status.detail = detail;
  window.dispatchEvent(new CustomEvent('bandlink:status', { detail: { ...status } }));
}

// ---------------------------------------------------------------- bridge mode: the real Bluetooth link
let band = null, ws = null, timer = null, rate = 50, queue = [], busy = false, lastIngestTry = 0, dropTimer = null, sent = 0;
const kit = IS_BRIDGE ? import('./vendor/vexo/index.js') : null;   // preload so the click stays inside the user gesture
if (kit) kit.catch(() => {});

function openIngest() {
  lastIngestTry = Date.now();
  return new Promise((resolve, reject) => {
    const s = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ingest`);
    s.onmessage = (ev) => handleCommand(ev.data);   // S2 sends LED feedback and rate commands down this socket
    s.onopen = () => resolve(s);
    s.onerror = () => reject(new Error('Backend ingest not reachable yet'));
    s.onclose = () => { if (ws === s) ws = null; };
  });
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

// Exposure alert on the wrist: the LED is locked blue while the mic is on (BAND_FACTS.md), so pause the
// mic for a few seconds, flash the colour, then turn the mic back on (voice.js keeps its Deepgram link open).
let alerting = false;
async function alertOnBand(colour, pattern, seconds) {
  if (!band || alerting) return;
  alerting = true;
  const micWasOn = !!(band.streams && band.streams.audio);
  try {
    if (micWasOn) await band.configure({ audio: false, micLevel: false });
    await band.setLed(colour, { pattern });
    await new Promise((r) => setTimeout(r, Math.min(10, Math.max(1, seconds)) * 1000));
    await band.ledOff().catch(() => {});
  } catch (e) { /* the band refused; carry on */ }
  finally {
    if (micWasOn) { try { await band.configure({ audio: true }); } catch (e) { /* voice.js retries */ } }
    alerting = false;
  }
}

// Band events (tap, double-tap, free-fall, tilt, orientation) go to the backend, which acts on them:
// double-tap confirms an uncertain tool session hands-free; free-fall raises a drop/fall safety alert.
const EVENT_KINDS = ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation'];
function wireEvents(b) {
  for (const kind of EVENT_KINDS) {
    try {
      b.on(kind, (e) => {
        send({ type: 'event', kind, t: e && e.t != null ? e.t : null, detail: e || null });
        window.dispatchEvent(new CustomEvent('bandlink:event', { detail: { kind, at: Date.now() } }));
      });
    } catch (err) { /* this firmware does not report that event */ }
  }
}
// Backend → band: {"type":"command","cmd":"led","colour":"#ffb000","pattern":"blink","period":400,"brightness":1}
// or {"type":"command","cmd":"rate","rate":416,"sensors":"both"}. LED commands fail while the mic is on (BAND_FACTS.md).
async function handleCommand(raw) {
  let m;
  try { m = JSON.parse(raw); } catch (e) { return; }
  if (!m || m.type !== 'command' || !band) return;
  try {
    if (m.cmd === 'led' && m.colour) {
      const opts = { pattern: m.pattern || 'solid' };
      if (m.period != null) opts.period = m.period;
      if (m.brightness != null) opts.brightness = m.brightness;
      await band.setLed(m.colour, opts);
    } else if (m.cmd === 'rate' && m.rate) {
      await setRate(m.rate, m.sensors || 'both');
    } else if (m.cmd === 'alert') {
      await alertOnBand(m.colour || '#EF4444', m.pattern || 'blink', Number(m.seconds) || 4);
    }
  } catch (e) { /* the band refused (e.g. LED while the mic is on); keep streaming */ }
}
function stopStreaming() { clearInterval(timer); timer = null; queue = []; }
function finalDisconnect(detail) {
  clearTimeout(dropTimer);
  stopStreaming();
  band = null;
  send({ type: 'connection', state: 'disconnected', transport: 'browser-ble', band: BAND });
  emit('disconnected', detail);
}
function startStreaming() {
  stopStreaming();
  timer = setInterval(() => {
    if (!ws && Date.now() - lastIngestTry > 2000) {
      openIngest().then((s) => {
        ws = s;
        send({ type: 'connection', state: band ? 'connected' : 'disconnected', transport: 'browser-ble', band: BAND });
        send({ type: 'rate', rate, sensors: 'both' });
      }).catch(() => {});
    }
    if (!queue.length) return;
    const samples = queue.splice(0, queue.length);
    if (ws) { send({ type: 'motion', rate, samples }); sent += samples.length; }
    window.dispatchEvent(new CustomEvent('bandlink:batch', { detail: { samples, sent, ingest: !!ws } }));
  }, 100);
}

async function connectInner() {
  emit('connecting', 'Pick Vexo-HACK-07 in the Bluetooth list');
  let Band;
  try { ({ Band } = await kit); } catch (e) { emit('error', 'Band library failed to load'); throw e; }
  let b;
  try {
    b = await Band.connect(BAND, { autoReconnect: true });
  } catch (e) {
    emit('disconnected', e && e.message ? e.message : String(e));
    throw e;
  }
  if (b.number !== BAND) { await b.disconnect().catch(() => {}); emit('error', 'That is not band 07'); return status; }
  band = b;
  try { ws = await openIngest(); } catch (e) { /* retried every 2 s while streaming */ }
  try { await band.setMotion({ accelRange: 16, gyroRange: 2000 }); } catch (e) { /* keep defaults */ }
  rate = 416;                                       // BAND_FACTS.md: 416 Hz both sensors, 0 lost over BLE
  try { await band.configure({ motion: rate, sensors: 'both', events: true }); } catch (e) {
    rate = 50;
    try { await band.configure({ motion: 50, sensors: 'both', events: true }); } catch (e2) { /* keep current */ }
  }
  band.on('motion', (m) => {
    queue.push({ t: m.t, n: m.n, ax: m.ax, ay: m.ay, az: m.az, gx: m.gx ?? null, gy: m.gy ?? null, gz: m.gz ?? null });
    if (queue.length > 8000) queue.splice(0, queue.length - 8000);
  });
  wireEvents(band);
  band.on('disconnect', (d) => {
    send({ type: 'connection', state: 'disconnected', transport: 'browser-ble', band: BAND, reason: d && d.reason });
    if (d && d.reason === 'closed') return finalDisconnect('Band 07 not connected');
    emit('connecting', 'Link dropped, reconnecting to band 07…');
    clearTimeout(dropTimer);
    dropTimer = setTimeout(() => { if (status.state !== 'connected') finalDisconnect('Band 07 lost; click Connect'); }, 40000);
  });
  band.on('reconnecting', () => emit('connecting', 'Reconnecting to band 07…'));
  band.on('reconnected', (d) => {
    clearTimeout(dropTimer);
    if (d && d.failed) return finalDisconnect('Could not reconnect; click Connect');
    send({ type: 'connection', state: 'connected', transport: 'browser-ble', band: BAND });
    send({ type: 'rate', rate, sensors: 'both' });
    emit('connected', 'Band 07 connected over Bluetooth');
  });
  startStreaming();
  send({ type: 'connection', state: 'connected', transport: 'browser-ble', band: BAND });
  send({ type: 'rate', rate, sensors: 'both' });
  status.rate = rate;
  emit('connected', 'Band 07 connected over Bluetooth');
  return status;
}

async function connect() {
  if (!IS_BRIDGE) { openBridge(); return status; }
  if (busy || status.state === 'connected') return status;
  if (band) { await band.disconnect().catch(() => {}); band = null; }
  busy = true;
  try { return await connectInner(); } finally { busy = false; }
}
async function setRate(r, sensors = 'both') {
  if (!band) return status;
  await band.configure({ motion: r, sensors });
  rate = r;
  status.rate = r;
  send({ type: 'rate', rate: r, sensors });
  emit(status.state, status.detail);
  return status;
}
async function led(hex) {                          // fails while the mic or mic-level meter is on (BAND_FACTS.md)
  if (!band) return false;
  try { await band.setLed(hex); return true; } catch (e) { return false; }
}
async function disconnect() {
  const b = band;
  finalDisconnect('Band 07 not connected');
  if (b) await b.disconnect().catch(() => {});
}

function openBridge() {
  const w = window.open(BRIDGE_URL, BRIDGE_WINDOW);
  if (w) w.focus();
}

window.BandLink = { connect, disconnect, setRate, led, status, openBridge, isBridge: IS_BRIDGE, getBand: () => band };
export { connect, disconnect, setRate, led, status, openBridge };

// ---------------------------------------------------------------- dashboard mode: one honest indicator
function hideOtherBadges(bar, mine) {              // no other badge in the bar may contradict this one
  bar.querySelectorAll('*').forEach((el) => {
    if (mine.contains(el) || el.children.length) return;
    if (/band\s*0?7|simulated|connecting|reconnect|backend|offline|no band/i.test(el.textContent || '')) {
      const pill = el.closest('.badge, [class*="badge"], [class*="pill"], [class*="status"], [class*="chip"]') || el;
      if (!mine.contains(pill) && bar.contains(pill)) pill.style.display = 'none';
    }
  });
}

function mountIndicator() {
  if (document.getElementById('bandStatus')) return;
  const bar = document.querySelector('header') || document.body;
  const pill = document.createElement('div');
  pill.id = 'bandStatus';
  pill.setAttribute('role', 'status');
  Object.assign(pill.style, {
    display: 'inline-flex', alignItems: 'center', gap: '8px', height: '32px', padding: '0 6px 0 12px', marginLeft: '12px',
    border: '1px solid #E4E4E7', borderRadius: '999px', background: '#FFFFFF',
    font: '500 13px Geist, system-ui, sans-serif', color: '#09090B', whiteSpace: 'nowrap',
  });
  const dot = document.createElement('span');
  Object.assign(dot.style, { width: '8px', height: '8px', borderRadius: '50%', flex: 'none' });
  const label = document.createElement('span');
  const action = document.createElement('button');
  action.type = 'button';
  Object.assign(action.style, { height: '24px', border: '0', borderRadius: '999px', cursor: 'pointer', font: '500 12px Geist, system-ui, sans-serif', padding: '0 10px' });
  action.addEventListener('click', openBridge);
  pill.append(dot, label, action);

  let server = { state: 'unknown' };
  let lastLive = 0;
  function render() {
    const live = server.state === 'connected' && !server.simulated;
    const simulated = !!server.simulated || server.state === 'simulated';   // only when the team switches to sim on purpose
    if (live) lastLive = Date.now();
    let colour = '#A1A1AA', text = 'Band 07 · Not connected', act = 'Connect';
    if (live) { colour = '#10B981'; text = 'Band 07 · Live'; act = ''; }
    else if (simulated) { colour = '#F59E0B'; text = 'Simulated band · testing'; act = ''; }
    else if (Date.now() - lastLive < 12000) { colour = '#F59E0B'; text = 'Band 07 · Reconnecting…'; act = ''; }
    dot.style.background = colour;
    label.textContent = text;
    action.textContent = act;
    action.title = 'Open the Band link tab (it holds the Bluetooth connection)';
    if (act === 'Connect') Object.assign(action.style, { background: '#18181B', color: '#FAFAFA' });
    else Object.assign(action.style, { background: '#F4F4F5', color: '#3F3F46' });
    pill.title = live ? `Band 07 streaming${server.rate ? ` at ${server.rate} Hz` : ''}` : 'Band 07 is not streaming';
    hideOtherBadges(bar, pill);
  }
  async function poll() {
    try { server = (await (await fetch('/api/state', { cache: 'no-store' })).json()).connection || {}; }
    catch (e) { server = { state: 'unknown' }; }
    render();
  }
  bar.appendChild(pill);
  render();
  poll();
  setInterval(poll, 2000);
  setInterval(() => hideOtherBadges(bar, pill), 1000);
}

// ---------- live transcript dock (dashboard) — shows what the technician says, live ----------
// Every final sentence from band 07's mic (/ws {"type":"transcript"}) plus BeltGuard's replies
// (/ws {"type":"assistant"}). Steps aside automatically once S8's Live panel ([data-live-panel]) exists.
function mountTranscriptDock() {
  if (IS_BRIDGE || document.getElementById('bgTranscript')) return;
  if (document.querySelector('[data-live-panel]')) return;
  const dock = document.createElement('aside');
  dock.id = 'bgTranscript';
  dock.setAttribute('aria-label', 'Live transcript');
  // Resizable: drag the top edge (height), right edge (width) or top-right corner. Size is remembered.
  const SIZE_KEY = 'beltguard.txDock';
  let size = { w: 440, h: 300 };
  try { const s = JSON.parse(localStorage.getItem(SIZE_KEY) || 'null'); if (s && s.w > 0 && s.h > 0) size = s; } catch (e) { /* storage blocked */ }
  const clampSize = () => {
    size.w = Math.round(Math.max(300, Math.min(size.w, window.innerWidth - 48)));
    size.h = Math.round(Math.max(120, Math.min(size.h, window.innerHeight * 0.85)));
  };
  clampSize();
  Object.assign(dock.style, {
    position: 'fixed', left: '24px', bottom: '24px', width: size.w + 'px', height: size.h + 'px',
    display: 'flex', flexDirection: 'column', background: 'rgba(255,255,255,0.97)', border: '1px solid #E4E4E7',
    borderRadius: '12px', boxShadow: '0 4px 16px rgb(0 0 0 / .08)', font: '13px/1.45 Geist, system-ui, sans-serif',
    color: '#09090B', zIndex: '30', overflow: 'hidden',
  });
  dock.innerHTML = '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #F4F4F5">'
    + '<span style="width:8px;height:8px;border-radius:50%;background:#10B981;flex:none"></span>'
    + '<b style="font-weight:600">Live transcript</b><span style="color:#71717A">band 07 mic · every sentence</span>'
    + '<button id="bgTxMin" type="button" style="margin-left:auto;border:0;background:none;color:#71717A;cursor:pointer;font:500 12px Geist,system-ui,sans-serif">Hide</button></div>'
    + '<ol id="bgTxList" style="list-style:none;margin:0;padding:10px 14px 12px;overflow:auto;display:flex;flex-direction:column;gap:8px;flex:1;min-height:0"></ol>';
  document.body.appendChild(dock);
  const list = dock.querySelector('#bgTxList');
  const applySize = () => {
    clampSize();
    dock.style.width = size.w + 'px';
    if (list.style.display !== 'none') dock.style.height = size.h + 'px';
  };
  function grip(css, cursor, dir, inner) {
    const g = document.createElement('div');
    g.title = 'Drag to resize';
    g.style.cssText = 'position:absolute;z-index:2;touch-action:none;cursor:' + cursor + ';' + css;
    if (inner) g.innerHTML = inner;
    g.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      g.setPointerCapture(e.pointerId);
      const x0 = e.clientX, y0 = e.clientY, w0 = dock.offsetWidth, h0 = dock.offsetHeight;
      const move = (ev) => {
        if (dir.includes('y') && list.style.display === 'none') { list.style.display = 'flex'; dock.querySelector('#bgTxMin').textContent = 'Hide'; }
        if (dir.includes('x')) size.w = w0 + (ev.clientX - x0);
        if (dir.includes('y')) size.h = h0 + (y0 - ev.clientY);      // anchored at the bottom: drag up = taller
        applySize();
      };
      const up = () => {
        g.removeEventListener('pointermove', move);
        g.removeEventListener('pointerup', up);
        g.removeEventListener('pointercancel', up);
        try { localStorage.setItem(SIZE_KEY, JSON.stringify(size)); } catch (err) { /* storage blocked */ }
      };
      g.addEventListener('pointermove', move);
      g.addEventListener('pointerup', up);
      g.addEventListener('pointercancel', up);
    });
    dock.appendChild(g);
  }
  grip('left:0;right:16px;top:0;height:9px', 'ns-resize', 'y',
    '<span style="position:absolute;left:50%;top:3px;width:36px;height:4px;margin-left:-18px;border-radius:2px;background:#D4D4D8"></span>');
  grip('top:16px;bottom:0;right:0;width:9px', 'ew-resize', 'x');
  grip('top:0;right:0;width:16px;height:16px', 'nesw-resize', 'xy');
  window.addEventListener('resize', applySize);
  const fmt = (ts) => new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const seen = new Set();
  const clip = (s) => (s.length > 200 ? s.slice(0, 197) + '…' : s);
  function add(who, text, ts, chip, key) {
    if (!text) return;
    if (key) { if (seen.has(key)) return; seen.add(key); }
    const li = document.createElement('li');
    Object.assign(li.style, { display: 'grid', gridTemplateColumns: '64px 1fr', gap: '8px' });
    const isBG = who === 'BeltGuard';
    li.innerHTML = '<time style="font:11px \'Geist Mono\',ui-monospace,monospace;color:#A1A1AA;padding-top:2px"></time>'
      + '<div><span class="who" style="font-weight:600"></span> <span class="tx"></span> <span class="chip"></span></div>';
    li.querySelector('time').textContent = fmt(ts);
    const w = li.querySelector('.who');
    w.textContent = who + ':';
    w.style.color = isBG ? '#047857' : '#09090B';
    const tx = li.querySelector('.tx');
    tx.textContent = clip(String(text));
    if (!isBG && !chip) tx.style.color = '#52525B';
    if (chip) {
      const c = li.querySelector('.chip');
      c.textContent = chip;
      Object.assign(c.style, { display: 'inline-block', marginLeft: '6px', padding: '0 8px', borderRadius: '999px', background: '#ECFDF5', color: '#047857', fontSize: '11.5px', fontWeight: '500' });
    }
    list.appendChild(li);
    while (list.children.length > 14) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }
  const chipFor = (r) => (r.result_status === 'ok' ? 'Defect logged' : r.result_status === 'clarify' ? 'Question asked' : (r.intent && r.intent !== 'none' ? 'Done: ' + String(r.intent).replace(/_/g, ' ') : null));
  fetch('/api/transcripts?limit=6', { cache: 'no-store' }).then((r) => r.json()).then((j) => {
    (j.rows || []).slice().reverse().forEach((r) => {
      add('Technician', r.text, r.ts, chipFor(r), r.id);
      if (r.response) add('BeltGuard', r.response, r.ts, null, r.id + ':r');
    });
  }).catch(() => {});
  function open() {
    let s;
    try { s = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`); } catch (e) { setTimeout(open, 2000); return; }
    s.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (document.querySelector('[data-live-panel]')) { dock.remove(); s.close(); return; }   // S8's panel took over
      if (m.type === 'transcript' && m.final !== false && m.text) add('Technician', m.text, m.ts, m.acted || null, m.transcript_id || m.id || ('t:' + m.ts + m.text.slice(0, 16)));
      else if (m.type === 'assistant' && m.text) add('BeltGuard', m.text, m.ts, null, 'a:' + (m.ts || '') + m.text.slice(0, 16));
    };
    s.onclose = () => { if (document.getElementById('bgTranscript')) setTimeout(open, 2000); };
  }
  open();
  dock.querySelector('#bgTxMin').addEventListener('click', () => {
    const hidden = list.style.display === 'none';
    list.style.display = hidden ? 'flex' : 'none';
    dock.style.height = hidden ? size.h + 'px' : 'auto';
    dock.querySelector('#bgTxMin').textContent = hidden ? 'Hide' : 'Show';
  });
}

function start() {
  if (IS_BRIDGE) {                                  // bridge.html draws its own UI (bridge.js)
    // Keep /ws/ingest open from page load, before Connect, so the backend knows a Band link page is
    // present (it pauses its own Python Bluetooth search then, even during Chrome's device picker).
    setInterval(() => {
      if (!ws && Date.now() - lastIngestTry > 2000) {
        openIngest().then((s) => { ws = s; send({ type: 'connection', state: band ? 'connected' : 'disconnected', transport: 'browser-ble', band: BAND }); }).catch(() => {});
      }
    }, 1000);
    return;
  }
  const old = document.getElementById('bandConnect');
  if (old) old.remove();
  mountIndicator();
  mountTranscriptDock();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
