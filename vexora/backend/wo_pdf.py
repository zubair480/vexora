"""Vexora work order as a one-page PDF, plus the shift record pages when data["record"] is given. Stdlib only.

data["record"] (from wo_live.gather) adds pages 2+: every sentence the band mic heard this shift, the band's
motion sensor minute by minute, tool checkout and use, exposure, job steps and every event Vexora logged.

    build_work_order_pdf(data: dict) -> bytes     never raises on missing / None keys
    pdf_filename(data: dict) -> str               "Vexora-WO-0007.pdf"

A hand-written PDF 1.4: one US Letter page, Helvetica + Helvetica-Bold (WinAnsiEncoding) and one
uncompressed content stream. Long values wrap with the Helvetica AFM widths; when the page would
overflow, the longest blocks (job steps, quote, parts ...) are cut with "…", so it is always one page.

    python wo_pdf.py out.pdf [work_order.json]    write the demo work order (or the JSON) to out.pdf
"""
from __future__ import annotations

import json
import re
import sys
import time

# Adobe core-font AFM advance widths (1/1000 em) for WinAnsi codes 32..126.
_HELV = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,  # space ! " # $ % & ' ( ) * + , - . /
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,  # 0-9
    278, 278, 584, 584, 584, 556, 1015,  # : ; < = > ? @
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,  # A-M
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,  # N-Z
    278, 278, 278, 469, 556, 333,  # [ \ ] ^ _ `
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,  # a-m
    556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,  # n-z
    334, 260, 334, 584,  # { | } ~
)
_HELV_BOLD = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    333, 333, 584, 584, 584, 611, 975,
    722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    333, 278, 333, 584, 556, 333,
    556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
    611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
    389, 280, 389, 584,
)
# cp1252 extras we print (regular, bold): … ‘ ’ “ ” • – — nbsp · ° ×
_EXTRA = {0x85: (1000, 1000), 0x91: (222, 278), 0x92: (222, 278), 0x93: (333, 500), 0x94: (333, 500),
          0x95: (350, 350), 0x96: (556, 556), 0x97: (1000, 1000), 0xA0: (278, 278), 0xB7: (278, 278),
          0xB0: (400, 400), 0xD7: (584, 584)}


def _table(ascii_widths, col):
    t = [611] * 256  # other accented letters: a slightly wide guess, so wrapping errs short
    for i, w in enumerate(ascii_widths):
        t[32 + i] = w
    for code, pair in _EXTRA.items():
        t[code] = pair[col]
    return t


_W = (_table(_HELV, 0), _table(_HELV_BOLD, 1))  # indexed by bold

PAGE_W, PAGE_H = 612, 792
MARGIN = 48
LABEL_W = 130
CONTENT_W = PAGE_W - 2 * MARGIN  # 516
VALUE_X = MARGIN + LABEL_W
VALUE_W = CONTENT_W - LABEL_W  # 386
INK, MUTED, RULE = "0.035 0.035 0.043", "0.443 0.443 0.478", "0.894 0.894 0.906"  # zinc 950 / 500 / 200
BODY, SMALL, LEAD = 9.5, 8.0, 12.5
FOOTER_Y, BOTTOM = 40, 66  # only the footer goes below BOTTOM
DASH, ELL, DOT = "—", "…", "·"
FOOTER = "Created by Vexora from voice. A person decides; Vexora never closes work."
SCREENING = "Screening estimate, not a certified HAV measurement."
_SOURCES = {"band-mic": "band mic", "band_mic": "band mic", "typed": "typed"}
_FALLBACK = {"≥": ">=", "≤": "<=", "→": "->", "←": "<-", "↔": "<->", "≈": "~",
             "≠": "!=", "−": "-", "‐": "-", "‑": "-", "‒": "-", "―": "-",
             "′": "'", "″": '"', "​": "", "﻿": ""}


# --- text -------------------------------------------------------------------------------------------
def _chars(s: str) -> str:
    """Drop control characters, map a few symbols cp1252 lacks to ASCII (the rest become '?')."""
    return "".join(_FALLBACK.get(ch, ch) for ch in s if ch >= " " and not "\x7f" <= ch <= "\x9f")


def _clean(value) -> str:
    """Any value -> one line of text with single spaces."""
    if value is None:
        return ""
    return _chars(" ".join(str(value).split()))


def _pdf_str(value) -> bytes:
    b = _chars("" if value is None else str(value)).encode("cp1252", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _width(s: str, size: float, bold: bool = False) -> float:
    t = _W[bool(bold)]
    return sum(t[b] for b in s.encode("cp1252", "replace")) * size / 1000.0


def _fit_chars(s: str, size: float, width: float, bold: bool) -> int:
    t, acc, k = _W[bool(bold)], 0.0, 0
    for b in s.encode("cp1252", "replace"):
        acc += t[b] * size / 1000.0
        if acc > width:
            break
        k += 1
    return max(k, 1)


def _with_tail(line: str, size: float, width: float, bold: bool, tail: str) -> str:
    line = line.rstrip()
    while line and _width(line + tail, size, bold) > width:
        line = line[:-1].rstrip()
    return line + tail


def _wrap(text, size: float, width: float, max_lines: int, bold: bool = False, tail: str = ELL) -> list[str]:
    """Word-wrap to `width` points; more than `max_lines` lines -> cut, last line ends with `tail`."""
    max_lines = max(int(max_lines), 1)
    lines: list[str] = []
    cur = ""
    for word in _clean(text).split(" "):
        if not word:
            continue
        cand = f"{cur} {word}" if cur else word
        if _width(cand, size, bold) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = word
        while len(cur) > 1 and _width(cur, size, bold) > width and len(lines) <= max_lines:
            k = _fit_chars(cur, size, width, bold)
            lines.append(cur[:k])
            cur = cur[k:]
        if len(lines) > max_lines:
            break
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _with_tail(lines[-1], size, width, bold, tail)
    return lines


def _one(text, size: float, width: float, bold: bool = False) -> str:
    return (_wrap(text, size, width, 1, bold) or [""])[0]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _num(v, nd: int = 0):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):
        return None
    return f"{f:.{nd}f}"


def _when(ts, fmt: str) -> str:
    try:
        f = float(ts)
        if f != f:
            return ""
        if f > 1e11:  # milliseconds
            f /= 1000.0
        return time.strftime(fmt, time.localtime(f))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _pair(v) -> str:
    """{'id': 'S3', 'name': 'Return idlers'} -> 'S3 · Return idlers'; plain values pass through."""
    if isinstance(v, dict):
        a, b = _clean(v.get("id")), _clean(v.get("name"))
        return f" {DOT} ".join(x for x in (a, b) if x) if a != b else a
    return _clean(v)


def _list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


# --- layout -----------------------------------------------------------------------------------------
class _Row:
    """label str -> label + value columns; "" -> value column only; None -> full width."""

    def __init__(self, label, make, cap: int = 1, floor: int = 1):
        self.label, self.make, self.floor = label, make, floor
        self.width = CONTENT_W if label is None else VALUE_W
        self.lines = make(cap, self.width)

    def shrink(self) -> None:
        self.lines = self.make(len(self.lines) - 1, self.width)


def _val(text, color=INK, size=BODY, tail=ELL, empty=DASH):
    def make(cap, width):
        if not _clean(text):
            return [(empty, MUTED, size)]
        return [(ln, color, size) for ln in _wrap(text, size, width, cap, tail=tail)]
    return make


def _items(items, empty):
    """items: [(prefix, body, suffix)] -> one line each, plus '… and N more' when over the cap."""
    def make(cap, width):
        if not items:
            return [(empty, MUTED, BODY)]
        shown = items if len(items) <= cap else items[:max(cap - 1, 1)]
        out = []
        for prefix, body, suffix in shown:
            room = width - _width(prefix + suffix, BODY)
            out.append((prefix + (_one(body, BODY, room) or DASH) + suffix, INK, BODY))
        if len(shown) < len(items):
            out.append((f"{ELL} and {len(items) - len(shown)} more", MUTED, BODY))
        return out
    return make


def _sections(d: dict) -> list:
    said = d.get("said")
    said = said if isinstance(said, dict) else {"text": said}
    exp = d.get("exposure") if isinstance(d.get("exposure"), dict) else {}
    tool = d.get("tool")
    prio, reason = _clean(d.get("priority")), _clean(d.get("priority_reason"))

    def prio_make(cap, width):
        out = [(_one(prio, BODY, width) or DASH, INK if prio else MUTED, BODY)]
        if reason and cap > 1:
            out += [(ln, MUTED, BODY) for ln in _wrap(reason, BODY, width, cap - 1)]
        return out

    quote = _clean(said.get("text"))
    at = _when(said.get("ts"), "%H:%M:%S")
    src = _clean(said.get("source"))
    src = _SOURCES.get(src.lower(), src.replace("-", " ").replace("_", " "))
    meta = " ".join(p for p in ((f"at {at}" if at else ""), (f"via {src}" if src else "")) if p)

    steps = []
    for i, st in enumerate(_list(d.get("job_steps")), 1):
        body, state = (st.get("title"), st.get("status")) if isinstance(st, dict) else (st, None)
        state = _one(state, BODY, 90)
        steps.append((f"{i}.  ", body, f"  [{state}]" if state else ""))
    parts = [("•  ", p, "") for p in _list(d.get("parts")) if _clean(p)]

    tool_s = (_clean(tool.get("name")) or _clean(tool.get("id"))) if isinstance(tool, dict) else _clean(tool)
    raw_pts = exp.get("points")
    small = isinstance(raw_pts, (int, float)) and not isinstance(raw_pts, bool) and abs(raw_pts) < 10
    pts = _num(raw_pts, 1 if small else 0)                    # "0.3 of 400", not "0 of 400"
    lim, act = _num(exp.get("limit")), _num(exp.get("action"))
    band_s = {"within": "Within action value",
              "action": "Above action value" + (f" ({act})" if act else ""),
              "over": "Above limit value" + (f" ({lim})" if lim else "")}.get(_clean(exp.get("band")).lower(), "")
    exp_s = (f"{pts} of {lim}" if lim else f"{pts} points") if pts else ""
    exp_s = f" {DOT} ".join(x for x in (exp_s, band_s) if x)
    trig = _num(exp.get("trigger_min"), 1)

    tech = f" {DOT} ".join(x for x in (_clean(d.get("technician")), _clean(d.get("technician_name"))) if x)
    band_no = _clean(d.get("band"))
    tech = f" {DOT} ".join(x for x in (tech, f"band {band_no}" if band_no else "") if x)

    said_rows = [_Row(None, _val(f"“{quote}”" if quote else "", tail=ELL + "”",
                                 empty="Nothing recorded"), cap=4)]
    if meta:
        said_rows.append(_Row(None, _val(_cap(meta), MUTED)))
    rec = d.get("record")
    if isinstance(rec, dict):
        n_said = len([t for t in _list(rec.get("transcript")) if isinstance(t, dict) and _clean(t.get("text"))])
        said_rows.append(_Row(None, _val(f"Every word from this shift ({n_said} sentence{'s' if n_said != 1 else ''}) "
                                         f"and the band's sensor record start on page 2.", MUTED, SMALL)))
    return [
        ("Conveyor", [
            _Row("Conveyor", _val(_pair(d.get("conveyor")))),
            _Row("Station", _val(_pair(d.get("station")))),
            _Row("Component", _val(_cap(_clean(d.get("component"))))),
            _Row("Condition", _val(_cap(_clean(d.get("condition"))))),
            _Row("Severity", _val(_cap(_clean(d.get("severity"))))),
            _Row("Priority", prio_make, cap=3, floor=2 if reason else 1),
        ]),
        ("What the technician said", said_rows),
        ("Job steps so far", [_Row(None, _items(steps, "No steps yet"), cap=10, floor=3)]),
        ("Isolation", [_Row(None, _val(d.get("isolation"), empty="Not reported"), cap=3)]),
        ("Tool and exposure today", [
            _Row("Tool in use", _val(tool_s, empty="None checked out")),
            _Row("Exposure points", _val(exp_s)),
            _Row("Trigger time", _val(f"{trig} min" if trig else "")),
            _Row("", _val(SCREENING, MUTED, SMALL)),
        ]),
        ("Parts request", [_Row(None, _items(parts, "None"), cap=5, floor=3)]),
        ("Created", [
            _Row("Technician", _val(tech)),
            _Row("Created", _val(_when(d.get("created_ts"), "%Y-%m-%d %H:%M:%S"))),
        ]),
    ]


def _height(sections) -> float:
    # rule -> heading 18, heading -> first row 15, LEAD per further line, 11 below the last line
    return sum(44 + LEAD * (sum(len(r.lines) for r in rows) - 1) for _, rows in sections)


def _fit(sections, budget: float) -> None:
    """Shrink the longest flexible block one line at a time until everything fits the page."""
    for _ in range(500):
        if _height(sections) <= budget:
            return
        rows = [r for _, rs in sections for r in rs if len(r.lines) > r.floor]
        if not rows:
            return
        max(rows, key=lambda r: len(r.lines)).shrink()


# --- PDF ----------------------------------------------------------------------------------------------
def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _text(x, y, s, size, color, bold=False) -> bytes:
    head = f"BT /{'F2' if bold else 'F1'} {_f(size)} Tf {color} rg {_f(x)} {_f(y)} Td ("
    return head.encode("ascii") + _pdf_str(s) + b") Tj ET"


def _rule(y) -> bytes:
    return f"{RULE} RG 0.6 w {_f(MARGIN)} {_f(y)} m {_f(PAGE_W - MARGIN)} {_f(y)} l S".encode("ascii")


def _bar(x, y, w, h, color) -> bytes:
    return f"{color} rg {_f(x)} {_f(y)} {_f(w)} {_f(h)} re f".encode("ascii")


def _footer(i: int, n: int) -> list:
    ops = [_rule(FOOTER_Y + 12), _text(MARGIN, FOOTER_Y, FOOTER, 7.5, MUTED)]
    if n > 1:
        s = f"Page {i} of {n}"
        ops.append(_text(PAGE_W - MARGIN - _width(s, 7.5), FOOTER_Y, s, 7.5, MUTED))
    return ops


def _assemble(content, title: str) -> bytes:
    """content: one page's stream (bytes) or a list of page streams."""
    pages = content if isinstance(content, list) else [content]
    info_title = _clean(title).encode("ascii", "replace")
    info_title = info_title.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    n = len(pages)
    kids = " ".join("%d 0 R" % (6 + 2 * i) for i in range(n)).encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Title (" + info_title + b") /Producer (Vexora) >>",
    ]
    for i, content_i in enumerate(pages):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % (7 + 2 * i))
        objs.append(b"<< /Length %d >>\nstream\n" % len(content_i) + content_i + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# --- shift record pages (S0 18:45) --------------------------------------------------------------------
# Everything band 07 recorded from the start of the shift, after the one-page work order: every sentence
# the band mic heard (with what Vexora answered), the motion sensor minute by minute, tool checkout and use,
# exposure, job steps and every event Vexora logged. Only drawn when data["record"] is given.
TIME_W = 54
GREY, AMBER, RED = "0.831 0.831 0.847", "0.961 0.620 0.043", "0.937 0.267 0.267"


def _dur(sec) -> str:
    try:
        s = max(0, int(round(float(sec))))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


class _Flow:
    """Rows that flow over as many pages as they need, each page with a running header."""

    def __init__(self, header: str):
        self.header, self.pages, self.ops, self.y, self.fresh = header, [], [], 0.0, True
        self.page()

    def page(self) -> None:
        if self.ops:
            self.pages.append(self.ops)
        top = PAGE_H - MARGIN - 9
        self.ops = [_text(MARGIN, top, _one(self.header, 8.5, CONTENT_W, bold=True), 8.5, MUTED, bold=True)]
        self.y, self.fresh = top - 26, True

    def done(self) -> list:
        if self.ops:
            self.pages.append(self.ops)
            self.ops = []
        return self.pages

    def gap(self, h: float) -> None:
        self.y -= h

    def heading(self, s: str, note: str = "") -> None:
        if not self.fresh:
            self.y -= 8
        if self.y - 33 - 2 * LEAD < BOTTOM:          # keep a heading with its first lines
            self.page()
        self.ops.append(_rule(self.y + 1.5))
        self.y -= 18
        self.ops.append(_text(MARGIN, self.y, _one(s, 10.5, CONTENT_W, bold=True), 10.5, INK, bold=True))
        if note:
            room = CONTENT_W - _width(s, 10.5, True) - 16
            note = _one(note, SMALL, max(room, 40))
            self.ops.append(_text(PAGE_W - MARGIN - _width(note, SMALL), self.y, note, SMALL, MUTED))
        self.y -= 15
        self.fresh = False

    def row(self, cells, lead: float = LEAD, after: float = 0.0, keep: int = 1, bars=()) -> None:
        """cells: [(x offset, width, text, color, size, bold)], wrapped side by side; long rows cross pages.
        bars: [(x offset, width, fraction 0..1, color)] drawn on the first line."""
        wrapped = []
        for dx, w, text, color, size, bold in cells:
            wrapped.append((dx, _wrap(text, size, w, 400, bold) if _clean(text) else [], color, size, bold))
        n = max((len(c[1]) for c in wrapped), default=0)
        if n == 0 and not bars:
            return
        n = max(n, 1)
        if self.y - lead * (min(n, keep) - 1) < BOTTOM:
            self.page()
        for i in range(n):
            if self.y < BOTTOM:
                self.page()
            for dx, lines, color, size, bold in wrapped:
                if i < len(lines):
                    self.ops.append(_text(MARGIN + dx, self.y, lines[i], size, color, bold))
            if i == 0:
                for dx, w, frac, color in bars:
                    self.ops.append(_bar(MARGIN + dx, self.y - 0.5, max(1.0, w * max(0.0, min(1.0, frac))), 7, color))
            self.y -= lead
        self.y -= after
        self.fresh = False

    def text(self, s, color=INK, size=BODY, lead=LEAD, after=0.0, dx=0) -> None:
        self.row([(dx, CONTENT_W - dx, s, color, size, False)], lead=lead, after=after)

    def kv(self, label, value, color=INK) -> None:
        self.row([(0, LABEL_W - 10, label, MUTED, BODY, False), (LABEL_W, VALUE_W, value or DASH, color, BODY, False)])


def _record_pages(d: dict, rec: dict) -> list:
    wo, band = _clean(d.get("wo_id")), _clean(d.get("band")) or "07"
    f = _Flow(f" {DOT} ".join(x for x in ("Vexora shift record", wo, f"band {band}",
                                            _clean(d.get("technician"))) if x))
    since, until, made = rec.get("since"), rec.get("until"), rec.get("made")
    live = bool(rec.get("live"))

    f.row([(0, CONTENT_W, "Shift record", INK, 16, True)], lead=20)
    try:
        span = _dur(float(until) - float(since))
    except (TypeError, ValueError):
        span = ""
    when = (f"Shift started {_when(since, '%H:%M:%S')}"
            + (f", still running ({span} so far)" if live else f", ended {_when(until, '%H:%M:%S')} ({span})")
            + (f" {DOT} PDF made {_when(made, '%H:%M:%S')}" if made else ""))
    f.text(when, MUTED)
    f.text("Everything band 07 recorded from the start of the shift: every sentence its microphone heard, its "
           "motion sensor minute by minute, tool use, exposure and every action Vexora logged.", MUTED, after=2)

    # every sentence the band mic heard, in full, with what Vexora answered
    said = [t for t in _list(rec.get("transcript")) if isinstance(t, dict) and _clean(t.get("text"))]
    f.heading("Everything said on the band mic", f"{len(said)} sentence{'s' if len(said) != 1 else ''}")
    if not said:
        f.text("Nothing heard yet this shift.", MUTED)
    for t in said:
        typed = _clean(t.get("input")).lower() == "typed"
        f.row([(0, TIME_W, _when(t.get("ts"), "%H:%M:%S"), MUTED, BODY, False),
               (TIME_W, CONTENT_W - TIME_W, ("(typed) " if typed else "") + f"“{_clean(t.get('text'))}”",
                INK, BODY, False)], keep=2)
        reply = _clean(t.get("response")) or _clean(t.get("acted"))
        if reply:
            f.row([(TIME_W, CONTENT_W - TIME_W, f"Vexora: {reply}", MUTED, SMALL, False)], lead=10.5)
        f.gap(3)

    # the band's own record
    s = rec.get("sensor") if isinstance(rec.get("sensor"), dict) else {}
    f.heading("Band 07 sensor")
    rate, sensors = _num(s.get("rate")), _clean(s.get("sensors"))
    link = _clean(s.get("link")).replace("browser-ble", "Bluetooth through the Band link page")
    what = {"both": "accelerometer + gyroscope", "accel": "accelerometer", "gyro": "gyroscope"}.get(sensors, sensors)
    f.kv("Connection", f" {DOT} ".join(x for x in (link, f"{what} at {rate} Hz" if rate and what else "") if x))
    n_samples = s.get("samples")
    if isinstance(n_samples, (int, float)) and n_samples:
        avg = _num(s.get("avg_hz"))
        f.kv("Motion samples", f"{int(n_samples):,} samples from {_when(s.get('first_ts'), '%H:%M:%S')} to "
                               f"{_when(s.get('last_ts'), '%H:%M:%S')}" + (f" (average {avg} Hz)" if avg else ""))
    else:
        f.kv("Motion samples", "No motion samples recorded in this shift", MUTED)
    f.kv("Band mic", f"{len(said)} sentence{'s' if len(said) != 1 else ''} transcribed by Deepgram")
    if s.get("lost") is not None:
        f.kv("Packets lost", _clean(s.get("lost")))
    if _clean(s.get("raw_file")):
        f.kv("Raw data file", _clean(s.get("raw_file")))

    mins = [m for m in _list(s.get("minutes")) if isinstance(m, dict)]
    step = int(s.get("bucket_s") or 60)
    f.heading("Movement, " + ("minute by minute" if step == 60 else f"every {step // 60} minutes"),
              "accelerometer and gyroscope on the wrist")
    if not mins:
        f.text("No motion data in this shift.", MUTED)
    else:
        cols = ((0, "Time"), (TIME_W, "Samples"), (TIME_W + 56, "Movement"), (TIME_W + 238, "Rotation"),
                (TIME_W + 306, "Peak"), (TIME_W + 356, "Tool running"))
        f.row([(dx, 120, lab, MUTED, SMALL, True) for dx, lab in cols], lead=11)
        for m in mins:
            g = float(m.get("move_g") or 0.0)
            color = RED if g >= 0.15 else AMBER if g >= 0.05 else GREY
            tool = _clean(m.get("tool"))
            tool_s = f"{tool} {_dur(m.get('tool_s'))}" if tool and m.get("tool_s") else ""
            f.row([(0, TIME_W, _when(m.get("t"), "%H:%M"), MUTED, BODY, False),
                   (TIME_W, 54, f"{int(m.get('n') or 0):,}", INK, BODY, False),
                   (TIME_W + 56, 46, f"{g:.2f} g", INK, BODY, False),
                   (TIME_W + 238, 66, f"{float(m.get('rot_dps') or 0):.0f} °/s", INK, BODY, False),
                   (TIME_W + 306, 48, f"{float(m.get('peak_g') or 0):.2f} g", INK, BODY, False),
                   (TIME_W + 356, CONTENT_W - TIME_W - 356, tool_s, INK, BODY, False)],
                  bars=[(TIME_W + 100, 128, g / 0.5, color)])
        f.text("Movement is the wrist acceleration's spread (g rms); rotation is the gyroscope (°/s rms).",
               MUTED, SMALL, lead=10.5)

    # tool checkout and use
    f.heading("Tool checkout and use")
    outs = [c for c in _list(rec.get("checkouts")) if isinstance(c, dict)]
    sessions = [x for x in _list(rec.get("sessions")) if isinstance(x, dict)]
    if not outs and not sessions:
        f.text("No tool checked out or detected this shift.", MUTED)
    for c in outs:
        back = _when(c.get("returned_ts"), "%H:%M:%S")
        f.row([(0, TIME_W, _when(c.get("ts"), "%H:%M:%S"), MUTED, BODY, False),
               (TIME_W, CONTENT_W - TIME_W, f"{_clean(c.get('tool')) or 'Tool'} checked out to "
                                            f"{_clean(c.get('worker_id')) or 'W-01'}"
                                            + (f" {DOT} returned {back}" if back else f" {DOT} still checked out"),
                INK, BODY, False)])
    for x in sessions:
        a, b = _when(x.get("start"), "%H:%M:%S"), _when(x.get("end"), "%H:%M:%S")
        pts = _num(x.get("points"), 1)
        bits = [_clean(x.get("tool")) or "Tool not selected", f"{_dur(x.get('trigger_s'))} trigger",
                f"confidence {_num(x.get('confidence'), 2)}" if _num(x.get("confidence"), 2) else "",
                f"{pts} pts" if pts is not None else "", _clean(x.get("status"))]
        f.row([(0, TIME_W, a, MUTED, BODY, False),
               (TIME_W, CONTENT_W - TIME_W, f" {DOT} ".join(p for p in bits if p)
                + (f" {DOT} until {b}" if b else f" {DOT} running"), INK, BODY, False)])

    # exposure
    ex = rec.get("exposure") if isinstance(rec.get("exposure"), dict) else {}
    f.heading("Exposure this shift")
    tot, act, lim = _num(ex.get("total_points"), 1), _num(ex.get("action")), _num(ex.get("limit"))
    st = {"below_action": "within the action value", "above_action": "above the action value",
          "above_limit": "above the limit value"}.get(_clean(ex.get("status")), _clean(ex.get("status")).replace("_", " "))
    f.kv("Total", f" {DOT} ".join(x for x in (f"{tot} points" if tot else "0 points",
                                                f"action {act} / limit {lim}" if act and lim else "", st) if x))
    for tid, v in (ex.get("by_tool") or {}).items():
        if isinstance(v, dict):
            f.kv(_clean(v.get("name")) or _clean(tid),
                 f"{_dur(v.get('trigger_s'))} trigger {DOT} {_num(v.get('points'), 1) or '0'} pts")
    if _clean(ex.get("method")):
        f.text(_clean(ex.get("method")), MUTED, SMALL, lead=10.5)

    # job steps
    steps = [x for x in _list(rec.get("steps")) if isinstance(x, dict) and _clean(x.get("title"))]
    f.heading("Job steps", f"{len(steps)} step{'s' if len(steps) != 1 else ''}")
    if not steps:
        f.text("No steps yet.", MUTED)
    for i, x in enumerate(steps, 1):
        f.row([(0, 22, f"{i}.", MUTED, BODY, False),
               (22, CONTENT_W - 22 - 80, _clean(x.get("title")), INK, BODY, False),
               (CONTENT_W - 76, 76, _clean(x.get("status")), MUTED, BODY, False)])
        if _clean(x.get("detail")):
            f.row([(22, CONTENT_W - 22 - 80, _clean(x.get("detail")), MUTED, SMALL, False)], lead=10.5)

    # every event Vexora logged
    evs = [e for e in _list(rec.get("events")) if isinstance(e, dict) and _clean(e.get("title"))]
    f.heading("Everything Vexora logged", f"{len(evs)} event{'s' if len(evs) != 1 else ''}")
    if not evs:
        f.text("No events yet.", MUTED)
    for e in evs:
        f.row([(0, TIME_W, _when(e.get("ts"), "%H:%M:%S"), MUTED, BODY, False),
               (TIME_W, CONTENT_W - TIME_W, _clean(e.get("title")), INK, BODY, False)])
        if _clean(e.get("detail")):
            f.row([(TIME_W, CONTENT_W - TIME_W, _clean(e.get("detail")), MUTED, SMALL, False)], lead=10.5)
        f.gap(2)
    return f.done()


def build_work_order_pdf(data: dict) -> bytes:
    """Render a Vexora work order dict as a one-page PDF (bytes). Every key is optional."""
    d = data if isinstance(data, dict) else {}
    wo, status, title = _clean(d.get("wo_id")), _clean(d.get("status")), _clean(d.get("title"))
    ops: list[bytes] = []

    y = PAGE_H - MARGIN - 13  # baseline of the 18 pt header
    head = "Vexora work order" + (f" {wo}" if wo else "") + (f" {DOT} {status.upper()}" if status else "")
    ops.append(_text(MARGIN, y, _one(head, 18, CONTENT_W, bold=True), 18, INK, bold=True))
    gap = 17
    for ln in _wrap(title, 10.5, CONTENT_W, 2):
        y -= gap
        gap = 13
        ops.append(_text(MARGIN, y, ln, 10.5, MUTED))
    y -= 15

    sections = _sections(d)
    _fit(sections, y - BOTTOM)
    for heading, rows in sections:
        ops.append(_rule(y))
        y -= 18
        ops.append(_text(MARGIN, y, heading, 10.5, INK, bold=True))
        y -= 15
        for r_i, row in enumerate(rows):
            if r_i:
                y -= LEAD
            if row.label:
                ops.append(_text(MARGIN, y, row.label, BODY, MUTED))
            x = MARGIN if row.label is None else VALUE_X
            for l_i, (s, color, size) in enumerate(row.lines):
                if l_i:
                    y -= LEAD
                ops.append(_text(x, y, s, size, color))
        y -= 11

    pages = [ops]
    rec = d.get("record")
    if isinstance(rec, dict):
        try:
            pages += _record_pages(d, rec)
        except Exception as e:  # the work order page must always render
            print("[wo_pdf] shift record pages failed:", repr(e), file=sys.stderr)
    n = len(pages)
    streams = [b"\n".join(p + _footer(i + 1, n)) for i, p in enumerate(pages)]
    return _assemble(streams if n > 1 else streams[0], "Vexora work order" + (f" {wo}" if wo else ""))


def pdf_filename(data) -> str:
    """'WO-0007' -> 'Vexora-WO-0007.pdf' (safe characters only)."""
    wo = _clean(data.get("wo_id")) if isinstance(data, dict) else ""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", wo).strip("-.")
    return f"Vexora-{safe}.pdf" if safe else "Vexora-work-order.pdf"


def _demo() -> dict:
    now = time.time()
    return {
        "wo_id": "WO-0007", "status": "draft", "title": "Replace return idler at S3",
        "priority": "P2", "priority_reason": "Medium severity on a running belt; plan it for the next stop.",
        "conveyor": {"id": "CV-102", "name": "Overland conveyor CV-102"},
        "station": {"id": "S3", "name": "Return idlers"},
        "component": "return idler", "condition": "change needed", "severity": "medium",
        "said": {"text": "idler change needed", "ts": now - 95, "source": "band-mic"},
        "job_steps": [{"title": "Isolate and lock out the drive", "status": "done"},
                      {"title": "Swap the idler", "status": "todo"}],
        "isolation": "Isolation reported by technician at " + time.strftime("%H:%M", time.localtime(now - 400))
                     + " (not verified by Vexora)",
        "tool": {"id": "impact_wrench", "name": "Impact wrench"},
        "exposure": {"points": 123.4, "trigger_min": 12.5, "band": "action", "action": 100, "limit": 400},
        "parts": ["2x return idler roller"],
        "technician": "W-01", "technician_name": "Technician (band 07)", "band": "07",
        "created_ts": now - 90,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "sample-wo.pdf"
    payload = _demo()
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as fh:
            payload = json.load(fh)
    with open(target, "wb") as fh:
        fh.write(build_work_order_pdf(payload))
    print(target)
