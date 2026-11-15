// voice.js — owned by S0. ALWAYS-ON voice: whenever band 07 is connected, its microphone streams to
// Deepgram (the kit's own deepgramLink, copied unchanged into ./vendor/speech) and every finished
// sentence goes to BeltGuard (/api/report), which records it and acts on it (defect + work-order
// draft, or a clarifying question). It starts by itself, restarts after band or Deepgram drops, and
// restarts the audio if it ever goes quiet. The key comes from beltguard/.env via /api/voice/config.
import { deepgramLink } from './vendor/speech/deepgram.js';

const KEY_STORE = 'beltguard.deepgramKey';          // optional browser override
const LOOKS_LIKE_KEY = /^[\w.~-]{1,200}$/;
const $ = (sel) => document.querySelector(sel);
let link = null, band = null, onAudio = null, listening = false, busy = false;
let wanted = true;                                   // always on unless the user pauses
let lastFrame = 0, retryAt = 0, lastProblem = '';
const liveCh = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('beltguard-voice') : null;   // dashboards show partial words

const PROBLEMS = {
  refused: 'Deepgram refused the key.',
  closed: 'Deepgram connection closed; reconnecting…',
  reported: 'Deepgram reported a problem; reconnecting…',
  'no-key': 'No Deepgram key found in BeltGuard config.',
  'no-socket': 'Could not reach Deepgram (internet?); retrying…',
};

async function serverKey() {
  try {
    const c = await (await fetch('/api/voice/config', { cache: 'no-store' })).json();
    return c && c.deepgram_key ? String(c.deepgram_key) : '';
  } catch (e) { return ''; }
}

async function findBand() {
  if (window.BandLink && typeof window.BandLink.getBand === 'function') {
    const b = window.BandLink.getBand();
    if (b) return b;
  }
  if (!window.BandLink || window.BandLink.status.state !== 'connected') return null;
  const { Band } = await import('./vendor/vexo/index.js');      // same instance bandlink.js uses
  return new Promise((resolve) => {
    const orig = Band.prototype._emit;
    let done = false;
    const finish = (b) => { if (done) return; done = true; Band.prototype._emit = orig; resolve(b); };
    Band.prototype._emit = function (...args) { if (this.number === 7) finish(this); return orig.apply(this, args); };
    setTimeout(() => finish(null), 2500);
  });
}

function setStatus(text, tone = 'idle') {
  if (!$('#vStatus')) return;
  $('#vStatus').textContent = text;
  $('#vDot').style.background = { ok: '#10B981', warn: '#F59E0B', bad: '#EF4444' }[tone] || '#A1A1AA';
}

function summarize(j, ok) {
  if (!ok) return (j && (j.detail || j.error)) || 'Server refused it';
  if (j && j.response_text) return j.response_text;          // a voice action BeltGuard carried out (S2 + S4 intents)
  const res = (j && (j.result || j)) || {};
  const st = res.status || j.status;
  if (st === 'ok' || res.defect || j.defect) {
    const d = res.defect || j.defect || {};
    const wo = res.work_order || j.work_order || {};
    return `Defect logged${d.station_id ? ' at ' + d.station_id : ''} · ${wo.id ? wo.id + ' drafted' : 'work order drafted'}`;
  }
  if (st === 'clarify') return 'Question back: ' + (res.question || j.question || 'which station?');
  return 'Recorded';
}

async function addLine(text) {
  const li = document.createElement('li');
  li.innerHTML = '<time></time><span class="t"></span><span class="r">Recording…</span>';
  li.querySelector('time').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  li.querySelector('.t').textContent = text;
  $('#vLines').prepend(li);
  try {
    const r = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, source: 'band-mic' }) });
    const j = await r.json().catch(() => ({}));
    li.querySelector('.r').textContent = summarize(j, r.ok);
  } catch (e) {
    li.querySelector('.r').textContent = 'Not recorded: server unreachable';
  }
}

// Words still being spoken: the newest line of the transcript list, updated on every partial result.
function showLive(text) {
  const t = String(text || '').trim();
  let li = document.getElementById('vLive');
  if (!t) { if (li) li.remove(); return; }
  if (!li) {
    li = document.createElement('li');
    li.id = 'vLive';
    li.innerHTML = '<time style="color:#059669;font-weight:600">now</time><span class="t" style="color:#3F3F46;font-style:italic"></span>'
      + '<span class="r" style="background:#ECFDF5;color:#047857">Hearing…</span>';
    $('#vLines').prepend(li);
  }
  li.querySelector('.t').textContent = t;
}

// Instant job actions: as soon as the words-so-far contain a job step, send just that clause to BeltGuard,
// without waiting for Deepgram to close the sentence (background talk can keep it open for many seconds).
// Each step group is sent at most once per 30 s; the later full sentence is a no-op on the server.
const JOB_GROUPS = [
  ['test run', /\b(test run|remov\w* (the )?isolation|isolation('?s| is)? removed)\b/i],
  ['job done', /\b(job('?s| is)? (done|complete|finished)|all done|ready for sign[- ]?off|work('?s| is)? (done|complete|finished))\b/i],
  ['guard on', /\b(guard('?s| is)? (back )?on|refit\w* (the )?guard|put (the )?guard back)\b/i],
  ['guard off', /\b(guard('?s| is)? (off|removed)|remov\w* (the )?guard|tak\w* (the )?guard off)\b/i],
  ['idler', /\b(new idler|idler('?s| is)? (fitted|in|installed|replaced|changed|done)|replac\w* (the )?idler|fitt?\w* (the )?(new )?idler|chang\w* (the )?idler)\b/i],
  ['isolation', /\b(isolat\w*|lock(ed)?[ -]?out|loto)\b/i],
];
const liveSentAt = {};
function liveAct(text) {
  const clauses = String(text || '').split(/(?<=[.?!])\s+/).slice(-3);      // only the newest words
  for (let i = clauses.length - 1; i >= 0; i--) {
    const clause = clauses[i].trim();
    if (!clause || clause.length > 160) continue;
    const hit = JOB_GROUPS.find(([, re]) => re.test(clause));
    if (!hit) continue;
    const group = hit[0];
    if (Date.now() - (liveSentAt[group] || 0) < 30000) return;
    liveSentAt[group] = Date.now();
    const chip = document.querySelector('#vLive .r');
    if (chip) chip.textContent = 'Acting: ' + group + '…';
    fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: clause, source: 'band-mic' }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => { const c = document.querySelector('#vLive .r'); if (c) c.textContent = summarize(j, ok); })
      .catch(() => {});
    return;
  }
}

// Tell the backend the mic state (passive: no band command). Re-sent every 30 s so it recovers after restarts.
let lastMicReport = 0;
function reportMic(on) {
  lastMicReport = Date.now();
  fetch('/api/mic/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on, source: 'band-link' }) }).catch(() => {});
}

function closeLink() {
  if (link) { try { link.stop(); } catch (e) { /* closing */ } link = null; }
}

async function start() {
  const key = (await serverKey()) || localStorage.getItem(KEY_STORE) || '';
  if (!key) { setStatus(PROBLEMS['no-key'], 'bad'); retryAt = Date.now() + 15000; return; }
  const b = await findBand();
  if (!b) { setStatus('Waiting for band 07 to connect…', 'warn'); return; }
  if (band && band !== b && onAudio) { try { band.off('audio', onAudio); } catch (e) { /* old band */ } onAudio = null; }
  band = b;
  closeLink();
  setStatus('Starting the microphone…', 'warn');
  link = deepgramLink({ key });
  link.start({
    onText: ({ final, text }) => {
      try { if (liveCh) liveCh.postMessage({ final: !!final, text: text || '' }); } catch (e) { /* channel closed */ }   // live words to dashboards
      if (!final) { showLive(text); liveAct(text); return; }
      showLive('');
      if (text && text.trim()) addLine(text.trim());
    },
    onError: (problem) => {
      lastProblem = problem;
      listening = false;
      closeLink();
      setStatus(PROBLEMS[problem] || PROBLEMS.reported, problem === 'refused' ? 'bad' : 'warn');
      retryAt = Date.now() + (problem === 'refused' || problem === 'no-key' ? 30000 : 2000);
    },
  });
  if (!onAudio) {
    onAudio = (frame) => { lastFrame = Date.now(); if (frame && frame.opus && link) link.packet(frame.opus); };
    band.on('audio', onAudio);
  }
  try {
    await band.configure({ audio: true });
  } catch (e) {
    setStatus('The band would not start audio; retrying…', 'warn');
    closeLink();
    retryAt = Date.now() + 3000;
    return;
  }
  lastFrame = Date.now();
  listening = true;
  lastProblem = '';
  reportMic(true);
  setStatus('Always listening · band 07 mic on · every sentence is recorded', 'ok');
}

async function pause() {
  wanted = false;
  listening = false;
  closeLink();
  reportMic(false);
  if (band) { try { await band.configure({ audio: false, micLevel: false }); } catch (e) { /* band gone */ } }
  $('#vInterim').textContent = '';
  setStatus('Paused. Tap Resume to keep transcribing', 'idle');
}

async function ensure() {                            // the always-on loop
  if (busy || !wanted) return;
  const connected = window.BandLink && window.BandLink.status.state === 'connected';
  if (!connected) {
    if (listening || link) { listening = false; closeLink(); }
    setStatus('Waiting for band 07 to connect…', 'warn');
    return;
  }
  const quiet = listening && Date.now() - lastFrame > 8000;   // audio stopped (e.g. after a reconnect)
  if (listening && !quiet && link) {
    if (Date.now() - lastMicReport > 30000) reportMic(true);
    return;
  }
  if (Date.now() < retryAt) return;
  busy = true;
  try { await start(); } finally { busy = false; }
}

function renderButton() {
  const btn = $('#vToggle');
  if (!btn) return;
  btn.textContent = wanted ? 'Pause' : 'Resume listening';
  btn.classList.toggle('secondary', wanted);
}

function mount() {
  if (document.getElementById('voiceCard')) return;
  const style = document.createElement('style');
  style.textContent = `
    #voiceCard .interim{display:none}
    #voiceCard .lines{list-style:none;margin:8px 0 0;padding:0;max-height:340px;overflow:auto}
    #voiceCard .lines li{display:grid;grid-template-columns:80px 1fr auto;gap:12px;align-items:baseline;padding:10px 0;border-top:1px solid #F4F4F5}
    #voiceCard .lines time{font:12px 'Geist Mono',ui-monospace,monospace;color:#A1A1AA}
    #voiceCard .lines .r{font-size:12.5px;color:#3F3F46;background:#F4F4F5;border-radius:999px;padding:2px 10px;white-space:nowrap}
    @media (max-width:640px){#voiceCard .lines li{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);
  const card = document.createElement('section');
  card.className = 'card';
  card.id = 'voiceCard';
  card.innerHTML = `
    <div class="hero">
      <div>
        <h2 style="margin:0">Voice inspection</h2>
        <div class="state"><span class="dot" id="vDot"></span><span id="vStatus">Starting…</span></div>
      </div>
      <button class="btn secondary" id="vToggle" type="button">Pause</button>
    </div>
    <p class="hint">While band 07 is connected its microphone is always on. Deepgram turns speech into text, and BeltGuard records every finished sentence and acts on it: a defect report becomes a work-order draft, an unclear one gets a question back.</p>
    <div id="vInterim" class="interim"></div>
    <ol id="vLines" class="lines"></ol>`;
  const first = document.querySelector('main .card');
  if (first && first.nextSibling) first.parentNode.insertBefore(card, first.nextSibling); else (document.querySelector('main') || document.body).appendChild(card);
  $('#vToggle').addEventListener('click', async () => {
    if (wanted) await pause(); else { wanted = true; retryAt = 0; ensure(); }
    renderButton();
  });
  renderButton();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
window.addEventListener('bandlink:status', () => setTimeout(ensure, 600));
setInterval(ensure, 3000);
setTimeout(ensure, 300);
window.BeltGuardVoice = { ensure, pause, stop: pause };
void LOOKS_LIKE_KEY;
