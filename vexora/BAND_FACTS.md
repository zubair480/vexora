# Band 07 — verified on the real hardware (beta firmware), 2026-11-14 13:21–13:26

Tested by S0 through the kit JS API in Chrome over Bluetooth. Every line below was measured, not taken from the docs. Things the docs claim that were NOT tested are listed at the end.

## Identity and link
- number 7, protocol 1.1, firmware 1.1.0, hardware 1, features: fast-motion, intervals, clock.
- capabilities: accel, gyro, events, steps, mic, led, battery, temperature, storage, tx-power, usb.
- Radio: 2M PHY, 15 ms interval, MTU 247, TX -8 dBm, RSSI -36 dBm (band on its charger next to the laptop).
- Battery 100 %, 4137 mV, charging — **the band was on the charger: wear it for the demo.**

## Motion — what works over Bluetooth
| Asked | Measured on band clock (`t`) | Lost (via `n`) | Rest noise |
|---|---|---|---|
| 50 Hz both | 50 Hz (2 samples / 40 ms) | 0 | ~2 mg |
| 208 Hz both | 203–211 Hz | 0 | 1.3 mg |
| 416 Hz both | 407–423 Hz | 0 | 2.1 mg |
| 833 Hz accel only | 867 Hz | 0 | 2.5 mg |
| 208 Hz both **with audio on** | 211 Hz | 0 | — |
| 1666 Hz accel | **refused** over BLE (USB only) | — | — |
- `motion_limits()` = rates [10,25,50,100,208,416,833,1666], accel 833, gyro 833, both 416 (BLE).
- Real rates differ from the asked rate by a few %, and differently per run: **always use `t` (band ms clock) for time, never sample counts.**
- `n` increments by exactly 1 per sample within a steady stream, but **jumps/restarts when the rate or sensors change** — reset your gap counter after every reconfigure.
- Sample fields (JS): `t` (band ms), `n`, `time` (laptop epoch **ms** in JS; seconds in Python per SDK), `ax ay az` in g, `gx gy gz` in deg/s (`gx..gz` absent when sensors="accel").
- Ranges: accel 2/4/8/16 g and gyro 2000 dps both **apply** (verified `accelRange:16`). Default after connect: 2 g / 2000 dps. Use 16 g for tool vibration.
- Gyro bias at rest: x +0.37…+0.45, **y -1.6…-1.8**, z +0.40…+0.55 deg/s — subtract it.

## Mic
- 16 kHz mono Opus, 20 ms frames → **50 audio frames/s verified**, 24 kbps, gain +20 dB (max).
- Audio + 208 Hz both runs with 0 lost samples.

## LED — the lock is wider than the docs say
- LED commands fail while the mic is on **and also while only the mic-level meter (`mic_level`/`micLevel`) is on**: the band shows solid blue ("mic is on") and refuses.
- To use the LED: `configure(audio=False, mic_level=False)` first. Verified: solid orange (240,90,0) applied, `led_off` works.
- Valid patterns: off, solid, blink, breathe, flash, rainbow (`pulse` is rejected).

## Events
- Kinds reported: tap, double-tap, free-fall, tilt, orientation, motion; tap threshold 0.8 g. Stream turns on with `events=True`.

## Not yet verified on the band (don't promise these in the demo until tested)
- Whether a phone vibrating in the banded palm is visible at 416/833 Hz (next test, via S3's label runs).
- Tap/double-tap detection reliability while wearing, step counting accuracy, free-fall, auto-reconnect after a drop.

## Consequences for the build
- S2: configure 16 g / 2000 dps after connect; 50 Hz both by default; 416 Hz both (mic off) or 208 Hz both (mic on) for tool sessions; reset `n` gap tracking after each reconfigure; LED only with audio AND mic_level off.
- S3: tune at 416 Hz both (best tool evidence with gyro) and 208 Hz (with mic). 833 Hz accel-only is available for spectral peaks.
- S1: show "LED locked while mic on" honestly; rate badge should show the measured rate from `t`.
