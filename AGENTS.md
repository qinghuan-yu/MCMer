# Repository Execution Rules

These rules are mandatory for every agent modifying this repository.

## Single Source of Truth

- The only long-term execution plan is `docs/compute_first_guidance_plan.md`.
- Keep only `AGENTS.md` (execution rules) and `docs/compute_first_guidance_plan.md` (active plan). Do not create parallel roadmap, optimization-plan, simplification-plan, or guidance-plan documents.
- Before every code modification, read the complete long-term plan and identify its current unchecked step.
- When the target changes, rewrite the single long-term plan, compact completed phases into locked invariants, and delete superseded planning documents.

## Product and Runtime Direction

- The target is one reproducible, independently checked end-to-end modeling subtask, followed by a frozen final version and project archival. The active plan owns the exact acceptance scope; this file owns execution rules. General-purpose agent development is no longer an active goal.
- Reuse the current Guidance workflow, ModelIR, registered solvers, and mathematical evidence kernel. New harnesses, production sandboxes, SDK migrations, orchestration replacements, and broader problem coverage are outside the closeout scope unless the user explicitly changes that scope.
- New generated-code execution is outside this closeout scope. Existing isolation guarantees still apply: no unrestricted host fallback, no inherited credentials or configuration secrets, and explicit file, network, dependency, time, memory, and output limits for any approved isolated experiment.
- Keep exploratory observations, assumptions, and formally accepted results distinct. Both ModelIR and code execution must retain source, transformation, version, run, and validation evidence before their results can support final claims.
- Keep the existing workflow responsible for execution state, budgets, cancellation, recovery, and validation. A model completion message, successful script, or second LLM opinion is not sufficient to mark mathematics verified; a new harness is not a prerequisite for closeout.
- Preserve evidence and failure-isolation guarantees. Do not create a second result authority, hidden fallback, or parallel production workflow to make the demonstration pass.
- Freeze the single task and acceptance criteria before live execution. Only fix reproduced blockers for that task; retain unrelated historical failures as explicit limitations. Do not treat one successful subtask as full-problem, generalization, or competitive-performance evidence. Stop feature development once archival acceptance is satisfied.
- Real API tests use only `deepseek-flash` within the authorized data scope. Exclude biological research problems. Keep failed runs and unknown usage in the ledger; do not silently expand budgets or resample until success.

## Verify Before Diagnosing

- Never propose a refactoring, claim a capability gap, or mark code as dead based solely on static reading or file line counts.
- Every architectural diagnosis or boundary-replacement proposal must be backed by a reproducible test, runtime experiment, or verified cross-module call graph (including `app/guidance/`, `app/polish/`, and `app/core/`).
- File-size splitting is secondary to behavioral and mathematical correctness; do not split modules unless explicitly scheduled in the active plan.

## Test First

- Every new behavior or bug fix must follow Red-Green-Refactor.
- Write the smallest failing unit or contract test before production code.
- Run the test and confirm it fails for the intended missing behavior.
- Implement only enough production code to pass, then refactor with tests green.

## Refactor Instead of Patching

- If the current module boundary conflicts with the requested behavior, do not add an `if`/`else`, feature flag, compatibility branch, or pass-through adapter.
- Stop implementation and explain the conflicting data flow and coupling with empirical evidence.
- Propose the module-level replacement, including affected files, migration boundary, and verification.
- Obtain explicit user approval before replacing the conflicting module.
- Apply existing approval to its stated scope without asking again. A direction or plan update alone does not mark a new implementation complete or approve unspecified module replacements; prepare the concrete migration and verification before requesting any additional approval.

## Plan State

- After completing each subtask, immediately change its checkbox from `[ ]` to `[x]` in the long-term plan.
- Keep exactly one explicit `CURRENT` step in the plan.
- Compact completed phases into concise locked invariants rather than accumulating historical `[x]` logs.
- Remove obsolete plan versions instead of archiving them beside the active plan.

## Mandatory Current-State Summary

After every modification, report a `现状摘要` containing:

1. The active core data flow.
2. The most dangerous coupling or boundary violation.
3. What changed in this modification.
4. Which long-term-plan checkbox was completed.
5. The next unchecked step.
