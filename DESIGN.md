---
name: Jophiel
description: Her parts as electric neurons in a star field (owner-only System page and its part pages).
colors:
  cognition-cyan: "#3FD3F0"
  mouth-green: "#4BE37A"
  mind-rose: "#FF5C8A"
  hands-amber: "#F5D23A"
  memory-orange: "#FF9A3C"
  observation-blue: "#6E8CFF"
  learning-violet: "#B77DFF"
  core-white: "#F4FFFE"
  star-white: "#FFFFFF"
  star-cool: "#C8DCFF"
  field-black: "#040506"
  chrome-glass: "#0A0D10"
  hairline: "#151A1F"
  entry-rule: "#12171B"
  reticle-rule: "#2A3138"
  reticle-tick: "#4A545D"
  reticle-hover: "#5A6670"
  ink-title: "#F1F4F6"
  ink-bright: "#EEF1F3"
  ink: "#E9EDF0"
  ink-heading: "#DDE3E8"
  ink-body: "#D5DBE0"
  ink-leader: "#C9D1D8"
  ink-list: "#C5CDD5"
  ink-secondary: "#AEB7BF"
  ink-muted: "#8C97A3"
  ink-time: "#7D8790"
  fail-red: "#FF4136"
  error-text: "#FF8A80"
typography:
  display:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.02em"
  node-label:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 560
  body:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.625
  body-small:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.625
  label:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
  meta:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  time:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    fontFeature: "tnum"
  sub-label:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 400
rounded:
  full: "9999px"
  none: "0px"
spacing:
  gutter-phone: "16px"
  gutter-wide: "24px"
  page-x-phone: "20px"
  page-x-wide: "32px"
  rail: "288px"
  rail-gap: "56px"
  rail-block: "28px"
  entry-y: "16px"
  section: "48px"
components:
  chrome-button:
    backgroundColor: "{colors.chrome-glass}"
    textColor: "{colors.ink-heading}"
    rounded: "{rounded.full}"
    size: "36px"
  chrome-button-hover:
    textColor: "{colors.star-white}"
  back-link:
    textColor: "{colors.ink-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "6px 8px"
  back-link-hover:
    textColor: "{colors.star-white}"
  part-page:
    backgroundColor: "{colors.field-black}"
    textColor: "{colors.ink-heading}"
    rounded: "{rounded.none}"
    width: "1152px"
    padding: "32px 32px 96px"
  part-rail:
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-small}"
    width: "{spacing.rail}"
  sub-part-link:
    textColor: "{colors.ink-body}"
    typography: "{typography.body-small}"
  sub-part-link-hover:
    textColor: "{colors.star-white}"
  sub-part-unlinked:
    textColor: "{colors.ink-muted}"
    typography: "{typography.body-small}"
  timeline-entry:
    textColor: "{colors.ink-heading}"
    typography: "{typography.body}"
    padding: "16px 0"
  specimen-label:
    textColor: "{colors.ink}"
    typography: "{typography.title}"
    padding: "24px"
  page-link:
    textColor: "{colors.ink-muted}"
  page-link-hover:
    textColor: "{colors.star-white}"
---

# Design System: Jophiel

## Overview

**Creative North Star: "The Live Field"**

Jophiel's parts are electric neurons hanging in a night sky. Each part is a charged node: a white-hot core in its own color, a thin ring around it that fills with light over the part's beat and then discharges in one flare, and living arcs of electricity that keep re-forming around it while it's alive, cracking into a burst of bolts and sparks each time it discharges. Between the nodes run conducting fibres, hairlines inside a faint glow that blend from one part's color to the other's. When one part really hands work to another, a spike of light runs the fibre and the core it reaches flares. Behind everything is a still star field at infinity, about a third of it tinted with the parts' own colors so the sky belongs to the same system. Everything the visitor reads on the field besides the nodes is quiet instrument overlay: a specimen label with live counts and the last signal top left, a magnification readout on a tick ruler bottom left, round zoom controls bottom right. The world refuses the category default for "system status": no node-graph dashboard of rounded boxes, neon edges and a stats sidebar.

The field is full-bleed and spatial, not paged. It is a map you drag, wheel, pinch and key around, from ×1 (the whole field) to ×6; past ×1.9 each node's sub-cores resolve inside its ring, fully legible by ×2.7, while the core shrinks to make room. Clicking a node is a jump, not a panel: the stars streak out from the node like a jump to lightspeed, the camera flies in with the node held on its path, the node's light floods the screen, and the part opens as its own page at `/system/:part`. The page sits over the field it was jumped into, the stars still faintly there: a sticky left rail with the live node, its name, state, role, sub-parts, workers and hand-offs, beside a readable timeline of the part's real recent records. Esc, the back button or "← System" plays the jump in reverse. The density is low on the field and comfortable, editorial-readable, on the part page.

**Scope.** This world is built only on the owner-only System page (`/system`) and its part pages (`/system/:part`). The rest of the app (chat, voice dashboard, login, integrations, predict) predates it, uses a light pale-blue and lavender look written as hardcoded Tailwind hexes, and is an **unrecorded incumbent**: this file records no tokens for it, and nothing here should be read as describing it. The older dark `@theme` block at the top of `frontend/src/index.css` (Space Grotesk, teal accent) is not this world either; the System page does not use it.

**History and divergences.** The first build was a fluorescence-microscope look; the electric-neuron world replaced it so pulses read as current. This refresh replaces the right-hand details sheet (and its bottom-sheet form on phones) with the jump into a part page. The part colors, type, chrome and node anatomy carried over. There is still no physical scale bar: magnification is a tick ruler marked at ×1, ×2, ×4 and ×6, because a µm unit would be a made-up claim. The direction contract says fibres thicken with zoom along with filaments and rings; the build thickens only filaments and rings, and fibres stay a fixed 1.1px hairline. The build is recorded here.

**Key Characteristics:**
- Night field (#040506) of star points, about a third tinted in part colors, a few twinkling, fixed at infinity and moving at half the pan.
- One color per part, kept everywhere that part appears, on the field and on its page.
- Nodes as charge: white-hot core, charging ring, living arcs of electricity re-forming around it, a burst of bolts and sparks on the state's beat.
- Fibres as conductors: a 1.1px line in a 6px glow, sender-to-receiver gradient; real hand-offs run them as tapered spikes.
- A node opens by a jump: streaking stars, a fly-in, the part's light flooding the screen, then its own page; back plays it in reverse.
- Part pages are a sticky rail plus a rule-divided timeline with a time column, never cards.
- Overlay chrome in Schibsted Grotesk with tabular figures; round glass zoom buttons.
- Reduced motion gets a still field and a jump without animation.

## Colors

A black sky lit by seven part colors and stars in white and the same part colors, with a cool grey ink ramp for text and one red reserved for failure.

### Primary
Seven part colors, one per part. They are equal in rank; none is "the brand accent".
- **Cognition Cyan**: the central, largest node. Also the page's default focus ring and text-selection tint (cyan at 32%).
- **Mouth Green**, **Mind Rose**, **Hands Amber**, **Memory Orange**, **Observation Blue**, **Learning Violet**.

On the field each part color paints its node's core (a radial gradient from pure white through Core White at 38% into the color at 62%), its halos, arc glow and charging ring, its sub-cores, its state word under the label, its end of every fibre it touches, the tail of every pulse it sends, and its share of the tinted stars and warp streaks. In the jump it is the flood of light (white at the centre, the part color at 16%, the color at 33% alpha at 34%, gone by 62%). On its page it is the live node glyph, the state word, the sub-part dots (with an 8px glow), every timeline meta line, the reasoning rule (at 40%) and the page's focus ring. A hand-off line in the rail takes the *other* part's color for its dot.

### Neutral
- **Star White / Star Cool**: the star field. About 34% of stars carry a part color, about 8% are Star Cool, the rest Star White. Star White is also every pulse's head, and the chrome's hover ink.
- **Core White**: the inner light of every core and the middle of every pulse.
- **Field Black**: the sky; the stroke ring around every field label, so labels read over glow and fibres; the vignette; and the part page's veil (Field Black at 86% top left deepening to 94%, so the stars stay faintly visible behind the page).
- **Chrome Glass** (at 90% alpha): the round zoom buttons.
- **Hairline**: the rule above each rail block on a part page.
- **Entry Rule**: the rule between timeline entries; a step darker than Hairline so a long timeline stays quiet.
- **Reticle Rule / Reticle Tick / Reticle Hover**: chrome button borders, the ruler line and the page's scrollbar thumb; ruler ticks; chrome border on hover and the "Esc" hint beside "System".
- **Ink ramp**, brightest to faintest: Ink Title (the part's name on its page), Ink Bright (timeline section headings), Ink (specimen label, node labels, magnification readout), Ink Heading (rail block headings, timeline entry text, chrome icons), Ink Body (linked sub-parts, sub-core labels), Ink Leader (sub-core leader lines, at 35%), Ink List (worker names, hand-off lines, code), Ink Secondary (status line, role line, back link, reasoning), Ink Muted (last-signal line, page links, sub-parts with no section, empty states, down/off state words, dropped or stale entries), Ink Time (relative times).

### Named Rules
**The One Color Rule.** A part has exactly one color, and that color appears wherever the part does: core, ring, arc glow, fibre ends, pulse tail, sub-cores, the flood of the jump, and on its page the glyph, state word, sub-part dots, meta lines, reasoning rule and focus ring. A part is never given a second hue, and a part color is never borrowed for anything that isn't its part. The shared uses are Cognition cyan as the map's default focus ring, and the tinted stars, which only ever carry the seven part colors.

**The Red Means Failing Rule.** Fail Red (#FF4136) is reserved for a failing part: its ring and its state word, on the field and on its page. Error prose (a failed record, a worker error, a load failure) uses the softer Error Text (#FF8A80). No other surface uses red.

**The Light Is State Rule.** State is told by charge, not badges: the ring charges and discharges on the state's period (alive 2.8 s, working 1.05 s, partly down 4.4 s, failing 1.6 s); the node dims to 72% when partly down, 45% unknown, 28% down and 14% off on the field (the page glyph uses 75%, 45%, 30% and 16%).

## Typography

**Display Font:** Schibsted Grotesk Variable (self-hosted via `@fontsource-variable/schibsted-grotesk`, with ui-sans-serif, system-ui fallback)
**Body Font:** the same
**Label/Mono Font:** none distinct in the world; figures use tabular numerals (`tnum`)

**Character:** one grotesk, tight-tracked for names and plain for everything else; small instrument sizes on the field, a readable 15px on the part pages.

### Hierarchy
- **Display** (600, 30px, 1, -0.025em): the part's name at the top of its page rail.
- **Headline** (600, 19px, 1.25, -0.015em): timeline section headings on a part page ("Hard thoughts", "What she said").
- **Title** (600, 19px, 1, -0.02em): the specimen label "Jophiel · live field"; " · live field" drops to 400 in Ink Muted.
- **Node Label** (560, 15px at ×1, 650 when selected): part names on the field. It is drawn in field units and grows slower than the zoom (size × magnification^0.22), so labels stay readable without ballooning. The state word below it is 72% of that size in the part color.
- **Body** (400, 15px, 1.625, max 68ch): timeline entry text.
- **Body Small** (400, 14px, 1.625): the role line (max 260px on wide screens), sub-part names, the state word on the page (500, in the part color), empty states, "Her reasoning".
- **Label** (600, 13px): rail block headings ("Inside", "Running as", "Hand-offs"); at 400, the status line, page links, the back link, worker and hand-off lines.
- **Meta** (400, 12.5px): timeline meta lines in the part color (or Error Text), joined with " · ".
- **Time** (400, 12px, tabular figures): the timeline's time column, hand-off ages, the last-signal line, the ruler's hint text.
- **Sub-label** (400, 11.5px at ×1, scaled by magnification^0.12): sub-core names once they resolve.

### Named Rules
**The Sentence Case Rule.** All overlay and page text is sentence case and untracked (beyond the negative tracking on names and headings). No uppercase tracked labels.

**The Tabular Figures Rule.** Any number that updates live (counts, magnification, "1 s", "46 s", "7 min") is set in tabular figures so it doesn't jitter.

## Layout

The System page is fixed to the viewport (`inset: 0`, no page scroll). The field fills it; wheel zoom is captured so the page never scrolls. A part page is a layer over it that scrolls on its own.

- **Field.** Seven parts at fixed positions in a 1600 × 1000 field: Cognition central and largest, Mouth right, Memory beneath, Mind upper left, Hands lower right, Observation top, Learning lower left. The fitted view frames the nodes and their labels (not the empty field), with 70px clear at top and bottom (110px on top in portrait) for the overlay, at 96% of the available fit.
- **Portrait.** When the viewport is more than 1.15× taller than wide, the field is turned on its side (x and y swapped) so every node keeps its neighbours and a phone is filled; labels move above or below.
- **Overlay.** Specimen label, status, last signal and page links top left; magnification ruler bottom left; zoom buttons bottom right. Gutters 16px, 24px from 640px up. The top-left text fades out (300ms) as soon as a jump begins; the bottom chrome fades while the light is up or a page is open; the map's nodes and fibres fade out while a page is open.
- **Part page.** Centred, at most 1152px wide, 20px sides on phones and 32px from 640px, 20px / 32px on top, 96px at the bottom. From 1024px it is two columns: a 288px rail that sticks 32px from the top, a 56px gap, and the timeline. Below 1024px the rail stacks above the timeline, the glyph sits beside the name, and sub-parts wrap into a row.
- **Rail rhythm.** Back link, then glyph (132px) and name, the role line 16px below, then blocks 28px apart, each under a Hairline rule with 16px above its heading and 10px below.
- **Timeline rhythm.** Sections 48px apart (sub-part links scroll to them, 32px clear of the top). Entries are a two-column grid: a 56px time column (72px from 640px), a 16px gap, then meta and text; 16px above and below each entry.
- **Navigation.** Pan with drag or arrow keys (80px a press), zoom with wheel, pinch, keys and the ×1.6 buttons, 0 to fit. View moves animate over 420ms with an exponential ease-out, zoom interpolated in log space. A part page lives at its own address (`/system/mind`), so back and forward work, and opening that address directly lands inside the node so going back still surfaces.

## Elevation & Depth

Depth is optical, not material. The nodes are the near plane. The stars are at infinity: painted once to a canvas covering the field and a 2.6× margin, one star per 1,900px² (0.35–1.95px, alpha 0.18–0.93, the brightest with a soft glow out to 3.5× their radius), plus 34 twinkling points (1.2–2.8px, 3–8 s); they never grow with the zoom and move at half the pan. A vignette darkens the field's edges and the top-left corner behind the overlay text. Light, not shadow, is what lifts a node: a halo out to 2.2× its radius and a discharge halo that flares on each beat. A part page is not lifted either: it is a veil of Field Black over the field it came from, with no border and no shadow.

### Shadow Vocabulary
- **Part dot glow** (`box-shadow: 0 0 8px <part color>`): sub-part dots in the rail.
- **Twinkle glow** (`box-shadow: 0 0 4px rgba(255,255,255,0.8)`): a twinkling star.

### Named Rules
**The Stars At Infinity Rule.** The sky never scales with the zoom and never carries meaning; only nodes, fibres and pulses are signal. Star tints are the parts' colors for belonging, not for state. Nothing sits on a card above the field; a part page replaces the view rather than floating over it.

## Shapes

Nodes are concentric and electric. A core disc (36% of the node's radius, shrinking to 22% as sub-cores resolve), a charged ring at 80% of the radius, and electricity: living arcs that leave the ring and re-form every 110 ms (a lightning path by midpoint displacement, jag 0.42, sometimes forking; a white-hot 0.9px middle inside a 3.2px glow in the part color at 28%), one to three at a time when alive, three to five when working, none or two when partly down, two to four in Fail Red when failing, none when down or off. On each discharge of the beat, and on each arriving pulse, a burst cracks out: four to six bolts all round (gone in 170 ms) and nine sparks flying off and fading (380–580 ms). Under reduced motion one set of arcs stays still. Arc and ring strokes thicken with magnification^0.5 so they keep their proportion to the node when zoomed in. Sub-cores orbit inside the ring at 56% of the radius, evenly spaced and turning together (one turn in about a minute), each bobbing a little on its own; no spokes. Each sub-core's label rides its bearing just outside the ring, turned back so it stays upright. Fibres are cubic curves bowed by a seeded amount, tucked a little inside each node so drift never shows a gap. Nodes drift a few field units in 19–27 s loops.

The chrome and pages are the opposite: geometric and minimal. Zoom buttons, the back link's hover area and dots are full circles; the part page is square, edge to edge; rules are 1px lines; the ruler is a 1px line with ticks. Icons are 16px line drawings at a 1.5 stroke with round caps and joins (plus, minus, fit corners, the back chevron, and the disclosure chevron, which turns 90° over 160ms).

## Components

### Buttons (chrome)
Round glass instruments, quiet until touched.
- **Shape:** full circle, 36px.
- **Default:** Chrome Glass at 90% alpha, a 1px Reticle Rule border, Ink Heading icon.
- **Hover / Focus:** border to Reticle Hover, icon to white (150ms). Focus is a 1.5px cyan outline, 3px offset.
- **Disabled:** 35% opacity at the zoom limits.

### Back link
"← System" at the top of the rail: back chevron, "System" in Ink Secondary (13px), then "Esc" in Reticle Hover, which brightens to Ink Muted on hover while "System" turns white. Borderless, a pill hover area; it plays the jump in reverse.

### Nodes (signature)
- **Body:** halo (2.2r); a discharge halo (2r) and a white core flash that fire at 88% of each period with an expo ease-out; living arcs and bursts (above); a faint base ring (1.2px at 30%) under the charging ring (2px, stroke opacity climbing 0.18 → 1 over 86% of the period, then dropping as the flare fires); the core gradient. Line widths are multiplied by magnification^0.5.
- **States:** partly down breaks the ring into dashes (half and a third of the ring radius); failing turns the ring Fail Red, 2.6px, pulsing on a 1.2 s cycle; down holds a still ring at 35%; off draws a dotted ring and no core; selected adds a thin 1px ring at 1.22r. Hit target is an invisible 1.4r circle.
- **Label:** name plus state word, ringed in Field Black (`paint-order: stroke`, 0.32em), placed 1.5r + 8 from the centre on its side, past where the arcs usually reach. Keyboard focus underlines the name instead of drawing an outline.
- **Node glyph:** the same node drawn at a fixed size (132px on a part page), with the same living arcs and bursts, ring, core and beat, so the page carries the live part rather than an icon of it.

### Fibres and pulses
- **Fibre:** a 6px glow at 12% under a 1.1px line, both non-scaling and in a sender-to-receiver gradient; 70% opacity when both ends run, 18% otherwise. Every fibre is a real hand-off.
- **Pulse:** each real signal runs its fibre in 760ms (`cubic-bezier(0.45, 0, 0.25, 1)`) as a tapered spike: a white head (2.6px, up to 22 field units long), a Core White middle three times as long (1.8px, 50%), and a tail in the sender's color seven times as long (1.2px, 25%), while the whole fibre brightens to 55% and lets go. On arrival the receiving node flares once (700ms, scaling 0.7 → 1.25 and fading). Opening the page replays the last 90 s of signals 160ms apart, then goes live.

### The jump (signature)
- **Aim (420ms):** first the camera turns (pans at the same zoom, ease-in-out) so the clicked node sits in the middle of the screen.
- **In (900ms):** then it flies straight in to 22× the fitted zoom with an ease-in, the node held in the middle; the sky itself warps: the painted stars step aside and every star on screen is redrawn at its own place, size and color, then stretched into a streak pulling away from the node (farther stars streak longer);  the part's light stays hidden for the first 45%, then floods the screen from the centre by the end. The page then fades in over 380ms from 103% scale with an expo ease-out, and the light settles from full to 40% under it over 700ms.
- **Out (800ms, then 480ms):** the camera zooms back out with the node still in the middle, the light shrinking to nothing and the streaks shrinking back into the same stars; then it turns back to the view it left (ease-in-out).
- **Reduced motion:** the view jumps straight inside the node and the page appears; nothing streaks or floods.

### Part page
- **Rail:** back link; node glyph with the Display name and the state word (part color, Fail Red when failing, Ink Muted when down or off); role line; "Inside" lists the sub-parts with a glowing part-color dot each, and only those with a section on the page are links (Ink Body, white on hover; the rest are plain Ink Muted text); "Running as" lists workers with their state and seconds since seen, worker errors in Error Text; "Hand-offs" lists up to eight recent signals into and out of the part, each with the other part's dot and an age.
- **Timeline:** headline sections, each a list of rule-divided entries (Entry Rule): time column on the left, a part-color meta line, then the text. Dropped or stale entries go Ink Muted; failed ones put their meta in Error Text. Reasoning, where a record has it, folds into a "Her reasoning" disclosure with a part-color left rule. Empty sections say so plainly in Ink Muted.
- **Focus:** the timeline takes focus on open; Escape closes; focus rings inside use the part color. A drawing failure shows a plain Error Text line and leaves Esc working.

- **Records that open:** an entry can carry the rest of its record behind a disclosure ("Whole record", "The three reports", "What it saw and what was applied"): label/value lines in Ink Muted / Ink Body, raw records as indented text on a near-black well, scrolled when long. A "Trace" pill (hairline ring, 12px) sits at the end of an entry's meta line when the record can be followed through her parts.
- **Hands:** the page opens on "Goal and plan": the goal in 16px Ink Strong, the plan's steps with a filled amber dot for done, a lit amber ring for the active one and a hollow ring for the rest, then the user's rules and what code counted. Her hands' decisions keep three reports apart: her claim, the pack's report, her state before and after.

### Log drawer
A 400px panel on the right of the field (full width below 1024px), Surface Night at 92% with a backdrop blur and a hairline left rule; on a wide screen the field refits to the space left. Three tabs as quiet pills: **Watch** (her hands' goal pinned on top in a hairline card, then one log of what she thought, what memory captured and what her hands ran, each row with its part's dot and colour, filterable by Thinking / Memory / Hands), **Signals** (every hand-off, newest first, filterable by part, with a Trace pill where the signal names a turn or decision) and **Errors** (worker errors, invalid decisions, failed executions, capture gaps; the tab shows the count of the last ten minutes in Error Text). Clicking a row's part opens that part's page at the row's section. Closed, it is one "Log" pill top right with the last signal's colour dot. Opening state and tab are remembered per browser.

### Trace
A sheet sliding in from the right over any page (max 760px), one vertical line of steps, each a part-colour dot and meta line (part, trigger, clock time, seconds, model), the step's title ("Student 2 decided", "The skill ran"), its answer in a line, and "What it was given" / "What it answered" disclosures. What was never saved is listed under "Missing from the record" in amber, never drawn as a step. Esc closes the trace only.

### Magnification ruler
Readout "×1.0" (13px, 500, tabular) beside a 112–160px line with ticks at ×1, ×2, ×4 and ×6 on a log scale; a bright fill and a 9px cursor mark the current magnification; a hint says "zoom in to see inside each part" until sub-cores resolve, then "inside view".

### Navigation
Page links under the status and last-signal lines: 13px, Ink Muted, white on hover. No tab bar on this page.

## Do's and Don'ts

### Do:
- **Do** give every part its one color and carry it to its ring, arcs, fibres, pulse tails, sub-cores, its jump and its page (The One Color Rule).
- **Do** show state as charge: the ring's beat, the discharge flare, dimming, a broken ring, a red ring.
- **Do** keep the field full-bleed on Field Black (#040506) with the star field at infinity behind the nodes.
- **Do** run a pulse only for a real hand-off between two parts.
- **Do** set live numbers in tabular figures and ring field labels in Field Black.
- **Do** open a part by jumping into it and close it by playing the jump in reverse; give each part page its own address.
- **Do** show a part's records as a timeline: a time column, a part-color meta line, rule-divided entries.
- **Do** link a sub-part in the rail only when it has a section on the page.
- **Do** stop charging, discharge, crackle, twinkle, drift and the failing pulse under `prefers-reduced-motion`, let pulses land as a bare arrival, and jump view changes and dives instead of animating them.

### Don't:
- **Don't** draw parts as rounded boxes, neon edges or a stats sidebar: the node-graph dashboard is the default this world refuses.
- **Don't** put records in rounded cards on this page; part pages are rule-divided timelines.
- **Don't** bring back a side sheet or modal card for a part; the jump and its page replace it.
- **Don't** add a physical scale bar or µm units; magnification is the ×1–×6 tick ruler.
- **Don't** use red for anything but failure.
- **Don't** let the stars scale with the zoom or carry state, or tint them with anything but the part colors.
- **Don't** set uppercase, letter-spaced labels on the field or pages (The Sentence Case Rule).
- **Don't** extend these tokens to the rest of the app; the light incumbent there is a separate, unrecorded look.
