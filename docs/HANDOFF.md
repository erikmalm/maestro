# Maestro session handoff

Checkpoint: 2026-11-28, Europe/Stockholm. Check live GitHub before acting; this is a checkpoint, not a status feed. Follow `AGENTS.md`.

## Delivered on main

- Main is `b02b193`: PR #57 bounded capture/storage (`63979fc`), then PR #58 keyword/domain/UTC-date Evidence search. Fresh Copilot reviews recommended approval; the user approved both merges. Main is clean and public Research remains saved-evidence-only.
- Accepted supplied source text is bounded at 256 KiB. Keep #36 open for remaining acceptance and #20 separate. Legacy chat retains its excerpt clipping.

## Candidates

- **PR #59 / #29**, head `67eb727`, tested runtime `a25eee7`: explicit default-off acquisition, durable capture, reassessment, writing and review. **925 backend tests (200.296s)**, **127 browser tests/build**, 162 focused checks and independent reviews pass. Fresh Copilot review has **no open issues / Needs a closer look**, solely for the unverified live acquisition/repeat. Both code findings are fixed and threads resolved.
- **PR #60 / #30**, head `197340b`, tested runtime `fe21ca4`, stacked on #59: one justified correction with targeted-row no-progress stops. New answers capture the approved **7 calls / 180 cumulative seconds**, unchanged token/spend/search caps; old runs keep their policy. **947 backend tests (212.376s)**, 26 Research browser tests/build, 70 focused and 39 combined checks plus independent reviews pass. Fresh Copilot review has **no open issues / Needs a closer look**, solely for live acquisition/reuse and intended-model triage. No measured triage improvement is claimed.
- **PR #61 / #38**, `feat/reviewed-chat-routing`, tested runtime `c56d4d7`, stacked on #60: opt-in **Review request** in Workspace; ordinary Send remains unchanged. Copilot's `ce68602` findings (delayed message usage and stale Workspace return) are fixed and independently reviewed. Pending/unknown token totals remain distinct from zero; exact settlement refreshes the same message without letting unrelated workspace growth block accounting. All **983 backend tests (201.767s)**, **136 browser tests**, build and desktop/mobile checks pass, with **121 focused backend checks**. An earlier full run hit known native shutdown #56; eight native cases and the complete rerun passed. Check the PR for fresh GitHub review. The eight-case runner has 18 fake-transport/grader checks passing; real routing/rewrite/Research parity remains unmeasured.

## Preserved live-test gate

The first deliberate acceptance at `a8e925f` stopped during cold model loading after 45 seconds: **one local transport, zero hosted searches, unknown completion**. Preserve its original report and accounting history:

`%TEMP%/maestro-pr59-live-6ubiikvu/report.json`

The user approved restart/recovery/preload and one acceptance retry (at most one hosted search), then manually quit Ollama after tool policy rejected command-based process control. Old server `24232` was confirmed gone; replacement `26100` returned an empty model list. Supported `Provider.recover_local` settled the preserved reservation conservatively, retaining the original report unchanged. Recovery evidence is in `%TEMP%/maestro-pr59-recovery-f77e1e8220224c76a9e308ac4db6fd4d/`.

The empty 32K preload returned HTTP 500 after 56.041s: the CUDA runner crashed at warm-up with `shared object initialization failed` / `0xc0000409`. **No acceptance retry or hosted search ran.** Runtime readiness confirmation is pending before one further empty preload. `%TEMP%/maestro-ollama-eager.ps1` is an independently reviewed upstream-workaround trial if needed, setting `CUDA_MODULE_LOADING=EAGER` only for the next Ollama process; no permanent environment/settings change. Do not infer until readiness is verified. The private acceptance script is `%TEMP%/maestro-pr59-live-plan-20261128/accept_targeted_research.py`; inspect it and supply reviewed head `67eb727411ce032ee72eb5e34a1ce304fe05614f` explicitly (its default is old). Stop on another unknown completion and retain the workspace.

## Next steps

1. Verify Ollama readiness after the pending local step; if preload succeeds, use the still-unspent single #59 acquisition/reuse retry. Obtain user merge approval only when ready.
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
