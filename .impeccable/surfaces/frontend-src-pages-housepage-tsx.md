---
version: 1
slug: "frontend-src-pages-housepage-tsx"
primary_target: "frontend/src/pages/HousePage.tsx"
related_targets: []
---

# The House (`/dashboard`)

**Scope and mode:** Experience. The signed-in home and the only way between pages (no tabs). Audience: the owner daily; later, other signed-in people (no System house for them). Job: pick a place and go there, and feel you've come home to her.

**Constraints:** every house is one native link with a real URL; System stays owner-only; the shared ChatPage stays mounted; reduced motion gets still houses and instant navigation; modified clicks keep browser behavior. Cottage art is the user's (`assets/cloud-house`); each page's own cottage and chibi Jophiel are drawn later. Rive is reserved for chibi Jophiel; house, door and light are CSS / Web Animations.

**Memorable moment:** the chosen door swings in and its lamplight floods the screen.

**Unresolved:** per-page cottage art, painted cloud bed, chibi Jophiel (Rive) asleep on the bed, login golden gate.

## Direction contract

THESIS: Home is a village of lit cottages floating in a golden-dusk heaven; you go somewhere by walking through a door, not by picking a tab. Refuses the app-shell dashboard (sidebar, tab bar, tiles).

OWN-WORLD: Full-bleed dusk sky, violet zenith to rose-lilac to peach-gold horizon with a low sun glow, a few early stars, sparse snow; drifting cloud banks in lilac-shadowed white. Painted snowy cottages on clouds. Timber plank signs on rope, hand-lettered rounded type in cream with dark-wood stroke. Cloud-shaped controls. No cards, no glass panels.

STORY: You arrive home at dusk, see her house first, read each sign at a glance, step through a door into that room.

FIRST VIEWPORT: Chat cottage largest, front and centre (about 40% of width). Integrations left and Sessions right, smaller and higher, in a shallow arc; System smallest at the back between them (owner only). An empty cloud bed in the bottom-left foreground. Sign-out cloud button top right. Phone: Chat full width, the rest two up, bed last.

FORM: User-pinned world (heaven village, golden dusk, Christmas cottage), position 1; signature interaction: door opens inward, amber light spreads from the doorway to an ivory full screen, navigation at peak, fade on arrival (~1 s). seed: none (unavailable)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
