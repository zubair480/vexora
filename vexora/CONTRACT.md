# BeltGuard build contract — shared by every session

> **13:09 DESIGN CHANGE (user request, overrides any earlier brief): LIGHT MODE.** Every UI follows `vexora/DESIGN.md` — IBM Plex type, warm paper background, white panels, hairlines, one safety-orange accent. No dark/neon/bloom/scanlines/monospace body. The 3D view is a tasteful tabletop scale model, not a sci-fi hologram.
> **Demo tool decision:** a phone held in the palm on vibrate is the safe stand-in for the 1/2-in impact wrench (label it "DEMO stand-in"). S0 is verifying on the real band what the beta firmware actually supports — wait for `BAND_FACTS.md` before relying on 16 g ranges, 208/416/833 Hz or events.

Read this before coding. Re-read it, and `ops/heartbeat.log`, every few minutes. Only the orchestrator (S0) edits this file; ask in the heartbeat log if you need a change.

Root: `C:\Users\zubai\vexo hackathon\beltguard\` · Python 3.13 (fastapi, uvicorn, numpy, openai, websockets already installed) · Node 24.

## Band rules
- Band **7** (`Vexo-HACK-07`). Never any other number. Never call setBandNumber.
- The band holds ONE Bluetooth link. Only `backend/band.py` (S2) connects to it. Everyone else gets band data through the backend (WS / REST / `data/raw/*.jsonl`).
- The user's Chrome kit-dashboard tab may hold the link. Before the first real connect, S2 writes `NEED BAND` in the heartbeat; S0 gets the user to free it.
- Band 07 facts: gyro bias x +0.45, y -1.82, z +0.55 deg/s (subtract it). Real rates run fast (208→211, 416→423) — use `motion.t`, never sample counts. LED commands fail while the mic is on. Mic gain is at max (+20 dB) and may clip.

## Layout and owners (touch only your own files; commit nothing; switch no branch)
```
vexora/
  CONTRACT.md            S0 orchestrator
  README.md, .env.example, .gitignore, demo/   S0
  vexo-open-kit/         unchanged copy of github.com/VexoAI-Org/vexo-open-kit — never edit
  backend/
    app.py               S2  FastAPI on 127.0.0.1:8765; serves ../frontend at "/"; WS /ws; REST /api/*
    db.py                S2  SQLite data/beltguard.db, schema + seed
    events.py            S2  asyncio event queue + WS broadcaster
    band.py              S2  official vexo_open SDK bridge (band 7)
    sim.py               S2  SIMULATED band source (every message source:"sim")
    detector.py          S3  pure python + numpy, no I/O
    detector_config.json S3  thresholds tuned from real recordings
    exposure.py          S4  HAV maths
    inspection.py        S4  OpenAI extraction + deterministic asset validation
    planner.py           S4  exposure-aware recommendation
    tool_profiles.json   S4  tool vibration magnitudes + source
  frontend/              S8  index.html, app.js, style.css, charts.js, replay.js (S1 is RETIRED at 13:38 — S1 must not edit anything). Three.js via CDN import map, no build step
  frontend/supervisor.html, supervisor.js   S4  supervisor review page (same WS/REST, light mode per DESIGN.md)
  frontend/bandlink.js, bridge.html, bridge.js, voice.js   S0  Band link tab (Web Bluetooth, kit SDK in vendor/vexo) streams to /ws/ingest; always-on voice (kit deepgram.js in vendor/speech) -> /api/report; band events + alert command
  frontend/model3d.js    S7  3D scene (mongomation style) — keeps createModel(container,{onStationClick}) and every method app.js calls
> 13:36 DESIGN v2: DESIGN.md now = shadcn/ui zinc, Geist, white, decluttered (3D hero + 3 cards + tabs). No beige/brown anywhere.
  tools/                 S3  label/capture/analysis scripts (talk to backend REST, never to the band)
  data/raw/              S2 writes raw motion jsonl, S3 reads
  ops/heartbeat.log      everyone appends
```

## Python interfaces (exact names)
```python
# detector.py (S3)
@dataclass
class MotionSample:
    t: float            # band seconds, from motion.t
    n: int              # sample counter, from motion.n (gaps = lost samples)
    ax: float; ay: float; az: float   # g
    gx: float; gy: float; gz: float   # deg/s, bias-corrected

class ToolDetector:
    def __init__(self, rate_hz: float = 50.0, config: dict | None = None): ...
    def update(self, s: MotionSample) -> list[dict]: ...   # 0+ events, see below
    def snapshot(self) -> dict: ...  # {"state": str, "confidence": float, "features": dict}
# states: "idle" | "walking" | "gesture" | "candidate" | "tool_active" | "unresolved"
# events: {"kind":"activity","state":..,"confidence":..}
#         {"kind":"tool_start","t":..,"confidence":..}
#         {"kind":"tool_stop","t_start":..,"t_end":..,"trigger_s":..,"confidence":..,"status":"confirmed"|"unresolved"}
# The detector NEVER names a tool. Tool identity comes only from the user's selection.

# exposure.py (S4)
ACTION_POINTS = 100; LIMIT_POINTS = 400
def points(vibration_m_s2: float, trigger_hours: float) -> float   # 2 * a**2 * T
def daily_summary(records: list[dict]) -> dict   # {"total_points","status":"below_action"|"above_action"|"above_limit","minutes_to_action","minutes_to_limit"}
def minutes_for_points(vibration_m_s2: float, pts: float) -> float

# inspection.py (S4)
def extract_defect(transcript: str, stations: list[dict], conveyor: dict, worker: dict) -> dict
# -> {"status":"ok","station_id":"S3","component":..,"condition":..,"severity":"low|medium|high|critical",
#     "work_order":{"title","priority","priority_reason","steps":[..],"requires_isolation":True,
#                   "tool_id":..,"est_trigger_min":..},"source":"ai"|"rules"}
# or {"status":"clarify","question":"Which station? ..."}   # never invent a station

# planner.py (S4)
def recommend(current_points: float, planned: dict, tool_profiles: list[dict]) -> dict
# planned = {"station_id","task","tool_id","est_trigger_min"}
# -> {"projected_points","added_points","status":"ok"|"concern"|"over_limit","headline","actions":[..],
#     "requires_supervisor_review":True}
```

## WebSocket `/ws` (server → client). Every message has `type`, `ts` (unix s, laptop clock) and `source` ("band"|"user"|"ai"|"rules"|"system"|"sim")
```json
{"type":"motion","rate":50,"t0":12.34,"dt":0.02,"n0":1234,"lost_total":0,
 "accel":[[x,y,z],...],"gyro":[[x,y,z],...]}                      // ~10 batches/s; g and deg/s
{"type":"activity","state":"walking","confidence":0.82,"features":{"acc_rms":..,"gyro_rms":..,"hf_ratio":..}}   // ~4/s
{"type":"connection","state":"connected|connecting|disconnected|simulated|error","band":7,"rate":50,"detail":".."}
{"type":"transcript","text":"..","final":true}
{"type":"clarify","question":".."}
{"type":"event","event":{"id","ts","source","lane":"walking|inspection|tool|exposure|work_order|system",
                         "kind":"..","title":"..","detail":"..","station_id":"S3","refs":{}}}
{"type":"snapshot","state":{ ...see below... }}                  // on connect + after every state change
```
Snapshot `state`:
```json
{"worker":{"id":"W-01","name":".."},
 "conveyor":{"id":"CV-102","name":"Overland conveyor CV-102"},
 "stations":[{"id":"S1","number":1,"name":"Tail pulley & loading zone","pos":0.08,"status":"ok|inspected|defect|active"}, ...],
 "active_station":"S3",
 "tools":[{"id":"impact_wrench","name":"..","vibration_m_s2":..,"source":".."}],
 "active_tool":{...}|null,
 "defects":[{"id","ts","source","station_id","component","condition","severity","transcript"}],
 "work_orders":[{"id","ts","source","defect_id","station_id","title","priority","status":"draft|planned|in_progress|awaiting_verification",
                 "steps":[],"requires_isolation":true,"tool_id","est_trigger_min"}],
 "tool_sessions":[{"id","ts","source","t_start","t_end","trigger_s","tool_id","station_id","work_order_id","confidence",
                   "status":"active|confirmed|unresolved|rejected","points"}],
 "exposure":{"total_points":0,"action":100,"limit":400,"status":"below_action","records":[]},
 "recommendation":null,
 "timeline":[ /* last 200 events */ ],
 "connection":{"state":"connected","band":7,"rate":50,"lost_total":0},
 "mic":{"on":false},
 "detector":{"state":"idle","confidence":0.0}}
```

## REST (client → server), JSON
```
GET  /api/state                                  -> snapshot state
POST /api/station   {"station_id":"S3"}          select inspection station (source:"user")
POST /api/tool      {"tool_id":"impact_wrench"|null}
POST /api/report    {"text":".."}                typed fallback for a spoken defect report
POST /api/mic       {"on":true|false}
POST /api/tool_session/{id}/confirm {"confirmed":true,"trigger_s":12.5}
POST /api/repair/plan {"station_id","task","tool_id","est_trigger_min"}
POST /api/label     {"label":"walking|gesture|tool|idle|none"}   raw-recording label for S3
POST /api/rate      {"rate":50|208|416,"sensors":"both|accel"}   only rates motion_limits() allows
```

## Seed data
Conveyor CV-102. Stations: S1 Tail pulley & loading zone (pos 0.08) · S2 Carry idlers & belt tracking (0.30) · S3 Return idlers (0.52) · S4 Take-up & belt tension (0.74) · S5 Head/drive pulley & discharge (0.94). Worker W-01. DB tables: workers, conveyors, stations, inspection_events, defects, tool_profiles, tool_sessions, exposure_records, work_orders, shift_events — every row has `id`, `ts`, `source`.

## Honesty rules (judges will check)
- HAV numbers are "SCREENING ESTIMATE — not a certified HAV measurement". points = 2·a²·T with `a` from the selected tool profile (source recorded), `T` from detector trigger time. The wrist IMU never measures hand-transmitted vibration.
- AI never authorizes isolation, never closes a work order, never auto-reassigns people. Recommendations say "Supervisor review required".
- Anything simulated or illustrative is labelled SIMULATED / DEMO on screen and has source "sim".

## Heartbeat (the user asked for a 1-minute heartbeat)
Append one line to `ops/heartbeat.log` at least every minute while you work, and at every milestone or blocker:
`HH:MM:SS [S1 hologram-ui] did X · next Y · blocker Z`
Use bash: `echo "$(date +%H:%M:%S) [S2 band-backend] ..." >> "/c/Users/zubai/vexo hackathon/vexora/ops/heartbeat.log"`
