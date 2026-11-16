# Shared training data

Status: proposed plan, not implementation authorization. [Plan index](README.md).

## Outcome

Versioned, auditable datasets for shared learners.

Dependencies: Step 3; step 4 for trial-derived records.

Roadmap clarification (2026-11-05, updated after the step 4 interview): [post-audit 3](POST-AUDIT-03-broaden-experience-understanding.md) owns the implemented intake, inquiry, and initial product/delivery foundation; [post-audit 4](POST-AUDIT-04-broader-learning-products.md) completes remaining broader-product semantics and validation. Reuse both rather than duplicate their work. Their relevant behavioral gates remain prerequisites for later training and integration claims; ordinary-experience understanding is not deferred to later expansion.

## Work

- Derive learner-specific supervised targets first; use preference pairs only where evidence supports comparisons.
- Preserve original user ownership and source lineage while selecting eligible personal experience for shared training.
- Keep student inputs restricted to decision-time information; separate real and synthetic origins.
- Split related experiences and synthetic descendants together; reserve unseen-user evaluation where testing cross-user generalization.
- Include ordinary successes and baseline rehearsal; propagate source invalidation into future exports.

## Decisions to settle within this plan

Personal-data eligibility and transformation; label acceptance; dataset format migration; deletion strategy; balanced sampling across users.

## Completion evidence

Every training row has a supported target, eligible source, faithful input, and traceable lineage. Repeated reflections cannot manufacture independent support.

## Boundary

Deleting a row does not remove its influence from already trained weights. A model-level removal strategy remains a required decision.

