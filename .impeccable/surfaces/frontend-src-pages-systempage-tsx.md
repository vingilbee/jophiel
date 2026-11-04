---
version: 1
slug: "frontend-src-pages-systempage-tsx"
primary_target: "frontend/src/pages/SystemPage.tsx"
related_targets: []
---

# System page (`/system`)

Scope: owner-only route replacing `/learning`. Mode: Experience (showpiece first, still truthful). Build path: code-led (no image generation on this machine).

Audience and job: the owner watching Jophiel's parts live, showing her off, then clicking in to see what a part actually produced or thought. Only the owner's own activity drives signals. Constraints: real records only; parts without reasoning show outputs, never invented thoughts; reduced-motion gets a still field; keyboard pan/zoom and focusable parts.

## Direction contract

THESIS: Jophiel seen under a fluorescence microscope. Her seven parts are labelled cells in one live field of view; activity is a calcium transient, a hand-off is a spike travelling down an axon. Refuses the category default: a node-graph dashboard of rounded boxes, neon edges and a stats sidebar.

OWN-WORLD: confocal / Brainbow imaging. Ground is the microscope's black field (#040506) with out-of-focus tissue glow. Each part owns one fluorophore and keeps it everywhere (dot, wire, panel rule): Mouth GFP green, Cognition CFP cyan, Mind mCherry rose, Hands YFP amber, Memory mOrange, Observation BFP blue, Learning violet. Somata are irregular glowing bodies with dendrite stubs; wires are tapered axons with varicosities. Chrome is the microscope's own overlay: scale bar, magnification readout, specimen label, set in Schibsted Grotesk with tabular figures.

STORY: the visitor sees a living cell field, understands which parts are alive (steady glow), dead (dim) or failing (red rim), watches signals travel, zooms toward a cell to see its sub-cells resolve, clicks one and reads its real recent output.

FIRST VIEWPORT: full-bleed field. Seven somata fill it, Cognition central and largest, Mouth right toward the user, Memory beneath, Mind upper left, Hands lower right, Observation top, Learning lower left; axons between them. Top-left specimen label "Jophiel · live field" with live/idle counts; bottom-left scale bar and magnification (×1 – ×6); bottom-right zoom controls. Primary action: click a cell; the details panel slides from the right as a dark slide-label sheet in that cell's fluorophore.

FORM: pinned by the user (dark, neuron-futuristic, fixed positions with slight drift, game-map pan/zoom); no roll was run against the pin. seed: none (pinned)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
