# BeltGuard: the VexoHacks entry, ready to paste

Rewritten at 15:00 PT by S6 for the final product flow, and updated at 15:30 with the control room and the new tool-checkout screens. For **https://vexohacks.devpost.com/** (deadline 6:00 PM PT). The requirements are in `SUBMISSION_REQUIREMENTS.md`, the commit, push and submit steps are in `RELEASE_CHECKLIST.md`, and the live run sheet and video shot list are in `../demo/DEMO_SCRIPT.md`.

Nothing has been submitted. **Check every ⚠️ line just before you paste.** Each one depends on what actually works at about 5:30 PM.

---

## Form fields

| Devpost field | Paste this |
|---|---|
| **Project name** | BeltGuard |
| **Elevator pitch** (174 chars) | A Vexo band times every power-tool trigger pull at the wrist, tells the technician how long they can keep going before the legal vibration limit, and runs the shift by voice. |
| **Theme track** | **Ambient + Voice** (reason below) |
| **Sponsor tracks** | None. Deepgram does our speech-to-text, but its track asks for Pipecat, which we don't use. We don't use Gemini, ElevenLabs or Stripe: the spoken replies come from the browser's built-in speech. The rules say a track has to be meaningfully addressed. ⚠️ Change this only if one of them really ends up in the build. |
| **Built with** | vexo-open, bluetooth-low-energy, python, fastapi, websockets, asyncio, sqlite, numpy, deepgram, openai, javascript, three.js, web-speech-api |
| **Try it out** | https://github.com/zubair480/vexo-hacks (project in `beltguard/`). ⚠️ Paste this only after `beltguard/` is committed and pushed. |
| **Video** | ⚠️ unlisted YouTube link, 60–90 s (shot list in `demo/DEMO_SCRIPT.md`) |
| **Team** | Zubair Zafar · Ali Amjad ⚠️ confirm both are added on Devpost |

**Why Ambient + Voice:**
- **Ambient:** BeltGuard understands the moment a power tool runs in the technician's hand. It times it, scores it and warns at the limit, and nobody has to open an app.
- **Voice:** the whole shift runs hands-free on the band's always-on mic with the wake word "BeltGuard".
- We don't claim Vision, because the band has no camera.

---

## About the project (paste into the Devpost story)

### Inspiration
Hand-arm vibration from power tools causes permanent nerve, blood-vessel and joint damage, known as hand-arm vibration syndrome (HAVS). UK employers have to keep it under control with the HSE points system: **points = 2 × a² × T**. Here *a* is the tool's vibration in m/s² and *T* is trigger time in hours. **100 points is the exposure action value and 400 points is the exposure limit value.**

The formula is easy, but **T is the weak input.** When researchers watched workers, they overestimated their own vibration exposure time by a median **2.5×**. On questionnaires the overestimate reached a median **9×**. So most paper HAV records are guesses.

In the US, NIOSH estimated that about **1.45 million workers** use vibrating tools, and **OSHA still has no hand-arm vibration limit**.

Today's answers add dedicated hardware:
- Reactec's HAVwear (Reactec/Ideagen) is a wrist unit that reads an **RFID tag fitted to every tool**.
- HAV-Sentry is a **sensor glove**.
- spacebands sells a separate HAV and noise wearable.

Meanwhile, sites already hand out power tools from a crib. We asked: what if the checkout itself started the exposure clock, and the band the technician already wears did the timing?

### What it does
A maintenance technician on overland conveyor CV-102 wears a Vexo band on the tool-hand wrist.

1. **Pick a tool from the crib.** **Start** walks the 3D worker to the tool wall. The technician taps a card, clicks the tool on the 3D wall, or says *"BeltGuard, I'm taking the hammer drill."* Each tool shows its vibration magnitude and source:

   | Tool | m/s² |
   |---|---|
   | Drill | 5 |
   | Hammer drill | 11 |
   | Impact wrench ½" | 5 |
   | Angle grinder | 7 |
   | Power jack | 2.5 |
   | Breaker hammer | 14 |

   The magnitudes are HSE Table 1 reference values. The power jack isn't in HSE's table, so it uses a conservative 2.5 m/s² bound. The card then reads "Hammer drill locked to Technician W-01", with the time, the points per hour and the minutes left to the action value. Only that person's trigger time counts toward the checkout.
2. **Live wrist monitoring.** The band streams accelerometer and gyroscope data at 416 Hz. A detector marks when the tool is running, and **trigger time comes from the band's own sample clock**. Points rise only while the tool runs, and they stay "provisional" until the session is confirmed.
3. **Exposure verdict.** One unmistakable state, with the minutes left to 100 and to 400:
   - **"Within the safe limit"**
   - **"Action value reached: take a break or rotate"**
   - **"Limit reached: stop using vibrating tools today"**

   When the day crosses 100 or 400 points, the band flashes red. BeltGuard also says "Exposure action value (100 pts) reached. Supervisor review required (screening estimate)."
4. **Return with a summary.** "BeltGuard, done, returning the hammer drill" closes the checkout and reports the trigger time and points with that tool. Today's points stay on the technician's record.
5. **The whole shift by voice.** The band's mic is always on and Deepgram transcribes it. Commands start with the wake word, and the laptop speaks the answers:

   | Technician says | BeltGuard |
   |---|---|
   | "BeltGuard, I'm at station three." | "Station 3, return idlers. Ready when you are." |
   | "BeltGuard, how long can I keep going?" | "You're at 1.3 points, below the 100-point action value. About 24 minutes left with the hammer drill before 100." |
   | "BeltGuard, idler replaced." | The work order goes to *awaiting supervisor verification*. BeltGuard never closes work itself. |

   - **A clear defect needs no wake word.** "Return idler seized" becomes a structured defect: OpenAI extracts it with a strict schema when a key is set (otherwise a rules extractor does), and **code, not the model, checks the station**. It also produces a work-order draft that says isolation must be verified by an authorized person. If the station isn't clear, BeltGuard asks instead of guessing.
   - **Every sentence is recorded, and chat is ignored.** Team conversation is stored and marked ignored, and BeltGuard never acts on it or answers it.
6. **Band features.**
   - A **double-tap** confirms a session the band was unsure about. BeltGuard asks "Was that Hammer drill, 20 seconds? Say yes or no, or double-tap to confirm."
   - **Free-fall** raises a drop or fall alert, "Possible drop or fall near S3 Return idlers: check on W-01", which a person must check.
   - The **LED flashes red** at 100 and 400 points.
7. **Supervisor control room.** One screen for the whole crew:
   - KPIs and the 3D site
   - a card per worker, with tool, trigger time, 0–400 bar, verdict and last answered voice line
   - a crew story feed with alerts

   W-01 is the real band 07. **W-02 to W-05 are simulated bands**, labelled "Simulated band" on screen. They run 30× faster than real time, live in memory only and are never written to the database.

### Why the band is essential (the take-the-band-out test)
- **Take the band off and T goes back to a guess**, with the same 2.5–9× error. The signal starts at the hand: the wrist moves with the tool from trigger pull to release. A phone in a pocket feels neither, and no fixed camera follows a technician along an overland conveyor.
- **Hands are busy and gloved.** The voice commands, the wake word and the double-tap replace the clipboard and the tablet.
- **No tool tags.** The tool comes from the crib checkout, not from an RFID tag fitted to every tool.
- **It's on the body.** The fall alert and the red flash at the limit happen on the person, wherever they are on the conveyor.

### How we built it
- **Hardware:** Vexo Open band 07 (beta firmware 1.1.0). We use:
  - accelerometer and gyroscope at 16 g / 2000 dps, streaming both sensors at 416 Hz. The rate drops itself to 208 Hz if audio makes the band lose samples.
  - the 16 kHz Opus mic, always on
  - the double-tap and free-fall events
  - the RGB LED
- **Band link:** the official `vexo_open` Python SDK over Bluetooth LE or USB. One backend process owns the band's single link. A Chrome Web Bluetooth path, using the kit's JavaScript SDK, is the backup.
- **Backend:** Python 3.13, FastAPI and uvicorn, an asyncio event hub that broadcasts over WebSocket, and SQLite. Every row has an id, a timestamp and a source.
- **Tool-use detector:** pure Python and numpy.
  - Its states are idle, walking, gesture, possible tool use, tool running and unresolved, and every session carries a confidence score.
  - Timing uses the band clock, and lost samples are counted from the sample counter.
  - We set its live thresholds from band 07 recordings, so hand movements stay "unresolved" instead of counting.
- **Exposure engine:** HSE points against 100/400, with the minutes left to each and a verdict for every tool. Each tool profile records its source.
- **Voice:**
  - The kit's speech helper streams the band's audio to Deepgram nova-3.
  - A deterministic intent parser handles the wake word and the commands (station, checkout, return, exposure question, confirm or reject, job done, isolation claim, break) and needs no API key.
  - Defect reports go to OpenAI structured extraction (the kit's `ai.extract`, strict JSON schema, storage off) when a key is set, with a rules fallback. ⚠️ At 15:25 `OPENAI_API_KEY` was empty, so the rules extractor runs. Add the key before 5:30, or keep this wording.
- **Dashboard:** vanilla JavaScript.
  - A 5-step checkout flow: Unlock tool, Assigned, Using tool, Exposure verdict, Return tool.
  - A Three.js 3D model of the conveyor site. The worker walks to the tool wall, picks the checked-out tool, walks to the station and holds it while it runs.
  - Live wrist signals, an Inspection tab, and the supervisor control room (one real band, four labelled simulated ones).
  - Replies are spoken with the browser's speech API.

### Challenges we ran into
- **An always-on mic in a loud room.** Most of what the band hears is chat. So commands need the wake word, unaddressed speech only counts when it's a clear defect report (a part plus a problem), and a stray "yes" only counts in the 30 seconds after BeltGuard asks a question.
- **The band locks its LED while the mic is on.** To flash red at 100/400, BeltGuard pauses the audio, blinks for 4 seconds, then resumes listening.
- **The band holds one Bluetooth link.** One backend owns it, and everything else reads from the backend.
- **Real sample rates drift.** 416 Hz actually runs at 407–423 Hz, so every duration uses the band's clock, never sample counts.
- **Keeping the AI safe around isolation and work orders.** The model drafts, code validates, and people decide.

### Accomplishments that we're proud of
- **We measured band 07 directly rather than trusting the docs:** 0 lost samples at 416 Hz on both sensors, and at 208 Hz with audio streaming at the same time.
- **The voice end-to-end suite passed 87 of 88 checks with 0 failures**, on a copy of the database. It covers the wake word, every intent, chat being ignored, the double-tap confirm and the fall alert. The one warning, a help reply that was too short, was fixed afterwards. ⚠️ Update this with the final run if there is one.
- **The tool-use detector passes 21/21 synthetic tests and a 25-seed sweep with zero failures.**
- **Every number is labelled:** its source, "provisional" or "confirmed", and "screening estimate".

### What we learned
- The hard part of hand-arm vibration isn't the formula. It's trigger time.
- Keep the two inputs separate. The wrist is good at *when*, and the tool's reference value supplies *how hard*. Mixing them would turn a screening tool into a fake meter.
- Always-on voice needs a wake word. Without one, the room's chat becomes data.

### What's next
- **Validate:** compare trigger time against video and a reference ISO 5349 meter on real hammer drills, grinders and breakers.
- **Connect:** take checkouts from the real tool crib and the work-order system (SAP PM, Maximo), and write back exposure and defects.
- **Grow:** a multi-worker supervisor view, running on a phone instead of a laptop, and a pilot with a conveyor maintenance contractor.

### Honest limitations
- **A screening estimate, not a certified HAV meter.** ISO 5349 measures vibration at the hand–tool interface. The band times trigger use; it never measures vibration at the hand. *a* is an HSE reference value for the tool type, not the specific tool.
- **On stage, a phone on vibrate stands in for the hammer drill.** The band times the buzz for real, and the points use the hammer drill's 11 m/s². We haven't tested on real power tools. ⚠️ Confirm on band 07 that the phone buzz starts a session.
- **100 and 400 aren't shown live.** Reaching 100 takes about 25 minutes of real hammer-drill trigger time, so the demo shows the verdict below the action value.
- **The band never names the tool.** The checkout does.
- **The AI never authorizes isolation, never closes a work order and never reassigns anyone.** "Job done" only moves a work order to awaiting supervisor verification. The fall alert only reports.
- **Always listening has a cost.** The band's audio streams to Deepgram all shift, and chat is transcribed and stored (marked ignored). A deployment would need consent and data-retention rules. Only defect reports go to OpenAI, with storage off, and only when a key is set.
- **Most of the control-room crew is simulated.** Only W-01 is a real band. W-02 to W-05 are simulated, run at 30× time and are labelled on screen.
- **The laptop does the talking and the bridging.** It speaks the replies (the band has no speaker) and bridges Bluetooth. There's no phone app or cloud yet.
- **One seeded conveyor and one worker.** CV-102 with five stations, and Technician W-01.
- ⚠️ The double-tap and free-fall events haven't been tested on band 07 yet (as of 15:00). Delete this line once they've been seen working.
- Built in one day at VexoHacks. The first commit was at 10:00 AM on 2026-11-14, after the 9 AM code-start rule.

### Sources
- HSE, [Vibration risk assessment](https://www.hse.gov.uk/vibration/hav/assessrisks.htm): the points system, 100/400, and the Table 1 tool magnitudes.
- [Control of Vibration at Work Regulations 2005](https://www.legislation.gov.uk/uksi/2005/1093/contents/made)
- Palmer KT et al., Occup Environ Med 2000;57:237–241, [doi:10.1136/oem.57.4.237](https://doi.org/10.1136/oem.57.4.237): median 2.5× overestimate of exposure time by observed workers.
- McCallig M et al., Applied Ergonomics 2010;42(1):37–45, [doi:10.1016/j.apergo.2010.04.002](https://doi.org/10.1016/j.apergo.2010.04.002): questionnaire times a median 9× observed.
- NIOSH, [Criteria for a Recommended Standard: Hand-Arm Vibration, Pub. 89-106](https://stacks.cdc.gov/view/cdc/11176): about 1.45 million US workers.
- MEMIC, [HAVS and the Have Nots](https://memic.com/workplace-safety/safety-net-blog/havs-and-the-have-nots): no OSHA hand-arm vibration standard.
- Safe Work Australia, [Guide to measuring and assessing workplace exposure to hand-arm vibration](https://www.safeworkaustralia.gov.au/system/files/documents/1703/guidetomeasuringassessinghandarmvibration.pdf): treat declarations below 2.5 m/s² as 2.5 (the power jack's value).
- Incumbents:
  - Reactec HAVwear with RFID tool tags: [RFID Journal](https://www.rfidjournal.com/?p=67649), [Construction Index](https://www.theconstructionindex.co.uk/news/view/the-havs-meter-you-wear-on-your-wrist)
  - HAV-Sentry glove: [hav-sentry.com](https://hav-sentry.com/), [NSC](https://nsc.org/techhub/profile/hav-sentry)
  - spacebands: [Safety, Health & Wellbeing Live](https://www.safetyhealthwellbeing.live/press-releases/spacebands-wearables-that-control-havs-and-noise-exposure-and-cut-costs)

---

## Video
Use the 60–90 s shot list in `demo/DEMO_SCRIPT.md`. It's a screen recording plus an over-the-shoulder phone shot of the band on the wrist, with the room audio so viewers hear BeltGuard answer. Record it at about 5:00–5:15 PM on the real band, with no simulator, and say in a caption that the phone stands in for the hammer drill. Upload it to YouTube as unlisted and paste the link above.
