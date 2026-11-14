# BeltGuard UI brief

Source of truth for every agent working on the BeltGuard dashboard. Read this and look at both
images in `design/reference/` before writing UI code.

- `design/reference/live-shift-cv102.webp`: "Live Shift — CV-102" (conveyor illustration, Vexo band
  live charts, HAV gauge, active tool, defect, work order, proactive recommendation, shift timeline)
- `design/reference/overview-cv04.webp`: "Conveyor Maintenance & Vibration Safety" (breadcrumb,
  KPI row, conveyor route map with defect callout, HAV gauge with projection, recommendation,
  worker status, live sensor, draft work order, filterable event timeline)

## Product

BeltGuard is a conveyor maintenance and vibration safety dashboard for mining and industrial
maintenance teams. It combines conveyor inspection, worker hand-arm vibration (HAV) exposure
tracking, defect logging, work-order drafting, proactive recommendations and live data from a
Vexo band (a wrist band with a motion sensor) worn by the worker.

The story the screen must tell at a glance: a maintenance worker inspects a conveyor, finds a
defect, uses a power tool, BeltGuard tracks vibration exposure, drafts a work order, and warns
ahead of time if the next repair would push exposure too high.

## Design direction

Light, premium, calm, 2026 SaaS (Linear, Stripe, polished industrial software). Soft off-white
background, white cards, minimal borders, soft shadows, spacious layout, strong typography, soft
green / amber / red status coding, subtle mine and conveyor illustration.

Avoid: dark SCADA/HMI look, dense tables, clutter, harsh gradients, heavy borders, chart dumps.

### Palette (defined as tokens in `app/src/styles/tokens.css`; use the tokens, not raw hex)

| Token | Hex | Use |
|---|---|---|
| bg | `#F7F6F2` | app background |
| surface | `#FFFFFF` | cards |
| panel | `#F2F4EF` | secondary panels, sidebar tint |
| ink | `#16202A` | primary text |
| ink-2 | `#667085` | secondary text |
| ink-3 | `#98A2B3` | muted text |
| ok / ok-bg | `#5FB46B` / `#EAF6EA` | success |
| warn / warn-bg | `#F2A93B` / `#FFF3E4` | attention |
| bad / bad-bg | `#E56565` / `#FCECEC` | defect, danger |
| info | `#4D8DFF` | accent blue, walking events |
| sensor | `#22B8B5` | sensor teal |
| brand | `#D9901A` | BeltGuard gold |
| plum / plum-bg | `#8B6FD6` / `#F1ECFB` | work-order events |

Typography: Plus Jakarta Sans (headings, numbers) and Inter (body). Large bold page title, semibold
card titles, small muted labels, big KPI numbers.

## Layout (one dashboard screen)

1. **Sidebar**: brand "BeltGuard / Conveyor Safety & HAV Management"; nav: Overview, Inspections,
   Vibration (Vexo), Work Orders, Exposure (HAV), Conveyors, Events, Reports. Lightly tinted.
2. **Top bar**: breadcrumb `SITE / NORTH PIT / CV-04`, site selector, Live pill, date/time, search,
   notifications, avatar.
3. **Hero header**: title "Conveyor Maintenance & Vibration Safety", subtitle "Belt health,
   vibration risk, and worker exposure in one view", conveyor/shift/in-service pills, soft mine art.
4. **KPI row**: Belt Health 96%, Active Defects 1, Workers on Task 2, Vibration Risk Elevated,
   Current Exposure 85 pts.
5. **Conveyor route map (visual hero)**: conveyor across the panel, 5 stations (Head / Drive,
   Carry Idlers, Return Idlers, Transition, Tail / Take-up) with OK / attention / defect status,
   Station 3 / Return Idler 14 highlighted with a pulsing glow and an attached defect callout;
   Route View / List View toggle; station hover tooltip. Three.js only if it adds subtle depth;
   it must read as UI, not a game.
6. **Estimated HAV exposure**: semicircle gauge, 85 points. One reference image says "63% of
   action limit", which is wrong maths: show "85% of action value". Action value 100 points, limit value 400 points,
   projected next task 138 points. Always label it "Estimated".
7. **Recommendation (AI insight)**: "Next planned repair could raise exposure to 138 points —
   review required." plus explanation, equipment thumbnail, primary CTA "Review & Plan" /
   "Create Work Order", secondary "View Details".
8. **Ops cards**: Worker Status (two workers with exposure), Live Sensor (Vexo band:
   acceleration and gyroscope charts, RMS, connected/battery), Active Tool (Impact Wrench 01,
   HAV emission 12.5 m/s², 18 min today), Defect (Return idler bearing seized, needs replacement),
   Draft Work Order WO-4783 (asset, task, priority High, planned, Edit / Submit for Approval).
9. **Shift timeline**: horizontal, filter tabs (All, Defects, Vibration, Exposure, Work Orders),
   event chips coloured by kind: walking blue, inspection green, tool amber, work order purple,
   defect red, exposure neutral.

## States to design for

Band connected / disconnected; sensor live / stale; work order draft / submitted; station ok /
attention / defect; exposure below / above action value; current vs projected exposure.

## Polish

Hover elevation on cards, station tooltips, smooth transitions (Framer Motion), timeline filters,
clear CTA hierarchy, responsive down to laptop width (1280px) and readable on a tablet.

## Stack

React 18 + TypeScript + Vite, Tailwind CSS v3 with the tokens above, lucide-react icons, Recharts,
Framer Motion, Three.js (via @react-three/fiber) only for the conveyor hero if it helps.

MongoMation (https://github.com/zubair480/MongoMation) is a reference for 3D / polish ideas only.
Do not copy its branding, structure or any MongoDB-specific patterns.

## Data

All data comes from `app/src/data/` (typed mock shift data). No backend. Live Vexo band wiring
comes later and needs the team's band number: do not connect to any band.

## HAV maths (UK HSE points system)

points = 2 × a_hv² × hours (a_hv in m/s²). Check: 2.5 m/s² for 8 hours = 100 points.
Use the helper `havPoints(a_hv, minutes)` in `app/src/lib/hav.ts`; do not reimplement it.
Action value: 100 points. Limit value: 400 points.
