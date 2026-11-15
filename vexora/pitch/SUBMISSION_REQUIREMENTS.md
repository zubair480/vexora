# VexoHacks: submission requirements

Captured 13:35 PT on Sat 2026-11-14 by S6 (submission-pitch). Read-only: nothing was joined, submitted or posted.

## Sources (most authoritative first)

| Source | What it gave | Link |
|---|---|---|
| Devpost overview + rules (live, read in the user's Chrome) | where to submit, the deadline, what to submit, prizes, judges | https://vexohacks.devpost.com/ · https://vexohacks.devpost.com/rules |
| Organisers' FAQ doc (pinned in Discord `#faq`) | judging criteria, code-start rule, team size, submission list | https://docs.google.com/document/d/1IM6GMmCBh07LKrnrFy8LR6UeU0Ec9MhcnTrx9TWDSV4 |
| Kickoff slides (Discord `#annoucements`, 10:37 AM) | schedule, build rules, submission checklist, prize recap | https://docs.google.com/presentation/d/1WfolIHSbiYNbzTfdFW26vU3bxyHt2O9NPFA8HRCGcq0 |
| Vexo's hackathon page | the Voice / Vision / Ambient tracks, schedule | https://www.vexoai.com/hackathon |
| YC event page | attendance and address only, **no submission info** | https://events.ycombinator.com/vexohacks |

The URL in the brief, `events.combinators.com/vexohacks`, shows a browser error page. The intended page is `events.ycombinator.com/vexohacks`.

## Where to submit

- **Devpost: https://vexohacks.devpost.com/** · deadline **Nov 14, 2026 @ 6:00 PM PST**.
- It's invite only, for admitted VexoHacks participants aged 16+. The Devpost page listed 15 participants at 13:30.
- The user's Chrome is **not signed in to Devpost** (the header shows Log in / Sign up). One teammate has to log in, click **Join hackathon**, then **Enter a submission**, and add the other teammate to the project.
- The FAQ and slides say the Devpost link would be announced on Discord. By the last announcement (1:01 PM, lunch) it hadn't been posted, but the Devpost page is already live.

## What to submit (Devpost "Requirements", in its own words, condensed)

1. **A project description** saying what it does and how it works.
2. **A public GitHub repository** with the code and a README.
3. **A video** showing the working project.
4. **The sponsor track(s)** you're entering, if any.
- Every submission and every code commit must land **before 6:00 PM**.
- **Be ready to demo in person during judging.**

The FAQ says the same: push to GitHub, video demo, README, and the Devpost link, all before 6:00 PM, with no commits after 6:00 PM.

**Video rules:** none published. Devpost, the rules page, the FAQ and the slides give no length or format. Devpost's standard form takes a link to YouTube, Vimeo, Facebook Video or Youku (confirm in the form). Suggestion: an **unlisted YouTube video of 2 minutes or less**, recorded by about 5:15 PM.

**Form fields:** we can't see the form without signing in. The standard Devpost project form asks for: project name · elevator pitch (short tagline, about 200 characters) · the story (*Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned / What's next*) · built with (tags) · "try it out" links (the GitHub repo) · video link · image gallery (JPG/PNG/GIF, 3:2 works best). After that come hackathon questions such as the sponsor track. `SUBMISSION.md` is laid out to paste straight into these.

## Rules that touch BeltGuard

- **All code must be started after 9:00 AM Saturday**, and the organisers say they'll actively check. The kickoff deck notes hacking officially began at 10:15 AM. ✅ Our repo's first commits are 10:00:30 (Vexo Open kit added as reference) and 10:16:42.
- The project must be **public on GitHub and related to hardware**. ✅ The remote `github.com/zubair480/vexo-hacks` is public (GitHub API returned 200 at 13:34).
  - ⚠️ But **`vexora/` isn't committed yet**: it's all untracked. Nothing counts until it's pushed **before 6:00 PM**. Committing and pushing is the user's call.
  - ⚠️ **Don't push `VexoHacks-brief.md` or `.claude/`.** The brief holds private team notes (registration status, Discord invites, account notes).
  - ✅ `vexora/.gitignore` already excludes `.env`, the SQLite DB and `data/raw/`.
  - ⚠️ The root `README.md` is just `# vexo-hacks`. Point it at `vexora/README.md`, or judges land on an empty page (S0 owns READMEs).
- **To win a track, the project has to meaningfully address it.** Don't tick sponsor tracks we don't really use (see below).
- Teams are at most 3 people plus AI. Attendance is in person only.
- Vexo (Discord, 9:40 AM): only connect to your own device. Firmware is fixed. The BLE contract in `VexoAI-Org/vexo-open-kit` is the interface.

## Judging

- Devpost lists judging criteria as "TBD", and the rules page says "will be revealed soon". **The only published criteria are in the FAQ, with no weights given:**
  1. **Approach & Idea**: viability, how well it solves the problem (market, investors, feasibility, creativity)
  2. **Product: technical**: is there a demo, how good is the prototype, how technically impressive
  3. **Presentation**: a clear, appealing pitch
  4. **Product: design**: effort and thought put into design
  5. **X Factor**: what makes it extraordinary
  6. **AI relevance & depth**: meaningful use of AI agents
- The FAQ's "winning criteria" line: ship great products, and give a good demo that explains why your code is great.
- **Judges (Devpost):** Isa (Raven) · Omar Sekkat, Karan Sandeep (Marv Labs) · Chrissie Cui, Pratik Gupta (Stripe) · Aarush, Arnav Kumar (Subvocal) · Amit Patil (Superserve) · Farqaleet, Haris, Vikram (Vexo) · Vivek Shukla (Mastra) · Georgia Gabriela (Apple) · Om Mahajan (Google) · David Lieb (Y Combinator) · Ivan Leo, Gabby Ong, Amit Vadi (Google DeepMind) · Daniel Tsentsniper, Adam Proscheck (Deepgram).

## Tracks

There are two separate kinds of "track". Don't mix them up in the form.

**1. Theme tracks: Voice / Vision / Ambient** (Vexo's page and the YC/Luma listing; you can pick one or combine them). These aren't on Devpost.
- *Voice*: an interaction done entirely by speech, where asking beats opening anything.
- *Vision*: something that sees what you see and helps before you think to ask.
- *Ambient*: AI out of the chat window, into the places, objects and moments it should already understand.
- The kickoff deck put it as "The challenge: Wearables · Voice · Real-world experiences."

**2. Sponsor tracks**, the only tracks on Devpost. Overall awards are judged separately:

| Sponsor track | Prize | Does BeltGuard qualify honestly? |
|---|---|---|
| Stripe: Best Economic Agent | $3K / $2K / $1K Stripe credits | No. No payments or agent commerce. |
| Google DeepMind: Best Use of Gemini 4 | $2,000 GCP credits per member / $1,000 / $500 | Only if Gemini really ends up in the pipeline. Today it's OpenAI. |
| ElevenLabs: Best Project Built with ElevenLabs | 3 months Scale per member (~$897 each) | Only if ElevenLabs voices BeltGuard's spoken replies. As of 15:00 the browser's built-in speech says them. |
| Deepgram: Best Voice Use Case Built with Pipecat | up to $100K Deepgram credits + Nintendo Switches | No. We use Deepgram speech-to-text, but not Pipecat. |

**Recommendation:** enter **Overall** and name **Ambient + Voice** as the theme. Leave the sponsor tracks unticked unless the team adds a real Gemini or ElevenLabs step before 6 PM.

## Prizes (Devpost)

- **Best Overall:** AR glasses + $5,000 OpenAI API credits + 3 months ElevenLabs Pro + 1 year Superserve Pro + **final YC interview**
- **Second Overall:** drones + $2,500 OpenAI API credits
- **Third Overall:** Ray-Ban Meta glasses + $1,000 OpenAI API credits
- Sponsor tracks as in the table above.

## Deadlines and schedule (PT, today)

| Time | What |
|---|---|
| **6:00 PM** | **Submissions and commits close**, then dinner |
| 6:30 PM | Judging starts (round 1: "show what you built", in person) |
| 8:00–9:00 PM | Finals and closing |

⚠️ The team brief said "demos 7 PM". Vexo's page and the kickoff deck both say **judging starts at 6:30 PM**. Plan for 6:30.

## Countdown checklist (suggested; the user decides and does each step)

- [ ] **~5:00 PM:** freeze features. Record the 60–90 s video of the live flow on the real band (shot list in `demo/DEMO_SCRIPT.md`) and upload it unlisted.
- [ ] **~5:20 PM:** commit `vexora/` (not the brief or `.claude/`), update the root README, push, and open the repo in a private window to confirm it's public.
- [ ] **~5:30 PM:** one teammate logs in to Devpost, joins VexoHacks, pastes `SUBMISSION.md`, adds the video and repo links and the teammate, and **submits by 5:45 PM** (a 15-minute buffer).
- [ ] **6:30 PM:** demo station ready per the `demo/DEMO_SCRIPT.md` pre-flight: band 07 charged and live, backend running (`python -u backend/app.py`), clean shift, and the video ready as the fallback (no simulator).
