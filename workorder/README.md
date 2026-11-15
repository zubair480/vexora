# Work-order flow

One click on **Work order** runs the whole flow and records what really happened:

`draft → checks → schedule → create work order → calendar → verify`

A step that cannot run is shown as **blocked** with the real reason. Nothing is reported that did not happen.

## Run

From the repo root (Python 3.11+, `pip install fastapi uvicorn`):

    python -m uvicorn workorder.app:app --port 8020

Open http://127.0.0.1:8020/, type your name in **Approved by**, click **Work order**.

## What each step does

| Step | What it does |
|---|---|
| draft | builds the work order from the form (sample values if nothing is sent, marked "Sample data") |
| checks | refuses it without an asset, a problem, a task, a valid priority, a repair time and a named approver |
| schedule | picks the repair window: the given start, or the next half hour at least 30 minutes away |
| work order | creates it in the configured system and keeps the real reference |
| calendar | prepares an `.ics` file and an "Add to Google Calendar" link (a person adds it; nothing is booked automatically) |
| verify | reads the work order back from that system and checks it matches |

## Plugging it in

- **API:** `POST /api/workorder/run` (JSON: `approved_by`, `asset_id`, `station_id`, `problem`, `task`, `priority`,
  `repair_minutes`, `technician`, `window_start`), `GET /api/workorder`, `GET /api/workorder/{id}`,
  `GET /api/workorder/{id}/event.ics`.
- **Mount in another FastAPI app:** `app.include_router(workorder.app.router)`.
- **Another system for the work order:** set `WORKORDER_PROVIDER=odoo` with `ODOO_URL`, `ODOO_API_KEY`, `ODOO_DB`
  (creates an Odoo maintenance request over the JSON-2 API). Ported from PlantAPI; not yet run against a live Odoo.
  Add a new class with `create` and `verify` in `providers.py` for any other system.
- **Another calendar:** add a class with `schedule` in `providers.py` and return it from `calendar_provider()`.

The pattern (state machine, evidence per step, re-check instead of trust) comes from PlantAPI's engine.
