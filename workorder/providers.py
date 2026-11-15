"""Where the work order and the calendar entry are created. Swap a provider without touching the flow.

Work order:  `local` (default: BeltGuard's own store) or `odoo` (Odoo JSON-2 API, a maintenance request).
Calendar:    `link` (default: an .ics file and an "Add to Google Calendar" link; a person adds it).

Choose with env `WORKORDER_PROVIDER`. Odoo needs `ODOO_URL`, `ODOO_API_KEY` and optionally `ODOO_DB`.
The Odoo provider is ported from PlantAPI's `odooCall`; it has NOT been run against a live Odoo here.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from . import store


class ProviderError(Exception):
    pass


def _utc(dt: datetime, fmt: str) -> str:
    return dt.astimezone(timezone.utc).strftime(fmt)


def title_of(req: dict) -> str:
    return f"{req['task']} — {req['asset_id']}"


def describe(wo_ref: str, req: dict) -> str:
    return (f"Work order {wo_ref} | {req.get('conveyor_id') or ''} {req.get('station_id') or ''} {req['asset_id']} | "
            f"Problem: {req['problem']} | Priority: {req['priority']} | Technician: {req['technician']} | "
            f"Approved by: {req['approved_by']} | Isolation must be verified by an authorized person before work starts.")


# ---------------------------------------------------------------- work orders
class LocalProvider:
    """The work order lives in BeltGuard's own database; its id is the reference."""
    name = "beltguard"

    def create(self, wo_id: str, req: dict, begin: datetime, end: datetime) -> dict:
        return {"ref": wo_id, "system": self.name, "url": None}

    def verify(self, ref: str, req: dict, begin: datetime, end: datetime) -> Optional[str]:
        row = store.get(ref)
        if not row:
            return f"{ref} is not in the store"
        if row["request"].get("asset_id") != req["asset_id"]:
            return f"{ref} is for {row['request'].get('asset_id')}, not {req['asset_id']}"
        return None


class OdooProvider:
    """A maintenance request in Odoo, created over the JSON-2 API and read back to verify."""
    name = "odoo"

    def _call(self, model: str, method: str, body: dict):
        url = os.environ.get("ODOO_URL", "").rstrip("/")
        key = os.environ.get("ODOO_API_KEY", "")
        if not url or not key:
            raise ProviderError("Odoo is not configured: set ODOO_URL and ODOO_API_KEY")
        headers = {"Content-Type": "application/json", "Authorization": f"bearer {key}"}
        if os.environ.get("ODOO_DB"):
            headers["X-Odoo-Database"] = os.environ["ODOO_DB"]
        last = None
        for _ in range(3):                                   # retry network errors and 5xx twice
            request = urllib.request.Request(f"{url}/json/2/{model}/{method}", json.dumps(body).encode(), headers)
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                last = ProviderError(f"Odoo {model}.{method} returned {error.code}")
                if error.code < 500:
                    raise last from None                     # client errors are not retried
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last = ProviderError(f"Odoo {model}.{method} failed: {type(error).__name__}")
        raise last

    def create(self, wo_id: str, req: dict, begin: datetime, end: datetime) -> dict:
        ids = self._call("maintenance.request", "create", {"vals_list": [{
            "name": f"{wo_id}: {title_of(req)}", "description": describe(wo_id, req),
            "schedule_date": _utc(begin, "%Y-%m-%d %H:%M:%S"),
            "duration": round((end - begin).total_seconds() / 3600, 2),
            "priority": {"low": "1", "medium": "2", "high": "3"}[req["priority"]]}]})
        if not isinstance(ids, list) or not ids:
            raise ProviderError("Odoo did not return a record id")
        return {"ref": f"maintenance.request:{ids[0]}", "system": self.name,
                "url": f"{os.environ.get('ODOO_URL', '').rstrip('/')}/odoo/action-maintenance.hr_equipment_request_action/{ids[0]}"}

    def verify(self, ref: str, req: dict, begin: datetime, end: datetime) -> Optional[str]:
        rows = self._call("maintenance.request", "read", {"ids": [int(ref.split(":")[-1])], "fields": ["name", "schedule_date"]})
        if not rows:
            return f"{ref} not found in Odoo"
        if req["asset_id"] not in str(rows[0].get("name")):
            return f"{ref} in Odoo does not name {req['asset_id']}"
        return None


def work_order_provider():
    return OdooProvider() if os.environ.get("WORKORDER_PROVIDER", "local").lower() == "odoo" else LocalProvider()


# ---------------------------------------------------------------- calendar
def ics(wo_id: str, wo_ref: str, req: dict, begin: datetime, end: datetime) -> str:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    stamp = "%Y%m%dT%H%M%SZ"
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BeltGuard//Work order//EN", "BEGIN:VEVENT",
        f"UID:{wo_id}@beltguard", f"DTSTAMP:{_utc(datetime.now(timezone.utc), stamp)}",
        f"DTSTART:{_utc(begin, stamp)}", f"DTEND:{_utc(end, stamp)}",
        f"SUMMARY:{esc(req['technician'] + ': ' + title_of(req))}",
        f"DESCRIPTION:{esc(describe(wo_ref, req))}",
        f"LOCATION:{esc(' '.join(x for x in (req.get('conveyor_id'), req.get('station_id')) if x))}",
        "END:VEVENT", "END:VCALENDAR", ""])


class LinkCalendar:
    """No account needed: an .ics file plus a prefilled Google Calendar link. A person adds the event."""
    name = "link"

    def schedule(self, wo_id: str, wo_ref: str, req: dict, begin: datetime, end: datetime) -> dict:
        stamp = "%Y%m%dT%H%M%SZ"
        query = urllib.parse.urlencode({
            "action": "TEMPLATE", "text": f"{req['technician']}: {title_of(req)}",
            "dates": f"{_utc(begin, stamp)}/{_utc(end, stamp)}", "details": describe(wo_ref, req),
            "location": " ".join(x for x in (req.get("conveyor_id"), req.get("station_id")) if x)})
        return {"status": "ready", "provider": self.name, "booked": False,
                "detail": "Calendar entry prepared: open the link or the .ics file to add it (not added automatically)",
                "google_link": f"https://calendar.google.com/calendar/render?{query}",
                "ics_path": f"/api/workorder/{wo_id}/event.ics"}


def calendar_provider():
    return LinkCalendar()
