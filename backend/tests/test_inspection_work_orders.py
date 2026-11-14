"""Work-order drafts: creation, listing, human review transitions, persistence."""
import re

import pytest

from backend import database, work_orders
from backend.work_orders import InvalidTransition, WorkOrderNotFound

FIELDS = {
    "work_order_id", "defect_id", "inspection_id", "asset_id", "station_id", "conveyor_id",
    "task", "priority", "status", "source", "shift_id", "worker_id", "created_ts", "updated_ts",
    "reviewed_by", "review_note",
}


def make(**over):
    args = dict(defect_id="DEF-1", inspection_id="INS-1", asset_id="CV-102-IDLER-07",
                station_id="ST-3", conveyor_id="CV-102", task="Replace seized idler",
                priority="high", shift_id="shift-a", worker_id="worker-1")
    args.update(over)
    return work_orders.create(**args)


def test_create_gives_a_draft(tmp_db):
    order = make()
    assert set(order) == FIELDS
    assert order["status"] == "draft"
    assert order["source"] == "ai"
    assert re.fullmatch(r"WO-[0-9A-F]{8}", order["work_order_id"])
    assert order["reviewed_by"] is None and order["review_note"] is None
    assert order["created_ts"] == order["updated_ts"]
    assert work_orders.history(order["work_order_id"]) == []


def test_create_with_given_id_and_nullable_location(tmp_db):
    order = make(work_order_id="WO-FIXED", station_id=None, conveyor_id=None, source="manual")
    assert order["work_order_id"] == "WO-FIXED"
    stored = work_orders.get("WO-FIXED")
    assert stored == order
    assert stored["station_id"] is None and stored["conveyor_id"] is None
    assert stored["source"] == "manual"


def test_get_and_list(tmp_db):
    assert work_orders.get("WO-NOPE") is None
    assert work_orders.list_work_orders() == []
    first, second, third = make(work_order_id="WO-1"), make(work_order_id="WO-2"), make(work_order_id="WO-3")
    assert work_orders.get("WO-2") == second
    assert [o["work_order_id"] for o in work_orders.list_work_orders()] == ["WO-1", "WO-2", "WO-3"]
    assert work_orders.list_work_orders()[0] == first
    assert work_orders.list_work_orders()[2] == third


def test_list_filters(tmp_db):
    make(work_order_id="WO-A1", shift_id="shift-a")
    make(work_order_id="WO-A2", shift_id="shift-a")
    make(work_order_id="WO-B1", shift_id="shift-b")
    work_orders.review("WO-A2", "submit", "Dana")
    ids = lambda **kw: [o["work_order_id"] for o in work_orders.list_work_orders(**kw)]
    assert ids(shift_id="shift-a") == ["WO-A1", "WO-A2"]
    assert ids(shift_id="shift-b") == ["WO-B1"]
    assert ids(status="draft") == ["WO-A1", "WO-B1"]
    assert ids(status="submitted") == ["WO-A2"]
    assert ids(shift_id="shift-a", status="draft") == ["WO-A1"]
    assert ids(shift_id="shift-b", status="approved") == []


def test_happy_path_submit_then_approve(tmp_db):
    wo = make()["work_order_id"]
    submitted = work_orders.review(wo, "submit", "  Dana  ", note="looks right")
    assert submitted["status"] == "submitted"
    assert submitted["reviewed_by"] == "Dana"
    assert submitted["review_note"] == "looks right"
    assert submitted["updated_ts"] >= submitted["created_ts"]
    approved = work_orders.review(wo, "approve", "Sam")
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "Sam"
    assert work_orders.get(wo) == approved


def test_reject_paths(tmp_db):
    after_submit = make()["work_order_id"]
    work_orders.review(after_submit, "submit", "Dana")
    assert work_orders.review(after_submit, "reject", "Sam", "duplicate")["status"] == "rejected"
    discarded = make()["work_order_id"]
    out = work_orders.review(discarded, "reject", "Dana", "not a real defect")
    assert out["status"] == "rejected" and out["review_note"] == "not a real defect"


def test_cannot_approve_a_draft(tmp_db):
    wo = make()["work_order_id"]
    with pytest.raises(InvalidTransition, match="submit it for review first"):
        work_orders.review(wo, "approve", "Dana")
    assert work_orders.get(wo)["status"] == "draft"
    assert work_orders.history(wo) == []


def test_cannot_submit_twice(tmp_db):
    wo = make()["work_order_id"]
    work_orders.review(wo, "submit", "Dana")
    with pytest.raises(InvalidTransition):
        work_orders.review(wo, "submit", "Dana")


@pytest.mark.parametrize("final_action", ["approve", "reject"])
def test_final_states_accept_nothing(tmp_db, final_action):
    wo = make()["work_order_id"]
    work_orders.review(wo, "submit", "Dana")
    final = work_orders.review(wo, final_action, "Sam")
    for action in work_orders.ACTIONS:
        with pytest.raises(InvalidTransition, match="final"):
            work_orders.review(wo, action, "Sam")
    assert work_orders.get(wo) == final
    assert len(work_orders.history(wo)) == 2


def test_unknown_action_and_blank_reviewer(tmp_db):
    wo = make()["work_order_id"]
    with pytest.raises(InvalidTransition, match="unknown action"):
        work_orders.review(wo, "auto_approve", "Dana")
    for blank in ("", "   ", None):
        with pytest.raises(InvalidTransition, match="a named human reviewer is required"):
            work_orders.review(wo, "submit", blank)
    assert work_orders.get(wo)["status"] == "draft"


def test_unknown_id(tmp_db):
    with pytest.raises(WorkOrderNotFound):
        work_orders.review("WO-MISSING", "submit", "Dana")
    assert issubclass(WorkOrderNotFound, work_orders.WorkOrderError)
    assert issubclass(InvalidTransition, work_orders.WorkOrderError)
    assert work_orders.history("WO-MISSING") == []


def test_history_rows(tmp_db):
    wo = make()["work_order_id"]
    other = make()["work_order_id"]
    work_orders.review(wo, "submit", "Dana", "ready")
    work_orders.review(wo, "approve", "Sam")
    work_orders.review(other, "reject", "Dana")
    rows = work_orders.history(wo)
    assert [set(r) for r in rows] == [
        {"work_order_id", "action", "from_status", "to_status", "reviewer", "note", "ts"}] * 2
    assert [(r["action"], r["from_status"], r["to_status"], r["reviewer"], r["note"]) for r in rows] == [
        ("submit", "draft", "submitted", "Dana", "ready"),
        ("approve", "submitted", "approved", "Sam", None),
    ]
    assert all(r["work_order_id"] == wo for r in rows)
    assert rows[0]["ts"] <= rows[1]["ts"]
    assert len(work_orders.history(other)) == 1


def test_persists_across_connections(tmp_db):
    wo = make()["work_order_id"]
    work_orders.review(wo, "submit", "Dana")
    database.reset_for_tests()
    con = database.connect()
    try:
        row = con.execute(
            "SELECT status, reviewed_by FROM work_orders WHERE work_order_id = ?", (wo,)
        ).fetchone()
    finally:
        con.close()
    assert row["status"] == "submitted" and row["reviewed_by"] == "Dana"
    assert work_orders.get(wo)["status"] == "submitted"
    assert len(work_orders.history(wo)) == 1
