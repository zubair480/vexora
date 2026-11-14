# BeltGuard demo script

A repeatable script for the 13-step live acceptance flow, then a rehearsal that needs no band.

Read this first:

- The dashboard (`http://localhost:5173`) currently renders demo data from
  `app/src/data/shift.ts`. It does not read the backend yet. So every step below is driven and
  checked through the API. Where a UI action is named, it is what the dashboard is meant to do
  once it is wired to `GET /api/shift`; it is not available today.
- Nothing in the live flow has been run against a real band yet. See
  [CAPABILITIES.md](CAPABILITIES.md).
- The band number is never typed in any command. It comes only from
  `VEXO_BAND=<your band number>` in `.env`.

## Before you start

1. `.env` holds `VEXO_BAND=<your band number>`, `DEEPGRAM_API_KEY` and `OPENAI_API_KEY`
   (README, ".env setup").
2. Nothing else is connected to the band: close the kit dashboard and any browser tab that uses
   it. Leave the dashboard's own Connect buttons alone.
3. Start from a clean database so earlier runs do not show up. Stop the backend, then:

   ```powershell
   Remove-Item beltguard.db* -ErrorAction SilentlyContinue
   ```

4. Start both servers: `.\scripts\dev.ps1`
5. In a third PowerShell window, define two helpers (used by every step):

   ```powershell
   $api = 'http://127.0.0.1:8010'
   function Get-Api($path) { Invoke-RestMethod -Uri "$api$path" }
   function Post-Api($path, $body = @{}) {
       Invoke-RestMethod -Method Post -Uri "$api$path" -ContentType 'application/json' -Body ($body | ConvertTo-Json)
   }
   Get-Api '/api/health'      # ok = True
   ```

6. Tell the people near the band that its microphone will be on (solid blue light) and that the
   sound is sent to Deepgram.
7. Safety: operate the tool only if you are trained to, with the guard and PPE in place, on a
   secured workpiece, for a few seconds. The demo needs about 5 to 10 seconds of trigger time.

## Live flow (13 steps)

### 1. Connect the band

```powershell
Post-Api '/api/band/connect'                              # Bluetooth
Post-Api '/api/band/connect' @{ transport = 'serial' }    # or: USB cable
```

Expect `connected: True`, `source: band`, your band's name in `band_name`, and `configured_hz`
(the rate the band granted). If it fails, the error code says why:
`band-number-missing` (set `VEXO_BAND`), `wrong-band`, or the SDK's own code. If the band is not
found, double-tap it and try again. Never try another number.

### 2. Live traces

```powershell
Get-Api '/api/band/status'                                # effective_hz, missing_samples, completeness, stale
(Get-Api '/api/band/imu/recent?seconds=5&max_points=300').samples | Select-Object -Last 5
```

Expect `stale: False`, `source: band`, and samples with `ax ay az` in g and `gx gy gz` in deg/s
that change when the wrist moves. Live events (`sensor_quality` about once a second) arrive on
the WebSocket `ws://127.0.0.1:8010/ws`.

### 3. Walk the route

Walk along the conveyor for 10 to 15 seconds, then:

```powershell
Get-Api '/api/toolsense/state'
```

Expect `activity` to move from `stationary` to `walking` (an estimate from the rule baseline,
flagged `estimated: True`). `model` names the classifier in use.

### 4. Select Station 3

```powershell
Get-Api '/api/inspection/assets?station_id=S3'
```

Expect Station `S3` ("Return Idlers") and its assets, including `RETURN-IDLER-14`. The selection
is carried into the next step as `station_hint = 'S3'`. UI action (once wired): click Station 3
on the conveyor route map.

### 5. Speak the defect

Hold the band near your mouth, run the call, and say clearly:

> Station 3, return idler 14, bearing seized. Needs replacement.

```powershell
$r = Post-Api '/api/inspection/listen' @{ seconds = 8; station_hint = 'S3' }
$r.inspection.transcript
```

The band's light is solid blue while it listens. Errors are explicit: `409 no-band` (step 1 not
done), `422 nothing-heard`, `503` (Deepgram or OpenAI did not run; nothing is recorded).

### 6. Structured defect and work-order draft

```powershell
$r.status              # recorded
$r.defect              # asset_id RETURN-IDLER-14, station_id S3, condition "reported bearing seized", verification_status unverified
$r.work_order          # status draft
```

Expect a defect that is `unverified` and a work order that is a `draft`. If `status` is
`needs_clarification`, answer the question:

```powershell
Post-Api "/api/inspection/$($r.inspection.inspection_id)/clarify" @{ asset_id = 'RETURN-IDLER-14' }
```

### 7. Select a known tool

```powershell
(Get-Api '/api/exposure/tools').tools | Format-Table tool_id, name, a_hv_ms2, source
$tool = 'impact_wrench'
```

Pick a tool that has a configured `a_hv_ms2`. The seeded values are labelled engineering
examples. To use the real tool's declared value instead:

```powershell
Invoke-RestMethod -Method Put -Uri "$api/api/exposure/tools/impact_wrench" -ContentType 'application/json' `
  -Body '{"name":"Impact wrench","a_hv_ms2":8.0,"source":"Manufacturer declared value, <document reference>"}'
```

### 8. Operate the tool safely

Run the tool for 5 to 10 seconds with the band on the wrist of the hand that holds it, then stop
and keep the hand still for a few seconds. A candidate needs at least 3 seconds of tool-like
motion to become a session.

### 9. Candidate tool session detected and confirmed

```powershell
$s = Get-Api '/api/toolsense/sessions' | Where-Object { $_.end_ts } | Select-Object -Last 1
$s | Format-List session_id, duration_s, confidence, completeness, confirmation, source
```

Expect `confirmation: tentative`, `source: band`, no `tool_id`. Before confirmation,
`Get-Api '/api/exposure/current'` gives no points for it. Now a person confirms which tool it was:

```powershell
Post-Api "/api/toolsense/sessions/$($s.session_id)/confirm" @{ tool_id = $tool; confirmed = $true }
```

If no session appears, the rule baseline did not recognise the motion as tool use (it has not
been tuned on real recordings). Say so, and continue with a manual entry, which is labelled as
manual:

```powershell
Post-Api '/api/exposure/manual' @{ tool_id = $tool; minutes = 0.2; note = 'demo: detector missed the session' }
```

### 10. Linked to Station 3

```powershell
$shift = Get-Api '/api/shift'
$shift.conveyor.stations | Where-Object id -eq 'S3'     # status defect, asset Return Idler 14
$shift.workOrder                                         # CV-102 - S3 / Return Idler 14
$shift.tool                                              # the confirmed tool, minutesToday
(Get-Api '/api/exposure/tasks').tasks | Select-Object -First 1   # task-idler-s3: Replace seized idler at Station 3, impact_wrench
```

Be exact about what this shows: the defect, the work order and the planned task are tied to
Station 3, and the confirmed tool session belongs to the same shift and worker. A tool session
record has no station field of its own; there is no stored session-to-station link.

### 11. Exposure updates from the configured tool value

```powershell
$e = Get-Api '/api/exposure/current'
$e | Format-List label, total_points, a8, level, status
$e.entries | Format-Table tool_name, a_hv_ms2, a_hv_source, trigger_s, points, duration_basis, source
```

Expect one entry whose points equal `2 x a_hv^2 x trigger_s / 3600`, with `a_hv_ms2` and
`a_hv_source` taken from the tool profile. For a few seconds of use this is a fraction of a
point; that is correct. An `exposure_updated` event appears on `/ws`.

### 12. Next planned job evaluated, with a recommendation

Confirming the session already re-evaluated the next task. To evaluate it explicitly:

```powershell
$next = (Get-Api '/api/exposure/tasks').next_task_id      # task-idler-s3
$rec = Post-Api '/api/exposure/plan/evaluate' @{ task_id = $next }
$rec | Format-List task_title, tool_name, current_points, additional_points, projected_points, crosses_action, status
$rec.reasons
```

Expect `additional_points` of about 53.3 for the seeded task (impact wrench at 8 m/s2 for
25 minutes), `status: pending`, `requires_approval: True`.

To show a recommendation with options, add earlier work on the same tool as a manual entry and
evaluate again. This entry is labelled manual and is demo input, not measured use:

```powershell
Post-Api '/api/exposure/manual' @{ tool_id = 'impact_wrench'; minutes = 25; note = 'demo: earlier job' }
$rec = Post-Api '/api/exposure/plan/evaluate' @{ task_id = $next }
$rec.crosses_action       # True: projected is now above 100 points
$rec.options | Format-Table kind, description -Wrap
$rec.blocked_options | Format-Table worker_name, reason -Wrap
```

Expect review options, the configured alternative tool (low-vibration impact wrench) with its
own projection, a reassignment option for a verified qualified worker, and a blocked option for
the worker whose qualifications are unknown. Nothing is approved. A named person decides:

```powershell
Post-Api "/api/exposure/plan/recommendations/$($rec.recommendation_id)/decision" `
  @{ decision = 'approved'; decided_by = '<supervisor name>'; revision = $rec.revision; note = 'demo' }
```

### 13. Full shift view

```powershell
$shift = Get-Api '/api/shift'
$shift.replay                    # False on a live run
$shift.band | Format-List connected, live, name, motionRate, effectiveRate, completeness
$shift.exposure | Format-List label, current, projected, action, limit, status
$shift.defect; $shift.workOrder; $shift.tool; $shift.recommendation
$shift.events | Format-Table start, kind, type, source, detail -Wrap
Get-Api '/api/events' | Measure-Object
```

Expect the defect (`verificationStatus: unverified`), the draft work order, the tool, the
estimated exposure, the recommendation and a timeline with `inspection`, `defect`, `workorder`,
`tool` and `exposure` entries. Fields with no real source (belt health, shift times) are `null`.
UI action (once wired): the dashboard shows this object.

Optional close-out, the human review of the work order (approve marks the defect verified by
that person):

```powershell
$wo = $shift.workOrder.id
Post-Api "/api/inspection/work-orders/$wo/review" @{ action = 'submit';  reviewer = '<reviewer name>' }
Post-Api "/api/inspection/work-orders/$wo/review" @{ action = 'approve'; reviewer = '<reviewer name>' }
Post-Api '/api/band/disconnect'
```

## No-band rehearsal (replay and typed report)

No band, no `VEXO_BAND`, no Deepgram key. It still needs `OPENAI_API_KEY`, because the typed
report uses the same AI field extraction; without the key the report call returns
`503 ai_unavailable` and records nothing. Everything below is labelled replay/synthetic and must
be presented that way.

Start clean and define the helpers as in "Before you start" (items 3 to 5).

```powershell
# Steps 1-3 stand-in: no band. Status says so.
Get-Api '/api/band/status'                         # connected False, band_number_set as configured

# Steps 4-6: Station 3 and the typed defect report
Get-Api '/api/inspection/assets?station_id=S3'
$r = Post-Api '/api/inspection/report' @{
    text = 'Station 3, return idler 14, bearing seized. Needs replacement.'; station_hint = 'S3' }
$r.status; $r.defect; $r.work_order                # recorded, unverified defect, draft work order

# Steps 7-8: pick a tool, then replay the synthetic tool-use fixture instead of operating a tool
$tool = 'impact_wrench'
(Get-Api '/api/band/recordings').recordings | Format-Table session_id, label, source, fixture
Post-Api '/api/band/replay/start' @{ session_id = 'synthetic_tool_active'; speed = 1 }
Get-Api '/api/band/status'                         # connected False, source replay
(Get-Api '/api/band/imu/recent').source            # replay
Start-Sleep -Seconds 10                            # the fixture is 6 s; wait for it to finish
Post-Api '/api/band/replay/stop'

# Step 9: the candidate session, then the human confirmation
$s = Get-Api '/api/toolsense/sessions' | Where-Object { $_.end_ts } | Select-Object -Last 1
$s | Format-List session_id, duration_s, confirmation, source     # tentative, replay
Post-Api "/api/toolsense/sessions/$($s.session_id)/confirm" @{ tool_id = $tool; confirmed = $true }

# Steps 10-13: same calls as the live flow
$e = Get-Api '/api/exposure/current'
$e.contains_simulated; $e.reasons                  # True, "Includes simulated (replay/synthetic) data: not a live recording."
$next = (Get-Api '/api/exposure/tasks').next_task_id
Post-Api '/api/exposure/plan/evaluate' @{ task_id = $next }
(Get-Api '/api/shift').replay                      # True
```

What the rehearsal proves: the wiring from motion to session, confirmation, exposure,
recommendation, defect, work order and shift view. It proves nothing about a real band or about
detection accuracy. The automated version of this rehearsal is
`backend/tests/test_pipeline_e2e.py` (with the AI extraction replaced by a fixed answer).

## If something goes wrong

| Symptom | Meaning | Do |
|---|---|---|
| `409 band-number-missing` | `VEXO_BAND` is not set | Set it in `.env`, restart the backend |
| Connect fails, band not found | Another connection holds the band, or the connect window closed | Close other connections, double-tap the band, retry. Never try another number |
| `409 replay-running` / `409 band-connected` | Live and replay must not mix | Stop the replay, or disconnect the band |
| `503` on `/api/inspection/report` or `/listen` | A key is missing or the service did not answer | Check `.env`; nothing was recorded |
| `status: needs_clarification` | The asset was not matched in the register | Call the clarify endpoint with an `asset_id` |
| No tool session after step 8 | The rule baseline did not see tool use | Say so; use the labelled manual entry |
| `422` on a decision | The recommendation changed since it was read | Evaluate again and quote the new `revision` |
