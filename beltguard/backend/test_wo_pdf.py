"""Tests for wo_pdf.py (one-page work order PDF).  Run:  python -m pytest backend/test_wo_pdf.py -q"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import wo_pdf  # noqa: E402

FULL = {
    "wo_id": "WO-0007", "status": "draft", "title": "Replace return idler at S3",
    "priority": "P2", "priority_reason": "Medium severity on a running belt; plan it for the next stop.",
    "conveyor": {"id": "CV-102", "name": "Overland conveyor CV-102"},
    "station": {"id": "S3", "name": "Return idlers"},
    "component": "return idler", "condition": "change needed", "severity": "medium",
    "said": {"text": "idler change needed", "ts": 1731625000.0, "source": "band-mic"},
    "job_steps": [{"title": "Isolate and lock out the drive", "status": "done"},
                  {"title": "Swap the idler", "status": "todo"}],
    "isolation": "Isolation reported by technician at 16:41 (not verified by BeltGuard)",
    "tool": {"id": "impact_wrench", "name": "Impact wrench"},
    "exposure": {"points": 123.4, "trigger_min": 12.5, "band": "action", "action": 100, "limit": 400},
    "parts": ["2x return idler roller"],
    "technician": "W-01", "technician_name": "Technician (band 07)", "band": "07",
    "created_ts": 1731625001.0,
}
HEADER = "BeltGuard work order WO-0007 · DRAFT"
SCREENING = "Screening estimate, not a certified HAV measurement."
FOOTER = "Created by BeltGuard from voice. A person decides; BeltGuard never closes work."


def _enc(s: str) -> bytes:
    """Text as it appears inside a PDF literal string: cp1252 bytes with \\ ( ) escaped."""
    b = s.encode("cp1252")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _check_valid(pdf: bytes) -> None:
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip(b"\r\n").endswith(b"%%EOF")
    for kw in (b"xref", b"trailer", b"startxref"):
        assert kw in pdf
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", pdf)
    assert m, "startxref number missing"
    off = int(m.group(1))
    assert pdf[off:off + 4] == b"xref", "startxref does not point at the xref keyword"
    head = re.match(rb"xref\r?\n(\d+) (\d+)\r?\n", pdf[off:])
    assert head
    first, count = int(head.group(1)), int(head.group(2))
    table = pdf[off + head.end(): off + head.end() + 20 * count]
    assert len(table) == 20 * count
    for i in range(count):
        entry = table[20 * i: 20 * i + 20]
        assert re.fullmatch(rb"\d{10} \d{5} [nf](?: \n|\r\n| \r)", entry), entry
        if entry[17:18] == b"n":
            assert pdf[int(entry[:10]):].startswith(b"%d 0 obj" % (first + i)), (first + i, entry)
    trailer = pdf[pdf.rindex(b"trailer"):]
    assert re.search(rb"/Size %d\b" % count, trailer)
    assert b"/Root" in trailer
    streams = list(re.finditer(rb"/Length (\d+)\s*>>\s*stream\r?\n", pdf))
    assert streams
    for sm in streams:
        end = sm.end() + int(sm.group(1))
        assert re.match(rb"\r?\nendstream", pdf[end:end + 12]), "stream /Length is wrong"


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def _positions(pdf: bytes) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(rb"(-?[\d.]+) (-?[\d.]+) Td", pdf)]


def _assert_on_page(pdf: bytes) -> None:
    pos = _positions(pdf)
    assert pos
    for x, y in pos:
        assert wo_pdf.MARGIN <= x < wo_pdf.PAGE_W - wo_pdf.MARGIN, (x, y)
        assert wo_pdf.FOOTER_Y <= y <= wo_pdf.PAGE_H - wo_pdf.MARGIN, (x, y)
    # every line ends inside the right margin (measured with the same AFM widths)
    ops = re.findall(rb"BT /F([12]) ([\d.]+) Tf [\d. ]+ rg (-?[\d.]+) (-?[\d.]+) Td \((.*?)\) Tj ET", pdf)
    assert len(ops) == len(pos)
    for font, size, x, _y, raw in ops:
        text = re.sub(rb"\\(.)", rb"\1", raw).decode("cp1252", "replace")
        right = float(x) + wo_pdf._width(text, float(size), bold=font == b"2")
        assert right <= wo_pdf.PAGE_W - wo_pdf.MARGIN + 0.01, (right, text[:60])


def test_full_sample_is_a_valid_one_page_pdf():
    pdf = wo_pdf.build_work_order_pdf(FULL)
    _check_valid(pdf)
    assert _page_count(pdf) == 1
    assert b"\n%\xe2\xe3\xcf\xd3\n" in pdf[:20]
    assert b"/Title (BeltGuard work order WO-0007)" in pdf
    assert b"/Producer (BeltGuard)" in pdf
    assert pdf.count(b"/Encoding /WinAnsiEncoding") == 2
    _assert_on_page(pdf)


def test_full_sample_text_is_in_the_content_stream():
    pdf = wo_pdf.build_work_order_pdf(FULL)
    for phrase in (HEADER, "Replace return idler at S3", "CV-102", "CV-102 · Overland conveyor CV-102",
                   "S3 · Return idlers", "idler change needed", "“idler change needed”",
                   "via band mic", "1.  Isolate and lock out the drive  [done]", "2.  Swap the idler  [todo]",
                   "Isolation reported by technician at 16:41 (not verified by BeltGuard)", "Impact wrench",
                   "123 of 400 · Above action value (100)", "12.5 min", SCREENING,
                   "•  2x return idler roller", "W-01 · Technician (band 07) · band 07", FOOTER):
        assert _enc(phrase) in pdf, phrase


def test_exposure_band_texts():
    for band, text in (("within", "Within action value"), ("action", "Above action value (100)"),
                       ("over", "Above limit value (400)")):
        pdf = wo_pdf.build_work_order_pdf(dict(FULL, exposure=dict(FULL["exposure"], band=band)))
        assert _enc(text) in pdf, band


def test_empty_and_all_none_inputs_still_render():
    nested_none = {k: None for k in FULL}
    nested_none.update(conveyor={"id": None, "name": None}, said={"text": None, "ts": None, "source": None},
                       tool={"id": None, "name": None}, job_steps=[{"title": None, "status": None}, None],
                       exposure={k: None for k in FULL["exposure"]}, parts=[None])
    for data in ({}, {k: None for k in FULL}, nested_none, None):
        pdf = wo_pdf.build_work_order_pdf(data)
        _check_valid(pdf)
        assert _page_count(pdf) == 1
        assert _enc(FOOTER) in pdf
        _assert_on_page(pdf)
    pdf = wo_pdf.build_work_order_pdf({})
    for phrase in ("No steps yet", "Not reported", "None checked out", "Nothing recorded", SCREENING):
        assert _enc(phrase) in pdf, phrase


def test_long_steps_and_quote_stay_on_one_page():
    quote = "belt " * 400
    assert len(quote) == 2000
    data = dict(FULL, job_steps=[{"title": f"Step {i} check the idler frame", "status": "todo"} for i in range(40)],
                said={"text": quote, "ts": 1731625000.0, "source": "band-mic"})
    pdf = wo_pdf.build_work_order_pdf(data)
    _check_valid(pdf)
    assert _page_count(pdf) == 1
    assert "…".encode("cp1252") in pdf
    assert re.search(rb"\x85 and \d+ more", pdf)
    _assert_on_page(pdf)


def test_every_field_long_still_fits_one_page():
    long = "conveyor " * 250
    data = {
        "wo_id": "WO-" + "9" * 300, "status": long, "title": long, "priority": long, "priority_reason": long,
        "conveyor": {"id": long, "name": long}, "station": {"id": long, "name": long},
        "component": long, "condition": long, "severity": long,
        "said": {"text": long, "ts": 1731625000.0, "source": long},
        "job_steps": [{"title": long, "status": long}] * 40, "isolation": long,
        "tool": {"id": long, "name": long},
        "exposure": {"points": 1e9, "trigger_min": 1e9, "band": "over", "action": 100, "limit": 400},
        "parts": [long] * 40, "technician": long, "technician_name": long, "band": long,
        "created_ts": 1731625001.0,
    }
    pdf = wo_pdf.build_work_order_pdf(data)
    _check_valid(pdf)
    assert _page_count(pdf) == 1
    assert _enc(FOOTER) in pdf and _enc(SCREENING) in pdf
    _assert_on_page(pdf)


def test_parentheses_and_backslashes_are_escaped():
    pdf = wo_pdf.build_work_order_pdf(dict(FULL, title="(left) a\\b", said={"text": "x) (y \\ z"}))
    assert b"\\(left\\) a\\\\b" in pdf
    assert _enc("(left) a\\b") in pdf
    _check_valid(pdf)


def test_characters_outside_cp1252_fall_back():
    pdf = wo_pdf.build_work_order_pdf({"said": {"text": "load ≥ 3 kN → swap \U0001F600 now"}})
    assert b"load >= 3 kN -> swap ? now" in pdf
    _check_valid(pdf)


def test_pdf_filename():
    assert wo_pdf.pdf_filename(FULL) == "BeltGuard-WO-0007.pdf"
    assert wo_pdf.pdf_filename({}) == "BeltGuard-work-order.pdf"
    assert wo_pdf.pdf_filename({"wo_id": None}) == "BeltGuard-work-order.pdf"
    assert wo_pdf.pdf_filename({"wo_id": "WO 7/../x"}) == "BeltGuard-WO-7-..-x.pdf"


def test_width_tables_cover_printable_ascii():
    assert len(wo_pdf._HELV) == len(wo_pdf._HELV_BOLD) == 95
    assert wo_pdf._width("A", 1000) == 667 and wo_pdf._width("A", 1000, bold=True) == 722
    assert wo_pdf._width("~", 1000) == 584 and wo_pdf._width("i", 1000) == 222
