# Figma handoff brief — Columbina NutritionDetection

Use this brief when creating the first Figma project/file for the app.

## Product context

NutritionDetection is a nutrition assistant web app. Users can:

- Chat with an AI nutrition assistant about food, macros, and nutrition goals.
- Scan or upload meal images for nutrition estimation.
- Use voice conversation with the assistant.
- View nutrition progress and meal history on a dashboard.
- Connect integrations.

The assistant is named **Columbina**, inspired by the Genshin Impact character’s quiet, celestial, moonlit visual identity. The product should feel like a calm personal nutrition companion—not a generic admin panel and not a cyberpunk terminal.

## Current implementation

- Frontend: React + TypeScript + Vite.
- Styling: Tailwind CSS with custom design tokens.
- Current pages: Dashboard, Chat, Scan/Predict, Integrations, Landing, Login.
- Current UI: near-black background, faint grid texture, thin borders, monospace labels, teal accent, sparse centered layouts, large voice orb.
- Current problems: too terminal-like, emotionally flat, weak nutrition hierarchy, too much empty space, and the chatbot/dashboard do not feel like parts of one character-led product.

## Redesign goal

Create a polished responsive web app system with:

1. A dashboard focused on “today”: calories, protein/carbs/fat, hydration, recent meals, and scan entry point.
2. A chatbot with a calm conversation canvas, conversation history, assistant identity, meal/photo attachment cards, and a clear composer.
3. A scan flow for uploading a meal image and reviewing/editing estimated foods and nutrition.
4. A voice state treatment that feels like moonlight/water/air rather than a glowing neon tech orb.

## Visual direction

- Deep blue-black ink canvas instead of pure black.
- Porcelain white and cool mist-blue surfaces.
- Soft rose-pink for Columbina’s assistant identity and active states.
- Desaturated sky blue for charts and progress.
- Restrained crimson only for warnings or nutrition gaps.
- Soft rounded cards, subtle translucency, restrained shadows, generous but purposeful spacing.
- Use decorative celestial/floral/feather motifs sparingly as background detail or assistant branding.
- Prefer a refined humanist sans-serif for body text; reserve mono for small metadata only.

## Suggested palette

| Token | Hex | Use |
|---|---|---|
| Ink | `#101522` | page background |
| Midnight | `#1B2940` | sidebar and deep panels |
| Porcelain | `#F4F4F1` | light cards and primary text |
| Moon mist | `#C9D8E8` | secondary surfaces and dividers |
| Columbine blue | `#6E91B8` | charts and secondary actions |
| Veil pink | `#D99AAE` | assistant accent and active nav |
| Crimson thread | `#A94B60` | warnings / missed targets |
| Soft lilac | `#B6ABC9` | reflective voice state |

## Figma pages to create

- `00 — Foundations`: palette, typography, spacing, radii, shadows, icon rules.
- `01 — Components`: navigation, buttons, cards, macro bars, progress ring, chat bubbles, composer, meal cards, upload states, voice states.
- `02 — Dashboard`: desktop and mobile.
- `03 — Chat`: empty state, active conversation, attachment preview, streaming response.
- `04 — Scan`: upload, analyzing, results, edit nutrition, confirmation.
- `05 — Voice`: standby, listening, thinking, speaking, interrupted/error.
- `06 — Prototype flows`: dashboard → scan → result; dashboard → chat; chat → voice.

## Important UX constraints

- Nutrition numbers must remain high contrast and easy to scan.
- A meal estimate should communicate that it is an estimate and provide an edit/correction path.
- Do not hide the primary action under decorative visuals.
- Preserve responsive behavior for narrow mobile screens.
- Keep Columbina-inspired visual cues original and thematic; do not copy character artwork into production UI without checking rights.

## Existing local references

See the images and research notes in this folder, especially `README.md` and `sources.md`. The local previews are inspiration only and should not automatically become production assets.
