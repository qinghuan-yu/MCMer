# Repository Execution Rules

These rules are mandatory for every agent modifying this repository.

## Single Source of Truth

- The only long-term execution plan is `docs/compute_first_guidance_plan.md`.
- Before every code modification, read the complete long-term plan and identify its current unchecked step.
- Do not create parallel roadmap, optimization-plan, simplification-plan, or guidance-plan documents.
- When the target changes, rewrite the single long-term plan and delete superseded planning documents.

## Test First

- Every new behavior or bug fix must follow Red-Green-Refactor.
- Write the smallest failing unit or contract test before production code.
- Run the test and confirm it fails for the intended missing behavior.
- Implement only enough production code to pass, then refactor with tests green.

## Refactor Instead of Patching

- If the current module boundary conflicts with the requested behavior, do not add an `if`/`else`, feature flag, compatibility branch, or pass-through adapter.
- Stop implementation and explain the conflicting data flow and coupling.
- Propose the module-level replacement, including affected files, migration boundary, and verification.
- Obtain explicit user approval before replacing the conflicting module.

## Plan State

- After completing each subtask, immediately change its checkbox from `[ ]` to `[x]` in the long-term plan.
- Keep exactly one explicit `CURRENT` step in the plan.
- Remove obsolete plan versions instead of archiving them beside the active plan.

## Mandatory Current-State Summary

After every modification, report a `现状摘要` containing:

1. The active core data flow.
2. The most dangerous coupling or boundary violation.
3. What changed in this modification.
4. Which long-term-plan checkbox was completed.
5. The next unchecked step.

