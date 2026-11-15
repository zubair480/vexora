# BeltGuard release checklist: 6:00 PM PT submission

Prepared at 15:30 PT by S6; the copy-paste block was added at 15:40. **State at 15:40:** there's a local commit `fc536a7`, "BeltGuard: Vexo band 07 tool-vibration exposure MVP", made at 15:38 with 333 files. S6 checked it: no `.env`, database, raw recordings, `tools/out/` or heartbeat log, and no key values. **Nothing is pushed or submitted.** Run the block below only after the user says go (S0 is asking at about 5:15 PM).

## Copy-paste: commit and push
**1. Optional, recommended: point the repo's front page at the project.** The root `README.md` is just `# vexo-hacks`. Paste the whole block, including the closing `'@` line:

```powershell
cd "C:\Users\zubai\vexo hackathon"
@'
# vexo-hacks

Our VexoHacks project is **BeltGuard**: a Vexo band times every power-tool trigger pull at the wrist and tells the technician how long they can keep going before the legal vibration limit. See [vexora/README.md](vexora/README.md).
'@ | Set-Content -Encoding utf8 README.md
```

**2. Commit and push in one paste.**
- It stages only `vexora/` and the root `README.md`.
- Then it refuses to commit if anything outside those got staged, if a `.env`, database, raw recording or `tools/out/` file got staged, or if any staged file contains a key value from `vexora/.env`. Key values are never printed.
- If all is clean, it commits what changed since `fc536a7` and pushes both commits to `main`. If nothing changed, git says "nothing to commit" and the push still sends `fc536a7`.

```powershell
& {
  cd "C:\Users\zubai\vexo hackathon"
  git add beltguard README.md
  $staged = @(git diff --cached --name-only)
  $bad = $staged | Where-Object { $_ -notmatch '^(vexora/|README\.md$)' -or $_ -match '(^|/)\.env$|\.db(-wal|-shm)?$|^vexora/data/raw/|^vexora/tools/out/' }
  $vals = Get-Content beltguard\.env | Where-Object { $_ -match '^(DEEPGRAM_API_KEY|OPENAI_API_KEY)=.{8,}' } | ForEach-Object { ($_ -split '=', 2)[1].Trim().Trim('"').Trim("'") }
  $leaks = foreach ($v in $vals) { $staged | Where-Object { (Test-Path -LiteralPath $_) -and (Select-String -LiteralPath $_ -SimpleMatch $v -Quiet) } }
  if ($bad -or $leaks) { "STOP: nothing committed."; $bad | ForEach-Object { "DO NOT COMMIT: $_" }; $leaks | ForEach-Object { "LEAK: $_" }; return }
  "OK: $($staged.Count) staged files, no key values, nothing forbidden."
  git commit -m "BeltGuard: final updates before the 6 PM submission" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  git push origin main
  git log --oneline -1
}
```

- **If it prints STOP:** for each listed file, run `git restore --staged "<file>"`, fix the file, then paste the block again.
- **If the push is rejected** because the remote has newer commits, run `git pull --rebase origin main`, then `git push origin main`.
- **If anything changes before the 5:30 freeze,** paste the block again at about 5:35. It commits only what changed, and says "nothing to commit" if nothing did.

**3. Check it landed.** Open https://github.com/zubair480/vexo-hacks in a private browser window:
- the repo is public
- `vexora/README.md` renders with its three screenshots
- there's no `vexora/.env`
- the commit time is before 6:00 PM

| Time (PT) | What |
|---|---|
| 5:00–5:20 | Record the video takes (section 4; the 60-second shot list is at the top of `demo/DEMO_SCRIPT.md`) |
| ~5:15 | The user says go, and you run the copy-paste block above |
| **5:30** | **FREEZE.** No more edits to `backend/` or `frontend/` (every save hot-reloads the live app), and no backend restarts (S2) |
| 5:30–5:40 | Paste the block once more if anything changed after the first commit |
| 5:40–5:45 | Submit on Devpost (section 5): a 15-minute buffer |
| **6:00** | Submissions and commits close |
| 6:15 | Demo pre-flight from `demo/DEMO_SCRIPT.md` |
| 6:30 | Judging |

## 1. Release scan (run at 15:22 on what `git add beltguard` would stage)
- **316 files, 20.4 MB.** More files may appear before the freeze, so re-run the leak check in section 3.
- **Secrets: clean.**
  - The `DEEPGRAM_API_KEY` value from `vexora/.env` appears in none of the 316 files, `ops/heartbeat.log` and every log included.
  - There are no OpenAI, AWS, GitHub, Google or Slack token patterns, no private keys, and no email addresses.
  - The only 40-hex strings are Bluetooth protocol test vectors in the kit's docs.
- **Ignored, so these stay on the laptop:** `vexora/.env` (`git check-ignore` → `vexora/.gitignore:1:.env`), `data/beltguard.db` (116 MB) with its `-wal`/`-shm`, `data/raw/` (231 MB, 31 files), `__pycache__/`, and the kit's `build/` and `egg-info/`.
- **Staged unless you decide otherwise:**
  - **Done at 15:36:** `tools/out/` (the 15.1 MB labelled band recording) was added to `vexora/.gitignore`. The file stays on disk, and it was never staged.
  - Logs with no secrets: `ops/heartbeat.log` (105 KB, the team's build log), `ops/band_probe.out`, `ops/band_probe.jsonl`, `data/backend.log`, `data/backend-s0.out` and `data/isolated-8799.out`. Keep or drop them; it's your call.
  - Harmless staging copies and old files: `frontend/app3s.js`, `v3s.html`, `v3s.css`, `supervisor-next.*`, `crew-test.html`, `app.js`, `style.css`, `holo.js` and `ops/app3.backup-143001.js`.
  - `pitch/` holds the team names and the repo URL, which are public on Devpost anyway. `pitch/deck/` is the source of the unused old deck.
- **Licences:** `vexo-open-kit/` and `frontend/vendor/` are the Vexo Open kit, MIT, with its `LICENSE` included. Credited in the README.
- ⚠️ **`OPENAI_API_KEY` is empty in `vexora/.env`**, so defect extraction runs on the rules extractor (source `rules`). The README and `SUBMISSION.md` say "when an OpenAI key is set". If you add a key before 5:30, the AI path switches on with no other change.

## 2. Before committing (at the 5:30 freeze)
1. Make sure every session has stopped editing.
2. `tools/out/` is already ignored (done at 15:36). Optional: keep the stray logs out of git too:
   ```powershell
   cd "C:\Users\zubai\vexo hackathon"
   Add-Content beltguard\.gitignore "data/*.log"
   Add-Content beltguard\.gitignore "data/*.out"
   ```
3. The root `README.md` pointer is step 1 of the copy-paste block at the top.

## 3. Commit and push, step by step (the same as the top block, for a manual look)
Run these from the repository root in PowerShell. Add only `beltguard` and the root `README.md`: never `VexoHacks-brief.md` (private notes), `.claude/` or `reference/`.

```powershell
cd "C:\Users\zubai\vexo hackathon"
git status --short
git check-ignore -v vexora/.env
git add beltguard README.md
git status --short
```

The second `git status --short` must show only `vexora/...` and `README.md`, with no `.env`, no `.db`, no `VexoHacks-brief.md` and no `.claude/`.

Leak check on what's staged. It prints `LEAK: <file>` for any staged file that contains a key value, and never prints the key:

```powershell
$vals = Get-Content beltguard\.env | Where-Object { $_ -match '^(DEEPGRAM_API_KEY|OPENAI_API_KEY)=.{8,}' } | ForEach-Object { ($_ -split '=', 2)[1].Trim().Trim('"').Trim("'") }
$staged = git diff --cached --name-only | Where-Object { Test-Path -LiteralPath $_ }
foreach ($v in $vals) { $staged | Where-Object { Select-String -LiteralPath $_ -SimpleMatch $v -Quiet } | ForEach-Object { "LEAK: $_" } }
"checked $(@($staged).Count) staged files against $(@($vals).Count) key(s)"
```

If anything says LEAK, run `git restore --staged <file>`, fix the file and add it again. Then commit and push:

```powershell
git commit -m "BeltGuard: wrist-timed tool checkout and HAV exposure verdict on the Vexo band" -m "Tool checkout, live wrist timing at 416 Hz, exposure verdict and return; voice with the BeltGuard wake word; double-tap, free-fall and LED alerts; supervisor control room with W-01 on the real band 07 and W-02 to W-05 simulated and labelled. Screening estimate, not a certified HAV meter." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push origin main
```

If the push is rejected because the remote has newer commits, run `git pull --rebase origin main`, then `git push origin main`.

**Check it landed:**
- Open https://github.com/zubair480/vexo-hacks in a private browser window.
- The repo must be public, `vexora/README.md` must render with its three screenshots, and there must be no `vexora/.env`.
- `git log --oneline -3` shows the commit, and its time must be before 6:00 PM.

## 4. Video (60–90 s, record 5:00–5:20, upload unlisted to YouTube)
**Take A: live band (required; this is the real thing).**
- Follow the shot list in `demo/DEMO_SCRIPT.md` on http://127.0.0.1:8765/, with band 07 on the wrist and **Band 07 · Live** in the top bar.
- Record the Chrome window with **Win+Alt+R** (Xbox Game Bar), while a second person films the wrist and screen over the shoulder on a phone, with room audio.
- Say on camera, or caption, that the phone stands in for the hammer drill.

**Take B: clean UI walkthrough (optional, S8's autoplay).**
1. Open **http://127.0.0.1:8799/?autoplay=1** in a fresh tab (1440×900 or 1280×800). This is the test server on a copy of the database, with the simulated band.
2. Click **Play with voice**. The click lets Chrome speak BeltGuard's replies; without it the run starts silently after 5 s.
3. The run takes about 83 s: start of shift → walk to the crib → hammer drill locked to W-01 → station 3 → a 10 s simulated tool scene → verdict → "how long can I keep going?" → defect and work order → return. **Replay** reruns it.
4. Autoplay refuses to run on a real band.

**Honesty for Take B:** the band is simulated, and the "voice" lines are typed into the voice path by the script. The amber "Simulated band · testing" chip must stay in frame, and the caption must say "Test server: simulated band, scripted voice commands". Never cut Take B footage into Take A's live segments without that caption.

**Control-room shot (optional):** http://127.0.0.1:8765/supervisor.html. W-02 to W-05 are simulated, so keep their "Simulated band" chips or the footer line in frame.

**After recording on the live server**, reset the shift before judging: run `POST /api/tool/return`, then `POST /api/reset`, as in the `DEMO_SCRIPT.md` pre-flight.

## 5. Devpost (5:40, submit by 5:45)
1. One teammate logs in at **https://vexohacks.devpost.com/**, clicks **Join hackathon** if they haven't, then **Enter a submission**.
2. Fill in the fields from `pitch/SUBMISSION.md`:

   | Field | Final text |
   |---|---|
   | Project name | BeltGuard |
   | Elevator pitch | A Vexo band times every power-tool trigger pull at the wrist, tells the technician how long they can keep going before the legal vibration limit, and runs the shift by voice. |
   | About the project | Paste the whole "About the project" section of `pitch/SUBMISSION.md`, from Inspiration to Sources. |
   | Built with | vexo-open, bluetooth-low-energy, python, fastapi, websockets, asyncio, sqlite, numpy, deepgram, openai, javascript, three.js, web-speech-api |
   | Try it out | https://github.com/zubair480/vexo-hacks |
   | Video | the unlisted YouTube link from section 4 |
   | Image gallery | `vexora/docs/img/technician-dashboard.jpg`, `control-room.jpg`, `3d-tool-wall.png` |
   | Theme track | Ambient + Voice |
   | Sponsor tracks | none (see `SUBMISSION.md` for why) |
   | Team | add the teammate before submitting |

3. Check every ⚠️ line in `SUBMISSION.md` against what actually worked today, and delete the ones that no longer apply.
4. Click **Submit**, and check the project page shows as submitted. Edits are possible until 6:00.
