"""BeltGuard inspection intelligence. Owner: S4 hav-brain.

extract_defect(transcript, stations, conveyor, worker) turns one spoken (or typed) defect
report into a structured defect plus a work-order draft.

Two layers:
  1. Extraction: OpenAI structured output through the kit's own helper
     (vexo-open-kit/examples/python/ai.py -> ai.extract, strict JSON schema). Without a key,
     or when the call fails, a rules/keyword extractor does the same job (source "rules").
  2. Deterministic validation: code decides the STATION and the CONVEYOR from the words
     actually said, never the model. A missing, unknown, conflicting or ambiguous station
     returns {"status": "clarify", "question": ...}. The model cannot invent a station.
     A reported problem with no part named ("something is broken") returns clarify with
     reason "component_missing"; the model's part name only counts if the technician said it.

Safety rules: every work order requires isolation, which must be verified by an
authorized person. BeltGuard never authorizes isolation and never closes or verifies work.

Results:
  {"status": "ok", "station_id": "S3", ..., "work_order": {...}, "source": "ai" | "rules"}
  {"status": "clarify", "question": "...", "reason": "...", "transcript": ..., "partial": {...}}
      -> keep the transcript; when the technician answers, call
         extract_defect(previous_transcript + " " + answer, ...) again.
  {"status": "ignored", "reason": "..."}   the words were not a defect report (chit-chat).

Optional keyword arguments: active_station_id (the station selected on the dashboard, used
only when the words name no station), force_rules (skip OpenAI) and source. With source "band"
(also "sim", "mic", "voice") a strict gate applies: the sentence needs an equipment noun AND a
problem word, or an explicit "report a defect" / "fault" / "issue at station ...". Otherwise it is
{"status": "ignored", "reason": "chat"} with no question. Typed input (any other source) stays lenient.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
import time
from typing import Optional

try:  # works as backend.inspection and as a top-level module with backend/ on sys.path
    from . import exposure  # type: ignore
except ImportError:
    import exposure  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_PATH = os.path.join(ROOT, ".env")
KIT_AI_PATH = os.path.join(ROOT, "vexo-open-kit", "examples", "python", "ai.py")
AI_TIMEOUT_S = float(os.environ.get("BELTGUARD_AI_TIMEOUT", "20"))

ISOLATION_NOTE = ("Isolation must be verified by an authorized person before work starts. "
                  "BeltGuard does not authorize isolation.")
CLOSE_OUT_NOTE = "Close-out requires supervisor verification. BeltGuard drafts work orders; it never closes them."

DEFAULT_STATIONS = [
    {"id": "S1", "number": 1, "name": "Tail pulley & loading zone"},
    {"id": "S2", "number": 2, "name": "Carry idlers & belt tracking"},
    {"id": "S3", "number": 3, "name": "Return idlers"},
    {"id": "S4", "number": 4, "name": "Take-up & belt tension"},
    {"id": "S5", "number": 5, "name": "Head/drive pulley & discharge"},
]


# ---------------------------------------------------------------- keys and the kit's AI helper

def load_env(path: str = ENV_PATH) -> None:
    """Make beltguard/.env visible to the kit helper, which reads the environment (or a .env
    in the folder the program started in). Variables already set win."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.replace("export ", "", 1).partition("=")
                name, value = name.strip(), value.strip().strip("'\"")
                if name and value and not os.environ.get(name):
                    os.environ[name] = value
    except OSError:
        pass


load_env()

_ai_lock = threading.Lock()
_ai_module = None
_ai_problem: Optional[str] = None


def kit_ai():
    """The kit's OpenAI helper (examples/python/ai.py), loaded once; None if it cannot load."""
    global _ai_module, _ai_problem
    with _ai_lock:
        if _ai_module is not None or _ai_problem is not None:
            return _ai_module
        try:
            existing = sys.modules.get("ai")
            if existing is not None and os.path.abspath(getattr(existing, "__file__", "") or "") == os.path.abspath(KIT_AI_PATH):
                module = existing
            else:
                folder = os.path.dirname(KIT_AI_PATH)
                if folder not in sys.path:
                    sys.path.append(folder)  # ai.py does "import kit" from its own folder
                spec = importlib.util.spec_from_file_location("ai", KIT_AI_PATH)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules.setdefault("ai", module)
            module.TIMEOUT = AI_TIMEOUT_S  # a live demo cannot wait the kit's default 60 s
            _ai_module = module
        except Exception as error:  # kit missing or broken: the rules path still works
            _ai_problem = "%s: %s" % (type(error).__name__, error)
        return _ai_module


def ai_status() -> dict:
    """{"available": bool, "model": str | None, "problem": str | None}"""
    if os.environ.get("BELTGUARD_AI", "1").strip().lower() in ("0", "false", "off", "no"):
        return {"available": False, "model": None, "problem": "AI switched off (BELTGUARD_AI=0)"}
    module = kit_ai()
    if module is None:
        return {"available": False, "model": None, "problem": _ai_problem}
    try:
        module.key()
    except Exception as error:
        return {"available": False, "model": module.MODEL, "problem": _short(error)}
    return {"available": True, "model": module.MODEL, "problem": None}


def _short(error: Exception) -> str:
    text = str(error).strip() or type(error).__name__
    return text[:240]


# ---------------------------------------------------------------- text helpers

_NUMBER_WORDS = {"one": 1, "won": 1, "two": 2, "three": 3, "tree": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
_NUM_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_ORD_ALT = "|".join(_ORDINAL_WORDS)

_EXPLICIT_STATION = [
    re.compile(r"\bstation\s*(?:number\s*|no\s*|#\s*)?(\d{1,2})\b"),
    re.compile(r"\bstation\s*(?:number\s*)?(%s)\b" % _NUM_ALT),
    re.compile(r"\b(%s)\s+station\b" % _ORD_ALT),
    re.compile(r"(?<![a-z0-9'])s-?(\d{1,2})\b"),
    re.compile(r"\bstn\s*(\d{1,2})\b"),
]

_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_ALT = "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True))


# The voice wake word ("BeltGuard" and Deepgram's spellings) at the start of a sentence. Never after "the":
# "the belt guard is missing" names a conveyor part. Stripped before anything reads the words.
_WAKE_RAW = re.compile(
    r"^\s*(?:(?:um+|uh+|er+|erm|so|okay|ok|hey|hi|hello|yo|right|alright)[\s,.!]+)*"
    r"(?:(?:belt|bell|bel|built)[\s\-]?(?:guard|gard)s?|vex[\s\-]?or+a?h?|vexer+a|vexo[\s\-]?ra)\b[\s,.:;!?\-]*",
    re.IGNORECASE)   # "Vexora" is the product name since 17:35; "BeltGuard" still works


def strip_wake_word(text: str) -> tuple[bool, str]:
    """(said the wake word?, the text without it). "BeltGuard, taking the drill" -> (True, "taking the drill")."""
    raw = text or ""
    found = _WAKE_RAW.match(raw)
    return (True, raw[found.end():].strip()) if found else (False, raw.strip())


def _norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("–", "-").replace("—", " ")
    t = re.sub(r"[^a-z0-9#'\- ]+", " ", t)
    # Deepgram mishears "idler" on the band mic ("Adler change needed at station 3", 17:4x): read them as idler
    t = re.sub(r"(?<![a-z])(?:adler|aidler|eidler|idlar|idol|idoler)(s?)(?![a-z])", r"idler\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _to_int(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token) or _ORDINAL_WORDS.get(token)


def _keyword_regex(phrase: str) -> re.Pattern:
    body = re.escape(phrase).replace(r"\ ", r"\s+").replace(r"\-", r"[\s-]?")
    return re.compile(r"(?<![a-z])" + body + r"s?(?![a-z])")


# ---------------------------------------------------------------- stations (deterministic)

_LOCATION_KEYWORDS = {
    "tail": ["tail pulley", "tail end", "tail", "loading zone", "load zone", "loading point", "loading chute",
             "feed chute", "skirt", "skirting", "skirt rubber", "impact bed", "impact idler", "impact roller"],
    "carry": ["carry idler", "carrying idler", "troughing idler", "trough idler", "top idler", "carry roller",
              "top roller", "carry side", "carrying side", "belt tracking", "training idler", "tracking idler"],
    "return": ["return idler", "return roller", "bottom idler", "bottom roller", "return side", "return strand",
               "return belt"],
    "takeup": ["take-up", "take up", "takeup", "counterweight", "counter weight", "belt tension", "tensioner",
               "gravity take"],
    "head": ["head pulley", "drive pulley", "head end", "discharge", "head chute", "gearbox", "gear box",
             "drive motor", "drive unit", "snub pulley", "drive"],
}
_LOCATION_REGEX = {group: [(kw, _keyword_regex(kw)) for kw in kws] for group, kws in _LOCATION_KEYWORDS.items()}


def _location_group(name: str) -> Optional[str]:
    n = (name or "").lower()
    for group, probe in (("tail", "tail"), ("carry", "carry"), ("return", "return"),
                         ("takeup", "take"), ("head", "head"), ("head", "drive")):
        if probe in n:
            return group
    return None


def _stations(stations) -> list[dict]:
    out = []
    for i, s in enumerate(stations or DEFAULT_STATIONS):
        sid = str(s.get("id") or s.get("station_id") or "S%d" % (i + 1))
        number = s.get("number")
        if number is None:
            found = re.search(r"(\d+)", sid)
            number = int(found.group(1)) if found else i + 1
        name = str(s.get("name") or sid)
        out.append({"id": sid, "number": int(number), "name": name, "status": str(s.get("status") or ""),
                    "group": _location_group(name)})
    return out


def _explicit_numbers(t: str) -> list[int]:
    found = []
    for pattern in _EXPLICIT_STATION:
        for match in pattern.finditer(t):
            value = _to_int(match.group(1))
            if value is not None and value not in found:
                found.append(value)
    return found


def _keyword_hits(t: str, stations: list[dict]) -> dict:
    """{station_id: [keywords said]} for component/location words that pin a station."""
    hits: dict = {}
    for station in stations:
        for keyword, regex in _LOCATION_REGEX.get(station["group"], []):
            if regex.search(t):
                hits.setdefault(station["id"], []).append(keyword)
    return hits


def _station_menu(stations: list[dict]) -> str:
    return ", ".join("%d %s" % (s["number"], s["name"]) for s in stations)


def resolve_station(transcript: str, stations=None, active_station_id: Optional[str] = None) -> dict:
    """Decide the station from the words said (and the dashboard selection as a last resort).
    -> {"station": {...} | None, "reason": "ok|missing|unknown|ambiguous|conflict", "evidence": str,
        "question": str | None}"""
    st = _stations(stations)
    by_number = {s["number"]: s for s in st}
    by_id = {s["id"]: s for s in st}
    t = _norm(transcript)
    numbers = _explicit_numbers(t)
    hits = _keyword_hits(t, st)
    active = by_id.get(active_station_id) if active_station_id else None
    menu = _station_menu(st)

    def ok(station, evidence):
        return {"station": station, "reason": "ok", "evidence": evidence, "question": None}

    def ask(reason, question):
        return {"station": None, "reason": reason, "evidence": "", "question": question}

    if len(numbers) > 1:
        said = " and ".join(str(n) for n in numbers)
        return ask("ambiguous", "I heard station %s. Which single station is the defect at? (%s)" % (said, menu))
    if len(numbers) == 1:
        station = by_number.get(numbers[0])
        if station is None:
            return ask("unknown", "CV-102 has stations 1 to %d, but I heard station %d. Which station is it? (%s)"
                       % (len(st), numbers[0], menu))
        others = [sid for sid in hits if sid != station["id"]]
        if others and station["id"] not in hits:
            other = by_id[others[0]]
            return ask("conflict", "You said station %d (%s), but '%s' is inspected at station %d (%s). Which station is it?"
                       % (station["number"], station["name"], hits[others[0]][0], other["number"], other["name"]))
        return ok(station, "said station %d" % station["number"])
    if len(hits) == 1:
        sid, words = next(iter(hits.items()))
        station = by_id[sid]
        if active and active["id"] != sid:
            return ask("conflict", "You are at station %d (%s), but '%s' is inspected at station %d (%s). Which station is it?"
                       % (active["number"], active["name"], words[0], station["number"], station["name"]))
        return ok(station, "'%s' is inspected at station %d" % (words[0], station["number"]))
    if len(hits) > 1:
        if active and active["id"] in hits:
            return ok(active, "selected station %d on the dashboard (user input)" % active["number"])
        named = ["%d %s ('%s')" % (by_id[sid]["number"], by_id[sid]["name"], words[0]) for sid, words in hits.items()]
        return ask("ambiguous", "That could be station %s. Which station is the defect at?" % " or ".join(named))
    if active:
        return ok(active, "selected station %d on the dashboard (user input)" % active["number"])
    return ask("missing", "Which station on CV-102 is that at? Say the station number: %s." % menu)


def _grounded_station(evidence: str, ai_number, transcript: str, stations: list[dict]) -> Optional[dict]:
    """Accept the model's station only if its cited words appear verbatim in the transcript
    and contain exactly one station number that matches the number the model gave."""
    ev, tr = _norm(evidence), _norm(transcript)
    if not ev or ev not in tr:
        return None
    values = {v for v in (_to_int(token) for token in ev.split()) if v is not None}
    by_number = {s["number"]: s for s in stations}
    if len(values) == 1:
        value = values.pop()
        if value in by_number and (ai_number is None or int(ai_number) == value):
            return by_number[value]
    return None


# ---------------------------------------------------------------- conveyor (deterministic)

def _conveyor_mentions(transcript: str) -> list[str]:
    t = _norm(transcript).replace("-", " ")

    def spelled(match):
        return match.group(1) + " " + "".join(_DIGIT_WORDS[w] for w in match.group(2).split())

    def hundred(match):
        return "%s %d" % (match.group(1), 100 * int(_DIGIT_WORDS[match.group(2)]) + int(_DIGIT_WORDS[match.group(3)]))

    t = re.sub(r"\b(c\s?v|conveyor)\s+(%s)\s+hundred\s+(?:and\s+)?(%s)\b" % (_DIGIT_ALT, _DIGIT_ALT), hundred, t)
    t = re.sub(r"\b(c\s?v|conveyor)\s+((?:(?:%s)\s+){1,3}(?:%s))\b" % (_DIGIT_ALT, _DIGIT_ALT), spelled, t)
    found = [m.group(1) for m in re.finditer(r"\bc\s?v\s?(\d{2,4})\b", t)]
    found += [m.group(1) for m in re.finditer(r"\bconveyor\s+(?:number\s+)?(\d{2,4})\b", t)]
    return list(dict.fromkeys(found))


def resolve_conveyor(transcript: str, conveyor: dict) -> dict:
    """-> {"conveyor_id", "evidence", "question" | None}. No conveyor said = the active inspection's."""
    conveyor_id = (conveyor or {}).get("id") or "CV-102"
    digits = re.sub(r"\D", "", conveyor_id)
    mentions = _conveyor_mentions(transcript)
    wrong = [m for m in mentions if m != digits]
    if wrong:
        return {"conveyor_id": None, "evidence": "",
                "question": "I heard conveyor CV-%s, but this inspection is on %s. Which conveyor is the defect on?"
                            % (wrong[0], conveyor_id)}
    if mentions:
        return {"conveyor_id": conveyor_id, "evidence": "said %s" % conveyor_id, "question": None}
    return {"conveyor_id": conveyor_id, "evidence": "active inspection %s (not said)" % conveyor_id, "question": None}


# ---------------------------------------------------------------- rules extractor

_COMPONENTS = [
    ("return idler", "Return idler"), ("return roller", "Return idler"), ("bottom idler", "Return idler"),
    ("bottom roller", "Return idler"),
    ("carry idler", "Carry idler"), ("carrying idler", "Carry idler"), ("troughing idler", "Carry idler"),
    ("trough idler", "Carry idler"), ("top idler", "Carry idler"), ("top roller", "Carry idler"),
    ("impact idler", "Impact idler"), ("impact roller", "Impact idler"), ("impact bed", "Impact bed"),
    ("training idler", "Training idler"), ("tracking idler", "Training idler"),
    ("idler bearing", "Idler bearing"), ("idler frame", "Idler frame"), ("idler bracket", "Idler bracket"),
    ("tail pulley", "Tail pulley"), ("head pulley", "Head pulley"), ("drive pulley", "Drive pulley"),
    ("snub pulley", "Snub pulley"), ("bend pulley", "Bend pulley"),
    ("pulley lagging", "Pulley lagging"), ("lagging", "Pulley lagging"),
    ("belt splice", "Belt splice"), ("splice", "Belt splice"), ("belt edge", "Belt edge"),
    ("belt cover", "Belt cover"), ("belt cleaner", "Belt cleaner"), ("scraper", "Belt cleaner"),
    ("skirt rubber", "Skirting"), ("skirting", "Skirting"), ("skirt", "Skirting"),
    ("chute liner", "Chute liner"), ("loading chute", "Loading chute"), ("head chute", "Head chute"),
    ("chute", "Chute"),
    ("take-up", "Take-up"), ("take up", "Take-up"), ("takeup", "Take-up"),
    ("counterweight", "Take-up counterweight"), ("counter weight", "Take-up counterweight"),
    ("gearbox", "Gearbox"), ("gear box", "Gearbox"), ("drive motor", "Drive motor"), ("motor", "Drive motor"),
    ("coupling", "Coupling"), ("pull wire", "Pull-wire switch"), ("pull cord", "Pull-wire switch"),
    ("lanyard", "Pull-wire switch"), ("guard", "Guard"), ("bearing", "Bearing"),
    ("roller", "Idler"), ("idler", "Idler"), ("pulley", "Pulley"), ("belt", "Belt"),
]
_COMPONENT_REGEX = [(_keyword_regex(phrase), name) for phrase, name in _COMPONENTS]

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_CONDITIONS = [  # (regex, canonical condition, severity floor, reason)
    (r"smok\w*|burn\w*|fire|flames?", "smoking / burning", "critical", "Smoke or burning: fire risk on the conveyor"),
    (r"torn|ripped|rip|tear", "torn", "critical", "Belt damage can run along the belt in seconds"),
    (r"seiz\w*|jammed|stuck|not\s+(?:turning|rotating|spinning)|frozen|locked\s+up", "seized", "high",
     "A seized idler wears through the belt and heats up from friction (fire risk)"),
    (r"overheat\w*|(?:running|too|very|is|was|feels|getting)\s+hot|hot\s+(?:bearing|idler|roller|motor|pulley|gearbox|spot)",
     "overheating", "high", "Overheating part: bearing failure and fire risk"),
    (r"collaps\w*", "collapsed", "high", "A collapsed part can rip the belt"),
    (r"crack\w*|broken|snapped|fractur\w*", "cracked / broken", "high", "A broken part can fail completely in service"),
    (r"missing", "missing", "high", "Missing part or guard: exposure to moving parts"),
    (r"worn(?:\s+out)?|wear\w*|thin", "worn", "medium", "Wear progresses; replace before failure"),
    (r"nois\w*|squeal\w*|grinding|grinds?(?!er)|screech\w*|knock\w*|rattl(?:ing|es|ed|e(?!\s+gun))",  # not "grinder", "rattle gun"
     "noisy", "medium", "Noise often comes before bearing failure"),
    (r"leak\w*", "leaking", "medium", "Lubricant loss damages bearings and gearboxes"),
    (r"loose|slack", "loose", "medium", "Loose parts can fall or knock the belt off line"),
    (r"misalign\w*|mistrack\w*|tracking\s+off|off\s+track|wander\w*", "mistracking", "medium",
     "Mistracking damages belt edges and spills material"),
    (r"damag\w*|bent|dented", "damaged", "medium", "Damage noted; inspect and repair"),
    (r"vibrat\w*", "excessive vibration", "medium", "Excess vibration points to a mechanical fault"),
    (r"fault\w*|fail\w*|(?:not|isn'?t|stopped)\s+working", "fault", "medium", "Reported fault: find the cause before restarting"),
    (r"rubbing|rubs|slipping|slips|wobbl\w*|chewing|scraping|dragging", "abnormal running", "medium",
     "Rubbing, slipping or wobbling wears the belt"),
    (r"build\s*-?\s*up|carry\s*back|spillage|caked|blocked", "material build-up", "low",
     "Build-up causes mistracking and faster wear"),
]
_CONDITION_REGEX = [(re.compile(r"(?<![a-z])(?:%s)(?![a-z])" % rx), c, s, r) for rx, c, s, r in _CONDITIONS]
_REPLACE_WORDS = re.compile(r"(?<![a-z])(?:replac\w*|change\s*-?\s*out|swap\w*|renew\w*|new\s+one|"
                            # "idler change needed", "needs changing", "has to be changed" (not "change station")
                            r"chang(?:e|ing)\s+(?:is\s+|was\s+)?(?:needed|required|due)|needs?\s+(?:to\s+be\s+)?chang(?:ed|ing)|"
                            r"(?:has|have|had|got)\s+to\s+be\s+changed|(?:should|must)\s+be\s+changed)(?![a-z])")
_ACTION_BY_CONDITION = {"seized": "replace", "cracked / broken": "replace", "worn": "replace", "collapsed": "replace",
                        "torn": "repair", "missing": "replace", "mistracking": "adjust", "material build-up": "clean",
                        "noisy": "inspect_further", "overheating": "inspect_further", "smoking / burning": "inspect_further",
                        "excessive vibration": "inspect_further", "leaking": "repair", "loose": "repair",
                        "damaged": "repair"}


_REPAIR_WORDS = re.compile(r"(?<![a-z])(?:repair\w*|fix\w*|needs?\s+(?:attention|looking\s+at))(?![a-z])")
# Negated or all-clear phrases: "no damage", "nothing wrong", "looks fine", "all clear", "everything else is fine".
_ALL_CLEAR = re.compile(
    r"(?<![a-z])(?:(?:no|zero|without|nothing)\s+(?:visible\s+|obvious\s+)?(?:damage|faults?|defects?|problems?|issues?|"
    r"wear|leaks?|wrong|broken|noise)|all\s+(?:clear|good|ok|okay|fine)|looks?\s+(?:good|fine|ok|okay|normal)|"
    r"(?:is|are|all)\s+(?:fine|ok|okay|good))(?![a-z])")


def _rules_extract(transcript: str) -> dict:
    t = _norm(transcript)
    all_clear = bool(_ALL_CLEAR.search(t))
    tc = _ALL_CLEAR.sub(" ", t)  # negated problem words ("no damage") are not problems
    component = next((name for regex, name in _COMPONENT_REGEX if regex.search(t)), None)
    found = [(c, s, r) for regex, c, s, r in _CONDITION_REGEX if regex.search(tc)]
    replace = bool(_REPLACE_WORDS.search(tc))
    condition, severity, reason = (None, "medium", "Reported defect")
    if found:
        condition, severity, reason = max(found, key=lambda item: _SEVERITY_RANK[item[1]])
    elif replace or _REPAIR_WORDS.search(tc):  # "return idler at station 3 needs replacing" is a report too
        condition = "needs replacement" if replace else "needs repair"
        reason = "Reported for %s by the technician" % ("replacement" if replace else "repair")
    action = "replace" if replace else _ACTION_BY_CONDITION.get(condition or "", "repair")
    mentions_place = bool(_explicit_numbers(t)) or bool(_conveyor_mentions(transcript)) or any(
        regex.search(t) for group in _LOCATION_REGEX.values() for _, regex in group)
    # has_problem: the words report a problem. The part may still be missing: extract_defect then asks for it.
    return {"component": component, "condition": condition, "severity": severity, "severity_reason": reason,
            "action": action, "has_problem": bool(condition), "all_clear": all_clear, "mentions_place": mentions_place}


_GATE_EXTRA_NOUN = re.compile(r"(?<![a-z])conveyor(?![a-z])")
_EXPLICIT_REPORT = re.compile(
    r"(?<![a-z])(?:report(?:ing)?\s+(?:a\s+|an\s+|the\s+)?(?:defect|fault|problem|issue)|(?:want|need|would\s+like|'d\s+like)\s+to\s+report|"
    r"log\s+(?:a\s+|an\s+)?(?:defect|fault)|new\s+defect|defects?|faults?|faulty|(?:issue|problem)\s+(?:at|on|with)\s+"
    r"(?:station|the|conveyor|cv))(?![a-z])")
_BAND_SOURCES = {"band", "sim", "mic", "voice"}


def is_inspection_report(transcript: str, allow_trigger: bool = True) -> bool:
    """Strict gate for the always-on band mic: an equipment noun AND a problem word, or (allow_trigger)
    an explicit report trigger ("report a defect", "fault", "issue at station ..."). Chat fails the gate."""
    t = _norm(transcript)
    tc = _ALL_CLEAR.sub(" ", t)
    noun = any(regex.search(t) for regex, _name in _COMPONENT_REGEX) or bool(_GATE_EXTRA_NOUN.search(t))
    problem = any(regex.search(tc) for regex, *_rest in _CONDITION_REGEX) or bool(_REPLACE_WORDS.search(tc))
    return (noun and problem) or (allow_trigger and bool(_EXPLICIT_REPORT.search(t)))


_VAGUE_PART_WORDS = {"component", "components", "something", "anything", "thing", "things", "unknown", "part", "parts",
                     "equipment", "item", "this", "that", "none", "unspecified", "unclear", "stuff", "asset",
                     "machine", "conveyor", "the", "and"}


def _component_grounded(component: Optional[str], transcript: str) -> bool:
    """True when the model's part name is made of words the technician actually said."""
    words = [w for w in re.findall(r"[a-z]+", (component or "").lower()) if len(w) >= 3 and w not in _VAGUE_PART_WORDS]
    if not words:
        return False
    t = _norm(transcript)
    return any(re.search(r"(?<![a-z])" + re.escape(w[:5]), t) for w in words)


# ---------------------------------------------------------------- OpenAI extractor (kit helper)

_AI_INSTRUCTIONS = """You turn a mining maintenance technician's spoken defect report about a belt conveyor into fields.
The words came from speech recognition and may contain recognition errors.
Conveyor in this inspection: {conveyor}. Its inspection stations: {stations}.
Rules:
- station_evidence: copy, word for word, the part of the report that names or numbers the station. null if the report does not say where. Never guess or infer a station.
- station_number: the station number those words give, or null.
- conveyor_mentioned: the conveyor id exactly as said, or null.
- component: the specific part, e.g. "return idler". condition: the failure in 1-4 words, e.g. "seized".
- severity: low, medium, high or critical for belt and fire risk. severity_reason: one short sentence.
- action: what the work order should do.
- task_steps: 2-5 short repair steps for the part only. Do NOT include stopping the conveyor, isolation, lock-out, permits, verification or close-out: code adds those, and only an authorized person can verify isolation.
- suggested_tool_id: the hand-held power tool most likely needed, or "none". Tools: {tools}.
- est_trigger_min: realistic minutes of trigger time on that tool for this job, or null.
- summary: one short sentence.
- is_defect_report: false when the words do not report an equipment problem."""


def _ai_schema(tool_ids: list[str]) -> dict:
    properties = {
        "is_defect_report": {"type": "boolean"},
        "conveyor_mentioned": {"type": ["string", "null"]},
        "station_evidence": {"type": ["string", "null"]},
        "station_number": {"type": ["integer", "null"]},
        "component": {"type": "string"},
        "condition": {"type": "string"},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "severity_reason": {"type": "string"},
        "action": {"type": "string", "enum": ["replace", "repair", "adjust", "clean", "inspect_further"]},
        "task_steps": {"type": "array", "items": {"type": "string"}},
        "suggested_tool_id": {"type": "string", "enum": list(tool_ids) + ["none"]},
        "est_trigger_min": {"type": ["number", "null"]},
        "summary": {"type": "string"},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _ai_extract(transcript: str, stations: list[dict], conveyor: dict, tools: list[dict]) -> dict:
    module = kit_ai()
    usable = [t for t in tools if not t.get("demo_stand_in")]
    instructions = _AI_INSTRUCTIONS.format(
        conveyor="%s (%s)" % (conveyor.get("id", "CV-102"), conveyor.get("name", "belt conveyor")),
        stations=_station_menu(stations),
        tools="; ".join("%s = %s" % (t["id"], t["name"]) for t in usable))
    return module.extract(transcript, _ai_schema([t["id"] for t in usable]), instructions)


# ---------------------------------------------------------------- work order

_PRIORITY = {
    "critical": ("P1", "P1 Emergency", "make safe now and report to the control room"),
    "high": ("P2", "P2 Urgent", "complete this shift"),
    "medium": ("P3", "P3 Planned", "schedule within 7 days"),
    "low": ("P4", "P4 Routine", "next planned shutdown"),
}
_VERB = {"replace": "Replace", "repair": "Repair", "adjust": "Adjust", "clean": "Clean", "inspect_further": "Inspect"}
_UNSAFE_STEP = re.compile(r"isolat|lock\s*-?\s*out|lockout|tag\s*-?\s*out|permit|authori[sz]|close[\s-]*out|"
                          r"close the work|sign[\s-]*off|zero energy|verif", re.I)

_tools_cache: Optional[list] = None
_tools_mtime: Optional[float] = None
_tools_lock = threading.Lock()


def _tool_profiles() -> list[dict]:
    """tool_profiles.json, re-read whenever the file changes."""
    global _tools_cache, _tools_mtime
    with _tools_lock:
        try:
            mtime = os.path.getmtime(exposure.PROFILES_PATH)
        except OSError:
            mtime = None
        if _tools_cache is None or mtime != _tools_mtime:
            try:
                _tools_cache = exposure.load_tool_profiles()
            except (OSError, ValueError):
                _tools_cache = _tools_cache or []
            _tools_mtime = mtime
        return _tools_cache


def _rules_tool(component: str, condition: str, action: str) -> tuple[Optional[str], Optional[float]]:
    c, cond = (component or "").lower(), (condition or "").lower()
    if "build" in cond or action == "clean":
        return "chipping_hammer", 20
    if "idler" in c or "roller" in c:
        return "impact_wrench", 4
    if "lagging" in c or "pulley" in c or "bearing" in c:
        return "impact_wrench_1in", 10
    if any(word in c for word in ("gearbox", "motor", "coupling", "drive")):
        return "impact_wrench_1in", 12
    if any(word in c for word in ("skirt", "liner", "chute")):
        return "angle_grinder", 15
    if "guard" in c:
        return "hammer_drill", 10
    return None, None


def _suggest_tool(component, condition, action, ai_fields, tools) -> tuple[Optional[dict], Optional[float], Optional[str]]:
    by_id = {t["id"]: t for t in tools if not t.get("demo_stand_in")}
    if ai_fields and ai_fields.get("suggested_tool_id") in by_id:
        tool = by_id[ai_fields["suggested_tool_id"]]
        est = ai_fields.get("est_trigger_min")
        if not isinstance(est, (int, float)) or est <= 0:
            est = tool.get("typical_trigger_min")
        return tool, float(min(max(est or 1, 0.5), 240)), "ai"
    tool_id, est = _rules_tool(component, condition, action)
    if tool_id in by_id:
        return by_id[tool_id], float(est or by_id[tool_id].get("typical_trigger_min") or 5), "rules"
    return None, None, None


def _steps(asset: str, component: str, condition: str, action: str, tool: Optional[dict], ai_steps) -> list[str]:
    part = component.lower()
    before = [
        "Stop %s with the normal stop control and tell the control room." % asset,
        "Isolate, lock out, tag and test for zero energy. " + ISOLATION_NOTE,
        "Make stored energy safe (take-up tension, material on the belt) before working at the %s." % part,
    ]
    core = [s.strip() for s in (ai_steps or []) if isinstance(s, str) and s.strip() and not _UNSAFE_STEP.search(s)]
    core = [s[:160] for s in core[:5]]
    if not core:
        with_tool = " (%s)" % tool["name"] if tool else ""
        if action == "replace" and ("idler" in part or "roller" in part):
            core = ["Release the %s from its drop-out brackets%s." % (part, with_tool),
                    "Fit the replacement idler; check it spins freely and sits square to the belt.",
                    "Check neighbouring idlers and the belt cover for heat damage or scoring."]
        elif action == "replace":
            core = ["Remove the defective %s%s." % (part, with_tool), "Fit the replacement and check alignment and fasteners."]
        elif action == "adjust":
            core = ["Adjust the %s until the belt runs centrally; check tracking at head and tail." % part]
        elif action == "clean":
            core = ["Remove material build-up from the %s%s." % (part, with_tool),
                    "Find and fix the source of the build-up (belt cleaner, skirting)."]
        elif action == "inspect_further":
            core = ["Inspect the %s closely (%s); record findings and raise a follow-up work order if needed." % (part, condition)]
        else:
            core = ["Repair the %s (%s)%s." % (part, condition, with_tool)]
    after = [
        "Locks come off only after the authorized person confirms the area is clear; test run and check belt tracking.",
        "Record the actual trigger time on each tool for the exposure record.",
        CLOSE_OUT_NOTE,
    ]
    return before + core + after


# ---------------------------------------------------------------- the entry point

def extract_defect(transcript: str, stations: list[dict], conveyor: dict, worker: dict,
                   *, active_station_id: Optional[str] = None, force_rules: bool = False,
                   source: Optional[str] = None) -> dict:
    started = time.time()
    _woke, text = strip_wake_word(transcript or "")     # "BeltGuard, the belt is torn": "guard" is not the part
    if text and (source or "").lower() in _BAND_SOURCES and not is_inspection_report(text):
        # always-on band mic: chat is ignored silently (no question back, no AI call)
        return {"status": "ignored", "reason": "chat", "question": None, "transcript": text, "source": "rules",
                "model": None, "ai_error": None, "latency_ms": int((time.time() - started) * 1000)}
    conveyor = conveyor or {"id": "CV-102", "name": "Overland conveyor CV-102"}
    asset = conveyor.get("id") or "CV-102"
    st = _stations(stations)
    if active_station_id is None:
        active_station_id = next((s["id"] for s in st if s["status"] == "active"), None)
    tools = _tool_profiles()

    rules = _rules_extract(text) if text else {"has_problem": False}
    ai_fields, ai_error, source, model = None, None, "rules", None
    # Every band-mic sentence arrives here. Ask the model only when the words could be a report (a problem
    # word, a part or a place); pure chit-chat stays on the fast, free rules path.
    explicit_report = bool(text) and bool(_EXPLICIT_REPORT.search(_norm(text)))   # "I want to report a defect"
    worth_ai = bool(rules.get("has_problem") or rules.get("component") or rules.get("mentions_place") or explicit_report)
    if text and not force_rules and worth_ai:
        status = ai_status()
        if status["available"]:
            try:
                ai_fields = _ai_extract(text, st, conveyor, tools)
                source, model = "ai", status["model"]
            except Exception as error:  # network, quota, refusal: the rules path carries on
                ai_error = _short(error)
        else:
            ai_error = status["problem"]

    def meta(extra: dict) -> dict:
        extra.update({"transcript": text, "source": source, "model": model, "ai_error": ai_error,
                      "latency_ms": int((time.time() - started) * 1000)})
        return extra

    ai_component = (ai_fields or {}).get("component")
    if ai_component and not _component_grounded(ai_component, text):
        ai_component = None  # the model may not invent a part the technician never named
    component = ai_component or rules.get("component")
    condition = (ai_fields or {}).get("condition") or rules.get("condition")
    severity = (ai_fields or {}).get("severity") or rules.get("severity") or "medium"
    severity_reason = (ai_fields or {}).get("severity_reason") or rules.get("severity_reason") or ""
    if rules.get("condition") and _SEVERITY_RANK[rules["severity"]] > _SEVERITY_RANK.get(severity, 1):
        severity, severity_reason = rules["severity"], rules["severity_reason"]  # rules only ever raise severity
    partial = {"component": component, "condition": condition, "severity": severity}

    if not text:
        return meta({"status": "clarify", "reason": "empty", "partial": partial,
                     "question": "I didn't catch that. Please repeat the defect and the station number."})
    if not (rules.get("has_problem") or explicit_report or (ai_fields or {}).get("is_defect_report")):
        if rules.get("all_clear"):
            return meta({"status": "ignored", "reason": "all clear", "question": None})
        return meta({"status": "ignored", "reason": "not a defect report",
                     "question": "I didn't hear an equipment defect. Say the station, the part and what is wrong, "
                                 "for example: \"station three, return idler seized\"."})

    conv = resolve_conveyor(text, conveyor)
    if conv["question"]:
        return meta({"status": "clarify", "reason": "conveyor_mismatch", "question": conv["question"], "partial": partial})

    where = resolve_station(text, st, active_station_id)
    if where["station"] is None and where["reason"] == "missing" and ai_fields and ai_fields.get("station_evidence"):
        grounded = _grounded_station(ai_fields["station_evidence"], ai_fields.get("station_number"), text, st)
        if grounded:
            where = {"station": grounded, "reason": "ok", "question": None,
                     "evidence": "words '%s' found in the transcript" % ai_fields["station_evidence"]}
    options = [{k: s[k] for k in ("id", "number", "name")} for s in st]
    if not component:  # a problem was reported but no part named: ask, never invent one
        if where["station"] is not None:
            question = ("Which part at station %d (%s) has the problem? For example: \"return idler seized\"."
                        % (where["station"]["number"], where["station"]["name"]))
        else:
            question = "What is broken, and at which station? For example: \"station three, return idler seized\"."
        return meta({"status": "clarify", "reason": "component_missing", "question": question,
                     "partial": partial, "options": options})
    if where["station"] is None:
        return meta({"status": "clarify", "reason": "station_" + where["reason"], "question": where["question"],
                     "partial": partial, "options": options})

    station = where["station"]
    condition = condition or "defect"
    action = (ai_fields or {}).get("action") or rules.get("action") or "repair"
    if action not in _VERB:
        action = "repair"
    tool, est_min, tool_basis = _suggest_tool(component, condition, action, ai_fields, tools)
    est_points = round(exposure.points(tool["vibration_m_s2"], est_min / 60.0), 2) if tool else None
    code, label, when = _PRIORITY.get(severity, _PRIORITY["medium"])
    # "Replace idler", not "Replace needs replacement idler"
    what = component.lower() if condition.lower().startswith("needs ") else "%s %s" % (condition.lower(), component.lower())
    title = "%s %s — %s %s %s" % (_VERB[action], what, asset, station["id"], station["name"])
    summary = (ai_fields or {}).get("summary") or "%s %s at %s %s %s." % (condition.capitalize(), component.lower(), asset,
                                                                            station["id"], station["name"])
    work_order = {
        "title": title,
        "asset": asset,
        "station_id": station["id"],
        "station_name": station["name"],
        "component": component,
        "failure_mode": condition,
        "priority": label,
        "priority_code": code,
        "priority_reason": "%s: %s" % (severity_reason.rstrip(".") or "Reported defect", when),
        "steps": _steps(asset, component, condition, action, tool, (ai_fields or {}).get("task_steps")),
        "requires_isolation": True,
        "isolation_note": ISOLATION_NOTE,
        "tool_id": tool["id"] if tool else None,
        "tool_name": tool["name"] if tool else None,
        "tool_basis": tool_basis,
        "est_trigger_min": est_min,
        "est_points": est_points,
        "est_points_label": exposure.SCREENING_LABEL,
        "status": "draft",
        "close_out": CLOSE_OUT_NOTE,
    }
    return meta({
        "status": "ok",
        "station_id": station["id"],
        "station_name": station["name"],
        "station_number": station["number"],
        "conveyor_id": conv["conveyor_id"],
        "component": component,
        "condition": condition,
        "severity": severity,
        "severity_reason": severity_reason,
        "action": action,
        "summary": summary,
        "worker_id": (worker or {}).get("id"),
        "evidence": {"station": where["evidence"], "conveyor": conv["evidence"]},
        "work_order": work_order,
    })


if __name__ == "__main__":  # python backend/inspection.py "station three, return idler seized" [--rules]
    import json

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    said = " ".join(a for a in sys.argv[1:] if a != "--rules") or "Conveyor CV-102, station three, return idler seized, needs replacement."
    print(json.dumps(extract_defect(said, DEFAULT_STATIONS, {"id": "CV-102", "name": "Overland conveyor CV-102"},
                                    {"id": "W-01"}, force_rules="--rules" in sys.argv), indent=1, ensure_ascii=False))
