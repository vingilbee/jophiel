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

THESIS: Jophiel's parts as electric neurons in a star field. Each part is a charged core that fills with light and discharges on its own beat; a hand-off is a spike of light running a conducting fibre, and the core it reaches flares. Refuses the category default: a node-graph dashboard of rounded boxes, neon edges and a stats sidebar. (Redesigned 2026-11-03 from the first build's fluorescence-microscope cells, at the user's request: nodes should read as electric so pulses read as current.)

OWN-WORLD: a night field (#040506) of white star points, a few twinkling, moving at half the pan. Each part keeps one colour everywhere (core, ring, filaments, fibre, its page's meta lines); about a third of the stars carry the parts' colours too: Mouth green, Mind rose, Hands amber, Memory orange, Observation blue, Learning violet, Phone lime (cyan kept for focus). A node is a white-hot core in its colour, a thin charged ring, and fine jagged filaments that crackle while it's alive. Fibres are hairlines inside a faint glow, blending between the two parts' colours. Chrome: specimen label with counts and the last signal, magnification readout over a tick ruler, zoom controls; Schibsted Grotesk with tabular figures.

STORY: the visitor sees her parts charging and discharging, understands which are alive (steady beat), working (fast beat), partly down (broken ring), failing (red ring) or down (dark), watches real hand-offs run between them (her own activity only, the last minute and a half replayed on opening), zooms toward a part to see its sub-cores inside the ring, jumps into one and reads its real recent output on its own page.

FIRST VIEWPORT: full-bleed star field. Seven nodes, Mind central, Mouth right, Memory beneath, Hands lower right, Observation top, Learning lower left, Phone upper left, fibres between them. Top-left "Jophiel · live field", running/trouble/down counts and the last signal; bottom-left magnification ×1–×6 on a tick ruler; bottom-right zoom controls. Primary action: click a node and jump into it: the stars streak out from it like a jump to lightspeed, the camera flies into the node and its light floods the screen, then the part opens as its own page (/system/:part): a sticky rail with the live node, its sub-parts as section links, its workers and hand-offs, beside a readable timeline. Esc or back plays the jump in reverse.

FORM: pinned by the user (dark, neuron-futuristic, fixed positions with slight drift, game-map pan/zoom); no roll was run against the pin. seed: none (pinned)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
