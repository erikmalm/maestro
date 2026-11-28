# Maestro session handoff

Checkpoint: 2026-11-28, Europe/Stockholm. Check live GitHub before acting.

## Current delivery

- **PR #57 merged as `63979fc`**, with the same file tree as reviewed head
  `936ee28`. Fresh Copilot review [6096498883](https://github.com/erikmalm/maestro/pull/57#pullrequestreview-6096498883)
  recommends approval with no open findings. The user explicitly approved merging.
  Recorded validation: 905 backend tests; 113 browser tests and production build.
  Independent simplicity review found no additional issue. Keep #36 open; #20's
  remaining accounting labels are separate.
- Main now supports complete accepted supplied source text up to 256 KiB, with
  capture/capacity and processing bounds. Legacy chat still clips search excerpts
  to 1,200 characters. Public Research on main remains saved-evidence-only.
- **#43 delivered in PR #58, merged as `b02b193` with user approval.** Evidence
  supports local keyword/domain/UTC-date search. Fresh Copilot review of `8b10ae9`
  recommends approval with no open findings. Validation: 123 browser tests at the
  baseline, all 24 Evidence tests after the incomplete-results fix, 16 API checks,
  build, disposable real-backend filters/pins/withdrawal, and desktop/mobile checks.
- **Candidate #29:** `feat/targeted-research-acquisition` adds an explicit,
  default-off research mode, one gap-driven acquisition, capture, reassessment,
  writing and review. All 923 backend tests pass in 203.159 seconds; 26 Research
  browser tests/build and independent frontend/backend reviews pass. The review
  reproduced and fixed capacity-preflight and terminal/capture races; 18 new test
  methods cover the acquisition seams. All 127 browser tests and the production
  build pass after #58 integration. Copilot's capacity finding is fixed by shared
  stage-input construction. The failing-before boundary now spends zero searches;
  a roomier control completes four stages/one search. All 137 focused checks and
  independent follow-up review pass. Fresh GitHub review remains required.
  The first
  deliberate live attempt at `a8e925f` stopped during cold model loading after
  45 seconds: one local transport, zero hosted searches, unknown completion.
  Preserve its reservation; do not retry or clear it without supported recovery.
  Report: `%TEMP%/maestro-pr59-live-6ubiikvu/report.json`. A request to restart the
  shared Ollama server, recover and retry once is pending user confirmation.
  No live acquisition/reuse success or deployment is claimed.

## Autonomous pilot

The user authorized focused automatic progress toward the search/evidence-library
goal. Follow `AGENTS.md`: small demonstrable PRs, independent correctness/simplicity
review and fresh GitHub review after substantive changes. The user retains merge
approval. Resolve routine implementation issues; stop for material product or
architecture decisions. Copilot quota was increased; keep review requests sensible.

Argus was inspected read-only at `C:\GitHub\argus`. It already owns market,
financial and private portfolio records. Future integration should use its API;
this milestone adds no financial actions or finance-specific store to Maestro.
Preserve Argus's existing uncommitted work.

## Next actions and bounds

1. Finish #29 review and the #58 integration checks. Recover the live test only
   after the user's answer, then demonstrate acquisition/reuse before merge approval.
2. Preserve #29's four
   model calls, 120 seconds, one hosted search/run and the shared daily cap.
   Protect reassessment plus writing/review capacity at actual dispatch. Capture
   survives answer failure; failed/disabled saving has no invented capture IDs.
3. Verify acquired content beyond character 1,200 and a repeat that reuses its
   exact saved version without another hosted request; fixture checks cover both.
4. Continue #30 bounded correction, #38 routing/caller migration, then #31 code
   retirement. Calculations #52, graphs and full-page/PDF acquisition remain later.

The user approved up to seven calls/180 cumulative seconds for #30, with unchanged
token/spending limits and one hosted search. #29 retains four calls/120 seconds.

Current/refresh claims still require independent origin verification; a newly
retrieved provider snippet cannot bypass that policy. Keep source pins, dates,
privacy and credential fences. Do not migrate or seed personal storage. The
intermittent Windows shutdown issue remains tracked separately in #56.

## Working locations

| Checkout | Purpose |
| --- | --- |
| `C:\GitHub\maestro` | Synced main at `b02b193` |
| `C:\GitHub\maestro-bounded-source-content` | Preserved reviewed #57 branch |
| `C:\GitHub\maestro-evidence-search` | Preserved reviewed #43 branch |
| `C:\GitHub\maestro-targeted-research-acquisition` | #29 implementation |
| `C:\GitHub\maestro-review-triage` | #30 correction implementation, based on #29 |

Use the project's pinned `.venv` for backend checks. Browser suites use port 8777;
coordinate runs across worktrees and never stop another user's preview to free it.
