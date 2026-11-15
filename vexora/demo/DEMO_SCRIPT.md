# Vexora: 2-minute live demo (judging from 6:30 PM)

## 60-second demo video: what to click, what to say (added 15:40)
Record it on the **live** server, http://127.0.0.1:8765/, with band 07 on the wearer's tool-hand wrist.

**Before you record:**
- **Band 07 · Live** shows in the top bar, and only one dashboard tab is open.
- Click once on the page, then in **Details** tick **Speak Vexora's replies aloud**.
- Start a clean shift: run `/api/tool/return` (if a tool is out), then `/api/reset` (commands in the pre-flight below). The screen says **Start of shift**.
- The wearer stands about 2 m from the laptop speakers.
- Record the Chrome window with **Win+Alt+R**, while a second person films the wrist and screen over the shoulder on a phone, with room audio.

| Time | Click (driver) | Say (wearer) | Screen, and what Vexora says |
|---|---|---|---|
| 0:00–0:06 | **Start** | "Vexora, I'm at station three." | The worker walks to the tool station and **Pick a tool** opens, with m/s² on every tool. Vexora: "Station 3, return idlers. Ready when you are." |
| 0:06–0:15 | — | "Vexora, I'm taking the hammer drill." | "Locking the hammer drill to you…", then **Hammer drill locked to Technician W-01** (11.0 m/s², 242 pts/h, 25 min to the action value). The worker takes the drill and walks to station 3. Vexora: "Hammer drill assigned to you. I'll time it from your band. About 24 minutes on it before the action value." |
| 0:15–0:20 | — | "Conveyor's isolated." (no wake word: the job checklist hears every sentence) | Vexora: "Isolation logged; an authorized person must verify it. Next: remove guard." |
| 0:20–0:32 | — | Buzz the phone pressed into the palm of the band hand for about 10 s, wrist still | Step 3, **Using the hammer drill**: **Tool running**, **Trigger time, running** counting up, and "+x pts provisional". The live panel under the 3D view appears. |
| 0:32–0:38 | — | Only if asked: double-tap the band | "Was that Hammer drill, 10 seconds? Say yes or no, or double-tap to confirm." Then "Confirmed by double-tap: TS-####, 10 s, +0.67 pts". Step 4: **Within the safe limit**, with the 0–400 bar and the minutes left. |
| 0:38–0:46 | — | "Vexora, how long can I keep going?" | "You're at 0.7 points, below the 100-point action value. About 24 minutes left with the hammer drill before 100." |
| 0:46–0:52 | Hold on the live panel | — | **Technician** shows every sentence, with chat dimmed. **Job** shows ✓ Isolate & lock out CV-102 ("heard …: Conveyor's isolated.") and Replace idler in progress ("band: hammer drill 0:10 trigger, 0.7 pts (confirmed)"). **Vexora did** shows "Isolation logged; an authorized person must verify it." |
| 0:52–1:00 | **Supervisor view** (top bar) | — | The control room: W-01 on **Band 07 · Live** with its points, W-02 to W-05 with **Simulated band** chips, and the crew story. |

**Captions** (burn them in, so the video is honest when watched without sound):
- 0:00: "Recorded live on Vexo band 07"
- 0:20: "A phone on vibrate stands in for the hammer drill. The maths uses the drill's HSE value, 11 m/s²."
- 0:52: "W-02 to W-05 are simulated bands, labelled on screen"
- end: "Screening estimate, not a certified HAV measurement. AI drafts; people decide."

**Don't say these near the band:** "Vexora, help" or "Guard's back on". The laptop reads its replies aloud, and the band mic can hear them and run or tick things; the reply to "Guard's back on" would tick the test-run step. The numbers in the table assume a 10 s buzz. If Vexora doesn't ask about the session, skip the double-tap row.

---

Final flow, rewritten at 14:58 PT by S6 from the live app at http://127.0.0.1:8765/ and the code (`backend/intents.py`, `backend/app.py`, `frontend/app3.js`). Screen labels were refreshed at 15:30 for the new **Pick tool** and **Locked to you** screens. Every **Vexora says** line is the exact text the code produces. Every spoken command was run through the voice parser and lands on the intended action.

**What the judges see:** a technician checks a tool out of the crib by voice. The band on their wrist times each trigger pull, and Vexora gives an exposure verdict with the minutes left. Voice runs the rest of the shift: station, defect, question, tool return, job done. Nothing on screen is simulated.

## Roles
- **Wearer:** band 07 on the tool-hand wrist, strap snug. Holds the phone (the stand-in tool) and does all the talking.
- **Driver:** at the laptop, with a single Chrome tab on http://127.0.0.1:8765/ full screen. Mostly hands off, because the screen follows the voice.

## What's real and what isn't
- Trigger time, the detector state and every number on screen come live from band 07. There's no simulator.
- On stage, **a phone on vibrate stands in for the hammer drill**. The band times the buzz for real, and the points use the hammer drill's HSE value of 11 m/s². The screen only says "Hammer drill", so the wearer has to say this out loud (it's in the script).
  - The alternative is to unlock **Phone on vibrate**, which is labelled "Demo stand-in" on screen and uses 5 m/s². Say "Vexora, I'm taking the demo tool". The countdown is then 120 minutes instead of 25.
- The laptop speaks Vexora's answers, because the band has no speaker. Each answer also appears in the conversation strip under the step panel.
- 100 and 400 points can't be reached on stage: reaching 100 takes about 25 minutes of real hammer-drill trigger time. Describe those states, and never fake them.
- The numbers below assume a 20-second buzz. Yours will differ a little.

## Pre-flight (from 6:15 PM)
1. **Use one dashboard tab.** Close every other Vexora, Band link and kit dashboard tab in every browser. Only one tab speaks the replies, and extra tabs slow the 3D view.
2. **Keep the backend running.** S2 does no restarts after 5:30 PM. Don't edit files in `backend/` or `frontend/` during judging, because every save hot-reloads. If the backend is down, run `python -u backend/app.py` from `vexora/`.
3. **Check the band is live.** The backend finds band 07 by itself over Bluetooth or USB. The top bar must say **Band 07 · Live**, and the Inspection tab must say **Listening on band 07**. If not, double-tap the band to wake it and wait up to a minute.
4. **Turn speech on.** Click once anywhere on the page, because Chrome only speaks after a click. In **Details**, tick **Speak Vexora's replies aloud**. Turn the laptop volume up, and have the wearer stand about 2 m from the speakers (see **Echo** under Fallbacks).
5. **Do a voice check.** Say "Vexora, how long can I keep going?" You should hear "You're at 0 points, below the 100-point action value." **Don't use "Vexora, help" near the band.** Its spoken examples start with "Vexora", and the band can hear and run them.
6. **Start a clean shift, last of all.** In PowerShell:
   ```powershell
   $api = "http://127.0.0.1:8765/api"
   try { Invoke-RestMethod -Method Post "$api/tool/return" } catch { "no tool was out" }
   Invoke-RestMethod -Method Post "$api/reset"
   ```
   The screen should show **Start of shift** with a **Start** button and 0 points. The reset doesn't hand back a checked-out tool, so the return comes first.
7. **Prepare the phone.** Set a vibrate-only timer or alarm you can start on cue that buzzes for at least 30 s, and turn the ringer off.
8. Keep this file open on a second screen for the fallbacks.

⚠️ **Test once on the real band before the video (about 5 PM), then reset.** These three haven't been verified on band 07 yet:
- that the phone buzz starts a session (the screen shows **Using the hammer drill** with the trigger time running)
- that a double-tap confirms an unsure session
- that a drop raises the fall alert

## Run sheet (2:00)

| Time | Wearer does and says | Vexora says (laptop) | Screen |
|---|---|---|---|
| 0:00 | Faces the judges with the band visible. "Power tools have a legal daily vibration limit. HSE counts it in points: two times the tool's vibration squared, times trigger time. Nobody measures trigger time. Workers guess, and they overestimate it 2.5 to 9 times. This band measures it." | — | **Start of shift.** The driver clicks **Start**: the 3D worker walks to the tool wall, and **Pick a tool** shows the crib: Drill 5.0, Hammer drill 11.0, Impact wrench 5.0, Angle grinder 7.0, Power jack 2.5, Breaker hammer 14.0 m/s², each marked "HSE guidance" (the power jack says "Conservative estimate"). |
| 0:15 | **"Vexora, I'm at station three."** | "Station 3, return idlers. Ready when you are." | The 3D view moves to station 3, and the conversation strip shows the You and Vexora lines. |
| 0:22 | **"Return idler seized."** Then: "No wake word for a clear defect. Team chat is recorded but never acted on." | "Logged seized return idler at station 3, return idlers. Work order WO-#### drafted for review." | A toast appears. The driver can click **Inspection** to show the work-order draft ("isolation must be verified by an authorized person"), then go back to **Tool checkout**. |
| 0:35 | **"Vexora, I'm taking the hammer drill."** Holds up the phone: "On stage, this phone on vibrate stands in for the drill. The maths uses the hammer drill's HSE value, 11 metres per second squared." | "Hammer drill assigned to you. I'll time it from your band. About 24 minutes on it before the action value." | "Locking the hammer drill to you…", then step 2: **Hammer drill locked to Technician W-01** (18:3x · 11.0 m/s², HSE guidance), with 242 pts/h and 25 min to the action value. The screen rounds to 25 and the voice rounds down to 24. The 3D worker takes the drill off the wall and walks to station 3, and step 3 opens. |
| 0:50 | Starts the buzz and presses the phone into the palm of the band hand, wrist still, for about 20 s. "The band samples at 416 hertz. It feels the trigger pull and the release, and that's trigger time from the band's own clock." Stops the buzz. | — | Step 3, **Using the hammer drill**: the activity badge reads **Tool running** with an orange sparkline, **Trigger time, running** counts up, and "+x pts provisional" shows. In 3D the worker holds the drill and the belt stops. |
| 1:12 | Only if Vexora asks: **double-tap the band.** "When the band isn't sure, it asks. One double-tap, gloves on." | "Was that Hammer drill, 20 seconds? Say yes or no, or double-tap to confirm." Then: "Confirmed by double-tap: TS-####, 20 s, +1.34 pts" | A card reads **Confirm 20 s of tool use?** with "Double-tap the band to confirm." It clears on confirm. If the session was confirmed straight away, nothing is asked: skip this row. |
| 1:22 | **"Vexora, how long can I keep going?"** | "You're at 1.3 points, below the 100-point action value. About 24 minutes left with the hammer drill before 100." | Step 4, **Within the safe limit**: "1.34 pts today, confirmed", the 0–400 bar with "100 action value" and "400 limit value", 24 min to the action value, 99 min to the limit value, and "Screening estimate — not a certified HAV measurement". |
| 1:32 | Points at the bar. "At 100 this turns to 'Action value reached: take a break or rotate', and the band flashes red. At 400 it's 'Limit reached: stop using vibrating tools today'. We'd need 25 minutes of real drilling to get there on stage." | — | — |
| 1:42 | **"Vexora, done, returning the hammer drill."** Say "hammer drill" here. "Returning the drill" also hands it back, but the reply would name the wrong tool. | "Hammer drill returned. 1.3 points with it today." | Back to step 1, **Pick a tool**. The worker hangs the drill back on the wall, and a toast reads "Hammer drill returned · 0.3 min · +1.34 pts". |
| 1:48 | **"Vexora, idler replaced."** | "WO-#### marked awaiting supervisor verification. Vexora never closes work on its own." | — |
| 1:54 | "It's a screening estimate. The band times trigger use, it doesn't measure vibration at the hand. Take the band off, and trigger time goes back to a guess." | — | — |

### Extras, only if there's time or a judge asks
- **Chat is ignored.** Say "I'm taking the breaker hammer" with no wake word. Nothing happens and nothing is said.
- **Fall alert.** Take the band off and drop it about 50 cm onto a folded jacket. A red banner reads "Possible drop or fall near S3 Return idlers: check on W-01", with an **Acknowledge** button, and the laptop says the same. ⚠️ Untested on band 07. Put the band back on the wrist afterwards.
- **Every sentence is kept.** http://127.0.0.1:8765/api/transcripts lists every sentence the band heard and what Vexora did with it. Chat is marked ignored.

## Fallbacks

Set this once in PowerShell: `$api = "http://127.0.0.1:8765/api"`

| Problem | Do | Say |
|---|---|---|
| The top bar isn't **Band 07 · Live** | Double-tap the band and wait 60 s. Make sure no kit dashboard or Band link tab holds the band. Last resort: open **Band link** from the top bar and connect band 07 from Chrome. Voice then runs in that tab. | "One Bluetooth link per band. It's reconnecting." |
| The band can't be recovered | Play the video. Don't switch to the simulator: the point is real data. | "Here's the same flow, recorded live at 5 PM." |
| A voice command gets no answer | Check the Inspection tab still says **Listening on band 07**. Repeat with "Vexora," first and a short pause after it. Or type the same words into **Or type the report** on the Inspection tab (typed text needs no wake word), or run:<br>`Invoke-RestMethod -Method Post "$api/report" -ContentType application/json -Body '{"text":"I am taking the hammer drill"}'` | "Same pipeline, typed." |
| The answer appears but isn't heard | Click the page once, check **Speak Vexora's replies aloud** in Details, and turn the volume up. Read the answer from the conversation strip. | — |
| Vexora asks "Which one: …?" | Say the full tool name, with "Vexora," first. | "It asks instead of guessing." |
| The wrong tool is assigned | Say "Vexora, I'm taking the hammer drill" again. It's one tool at a time, so the old one is handed back first. | — |
| **Echo:** a second identical defect appears, or a command nobody said runs | The band heard the laptop's speakers. Turn the volume down and step away. Tidy up after the demo, not on stage. | — |
| Step 3 never shows **Using the hammer drill** | Press the phone harder into the palm, keep the wrist still, and buzz for 20–30 s. If there's still nothing, say so and go on to the question and the return. Never confirm a session that didn't happen. | "The band didn't pick that up, so nothing is counted. No guessing." |
| The double-tap does nothing | Say "Yes" (no wake word is needed for 30 s after the question) or click **Confirm** on the card. | — |
| A screen control fails | Check out: `Invoke-RestMethod -Method Post "$api/tool/checkout" -ContentType application/json -Body '{"tool_id":"hammer_drill"}'`<br>Return: `Invoke-RestMethod -Method Post "$api/tool/return"` | — |

## Judge questions
- **Is this a HAV meter?** No, it's a screening estimate: the tool's HSE magnitude times the trigger time measured at the wrist. ISO 5349 measures vibration at the hand–tool interface. The band doesn't, and the screen says so.
- **How does it know the tool?** From the checkout, by tap or by voice. The band never guesses the tool; it only times its use.
- **What if detection is wrong?** Unsure sessions are marked "unresolved" and Vexora asks. A double-tap, "yes" or **Confirm** adds the session; "no" or **Reject** adds nothing. Trigger time comes from the band's sample clock, and lost samples are counted.
- **Is it always listening?** Yes. The band mic streams to Deepgram all shift, and every sentence is stored along with what Vexora did with it.
  - Commands need "Vexora" first. A clear defect report (a part plus a problem) is heard without it.
  - Everything else is chat: stored and marked ignored, never acted on or answered.
  - Only defect reports go to OpenAI, with storage turned off.
  - A real deployment would need consent and data-retention rules.
- **Does the AI make safety decisions?** No. AI drafts, and people decide:
  - "Job done" only moves a work order to awaiting supervisor verification.
  - Isolation claims are logged, and an authorized person has to verify them.
  - The fall alert says a person must check.
- **Why a band, not a phone?** The wrist of the tool hand feels every trigger pull, and a phone in a pocket doesn't. The mic is hands-free, the double-tap works with gloves on, and the LED flashes red on the wrist at 100 and 400 points.
- **What does the LED do?** It flashes red for 4 seconds when the day's total crosses 100 or 400 points. The band locks its LED while the mic is on, so Vexora pauses the audio for the flash and then resumes it.
- **Who buys it?** UK and EU employers already have to assess and control vibration exposure by law. In the US, NIOSH estimated about 1.45 million workers use vibrating tools, and OSHA sets no limit.

## Longer video cut: 60–90 s (the 60-second list at the top is the main one)

Record at about 5:00–5:15 PM, upload to YouTube as unlisted by 5:30, then reset the shift before judging.

**Setup:**
- One person wears the band.
- A second person films on a phone from over the wearer's shoulder, so the wrist and the laptop screen share one frame. Keep the room audio, because viewers need to hear Vexora answer.
- At the same time, record the Chrome window with **Win+Alt+R** (Xbox Game Bar) for close-ups.
- Edit with the over-the-shoulder take as the base, and cut to the screen recording for the step panels.

| Time | Shot | Audio or caption |
|---|---|---|
| 0:00–0:06 | Close-up of band 07 on the wrist, with the phone in hand. | Caption: "Vexora · Vexo band 07 · recorded live, nothing simulated" |
| 0:06–0:14 | Screen: **Start**, the worker walks to the tool wall, then **Pick a tool** with m/s² for each tool. | Caption: "Pick a tool. Its vibration value comes from HSE guidance." |
| 0:14–0:22 | Over the shoulder: "Vexora, I'm at station three." The reply is heard and the 3D view moves to station 3. | Live audio |
| 0:22–0:32 | "Return idler seized." The reply is heard and the toast appears. | Caption: "No wake word needed for a defect. Chat is ignored." |
| 0:32–0:42 | "Vexora, I'm taking the hammer drill." The step 2 card appears. | Caption: "A phone on vibrate stands in for the hammer drill (11 m/s², HSE)." |
| 0:42–0:58 | Close-up of the wrist with the phone buzzing, then cut to step 3 with the trigger time running. | Caption: "Trigger time from the band's own clock, 416 Hz." |
| 0:58–1:06 | If asked: "Was that Hammer drill, 20 seconds?…", a close-up of the double-tap, then "Confirmed by double-tap". | Live audio |
| 1:06–1:16 | "Vexora, how long can I keep going?" Step 4 shows **Within the safe limit**, the 100/400 bar and the minutes left. | Live audio |
| 1:16–1:24 | "Vexora, done, returning the hammer drill." Then "Vexora, idler replaced." | Live audio |
| 1:24–1:30 | End card. | "Screening estimate, not a certified HAV measurement. AI drafts; people decide. github.com/zubair480/vexo-hacks" |

For a 60 s cut, drop the station shot and the "idler replaced" line. If anything was retaken or a stand-in was used, the captions must say so.

Take B in `pitch/RELEASE_CHECKLIST.md` is a clean UI walkthrough on the test server (S8's `?autoplay=1`). It uses the simulated band, so it must be captioned as simulated.
