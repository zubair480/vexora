# BeltGuard capabilities: verified vs unverified

Status as of 2026-11-14, branch `ali-ui`.

**Verified** means: covered by an automated test in `backend/tests/` that runs on synthetic
arrays, the synthetic fixtures in `recordings/fixtures/`, replay, typed text, or a fake band.
It does not mean "proven on a real band" or "accurate in the field".

**Unverified** means: no automated test and no recorded real-world run covers it yet.

Last full run of `python -m pytest backend/tests -q` while writing this file: 336 passed,
1 skipped, 1 failed. The failure was
`test_band_bridge.py::test_start_uses_config_number_and_reads_limits_before_configure`; it
passed when run on its own straight afterwards, so it looks timing-dependent. Treat the suite as
"green except one flaky test" until that is fixed.

## Verified (automated tests, synthetic / replay / fake data)

| Capability | What the tests show | Test file |
|---|---|---|
| Band number comes only from `VEXO_BAND`, no default | Connect refuses without it, also for the pretend band; no band number literal in band code | `test_core.py`, `test_band_bridge.py`, `test_band_router.py` |
| Bridge logic against a fake band | Uses the configured number, refuses a wrong band, reads limits before configuring, clamps the rate, handlers only enqueue | `test_band_bridge.py` |
| Signal quality accounting | Missing samples from counter jumps, rate measured from sample time, counter restart is not a loss, silence becomes a gap and goes stale | `test_band_bridge.py` |
| Reconnect and disconnect handling (fake band) | A reconnect is a data gap; a disconnect gives stale status, not continuous data | `test_band_bridge.py` |
| LED queueing while the microphone is on (fake band) | LED commands are held and applied after audio stops | `test_band_bridge.py`, `test_speech_engine.py` |
| Band API endpoints | Status, connect errors, record round trip, recent IMU window, downsampling, NaN to null | `test_band_router.py` |
| Labelled recorder | `samples.csv` + `meta.json` format, gaps recorded, errors | `test_band_recorder.py` |
| Replay, always labelled | Everything published as `source="replay"`, spacing preserved, a recorded gap replays as a gap | `test_replay.py` |
| Live and replay never mix | Connect refused during replay; replay refused while connected or reconnecting; recent window never mixes sources | `test_band_router.py` |
| Event bus and event store | Event round trip, unknown type or source rejected, app starts | `test_core.py` |
| IMU feature extraction | Windowing and features on synthetic arrays | `test_imu_features.py` |
| Classifier code paths | Rule baseline and forest classifier behave as coded on synthetic signals; falls back to the rules when no usable model file exists | `test_tool_classifier.py` |
| Tool session detector | Hysteresis, minimum duration, data gaps never become session time, sessions start `tentative`, human confirm / reject | `test_tool_sessions.py`, `test_toolsense_router.py` |
| Training pipeline honesty | Held-out-session split, no accuracy claim with too few sessions or with synthetic data; a model trained on synthetic data is flagged and refused by default | `test_tool_train.py` |
| Asset register matching | Station and asset resolution, ambiguous and unknown references | `test_assets.py` |
| Defect report flow with the AI answer faked | Matched report gives an unverified defect and a draft work order; unmatched asks for clarification; AI failure records nothing | `test_inspection_agent.py` |
| Work-order review | Only a named human moves a draft on; invalid transitions refused; approve marks the defect verified by that person | `test_inspection_work_orders.py` |
| Speech wrapper with the kit helper faked | Typed path needs no band and no key; one capture at a time; clear errors | `test_speech_engine.py` |
| Exposure arithmetic and rules | HSE points and A(8); magnitude only from a tool profile; missing inputs give `needs_confirmation` / `unavailable`; gaps not counted; simulated data labelled | `test_exposure.py`, `test_exposure_api.py` |
| Planner (rule-based) | Projection, action / limit crossing, configured alternatives only, reassignment only to verified qualified workers, decision needs a named person and the current revision | `test_planner.py` |
| `GET /api/shift` shape | Empty shift is honest (nulls, no invented values); a report reaches the dashboard shape | `test_shift_view.py` |
| Whole flow on replay of a synthetic fixture | Typed report, replayed motion, candidate session, human confirmation, exposure from the configured tool value, shift view. Proves wiring, not accuracy | `test_pipeline_e2e.py` |

## Unverified as of now

| Capability | Why it is unverified |
|---|---|
| Connecting to a real band (Bluetooth or USB) | Never run. All band tests use a fake band object |
| Real motion rates: granted rate vs effective rate | Never measured on a real band |
| Real packet loss and data completeness | Never measured on a real band |
| Reconnect behaviour on a real link | Only simulated with a fake band |
| Band microphone capture | Never run on a real band |
| Band LED (blue lock during audio, queued updates applied afterwards) | Never run on a real band. No API endpoint drives the LED |
| Classifier accuracy on real recordings | No real recordings exist. No trained model exists. The detector runs on the rule baseline until one is trained, and the baseline's thresholds have not been tuned on real data |
| Detection of real tool use and of walking | Follows from the row above. Only synthetic signals have been used |
| Live Deepgram transcription | `DEEPGRAM_API_KEY` not yet configured. The kit's `speech.listen` is faked in tests |
| Live OpenAI extraction | `OPENAI_API_KEY` not yet configured. `ai.extract` is replaced by a fixed answer in tests; the one live test (`test_inspection_agent.py`, `live_ai_extract_on_the_example_sentence`) appears to be the test skipped in the run above. Without the key, `/api/inspection/report` and `/listen` return `503` and record nothing |
| The exact demo sentence end to end through speech and AI | Depends on the two rows above |
| Dashboard reading live backend data | The dashboard renders demo data from `app/src/data/shift.ts`. It does not fetch `GET /api/shift`, `/api/band/imu/recent` or `/ws` |
| Dashboard's own browser band connection | Separate path with its own constant (`app/src/band/config.ts`, currently `null`). Not exercised |
| WebSocket `/ws` delivery to a client | No automated test |
| Tool session linked to a station | Not implemented: a tool session has no station field. Station 3 is linked through the defect, the work order and the planned task only |
| Pretend-band (`transport: "sim"`) runs through the backend | Not exercised. Its data would be published with `source: "band"`, so it is not labelled as simulated |
| Seeded tool vibration values | Labelled engineering examples, not manufacturer or measured values |
| `scripts/dev.ps1` | Written, not run |

## In progress / experimental

| Capability | State |
|---|---|
| CP-SAT planning | In progress / experimental. `ortools` is in `backend/requirements.txt`, but no CP-SAT planner module exists in this branch (`planner_cpsat.py` is named as optional in `backend/CONTRACT.md`). The planner in use is the rule-based `backend/planner.py` |
| Acoustic screening | In progress / experimental. No code for it exists in this branch |

## Standing limits (by design, not gaps)

- Exposure is an estimate for screening and planning, not a certified measurement.
- Wrist acceleration is never used as tool vibration magnitude.
- AI never verifies a defect; a named human does.
- Nothing is approved automatically: work orders, tool sessions and recommendations all wait for
  a person.
