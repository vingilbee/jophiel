---
name: Jophiel
description: Her parts as electric neurons in a star field (owner-only System page).
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
  star-warm: "#FFECD6"
  field-black: "#040506"
  slide-sheet: "#07090B"
  chrome-glass: "#0A0D10"
  hover-wash: "#151A1F"
  hairline: "#151A1F"
  reticle-rule: "#2A3138"
  reticle-tick: "#4A545D"
  reticle-hover: "#5A6670"
  dark-pixel: "#3A434B"
  ink-bright: "#EEF1F3"
  ink: "#E9EDF0"
  ink-heading: "#DDE3E8"
  ink-body: "#D5DBE0"
  ink-leader: "#C9D1D8"
  ink-list: "#C5CDD5"
  ink-secondary: "#AEB7BF"
  ink-role: "#9AA5B1"
  ink-muted: "#8C97A3"
  ink-time: "#7D8790"
  fail-red: "#FF4136"
  error-text: "#FF8A80"
typography:
  headline:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.02em"
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
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.625
  label:
    fontFamily: "Schibsted Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
  meta:
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
  panel-x-phone: "20px"
  panel-x-wide: "24px"
  entry-y: "12px"
  section: "32px"
components:
  chrome-button:
    backgroundColor: "{colors.chrome-glass}"
    textColor: "{colors.ink-heading}"
    rounded: "{rounded.full}"
    size: "36px"
  chrome-button-hover:
    textColor: "{colors.star-white}"
  panel-close:
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.full}"
    size: "36px"
  panel-close-hover:
    backgroundColor: "{colors.hover-wash}"
    textColor: "{colors.star-white}"
  part-panel:
    backgroundColor: "{colors.slide-sheet}"
    textColor: "{colors.ink-body}"
    rounded: "{rounded.none}"
    width: "460px"
    padding: "24px"
  feed-entry:
    textColor: "{colors.ink-body}"
    typography: "{typography.body}"
    padding: "12px 0"
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

Jophiel's parts are electric neurons hanging in a night sky. Each part is a charged node: a white-hot core in its own color, a thin ring around it that fills with light over the part's beat and then discharges in one flare, and fine jagged filaments that crackle while it's alive. Between the nodes run conducting fibres, hairlines inside a faint glow that blend from one part's color to the other's. When one part really hands work to another, a spike of light runs the fibre and the core it reaches flares. Behind everything is a still star field at infinity. Everything the visitor reads besides the nodes is quiet instrument overlay: a specimen label with live counts and the last signal top left, a magnification readout on a tick ruler bottom left, round zoom controls bottom right. The world refuses the category default for "system status": no node-graph dashboard of rounded boxes, neon edges and a stats sidebar.

The field is full-bleed and spatial, not paged. It is a map you drag, wheel, pinch and key around, from ×1 (the whole field) to ×6; past ×1.9 each node's sub-cores resolve inside its ring, fully legible by ×2.7, while the core shrinks to make room. Clicking a node opens a plain dark sheet on the right (a bottom sheet on phones) that carries that part's color in its dot, its border and its meta lines, and lists the part's real recent records as quiet rule-divided entries. The density is low on the field and comfortable in the sheet.

**Scope.** This world is built only on the owner-only System page (`/system`). The rest of the app (chat, voice dashboard, login, integrations, predict) predates it, uses a light pale-blue and lavender look written as hardcoded Tailwind hexes, and is an **unrecorded incumbent**: this file records no tokens for it, and nothing here should be read as describing it. The older dark `@theme` block at the top of `frontend/src/index.css` (Space Grotesk, teal accent) is not this world either; the System page does not use it.

**History and divergences.** This refresh replaces the first build's fluorescence-microscope look (somata, dendrites, beaded axons, blurred tissue) with the electric-neuron world, at the user's request, so pulses read as current. The part colors, type, chrome and panel carried over unchanged. There is still no physical scale bar: magnification is a tick ruler marked at ×1, ×2, ×4 and ×6, because a µm unit would be a made-up claim. The overlay keeps the instrument words "specimen label" and "live field".

**Key Characteristics:**
- Night field (#040506) of white star points, a few twinkling, fixed at infinity and moving at half the pan.
- One color per part, kept everywhere that part appears.
- Nodes as charge: white-hot core, charging ring, crackling jagged filaments, discharge flare on the state's beat.
- Fibres as conductors: a 1.1px line in a 6px glow, sender-to-receiver gradient; real hand-offs run them as tapered spikes.
- Overlay chrome in Schibsted Grotesk with tabular figures; round glass zoom buttons.
- Reduced motion gets a still field.

## Colors

A black sky lit by seven part colors and white stars, with a cool grey ink ramp for the overlay and one red reserved for failure.

### Primary
Seven part colors, one per part. They are equal in rank; none is "the brand accent".
- **Cognition Cyan**: the central, largest node. Also the page's default focus ring and text-selection tint (cyan at 32%).
- **Mouth Green**, **Mind Rose**, **Hands Amber**, **Memory Orange**, **Observation Blue**, **Learning Violet**.

Each part color paints its node's core (a radial gradient from pure white through Core White at 38% into the color at 62%), its halos, filaments and charging ring, its sub-cores and their spokes, its state word under the label, its end of every fibre it touches, the tail of every pulse it sends, and the panel's dot, border (at 33% alpha), meta lines and focus ring.

### Neutral
- **Star White / Star Cool / Star Warm**: the star field. Most stars are white; about one in eight is cool-tinted and one in sixteen warm. Star White is also every pulse's head, and the chrome's hover ink.
- **Core White**: the inner light of every core and the middle of every pulse.
- **Field Black**: the sky and the stroke ring around every label, so labels read over glow and fibres. The vignette is Field Black too.
- **Slide Sheet**: the details panel.
- **Chrome Glass** (at 90% alpha): the round zoom buttons.
- **Hairline / Hover Wash**: dividers between entries in the panel; the close button's hover fill.
- **Reticle Rule / Reticle Tick / Reticle Hover**: chrome button borders and the ruler line; ruler ticks; chrome border on hover.
- **Dark Pixel**: a worker dot that is down.
- **Ink ramp**, brightest to faintest: Ink Bright (panel title), Ink (specimen label, node labels, magnification readout), Ink Heading (section headings, chrome icons), Ink Body (entry text, sub-core labels), Ink Leader (sub-core leader lines, at 35%), Ink List (worker names, code), Ink Secondary (status line, reasoning), Ink Role (the part's one-line role), Ink Muted (last-signal line, page links, empty states, down/off state words), Ink Time (relative times).

### Named Rules
**The One Color Rule.** A part has exactly one color, and that color appears wherever the part does: core, ring, filaments, fibre ends, pulse tail, sub-cores, panel dot, panel border, panel meta, panel focus ring. A part is never given a second hue, and a part color is never borrowed for anything that isn't its part (Cognition cyan as the page's default focus ring is the one shared use).

**The Red Means Failing Rule.** Fail Red (#FF4136) is reserved for a failing part or worker: the ring, its state word, its worker dot. Error prose uses the softer Error Text (#FF8A80). No other surface uses red.

**The Light Is State Rule.** State is told by charge, not badges: the ring charges and discharges on the state's period (alive 2.8 s, working 1.05 s, partly down 4.4 s, failing 1.6 s); the node dims to 72% when partly down, 45% unknown, 28% down and 14% off.

## Typography

**Display Font:** Schibsted Grotesk Variable (self-hosted via `@fontsource-variable/schibsted-grotesk`, with ui-sans-serif, system-ui fallback)
**Body Font:** the same
**Label/Mono Font:** none distinct in the world; figures use tabular numerals (`tnum`)

**Character:** one grotesk at instrument-overlay sizes, tight-tracked for names and plain for everything else.

### Hierarchy
- **Headline** (600, 22px, 1.25, -0.02em): the part's name at the top of the details panel.
- **Title** (600, 19px, 1, -0.02em): the specimen label "Jophiel · live field"; " · live field" drops to 400 in Ink Muted.
- **Node Label** (560, 15px at ×1, 650 when selected): part names on the field. It is drawn in field units and grows slower than the zoom (size × magnification^0.22), so labels stay readable without ballooning. The state word below it is 72% of that size in the part color.
- **Body** (400, 13.5px, 1.625): entry text in the panel; the role line is 13px in Ink Role.
- **Label** (600, 13px): panel section headings ("Running as", "Her thoughts"), the status line and page links (400).
- **Meta** (400, 12px, tabular figures): entry meta lines in the part color, relative times in Ink Time, the last-signal line, the ruler's hint text.
- **Sub-label** (400, 11.5px at ×1, scaled by magnification^0.12): sub-core names once they resolve.

### Named Rules
**The Sentence Case Rule.** All overlay and panel text is sentence case and untracked (beyond the -0.02em on names). No uppercase tracked labels.

**The Tabular Figures Rule.** Any number that updates live (counts, magnification, "seen 3 s ago") is set in tabular figures so it doesn't jitter.

## Layout

The page is fixed to the viewport (`inset: 0`, no page scroll). The field fills it; wheel zoom is captured so the page never scrolls.

- **Field.** Seven parts at fixed positions in a 1600 × 1000 field: Cognition central and largest, Mouth right, Memory beneath, Mind upper left, Hands lower right, Observation top, Learning lower left. The fitted view frames the nodes and their labels (not the empty field), with 70px clear at top and bottom (110px on top in portrait) for the overlay, at 96% of the available fit.
- **Portrait.** When the viewport is more than 1.15× taller than wide, the field is turned on its side (x and y swapped) so every node keeps its neighbours and a phone is filled; labels move above or below.
- **Overlay.** Specimen label, status, last signal and page links top left; magnification ruler bottom left; zoom buttons bottom right. Gutters 16px, 24px from 640px up. When the panel is open on a wide screen, the bottom chrome slides in by the panel's 460px (300ms ease-out).
- **Panel.** From 640px: a 460px sheet pinned to the right, full height. Below 640px: a bottom sheet up to 68vh, and the chosen node is animated into the strip above it. Panel padding 20px / 24px; sections 28–32px apart; entries 12px top and bottom.
- **Navigation.** Pan with drag or arrow keys (80px a press), zoom with wheel, pinch, + / − (×1.5, toward the node nearest the centre, then centring it) and 0 to fit. View moves animate over 420ms with an exponential ease-out, zoom interpolated in log space.

## Elevation & Depth

Depth is optical, not material. The nodes are the near plane. The stars are at infinity: painted once to a canvas covering the field and a 2.6× margin, one star per 1,900px² (0.35–1.95px, alpha 0.18–0.93, the brightest with a soft glow out to 3.5× their radius), plus 34 twinkling points (1.2–2.8px, 3–8 s); they never grow with the zoom and move at half the pan. A vignette darkens the field's edges and the top-left corner behind the overlay text. Light, not shadow, is what lifts a node: a halo out to 2.2× its radius and a discharge halo that flares on each beat.

### Shadow Vocabulary
- **Sheet edge** (`box-shadow: -24px 0 48px rgba(0,0,0,0.55)`): the details panel over the field.
- **Part dot glow** (`box-shadow: 0 0 14px <part color>`): the panel's part dot.
- **Twinkle glow** (`box-shadow: 0 0 4px rgba(255,255,255,0.8)`): a twinkling star.

### Named Rules
**The Stars At Infinity Rule.** The sky never scales with the zoom and never carries meaning; only nodes, fibres and pulses are signal. Nothing sits on a card above the field except the details panel.

## Shapes

Nodes are concentric and electric. A core disc (36% of the node's radius, shrinking to 22% as sub-cores resolve), a charged ring at 80% of the radius, and six to eight thin jagged filaments that leave just inside the ring, bending a little at each of six steps, more than half of them forking once, kept clear of the directions the node's fibres leave in. Up close the filaments draw in toward the ring instead of growing into long zigzags. Every node's filaments are drawn from a seed of its part id, so it keeps its shape between visits. Sub-cores are small discs inside the ring at 56% of the radius on straight spokes from the centre, fanned away from the label, with their labels outside the ring on the same bearing behind a short leader. Fibres are cubic curves bowed by a seeded amount, tucked a little inside each node so drift never shows a gap. Nodes drift a few field units in 19–27 s loops.

The chrome is the opposite: geometric and minimal. Zoom buttons and dots are full circles; the panel is square-cornered; the ruler is a 1px line with ticks. Icons are 16px line drawings at a 1.5 stroke with round caps and joins (plus, minus, fit corners, close, and the disclosure chevron, which turns 90° over 160ms).

## Components

### Buttons (chrome)
Round glass instruments, quiet until touched.
- **Shape:** full circle, 36px.
- **Default:** Chrome Glass at 90% alpha, a 1px Reticle Rule border, Ink Heading icon.
- **Hover / Focus:** border to Reticle Hover, icon to white (150ms). Focus is a 1.5px cyan outline, 3px offset.
- **Disabled:** 35% opacity at the zoom limits.
- **Close (panel):** borderless circle, Ink Secondary; hover fills Hover Wash.

### Nodes (signature)
- **Body:** halo (2.2r); a discharge halo (2r) and a white core flash that fire at 88% of each period with an expo ease-out; filaments 1.1px at 75% that crackle (a stepped 3.7 s flicker) while running; a faint base ring (1.2px at 30%) under the charging ring (2px, stroke opacity climbing 0.18 → 1 over 86% of the period, then dropping as the flare fires); the core gradient.
- **States:** partly down breaks the ring into dashes (half and a third of the ring radius); failing turns the ring Fail Red, 2.6px, pulsing on a 1.2 s cycle; down holds a still ring at 35%; off draws a dotted ring and no core; selected adds a thin 1px ring at 1.22r. Hit target is an invisible 1.4r circle.
- **Label:** name plus state word, ringed in Field Black (`paint-order: stroke`, 0.32em), placed just past the filaments on its side. Keyboard focus underlines the name instead of drawing an outline.

### Fibres and pulses
- **Fibre:** a 6px glow at 12% under a 1.1px line, both non-scaling and in a sender-to-receiver gradient; 70% opacity when both ends run, 18% otherwise. Every fibre is a real hand-off.
- **Pulse:** each real signal runs its fibre in 760ms (`cubic-bezier(0.45, 0, 0.25, 1)`) as a tapered spike: a white head (2.6px, up to 22 field units long), a Core White middle three times as long (1.8px, 50%), and a tail in the sender's color seven times as long (1.2px, 25%), while the whole fibre brightens to 55% and lets go. On arrival the receiving node flares once (700ms, scaling 0.7 → 1.25 and fading). Opening the page replays the last 90 s of signals 160ms apart, then goes live.

### Magnification ruler
Readout "×1.0" (13px, 500, tabular) beside a 112–160px line with ticks at ×1, ×2, ×4 and ×6 on a log scale; a bright fill and a 9px cursor mark the current magnification; a hint says "zoom in to see inside each part" until sub-cores resolve, then "inside view".

### Details panel
- **Background:** Slide Sheet, square corners, a 1px border in the part color at 33% alpha (left on wide screens, top on phones), the sheet-edge shadow.
- **Header:** glowing part dot, headline name, role line, close button.
- **Content:** "Running as" worker list, then the part's feed in sections. Entries are rule-divided (Hairline), not boxed: a meta line in the part color (or Error Text) with a right-aligned relative time, then body text. Reasoning, where a record has it, folds into a disclosure.
- **Focus:** the panel takes focus on open; Escape closes; focus rings inside use the part color.

### Navigation
Page links under the status and last-signal lines: 13px, Ink Muted, white on hover. No tab bar on this page.

## Do's and Don'ts

### Do:
- **Do** give every part its one color and carry it to its ring, filaments, fibres, pulse tails, sub-cores and panel (The One Color Rule).
- **Do** show state as charge: the ring's beat, the discharge flare, dimming, a broken ring, a red ring.
- **Do** keep the field full-bleed on Field Black (#040506) with the star field at infinity behind the nodes.
- **Do** run a pulse only for a real hand-off between two parts.
- **Do** set live numbers in tabular figures and ring field labels in Field Black.
- **Do** list a part's records as rule-divided entries with a part-color meta line.
- **Do** stop charging, discharge, crackle, twinkle, drift and the failing pulse under `prefers-reduced-motion`, let pulses land as a bare arrival, and jump view changes instead of animating them.

### Don't:
- **Don't** draw parts as rounded boxes, neon edges or a stats sidebar: the node-graph dashboard is the default this world refuses.
- **Don't** put records in rounded cards on this page; the panel is rule-divided.
- **Don't** add a physical scale bar or µm units; magnification is the ×1–×6 tick ruler.
- **Don't** use red for anything but failure.
- **Don't** let the stars scale with the zoom or carry state.
- **Don't** set uppercase, letter-spaced labels on the field or panel (The Sentence Case Rule).
- **Don't** extend these tokens to the rest of the app; the light incumbent there is a separate, unrecorded look.
