# BeltGuard design system v2 — shadcn/ui, neutral, focused (replaces v1)

## v3 (13:46) GUIDED FLOW — overrides the "Layout" section below
Team feedback: "which data is relevant?", "one thing at a time", "zoom-in animation at the start, then leave it at rest", "serious business, not gamification", "calmer, clearer data viz: boxes with a stat". The user: walk the customer through step by step, minimise cognitive load.
- **14:03 PRODUCT FOCUS (user): the #1 feature is TOOL CHECKOUT → LIVE VIBRATION MONITORING → EXPOSURE VERDICT.** New stepper: **1 Unlock tool** (pick from the tool crib: drill, hammer drill, impact wrench, angle grinder, power jack, breaker hammer; each card shows its vibration magnitude m/s² + source) · **2 Assigned** ("Hammer drill assigned to Technician W-01 at 14:05", one sentence on what's monitored) · **3 Using tool** (live wrist vibration, trigger-time counter, points rising in real time) · **4 Exposure verdict** (one big, unmistakable state: "Within the safe limit" / "Action value reached: take a break or rotate" / "Limit reached: stop using vibrating tools today", plus minutes left to each value) · **5 Return tool** (session summary). Inspection, defect and work order become a secondary "Inspection" tab. The old stepper below is superseded.
- (superseded) Stepper: 1 Inspect · 2 Report defect · 3 Work order · 4 Tool use · 5 Exposure · 6 Next job. Done steps get a check; the current step is highlighted. It auto-advances on live events (station selected → defect created → work order drafted → tool session → exposure record → recommendation). Done steps are clickable to revisit. One sentence of context sits above it.
- **Main**: the 3D scene (left ~62%). **Step panel** (right ~38%) shows ONLY the current step: a plain-words title, 2–3 **stat boxes** (label above, big Geist Mono number, unit), one short sentence on why it matters, and ONE primary action.
  1. Inspect: station list (5) with status; stats: Stations inspected x/5, Defects found.
  2. Report defect: mic button + typed input; transcript → stats: Component, Condition, Severity.
  3. Work order: title, priority, "Isolation must be verified by an authorized person"; steps collapsed.
  4. Tool use: tool select; live state (Idle / Tool running); stats: Trigger time, Confidence, Tool magnitude (m/s²).
  5. Exposure: big points number + 0–400 bar with labelled 100/400 ticks; stats: Today, Minutes to action value, Minutes to limit.
  6. Next job: planned repair → stats: Projected points, Added points; recommendation + "Supervisor review required".
- Everything else (full signal charts, full timeline, raw data) goes in a **Details** drawer, closed by default.
- At most ONE accent colour on screen at a time. No decorative badges.
- **3D**: a single intro zoom on load (~2.5 s, eased), then the camera RESTS. No auto-orbit, no idle spinning. On step change, ease the camera (~1 s) to the relevant station. Only the current station's badge is prominent; others small and muted. Realistic and calm, not game-like.

Team feedback on v1 (13:35): the beige/brown "paper" palette reads as Claude-coded, the screen is too cluttered ("I don't know what to focus on"), use shadcn, the 3D is the part people like.

## Never
Beige, cream, brown, tan, wood, "paper" backgrounds; orange text walls; dark/neon/glow; emoji; monospace body text; ALL-CAPS overlines; more than one focal point per region; more than three status colours visible at once.

## Tokens (shadcn/ui "zinc" theme)
```css
--background:#FFFFFF; --muted:#F4F4F5; --card:#FFFFFF;
--foreground:#09090B; --muted-foreground:#71717A;
--border:#E4E4E7; --input:#E4E4E7; --ring:#A1A1AA;
--primary:#18181B; --primary-foreground:#FAFAFA;
--destructive:#EF4444; --warning:#F59E0B; --success:#10B981; --info:#3B82F6;
--tool:#F97316;   /* only for "tool running" in the 3D scene and its badge */
--radius:8px;     /* cards rounded-lg; buttons/inputs 6px */
--shadow-sm:0 1px 2px 0 rgb(0 0 0 / .05);
```
Font: **Geist** for UI, **Geist Mono** for numbers (Google Fonts: `family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500`). 14px base, headings 600 with `letter-spacing:-0.01em`, KPI numbers 40px Geist Mono 500 `tabular-nums`.

## Components (shadcn look, hand-written CSS is fine)
Card (header = title 16/600 + description 13 muted), Badge (secondary / outline / destructive / success), Button (default black, outline, ghost; h 36px, px 16px), Tabs (muted rounded list, white active pill), Separator, Progress, Select, Input, Sheet (right drawer) for details.

## Layout — one thing to look at
- Top bar 56px: logo + "BeltGuard" · asset "CV-102" · station · band badge ("Band 07 · live" success / "simulated" secondary) · clock.
- Main: **3D scene is the hero** — left ~64% width, full height.
- Right rail ~36%: exactly three cards, stacked:
  1. **Now** — technician, station, activity badge + confidence, active tool select, one live accel sparkline.
  2. **Exposure today** — big number + `pts`, Progress bar 0–400 with ticks at 100 (action) and 400 (limit), one caption "Screening estimate — not a certified HAV measurement".
  3. **Next action** — the single most important item (unresolved tool session to confirm, or the newest work order, or the planner recommendation) with one primary button.
- Below the 3D: Tabs — **Inspection** (defect + work-order draft, typed report, mic) · **Signals** (full accel/gyro charts) · **Timeline** (the lane timeline) · **Plan** (repair planner).
- Whitespace over boxes. 24px gutters, 16px card padding.

## 3D scene (mongomation style)
Port the look of mongomation `factory3d.js`: ACES filmic tone mapping (exposure 1.05), sRGB output, PCF soft shadows, RoomEnvironment PMREM environment, background `0xf6f7f9` with matching fog, hemisphere `0xffffff/0xdfe3e8` 0.9 + sun 2.2. Materials: aluminium `0xdfe3e8` (metal .45 rough .35), dark aluminium `0xb4bac2`, rollers `0xeef0f3` (metal .55 rough .25), safety yellow `0xf2c230` for guards, rails and floor markings, ink `0x1b1d21`, floor `0xf4f5f7` (rough .95) with a drafting-paper grid (minor `0xe3e6ea`, major `0xd3d8de`). Belt = procedural rubber with cleats, scrolling. Ore = graphite greys `0x3a3f45`–`0x5b6066` (never brown). Technician = white helmet, hi-vis `--tool` vest. Station rings: zinc-300, selected zinc-900, defect red-500 pulsing. Labels = CSS2D shadcn badges (white, 1px `--border`, 6px radius, Geist 12px). Camera FOV 32, three-quarter view, gentle orbit, parts rise in on load.
