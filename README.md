# BeltGuard

BeltGuard is a conveyor-maintenance and hand-arm-vibration (HAV) exposure platform driven by a
Vexo Open wristband. A worker walks the conveyor route, reports defects by voice, and uses
powered hand tools. BeltGuard turns that into: a structured defect and a draft work order, a
candidate tool-use session that a person confirms, an estimated HAV exposure figure, and a
planning recommendation for the next job that a supervisor must decide on.

Everything it produces is an estimate or a draft for a human to review. See
[Honesty statements](#honesty-statements).

- Demo script: [docs/DEMO.md](docs/DEMO.md)
- What is verified and what is not: [docs/CAPABILITIES.md](docs/CAPABILITIES.md)
- Backend contract between modules: [backend/CONTRACT.md](backend/CONTRACT.md)

## Architecture

```
Vexo Open band (IMU, microphone, LED)
        |  Bluetooth / USB, unmodified Vexo SDK (reference/vexo-open-kit/)
        v
Python bridge            backend/vexo_bridge.py   SDK handlers only enqueue
        |                (or backend/replay.py for a recording, source = "replay")
        v
Bounded event bus        backend/event_bus.py     events queue 2000, IMU queue 500;
        |                                         a full queue drops and counts, never blocks
        +--> tool-use classifier + session detector   backend/tool_classifier.py, tool_sessions.py
        +--> speech + AI extraction                   backend/speech_engine.py, inspection_agent.py
        +--> exposure + planner                       backend/exposure.py, planner.py
        v
SQLite                   beltguard.db (events and module tables)
        v
FastAPI + WebSocket      backend/main.py, backend/routers/   port 8010, /ws
        v
React dashboard          app/ (Vite, port 5173)
```

- High-rate IMU travels as `ImuBatch` on a separate bus queue. It does not go over the WebSocket;
  the chart endpoint is `GET /api/band/imu/recent`.
- Every event carries a `source`: `band`, `replay`, `synthetic`, `manual`, `ai` or `derived`.
- `sensor_quality` events are sent live but not stored.
- Dropped bus items are visible in `GET /api/health` (`dropped_events`, `dropped_imu`).

Current state of the dashboard: `app/` renders the demo data in `app/src/data/shift.ts`. The
backend serves the same `ShiftData` shape at `GET /api/shift`, but the dashboard does not fetch it
yet. See [docs/CAPABILITIES.md](docs/CAPABILITIES.md).

## Install

Requirements: Python 3.12, Node.js with npm, Windows PowerShell (commands below are run from the
repo root).

```powershell
python -m pip install -r backend/requirements.txt
npm --prefix app install
```

The Vexo SDK is not installed with pip. It is used straight from `reference/vexo-open-kit/`,
unmodified.

## `.env` setup

```powershell
Copy-Item .env.example .env
```

Then fill in `.env` yourself:

```
VEXO_BAND=<your band number>
DEEPGRAM_API_KEY=<your Deepgram key>
OPENAI_API_KEY=<your OpenAI key>
```

- `.env` is gitignored. Never commit it, never paste a key into code, a chat, a log or a
  screenshot. If a key leaks, tell an organiser at once.
- A real environment variable wins over the `.env` file.
- `DEEPGRAM_API_KEY` is needed only for speech from the band's microphone.
- `OPENAI_API_KEY` is needed for every defect report, typed or spoken (the field extraction).
- Optional: `BELTGUARD_DB` (database path, default `beltguard.db`), `BELTGUARD_SHIFT`
  (default `shift-demo`), `BELTGUARD_WORKER` (default `worker-1`), `BELTGUARD_CONVEYOR`
  (default `CV-102`), `OPENAI_MODEL` (read by the kit's `ai.py`).

## Device setup

These follow the kit's rules in `reference/vexo-open-kit/AGENTS.md`.

1. Your band number is written on the bag the band came in. Put it in one place only:
   `VEXO_BAND=<your band number>` in `.env`. The backend reads it from there and nowhere else.
2. There is no default. If `VEXO_BAND` is unset, the backend refuses to connect
   (`409 band-number-missing`). It never guesses, never scans, never tries another number.
3. Never use a number from an example, a log or another team. Other teams are in the same room.
   If a different band answers, the connection is refused (`wrong-band`).
4. If the band is not found: make sure nothing else is connected to it (the kit dashboard, a
   browser tab, a teammate's laptop), then double-tap the band (it can be found for 60 seconds)
   and try again. Do not try another number.
5. Bluetooth is the default transport. With a USB data cable use `"transport": "serial"`.
6. The band takes one Bluetooth connection at a time. Close the kit dashboard and any browser
   connection before the backend connects.
7. While the microphone is on, the band's light is solid blue and what it hears is sent over the
   internet to Deepgram. Tell the people nearby first.

Note: the dashboard has its own, separate browser connection path with its own constant
(`BAND_NUMBER` in `app/src/band/config.ts`, currently `null`, so its Connect buttons only explain
what to do). The backend flow in this README does not use it. For the demo, connect through the
backend only.

## Run

One command, two windows (backend on 8010, dashboard on 5173):

```powershell
.\scripts\dev.ps1
```

Or by hand, from the repo root:

```powershell
python -m uvicorn backend.main:app --port 8010
npm --prefix app run dev
```

Ports 8000 (kit dashboard) and 5173 (UI) are reserved, so the API uses 8010.

## Real hardware mode vs recorded replay mode

| | Real hardware | Recorded replay |
|---|---|---|
| Start | `POST /api/band/connect` | `POST /api/band/replay/start` with a `session_id` |
| Needs | `VEXO_BAND` and the band | A recording under `recordings/` or `recordings/fixtures/` |
| IMU and event `source` | `band` | `replay` |
| `GET /api/band/status` | `connected: true`, `source: "band"` | `connected: false`, `source: "replay"` |
| Exposure | Live estimate | `contains_simulated: true`, with a reason saying it is not a live recording |
| `GET /api/shift` | `replay: false` | `replay: true` |

Replay is always labelled and is never presented as a live band. The two modes cannot mix:
connecting is refused while a replay runs (`409 replay-running`), and a replay is refused while
the band is connected or reconnecting (`409 band-connected`). A gap in a recording is replayed as
a gap. Disconnected time is a data gap, never exposure time.

The two committed fixtures, `synthetic_stationary` and `synthetic_tool_active`, are
script-generated (`source: "synthetic"` in their `meta.json`). They are not real band recordings.

The kit's pretend band is also reachable (`"transport": "sim"` on connect; it still requires
`VEXO_BAND`). Its data is published with `source: "band"`, so do not use it for a demo or for
recordings that anyone will read as real.

## Endpoints

Base URL `http://127.0.0.1:8010`. Interactive list: `/docs` (FastAPI).

### Core (`backend/main.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, dropped bus counters, current shift context |
| GET | `/api/events` | Stored events of a shift (`shift_id`, `limit`) |
| GET | `/api/shift` | The dashboard's `ShiftData`; values with no real source are `null` |
| WS | `/ws` | Live events as `{"kind": "event", "event": {...}}` |

### Band (`backend/routers/band.py`, prefix `/api/band`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/band/status` | Bridge, recording and replay status; `band_number_set` |
| POST | `/api/band/connect` | Connect the team's band. Body: `transport` (`ble`, `serial`, `sim`), `motion_hz`, `sensors` |
| POST | `/api/band/disconnect` | Disconnect |
| POST | `/api/band/record/start` | Start a labelled recording. Body: `label`, `tool_id`, `wearer`, `notes` |
| POST | `/api/band/record/stop` | Stop and write `meta.json` |
| GET | `/api/band/recordings` | List recordings and fixtures |
| POST | `/api/band/replay/start` | Replay a recording. Body: `session_id`, `speed` |
| POST | `/api/band/replay/stop` | Stop the replay |
| GET | `/api/band/imu/recent` | Recent IMU window for the chart (`seconds`, `max_points`) |

### Toolsense (`backend/routers/toolsense.py`, prefix `/api/toolsense`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/toolsense/state` | Current activity estimate and active model |
| GET | `/api/toolsense/sessions` | Tool sessions of a shift |
| POST | `/api/toolsense/sessions/{session_id}/confirm` | A person confirms or rejects. Body: `tool_id`, `confirmed` |
| POST | `/api/toolsense/train` | Train from recordings. Body: `include_synthetic` |
| GET | `/api/toolsense/report` | Last training report, or collection status |

### Inspection (`backend/routers/inspection.py`, prefix `/api/inspection`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/inspection/report` | Typed defect report. Body: `text`, `station_hint`, `shift_id`, `worker_id` |
| POST | `/api/inspection/listen` | Push-to-talk on the connected band. Body: `seconds`, `station_hint` |
| POST | `/api/inspection/{inspection_id}/clarify` | Answer a clarification. Body: `asset_id` or `text` |
| GET | `/api/inspection/inspections` | Inspections of a shift |
| GET | `/api/inspection/defects` | Defects of a shift |
| GET | `/api/inspection/work-orders` | Work orders (`shift_id`, `status`) |
| POST | `/api/inspection/work-orders/{work_order_id}/review` | Human review. Body: `action` (`submit`, `approve`, `reject`), `reviewer`, `note` |
| GET | `/api/inspection/assets` | Stations and asset register (`station_id`) |

### Exposure (`backend/routers/exposure.py`, prefix `/api/exposure`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/exposure/current` | Current estimated exposure for the shift |
| POST | `/api/exposure/manual` | Manually entered trigger time. Body: `tool_id`, `minutes`, `note` |
| GET | `/api/exposure/tools` | Tool profiles with their configured vibration values |
| PUT | `/api/exposure/tools/{tool_id}` | Create or change a profile. Body: `name`, `a_hv_ms2`, `source` |
| GET | `/api/exposure/tasks` | Planned tasks, workers, next task |
| POST | `/api/exposure/plan/evaluate` | Evaluate a planned task. Body: `task_id` |
| GET | `/api/exposure/plan/recommendations` | Recommendations (`shift_id`, `status`) |
| POST | `/api/exposure/plan/recommendations/{rec_id}/decision` | Human decision. Body: `decision` (`approved`, `dismissed`), `decided_by`, `revision`, `note` |

## Exposure method

UK HSE points method:

```
points = 2 x a_hv^2 x trigger hours        A(8) = 2.5 x sqrt(points / 100)
100 points = exposure action value         400 points = exposure limit value
```

`a_hv` comes only from a configured tool profile. The seeded profiles (`impact_wrench`,
`angle_grinder`, `low_vibration_wrench`) are labelled engineering examples to be replaced with
the manufacturer's declared or measured value; `needle_scaler` is seeded with no value.

## Model training and evaluation

The session detector uses a rule baseline until a trained model exists. No real recordings exist
yet, so no trained model exists and no accuracy is claimed.

1. Collect labelled recordings from the real band (labels: `stationary`, `walking`, `gesture`,
   `tool_idle`, `tool_active`). They are written to `recordings/<session_id>/` (`samples.csv`,
   `meta.json`) and are gitignored.

   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/api/band/record/start -ContentType 'application/json' -Body '{"label":"tool_active","tool_id":"impact_wrench"}'
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/api/band/record/stop
   ```

   Collection targets: 5 sessions each of `stationary`, `walking`, `gesture`, `tool_idle`, and
   10 of `tool_active`.

2. Train and evaluate:

   ```powershell
   python -m backend.train
   python -m backend.train --recordings DIR --models DIR --window 1.5 --overlap 0.5
   python -m backend.train --include-synthetic
   ```

   - Evaluation is on held-out sessions; a session's windows never sit on both sides of the split.
   - With fewer than 3 sessions for any class present, the output says
     `NOT A VALID ACCURACY ESTIMATE: too few sessions` and `accuracy_claim` is false.
   - `--include-synthetic` is for pipeline tests only. The model and report are flagged, no
     accuracy is claimed, and the live detector ignores a model trained on synthetic data.
   - Output: `models/toolsense_rf.joblib` and `models/toolsense_report.json` (gitignored).

3. The same from the API: `POST /api/toolsense/train` (reloads the live classifier) and
   `GET /api/toolsense/report`.

## Tests

```powershell
python -m pytest backend/tests -q
```

The tests use synthetic arrays, the synthetic fixtures, typed text and fakes. They never connect
to a band and never call Deepgram or OpenAI.

Frontend checks: `npm --prefix app run lint` and `npm --prefix app run build`.

## Honesty statements

- **Exposure is an estimate.** It is for screening and planning. It is not a certified
  measurement and must not be used as one. Every exposure result carries the label
  "Estimated HAV exposure — screening/planning, not certified measurement".
- **Wrist acceleration is never used as tool vibration magnitude.** The band's motion is activity
  evidence only ("probably using a tool"). The vibration magnitude comes only from a tool profile
  that a person configured.
- **AI never verifies a defect.** The AI extracts the worker's own words and the asset register
  decides the match. A defect stays `unverified` until a named human reviews the work order.
- **No automatic approval.** Work orders are created as drafts. Detected tool sessions are
  `tentative` until a person confirms them. Planning recommendations stay `pending` until a named
  person decides. Nobody is reassigned automatically.
- **Replay and synthetic data are always labelled** and never presented as live.
- **Missing inputs are stated, not filled in.** Disconnected periods are data gaps, never
  exposure time. A value with no real source is `null`.
