# Maestro session handoff

Checkpoint: 2026-11-28, Europe/Stockholm. Check live GitHub before acting; this is a checkpoint, not a status feed. Follow `AGENTS.md`.

## Delivered on main

- Main is `b02b193`: PR #57 bounded capture/storage (`63979fc`), then PR #58 keyword/domain/UTC-date Evidence search. Fresh Copilot reviews recommended approval; the user approved both merges. Main is clean and public Research remains saved-evidence-only.
- Accepted supplied source text is bounded at 256 KiB. Keep #36 open for remaining acceptance and #20 separate. Legacy chat retains its excerpt clipping.

## Candidates

- **PR #59 / #29**, head `67eb727`, tested runtime `a25eee7`: explicit default-off acquisition, durable capture, reassessment, writing and review. **925 backend tests (200.296s)**, **127 browser tests/build**, 162 focused checks and independent reviews pass. Fresh Copilot review has **no open issues / Needs a closer look**, solely for the unverified live acquisition/repeat. Both code findings are fixed and threads resolved.
- **PR #60 / #30**, head `197340b`, tested runtime `fe21ca4`, stacked on #59: one justified correction with targeted-row no-progress stops. New answers capture the approved **7 calls / 180 cumulative seconds**, unchanged token/spend/search caps; old runs keep their policy. **947 backend tests (212.376s)**, 26 Research browser tests/build, 70 focused and 39 combined checks plus independent reviews pass. Fresh Copilot review has **no open issues / Needs a closer look**, solely for live acquisition/reuse and intended-model triage. No measured triage improvement is claimed.
- **#38 candidate**, `feat/reviewed-chat-routing`, tested runtime `c88c185`, stacked on #60: opt-in **Review request** in Workspace; ordinary Send remains unchanged. One owned route/requirements assessment, then direct/clarification or existing Research. **980 backend tests pass (191.253s)**. Independent backend/frontend and model-capture reviews are clear; the full 133-browser baseline, all 32 Research cases, production build and desktop/mobile inspection pass, with two mobile follow-up checks after presentation fixes. The eight-case runner has 18 fake-transport/grader checks passing; its synthetic continuations do not prove real rewrite quality. Check the candidate PR for fresh GitHub review. Intended-model routing and fully real direct/rewrite/Research parity remain gates.

## Preserved live-test gate

The first deliberate acceptance at `a8e925f` stopped during cold model loading after 45 seconds: **one local transport, zero hosted searches, unknown completion**. Preserve its reservation and report:

`%TEMP%/maestro-pr59-live-6ubiikvu/report.json`

A user question is pending: restart shared Ollama, recover this reservation through the supported API, preload the model, then retry once with at most one hosted search. **No restart approval has arrived.** Do not restart, clear the reservation or perform more inference until resolved. Restarting may interrupt other local AI apps. The prepared private acceptance script is `%TEMP%/maestro-pr59-live-plan-20261128/accept_targeted_research.py`; its default expected head is old, so inspect and supply the final reviewed head explicitly before any approved retry. It must stop on another unknown completion and retain its workspace.

## Next steps

1. After approved recovery, demonstrate #59 acquisition plus repeat reuse of the exact saved pins. Obtain user merge approval only when ready.
2. Complete #60's bounded local triage trial. Merge #59 first, then retarget #60 to main; retain user merge approval.
3. Finish the #38 opt-in candidate's review and intended-model routing/rewrite parity. Default cutover requires measured results and human guidance; only then migrate remaining supported callers and retire obsolete program code in #31.

## Working bounds

Reuse existing services; no new dependency, datastore or generic engine. Current/refresh claims still require independent origin verification. Preserve source versions, private memory and history; no personal migration or deployment. Graphs, calculations #52, full-page/PDF capture and financial actions are outside this milestone.

Argus at `C:\GitHub\argus` was inspected read-only. It owns market/financial/portfolio records; future integration should consume its API. Preserve its uncommitted work.

| Checkout | Purpose |
| --- | --- |
| `C:\GitHub\maestro` | Synced main |
| `C:\GitHub\maestro-targeted-research-acquisition` | PR #59 |
| `C:\GitHub\maestro-review-triage` | PR #60 |
| `C:\GitHub\maestro-reviewed-chat-routing` | #38 opt-in candidate |

Other worktrees remain preserved; use `git worktree list` to locate them. Use the pinned `.venv` for backend checks. Browser suites share port 8777: coordinate runs and never stop another user's preview. The intermittent native shutdown issue is tracked in #56.
