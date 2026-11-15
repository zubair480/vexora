# Vexora: the algorithms, why we chose them and what is behind them

This document lists every algorithm in Vexora, how each one works, why we built it that way, and what evidence stands behind it. It is written for a reviewer who wants to check our claims against the code, so each section names the file to open.

Two things to know first:

- **The research we cite supports the problem and the exposure formula. It does not support the detector.** The exposure maths is the UK regulator's published method, with published tool magnitudes. The tool-use detector is our own design, built in one day and tuned against recordings from our own band. No paper is behind it, and we say so below.
- **Nothing here is a certified measurement.** Vexora is a screening estimate. The band times the tool; it never measures vibration at the hand.

## Contents

1. [The pipeline in one picture](#1-the-pipeline-in-one-picture)
2. [The design rule everything follows](#2-the-design-rule-everything-follows)
3. [Tool-use detector (wrist motion to trigger time)](#3-tool-use-detector)
4. [How the detector was tuned](#4-how-the-detector-was-tuned)
5. [Exposure points (the regulator's formula)](#5-exposure-points)
6. [Exposure-aware planner](#6-exposure-aware-planner)
7. [Voice: wake word and intent parser](#7-voice-wake-word-and-intent-parser)
8. [Defect extraction and station validation](#8-defect-extraction-and-station-validation)
9. [Job agent: spoken steps and the isolation warning](#9-job-agent)
10. [Staged alert and band events](#10-staged-alert-and-band-events)
11. [Work-order flow](#11-work-order-flow)
12. [Research and standards we rely on](#12-research-and-standards-we-rely-on)
13. [What is tested, and what is not](#13-what-is-tested-and-what-is-not)
14. [Work on the `ali-ui` branch that is not in main](#14-work-on-the-ali-ui-branch-that-is-not-in-main)

## 1. The pipeline in one picture

```
Vexo band 07  (accelerometer + gyroscope at 416 Hz, microphone, tap and free-fall events, LED)
   │
   ├─ motion ──► detector.py ──► tool session (start, end, confidence)
   │                                   │
   │             tool checkout ───────►│  the tool comes from the checkout, never from motion
   │                                   ▼
   │                             exposure.py ──► points = 2 · a² · T ──► verdict at 100 / 400
   │                                   │
   │                                   ▼
   │                             planner.py ──► options for a supervisor
   │
   └─ speech ──► Deepgram ──► intents.py ──► command or question
                          ├─► inspection.py ──► defect + work-order draft
                          └─► job_agent.py ──► job steps, isolation warning
                                                   │
                                                   ▼
                                          workorder/flow.py ──► draft → checks → schedule → create → calendar → verify
```

## 2. The design rule everything follows

**The band measures time and intent. Published data supplies magnitude.**

Daily hand-arm vibration exposure depends on two inputs: how hard the tool vibrates (*a*, in m/s²) and how long the trigger is held (*T*). A wrist band cannot measure *a*: the standard measurement (ISO 5349) is taken at the hand–tool interface, and the wrist is not that. So we never turn wrist acceleration into a vibration value. We take *a* from the regulator's published table for the checked-out tool, and we use the band only for the input that paper records get wrong, which is *T*.

That choice is what the research supports. When researchers watched workers, self-reported tool time was a median 2.5 times the observed time (Palmer et al. 2000), and questionnaire estimates were a median 9 times (McCallig et al. 2010). The weak input is time, so time is what we measure.

## 3. Tool-use detector

**File:** `vexora/backend/detector.py` (numpy only, no I/O). **Live thresholds:** `vexora/backend/detector_config.json`.

**Job:** decide, from wrist motion alone, when a power tool is running, and time that on the band's own clock. It never names the tool.

**What kind of algorithm it is:** a hand-built scoring model followed by a state machine with hysteresis. It is *not* a trained classifier. Each feature is passed through a soft threshold (a logistic curve), the results are combined into one "tool evidence" score between 0 and 1, and a state machine turns that score into sessions.

### Why this and not a trained model

- **We had no labelled tool data.** A learned model needs labelled recordings of real tools on a real wrist. We had one day and one band.
- **Every decision must be explainable.** A supervisor, or a regulator, can be shown which feature crossed which threshold. A forest of trees cannot be read that way.
- **It runs anywhere.** It costs under 400 microseconds per sample in our tests, against a budget of about 2,400 at 416 samples a second.

The first version was simpler still: a one-second window with `acceleration RMS > 0.25 g` and `gyro RMS < 60 °/s`, two seconds to start and one second to stop (`StubDetector` in `band.py`, kept as the fallback if `detector.py` fails to load). It could not tell a vibrating tool from someone handling the band, which is why the frequency features below were added.

### Step 1: trust the band's clock, not the sample count

The band's real sample rate drifts. Asked for 416 samples a second, it delivered between 407 and 423, and differently on each run (`BAND_FACTS.md`). So:

- All durations are differences of band timestamps. The sample rate is measured in every window as `(n − 1) / duration` and never assumed.
- The time unit (seconds or milliseconds) is detected from the median gap between samples.
- Lost samples are counted from the band's sample counter, but only when the clock moved far enough to have produced them, so a counter restart is not mistaken for data loss.
- A gap of more than 0.5 s restarts the window. A gap of more than 2 s during a session ends it at the last good reading. A clock that jumps backwards by more than a second ends the session.

### Step 2: features over the newest one-second window, every quarter second

| Feature | How it is computed | What it tells us |
|---|---|---|
| High-frequency RMS | Hann window, FFT per axis, power summed over three axes, taken from 8 Hz up to 95% of the Nyquist limit | How much fast vibration reaches the wrist |
| High-frequency ratio | High-frequency power divided by all power above 0.5 Hz | Whether the motion is *mostly* vibration, or mostly arm movement |
| Rotation | Gyroscope low-passed below 3 Hz | A steady grip rotates little; handling and gesturing rotate a lot |
| Periodicity | Autocorrelation peak at walking cadences (0.7–1.3 Hz and 1.4–2.6 Hz) over three seconds, on acceleration and gyroscope; the two best must agree | Walking is periodic; a tool is not |
| Crest factor | Peak divided by RMS of the high-passed signal | Taps and claps are spiky; tool vibration is not |
| Zero-crossing rate | Sign changes on the strongest axis | Fast oscillation against slow movement |

Gravity and slow posture are removed first by subtracting each axis's mean.

### Step 3: one evidence score

```
tool = hf × weighted geometric mean( ratio 0.35, steady 0.20, non-periodic 0.20, crest 0.10, zero-crossing 0.15 )
```

`hf` multiplies the whole score, so with no high-frequency vibration there is no tool, whatever else is true. A geometric mean is used so that one strongly contradicting feature pulls the score down; an arithmetic mean would let the others outvote it. Parallel scores for idle, walking and gesture are computed the same way and are only used for the label shown when no tool is running.

Key thresholds (50% evidence points): high-frequency RMS 0.03 g; rotation 30 °/s; crest factor 6; zero-crossing 6 Hz. The high-frequency ratio threshold is set per sample rate (see section 4).

### Step 4: sessions, with hysteresis

Three thresholds on the evidence score, so the state does not flicker: **on 0.55, hold 0.40, off 0.35**.

- **Candidate:** opens when the score reaches 0.55. The start time is then walked *backwards* along the vibration envelope to where the buzz really began, so the start is not late by the window length.
- **Promoted to a session** only after 2 seconds of activity, with the median score over the vibrating windows at least 0.45. A spike alone never starts a session.
- **Dropped silently** if it goes quiet for 1.5 s before promotion. A 1.2 s burst is not a tool.
- **Ends** after 1.5 s of quiet with the score below 0.35. Pauses shorter than that merge into one session, so a pulsed tool is one session and not seven.

### Step 5: confidence

Confidence is the mean score over the vibrating windows, reduced for windows below the "on" threshold, for lost samples, for a forced end, for a session under 3 seconds and for a missing gyroscope. At 0.75 or above the session is *confirmed*; below, it is *unresolved*.

### What we can and cannot claim

- **Passes:** 21 of 21 synthetic tests, with signals generated at real sample times so motor frequencies alias as they would on the band. They cover idle, walking, gestures, a 12-second tool run timed to within 0.75 s, a burst that must not count, pauses that merge or split, weak buzz, buzz with hard wrist rotation, lost samples, counter restarts and three sample rates.
- **Real band:** every recording made on band 07 on the day, which included handling, double-taps, shaking and talking with the hands, produced zero false tool sessions.
- **Not shown:** we never captured a labelled recording of a real power tool, or of the phone-on-vibrate stand-in, so there are **no sensitivity or specificity figures on real tools**. The thresholds are provisional.
- **Two known gaps between the write-up and the running code:**
  - Exposure uses the session's start-to-end time, which includes merged pauses under 1.5 s. The detector also computes a pauses-excluded figure, but nothing consumes it yet.
  - The "ask the technician to confirm an unsure session" step is switched off in the demo build: an unsure session is auto-confirmed when a tool is checked out.
- **At 208 Hz** (the rate used while the microphone is streaming) the detector falls back to its default thresholds, because thresholds tuned at one rate do not transfer to another.

## 4. How the detector was tuned

**Files:** `vexora/tools/` and the `_meta` block in `detector_config.json`.

This is the honest account of how we arrived at the numbers.

1. **Started with defaults** chosen from first principles (ratio threshold 0.5).
2. **Recorded our own band** around 13:57–14:59 on the day: wearing it, handling it, talking with our hands, double-tapping and picking it up.
3. **Found the one real false-positive class: handling the band.** At 416 samples a second, handling ran a high-frequency ratio of 0.85–0.88 with a steady grip. A buzz held still is 0.97 or more.
4. **Moved the ratio threshold between the two:** 0.93 at 416 Hz (and 0.75 at 50 Hz, where handling ran 0.48–0.80). The result on the real recordings was zero false tool sessions, with every synthetic positive still detected.
5. **Marked the result provisional.** The thresholds were set by hand, at 15:20, from negatives only. The positives were synthetic.

We also built the tooling to do this properly, and did not get to run it on real tool data:

- `label_session.py` runs a guided labelled recording (walking, gesture, tool, idle) and rejects a run where the motion was not what was asked, for example a "tool" run where no vibration reached the band.
- `analyze.py` grid-searches the thresholds against labelled recordings. Its objective is `0.6 × specificity + 0.4 × sensitivity`, because a false tool claim costs more than a miss. It refuses to tune with fewer than 20 tool windows, never sets the vibration threshold below three times the idle noise, and only writes new thresholds if a full replay gives zero false confirmed sessions.
- `buzz_check.py` is a live meter that answers one question before any recording: does this tool's vibration reach the band at all?

## 5. Exposure points

**File:** `vexora/backend/exposure.py`. **Tool magnitudes:** `vexora/backend/tool_profiles.json`.

This is not our algorithm. It is the UK Health and Safety Executive's points method, implemented exactly:

```
points = 2 × a² × T          a = tool vibration magnitude in m/s², T = trigger time in hours
100 points = exposure action value     (equivalent to A(8) = 2.5 m/s²)
400 points = exposure limit value      (equivalent to A(8) = 5.0 m/s²)
minutes to reach p points = 60 × p / (2 × a²)
A(8) = 2.5 × √(points / 100)
```

**Why this method:** it is the one UK employers are already required to use under the Control of Vibration at Work Regulations 2005, so a Vexora record is in the units a safety manager and a regulator already work in. Points also add up across tools in a day, which A(8) values do not.

**Where *a* comes from:** HSE's recommended initial value for each tool type (Table 1 of its vibration risk assessment guidance), stored with its source and range. For example: hammer drill 11 (range 7–13), breaker 14 (7–18), angle grinder 7 (3–10), needle scaler 19 (12–26). Two tools not in that table use 2.5 as a cautious lower bound, following Safe Work Australia's guidance.

**Rules in the code:**

- Reaching a value counts as above it.
- Rejected sessions add nothing. Unresolved sessions are summed separately as "pending", and a worst-case total is shown.
- Inputs must be finite and non-negative, or the function raises an error.
- When the day's total crosses 100 or 400, the band flashes red and the laptop says so.

**Limit:** *a* is the published value for the tool *type*, not a measurement of the specific tool. HSE itself warns that manufacturers' figures can underestimate real vibration.

## 6. Exposure-aware planner

**File:** `vexora/backend/planner.py`.

**What it is:** a rule-based projection. There is no optimiser in main.

For a planned job it computes the points the job would add (`2 × a² × estimated minutes / 60`), adds them to today's total and classifies the result: 400 or more is over the limit; 100 or more is a concern; 80 or more is a concern because it is within 20 points of the action value. It then lists options for a supervisor: a lower-vibration tool with its own projected points, splitting the job across shifts, reassignment, or replanning.

**Why rules:** every result must be explainable, and nothing may be automatic. Every output carries `requires_supervisor_review: true` and an empty list of automatic actions. Vexora never reassigns anyone.

## 7. Voice: wake word and intent parser

**File:** `vexora/backend/intents.py` (wake-word pattern in `inspection.py`).

**What it is:** deterministic pattern matching with a fixed priority order. No model and no API key.

- **Wake word:** "Vexora" (and "BeltGuard"), with the spellings Deepgram actually produces, at the start of a sentence only. "The belt guard is missing" stays a part name.
- **Without the wake word,** the always-on microphone accepts only two things: a yes or no while Vexora is waiting for one, and a strict defect report (a named part *and* a problem word). Everything else is stored as chat and ignored.
- **Priority order** resolves clashes: "not a tool" is checked before "tool", so a rejection is never read as a checkout.
- **Tool names:** the longest alias wins, so "hammer drill" is not read as "drill". A vague word such as "hammer" returns a clarifying question.
- **Ask, never guess:** if a station or tool cannot be pinned down, the parser returns a question and the caller must speak it and not act.

**Why not a language model here:** the microphone is always on in a loud room. A stray "yes" must never add exposure to someone's record, and a deterministic parser lets us prove that with tests. It is also instant, free and works offline.

## 8. Defect extraction and station validation

**File:** `vexora/backend/inspection.py`.

**What it is:** a hybrid. A rules extractor always runs. An OpenAI structured-output call (strict JSON schema) is optional and only made when the words could be a report. **Code, not the model, decides the station.**

- **Station:** taken from explicit numbers ("station three", "S3") and location keywords. Two different numbers, a number out of range, or a number that conflicts with a keyword all return a question.
- **The model's station is accepted only if** the evidence it cites appears word for word in the transcript and contains exactly that one number, and only when the rules found nothing.
- **Parts are grounded:** a part name from the model counts only if a word of it was actually said.
- **Severity can only be raised by the rules,** never lowered below them.
- **Unsafe steps are stripped:** any model-written step about isolation, lock-out, permits or close-out is removed, and fixed safety steps written in code are added.

**Why hybrid:** a language model is good at turning messy speech into fields and bad at being trusted with a safety-relevant fact such as *where*. So the model drafts and the code verifies. On the day the OpenAI key was empty, so the rules extractor is what ran; we have no recorded result from a live model run.

## 9. Job agent

**File:** `vexora/backend/job_agent.py`.

**What it is:** rule-based language processing that builds the job checklist from what the technician says.

- Splits speech into clauses and ignores clauses over 14 words, negations, questions, talk about the software, and Vexora's own spoken replies picked up by the microphone.
- Extracts an action from known job patterns (isolate, guard off, replace, guard on, test run) or a generic verb-plus-equipment pattern.
- **Tense sets status:** "-ed" is done, "-ing" is in progress, a bare verb is planned. A step never moves backwards.
- **Isolation-before-work warning:** if a work step starts, or the band reports a tool running, and no isolation step is done, Vexora says "Safety check: isolate and lock out the conveyor first", once per job.
- Attaches band evidence to the step in progress: the tool, the trigger time and the points.

**Limit:** the warning is advisory. It never blocks work, and Vexora never verifies isolation; a spoken "it's isolated" is logged as a claim.

## 10. Staged alert and band events

- **Staged alert (5 s amber, 7 s orange, 10 s red).** `vexora/frontend/bandlink.js`. A separate front-end heuristic for the demo: half-second segments count as "moving" when acceleration RMS is at least 0.15 g and the zero-crossing frequency is at least 3 Hz, and the stages fire on the length of the run. It does not use the detector, the checked-out tool or points. The screen labels it as a demo threshold.
- **Points-based LED.** `vexora/backend/app.py`. Green below the action value, amber during a session, red above 100, fast red above 400.
- **Free-fall and double-tap** are detected by the band's own firmware. Vexora only reacts: free-fall raises a "possible drop or fall" alert; double-tap is a gloves-on "yes".

## 11. Work-order flow

**File:** `workorder/flow.py`.

**What it is:** a small state machine: draft → checks → schedule → create → calendar → verify. Each step records what it did and the evidence, and a failed step blocks with the real reason.

- **Checks:** asset, problem and task must be present; priority must be low, medium or high; repair time between 5 and 720 minutes; and a named person must approve.
- **Schedule:** the next half-hour mark that is at least 30 minutes away. There is no crew or conflict scheduling.
- **Verify:** the record is read back from the provider and checked against the asset, so the flow does not trust its own "created" result.

**Why:** the pattern (each step returns evidence, and the engine re-checks) comes from an earlier project of ours. The Odoo provider has not been run against a live Odoo system; the calendar entry is prepared, not booked.

## 12. Research and standards we rely on

| Source | What we use it for |
|---|---|
| HSE, *Vibration risk assessment*, incl. Table 1 — https://www.hse.gov.uk/vibration/hav/assessrisks.htm | The points formula, the 100 and 400 values, and the vibration magnitude for each tool type |
| The Control of Vibration at Work Regulations 2005 (SI 2005/1093) | The legal action and limit values |
| Palmer KT et al., *Validity of self reported occupational exposures to hand transmitted and whole body vibration*, Occup Environ Med 2000;57:237–241, doi:10.1136/oem.57.4.237 | Self-reported tool time is a median 2.5× the observed time: the reason we measure time |
| McCallig M et al., *Evaluating worker vibration exposures using self-reported and direct observation estimates of exposure duration*, Applied Ergonomics 2010;42(1):37–45, doi:10.1016/j.apergo.2010.04.002 | Questionnaire estimates are a median 9× observed |
| NIOSH, *Criteria for a Recommended Standard: Occupational Exposure to Hand-Arm Vibration*, Pub. 89-106, 1989 | About 1.45 million US workers use vibrating tools |
| Safe Work Australia, *Guide to measuring and assessing workplace exposure to hand-arm vibration* | Treating declared values below 2.5 m/s² as 2.5 |
| ISO 5349 | Named only as what Vexora is **not**: a measurement at the hand–tool interface |

**No research paper is cited for the detector's features, thresholds or state machine.** The techniques are standard signal processing (windowed FFT band power, autocorrelation, crest factor, hysteresis), combined and tuned by us.

## 13. What is tested, and what is not

| Area | Result |
|---|---|
| Detector (`test_detector.py`) | 21 of 21 pass, synthetic signals only |
| Exposure maths, tool profiles, planner, defect extraction (`test_brain.py`) | All pass; the live-model test is skipped without a key |
| Intent parser (`test_intents.py`) | 18 of 18 pass |
| Spoken job steps (`test_spoken_steps.py`) | 9 of 9 pass |
| Work-order flow (`workorder/tests`) | 40 of 40 pass |
| Older fixed-checklist job path (`test_job_agent.py`) | 14 pass, 4 fail; this path is no longer the live one |

Not tested or not verified:

- The detector on a real power tool.
- The detector's per-rate tuned thresholds (the tests use the built-in defaults).
- The staged 5 / 7 / 10 s alert (no tests).
- Double-tap and free-fall reliability while the band is worn.
- The Odoo provider against a live system.

## 14. Work on the `ali-ui` branch that is not in main

A parallel build on the `ali-ui` branch explored heavier methods. None of this code is in main, and none of it is validated on real data. It is listed so the full picture of what the team built is visible.

- **Shift-plan optimiser (Google OR-Tools CP-SAT).** Assigns each remaining task to a verified, qualified worker and orders the tasks. Boolean variables per task and worker, an all-different ordering, dependency constraints, and a hard cap keeping each worker strictly below 100 points. The objective minimises the highest projected exposure first, then priority-weighted delay. If the cap cannot be met, it relaxes it and reports that it did. It returns up to three alternative plans, each marked as a proposal that needs approval. *Why a constraint solver:* assignment under qualification, dependency and cap constraints is a small combinatorial problem that rules cannot solve well, and a solver can prove when no plan fits.
- **Feature extraction for a learned detector.** 21 features per 1.5-second window: a Butterworth low-pass at 0.5 Hz to estimate gravity, a Hann-windowed periodogram, six band powers, the two dominant frequencies, the high-frequency ratio and spectral entropy.
- **Random-forest activity classifier.** Code and a training pipeline exist (120 trees, sessions held out whole, never single windows). **No model was trained**, because no real labelled recordings exist, and the code refuses to claim accuracy from synthetic data.
- **Acoustic anomaly screening.** 13 MFCCs and 40 mel bands summarised per clip, scored as a z-score distance from reference clips of normal operation, switching to an isolation forest at 20 or more references. Experimental, tested on synthetic audio only, and not connected to the band's microphone.
