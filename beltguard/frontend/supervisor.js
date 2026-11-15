// BeltGuard control room: one supervisor sees and hears the whole crew. Owner: S4 hav-brain. Design: DESIGN.md v2.
// Data: GET /api/fleet (polled every 2 s while /openapi.json lists it) and WS {"type":"fleet"} every ~1 s (S2), plus the /ws
// snapshot for the real band (W-01) and its "transcript" / "assistant" lines (what W-01 said, what BeltGuard answered).
// The crew story is fleet.story when S2 sends it; until then it is derived here from changes between updates and S2's alerts.
// Nothing is invented: values the backend does not send show as "–", and simulated workers are labelled.
// Only sentences BeltGuard answered are shown as "heard"; overheard chat never reaches this screen.
// The supervisor's job here is compliance: see that everyone is within the exposure limits and export the shift report
// for the regulator. Nothing on this screen asks anyone to approve or acknowledge anything; the crew story is read-only.

const SCALE = 400;                    // crew bars run 0-400 points; ticks at 100 (action) and 400 (limit)
const EXPECTED = ["W-01", "W-02", "W-03", "W-04", "W-05"];

const el = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (v === null || v === undefined || v === "" || !isFinite(+v)) ? null : +v;
const fmtPts = (p) => { p = num(p); return p === null ? "–" : p < 10 ? p.toFixed(1) : Math.round(p).toString(); };
const hhmm = (ts) => { ts = num(ts); return ts ? new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : ""; };
const hhmmss = (ts) => { ts = num(ts); return ts ? new Date(ts * 1000).toLocaleTimeString("en-GB") : ""; };
const dur = (s) => { s = num(s) || 0; if (s < 60) return Math.round(s) + " s"; const m = Math.floor(s / 60); return m + " min"; };
const nowS = () => Date.now() / 1000;
const lower1 = (t) => String(t || "").replace(/^./, (c) => c.toLowerCase());

const SVG = (body) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
const ICON = {
  lock: SVG('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
  battery: SVG('<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/>'),
  temp: SVG('<path d="M14 14.76V5a2 2 0 0 0-4 0v9.76a4 4 0 1 0 4 0Z"/>'),
  walk: SVG('<circle cx="13" cy="4" r="2"/><path d="m9 20 3-6 3 3v6"/><path d="m6 12 3-4 4 1 3 3"/>'),
  mic: SVG('<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 19v3"/>'),
  alert: SVG('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>'),
  fall: SVG('<path d="M12 2v14"/><path d="m19 9-7 7-7-7"/><path d="M5 21h14"/>'),
  check: SVG('<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>'),
  pause: SVG('<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>'),
  tool: SVG('<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>'),
};

let state = null;                     // GET /api/state or the WS snapshot (the real band, W-01)
let fleet = null;                     // the last /api/fleet payload, normalised
let fleetFromWS = 0;                  // when the last WS fleet message arrived
let fleetMissing = false;
let wsLive = false, everConnected = false, retry = 0, frame = 0;
let model = null, selected = null;
const spark = new Map();              // worker id -> recent motion values (W-01 from the /ws motion stream)
const firstSeen = new Map();          // derived alert id -> first time seen
const prevW = new Map();              // worker id -> what we saw last update (for the derived story)
let story = [];                       // derived story items, newest first
let heardW01 = null;                  // {said, reply, ts}: W-01's last answered sentence
let lastTranscript = null;            // {text, ts}
const toolsSeen = new Map();          // worker id -> tool names seen this shift (for the compliance report)
let lastMotionAt = 0;                 // when the real band's last motion batch arrived (W-01's "last reading")

// No approvals anywhere: strip request-for-approval phrases that older backend texts may still carry.
const NO_APPROVAL = /\s*(?:[:;,\u00b7\u2014-]\s*)?(?:supervisor review required|needs (?:supervisor )?review|confirm or reject|awaiting (?:supervisor )?(?:verification|sign-?off|approval)|(?:isolation )?must be verified by an authori[sz]ed person|an authori[sz]ed person (?:must )?verif(?:y|ies)(?: (?:it|the isolation|isolation))?)\.?/gi;
const clean = (t) => String(t || "").replace(NO_APPROVAL, "").replace(/^\s*[\u00b7:;,\u2014-]\s*/, "").replace(/\s{2,}/g, " ").trim();
const statusOf = (p) => (p >= 400 ? "limit" : p >= 100 ? "action" : "within");
const STATUS_WORD = { within: "within", action: "action", limit: "over limit" };

// ---------------------------------------------------------------- normalise
function verdictOf(points, s) {
  s = s || (points >= 400 ? "limit" : points >= 100 ? "action" : "within");
  return s === "limit" || s === "above_limit" ? "limit" : s === "action" || s === "above_action" ? "action" : "within";
}

function stationLabel(id) {
  if (!id) return "";
  if (id === "CRIB") return "the tool crib";
  const st = ((state && state.stations) || []).find((s) => s.id === id);
  return st ? "station " + (st.number || id.replace("S", "")) + ", " + st.name.toLowerCase() : id;
}

// Privacy guard for "heard": a command is short, and only recent ones matter. A long ramble (overheard talk the old
// parser once answered, before the wake word) or anything older than 20 minutes never reaches the supervisor's screen.
const HEARD_MAX_CHARS = 140, HEARD_MAX_AGE_S = 20 * 60;
function normHeard(h) {
  if (!h) return null;
  if (Array.isArray(h)) {                       // S2 sends the last 3: show the newest one that passes the guard
    const items = h.map(normHeard).filter(Boolean);
    return items.sort((a, b) => (b.ts || 0) - (a.ts || 0))[0] || null;
  }
  if (typeof h === "string") h = { text: h };
  const said = h.text || h.said || h.transcript || null;
  const ts = num(h.ts);
  if (!said || said.length > HEARD_MAX_CHARS || (ts && nowS() - ts > HEARD_MAX_AGE_S)) return null;
  return { said, reply: h.reply || h.response || h.answer || null, ts };
}

function normWorker(w, i) {
  const band = w.band && typeof w.band === "object" ? w.band : {};
  const assignment = w.assignment && typeof w.assignment === "object" ? w.assignment : {};
  const tool = w.tool || assignment.tool || null;
  const verdict = w.verdict || {};
  const points = num(w.points ?? w.points_today ?? (w.exposure || {}).total_points ?? verdict.total_points) || 0;
  const id = w.id || w.worker_id || EXPECTED[i] || "W-0" + (i + 1);
  const simulated = Boolean(w.is_simulated ?? w.simulated ?? band.simulated ?? (w.source === "sim"));
  const last = w.last_event || null;
  const rawMotion = Array.isArray(w.last_motion) ? w.last_motion : Array.isArray(w.motion) ? w.motion : null;
  const nearOneG = rawMotion && rawMotion.length && Math.abs(rawMotion.reduce((a, v) => a + (+v || 0), 0) / rawMotion.length - 1) < 0.35;
  const activity = w.activity || w.state || (w.detector && w.detector.state) || null;
  return {
    id, name: w.name || id, station_id: w.station_id || w.active_station || null,
    walk: w.walk && typeof w.walk === "object" ? w.walk : null,
    tool: tool ? { id: tool.id || tool.tool_id, name: tool.name || tool.id || tool.tool_id } : null,
    since: num(w.tool_since ?? w.since_ts ?? assignment.since_ts),
    trigger_s: num(w.trigger_s ?? w.trigger_s_today ?? assignment.trigger_s_today),
    points, verdict: verdictOf(points, verdict.state || w.status), headline: verdict.headline || null,
    band_number: typeof w.band === "number" ? w.band : (band.number ?? w.band_number ?? null),
    live: Boolean(w.connected ?? band.live ?? false),
    simulated,
    battery: num(band.battery ?? w.battery),
    temp_c: num(band.temp_c ?? band.temperature_c ?? w.temperature_c ?? w.temp_c),
    motion: rawMotion ? rawMotion.map((v) => (nearOneG ? Math.abs((+v || 0) - 1) : +v || 0)) : null,
    activity,
    fall: Boolean(w.possible_fall ?? w.fall ?? (last && last.kind === "free_fall")),
    unresolved: activity === "unresolved" ? (w.pending_confirmation || {}) : (w.pending_confirmation || null),
    last_event: last ? { ts: num(last.ts), kind: last.kind || "" } : null,
    heard: normHeard(w.heard),
  };
}

function normFleet(payload) {
  const list = Array.isArray(payload) ? payload : payload && (payload.workers || payload.fleet || payload.crew) || [];
  return { workers: list.map(normWorker), alerts: Array.isArray(payload && payload.alerts) ? payload.alerts : null,
           story: Array.isArray(payload && payload.story) ? payload.story : null, label: (payload && payload.label) || null,
           ts: num(payload && payload.ts) };
}

function workerFromState(s) {
  const c = s.connection || {};
  const ex = s.exposure || {};
  const unresolved = (s.tool_sessions || []).find((x) => x && x.status === "unresolved") || null;
  const trig = Object.values(ex.by_tool || {}).reduce((a, v) => a + (num(v.trigger_s) || 0), 0);
  const w = normWorker({
    id: (s.worker || {}).id || "W-01", name: (s.worker || {}).name, station_id: s.active_station,
    tool: (s.assignment && s.assignment.tool) || s.active_tool, since_ts: s.assignment && s.assignment.since_ts,
    trigger_s: trig, points: ex.total_points, verdict: s.verdict || { state: ex.status },
    band: { number: c.band, live: c.state === "connected", simulated: c.simulated || c.state === "simulated", battery: c.battery },
    activity: (s.detector || {}).state, pending_confirmation: unresolved,
  }, 0);
  return w;
}

const RANK = { tool_active: 0, unresolved: 1, walking: 2 };
function crew() {
  let list;
  if (fleet && fleet.workers.length) list = fleet.workers.slice();
  else {
    list = state ? [workerFromState(state)] : [];
    const ids = new Set(list.map((w) => w.id));
    list = list.concat(EXPECTED.filter((id) => !ids.has(id)).map((id) => ({ id, placeholder: true })));
  }
  const w01 = list.find((w) => w.id === "W-01");
  if (w01 && !w01.placeholder && !w01.heard && heardW01) w01.heard = heardW01;
  const falls = new Set(((fleet && fleet.alerts) || []).filter((a) => a.kind === "fall" && nowS() - (num(a.ts) || 0) < 60)
    .map((a) => a.worker_id));
  list.forEach((w) => { if (falls.has(w.id)) w.fall = true; });
  // busy first: tool running, then a session to confirm, then walking; then highest points
  return list.sort((a, b) => (a.placeholder ? 9 : RANK[a.activity] ?? 3) - (b.placeholder ? 9 : RANK[b.activity] ?? 3)
    || (b.points || 0) - (a.points || 0) || a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------- story
function pushStory(item) {
  if (story.some((s) => s.id === item.id)) return;
  story.unshift(item);
  if (story.length > 120) story.length = 120;
}

function deriveStory(workers) {
  const t = nowS();
  for (const w of workers) {
    if (w.placeholder) continue;
    const p = prevW.get(w.id);
    const who = w.name;
    if (!p) {
      if (w.tool) pushStory({ id: w.id + ":has:" + w.tool.id, ts: w.since || t, worker: who, icon: "lock",
                              text: who + " has the " + lower1(w.tool.name) + " checked out" });
    } else {
      const pt = p.tool && p.tool.id, nt = w.tool && w.tool.id;
      if (pt !== nt) {
        if (p.tool) pushStory({ id: w.id + ":return:" + pt + ":" + Math.round(t), ts: t, worker: who, icon: "check",
                                text: who + " returned the " + lower1(p.tool.name) });
        if (w.tool) pushStory({ id: w.id + ":checkout:" + nt + ":" + Math.round(w.since || t), ts: w.since || t, worker: who,
                                icon: "lock", text: who + " checked out the " + lower1(w.tool.name) });
      }
      if (p.activity !== w.activity) {
        if (w.activity === "walking" && w.walk && w.walk.to) {
          pushStory({ id: w.id + ":walk:" + w.walk.to + ":" + Math.round(t), ts: t, worker: who, icon: "walk",
                      text: who + " is walking to " + stationLabel(w.walk.to) });
        } else if (p.activity === "walking" && w.station_id) {
          pushStory({ id: w.id + ":arrive:" + w.station_id + ":" + Math.round(t), ts: t, worker: who, icon: "walk",
                      text: who + " arrived at " + stationLabel(w.station_id) });
        }
        if (w.activity === "tool_active") {
          pushStory({ id: w.id + ":run:" + Math.round(t), ts: t, worker: who, icon: "tool",
                      text: who + " started the " + lower1((w.tool && w.tool.name) || "tool") + ": the band is timing it" });
        }
      }
      if (w.heard && w.heard.said && (!p.heard || p.heard.said !== w.heard.said || p.heard.reply !== w.heard.reply)) {
        pushStory({ id: w.id + ":heard:" + (w.heard.ts || Math.round(t)) + ":" + w.heard.said.length, ts: w.heard.ts || t,
                    worker: who, icon: "mic", mic: true,
                    text: who + ": “" + w.heard.said + "”" + (w.heard.reply ? "  BeltGuard: “" + w.heard.reply + "”" : "") });
      }
    }
    prevW.set(w.id, { tool: w.tool, activity: w.activity, verdict: w.verdict, heard: w.heard, station_id: w.station_id });
  }
}

const KIND = { confirm: "uncertain", uncertain: "uncertain", pending_confirmation: "uncertain", unresolved: "uncertain",
               action: "action", action_value: "action", above_action: "action",
               limit: "limit", limit_value: "limit", above_limit: "limit", fall: "fall", possible_fall: "fall", free_fall: "fall" };
const ALERT_LOOK = { action: ["amber", "alert"], uncertain: ["amber", "alert"], limit: ["red", "alert"], fall: ["red", "fall"] };

function alertItems(workers) {
  const out = [];
  const backend = new Set();
  const names = new Map(workers.map((w) => [w.id, w.name]));
  if (fleet && fleet.alerts) {
    const newest = new Map();
    fleet.alerts.forEach((a) => {
      const kind = KIND[a.kind || a.type] || a.kind || a.type || "info";
      const key = (a.worker_id || "") + ":" + kind;
      backend.add(key);
      const ts = num(a.ts) || 0;
      const row = { id: a.id || key + ":" + Math.round(ts), ts, worker: names.get(a.worker_id) || a.worker_id || "",
                    kind, alert: true, text: clean(a.title || a.text || a.message || ""), count: 1 };
      const prev = newest.get(key);
      if (!prev) newest.set(key, row);
      else if (ts > prev.ts) { row.count = prev.count + 1; newest.set(key, row); }
      else prev.count += 1;
    });
    newest.forEach((row) => out.push(row));
  }
  const t = nowS();
  const add = (w, kind, text) => {
    const id = w.id + ":" + kind;
    if (backend.has(id)) return;
    if (!firstSeen.has(id)) firstSeen.set(id, t);
    out.push({ id, ts: firstSeen.get(id), worker: w.name, kind, alert: true, text, count: 1 });
  };
  workers.filter((w) => !w.placeholder).forEach((w) => {
    if (w.points >= 400) add(w, "limit", w.name + " reached the 400-point limit value: stop vibrating-tool work");
    else if (w.points >= 100) add(w, "action", w.name + " reached the 100-point action value");
    if (w.fall) add(w, "fall", "Possible fall: " + w.name + ". Check on them now");
    if (w.unresolved) add(w, "uncertain", w.name + ": uncertain tool activity, not counted yet");
  });
  return out;
}

function storyItems(workers) {
  const alerts = alertItems(workers);
  let base;
  if (fleet && fleet.story) {
    // alert / fall / confirm lines come in fleet.alerts too (coloured rows): don't show them twice
    base = fleet.story.filter((s) => !/^(alert|fall|confirm)$/.test(String(s.kind || "")))
      .filter((s) => !(/voice|heard/.test(String(s.kind || "")) && String(s.text || "").length > HEARD_MAX_CHARS + 120))   // privacy guard
      .map((s, i) => {
      const kind = String(s.kind || s.icon || "");
      const icon = /mic|heard|voice|said/.test(kind) ? "mic" : /fall/.test(kind) ? "fall" : /walk|arriv/.test(kind) ? "walk"
        : /break|rest|pause/.test(kind) ? "pause" : /return|check|done/.test(kind) ? "check"
        : /run|start|tool_active/.test(kind) ? "tool" : /lock|checkout|assign/.test(kind) ? "lock"
        : /alert|action|limit/.test(kind) ? "alert" : "check";
      return { id: s.id || "story:" + (s.ts || i) + ":" + (s.worker_id || ""), ts: num(s.ts) || 0,
               worker: s.worker_name || s.worker || s.worker_id || "", icon, mic: icon === "mic", text: clean(s.text || s.title || "") };
    });
  } else base = story;
  return alerts.concat(base).sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, 60);
}

// ---------------------------------------------------------------- render
function badge(variant, text, title) {
  return '<span class="badge ' + variant + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(text) + "</span>";
}

function renderConn() {
  const node = el("conn");
  let v = "outline", t = "Connecting";
  if (!wsLive && everConnected) { v = "destructive"; t = "Offline, retrying"; }
  else if (wsLive && fleet && fleet.workers.length) { v = "success"; t = "Crew feed live"; }
  else if (wsLive && fleetMissing) { v = "secondary"; t = "Band 07 only · crew feed pending"; }
  else if (wsLive) { v = "success"; t = "Live"; }
  node.className = "badge " + v;
  node.textContent = t;
}

function renderKpis(workers, items) {
  const real = workers.filter((w) => !w.placeholder);
  const waiting = workers.length - real.length;
  const n = { within: 0, action: 0, limit: 0 };
  real.forEach((w) => { n[statusOf(w.points)] += 1; });
  el("k-shift").innerHTML = esc(n.within) + '<span class="unit">of ' + esc(workers.length) + "</span>";
  el("k-shift").className = "kpi-value " + (n.limit ? "st-limit" : n.action ? "st-action" : "st-within");
  const parts = [];
  if (n.action) parts.push('<span class="st-action">' + n.action + " at action (100–400)</span>");
  if (n.limit) parts.push('<span class="st-limit">' + n.limit + " over limit (400+)</span>");
  if (waiting) parts.push(esc(waiting) + " waiting for the crew feed");
  el("k-shift-sub").innerHTML = parts.length ? parts.join(" · ") : '<span class="st-within">Everyone under 100 points</span>';
  const out = real.filter((w) => w.tool);
  el("k-tools").textContent = String(out.length);
  el("k-tools-sub").textContent = out.length ? out.map((w) => w.tool.name).join(", ") : "None checked out";
  const top = real.slice().sort((a, b) => b.points - a.points)[0];
  el("k-close").textContent = top ? top.name : "–";
  el("k-close-sub").textContent = top ? fmtPts(top.points) + " pts · " + (top.points >= 100 ? "over the action value" : Math.max(0, Math.round(100 - top.points)) + " pts to go") : " ";
  const alerts = items.filter((a) => a.alert);
  el("k-alerts").textContent = String(alerts.length);
  el("k-alerts-sub").textContent = alerts.length ? alerts[0].text : "None this shift";
}

const VERDICT = { within: ["success", "Within limits"], action: ["warning", "Action"], limit: ["danger", "Over limit"] };

function workerCard(w) {
  if (w.placeholder) {
    return '<article class="worker placeholder" data-id="' + esc(w.id) + '"><div class="w-top"><span class="avatar">' +
      esc(w.id.replace("W-0", "W")) + '</span><div class="w-id"><div class="w-name">' + esc(w.id) +
      '</div><div class="w-sub">Waiting for the crew feed</div></div></div></article>';
  }
  const v = VERDICT[statusOf(w.points)] || VERDICT.within;
  const busy = w.activity === "tool_active";
  const bandChip = w.simulated ? badge("secondary", "Simulated band", "Simulated worker, not a real wearer")
    : badge(w.live ? "success" : "outline", "Band " + String(w.band_number ?? 7).padStart(2, "0") + (w.live ? " · Live" : " · Not connected"));
  const doing = w.activity === "walking" && w.walk && w.walk.to ? "walking to " + (w.walk.to === "CRIB" ? "tool crib" : w.walk.to)
    : w.activity === "unresolved" ? "tool activity to confirm" : w.activity === "tool_active" ? "using a tool" : w.activity || "idle";
  const where = [w.id, w.station_id ? (w.station_id === "CRIB" ? "Tool crib" : w.station_id) : null, doing].filter(Boolean).join(" · ");
  const toolLine = w.tool
    ? '<div class="w-tool">' + ICON.lock + "<span>" + esc(w.tool.name) + (w.since ? " · since " + esc(hhmm(w.since)) : "") + "</span>" +
      (busy ? badge("tool", "Running") : "") + "</div>"
    : '<div class="w-tool none"><span>No tool checked out</span></div>';
  const pct = Math.min(100, (w.points / SCALE) * 100);
  const heard = w.heard && w.heard.said
    ? '<div class="heard" title="Last thing BeltGuard heard and answered"><span class="mic">' + ICON.mic + '</span><span class="said">“' +
      esc(w.heard.said) + '”</span>' + (w.heard.reply ? '<span class="reply">BeltGuard: ' + esc(w.heard.reply) + "</span>" : "") + "</div>"
    : '<div class="heard none"><span class="mic">' + ICON.mic + '</span><span class="reply">Nothing heard yet</span></div>';
  return '<article class="worker' + (selected === w.id ? " selected" : "") + '" data-id="' + esc(w.id) + '" tabindex="0" role="button" aria-label="Focus ' + esc(w.name) + '">' +
    '<div class="w-top"><span class="avatar' + (busy ? " busy" : "") + '">' + esc(w.id.replace("W-0", "W")) + "</span>" +
      '<div class="w-id"><div class="w-name">' + esc(w.name) + '</div><div class="w-sub">' + esc(where) + "</div></div>" +
      '<div class="w-chips">' + bandChip +
        '<span class="dev" title="Band battery">' + ICON.battery + (w.battery === null ? "–" : Math.round(w.battery) + "%") + "</span>" +
        '<span class="dev" title="Band device temperature (not skin)">' + ICON.temp + (w.temp_c === null ? "–" : w.temp_c.toFixed(0) + " °C") + "</span>" +
        badge(v[0], v[1], clean(w.headline || "")) + "</div></div>" +
    '<div class="w-mid">' + toolLine +
      '<canvas class="spark" width="224" height="52" data-spark="' + esc(w.id) + '" title="Wrist motion, last ~30 s"></canvas>' +
      '<div class="bar-wrap" title="Points today on a 0–400 scale · ticks at 100 (action) and 400 (limit)"><div class="bar"><i class="zone"></i>' +
        '<i class="fill" style="width:' + pct.toFixed(1) + '%"></i></div><i class="tick" style="left:25%"></i><i class="tick" style="left:calc(100% - 1px)"></i></div>' +
      '<div class="pts">' + esc(fmtPts(w.points)) + '<small>pts</small><span class="pts-sub">' + esc(w.trigger_s === null ? "" : dur(w.trigger_s) + " trigger") + "</span></div></div>" +
    heard + "</article>";
}

function drawSparks(workers) {
  document.querySelectorAll("canvas[data-spark]").forEach((cv) => {
    const id = cv.getAttribute("data-spark");
    const w = workers.find((x) => x.id === id);
    const series = (w && w.motion && w.motion.length ? w.motion : spark.get(id)) || [];
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = "#E4E4E7"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H - 1.5); ctx.lineTo(W, H - 1.5); ctx.stroke();
    const pts = series.slice(-64).map(Number).filter((n) => isFinite(n));
    if (pts.length < 2) return;
    const max = Math.max(0.05, ...pts);
    ctx.strokeStyle = w && w.activity === "tool_active" ? "#F97316" : "#18181B";
    ctx.lineWidth = 2.5; ctx.lineJoin = "round";
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = (i / (pts.length - 1)) * (W - 2) + 1;
      const y = H - 3 - (p / max) * (H - 8);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  });
}

function renderStory(items) {
  const list = el("story");
  if (!items.length) { list.innerHTML = '<li class="empty">Nothing yet. Events appear here as the crew works.</li>'; return; }
  list.innerHTML = items.map((s) => {
    const look = s.alert ? (ALERT_LOOK[s.kind] || ["amber", "alert"]) : null;
    const cls = "s-item" + (look ? " " + look[0] : "") + (s.mic ? " mic" : "");
    const icon = ICON[look ? look[1] : s.icon] || ICON.check;
    const meta = [hhmmss(s.ts), s.worker, s.count > 1 ? s.count + " times" : ""].filter(Boolean).join(" · ");
    return '<li class="' + cls + '"><span class="s-icon">' + icon + '</span><div style="min-width:0"><div class="s-text" title="' +
      esc(s.text) + '">' + esc(s.text) + '</div><div class="s-meta">' + esc(meta) + "</div></div></li>";
  }).join("");
}

function noteTools(workers) {
  workers.forEach((w) => {
    if (w.placeholder || !w.tool || !w.tool.name) return;
    if (!toolsSeen.has(w.id)) toolsSeen.set(w.id, new Set());
    toolsSeen.get(w.id).add(w.tool.name);
  });
}
function render() {
  const workers = crew();
  noteTools(workers);
  deriveStory(workers);
  const items = storyItems(workers);
  renderConn();
  renderKpis(workers, items);
  el("workers").innerHTML = workers.map(workerCard).join("");
  drawSparks(workers);
  renderStory(items);
  el("fleet-label").textContent = fleet && fleet.label ? fleet.label : "";
  if (model) {
    try {
      if (state && typeof model.apply === "function") model.apply(state);
      if (typeof model.setFleet === "function") model.setFleet(workers.filter((w) => !w.placeholder));
    } catch (e) { console.warn("3D update skipped:", e); }
  }
}
function schedule() { if (!frame) frame = setTimeout(() => { frame = 0; try { render(); } catch (e) { console.error(e); } }, 80); }

// ---------------------------------------------------------------- data in
let fleetRoute = null, routeChecked = 0;                // does the backend serve /api/fleet? (from /openapi.json)
async function fleetRouteExists() {
  if (fleetRoute || Date.now() - routeChecked < 15000) return fleetRoute;
  routeChecked = Date.now();
  try {
    const res = await fetch("/openapi.json", { cache: "no-store" });
    if (res.ok) fleetRoute = Boolean(((await res.json()).paths || {})["/api/fleet"]);
  } catch (e) { /* keep the last answer */ }
  if (!fleetRoute) { fleetMissing = true; renderConn(); }
  return fleetRoute;
}

async function pollFleet() {
  if (Date.now() - fleetFromWS < 5000) return;          // the WS feed is flowing
  if (!(await fleetRouteExists())) return;
  try {
    const res = await fetch("/api/fleet", { cache: "no-store" });
    if (!res.ok) return;
    fleet = normFleet(await res.json());
    fleetMissing = false;
    everConnected = true;
    schedule();
  } catch (e) { /* offline: the badge says so */ }
}

async function loadState() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (res.ok) { state = await res.json(); everConnected = true; schedule(); }
  } catch (e) { /* retry via WS */ }
}

function motionRms(msg) {
  const acc = Array.isArray(msg.accel) ? msg.accel : [];
  if (!acc.length) return null;
  let s = 0;
  for (const a of acc) { const m = Math.hypot(a[0] || 0, a[1] || 0, a[2] || 0) - 1; s += m * m; }
  return Math.sqrt(s / acc.length);
}

function pushSpark(id, v) {
  if (v === null || !isFinite(v)) return;
  const list = spark.get(id) || [];
  list.push(v);
  if (list.length > 64) list.splice(0, list.length - 64);
  spark.set(id, list);
}

function connect() {
  let ws;
  try { ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws"); }
  catch (e) { setTimeout(connect, 2000); return; }
  ws.onopen = () => { wsLive = true; everConnected = true; retry = 0; renderConn(); };
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "snapshot" && m.state) { state = m.state; schedule(); }
    else if (m.type === "fleet") { fleet = normFleet(m.fleet || m.state || m); fleetFromWS = Date.now(); fleetMissing = false; schedule(); }
    else if (m.type === "motion") {
      pushSpark((state && state.worker && state.worker.id) || "W-01", motionRms(m));
      lastMotionAt = nowS();
      if (!fleet) schedule();
    } else if (m.type === "transcript" && m.final !== false && m.text) {
      lastTranscript = { text: m.text, ts: nowS(), input: m.input };     // shown only once BeltGuard answers it
    } else if (m.type === "assistant" && m.text) {
      const said = lastTranscript && nowS() - lastTranscript.ts < 20 ? lastTranscript.text : null;
      if (said && said.length <= HEARD_MAX_CHARS) { heardW01 = { said, reply: m.text, ts: nowS() }; lastTranscript = null; schedule(); }
    } else if (m.type === "connection" && state) { state.connection = Object.assign({}, state.connection, m); schedule(); }
    else if (m.type === "verdict" && state) { state.verdict = m.verdict; state.assignment = m.assignment; schedule(); }
    else if (m.type === "work_order_pdf") onWorkOrderPdf(m);
  };
  ws.onclose = () => { wsLive = false; renderConn(); setTimeout(connect, Math.min(10000, 500 * Math.pow(2, retry++))); };
  ws.onerror = () => { /* onclose follows */ };
}

// ---------------------------------------------------------------- interaction
function focus(id) {
  selected = id;
  document.querySelectorAll(".worker").forEach((n) => n.classList.toggle("selected", n.getAttribute("data-id") === id));
  if (model && typeof model.focusWorker === "function") { try { model.focusWorker(id); } catch (e) { console.warn(e); } }
}

el("workers").addEventListener("click", (e) => {
  const card = e.target.closest(".worker");
  if (card && !card.classList.contains("placeholder")) focus(card.getAttribute("data-id"));
});
el("workers").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const card = e.target.closest(".worker");
  if (card && !card.classList.contains("placeholder")) { e.preventDefault(); focus(card.getAttribute("data-id")); }
});
// ---------------------------------------------------------------- work orders (S12: what the crew reports becomes a WO + PDF)
// Load: GET /api/work_orders/recent?limit=8 -> {rows:[{id, ts, title, station_id, station_name, priority, status, worker_id, pdf_url}]}
// Live: WS {"type":"work_order_pdf", id, url, title, station_id, priority, ts, worker_id, text}. Newest first, at most 5 rows.
const woRows = new Map();             // id -> row
const woFresh = new Map();            // id -> when it arrived live (for the glow)
const safeUrl = (u) => (typeof u === "string" && /^(\/(?!\/)|https?:\/\/)/.test(u) ? u : null);
function woFrom(r) {
  const id = String((r && (r.id || r.work_order_id)) || "");
  if (!id) return null;
  return { id, ts: num(r.ts) || nowS(), title: r.title || "", station: r.station_name || r.station_id || "",
           priority: r.priority || "", worker: r.worker_id || "W-01",
           url: safeUrl(r.pdf_url || r.url) || "/api/work_orders/" + encodeURIComponent(id) + "/pdf" };
}
function renderWorkOrders() {
  const list = el("wo-list");
  if (!list) return;
  const rows = [...woRows.values()].sort((a, b) => b.ts - a.ts).slice(0, 5);
  if (!rows.length) { list.innerHTML = '<li class="empty">No work orders this shift</li>'; return; }
  const t = Date.now();
  list.innerHTML = rows.map((w) => {
    const fresh = woFresh.has(w.id) && t - woFresh.get(w.id) < 2400;
    const sub = [w.title, w.station, w.priority ? w.priority + " priority" : "", hhmm(w.ts)].filter(Boolean).join(" · ");
    return '<li class="wo-row' + (fresh ? " new" : "") + '"><div class="wo-main"><span>New work order <b class="mono">' + esc(w.id) +
      "</b> from " + esc(w.worker) + '</span><span class="muted">·</span><a href="' + esc(w.url) + '" target="_blank" rel="noopener">Open PDF</a></div>' +
      '<div class="wo-sub" title="' + esc(sub) + '">' + esc(sub) + "</div></li>";
  }).join("");
}
let toastTimer = 0;
function toast(text) {
  const node = el("toast");
  if (!node) return;
  node.textContent = text;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}
function onWorkOrderPdf(m) {
  const w = woFrom(m);
  if (!w) return;
  const isNew = !woRows.has(w.id);
  woRows.set(w.id, Object.assign({}, woRows.get(w.id) || {}, w));
  if (isNew) { woFresh.set(w.id, Date.now()); toast(m.text || "New work order " + w.id + " from " + w.worker); }
  renderWorkOrders();
}
async function loadWorkOrders() {
  try {
    const res = await fetch("/api/work_orders/recent?limit=8", { cache: "no-store" });
    if (!res.ok) return;                                    // not served yet: the WS rows still arrive
    const d = await res.json();
    let added = false;
    (Array.isArray(d && d.rows) ? d.rows : []).forEach((r) => { const w = woFrom(r); if (w && !woRows.has(w.id)) { woRows.set(w.id, w); added = true; } });
    if (added) renderWorkOrders();
  } catch (e) { /* offline: keep what we have */ }
}

// ---------------------------------------------------------------- shift compliance report (CSV + print view)
const two = (n) => String(n).padStart(2, "0");
const dateTime = (ts) => { const d = new Date(ts * 1000); return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + " " + two(d.getHours()) + ":" + two(d.getMinutes()); };
const csvCell = (v) => { const t = String(v == null ? "" : v); return /[",\n;]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
function toolsUsed(w) {
  const names = new Set(toolsSeen.get(w.id) || []);
  if (w.tool && w.tool.name) names.add(w.tool.name);
  if (w.id === "W-01" && state && state.exposure) {
    Object.values(state.exposure.by_tool || {}).forEach((t) => { if (t && (t.name || t.tool_name)) names.add(t.name || t.tool_name); });
    (state.exposure.records || []).forEach((r) => { const n = r && (r.tool_name || (r.tool && r.tool.name)); if (n) names.add(n); });
  }
  return [...names];
}
function lastReading(w) {
  if (!w.simulated && lastMotionAt) return lastMotionAt;
  if (w.live && fleet && fleet.ts) return fleet.ts;
  return (w.last_event && w.last_event.ts) || null;
}
function buildReport() {
  const workers = crew().filter((w) => !w.placeholder).sort((a, b) => a.id.localeCompare(b.id));
  const rows = workers.map((w) => {
    const last = lastReading(w);
    const st = statusOf(w.points);
    return { id: w.id, name: w.name, band: w.simulated ? "SIMULATED" : "Band " + two(w.band_number ?? 7) + " (real)",
             tools: toolsUsed(w).join("; ") || "none", trigger: w.trigger_s === null ? "" : (w.trigger_s / 60).toFixed(1),
             points: (w.points || 0).toFixed(1), status: STATUS_WORD[st], st, last: last ? hhmmss(last) : "–" };
  });
  const shift = num(state && state.shift_start);
  const within = rows.filter((r) => r.st === "within").length;
  return {
    rows, generated: nowS(), shift,
    head: [
      ["BeltGuard shift compliance report"],
      ["Screening estimate, not a certified HAV measurement"],
      ["Site", "CV-102 conveyor maintenance"],
      ["Shift start", shift ? dateTime(shift) : "not recorded"],
      ["Generated", dateTime(nowS())],
      ["Within limits", within + " of " + rows.length + " (under 100 points)"],
      ["Thresholds", "Action value 100 points (A(8) 2.5 m/s²); limit value 400 points (A(8) 5.0 m/s²)"],
    ].concat(fleet && fleet.label ? [["Note", fleet.label]] : []),
  };
}
const REPORT_COLS = ["Worker ID", "Name", "Band", "Tools used", "Trigger time (min)", "Points today", "Status", "Last reading"];
function reportCsv(r) {
  const lines = r.head.map((row) => row.map(csvCell).join(","));
  lines.push("", REPORT_COLS.map(csvCell).join(","));
  r.rows.forEach((x) => lines.push([x.id, x.name, x.band, x.tools, x.trigger, x.points, x.status, x.last].map(csvCell).join(",")));
  return lines.join("\r\n") + "\r\n";
}
function downloadCsv(text, name) {
  const blob = new Blob(["\uFEFF" + text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function showReport(r) {
  el("report-sub").textContent = r.head.slice(3, 6).map((row) => row.join(": ")).join(" · ");
  el("report-table").innerHTML = "<thead><tr>" + REPORT_COLS.map((c) => "<th>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>" +
    r.rows.map((x) => "<tr><td class=\"mono\">" + esc(x.id) + "</td><td>" + esc(x.name) + "</td><td>" + esc(x.band) + "</td><td>" + esc(x.tools) +
      "</td><td class=\"num\">" + esc(x.trigger) + "</td><td class=\"num\">" + esc(x.points) + "</td><td><span class=\"rs " + x.st + "\">" +
      esc(x.status) + "</span></td><td class=\"mono\">" + esc(x.last) + "</td></tr>").join("") + "</tbody>";
  el("report-foot").textContent = r.head.slice(6).map((row) => row.join(": ")).join(" · ");
  el("report").hidden = false;
  el("report-close").focus();
}
const reportName = (r) => { const d = new Date(r.generated * 1000); return "BeltGuard-shift-compliance-" + d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + "-" + two(d.getHours()) + two(d.getMinutes()) + ".csv"; };
el("report-btn").addEventListener("click", () => { const r = buildReport(); downloadCsv(reportCsv(r), reportName(r)); showReport(r); });
el("report-csv").addEventListener("click", () => { const r = buildReport(); downloadCsv(reportCsv(r), reportName(r)); });
el("report-print").addEventListener("click", () => window.print());
el("report-close").addEventListener("click", () => { el("report").hidden = true; });
el("report").addEventListener("click", (e) => { if (e.target === el("report")) el("report").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !el("report").hidden) el("report").hidden = true; });
window.BeltGuardReport = { build: buildReport, csv: (r) => reportCsv(r || buildReport()), show: (r) => showReport(r || buildReport()) };   // for QA
window.BeltGuardWO = { add: onWorkOrderPdf, rows: () => [...woRows.values()] };   // for QA

// ---------------------------------------------------------------- 3D site (S7's module; feature-detected)
import("./model3d.js?v=" + Date.now()).then((m) => {
  if (!m || typeof m.createModel !== "function") throw new Error("no createModel");
  el("stage-note").remove();
  model = m.createModel(el("stage"), { onStationClick: () => {}, onWorkerClick: (id) => focus(id) });
  schedule();
}).catch((e) => {
  console.warn("3D site unavailable:", e);
  el("stage-note").textContent = "3D site unavailable";
});

const tick = () => { el("clock").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
tick();
setInterval(tick, 15000);
setInterval(pollFleet, 2000);
setInterval(() => { if (!fleet || Date.now() - fleetFromWS > 3000) schedule(); }, 3000);
loadState();
pollFleet();
loadWorkOrders();
setInterval(loadWorkOrders, 30000);
connect();
