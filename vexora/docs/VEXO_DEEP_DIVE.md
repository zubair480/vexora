# Vexora in depth: the problem, the solution, and every Vexo capability we use

Vexora runs on one Vexo Open band (team band 07) on the wrist of a maintenance technician's tool hand.
This page explains the problem, how the system solves it, and exactly which band capabilities carry
which part of the product. Everything listed under "used" is a real call in this repository; the file
that makes the call is named next to it.

## 1. The problem

Hand-arm vibration syndrome is permanent nerve and circulation damage caused by vibrating power tools.
It is preventable, and regulators manage it with a daily budget. The UK HSE points system is:

    points = 2 × a² × T        a = tool vibration magnitude (m/s²), T = trigger time (hours)
    100 points = exposure action value        400 points = exposure limit value

The tool's magnitude `a` is published. **The weak input is `T`: nobody measures trigger time.** It is
written down from memory at the end of a shift, and studies show workers overestimate it several times
over. So the number that decides whether someone keeps working is a guess, and the warning arrives after
the exposure has already happened.

Conveyor maintenance makes this worse. The technician's hands are gloved and busy, they are walking a
belt with no desk or tablet, and the defect they find, the tool they use and the paperwork that follows
live in three different places.

Existing products solve the timing problem by adding hardware: an RFID tag on every tool, a sensor
glove, or a dedicated wearable.

## 2. The solution

Vexora turns the band the technician already wears into the missing measurement and the whole interface:

1. **The tool is known, not guessed.** The technician checks a tool out, by voice or by click. From that
   moment the tool's published HSE magnitude is locked to them.
2. **The wrist times the trigger.** The band's accelerometer and gyroscope are sampled at 416 Hz. A
   detector decides, every quarter second, whether the tool is running, and measures the run on the
   band's own clock. Walking and gesturing do not count.
3. **Points update live.** `2 × a² × T` with the tool's published `a` and the measured `T`. The
   technician sees and hears how many minutes remain before the action value and the limit.
4. **Voice runs the job.** The band's microphone streams to Deepgram all shift. "Vexora, I'm at
   station three", "return idler seized", "I'm taking the hammer drill", "how long can I keep going?",
   "idler replaced" each change the job state. The laptop speaks the replies.
5. **The band answers back.** Its LED goes amber, orange and red as exposure builds, a double-tap
   confirms a session the detector was unsure about, and a free-fall raises a safety alert.
6. **The defect becomes a work order.** A spoken defect drafts a work order and a one-page PDF for the
   supervisor. In the control room, **Run work order** executes a checked workflow: draft, checks,
   schedule, create, calendar entry, verify (the `workorder/` module).
7. **The supervisor sees the crew.** A control room shows every worker on a 3D model of the conveyor,
   with tool, trigger time, points and status, and exports a shift compliance report.

The design rule throughout: **the band measures time and intent; published data supplies the vibration
magnitude; people make the decisions.** Wrist acceleration is never used as the tool's magnitude, and a
work order is only ever drafted or run by a person's action.

## 3. Every Vexo capability we use, and what it does in the product

| Band capability | How Vexora uses it | Where in the code |
|---|---|---|
| **Accelerometer + gyroscope, both sensors** | The core measurement: detects tool vibration and separates it from walking and hand movement | `vexora/backend/detector.py`, `vexora/frontend/bandlink.js` |
| **Fast motion (firmware 1.1), 416 Hz** | Tool vibration lives well above walking frequencies; 416 samples a second resolves it. Measured on band 07 at 407–423 Hz with zero lost samples | `band.configure({ motion: 416, sensors: 'both' })` in `bandlink.js`; `BAND_FACTS.md` |
| **`motion_limits()`** | Asks the link what it can carry before choosing a rate, instead of assuming | `vexora/backend/band.py` |
| **Rate fallback** | If 416 Hz is refused or starts losing samples, drops to 208 Hz (the kit's recommended rate with audio on) or 50 Hz, and says so | `bandlink.js`, `band.py` |
| **Configurable sensor ranges** | Sets the accelerometer to **16 g** and the gyroscope to 2000 dps. At the 2 g default a power tool clips | `band.setMotion({ accelRange: 16, gyroRange: 2000 })` |
| **Band clock (`t`)** | Trigger time is measured on the band's own millisecond clock, never by counting samples, because real rates drift a few percent | `detector.py` |
| **Sample counter (`n`)** | Counts samples lost on the way, so a gap is recorded as a gap and never as tool time | `band.py`, `detector.py` |
| **Microphone (16 kHz Opus stream)** | Always-on voice for the whole shift: commands, defect reports, job steps, questions | `band.configure({ audio: true })` and `band.on('audio')` in `voice.js`; `band.py` |
| **Kit speech link to Deepgram** | The kit's own Deepgram code turns the band's Opus packets into live text | `vexora/frontend/vendor/speech/` |
| **Gesture: double-tap** | A hands-free "yes": confirms a session the detector marked unsure. Works with gloves on | `band.on("double-tap")` in `band.py` |
| **Gesture: free-fall** | Raises "Possible drop or fall: check on W-01" on screen, out loud and in the control room | `band.on("free-fall")` in `band.py` |
| **Multicolour LED: colour, pattern, period, brightness** | The exposure signal on the body: staged amber, orange, red; red blink at 250 ms when a threshold is crossed | `band.set_led(colour, brightness, pattern, period)`, `band.ledOff()` |
| **LED and microphone interlock** | The band locks its LED blue while the mic streams. Vexora pauses audio, flashes the LED, turns it off and resumes listening, so the alert is never lost | `bandlink.js` lines around `setLed`, `band.py` |
| **Battery events** | Battery shown per worker in the control room; low battery surfaces as a notice | `band.on("battery")` |
| **Link events (signal strength)** | Link quality feeds the connection status | `band.on("link")` |
| **Notices** (low battery, stream dropped) | Shown to the technician instead of failing silently | `band.on("notice")` |
| **`stream-taken`** | Detects another page taking a stream and reports it | `band.on("stream-taken")` |
| **Auto-reconnect** | `disconnect`, `reconnecting`, `reconnected` are handled; after a reconnect the ranges and the 416 Hz stream are configured again and the mic is restored | `Band.connect(BAND, { autoReconnect: true })` |
| **One-link ownership** | A dedicated Band link tab owns the single Bluetooth link, so dashboards can reload freely. A second Band link page refuses to connect while one holds the band | `bridge.html`, `bandlink.js` |
| **Three transports** | Web Bluetooth in Chrome, Bluetooth from Python, and USB serial from Python, all through the official SDK | `Band.connect(BAND, transport="serial", auto_reconnect=True)` in `band.py` |
| **Wrong-band protection** | Connects only to the team's own band number, held in one place; never scans for others | `Band.connect(BAND, …)` |
| **Pretend band (sim transport)** | Developer-only test server, always labelled SIMULATED; the normal server refuses to switch to it | `SimTransport` in `band.py` |

Not used: on-band storage and the step counter. The laptop is the source of truth, and steps are not
part of the exposure question.

## 4. What is creative about the use of the band

- **One sensor answers a regulatory question.** The band is not used as a fitness tracker or a remote.
  Its motion stream replaces the one input the HSE formula has always had to guess.
- **The band is input, output and safety device at once.** Motion measures, the microphone commands,
  the double-tap confirms, the LED warns, the free-fall event protects.
- **The mic and LED conflict is turned into a feature.** Because the LED is locked while the mic is on,
  the alert becomes a deliberate pause-flash-resume, which also makes the moment noticeable.
- **Timing is done on the band's clock.** That is what makes trigger time defensible when the radio
  rate drifts or a packet is lost.
- **No tags, no gloves, no extra hardware.** The checkout supplies the tool identity that other products
  buy with RFID.

## 5. Technical architecture

    Vexo band 07 (tool-hand wrist)
      accel + gyro 416 Hz (16 g / 2000 dps) · mic 16 kHz Opus · double-tap, free-fall · LED
            │ Web Bluetooth (Band link tab)              │ USB or Bluetooth (Python SDK)
            ▼                                            ▼
      frontend/bandlink.js + voice.js              backend/band.py
      band mic → Deepgram (kit speech link)              │
            └───────── motion, events, every sentence ───┘
                                  ▼
                 backend/app.py  FastAPI + asyncio event hub + SQLite
      detector.py   exposure.py   intents.py   inspection.py   planner.py   fleet.py
      tool running  2·a²·T,       wake word,   defect + work   exposure-    W-01 real,
      start / stop  100 / 400     commands     order + PDF     aware plan   W-02..05 simulated
                                  │
                                  ├── /workorder  (workorder/: draft → checks → schedule →
                                  │                create → calendar → verify, each step re-checked)
                                  ▼
          Technician dashboard · Control room (Three.js site) · Planning

- **Detector:** every 0.25 s it scores the last second of motion: vibration energy above walking
  frequencies, its share of total motion, and rhythm. Start and stop use hysteresis and a minimum
  duration; unsure sessions are marked unresolved and the technician is asked.
- **Exposure:** HSE points, A(8), minutes remaining to 100 and 400; tool magnitudes come from HSE's
  published table, each with its source recorded.
- **Voice intents:** deterministic code, no API key needed; it asks instead of guessing a station or tool.
  A clear defect needs no wake word; everything else said is stored and marked ignored.
- **Work-order flow:** a small state machine in the style of an approvals engine. A named person must
  approve; the window is checked; the created record is read back and verified; a blocked step shows
  the real reason.
- **Storage:** SQLite; every row carries an id, a timestamp and a source (band, voice, simulated, manual).

## 6. Honest limits

- Exposure is a **screening estimate, not a certified HAV measurement**. The band times trigger use; it
  does not measure vibration at the hand-tool interface.
- In the live demo a phone on vibrate stands in for a power tool, and the staged alert times are
  compressed demo thresholds, labelled as such on screen.
- Workers W-02 to W-05 in the control room are simulated and always labelled simulated.
- The calendar step of the work-order flow prepares a calendar entry for a person to add; it does not
  write to a calendar account by itself.
- Vexora drafts and recommends. People confirm tool sessions, approve work orders and verify isolation.
