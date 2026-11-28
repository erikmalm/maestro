# Maestro session handoff

Checkpoint: 2026-11-28, Europe/Stockholm. Check live GitHub before acting; this is a checkpoint, not a status feed. Follow `AGENTS.md`.

## Delivered on main

- Main is `b02b193`: PR #57 bounded capture/storage (`63979fc`), then PR #58 keyword/domain/UTC-date Evidence search. Fresh Copilot reviews recommended approval; the user approved both merges. Main is clean and public Research remains saved-evidence-only.
- Accepted supplied source text is bounded at 256 KiB. Keep #36 open for remaining acceptance and #20 separate. Legacy chat retains its excerpt clipping.

## Candidates

- **PR #59 / #29**, `feat/targeted-research-acquisition`, tested runtime `a25eee7`: explicit default-off acquisition, durable capture, reassessment, writing and review. All **925 backend tests passed (200.296s)**; full **127 browser tests/build** and desktop/mobile checks pass. The known-envelope and minimum finishing-schema/history findings are fixed, including repeated qualifiers; **162 focused checks** and independent follow-up review pass. Fresh GitHub review and live acquisition/reuse remain gates.
- **PR #60 / #30**, `feat/research-review-triage`, stacked on #59: one justified rewrite or evidence-gap correction in the original run. New answers capture the approved **7 calls / 180 cumulative seconds**; token/spend caps and one hosted search stay unchanged. Old runs retain captured policies; #29 alone keeps 4 calls/120 seconds. The targeted-row no-progress finding is fixed locally: accepted-row paraphrases cannot replace an unchanged rejection. All **70 focused checks**, **39 combined acquisition/correction checks** and independent integration review pass. Final broad validation, fresh GitHub review and real-model triage measurement remain; no improved model classification is yet claimed.

## Preserved live-test gate

The first deliberate acceptance at `a8e925f` stopped during cold model loading after 45 seconds: **one local transport, zero hosted searches, unknown completion**. Preserve its reservation and report:

`%TEMP%/maestro-pr59-live-6ubiikvu/report.json`

A user question is pending: restart shared Ollama, recover this reservation through the supported API, preload the model, then retry once with at most one hosted search. **No restart approval has arrived.** Do not restart, clear the reservation or perform more inference until resolved. Restarting may interrupt other local AI apps. The prepared private acceptance script is `%TEMP%/maestro-pr59-live-plan-20261128/accept_targeted_research.py`; its default expected head is old, so inspect and supply the final reviewed head explicitly before any approved retry. It must stop on another unknown completion and retain its workspace.

## Next steps

1. Finish #59 fresh review; after approved recovery, demonstrate one acquisition plus repeat reuse of the exact saved pins. Obtain user merge approval only when ready.
2. Finish #60 validation/review and bounded local triage trial. Merge #59 first, retarget #60 to main and retain user merge approval.
3. Continue #38 routing, then #31 obsolete program-code retirement. #38's issue records the prepared scope and eight frozen routing cases. Combine route and requirements in one memory-free assessment under the same owner/budget; default cutover follows measured results and human guidance.

## Working bounds

Reuse existing services; no new dependency, datastore or generic engine. Current/refresh claims still require independent origin verification. Preserve source versions, private memory and history; no personal migration or deployment. Graphs, calculations #52, full-page/PDF capture and financial actions are outside this milestone.

Argus at `C:\GitHub\argus` was inspected read-only. It owns market/financial/portfolio records; future integration should consume its API. Preserve its uncommitted work.

| Checkout | Purpose |
| --- | --- |
| `C:\GitHub\maestro` | Synced main |
| `C:\GitHub\maestro-targeted-research-acquisition` | PR #59 |
| `C:\GitHub\maestro-review-triage` | PR #60 |

Other worktrees remain preserved; use `git worktree list` to locate them. Use the pinned `.venv` for backend checks. Browser suites share port 8777: coordinate runs and never stop another user's preview. The intermittent native shutdown issue is tracked in #56.
