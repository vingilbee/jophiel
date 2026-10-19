# Cognitive system

## Product and evaluation scope

Columbina is a general conversational agent with a persistent identity and personality. Nutrition is a specialist capability, not the product's main focus. Default behavioral evaluation should exercise varied, multi-turn conversation rather than repeat meal-planning tasks. A conversational move may be a social response, reflection, question, disagreement, or direct answer; it need not create an artifact. See [the conversational test plan](../../../../../docs/COLUMBINA_CONVERSATIONAL_TEST_PLAN.md).

The package has two explicit generations:

- The package-root modules are the earlier local perception and intent
  prototype. They remain import-compatible through `app.ai.Cognitive.system`.
- [the current V0 guide](v0/README.md) contains the complete version-zero cognitive shadow system,
  including its contracts, pipeline stages, durable live-shadow integration,
  fixtures, evaluation material, and local trace-summary CLI.

New V0 code must live under `app.ai.Cognitive.system.v0`; do not add another
V0 package at the `system/` root.

See the [document index](v0/DOCUMENTATION_INDEX.md) before using an older brief as implementation guidance.
