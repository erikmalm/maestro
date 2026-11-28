# Working on Maestro

Read `docs/HANDOFF.md` first and check live GitHub state before resuming a PR.
Use `DEVELOPMENT_PLAN.md` and tracker #18 for the existing delivery sequence.

## Small autonomous increments

- Define one demonstrable outcome, acceptance checks and exclusions before editing.
- Reuse the existing archive, index, provider, quota and owned-run services. Ask for
  a decision before adding dependencies, another datastore or a substantial
  architectural change. Simplify or split a growing change before expanding it.
- Keep one implementation owner per change. Parallelize investigation and review;
  give independent implementations separate worktrees and nonoverlapping scope.
- Get an independent review for correctness and simplicity. Explain each new
  abstraction and report production additions/deletions separately from tests/docs.
- Request GitHub/Copilot review for each finished code PR and again after
  substantive fixes. Record the reviewed commit; old resolved threads are not a
  fresh review. Evaluate findings against the acceptance criteria and scope.
  Use `gh api --method POST repos/erikmalm/maestro/pulls/<number>/copilot-code-review`
  to request review; poll reviews with GET rather than requesting it again.
- During the autonomous pilot, obtain the user's approval before merging. Stop
  for product ambiguity, architectural expansion or unresolved review disagreement.
  Resolve routine implementation decisions and test failures autonomously.
- Update the existing handoff briefly at delivery/blockers; avoid duplicate plans.

## Verification and data

- Use the pinned project environment: `.venv/Scripts/python.exe -m unittest
  discover -s tests -p "test_*.py"`. From `frontend`, use `npm run build` and
  `npm run test:ui -- <affected specs>`; run the relevant full checks before delivery.
- Use focused regression checks while editing. Repeat broader checks when code
  changes or unresolved failures justify them. UI changes need visual inspection.
- Use disposable synthetic workspaces for tests and previews. Preserve personal
  data, source versions, private memory, credentials and existing worktrees.
- Live provider calls need an explicit bounded purpose and existing authorization.
  Preserve shared limits, source pins, privacy fences and once-only dispatch.
- Argus is future integration context. Its market/portfolio data remains in Argus;
  this search/library milestone does not implement financial actions.
