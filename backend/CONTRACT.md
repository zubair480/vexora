# BeltGuard backend contract

Read this first. It is what lets four sessions build in parallel and merge cleanly into `ali-ui`.

## Hard rules

1. **Never touch `app/` or `design/`.** Separate UI sessions own them. The UI reads one object,
   `ShiftData` (`app/src/data/types.ts`); the integration session maps backend state onto it.
2. **Edit only the files your session owns** (table below). The shared files (`main.py`,
   `models.py`, `event_bus.py`, `database.py`, `config.py`, `requirements.txt`, this file) are
   owned by the integration session. If you need a change there, do not edit: write the request
   in your final report. Add your own tables with `database.register_schema(...)` and your own
   dataclasses in your own modules.
3. **Band number.** It comes only from `config.band_number()` (env `VEXO_BAND`). Never write a
   band number literal anywhere, including tests, sim mode, docs and commands; never copy 42 or 7
   from the kit examples; never scan, loop over numbers, or connect to "whatever is found". If
   `VEXO_BAND` is unset, band code refuses to start. Read `reference/vexo-open-kit/AGENTS.md`
   in full before any band code. Only the `be-band` session may run code that connects to a
   real band. Everyone else tests on recordings / synthetic arrays / typed text.
4. **Use the Vexo SDK exactly as shipped** (next section). Do not modify
   `reference/vexo-open-kit/`. No custom Bluetooth stack, speech service or OpenAI client.
5. **Honesty.** Exposure is always "estimated — screening/planning, not certified measurement".
   Wrist acceleration is never used as tool vibration magnitude. AI never "verifies" a defect
   (`verification_status="unverified"` until a human reviews). Replay/synthetic data carries
   `source="replay"` / `"synthetic"` and is never presented as live. Disconnected periods create
   data gaps, never exposure time. No automatic safety approval or reassignment.
6. Keys only from env / gitignored `.env` (`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`). Never print them.
7. Callbacks from the band only enqueue. No heavy work, DB writes or AI calls inside callbacks.

## Vexo SDK alignment (checked against the kit in `reference/vexo-open-kit/`)

The source of truth is the kit: `AGENTS.md`, `docs/api.md`, `docs/speech.md`,
`docs/deepgram.md`, `examples/python/*.py`, `projects/step-diary/`. If anything below disagrees
with the kit, the kit wins: follow it and say so in your report.

`config.add_kit_to_path()` puts `python/` and `examples/python/` on `sys.path`. Then:

- `from vexo_open.sync import Band`
- `band = Band.connect(config.band_number(), transport, auto_reconnect=True)`; transport is
  `"ble"` (default), `"serial"` (USB) or `"sim"` (pretend band, still the team's number).
  `Band.connect(number)` itself rejects a wrong band (`wrong-band`).
- `band.info` / capabilities list (`accel gyro events steps mic led battery temperature storage
  tx-power usb`): check before asking for a stream.
- `limits = band.motion_limits()` -> `rates, accel, gyro, both, transport`.
  `streams = band.configure(motion=50, sensors="both", battery=True)`; `streams["motion"]` is the
  rate actually granted. Firmware 1.1 allows above 100 Hz; never assume configured == observed.
- `band.on("motion", handler)`: each reading `m` has `m.n` (sample counter, a jump = lost
  samples, restarts at 0 after a reconnect), `m.t` (band counter seconds), `m.time` (real time),
  accel in g and gyro in deg/s. Measure effective rate from `m.time` as in
  `examples/python/fast_motion.py`. Field names for the six axes: read `docs/api.md`, do not guess.
- Events: `battery`, `link`, `notice` (`low-battery`, `stream-dropped`...), `disconnect`
  (`reason`), `reconnecting`, `reconnected`. Pattern in `examples/python/reconnect.py`.
- Speech: `import speech`; `text = speech.listen(band, seconds, on_text=None)` (Deepgram, key
  `DEEPGRAM_API_KEY`). While the microphone streams the LED is locked blue and custom LED
  commands are refused: queue LED updates until audio is off.
- AI: `import ai`; `ai.extract(text, schema, instructions)`, `ai.choose(text, tools,
  instructions)`, `ai.ask(text, instructions)`; errors are `ai.AiError`. Key `OPENAI_API_KEY`,
  model from `ai.MODEL` / env `OPENAI_MODEL`.
- LED and storage: see `examples/python/led.py`, `storage.py`. There is no haptic motor.
- Handlers run on the library's thread and are dropped if they flood: enqueue and return.

## File ownership

| Session | Owns (all under `backend/`) |
|---|---|
| integration | `main.py models.py event_bus.py database.py config.py shift_view.py seed.py requirements.txt CONTRACT.md` |
| be-band | `vexo_bridge.py recorder.py replay.py routers/band.py tests/test_band*.py tests/test_replay*.py`, `recordings/` format |
| be-toolsense | `imu_features.py tool_classifier.py tool_sessions.py train.py routers/toolsense.py tests/test_tool*.py tests/test_imu*.py`, `models/` |
| be-voice | `speech_engine.py inspection_agent.py assets.py work_orders.py routers/inspection.py tests/test_inspection*.py tests/test_speech*.py tests/test_assets*.py` |
| be-hav | `exposure.py planner.py tool_profiles.py tasks.py routers/exposure.py tests/test_exposure*.py tests/test_planner*.py` (+ optional `planner_cpsat.py`) |

Each `routers/<name>.py` already exists as a stub exporting `router`; `main.py` includes it.

## Event model (`models.py`)

Everything that happens is an `Event` published with `event_bus.bus.publish(event)`; the bus
persists it to SQLite and fans it out to the WebSocket (`/ws`) and in-process subscribers
(`bus.subscribe(callback, types=None)`).

`Event(type, payload, source, shift_id, worker_id, station_id=None, asset_id=None, ts=now, id=uuid)`

`source` (provenance): `band` | `replay` | `synthetic` | `manual` | `ai` | `derived`.

| type | published by | payload (minimum) |
|---|---|---|
| `device_connected` / `device_disconnected` | be-band | `band_name, firmware, battery, transport, reason` |
| `sensor_quality` | be-band (about 1/s) | `configured_hz, effective_hz, missing_samples, completeness (0..1), saturated, stale` |
| `activity_changed` | be-toolsense | `activity (stationary/walking/gesture/tool_use/unknown), confidence, tool_probability` |
| `tool_session_started` / `tool_session_ended` | be-toolsense | `session_id, start_ts, end_ts, duration_s, tool_id (may be null), confidence, completeness, confirmation ("tentative"/"confirmed"/"rejected")` |
| `inspection_recorded` | be-voice | `inspection_id, transcript, conveyor_id, station_id, asset_id, condition, requested_action, verification_status, needs_clarification, clarification_question` |
| `defect_reported` | be-voice | `defect_id, inspection_id, asset_id, station_id, condition, severity, verification_status` |
| `work_order_created` | be-voice | `work_order_id, defect_id, asset_id, task, priority, status ("draft")` |
| `exposure_updated` | be-hav | `total_points, a8, level, entries[], status ("ok"/"needs_confirmation"/"unavailable")` |
| `planning_recommendation` | be-hav | `current_points, task_id, additional_points, projected_points, crosses_action, crosses_limit, reasons[], options[], requires_approval: true` |

High-rate IMU does **not** go through `Event`. be-band calls
`event_bus.bus.publish_imu(ImuBatch)`; consumers use `bus.subscribe_imu(callback)`.

`ImuSample`: `n`, `t`, `time`, `ax ay az` (g), `gx gy gz` (deg/s). `ImuBatch`: `samples,
configured_hz, effective_hz, source`.

## Recording format (shared by be-band recorder, replay, and be-toolsense training)

`recordings/<session_id>/samples.csv` with header `n,t,time,ax,ay,az,gx,gy,gz`, and
`recordings/<session_id>/meta.json`:
`{session_id, label, tool_id, band_name, wearer, configured_hz, effective_hz, missing_samples,
started_at, ended_at, source, notes}`. Labels: `stationary walking gesture tool_idle tool_active`.
Raw recordings are gitignored; `recordings/fixtures/` (small, real) may be committed.

## Cross-session calls (function signatures to honour)

- be-hav: `exposure.points(a_hv_ms2, trigger_seconds) -> float`,
  `exposure.a8(total_points) -> float`, `exposure.recompute(shift_id) -> dict` (publishes
  `exposure_updated`); subscribes to `tool_session_ended` and confirmations to recompute.
  `tool_profiles.get(tool_id) -> ToolProfile | None` with `a_hv_ms2, source, name`.
- be-toolsense: `tool_sessions.confirm(session_id, tool_id, confirmed: bool)` and
  `tool_sessions.list_sessions(shift_id) -> list[dict]`.
- be-voice: `assets.resolve(...) -> AssetMatch` and
  `inspection_agent.process_transcript(text, shift_id, worker_id, station_hint=None) -> dict`
  (must work with typed text and no band).
- Current shift context: `config.current_shift()` -> `{shift_id, worker_id, conveyor_id}`.

Until another session's module exists in your branch, call it through a guarded import and
degrade honestly (for example exposure "unavailable"), and test against a small fake.

## Running

`python -m pip install -r backend/requirements.txt`, then from the repo root:
`python -m uvicorn backend.main:app --port 8010` and `python -m pytest backend/tests -q`.
Ports 8000 (kit dashboard) and 5173 (UI) are taken.

## Done means

Your modules implemented, your router endpoints working, your pytest files passing
(`python -m pytest backend/tests -q` green), committed on your branch, and a final report
listing: what is verified, what is unverified, and any requested changes to shared files.
