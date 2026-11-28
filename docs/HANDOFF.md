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
- **Candidate #43:** `feat/evidence-search` adds local keyword/domain/UTC-date
  search to the existing Evidence page. No new datastore, dependency or module.
  All 123 browser tests and the production build pass. A disposable real-backend
  check verified combined filters, ordering, exact pins and source withdrawal.
  Desktop/mobile layouts were inspected. All data/transports were synthetic;
  zero hosted/model calls. GitHub review and merge are still delivery gates.
- **In progress #29:** `feat/targeted-research-acquisition` adds an explicit,
  default-off research mode, one gap-driven acquisition, capture, reassessment,
  writing and review. UI build/26 browser tests and independent frontend review
  pass; backend implementation, full validation and review are still in progress.
  Do not treat the branch as a delivered or live-verified acquisition path.

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

1. Finish #43 review and obtain merge approval. Refresh its branch against main.
2. Finish #29 with the existing journal/store/provider services. Preserve four
   model calls, 120 seconds, one hosted search/run and the shared daily cap.
   Protect reassessment plus writing/review capacity at actual dispatch. Capture
   survives answer failure; failed/disabled saving has no invented capture IDs.
3. Verify acquired content beyond character 1,200 and a repeat that reuses its
   exact saved version without another hosted request. Live verification remains
   a separate bounded acceptance step after fixture checks and review.
4. Continue #30 bounded correction, #38 routing/caller migration, then #31 code
   retirement. Calculations #52, graphs and full-page/PDF acquisition remain later.

Current/refresh claims still require independent origin verification; a newly
retrieved provider snippet cannot bypass that policy. Keep source pins, dates,
privacy and credential fences. Do not migrate or seed personal storage. The
intermittent Windows shutdown issue remains tracked separately in #56.

## Working locations

| Checkout | Purpose |
| --- | --- |
| `C:\GitHub\maestro` | Synced main at `63979fc` |
| `C:\GitHub\maestro-bounded-source-content` | Preserved reviewed #57 branch |
| `C:\GitHub\maestro-evidence-search` | #43 candidate and pilot instructions |
| `C:\GitHub\maestro-targeted-research-acquisition` | #29 implementation |

Use the project's pinned `.venv` for backend checks. Browser suites use port 8777;
coordinate runs across worktrees and never stop another user's preview to free it.
