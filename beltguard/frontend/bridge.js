// bridge.js — owned by S0. Draws the Band link tab: connect button, live readings like the kit dashboard.
// All data comes from band 07 through bandlink.js (Web Bluetooth). Nothing is simulated.
import { connect, disconnect, status } from './bandlink.js';

const $ = (id) => document.getElementById(id);
const COLOURS = ['#2563EB', '#10B981', '#F59E0B'];
// Raw readings to the dashboard tabs (same origin) so their charts draw every sample smoothly, like the kit.
const motionCh = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('beltguard-motion') : null;
const WINDOW_S = 6;                                  // seconds of history on each chart
const acc = [], gyr = [];                            // [t_seconds, x, y, z]
let lastT = null, hzSamples = [];

function setState(s) {
  const colour = { connected: '#10B981', connecting: '#F59E0B', error: '#EF4444' }[s.state] || '#A1A1AA';
  $('dot').style.background = colour;
  $('detail').textContent = s.state === 'connected' ? `Connected over Bluetooth · ${s.rate || ''} Hz requested`
    : s.state === 'connecting' ? (s.detail || 'Connecting…') : (s.detail || 'Not connected');
  // The main button only ever connects. When connected it reads "Connected" and does nothing, so a
  // double-click can never connect-then-disconnect. Disconnecting is a separate small link with a confirm.
  const btn = $('connect');
  btn.textContent = s.state === 'connected' ? 'Connected ✓' : s.state === 'connecting' ? 'Connecting…' : 'Connect band 07';
  btn.disabled = s.state === 'connecting' || s.state === 'connected';
  btn.classList.toggle('secondary', s.state === 'connected');
  let off = $('disconnectLink');
  if (!off) {
    off = document.createElement('button');
    off.id = 'disconnectLink';
    off.type = 'button';
    off.textContent = 'Disconnect';
    Object.assign(off.style, { marginLeft: '12px', border: '0', background: 'none', color: '#71717A', font: '500 13px Geist, system-ui, sans-serif', textDecoration: 'underline', cursor: 'pointer' });
    off.addEventListener('click', () => {
      if (window.confirm('Disconnect band 07? It stops streaming to BeltGuard and the band goes to sleep until you double-tap it.')) disconnect().catch(() => {});
    });
    $('detail').after(off);
  }
  off.hidden = s.state !== 'connected';
}

$('connect').addEventListener('click', () => {
  if (status.state !== 'connected') connect().catch(() => {});
});
window.addEventListener('bandlink:status', (e) => setState(e.detail));
setState(status);

window.addEventListener('bandlink:batch', (e) => {
  const { samples, sent, ingest } = e.detail;
  if (motionCh && samples.length) { try { motionCh.postMessage(samples); } catch (err) { /* channel closed */ } }
  $('sent').textContent = sent.toLocaleString();
  $('ingest').textContent = ingest ? 'Live' : 'Waiting';
  $('ingest').style.color = ingest ? '#10B981' : '#F59E0B';
  for (const s of samples) {
    const t = s.t / 1000;
    acc.push([t, s.ax, s.ay, s.az]);
    if (s.gx != null) gyr.push([t, s.gx, s.gy, s.gz]);
  }
  if (samples.length) {
    const first = samples[0].t / 1000, last = samples[samples.length - 1].t / 1000;
    if (lastT != null && last > lastT) hzSamples.push([samples.length, last - lastT]);
    lastT = last;
    if (hzSamples.length > 20) hzSamples.shift();
    const n = hzSamples.reduce((a, [k]) => a + k, 0), dt = hzSamples.reduce((a, [, d]) => a + d, 0);
    if (dt > 0) $('hz').textContent = Math.round(n / dt);
    void first;
  }
  const cut = (arr) => { if (!arr.length) return; const end = arr[arr.length - 1][0]; while (arr.length && arr[0][0] < end - WINDOW_S) arr.shift(); };
  cut(acc); cut(gyr);
  // movement: RMS of accel-magnitude change over the last 0.5 s, in mg
  const end = acc.length ? acc[acc.length - 1][0] : 0;
  const recent = acc.filter((a) => a[0] > end - 0.5).map((a) => Math.hypot(a[1], a[2], a[3]));
  if (recent.length > 4) {
    const mean = recent.reduce((x, y) => x + y, 0) / recent.length;
    $('move').textContent = Math.round(Math.sqrt(recent.reduce((x, v) => x + (v - mean) ** 2, 0) / recent.length) * 1000);
  }
  const lastA = acc[acc.length - 1], lastG = gyr[gyr.length - 1];
  if (lastA) $('accv').innerHTML = ['x', 'y', 'z'].map((k, i) => `<span><i style="background:${COLOURS[i]}"></i>${k} ${lastA[i + 1].toFixed(3)}</span>`).join('');
  if (lastG) $('gyrv').innerHTML = ['x', 'y', 'z'].map((k, i) => `<span><i style="background:${COLOURS[i]}"></i>${k} ${lastG[i + 1].toFixed(1)}</span>`).join('');
});

function draw(canvas, data, fallbackRange) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.strokeStyle = '#F4F4F5'; g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { const y = Math.round((h * i) / 4) + 0.5; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
  if (data.length < 2) {
    g.fillStyle = '#A1A1AA'; g.font = '13px Geist, system-ui, sans-serif'; g.textAlign = 'center';
    g.fillText(status.state === 'connected' ? 'Waiting for readings…' : 'Connect band 07 to see live readings', w / 2, h / 2 + 4);
    return;
  }
  let lo = Infinity, hi = -Infinity;
  for (const d of data) for (let i = 1; i < 4; i++) { if (d[i] < lo) lo = d[i]; if (d[i] > hi) hi = d[i]; }
  if (hi - lo < fallbackRange) { const mid = (hi + lo) / 2; lo = mid - fallbackRange / 2; hi = mid + fallbackRange / 2; }
  const t1 = data[data.length - 1][0], t0 = t1 - WINDOW_S;
  const X = (t) => ((t - t0) / WINDOW_S) * w, Y = (v) => h - 8 - ((v - lo) / (hi - lo)) * (h - 16);
  for (let i = 1; i < 4; i++) {
    g.strokeStyle = COLOURS[i - 1]; g.lineWidth = 1.5; g.beginPath();
    data.forEach((d, k) => { const x = X(d[0]), y = Y(d[i]); if (k) g.lineTo(x, y); else g.moveTo(x, y); });
    g.stroke();
  }
}
(function loop() {
  draw($('acc'), acc, 0.2);
  draw($('gyr'), gyr, 20);
  requestAnimationFrame(loop);
})();

// Band events (tap, double-tap, free-fall…) shown under the connection line, so taps visibly register.
window.addEventListener('bandlink:event', (e) => {
  let el = document.getElementById('lastEvent');
  if (!el) {
    el = document.createElement('div');
    el.id = 'lastEvent';
    el.className = 'state';
    el.style.marginTop = '4px';
    document.getElementById('detail').parentElement.after(el);
  }
  const at = new Date(e.detail.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  el.textContent = `Last band event: ${e.detail.kind} at ${at}`;
});
