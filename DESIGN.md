---
name: Jophiel
description: Her parts seen live under a fluorescence microscope (owner-only System page).
colors:
  cfp-cognition: "#3FD3F0"
  gfp-mouth: "#4BE37A"
  mcherry-mind: "#FF5C8A"
  yfp-hands: "#F5D23A"
  morange-memory: "#FF9A3C"
  bfp-observation: "#6E8CFF"
  venus-learning: "#B77DFF"
  field-black: "#040506"
  slide-sheet: "#07090B"
  chrome-glass: "#0A0D10"
  hover-wash: "#151A1F"
  hairline: "#151A1F"
  reticle-rule: "#2A3138"
  reticle-tick: "#4A545D"
  reticle-hover: "#5A6670"
  dark-pixel: "#3A434B"
  soma-core: "#F4FFFE"
  ink-bright: "#EEF1F3"
  ink: "#E9EDF0"
  ink-heading: "#DDE3E8"
  ink-body: "#D5DBE0"
  ink-list: "#C5CDD5"
  ink-sub: "#C9D1D8"
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
  cell-label:
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
    textColor: "#FFFFFF"
  panel-close:
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.full}"
    size: "36px"
  panel-close-hover:
    backgroundColor: "{colors.hover-wash}"
    textColor: "#FFFFFF"
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
    textColor: "#FFFFFF"
---

# Design System: Jophiel

## Overview

**Creative North Star: "The Live Field"**

Jophiel's parts are seen the way a confocal microscope sees a stained tissue sample: a black field, a few labelled cells glowing in their own fluorophores, tapered axons between them, and out-of-focus tissue blurring into the dark. A part being alive is a cell glowing and flashing with a calcium transient; a part that is down is a dim cell; a failing part's own membrane burns red. Everything the visitor reads besides the cells is the microscope's own overlay: a specimen label top left, a magnification gauge bottom left, round zoom controls bottom right. The world refuses the category default for "system status": no node-graph dashboard of rounded boxes, neon edges and a stats sidebar.

The field is full-bleed and spatial, not paged. It is a map you drag, wheel, pinch and key around, from ×1 (the whole field) to ×6, and as you zoom past ×1.9 each cell's sub-parts resolve around it, fully legible by ×2.7. Clicking a cell opens a plain dark sheet on the right (a bottom sheet on phones) that carries that cell's fluorophore in its dot, its border and its meta lines, and lists the part's real recent records as quiet rule-divided entries. The density is low on the field and comfortable in the sheet.

**Scope.** This world is built only on the owner-only System page (`/system`). The rest of the app (chat, voice dashboard, login, integrations, predict) predates it, uses a light pale-blue and lavender look written as hardcoded Tailwind hexes, and is an **unrecorded incumbent**: this file records no tokens for it, and nothing here should be read as describing it. The older dark `@theme` block at the top of `frontend/src/index.css` (Space Grotesk, teal accent) is not this world either; the System page does not use it.

**Divergences from the direction contract, where the build won.** There is no physical scale bar: magnification is a tick gauge marked at ×1, ×2, ×4 and ×6, because a µm unit would be a made-up claim. The details panel is a plain dark sheet with a fluorophore dot and border, not a slide-label. Wires carry static varicosities and dim with their ends' liveness; no spike travels along them yet.

**Key Characteristics:**
- Microscope-black field (#040506) with blurred, parallaxed out-of-focus tissue in the seven fluorophores.
- One fluorophore per part, kept everywhere that part appears.
- Irregular, seeded somata with tapered dendrites; curved tapered axons with bead varicosities.
- Overlay chrome in Schibsted Grotesk with tabular figures; round glass zoom buttons.
- State shown as light: steady glow, transient flashes, dim, dashed broken membrane, red burning rim.
- Reduced motion gets a still field.

## Colors

A black field lit only by seven fluorophores, with a cool grey ink ramp for the overlay and one red reserved for failure.

### Primary
Seven fluorophores, one per part. They are equal in rank; none is "the brand accent".
- **CFP Cyan** (Cognition): the central, largest cell. Also the page's default focus ring and text-selection tint (cyan at 32%).
- **GFP Green** (Mouth), **mCherry Rose** (Mind), **YFP Amber** (Hands), **mOrange** (Memory), **BFP Blue** (Observation), **Venus Violet** (Learning).

Each fluorophore paints its cell's soma (over a near-white core, #F4FFFE, that gives the cell its inner glow), its halo and dendrites, its sub-cells, its state word under the label, both ends of every wire it touches (a gradient from sender to receiver), the panel's dot and border (at 33% alpha) and the panel's entry meta lines and focus ring.

### Neutral
- **Field Black**: the microscope's field and the stroke ring around every label, so labels read over glow and wires.
- **Slide Sheet**: the details panel.
- **Chrome Glass** (at 90% alpha): the round zoom buttons.
- **Hairline / Hover Wash**: dividers between entries in the panel; the close button's hover fill.
- **Reticle Rule / Reticle Tick / Reticle Hover**: chrome button borders and the gauge line; gauge ticks; chrome border on hover.
- **Dark Pixel**: a worker dot that is down.
- **Ink ramp**, brightest to faintest: Ink Bright (panel title), Ink (specimen label, cell labels, gauge readout), Ink Heading (section headings, chrome icons), Ink Body (entry text), Ink Sub (sub-cell labels), Ink List (worker names, code), Ink Secondary (status line, reasoning), Ink Role (the part's one-line role), Ink Muted (page links, empty states, down/off state words), Ink Time (relative times).

### Named Rules
**The One Fluorophore Rule.** A part has exactly one color, its fluorophore, and that color appears wherever the part does: cell, wires, sub-cells, panel dot, panel border, panel meta, panel focus ring. A part is never given a second hue, and a fluorophore is never borrowed for anything that isn't its part (Cognition cyan as the page's default focus ring is the one shared use).

**The Red Means Failing Rule.** Fail Red (#FF4136) is reserved for a failing part or worker: the burning rim, its state word, its worker dot. Error prose uses the softer Error Text (#FF8A80). No other surface uses red.

**The Light Is State Rule.** State is told by light, not badges: steady halo plus a calcium transient (alive 2.8 s, working 1.05 s, partly down 4.4 s, failing 1.6 s), dimmed to 70% when partly down, 45% unknown, 30% down, 16% and unfilled when off.

## Typography

**Display Font:** Schibsted Grotesk Variable (self-hosted via `@fontsource-variable/schibsted-grotesk`, with ui-sans-serif, system-ui fallback)
**Body Font:** the same
**Label/Mono Font:** none distinct in the world; figures use tabular numerals (`tnum`)

**Character:** one grotesk at instrument-overlay sizes, tight-tracked for names and plain for everything else, the way a microscope's software sets its labels.

### Hierarchy
- **Headline** (600, 22px, 1.25, -0.02em): the part's name at the top of the details panel.
- **Title** (600, 19px, 1, -0.02em): the specimen label "Jophiel · live field"; " · live field" drops to 400 in Ink Muted.
- **Cell Label** (560, 15px at ×1, 650 when selected): part names on the field. It is drawn in field units and grows slower than the zoom (size × magnification^0.22), so labels stay readable without ballooning. The state word below it is 72% of that size in the fluorophore.
- **Body** (400, 13.5px, 1.625): entry text in the panel; the role line is 13px in Ink Role.
- **Label** (600, 13px): panel section headings ("Running as", "Her thoughts"), the status line and page links (400).
- **Meta** (400, 12px, tabular figures): entry meta lines in the fluorophore and relative times in Ink Time; the gauge's hint text.
- **Sub-label** (400, 11.5px at ×1, scaled by magnification^0.12): sub-cell names once they resolve.

### Named Rules
**The Sentence Case Rule.** All overlay and panel text is sentence case and untracked (beyond the -0.02em on names). No uppercase tracked labels.

**The Tabular Figures Rule.** Any number that updates live (counts, magnification, "seen 3 s ago") is set in tabular figures so it doesn't jitter.

## Layout

The page is fixed to the viewport (`inset: 0`, no page scroll). The field fills it; wheel zoom is captured so the page never scrolls.

- **Field.** Seven parts at fixed positions in a 1600 × 1000 field: Cognition central and largest, Mouth right, Memory beneath, Mind upper left, Hands lower right, Observation top, Learning lower left. The fitted view frames the cells and their labels (not the empty field), with 70px clear at top and bottom (110px on top in portrait) for the overlay, at 96% of the available fit.
- **Portrait.** When the viewport is more than 1.15× taller than wide, the field is turned on its side (x and y swapped) so every cell keeps its neighbours and a phone is filled; labels move above or below.
- **Overlay.** Specimen label, status and page links top left; magnification gauge bottom left; zoom buttons bottom right. Gutters 16px, 24px from 640px up. When the panel is open on a wide screen, the bottom chrome slides in by the panel's 460px (300ms ease-out).
- **Panel.** From 640px: a 460px sheet pinned to the right, full height. Below 640px: a bottom sheet up to 68vh, and the chosen cell is animated into the strip above it. Panel padding 20px / 24px; sections 28–32px apart; entries 12px top and bottom.
- **Navigation.** Pan with drag or arrow keys (80px a press), zoom with wheel, pinch, + / − (×1.5, toward the cell nearest the centre, then centring it) and 0 to fit. View moves animate over 420ms with an exponential ease-out, zoom interpolated in log space.

## Elevation & Depth

Depth is optical, not material. The field has focal planes: the cells are in focus; behind them, a canvas of blurred tissue (two layers, 22px and 9px blur, 11–16% alpha, in the fluorophores) moves at half the pan and grows slower than the zoom, so it reads as deeper. Fine processes (dendrites) carry a 1.1px blur, out of the focal plane. A vignette darkens the field's edges and the top-left corner behind the overlay text. Glow, not shadow, is what lifts a cell.

### Shadow Vocabulary
- **Sheet edge** (`box-shadow: -24px 0 48px rgba(0,0,0,0.55)`): the details panel over the field.
- **Fluorophore dot glow** (`box-shadow: 0 0 14px <fluorophore>`): the panel's part dot.

### Named Rules
**The Focal Plane Rule.** Anything that isn't a cell or the overlay is out of focus: blurred, dim, and slower under pan and zoom. Nothing sits on a card above the field except the details panel.

## Shapes

Cells are organic and seeded: an irregular soma (a closed smooth curve with three low-amplitude waves), five to seven tapered, bending dendrites (some forked) that grow away from the axon exits, and a dark nucleus. Each shape is drawn from a seed of the part's id, so a cell keeps its own shape between visits. Axons are curved, tapered and beaded with varicosities. Sub-cells are small somata on short curved stalks, fanned away from the label. Cells drift a few field units in 19–27s loops.

The chrome is the opposite: geometric and minimal. Zoom buttons and dots are full circles; the panel is square-cornered; the gauge is a 1px line with ticks. Icons are 16px line drawings at a 1.5 stroke with round caps (plus, minus, fit corners, close, and the disclosure chevron, which turns 90° over 160ms).

## Components

### Buttons (chrome)
Round glass instruments, quiet until touched.
- **Shape:** full circle, 36px.
- **Default:** Chrome Glass at 90% alpha, a 1px Reticle Rule border, Ink Heading icon.
- **Hover / Focus:** border to Reticle Hover, icon to white (150ms). Focus is a 1.5px cyan outline, 3px offset.
- **Disabled:** 35% opacity at the zoom limits.
- **Close (panel):** borderless circle, Ink Secondary; hover fills Hover Wash.

### Cells (signature)
- **Body:** halo (2.7r), calcium-transient halo and soma flash on the state's period, soft dendrites, soma gradient from the near-white core into the fluorophore, nucleus.
- **States:** partly down adds a dashed membrane in the fluorophore (10/7); failing adds a pulsing 2.4px red rim; selected adds a thin ring at 1.42r. Hit target is an invisible 1.5r circle.
- **Label:** name plus state word, ringed in Field Black (`paint-order: stroke`, 0.32em), placed just past the dendrites on its side. Keyboard focus underlines the name instead of drawing an outline.

### Wires
1.3px non-scaling strokes with a sender-to-receiver gradient and beads whose size grows gently with zoom; 62% opacity when both ends run, 20% otherwise. Every wire is a real hand-off.

### Magnification gauge
Readout "×1.0" (13px, 500, tabular) beside a 112–160px line with ticks at ×1, ×2, ×4 and ×6 on a log scale; a bright fill and a 9px cursor mark the current magnification; a hint says "zoom in to see inside each part" until sub-parts resolve, then "inside view".

### Details panel
- **Background:** Slide Sheet, square corners, a 1px border in the fluorophore at 33% alpha (left on wide screens, top on phones), the sheet-edge shadow.
- **Header:** glowing fluorophore dot, headline name, role line, close button.
- **Content:** "Running as" worker list, then the part's feed in sections. Entries are rule-divided (Hairline), not boxed: a meta line in the fluorophore (or Error Text) with a right-aligned relative time, then body text. Reasoning, where a record has it, folds into a disclosure.
- **Focus:** the panel takes focus on open; Escape closes; focus rings inside use the fluorophore.

### Navigation
Page links under the status line: 13px, Ink Muted, white on hover. No tab bar on this page.

## Do's and Don'ts

### Do:
- **Do** give every part its one fluorophore and carry it to its wires, sub-cells and panel (The One Fluorophore Rule).
- **Do** show state as light: glow, transient period, dimming, dashed membrane, red rim.
- **Do** keep the field full-bleed on Field Black (#040506) with out-of-focus tissue behind the cells.
- **Do** set live numbers in tabular figures and ring field labels in Field Black.
- **Do** list a part's records as rule-divided entries with a fluorophore meta line.
- **Do** stop transients, drift and the failing pulse under `prefers-reduced-motion`, and jump view changes instead of animating them.

### Don't:
- **Don't** draw parts as rounded boxes, neon edges or a stats sidebar: the node-graph dashboard is the default this world refuses.
- **Don't** put records in rounded cards on this page; the panel is rule-divided.
- **Don't** add a physical scale bar or µm units; magnification is the ×1–×6 tick gauge.
- **Don't** use red for anything but failure.
- **Don't** set uppercase, letter-spaced labels on the field or panel (The Sentence Case Rule).
- **Don't** extend these tokens to the rest of the app; the light incumbent there is a separate, unrecorded look.
