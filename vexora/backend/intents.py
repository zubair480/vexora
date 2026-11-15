"""Vexora voice intents. Owner: S4 hav-brain.

    parse_intent(text, context) -> {"intent", "slots", "confidence", "clarify", "source", "text"}
    respond(intent, result, state) -> str     one short sentence for the laptop to speak ("" = say nothing)

Deterministic rules, no key needed, written for Deepgram's phrasing (digits or number words,
contractions, filler words). It never invents a station or a tool: when the words do not pin one
down, "clarify" holds a question, and the caller must speak it and NOT act.

Intents
    select_station   "I'm at station three", "at the return idlers"      slots: station_id
    report_defect    "return idler seized at station 3"                   hand the text to inspection.extract_defect
    tool_checkout    "taking / checking out / unlock / grab the hammer drill"   slots: tool_id
    tool_return      "returning / done with / finished with the drill"   slots: tool_id
    exposure_query   "how much time do I have", "what's my exposure", "am I safe"   slots: tool_id (optional)
    confirm_session  "yes that was the tool", "confirm"                   slots: tool_session_id
    reject_session   "no, that wasn't the tool"                           slots: tool_session_id
    job_complete     "idler replaced", "job done at station three"        slots: work_order_id, station_id
                     -> the work order becomes awaiting_verification; Vexora never closes work
    isolation_claim  "conveyor isolated", "locked out"                    slots: station_id (optional)
                     -> log it as the technician's claim; an authorized person must verify it
    take_break       "taking a break"
    help             "what can I say"
    none             anything else: pass the text to inspection.extract_defect (it ignores chit-chat)

context: the backend snapshot (GET /api/state) or any dict with the same keys: stations, tools,
active_station, active_tool, tool_sessions, work_orders, exposure.
"""
from __future__ import annotations

import re
import time
from typing import Optional

UNRESOLVED_WINDOW_S = 600   # "yes" / "confirm" only reaches tool sessions that ended in the last 10 minutes

try:  # works as backend.intents and as a top-level module with backend/ on sys.path
    from . import exposure, inspection  # type: ignore
except ImportError:
    import exposure  # type: ignore
    import inspection  # type: ignore

# ---------------------------------------------------------------- text

_LEADING_FILLER = re.compile(r"^(?:(?:um+|uh+|er+|erm|hmm+|okay|ok|so|well|right|alright|hey|and)\s+)+")


def _clean(text: str) -> str:
    t = inspection._norm(text).replace("-", " ")
    t = re.sub(r"\bi\s+am\b", "i'm", t)
    t = re.sub(r"\bim\b", "i'm", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _LEADING_FILLER.sub("", t)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9'])(?:" + pattern + r")(?![a-z0-9])")


_HELP = _rx(r"what can i (?:say|do|ask)|what do i say|help(?: me)?|list (?:the )?commands|voice commands|"
            r"what are (?:my|the) options")
_CONFIRM = _rx(r"confirm(?:ed)?|yes,? (?:that|it) (?:was|is) (?:the |a |my )?tool|that was (?:the |a |my )?tool|"
               r"yes,? i was using (?:it|the tool|a tool)|correct,? (?:that|it) was (?:the |a )?tool")
_REJECT = _rx(r"reject|that (?:wasn'?t|was not|isn'?t|is not) (?:the |a |my )?tool|not (?:a|the) tool|"
              r"i (?:wasn'?t|was not) using (?:a|the|any) tool|false alarm|ignore (?:that|it)")
_BARE_YES = re.compile(r"^(?:yes|yeah|yep|yup|correct|that'?s right|right|affirmative)$")
_BARE_NO = re.compile(r"^(?:no|nope|nah|negative|wrong)$")
_ISOLATION = _rx(r"isolated|isolation (?:is )?(?:on|done|in place|complete)|lock(?:ed)? out|lockout|tag(?:ged)? out|"
                 r"locks? (?:are |is )?on|locked and tagged")
_ISO_NEGATED = _rx(r"(?:not|isn'?t|aren'?t|never|no|hasn'?t|haven'?t) (?:been |yet )?(?:isolated|locked out|tagged out)")
_BREAK = _rx(r"(?:taking|take|having|going (?:on|for)|on) (?:a |my |the |our )?"
             r"(?:(?:\d+|five|ten|fifteen|twenty|thirty) ?(?:minute|min)s? )?(?:break|rest|lunch|tea break|"
             r"coffee break|smoko)|break time|taking five|stepping away|back in (?:\d+|five|ten|fifteen) minutes")
_NOT_A_PART = r"(?! (?:idler|roller|side|strand|belt|pulley))"      # "return idler" is a conveyor part, not a verb
_RETURN = _rx(r"returning" + _NOT_A_PART + r"|return(?:ed)?" + _NOT_A_PART +
              r"|done with|finished (?:with|using)|putting (?:it |the [a-z ]+? )?(?:away|back)|"
              r"put (?:it |the [a-z ]+? )?(?:away|back)|dropping off|handing (?:it |the [a-z ]+? )?back|"
              r"checking (?:it |the [a-z ]+? )?in|check(?:ed)? in|signing (?:it )?in|no longer using|stopped using")
_CHECKOUT_STRONG = _rx(r"check(?:ing)? out|checkout|sign(?:ing)? out|unlock(?:ing)?|assign(?:ing)? me|book(?:ing)? out")
_CHECKOUT_WEAK = _rx(r"taking|take|grab(?:bing)?|picking up|pick up|using|i'?ll use|i need|i'?ll need|switching to|"
                     r"start(?:ing)? (?:with|on)|got the|getting|get")
_TOOL_WORD = _rx(r"tool|gun")
_RETURN_IT = _rx(r"(?:returning|return|done with|finished with|finished using|put|putting|hand|handing|check|checking) "
                 r"(?:it|that one|this one)")
_BARE_FILLER = {"the", "a", "an", "my", "please", "now", "ok", "okay", "and"}
_JOB_DONE = _rx(r"(?:job|work|repair|task|it|that)(?:'s| is| has been| was)? (?:done|complete|completed|finished|sorted)|"
                r"all done|finished (?:the |that |this )?(?:job|repair|work|task)|"
                r"completed (?:the |that |this )?(?:job|repair|work|task|work order)|"
                r"work order (?:is )?(?:done|complete|completed|finished)")
_PAST_FIX = _rx(r"replaced|fixed|repaired|changed out|swapped(?: out)?|realigned|re aligned|tightened")
_NOT_DONE = _rx(r"needs?|need to|has to|have to|must|should|to be|going to|gonna|will|not|isn'?t|wasn'?t|can'?t")
_EXPOSURE = _rx(r"how much (?:time|longer)|how long (?:can|do|have|until|before|left)|what'?s my (?:exposure|score|points|total)|"
                r"what is my (?:exposure|score|total)|my exposure|exposure|how many points|"
                r"am i (?:safe|ok|okay|alright|all right|good)|time left|minutes left|points (?:left|today)")
# Stricter variants for the always-on band mic, where most sentences are chat.
_HELP_STRICT = re.compile(r"^(?:help|help me)$|(?<![a-z])(?:what can i say|what do i say|voice commands|"
                          r"list (?:the )?commands)(?![a-z])")
_ISOLATION_STRONG = _rx(r"lock(?:ed)? out|lockout|tag(?:ged)? out|isolation (?:is )?(?:on|done|in place|complete)|"
                        r"locks? (?:are |is )?on|locked and tagged")
_ISOLATED = _rx(r"isolated")
_EQUIPMENT_CONTEXT = _rx(r"conveyor'?s?|cv ?\d*|belt|drive|station|machine|plant|motor|pulley|gearbox|it'?s|it is|"
                         r"everything|all|is now")
_JOB_DONE_STRICT = _rx(r"(?:job|repair|task|work order|the work)(?:'s| is| has been| was)? (?:done|complete|completed|finished)|"
                       r"finished (?:the |that |this )?(?:job|repair|task)|"
                       r"completed (?:the |that |this )?(?:job|repair|task|work order)")
_EXPOSURE_STRICT = _rx(r"how much (?:time|longer)|how long (?:can|do|have|until|before|left)|"
                       r"what'?s my (?:exposure|score|points|total)|what is my (?:exposure|score|total)|my exposure|"
                       r"how many points|am i (?:safe|ok|okay)|time left|minutes left")
_BREAK_MINUTES = re.compile(r"(\d+|five|ten|fifteen|twenty|thirty)\s*(?:minute|min)")
_MINUTE_WORDS = {"five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}
_LENIENT_SOURCES = {"typed", "user", "keyboard", "text"}

_GENERIC_PARTS = {"Belt", "Idler", "Pulley", "Bearing", "Guard", "Chute", "Drive motor"}
_AMBIGUOUS_PARTS = {"Belt", "Guard"}      # "my belt is loose", "security guard": these need a place on the band mic
# Answers to "Was that the hammer drill, 20 seconds? Say yes or no" need no wake word while the question is open.
_ANSWER_NO = re.compile(r"^(?:no|nope|nah|negative|wrong)(?![a-z])|wasn'?t|was not|not (?:a|the|my) tool")
_ANSWER_YES = re.compile(r"^(?:yes|yeah|yep|yup|correct|affirmative|that'?s right|that'?s correct|it was)(?![a-z])")


def split_wake_word(text: str) -> tuple[bool, str]:
    """(said the wake word "Vexora"?, the text without it). Same rule as inspection.strip_wake_word:
    at the start of the sentence (after optional "hey / ok / um"), never after "the"."""
    return inspection.strip_wake_word(text)


def _strict_band_report(text: str) -> Optional[dict]:
    """Without the wake word only a clear inspection report is heard: a named part AND a problem word.
    A generic part ("belt", "idler") also needs a place, so "my belt is loose" stays chat."""
    if not inspection.is_inspection_report(text, allow_trigger=False):
        return None
    rules = inspection._rules_extract(text)
    component = rules.get("component")
    # "idler change needed": idler/pulley/bearing/chute are conveyor-only words; "belt"/"guard" still need a place
    if not component or (component in _AMBIGUOUS_PARTS and not rules.get("mentions_place")):
        return None
    return rules

_PRESENCE = _rx(r"i'm (?:now )?(?:at|on)|now at|arrived (?:at)?|arriving at|at station|heading (?:to|over to)|going to|"
                r"moving to|walking to|over at|here at|we're at|reached|got to|moving on to|next station")

# ---------------------------------------------------------------- tools

_TOOL_ALIASES = {
    "hammer_drill": ["hammer drill", "hammerdrill", "rotary hammer", "sds drill", "impact drill", "masonry drill"],
    "drill": ["drill", "power drill", "cordless drill", "drill driver", "rotary drill"],
    "impact_wrench_1in": ["one inch impact wrench", "1 inch impact wrench", "one inch impact", "1 inch impact",
                          "1in impact wrench", "big impact wrench", "big rattle gun"],
    "impact_wrench": ["impact wrench", "half inch impact wrench", "half inch impact", "rattle gun", "impact gun", "rattler"],
    "angle_grinder": ["angle grinder", "grinder", "cut off wheel"],
    "power_jack": ["power jack", "belt lifter", "belt jack", "hydraulic jack", "jack"],
    "breaker_hammer": ["breaker hammer", "breaker", "jackhammer", "jack hammer", "demolition hammer", "concrete breaker"],
    "needle_scaler_vr": ["vibration reduced needle scaler", "vibration reduced scaler", "low vibration needle scaler",
                         "low vibration scaler", "anti vibration scaler"],
    "needle_scaler": ["needle scaler", "needle gun"],
    "chipping_hammer": ["chipping hammer", "chipper", "chipping gun", "air chisel"],
    "torque_wrench_hydraulic": ["hydraulic torque wrench", "torque wrench", "torque multiplier"],
    "demo_tool": ["demo tool", "stand in tool", "stand in", "standin", "phone"],
}
_GENERIC_TOOL = {   # words that name a kind of tool but not which one
    "hammer": ["hammer_drill", "breaker_hammer", "chipping_hammer"],
    "wrench": ["impact_wrench", "impact_wrench_1in", "torque_wrench_hydraulic"],
    "scaler": ["needle_scaler", "needle_scaler_vr"],
    "impact": ["impact_wrench", "impact_wrench_1in"],
}
_SPOKEN = {"impact_wrench": "impact wrench", "impact_wrench_1in": "one-inch impact wrench", "angle_grinder": "angle grinder",
           "needle_scaler": "needle scaler", "needle_scaler_vr": "vibration-reduced needle scaler",
           "chipping_hammer": "chipping hammer", "drill": "drill", "hammer_drill": "hammer drill",
           "breaker_hammer": "breaker hammer", "power_jack": "power jack", "torque_wrench_hydraulic": "torque wrench",
           "demo_tool": "demo tool"}


def _phrase_rx(phrase: str) -> re.Pattern:
    body = r"\s*".join(re.escape(w) for w in phrase.split())
    return re.compile(r"(?<![a-z0-9])" + body + r"(?:s|es)?(?![a-z0-9])")


_ALIAS_RX = {tid: [(a, _phrase_rx(a)) for a in aliases] for tid, aliases in _TOOL_ALIASES.items()}
_GENERIC_RX = {word: _phrase_rx(word) for word in _GENERIC_TOOL}


def _only_tool_words(t: str) -> bool:
    """True when the sentence is just a tool name ("hammer drill", "the grinder")."""
    s = t
    for rxs in _ALIAS_RX.values():
        for _alias, rx in rxs:
            s = rx.sub(" ", s)
    return not [w for w in s.split() if w not in _BARE_FILLER]


def spoken_tool(tool) -> str:
    """A tool name that sounds right out loud (no inch marks or brackets)."""
    if not tool:
        return "tool"
    tid = tool.get("id") if isinstance(tool, dict) else tool
    if tid in _SPOKEN:
        return _SPOKEN[tid]
    name = tool.get("name", tid) if isinstance(tool, dict) else str(tid)
    return re.sub(r"\s*\([^)]*\)|[\"']", "", str(name)).strip().lower() or "tool"


def find_tools(text: str, tools: list[dict]) -> tuple[list[str], list[str]]:
    """(tool ids named, candidate ids for a generic word like "hammer"). Longest alias wins."""
    t = _clean(text)
    known = {tool.get("id") for tool in tools or []}
    hits = []                                    # (start, end, tool_id)
    for tid, rxs in _ALIAS_RX.items():
        if tid not in known:
            continue
        for _alias, rx in rxs:
            for m in rx.finditer(t):
                hits.append((m.start(), m.end(), tid))
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept: list = []
    for h in hits:                               # drop matches inside a longer one ("drill" in "hammer drill")
        if not any(k[0] <= h[0] and h[1] <= k[1] for k in kept):
            kept.append(h)
    named = list(dict.fromkeys(tid for _, _, tid in sorted(kept)))
    generic: list[str] = []
    if not named:
        for word, rx in _GENERIC_RX.items():
            if rx.search(t):
                generic += [tid for tid in _GENERIC_TOOL[word] if tid in known and tid not in generic]
    return named, generic


# ---------------------------------------------------------------- context

def _context(context) -> dict:
    c = context or {}
    stations = c.get("stations") or inspection.DEFAULT_STATIONS
    tools = c.get("tools") or exposure.load_tool_profiles()
    active_tool = c.get("active_tool")
    if isinstance(active_tool, dict):
        active_tool = active_tool.get("id")
    active_tool = active_tool or c.get("active_tool_id")
    assignment = c.get("assignment") or {}
    if not active_tool and isinstance(assignment, dict):
        tool = assignment.get("tool")
        active_tool = tool.get("id") if isinstance(tool, dict) else assignment.get("tool_id")
    sessions = [s for s in (c.get("tool_sessions") or []) if isinstance(s, dict)]
    if "unresolved_session" in c:               # the backend already picked it (recent sessions only); None means none
        unresolved = c.get("unresolved_session")
    else:                                       # same rule as the backend: ended (or started) in the last 10 minutes
        cutoff = time.time() - UNRESOLVED_WINDOW_S
        ended = lambda s: float(s.get("wall_end") or s.get("wall_start") or s.get("ts") or 0)
        pending = [s for s in sessions if s.get("status") == "unresolved" and ended(s) >= cutoff]
        unresolved = max(pending, key=ended) if pending else None
    if isinstance(unresolved, dict):
        unresolved = unresolved.get("id")
    return {"stations": stations, "tools": tools, "active_station": c.get("active_station") or c.get("active_station_id"),
            "active_tool": active_tool, "unresolved": unresolved,
            "work_orders": c.get("work_orders") or c.get("open_work_orders") or [],
            "awaiting_confirmation": bool(c.get("awaiting_confirmation"))}


def _station_menu(stations) -> str:
    return ", ".join("%s %s" % (s.get("number") or s.get("id"), s.get("name")) for s in inspection._stations(stations))


def _result(intent: str, text: str, confidence: float, clarify: Optional[str] = None, ignore: bool = False,
            **slots) -> dict:
    """ignore=True: band-mic chat. The caller does nothing: no defect extraction, nothing spoken."""
    return {"intent": intent, "slots": {k: v for k, v in slots.items() if v is not None},
            "confidence": round(confidence, 2), "clarify": clarify, "ignore": ignore, "source": "rules", "text": text}


def _pick_work_order(t: str, ctx: dict, station_id: Optional[str]) -> tuple[Optional[dict], int]:
    """The open work order the technician means: at the named (or current) station, matching a part
    word if one was said, newest first. Returns (work_order, how many candidates there were)."""
    open_wos = [w for w in ctx["work_orders"] if w.get("status") not in ("closed", "awaiting_verification", "verified")]
    where = station_id or ctx["active_station"]
    candidates = [w for w in open_wos if not where or w.get("station_id") == where]
    words = [w for w in re.findall(r"[a-z]{4,}", t) if w not in ("done", "replaced", "fixed", "repaired", "finished",
                                                                 "complete", "completed", "station", "conveyor", "with")]
    by_part = [w for w in candidates if any(word[:5] in (w.get("title") or "").lower() for word in words)]
    candidates = by_part or candidates
    candidates.sort(key=lambda w: float(w.get("ts") or 0), reverse=True)
    return (candidates[0] if candidates else None), len(candidates)


# ---------------------------------------------------------------- parse

def parse_intent(text: str, context: Optional[dict] = None, source: str = "band") -> dict:
    """source "band" (default; also "sim", "mic"): the always-on mic. Commands need the wake word
    ("Vexora, I'm taking the hammer drill"); without it only a clear defect report (named part AND
    problem word) is heard, and everything else returns ignore=True (silent, no action).
    source "typed" / "user": words typed on purpose; no wake word needed.

    Extra keys for the caller: "text" (the words without the wake word: pass THESE to extract_defect),
    "raw" (as heard), "addressed" (wake word said), "extract_source" (pass as extract_defect's source)."""
    raw = (text or "").strip()
    band = (source or "band").lower() not in _LENIENT_SOURCES
    addressed, rest = split_wake_word(raw)
    if band and not addressed:
        ctx = _context(context)
        t = _clean(rest)
        if t and ctx["awaiting_confirmation"] and ctx["unresolved"]:     # the system just asked "yes or no?"
            answer = "reject_session" if _ANSWER_NO.search(t) else "confirm_session" if _ANSWER_YES.search(t) else None
            if answer:
                out = _result(answer, rest, 0.8, tool_session_id=ctx["unresolved"])
                out.update({"raw": raw, "addressed": False, "extract_source": "band"})
                return out
        rules = _strict_band_report(rest)
        if rules:
            out = _result("report_defect", rest, 0.8, component=rules.get("component"), condition=rules.get("condition"))
            out.update({"raw": raw, "addressed": False, "extract_source": "band"})
            return out
        out = _result("none", rest, 0.0, ignore=True)
        out.update({"raw": raw, "addressed": False, "extract_source": "band"})
        return out
    if addressed and not _clean(rest):
        out = _result("help", rest, 0.6)                      # just "Vexora?": tell them how to talk to it
    else:
        out = _parse(rest, context, strict=False)             # addressed on purpose (or typed): lenient
    out.update({"raw": raw, "addressed": addressed, "extract_source": "typed"})
    return out


def _parse(text: str, context: Optional[dict], strict: bool) -> dict:
    raw = (text or "").strip()
    t = _clean(raw)
    if not t:
        return _result("none", raw, 0.0, ignore=strict)
    ctx = _context(context)
    rules = inspection._rules_extract(raw)
    problem = bool(rules.get("has_problem"))
    equipment = bool(rules.get("component"))
    explicit = bool(inspection._EXPLICIT_REPORT.search(inspection._norm(raw)))     # "I want to report a defect"
    report = inspection.is_inspection_report(raw) if strict else (problem or explicit)

    # 1. confirm / reject a tool session ("not a tool" must win over "tool")
    if _REJECT.search(t):
        return _result("reject_session", raw, 0.9, None if ctx["unresolved"] else "There's no tool session waiting to be checked.",
                       tool_session_id=ctx["unresolved"])
    if _CONFIRM.search(t) and not problem:
        return _result("confirm_session", raw, 0.9, None if ctx["unresolved"] else "There's no tool session waiting to be checked.",
                       tool_session_id=ctx["unresolved"])

    # 2. help
    if (_HELP_STRICT.search(t) if strict else _HELP.search(t)) and not problem:
        return _result("help", raw, 0.9)

    # 3. isolation claim (logged as a claim; an authorized person verifies)
    claimed = _ISOLATION_STRONG.search(t) or (_ISOLATED.search(t) and (not strict or _EQUIPMENT_CONTEXT.search(t)))
    if claimed and not _ISO_NEGATED.search(t):
        where = inspection.resolve_station(raw, ctx["stations"], None)
        station_id = where["station"]["id"] if where["station"] else ctx["active_station"]
        wo, _count = _pick_work_order(t, ctx, station_id)
        return _result("isolation_claim", raw, 0.85, asset="CV-102", station_id=station_id,
                       work_order_id=wo.get("id") if wo else None)

    # 4. break
    if _BREAK.search(t) and not problem:
        found = _BREAK_MINUTES.search(t)
        minutes = None
        if found:
            minutes = int(found.group(1)) if found.group(1).isdigit() else _MINUTE_WORDS.get(found.group(1))
        return _result("take_break", raw, 0.85, minutes=minutes)

    named, generic = find_tools(raw, ctx["tools"])

    # 5. tool return
    if _RETURN.search(t) and (named or generic or _TOOL_WORD.search(t) or _RETURN_IT.search(t)):
        if len(named) == 1:
            return _result("tool_return", raw, 0.9, tool_id=named[0])
        if not named and ctx["active_tool"] and not generic:
            return _result("tool_return", raw, 0.75, tool_id=ctx["active_tool"])
        return _result("tool_return", raw, 0.5, "Which tool are you returning?")

    # 6. tool checkout
    strong, weak = bool(_CHECKOUT_STRONG.search(t)), bool(_CHECKOUT_WEAK.search(t))
    if named and (strong or weak or _only_tool_words(t)) and not problem:
        if len(named) > 1:
            return _result("tool_checkout", raw, 0.5, "One tool at a time: which one are you taking, the %s?"
                           % " or the ".join(spoken_tool(n) for n in named))
        return _result("tool_checkout", raw, 0.9 if (strong or weak) else 0.6, tool_id=named[0])
    if (strong or (weak and (generic or _TOOL_WORD.search(t)))) and not problem:
        if generic:
            return _result("tool_checkout", raw, 0.5, "Which one: the %s?" % " or the ".join(spoken_tool(g) for g in generic))
        if strong or _TOOL_WORD.search(t):
            return _result("tool_checkout", raw, 0.5, "Which tool are you taking? For example: hammer drill, impact wrench "
                                                     "or angle grinder.")

    # 7. job complete -> awaiting supervisor verification ("it's done" alone is chat on the band mic)
    job_done = _JOB_DONE_STRICT.search(t) if strict else _JOB_DONE.search(t)
    fixed = _PAST_FIX.search(t) and not _NOT_DONE.search(t) and (equipment or not strict)
    if job_done or fixed:
        where = inspection.resolve_station(raw, ctx["stations"], None)
        station_id = where["station"]["id"] if where["station"] else None
        wo, count = _pick_work_order(t, ctx, station_id)
        if wo is None:
            here = station_id or ctx["active_station"]
            return _result("job_complete", raw, 0.5, "I can't find an open work order%s. Which job is done?"
                           % (" at station " + str(here).lstrip("S") if here else ""), station_id=station_id)
        return _result("job_complete", raw, 0.85 if count == 1 else 0.7, work_order_id=wo.get("id"),
                       station_id=wo.get("station_id"))

    # 8. exposure question
    if (_EXPOSURE_STRICT if strict else _EXPOSURE).search(t) and not problem:
        return _result("exposure_query", raw, 0.85, tool_id=named[0] if len(named) == 1 else None)

    # 9. a defect report: extract_defect decides the station and the part (and asks if it can't).
    #    On the band mic only real inspection reports pass (equipment noun + problem word, or "report a defect").
    if report:
        return _result("report_defect", raw, 0.8, component=rules.get("component"), condition=rules.get("condition"))

    # 10. where the technician is
    where = inspection.resolve_station(raw, ctx["stations"], None)
    presence = bool(_PRESENCE.search(t))
    numbered = bool(inspection._explicit_numbers(inspection._norm(raw)))
    short = len(t.split()) <= 6
    if where["station"] is not None and (presence or (short and (numbered or not strict))):
        return _result("select_station", raw, 0.85 if presence else 0.6, station_id=where["station"]["id"])
    if presence and where["reason"] != "missing":
        return _result("select_station", raw, 0.5, where["question"])
    if presence and re.search(r"station|idler|pulley|take ?up|discharge|tail|head", t):
        return _result("select_station", raw, 0.5, "Which station are you at? %s." % _station_menu(ctx["stations"]))

    # 11. a bare yes / no answers a tool-session question, but on the band mic only right after it was asked
    #     (context["awaiting_confirmation"]): a stray "yes" in chat must never add exposure.
    if ctx["unresolved"] and (not strict or ctx["awaiting_confirmation"]):
        if _BARE_YES.match(t):
            return _result("confirm_session", raw, 0.6, tool_session_id=ctx["unresolved"])
        if _BARE_NO.match(t):
            return _result("reject_session", raw, 0.6, tool_session_id=ctx["unresolved"])

    # none: on the band mic this is chat (ignore=True: do nothing); typed text may still go to extract_defect
    return _result("none", raw, 0.0, ignore=strict)


# ---------------------------------------------------------------- respond

def _fmt(points: float) -> str:
    if points <= 0:
        return "0"
    if points < 0.1:
        return "%.2f" % points
    return "%.1f" % points if points < 10 else "%.0f" % points


def _station_words(state: dict, station_id: Optional[str]) -> str:
    st = next((s for s in inspection._stations(state.get("stations")) if s["id"] == station_id), None)
    return "station %d, %s" % (st["number"], st["name"].lower()) if st else ("station " + str(station_id).lstrip("S"))


def _tool_from(state: dict, tool_id) -> Optional[dict]:
    tools = state.get("tools") or exposure.load_tool_profiles()
    return exposure.get_tool(tool_id, tools) if tool_id else None


def _current_tool_id(state: dict) -> Optional[str]:
    """The tool in the technician's hands: checked out, else active, else the verdict's basis tool."""
    assignment = state.get("assignment") or {}
    tid = None
    if isinstance(assignment, dict):
        tool = assignment.get("tool")
        tid = assignment.get("tool_id") or (tool.get("id") if isinstance(tool, dict) else None)
    active = state.get("active_tool")
    active = active.get("id") if isinstance(active, dict) else active
    return tid or active or state.get("active_tool_id") or (state.get("verdict") or {}).get("basis_tool_id")


def exposure_sentence(state: dict, tool: Optional[dict] = None) -> str:
    ex = state.get("exposure") or state.get("verdict") or {}
    total = float(ex.get("total_points") or 0)
    pts = _fmt(total)
    if total >= exposure.LIMIT_POINTS:
        return "You're at %s points, over the 400-point limit value. Stop vibrating-tool work and tell your supervisor." % pts
    if total >= exposure.ACTION_POINTS:
        return ("You're at %s points, over the 100-point action value. Check with your supervisor before more "
                "vibrating-tool work." % pts)
    basis = tool or _tool_from(state, ex.get("basis_tool_id"))
    if basis and basis.get("vibration_m_s2"):
        minutes = exposure.minutes_for_points(float(basis["vibration_m_s2"]), exposure.ACTION_POINTS - total)
        return "You're at %s points, below the 100-point action value. About %d minutes left with the %s before 100." % (
            pts, int(minutes), spoken_tool(basis))
    return "You're at %s points, below the 100-point action value." % pts


def respond(intent, result: Optional[dict] = None, state: Optional[dict] = None) -> str:
    parsed = intent if isinstance(intent, dict) else {"intent": intent, "slots": {}}
    name, slots = parsed.get("intent") or parsed.get("name"), parsed.get("slots") or {}
    if name == "status_query":
        name = "exposure_query"
    result, state = result or {}, state or {}
    if parsed.get("clarify"):
        return parsed["clarify"]
    if parsed.get("ignore"):
        return ""
    if isinstance(result, dict) and result.get("error"):
        return "Sorry, that didn't work: %s." % str(result["error"]).rstrip(".")[:120]
    if isinstance(result, dict) and result.get("ok") is False:
        return str(result.get("message") or "Sorry, that didn't work.")

    if name == "select_station":
        return "%s. Ready when you are." % _station_words(state, slots.get("station_id")).capitalize()

    if name == "none" and parsed.get("addressed") and result.get("status") in (None, "ignored") \
            and result.get("reason") != "all clear":
        return "Sorry, I didn't catch that. Say Vexora, help, to hear what I can do."

    if name in ("report_defect", "none") and (name == "report_defect" or result.get("status")):
        status = result.get("status")
        if status in ("clarify",) or (status not in ("ok", None) and result.get("question")):
            return result.get("question") or "Which part, and at which station?"
        if status == "ignored":
            return "Noted, all clear." if result.get("reason") == "all clear" else ""   # chat: say nothing
        if status == "ok":
            defect = result.get("defect") or result
            wo = result.get("work_order") or {}
            part = " ".join(x for x in [(defect.get("condition") or ""), (defect.get("component") or "").lower()] if x)
            if (defect.get("condition") or "").lower().startswith("needs "):     # "idler needs replacement"
                part = "%s %s" % ((defect.get("component") or "the part").lower(), defect["condition"].lower())
            line = "Logged %s at %s. Work order %s drafted for review." % (
                part or "the defect", _station_words(state, defect.get("station_id")), wo.get("id") or "")
            return line.replace("order  drafted", "order drafted")
        return ""

    if name == "tool_checkout":
        tool = _tool_from(state, slots.get("tool_id"))
        if not tool:
            return "I couldn't find that tool."
        line = "%s assigned to you. I'll time it from your band." % spoken_tool(tool).capitalize()
        total = float((state.get("exposure") or state.get("verdict") or {}).get("total_points") or 0)
        if total < exposure.ACTION_POINTS and tool.get("vibration_m_s2"):
            minutes = exposure.minutes_for_points(float(tool["vibration_m_s2"]), exposure.ACTION_POINTS - total)
            line += " About %d minutes on it before the action value." % int(minutes)
        elif total >= exposure.ACTION_POINTS:
            line += " You're already over the action value: check with your supervisor."
        return line

    if name == "tool_return":
        tool = _tool_from(state, slots.get("tool_id"))
        line = "%s returned." % spoken_tool(tool or slots.get("tool_id")).capitalize()
        pts = result.get("points_with_this_tool")
        if pts is not None:
            line += " %s points with it today." % _fmt(float(pts))
        return line

    if name == "exposure_query":   # named tool, else the one in hand, else the last one used (inside exposure_sentence)
        return exposure_sentence(state, _tool_from(state, slots.get("tool_id") or _current_tool_id(state)))

    if name == "confirm_session":
        pts = result.get("points")
        tool = _tool_from(state, result.get("tool_id"))
        if pts is not None and tool:
            return "Confirmed: %s, %s points added." % (spoken_tool(tool), _fmt(float(pts)))
        return "Confirmed. Exposure updated."

    if name == "reject_session":
        return "Okay, not a tool. Nothing added to your exposure."

    if name == "job_complete":
        return ("%s marked awaiting supervisor verification. Vexora never closes work on its own."
                % (result.get("work_order_id") or slots.get("work_order_id") or "Work order")).strip()

    if name == "isolation_claim":
        return "Isolation noted. An authorized person must verify it before work starts."

    if name == "take_break":
        return "Break logged. Exposure only counts while a tool is running."

    if name == "help":
        return ("Say Vexora first, then a command. For example: Vexora, I'm at station three. "
                "Vexora, taking the hammer drill. Vexora, how long can I keep going. Vexora, done with the drill. "
                "Vexora, job done. To report a defect, just say it, like: return idler seized.")

    if name in (None, "none"):
        return ""
    return str(result.get("message") or "")


if __name__ == "__main__":  # python backend/intents.py "taking the hammer drill"
    import json
    import sys

    said = " ".join(sys.argv[1:]) or "taking the hammer drill"
    parsed = parse_intent(said, {})
    print(json.dumps(parsed, indent=1))
    print("say:", respond(parsed, {}, {"exposure": {"total_points": 2.5}}))
