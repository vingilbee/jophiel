# Shared training data

Status: proposed plan, not implementation authorization. [Plan index](README.md).

## Outcome

Versioned, auditable datasets for shared learners.

Dependencies: Step 3; step 4 for trial-derived records.

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

