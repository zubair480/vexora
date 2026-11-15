"""Supervisor fleet layer (S2 band-backend): GET /api/fleet and a {"type":"fleet"} broadcast every second.

W-01 is the REAL band 07 pipeline: everything about it is read from the live BeltGuard state and SQLite
(its heard lines are its transcripts + BeltGuard's replies, its story comes from its timeline events).
W-02..W-05 are SIMULATED bands for the control room. They live in memory only: nothing about them is ever
written to the database, every entry carries is_simulated=True and source "sim", and their clock is
compressed (TIME_SCALE simulated seconds per real second) so a short demo shows a shift's worth of exposure.
Their spoken lines go through S4's real intents.parse_intent/respond on an in-memory context, so the
replies are what BeltGuard would really say.
"""
from __future__ import annotations

import asyncio
import collections
import math
import random
import time

TIME_SCALE = 30.0                      # simulated seconds per real second (SIMULATED workers only)
ACTION, LIMIT = 100.0, 400.0
CRIB = "CRIB"
SIM_LABEL = ("W-02..W-05 are SIMULATED bands (time-compressed x%d) for the control-room demo; "
             "only W-01 is the real band 07." % int(TIME_SCALE))
STATION_NAMES = {"S1": "Tail pulley & loading zone", "S2": "Carry idlers & belt tracking", "S3": "Return idlers",
                 "S4": "Take-up & belt tension", "S5": "Head/drive pulley & discharge", CRIB: "tool crib"}

# Scripts: the first pass is a story; then the loop repeats. Steps:
#   ("say", text)  ("checkout", tool_id)  ("walk", dest, secs)  ("work", secs)  ("work_unsure", secs)
#   ("pause", secs)  ("return",)  ("break", secs)  ("confirm_tap",)  ("confirm_say", text)  ("fall",)
SCRIPTS = {
    "W-03": {"name": "M. Chen", "delay": 1.0, "first": [
        ("say", "BeltGuard, I'm taking the breaker hammer."), ("checkout", "breaker_hammer"),
        ("walk", "S3", 6.0), ("say", "BeltGuard, starting on the chute liner at station three."),
        ("work", 8.0), ("pause", 2.0), ("work", 8.0), ("pause", 2.0), ("work", 8.0), ("pause", 2.0),
        ("work", 8.0), ("pause", 2.0), ("work", 8.0), ("pause", 2.0)],
        "loop": [("work", 7.0), ("pause", 3.0)],
        # after the 100-point alert: ask, rotate to the low-vibration tool
        "on_action": [("pause", 2.0), ("say", "BeltGuard, how long can I keep going?"),
                      ("say", "Switching to the torque wrench."), ("walk", CRIB, 6.0), ("return",),
                      ("checkout", "torque_wrench_hydraulic"), ("walk", "S3", 6.0),
                      ("say", "BeltGuard, back at station three with the torque wrench.")]},
    "W-04": {"name": "R. Silva", "delay": 2.0, "first": [
        ("say", "BeltGuard, taking the impact wrench."), ("checkout", "impact_wrench"), ("walk", "S4", 7.0),
        ("say", "BeltGuard, I'm at station four."), ("work", 5.0), ("pause", 3.0), ("work", 5.0), ("pause", 3.0),
        ("fall",), ("pause", 6.0), ("say", "I'm fine, I just dropped the wrench."),
        ("work", 5.0), ("pause", 3.0)],
        "loop": [("work", 5.0), ("pause", 4.0)]},
    "W-05": {"name": "J. Patel", "delay": 3.0, "first": [
        ("say", "BeltGuard, checking out the hammer drill."), ("checkout", "hammer_drill"), ("walk", "S5", 8.0),
        ("say", "BeltGuard, I'm at station five."), ("work", 4.0), ("pause", 3.0), ("work_unsure", 3.0),
        ("pause", 5.0), ("confirm_tap",), ("work", 4.0), ("pause", 3.0), ("work_unsure", 3.0), ("pause", 5.0),
        ("confirm_say", "Yes, that was the drill.")],
        "loop": [("work", 4.0), ("pause", 4.0), ("work", 4.0), ("pause", 4.0), ("work", 4.0), ("pause", 4.0),
                 ("work", 4.0), ("pause", 4.0), ("work_unsure", 3.0), ("pause", 6.0), ("confirm_tap",)]},
    "W-02": {"name": "A. Okafor", "delay": 0.0, "first": [
        ("say", "BeltGuard, taking the angle grinder."), ("checkout", "angle_grinder"), ("walk", "S2", 6.0),
        ("say", "BeltGuard, I'm at station two."), ("work", 3.0), ("pause", 5.0)],
        "loop": [("work", 3.0), ("pause", 6.0)]},
}
ORDER = ["W-02", "W-03", "W-04", "W-05"]


def _points(app, a: float, hours: float) -> float:
    ex = app.mods.get("exposure") if app is not None else None
    try:
        return float(ex.points(a, hours)) if ex else 2 * a * a * hours
    except Exception:
        return 2 * a * a * hours


def _minutes_to(total: float, target: float, a) -> float | None:
    if not a:
        return None
    return round(max(0.0, target - total) / (2 * a * a) * 60.0, 1)


def _fmt_min(m) -> str:
    if m is None:
        return "?"
    h, mm = divmod(int(round(m)), 60)
    return ("%d h %02d min" % (h, mm)) if h else ("%d min" % mm)


def _verdict(total: float, a, tool_name) -> dict:
    state = "limit" if total >= LIMIT else "action" if total >= ACTION else "within"
    m_act, m_lim = _minutes_to(total, ACTION, a), _minutes_to(total, LIMIT, a)
    if state == "within":
        head = "Within the action value: %.0f of 100 points today." % total
        if tool_name and m_act is not None:
            head += " About %s more with the %s before 100." % (_fmt_min(m_act), tool_name)
    elif state == "action":
        head = "Action value reached: %.0f points today. Supervisor review required." % total
        if tool_name and m_lim is not None:
            head += " %s more with the %s reaches 400." % (_fmt_min(m_lim), tool_name)
    else:
        head = "Limit value reached: %.0f points today. Supervisor review required." % total
    return {"state": state, "minutes_to_action": m_act, "minutes_to_limit": m_lim, "headline": head,
            "total_points": round(total, 2)}


class SimWorker:
    """One SIMULATED band following a script (compressed clock, in memory only)."""

    def __init__(self, wid: str, app, fleet) -> None:
        spec = SCRIPTS[wid]
        self.app, self.fleet = app, fleet
        self.id, self.name = wid, spec["name"]
        self.spec = spec
        self.plan = list(spec["first"])
        self.points = 0.0
        self.trigger_s = 0.0
        self.pending = None                  # unresolved session {"trigger_s", "points"}
        self.battery = random.randint(74, 96)
        self.temp = random.uniform(31.0, 33.5)
        self.motion = collections.deque(maxlen=64)
        self.heard = collections.deque(maxlen=3)
        self.assignment = None
        self.tool_id = None
        self.station = CRIB
        self.walk = None
        self.state = "idle"
        self.last_event = {"kind": "shift_start", "ts": time.time()}
        self.alerted = set()
        self.phase, self.phase_left = "pause", spec["delay"]
        self.burst_trigger = self.burst_points = 0.0
        self.unsure = False
        self.rms = 0.0
        self.swapped = False
        self.elapsed = 0.0
        self.next_fall = None

    # ---- helpers -------------------------------------------------------------------------
    def tool(self, tool_id=None) -> dict:
        tid = tool_id or self.tool_id
        t = next((t for t in (getattr(self.app, "tools", None) or []) if t.get("id") == tid), None)
        return t or {"id": tid, "name": (tid or "tool").replace("_", " "), "vibration_m_s2": 5.0}

    def spoken(self, t=None) -> str:
        t = t or self.tool()
        im = self.app.mods.get("intents") if self.app is not None else None
        try:
            return im.spoken_tool(t) if im is not None and hasattr(im, "spoken_tool") else t["name"].lower()
        except Exception:
            return str(t.get("name") or "tool").lower()

    def _a(self) -> float:
        return float(self.tool().get("vibration_m_s2") or 5.0) if self.tool_id else 0.0

    def _story(self, kind: str, text: str, **extra) -> None:
        now = time.time()
        self.last_event = {"kind": kind, "ts": now}
        self.fleet.story.appendleft({"ts": now, "worker_id": self.id, "worker_name": self.name, "kind": kind,
                                     "text": text + " (SIMULATED)", "is_simulated": True, "simulated": True,
                                     "source": "sim", **extra})

    def _alert(self, kind: str, text: str) -> None:
        self.fleet.alerts.appendleft({"ts": time.time(), "worker_id": self.id, "kind": kind,
                                      "text": text + " (SIMULATED)", "is_simulated": True, "simulated": True,
                                      "source": "sim"})

    def _ctx(self) -> dict:
        t = self.tool() if self.tool_id else None
        v = _verdict(self.points, self._a(), t and t["name"])
        return {"active_station": self.station if self.station != CRIB else None,
                "active_tool": t, "active_tool_id": self.tool_id,
                "assignment": {"tool": t, "since_ts": self.assignment["since_ts"]} if self.assignment else None,
                "tools": getattr(self.app, "tools", None) or [], "stations": self.fleet.stations(),
                "exposure": {"total_points": self.points, "action": ACTION, "limit": LIMIT},
                "verdict": dict(v, basis_tool_id=self.tool_id, basis_vibration_m_s2=self._a()),
                "worker": {"id": self.id, "name": self.name}, "tool_sessions": [], "work_orders": [],
                "unresolved_session": ({"id": "SIM-" + self.id, "status": "unresolved", "tool_id": self.tool_id,
                                        "trigger_s": self.pending["trigger_s"]} if self.pending else None),
                "awaiting_confirmation": self.pending is not None}

    def _speak(self, text: str, result: dict | None = None) -> str:
        """Run the line through S4's real intents (in memory); return BeltGuard's reply ("" = silent)."""
        im = self.app.mods.get("intents") if self.app is not None else None
        reply = ""
        if im is not None:
            ctx = self._ctx()
            try:
                try:
                    parsed = im.parse_intent(text, ctx, source="band")
                except TypeError:
                    parsed = im.parse_intent(text, ctx)
                parsed = parsed or {}
                name = parsed.get("intent")
                if result is None:
                    result = {"ok": True}
                    if name == "tool_checkout":
                        slot = (parsed.get("slots") or {}).get("tool_id")
                        t = self.tool(slot) if slot else self.tool()
                        result.update(tool_id=t["id"], tool_name=t["name"])
                    elif name == "exposure_query":
                        result.update(verdict=ctx["verdict"])
                    elif name in ("none", "report_defect"):
                        result = {"status": "ignored"}
                reply = im.respond(parsed, result, ctx) or ""
            except Exception as e:
                reply = ""
                print("[fleet] intents failed for %s: %r" % (self.id, e), flush=True)
        if reply:                                        # only sentences BeltGuard answered; chat stays out
            self.heard.appendleft({"ts": time.time(), "text": text + " (SIMULATED)", "reply": reply,
                                   "is_simulated": True, "simulated": True})
            self._story("voice", "%s: “%s” → BeltGuard: “%s”" % (self.name, text, reply))
        return reply

    # ---- the script ----------------------------------------------------------------------
    def _begin(self, step) -> None:
        kind = step[0]
        if kind == "say":
            self._speak(step[1])
            self.phase_left = 0.0
        elif kind == "checkout":
            self.tool_id = step[1]
            t = self.tool()
            self.assignment = {"tool": t, "since_ts": time.time()}
            self._story("checkout", "%s unlocked the %s (%s m/s²); locked to %s" % (
                self.name, self.spoken(t), ("%g" % float(t.get("vibration_m_s2") or 0)), self.name))
            self.phase_left = 0.0
        elif kind == "walk":
            dest, secs = step[1], step[2]
            self.walk = {"from": self.station, "to": dest, "progress": 0.0, "seconds": secs}
            self.state = "walking"
            self.phase, self.phase_left = "walk", secs
            self._story("walk", "%s walking to %s" % (self.name, "the tool crib" if dest == CRIB else
                                                      "%s (%s)" % (dest, STATION_NAMES.get(dest, ""))))
        elif kind in ("work", "work_unsure"):
            self.state, self.phase, self.phase_left = "tool_active", "work", step[1]
            self.unsure = kind == "work_unsure"
            self.burst_trigger = self.burst_points = 0.0
            if not self.unsure and self.last_event.get("kind") not in ("start", "voice"):
                self._story("start", "%s running the %s at %s" % (self.name, self.spoken(), self.station))
        elif kind in ("pause", "break"):
            self.state = "unresolved" if self.pending else "idle"
            self.phase, self.phase_left = "pause", step[1]
            if kind == "break":
                self._story("break", "%s on a break" % self.name)
        elif kind == "return":
            t = self.tool()
            self._story("return", "%s returned the %s (%.0f s trigger, %.0f pts today)" % (
                self.name, self.spoken(t), self.trigger_s, self.points))
            self.assignment, self.tool_id = None, None
            self.phase_left = 0.0
        elif kind == "confirm_tap":
            self._confirm("double-tap")
            self.phase_left = 0.0
        elif kind == "confirm_say":
            if self.pending:
                pend = dict(self.pending)
                self._speak(step[1], {"ok": True, "points": pend["points"], "tool_id": self.tool_id,
                                      "session_id": "SIM-" + self.id})
                self._confirm("voice")
            self.phase_left = 0.0
        elif kind == "fall":
            where = "%s (%s)" % (self.station, STATION_NAMES.get(self.station, "").split(" &")[0]) \
                if self.station != CRIB else "the tool crib"
            self._story("fall", "Possible fall: %s at %s" % (self.name, where))
            self._alert("fall", "%s %s: possible drop or fall near %s: check on %s" % (self.id, self.name, where, self.id))
            for v in (1.0, 0.15, 0.05, 2.9, 1.6, 1.0):
                self.motion.append(v)
            self.phase_left = 0.0

    def _confirm(self, how: str) -> None:
        if not self.pending:
            return
        p, self.pending = self.pending, None
        self.points += p["points"]
        self.trigger_s += p["trigger_s"]
        self.state = "idle" if self.state == "unresolved" else self.state
        self._story("confirm", "%s confirmed %.0f s of %s use by %s" % (self.name, p["trigger_s"],
                                                                     self.spoken(), how))

    def _end(self) -> None:
        if self.phase == "walk" and self.walk:
            self.station, self.walk = self.walk["to"], None
            self.state = "unresolved" if self.pending else "idle"
            self._story("arrive", "%s arrived at %s" % (self.name, "the tool crib" if self.station == CRIB else
                                                        "%s (%s)" % (self.station, STATION_NAMES.get(self.station, ""))))
        elif self.phase == "work" and self.unsure:
            self.pending = {"trigger_s": self.burst_trigger, "points": self.burst_points}
            self.points -= self.burst_points
            self.trigger_s -= self.burst_trigger
            self.state = "unresolved"
            text = "%s: unresolved %s activity, %.0f s: confirm or reject" % (self.id, self.spoken(),
                                                                              self.burst_trigger)
            self._alert("confirm", text)
            reply = "Was that the %s, %.0f seconds? Say yes or no, or double-tap to confirm." % (
                self.spoken(), self.burst_trigger)
            self._story("confirm", "BeltGuard asked %s: “%s”" % (self.name, reply))
        elif self.phase == "work":
            self.state = "idle"

    def tick(self, dt: float) -> None:
        self.elapsed += dt
        self.battery = max(5.0, self.battery - dt / 180.0)
        self.temp += random.uniform(-0.05, 0.05)
        guard = 0
        while dt > 0 and guard < 50:
            guard += 1
            if self.phase_left <= 0:
                if not self.plan:
                    self.plan = list(self.spec["loop"])
                self._begin(self.plan.pop(0))
                continue
            step = min(dt, self.phase_left)
            if self.phase == "work" and self.tool_id:
                sim_s = step * TIME_SCALE
                pts = _points(self.app, self._a(), sim_s / 3600.0)
                self.points += pts
                self.trigger_s += sim_s
                self.burst_points += pts
                self.burst_trigger += sim_s
            if self.walk:
                self.walk["progress"] = min(1.0, self.walk["progress"] + step / max(0.1, self.walk["seconds"]))
            self.phase_left -= step
            dt -= step
            if self.phase_left <= 0:
                self._end()
        for mark, kind in ((ACTION, "action"), (LIMIT, "limit")):
            if self.points >= mark and kind not in self.alerted:
                self.alerted.add(kind)
                word = "action" if kind == "action" else "limit"
                self._alert(kind, "%s %s: exposure %s value (%d pts) reached" % (self.id, self.name, word, int(mark)))
                self._story("alert", "%s crossed the %d-point %s value; told to rotate" % (self.name, int(mark), word),
                            level=kind)
                if kind == "action" and self.spec.get("on_action"):
                    self.plan = list(self.spec["on_action"]) + list(self.spec["loop"])
                    self.phase_left = min(self.phase_left, 0.5)
        if self.id != "W-03" and not self.swapped and self.points >= 75.0:
            # like a planner recommendation: near the action value, switch to the low-vibration method
            self.swapped = True
            back = self.station if self.station != CRIB else "S2"
            self.plan = [("say", "BeltGuard, how much exposure have I had today?"), ("walk", CRIB, 6.0), ("return",),
                         ("checkout", "torque_wrench_hydraulic"), ("walk", back, 6.0)] + list(self.spec["loop"])
        if self.id == "W-04":                            # a reminder fall every 5 min after the scripted one
            if self.next_fall is None:
                self.next_fall = self.elapsed + 300.0
            elif self.elapsed >= self.next_fall:
                self.next_fall = self.elapsed + 300.0
                self.plan.insert(0, ("fall",))
        self._motion(8)

    def _motion(self, n: int) -> None:
        vals = []
        for i in range(n):
            if self.state == "tool_active":
                v = 1.0 + random.gauss(0, 0.45)
            elif self.state == "walking":
                v = 1.0 + 0.28 * abs(math.sin(time.time() * 11.3 + i)) - 0.12 + random.gauss(0, 0.03)
            else:
                v = 1.0 + random.gauss(0, 0.004)
            vals.append(round(abs(v), 3))
        self.motion.extend(vals)
        m = sum(vals) / len(vals)
        self.rms = round(math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)), 4)

    def view(self) -> dict:
        t = self.assignment["tool"] if self.assignment else None
        a = float((t or {}).get("vibration_m_s2") or 0) or None
        return {"id": self.id, "name": self.name, "band": None, "is_simulated": True, "simulated": True,
                "source": "sim",
                "station_id": self.station, "walk": dict(self.walk) if self.walk else None,
                "assignment": {"tool": {k: t.get(k) for k in ("id", "name", "vibration_m_s2")},
                               "since_ts": self.assignment["since_ts"]} if self.assignment else None,
                "state": self.state, "motion_rms": self.rms, "last_motion": list(self.motion),
                "trigger_s_today": round(self.trigger_s, 1), "points_today": round(self.points, 2),
                "pending_confirmation": ({"trigger_s": round(self.pending["trigger_s"], 1),
                                          "points": round(self.pending["points"], 2)} if self.pending else None),
                "verdict": _verdict(self.points, a, t and t.get("name")), "battery": int(self.battery),
                "temperature_c": round(self.temp, 1), "last_event": dict(self.last_event),
                "heard": list(self.heard), "connected": True}


def _patch_confirm_window(app) -> None:
    """Rehearsal hot-fix (S0, no restart allowed): double-tap / spoken-yes confirmations only reach unresolved
    sessions that ENDED in the last 10 minutes. app.py carries the same rule natively from its next restart;
    this applies it to the running instance now, because fleet.py hot-reloads and app.py does not."""
    import types

    def latest_recent(self, max_age_s: float = 600.0):
        rows = self.store.query("SELECT * FROM tool_sessions WHERE ts >= ? AND status = 'unresolved' "
                                "AND COALESCE(wall_end, wall_start, ts) >= ? ORDER BY ts DESC LIMIT 1",
                                (self.shift_start(), time.time() - max_age_s))
        return rows[0] if rows else None
    try:
        app.latest_unresolved_session = types.MethodType(latest_recent, app)
    except Exception as e:
        print("[fleet] confirm-window patch failed:", repr(e), flush=True)


def _ensure_job_now(app) -> None:
    """The job checklist exists from the start of a shift (all six steps to do), not only after the first sentence:
    create it on the agent and broadcast {type:"job"}."""
    try:
        agent = app.job_agent if getattr(app, "job_agent", None) is not None else app._agent()
        if agent is None:
            return
        if not (agent.snapshot() or {}).get("job"):
            agent.on_state(app.intent_context())
        job = (agent.snapshot() or {}).get("job")
        loop = getattr(app.hub, "loop", None)
        if job and loop is not None and loop.is_running():
            loop.create_task(app.hub.broadcast({"type": "job", "source": "rules", "job": app._job_view(job)}))
            app.changed()
    except Exception as e:
        print("[fleet] ensure job failed:", repr(e), flush=True)


def _patch_job_on_reset(app) -> None:
    """Hot-fix (S0, no restart during the user's session): a reset (new agent) creates the default job at once.
    app.py carries the same rule natively from its next restart."""
    import types
    orig = type(app).new_job_agent

    def new_job_agent(self):
        agent = orig(self)
        _ensure_job_now(self)
        return agent
    try:
        app.new_job_agent = types.MethodType(new_job_agent, app)
        _ensure_job_now(app)                             # the agent running right now too
    except Exception as e:
        print("[fleet] job-on-reset patch failed:", repr(e), flush=True)


def _drop_sustained_patches(app) -> None:
    """The provisional sustained-session hot-fix (16:47) is withdrawn: S0's detector.py now promotes sustained
    motion to tool_active, so the normal session path counts it. Remove its instance patches if still installed."""
    for name in ("confirm_session", "band_event", "assignment_view"):
        fn = app.__dict__.get(name)
        if fn is not None and "_patch_sustained" in getattr(getattr(fn, "__func__", None), "__qualname__", ""):
            app.__dict__.pop(name, None)
    st = app.__dict__.pop("_sustained", None)
    if st and st.get("sid") and app.active_session_id == st.get("sid"):
        app.active_session_id = None                 # never leave a counter running with nothing to close it


def _patch_auto_confirm(app) -> None:
    """User direction (S0, 16:48): no approval steps. A W-01 detector session with a tool checked out is counted
    automatically when it ends (band-detected trigger time x the locked tool's HSE magnitude: screening estimate).
    No confirm/reject prompt. Without a tool there is no magnitude to count with: it stays unresolved, silently."""
    import types
    cls = type(app)
    orig_det = cls.on_detector

    async def on_detector(self, ev, src):
        if isinstance(ev, dict) and ev.get("kind") == "tool_stop" and ev.get("status") != "confirmed":
            sid = self.active_session_id
            row = self.store.get("tool_sessions", sid) if sid else None
            if self.tool((row or {}).get("tool_id") or self.active_tool_id) is not None:
                ev = dict(ev, status="confirmed")    # counted automatically: tool locked + band evidence
        return await orig_det(self, ev, src)

    async def end_active_session(self, why: str):
        sid = self.active_session_id
        if not sid:
            return None
        self.active_session_id = None
        self.ignore_stop = True                      # the detector's own stop for it comes later: ignore
        row = self.store.get("tool_sessions", sid)
        if not row:
            return None
        now = time.time()
        trig = max(0.0, now - float(row.get("wall_start") or now))
        tool = self.tool(row.get("tool_id") or self.active_tool_id)
        status = "confirmed" if tool else "unresolved"
        row = self.store.update("tool_sessions", sid, wall_end=now, trigger_s=trig, status=status,
                                tool_id=tool["id"] if tool else None)
        await self.event("tool", "tool_stop" if tool else "tool_unresolved",
                         ("Tool use %.0f s ended by %s" % (trig, why)) if tool else
                         ("Tool activity %.0f s ended by %s: no tool checked out, not counted" % (trig, why)),
                         station_id=row.get("station_id"), refs={"tool_session_id": sid}, source="user")
        if tool:
            await self.add_exposure(row, tool, trig, "band")
        await self.update_led()
        asyncio.get_running_loop().create_task(self.job_tool(row))
        return row

    async def ask_confirmation(self, row, tool, trig):
        return None                                  # no approval prompts (user direction)
    try:
        app.on_detector = types.MethodType(on_detector, app)
        app.end_active_session = types.MethodType(end_active_session, app)
        app.ask_confirmation = types.MethodType(ask_confirmation, app)
    except Exception as e:
        print("[fleet] auto-confirm patch failed:", repr(e), flush=True)


class Fleet:
    def __init__(self, app) -> None:
        self.app = app
        if app is not None and hasattr(app, "store"):
            _patch_confirm_window(app)
            if hasattr(type(app), "new_job_agent"):
                _patch_job_on_reset(app)
            try:
                _drop_sustained_patches(app)
            except Exception as e:
                print("[fleet] dropping sustained patches failed:", repr(e), flush=True)
            if all(hasattr(type(app), m) for m in ("on_detector", "end_active_session", "ask_confirmation")):
                _patch_auto_confirm(app)
        self.alerts = collections.deque(maxlen=60)
        self.story = collections.deque(maxlen=120)
        self._stations = None
        self.sim = [SimWorker(wid, app, self) for wid in ORDER]
        self.seen_event_ts = time.time()

    def stations(self) -> list:
        if self._stations is None:
            try:
                self._stations = self.app.store.stations()
            except Exception:
                self._stations = [{"id": k, "number": int(k[1]), "name": v} for k, v in STATION_NAMES.items()
                                  if k != CRIB]
        return self._stations

    # ---- W-01: the real band 07 pipeline ------------------------------------------------
    def _w01_motion(self) -> list:
        try:
            rows = self.app.store.query("SELECT ts, ax, ay, az FROM motion_samples ORDER BY id DESC LIMIT 320")
        except Exception:
            return []
        if not rows or time.time() - float(rows[0]["ts"] or 0) > 5.0:
            return []                                   # no live readings in the last 5 s
        rows = rows[::-1]
        step = max(1, len(rows) // 64)
        return [round(math.sqrt(r["ax"] ** 2 + r["ay"] ** 2 + r["az"] ** 2), 3)
                for r in rows[::step][-64:] if r["ax"] is not None]

    def _w01_heard(self, ss: float) -> list:
        # PRIVACY: only lines that led to a real command, defect or job step; old parser replies to overheard
        # chat (before the wake word, intent unset) never reach the control room
        rows = self.app.store.query("SELECT ts, text, response, intent, acted FROM transcripts WHERE ts >= ? "
                                    "AND response IS NOT NULL "
                                    "AND response != '' AND ((result_status = 'ok' AND ((intent IS NOT NULL AND intent "
                                    "NOT IN ('none', 'chat')) OR defect_id IS NOT NULL)) OR (acted IS NOT NULL AND acted != '')) "
                                    "ORDER BY ts DESC LIMIT 3", (ss,))
        out = []
        for r in rows:
            if (r.get("intent") or "none") in ("none", "chat"):
                # PRIVACY: only the job agent acted on this sentence; show what was done, never the raw words
                # (step phrases are often said in the middle of private conversation)
                text = "Job step: " + str(r.get("acted") or "").replace("Step: ", "").split(" · ")[0][:90]
            else:
                text = (r["text"] or "")[:100]
            out.append({"ts": r["ts"], "text": text, "reply": r["response"] or "", "is_simulated": False})
        return out

    STORY_KINDS = {"tool_checkout": "checkout", "tool_return": "return", "tool_start": "start", "station": "arrive",
                   "transcript": "voice", "threshold": "alert", "safety": "fall", "tool_confirmed": "confirm",
                   "double_tap": "confirm", "tool_unresolved": "confirm", "break": "break", "agent_action": "agent"}
    CHAT_DETAILS = ("", "Chat: no action", "Not a defect report: no action")

    def _w01_story(self, ss: float) -> list:
        kinds = tuple(self.STORY_KINDS)
        rows = self.app.store.query("SELECT ts, kind, title, detail, refs FROM shift_events WHERE ts >= ? AND kind IN (%s) "
                                    "ORDER BY ts DESC LIMIT 30" % ",".join("?" * len(kinds)), (ss, *kinds))
        name = self.app.store.worker().get("name") or "W-01"
        out = []
        for r in rows:
            text = r["title"] or ""
            if r["kind"] == "transcript":
                refs = r.get("refs") or {}
                if (r.get("detail") or "") in self.CHAT_DETAILS or not (refs.get("intent") or refs.get("defect_id")):
                    continue                             # overheard chat from the real band never goes out
                text += " → BeltGuard: “%s”" % r["detail"]
            entry = {"ts": r["ts"], "worker_id": "W-01", "worker_name": name, "kind": self.STORY_KINDS[r["kind"]],
                     "text": "%s: %s" % (name, text), "is_simulated": False, "source": "band"}
            if r["kind"] == "threshold":
                entry["level"] = "limit" if "400" in text else "action"
            out.append(entry)
        return out

    def _w01(self) -> dict:
        app = self.app
        ss = app.shift_start()
        records = app.store.since("exposure_records", ss)
        assignment, verdict = app.assignment_view(records)
        conn = dict(app.connection)
        if app.bridge is not None:
            conn.update({k: v for k, v in app.bridge.status().items() if k in ("state", "battery", "transport")})
        recent_unresolved = app.store.query("SELECT id FROM tool_sessions WHERE ts >= ? AND status = 'unresolved' "
                                            "LIMIT 1", (time.time() - 300,))
        det = app.detector.get("state") if isinstance(app.detector, dict) else None
        if app.active_session_id:
            state = "tool_active"
        elif recent_unresolved or time.time() < getattr(app, "awaiting_confirmation_until", 0):
            state = "unresolved"
        elif det == "walking":
            state = "walking"
        else:
            state = "idle"
        last = app.store.query("SELECT kind, ts FROM shift_events WHERE ts >= ? ORDER BY ts DESC LIMIT 1", (ss,))
        trig = sum(float(r.get("trigger_s") or 0) for r in records if r.get("status") != "rejected")
        feats = (app.detector or {}).get("features") or {}
        live = conn.get("state") == "connected"
        return {"id": "W-01", "name": app.store.worker().get("name") or "Technician (band 07)", "band": 7,
                "is_simulated": False, "source": "band", "station_id": app.active_station, "walk": None,
                "assignment": ({"tool": {k: assignment["tool"].get(k) for k in ("id", "name", "vibration_m_s2")},
                                "since_ts": assignment["since_ts"]} if assignment and assignment.get("tool") else None),
                "state": state, "motion_rms": feats.get("acc_rms") if live else None,
                "last_motion": self._w01_motion(), "trigger_s_today": round(trig, 1),
                "points_today": round(float(verdict.get("total_points") or 0), 2), "pending_confirmation": None,
                "verdict": {"state": verdict.get("state"), "minutes_to_action": verdict.get("minutes_to_action"),
                            "minutes_to_limit": verdict.get("minutes_to_limit"), "headline": verdict.get("headline"),
                            "total_points": verdict.get("total_points")},
                "battery": conn.get("battery"), "temperature_c": None,
                "last_event": {"kind": last[0]["kind"], "ts": last[0]["ts"]} if last else None,
                "heard": self._w01_heard(ss), "connected": live, "transport": conn.get("transport")}

    def _w01_alerts(self) -> None:
        rows = self.app.store.query("SELECT ts, kind, title FROM shift_events WHERE ts > ? AND kind IN "
                                    "('threshold', 'safety', 'tool_unresolved') ORDER BY ts", (self.seen_event_ts,))
        for r in rows:
            self.seen_event_ts = max(self.seen_event_ts, float(r["ts"]))
            kind = {"safety": "fall", "tool_unresolved": "confirm"}.get(r["kind"])
            if r["kind"] == "threshold":
                kind = "limit" if "400" in (r["title"] or "") else "action"
            self.alerts.appendleft({"ts": r["ts"], "worker_id": "W-01", "kind": kind, "text": r["title"],
                                    "is_simulated": False, "source": "band"})

    # ---- the whole fleet ----------------------------------------------------------------
    def payload(self) -> dict:
        try:
            w01 = self._w01()
            w01_story = self._w01_story(self.app.shift_start())
        except Exception as e:
            w01 = {"id": "W-01", "name": "Technician (band 07)", "band": 7, "is_simulated": False, "source": "band",
                   "state": "idle", "heard": [], "error": str(e)[:120]}
            w01_story = []
        story = sorted(list(self.story) + w01_story, key=lambda s: s["ts"], reverse=True)[:40]
        return {"workers": [w01] + [w.view() for w in self.sim], "alerts": list(self.alerts)[:30], "story": story,
                "simulated_workers": [w.id for w in self.sim], "time_scale": TIME_SCALE, "label": SIM_LABEL,
                "ts": time.time()}

    async def run(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            dt, last = now - last, now
            try:
                for w in self.sim:
                    w.tick(dt)
                self._w01_alerts()
                if self.app.hub.clients:
                    await self.app.hub.broadcast({"type": "fleet", "source": "system", **self.payload()})
            except Exception as e:
                print("[fleet] tick failed:", repr(e), flush=True)
