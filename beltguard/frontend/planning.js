// Vexora planning page (the former supervisor page). Owner: S4 hav-brain. Design: DESIGN.md v2 (shadcn zinc, Geist).
// Reads the shared backend (GET /api/state, WebSocket /ws) and posts planned repairs to
// POST /api/repair/plan. Supervisor decisions are recorded in this browser only: Vexora
// never approves work, reassigns people or changes methods by itself.
"use strict";
(function () {
  const API = location.origin + "/api";
  const SCALE_MAX = 500;
  const STORE_KEY = "beltguard.supervisor.decisions.v1";

  const el = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => (v === null || v === undefined || v === "" || !isFinite(+v)) ? null : +v;
  const fmtPts = (p) => { p = num(p); return p === null ? "–" : p < 10 ? p.toFixed(1) : Math.round(p).toLocaleString("en-GB"); };
  const fmtMin = (m) => { m = num(m); return m === null ? "–" : m < 10 ? (Math.round(m * 10) / 10).toString() : Math.round(m).toLocaleString("en-GB"); };
  const fmtTrigger = (s) => { s = num(s) || 0; return s < 90 ? Math.round(s) + " s" : (s / 60).toFixed(1) + " min"; };
  const clock = (ts) => new Date((num(ts) || 0) * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const pct = (p) => Math.max(0, Math.min(100, ((num(p) || 0) / SCALE_MAX) * 100));
  const badge = (variant, text, title) => '<span class="badge ' + variant + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(text) + "</span>";

  const EXPOSURE = { below_action: ["success", "Below action value"], above_action: ["warning", "Above action value"],
                     above_limit: ["destructive", "Above limit value"] };
  const REC = { ok: ["success", "No concern"], concern: ["warning", "Concern"], over_limit: ["destructive", "Over limit"] };
  const WO = { draft: ["outline", "Draft"], planned: ["outline", "Planned"], in_progress: ["secondary", "In progress"],
               awaiting_verification: ["warning", "To verify"] };
  const SEVERITY = { critical: "danger", high: "danger", medium: "warning", low: "secondary" };
  const DECISION = { approve: "Approved", reassign: "Reassignment requested", method: "Method change requested" };

  let state = null;
  let localRec = null;          // the recommendation from this page's own last plan request
  let wsLive = false, everConnected = false, retry = 0, frame = 0;
  let toolsKey = "", stationsKey = "", formTouched = false;
  let decisions = loadDecisions();

  // ---------------------------------------------------------------- storage (per-viewer convenience only)
  function loadDecisions() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function saveDecisions() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(decisions.slice(0, 50))); } catch (e) { /* kept in memory */ }
  }

  // ---------------------------------------------------------------- data
  const toolById = (id) => ((state && state.tools) || []).find((t) => t.id === id) || null;
  const stationById = (id) => ((state && state.stations) || []).find((s) => s.id === id) || null;
  const defectById = (id) => ((state && state.defects) || []).find((d) => d.id === id) || null;

  function currentRec() {
    const server = state && state.recommendation;
    const shift = num(state && state.shift_start) || 0;
    if (localRec && (num(localRec.ts) || 0) < shift) localRec = null;    // a new shift started since
    if (!server) return localRec;
    if (!localRec) return server;
    return (num(localRec.ts) || 0) > (num(server.ts) || 0) ? localRec : server;
  }

  function schedule() {   // a timer, not requestAnimationFrame: a background tab must keep its numbers current
    if (!frame) frame = setTimeout(() => { frame = 0; render(); }, 60);
  }

  async function loadState() {
    try {
      const res = await fetch(API + "/state", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      state = await res.json();
      everConnected = true;
      schedule();
    } catch (e) { renderConn(); }
  }

  function connect() {
    let ws;
    try { ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws"); }
    catch (e) { reconnect(); return; }
    ws.onopen = () => { wsLive = true; everConnected = true; retry = 0; renderConn(); };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "snapshot" && msg.state) { state = msg.state; schedule(); }
      else if (msg.type === "connection" && state) {
        const next = Object.assign({}, state.connection || {}, msg);
        delete next.type; delete next.ts; delete next.source;
        state.connection = next;
        renderConn();
      }
    };
    ws.onclose = () => { wsLive = false; renderConn(); reconnect(); };
    ws.onerror = () => { /* onclose follows and reconnects */ };
  }
  function reconnect() { setTimeout(connect, Math.min(10000, 500 * Math.pow(2, retry++))); }

  // ---------------------------------------------------------------- render
  function setBadge(node, variant, text) { node.className = "badge " + variant; node.textContent = text; }

  function renderConn() {
    const c = (state && state.connection) || {};
    const band = "Band " + String(c.band != null ? c.band : 7).padStart(2, "0");
    let variant = "outline", text = "Connecting";
    if (!wsLive && everConnected) { variant = "destructive"; text = "Offline, retrying"; }
    else if (!wsLive) { variant = "outline"; text = "Connecting"; }
    else if (c.state === "connected") { variant = "success"; text = band + " · live"; }
    else if (c.state === "simulated") { variant = "secondary"; text = band + " · simulated"; }
    else if (c.state === "connecting") { variant = "outline"; text = band + " · connecting"; }
    else if (c.state) { variant = "destructive"; text = band + " · " + c.state; }
    setBadge(el("conn"), variant, text);
  }

  function renderHeader() {
    const conv = state.conveyor || {};
    const worker = state.worker || {};
    el("asset").textContent = conv.id || "CV-102";
    el("worker").textContent = worker.id || "W-01";
    el("exp-desc").textContent = worker.name || worker.id || "Technician";
  }

  function renderExposure() {
    const ex = state.exposure || {};
    const total = num(ex.total_points) || 0;
    const pair = EXPOSURE[ex.status] || EXPOSURE.below_action;
    el("exp-total").textContent = fmtPts(total);
    setBadge(el("exp-status"), pair[0], pair[1]);
    el("min-eav").textContent = total >= 100 ? "0" : fmtMin(ex.minutes_to_action);
    el("min-elv").textContent = total >= 400 ? "0" : fmtMin(ex.minutes_to_limit);
    const basis = toolById(ex.basis_tool_id);
    el("minis").title = basis ? "Trigger minutes left on " + basis.name + " (" + basis.vibration_m_s2 + " m/s²)" : "No tool use yet today";
    const pending = num(ex.pending_points) || 0;
    el("exp-desc").textContent = ((state.worker || {}).name || "Technician") +
      (pending > 0 ? " · " + fmtPts(pending) + " pts awaiting confirmation" : "");

    el("g-fill").style.width = (total > 0 ? Math.max(0.8, pct(total)) : 0) + "%";
    const rec = currentRec();
    const projected = rec ? num(rec.projected_points) : null;
    const showProj = projected !== null && projected > total;
    const proj = el("g-proj");
    proj.hidden = !showProj;
    el("proj-row").hidden = !showProj;
    el("g-off").hidden = !(showProj && projected > SCALE_MAX);
    if (showProj) {
      proj.style.left = pct(total) + "%";
      proj.style.width = Math.max(0.8, pct(projected) - pct(total)) + "%";
      el("proj-val").textContent = fmtPts(projected) + " pts";
    }
    el("gauge").setAttribute("aria-label", "Exposure on a 0 to 500 point scale: today " + fmtPts(total) + " points, " +
      pair[1].toLowerCase() + ". Action value 100, limit value 400." + (showProj ? " After the planned job: " + fmtPts(projected) + " points." : ""));

    const rows = Object.entries(ex.by_tool || {});
    el("by-tool").innerHTML = rows.length ? rows.map(([id, v]) => {
      const tool = toolById(id);
      return "<tr><td>" + esc(tool ? tool.name : id) + (tool && tool.demo_stand_in ? " " + badge("outline", "DEMO") : "") +
        '</td><td class="num">' + esc(tool ? tool.vibration_m_s2 : "–") + '</td><td class="num">' + esc(v.sessions || 0) +
        '</td><td class="num">' + esc(fmtTrigger(v.trigger_s)) + '</td><td class="num">' + esc(fmtPts(v.points)) + "</td></tr>";
    }).join("") : '<tr><td colspan="5" class="empty">No tool use yet today.</td></tr>';
  }

  function renderWorkOrders() {
    const list = ((state.work_orders || []).filter((w) => w.status !== "closed"))
      .sort((a, b) => (num(b.ts) || 0) - (num(a.ts) || 0));
    el("wo-count").textContent = String(list.length);
    if (!list.length) {
      el("wo-body").innerHTML = '<tr><td colspan="5" class="empty">No open work orders.</td></tr>';
      return;
    }
    el("wo-body").innerHTML = list.map((w) => {
      const st = stationById(w.station_id);
      const d = defectById(w.defect_id);
      const tool = toolById(w.tool_id);
      const mins = num(w.est_trigger_min);
      const est = tool && mins ? 2 * Math.pow(+tool.vibration_m_s2, 2) * (mins / 60) : null;
      const status = WO[w.status] || ["outline", w.status || "Draft"];
      const title = String(w.title || "Work order").split(" — ")[0];      // the station column already says where
      const priority = String(w.priority || "–");
      return "<tr>" +
        '<td><div class="wo-id">' + esc(w.id) + '</div><div class="wo-title">' + esc(title) + "</div>" +
          (d ? '<div class="sub"><span class="mono">' + esc(d.id) + "</span><span>" + esc(d.condition || "") + "</span>" +
               badge(SEVERITY[d.severity] || "secondary", d.severity || "defect") + "</div>"
             : '<div class="sub">Planned job</div>') + "</td>" +
        '<td><span class="mono">' + esc(w.station_id || "–") + '</span><div class="sub">' + esc(st ? st.name : "") + "</div></td>" +
        '<td class="mono" title="' + esc(priority + (w.priority_reason ? " · " + w.priority_reason : "")) + '">' +
          esc(priority.split(" ")[0]) + "</td>" +
        "<td>" + badge(status[0], status[1]) + "</td>" +
        "<td>" + (tool ? esc(tool.name) + '<div class="sub mono">' + (mins ? esc(fmtMin(mins)) + " min" : "") +
          (est !== null ? " · " + esc(fmtPts(est)) + " pts" : "") + "</div>" : '<span class="empty">–</span>') + "</td>" +
        "</tr>";
    }).join("");
  }

  function syncForm() {
    const tools = state.tools || [];
    const tKey = tools.map((t) => t.id + ":" + t.vibration_m_s2).join("|");
    if (tKey !== toolsKey) {
      toolsKey = tKey;
      const sel = el("f-tool");
      const keep = sel.value;
      sel.innerHTML = '<option value="">Choose a tool</option>' + tools.map((t) =>
        '<option value="' + esc(t.id) + '">' + esc(t.name) + " · " + esc(t.vibration_m_s2) + " m/s²</option>").join("");
      if (keep && toolById(keep)) sel.value = keep;
    }
    const stations = state.stations || [];
    const sKey = stations.map((s) => s.id).join("|");
    if (sKey !== stationsKey) {
      stationsKey = sKey;
      const sel = el("f-station");
      const keep = sel.value;
      sel.innerHTML = stations.map((s) => '<option value="' + esc(s.id) + '">' + esc(s.id + " · " + s.name) + "</option>").join("");
      sel.value = (keep && stationById(keep)) ? keep : (state.active_station || (stations[0] && stations[0].id) || "");
    }
    const rec = currentRec();
    if (!formTouched && rec && rec.planned) {          // show the plan under review until someone edits the form
      const p = rec.planned;
      if (p.station_id && stationById(p.station_id)) el("f-station").value = p.station_id;
      if (p.tool_id && toolById(p.tool_id)) el("f-tool").value = p.tool_id;
      if (num(p.est_trigger_min) !== null) el("f-min").value = p.est_trigger_min;
      if (p.task) el("f-task").value = p.task;
    }
  }

  function options(rec) {        // short labels on screen; the planner's full sentence stays as the tooltip
    const out = [];
    (rec.action_details || []).forEach((d) => {
      const alt = rec.alternative || {};
      if (d.kind === "review") return;
      if (d.kind === "lower_vibration") out.push({ label: "Lower-vibration method", sub: alt.tool_name || alt.method || "",
        value: num(d.projected_points) !== null ? fmtPts(d.projected_points) + " pts today" : "", full: d.text });
      else if (d.kind === "split_shifts") out.push({ label: "Split across shifts", value: fmtMin(d.minutes_today) + " min today", full: d.text });
      else if (d.kind === "reassign") out.push({ label: "Reassign", sub: "Qualified technician with lower exposure", full: d.text });
      else if (d.kind === "replan") out.push({ label: "Re-plan before work starts", value: "over limit", full: d.text });
      else if (d.kind === "minimise") out.push({ label: "Minimise further tool use", full: d.text });
      else if (d.kind === "proceed_note") out.push({ label: "No exposure concern", full: d.text });
      else if (d.kind === "confirm_tool") out.push({ label: "Confirm the tool first", full: d.text });
      else out.push({ label: d.text, full: d.text });
    });
    if (!out.length) (rec.actions || []).filter((t) => !/^Supervisor review required/.test(t)).forEach((t) => out.push({ label: t, full: t }));
    return out;
  }

  function renderRecommendation() {
    const rec = currentRec();
    const box = el("rec");
    document.querySelectorAll("[data-decision]").forEach((b) => { b.disabled = !rec; });
    if (!rec) {
      box.innerHTML = '<p class="empty">Plan a repair to see its exposure.</p>';
      return;
    }
    const pair = REC[rec.status] || ["warning", "Review"];
    const p = rec.planned || {};
    const st = stationById(p.station_id || rec.station_id);
    const tool = rec.tool || toolById(p.tool_id) || {};
    const minutes = num(rec.est_trigger_min) !== null ? rec.est_trigger_min : p.est_trigger_min;
    const opts = options(rec);
    box.innerHTML =
      '<div class="rec-top"><div><p class="mini-label">Projected today</p>' +
        '<div class="kpi">' + esc(fmtPts(rec.projected_points)) + '<span class="unit">pts</span></div>' +
        '<p class="rec-sub">+' + esc(fmtPts(rec.added_points)) + " from this job · now " + esc(fmtPts(rec.current_points)) + "</p></div>" +
      '<div class="badges">' + badge(pair[0], pair[1], rec.headline || "") + badge("outline", "Supervisor review") + "</div></div>" +
      '<div class="job"><div class="task" title="' + esc(p.task || rec.task || "") + '">' +
        esc((st ? st.id + " · " : "") + (p.task || rec.task || "Planned repair")) + "</div>" +
        '<div class="sub">' + esc(tool.name || p.tool_id || "") + (num(minutes) !== null ? " · " + esc(fmtMin(minutes)) + " min" : "") +
        (p.work_order_id ? ' · <span class="mono">' + esc(p.work_order_id) + "</span>" : "") + "</div></div>" +
      (opts.length ? '<ul class="opts">' + opts.map((o) =>
        '<li title="' + esc(o.full) + '"><span>' + esc(o.label) + (o.sub ? '<span class="opt-sub">' + esc(o.sub) + "</span>" : "") +
        "</span>" + (o.value ? '<span class="opt-val">' + esc(o.value) + "</span>" : "") + "</li>").join("") + "</ul>" : "");
  }

  function renderDecisions() {
    const log = el("log");
    el("log-clear").hidden = !decisions.length;
    if (!decisions.length) {
      log.innerHTML = '<li><span></span><span class="empty">None yet.</span></li>';
      return;
    }
    log.innerHTML = decisions.map((d) =>
      '<li><time datetime="' + esc(new Date(d.ts * 1000).toISOString()) + '">' + esc(clock(d.ts)) + "</time>" +
      '<span class="what">' + esc(d.label) + "</span>" +
      '<span class="detail">' + esc([d.station_id, d.tool, d.projected !== null && d.projected !== undefined
        ? fmtPts(d.projected) + " pts" : ""].filter(Boolean).join(" · ")) +
      (d.note ? " · “" + esc(d.note) + "”" : "") + "</span></li>").join("");
  }

  function render() {
    if (!state) return;
    renderConn();
    renderHeader();
    syncForm();
    renderExposure();
    renderWorkOrders();
    renderRecommendation();
    renderDecisions();
  }

  // ---------------------------------------------------------------- actions
  function hint(text, isError) {
    const node = el("f-hint");
    node.className = isError ? "error" : "hint";
    node.textContent = text || "";
  }

  ["f-station", "f-task", "f-min"].forEach((id) => el(id).addEventListener("input", () => { formTouched = true; }));
  el("f-tool").addEventListener("change", () => {
    formTouched = true;
    const tool = toolById(el("f-tool").value);
    if (tool && !el("f-min").value && num(tool.typical_trigger_min) !== null) {
      el("f-min").value = tool.typical_trigger_min;
      hint("Typical minutes from the tool profile.");
    }
  });

  el("plan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      station_id: el("f-station").value,
      task: el("f-task").value.trim() || "Planned repair",
      tool_id: el("f-tool").value,
      est_trigger_min: parseFloat(el("f-min").value),
    };
    if (!body.station_id || !body.tool_id || !(body.est_trigger_min > 0)) {
      hint("Choose a station and a tool, and enter minutes.", true);
      return;
    }
    const btn = el("f-submit");
    btn.disabled = true;
    btn.textContent = "Projecting";
    try {
      const res = await fetch(API + "/repair/plan", { method: "POST", headers: { "Content-Type": "application/json" },
                                                      body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
      localRec = data;
      formTouched = false;
      hint("");
      schedule();
    } catch (err) {
      hint("Could not project: " + err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Project exposure";
    }
  });

  document.querySelectorAll("[data-decision]").forEach((b) => b.addEventListener("click", () => {
    const rec = currentRec();
    if (!rec) return;
    const kind = b.getAttribute("data-decision");
    const p = rec.planned || {};
    decisions.unshift({
      id: Date.now(), ts: Date.now() / 1000, kind, label: DECISION[kind],
      station_id: p.station_id || rec.station_id || "", task: p.task || rec.task || "",
      tool: (rec.tool && rec.tool.name) || p.tool_id || "", projected: num(rec.projected_points),
      status: rec.status || "", note: el("d-note").value.trim(),
    });
    saveDecisions();
    el("d-note").value = "";
    const alt = rec.alternative;
    if (kind === "method" && alt && alt.tool_id && toolById(alt.tool_id)) {
      el("f-tool").value = alt.tool_id;
      formTouched = true;
      hint("Switched to " + toolById(alt.tool_id).name + ". Project to compare.");
    }
    renderDecisions();
  }));

  el("log-clear").addEventListener("click", () => { decisions = []; saveDecisions(); renderDecisions(); });

  const tick = () => { el("clock").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  tick();
  setInterval(tick, 15000);
  renderConn();
  renderDecisions();
  loadState();
  connect();
})();
