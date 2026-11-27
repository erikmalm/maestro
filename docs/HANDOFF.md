# Maestro session handoff

Checkpoint: 2026-11-27, Europe/Stockholm. Read this first when resuming without the previous conversation. Recheck GitHub before acting; this file records a checkpoint, not a live status feed.

## Current state

- Main contains the saved-evidence answer loop (#22-27/#37), native-port work (#51) and acquisition journal (#28/PR #55). Its latest implementation commit is `9743c3e`; `93bd9c1` reconciled the overview documents. Later wrap-up commits contain documentation only.
- **[PR #57](https://github.com/erikmalm/maestro/pull/57) is open and unmerged.** Branch: `feat/bounded-source-content`. Last tested runtime: **`30a78cdbeb2b374e5ab3c84c8d7db13cb3693eca`**. A later documentation commit or merge may change the PR head without changing this runtime.
- This PR is the capture/storage safety slice of [#36](https://github.com/erikmalm/maestro/issues/36), not completion of that issue. Keep #36 open; [#20](https://github.com/erikmalm/maestro/issues/20) tracks remaining accounting labels separately.
- Public Research remains saved-evidence-only with zero hosted searches. No hosted acquisition, personal-data migration, deployment or new live-model verification was performed for this PR.

## Review and validation

**905 backend tests passed in 158.518 seconds** at runtime `30a78cd`, using the project virtual environment, including all native lifecycle checks. Independent final review passed 45 focused checks. The **113 browser tests and production frontend build** passed at baseline `af634d6`; frontend code is unchanged by the later review fixes. Documentation-only wrap-up does not require rerunning unchanged runtime checks.

The [Copilot review](https://github.com/erikmalm/maestro/pull/57#pullrequestreview-6092339810) targets **`aa5cf69`**, before the fixes. Its summary still says **Changes recommended** and lists two issues. Both underlying threads were verified resolved:

1. [Escaped-source screening](https://github.com/erikmalm/maestro/pull/57#discussion_r4722848254): valid maximum-size newline-, quote- and backslash-heavy sources now retain journal pins and Research reads. The finite JSON-escape allowance derives from the unchanged shared character budget; credential, node, depth and per-string protections remain enforced, including current and retained keys after rotation.
2. [Packet metadata fitting](https://github.com/erikmalm/maestro/pull/57#discussion_r4722848282): fitting reserves whole-source omissions and the longer JSON boolean value. Tight-cap regressions retain valid evidence without exceeding the packet cap.

Both fixes are in `30a78cd`. The earlier self-review read-budget fix is in `aa5cf69`: corrupt enlarged objects are read only to the validated manifest size plus one rejection sentinel. Its regression consumes 54 bytes instead of 2.25 MiB for nine five-byte objects. Older supported pins remain readable after lowering future-write settings.

**No fresh Copilot review of the fixed runtime has been submitted at this checkpoint.** Resolved threads and passing tests do not replace that fresh review. No additional review was requested during wrap-up.

## Immediate next action

1. Check PR #57's current head, reviews and unresolved threads. Request a fresh Copilot review of the current head; distinguish its commit from the older summary above. Address any new findings and verify their regressions.
2. After review acceptance and explicit merge authorization, merge PR #57. It was not merged during wrap-up.
3. Sync main, record the actual merge SHA in the overview/worklog and reconcile the trackers. Keep #36 open for remaining acceptance.
4. Start [#29](https://github.com/erikmalm/maestro/issues/29), the next architectural increment: one controlled hosted acquisition for a recorded evidence gap, followed by ingestion, reassessment, writing and review.

## Architectural constraints for the next increment

- Keep the existing private database, immutable archive, derived index, owned run/stage service and once-only acquisition journal. A second archive engine or provider client for ordinary model stages is unnecessary.
- PR #57 retains complete accepted supplied text up to an initial **256 KiB** supported ceiling. The maintainer accepted starting here while noting that larger evidence may need more. Measure payloads/refusals in #29 before increasing transport, capture, read, credential-screening and processing limits together. Model packets remain independently bounded.
- The legacy hosted adapter still clips excerpts to **1,200 characters** and caps response bytes at **131,072**. #29 must add a separately bounded untruncated targeted acquisition path, bind material limits into booking/attempt identity and preserve credential screening. Full-page/PDF acquisition remains separate.
- Ingest eligible acquired evidence before composition; a successful capture survives a failed answer. Distinguish a received response, durable saved pins, selected evidence and accepted claims.
- Preserve exact pins, dates, completeness, current permissions and source withdrawal. Byte identity and model agreement do not establish publisher truth.
- Preserve one cumulative run allowance, finishing holds, cancellation/restart fences, at most one hosted search/run and at most 20/day or lower settings. Do not reset budgets at stage boundaries or redispatch uncertain attempts.
- Continue **#29 → #30 → #38 → #31**: targeted acquisition, bounded correction, routing/caller migration, then retirement of replaced paths. Before #30, refine the known wrong-unit triage: “42 billion” against “42 million” is correctly rejected but was labelled `evidence_gap` instead of `writing_defect`.
- #20 accounting, optional Evidence search #43/graph #34, calculations #52 and broader task execution/delegation remain separate. Research #29 is the immediate implementation priority after this PR.

## Known limits and operational notes

- Dense sources with more than 512 matching token instances are omitted whole with visible limited-work metadata, including some small sources. This disclosed recall limitation is separate from integrity or credential checks.
- Windows native shutdown can intermittently hang after a peer reset on clean main with pinned dependencies; [#56](https://github.com/erikmalm/maestro/issues/56) records the reproduction. The final 905-test run passed every native check. No shutdown or security guard was relaxed.
- Tests use synthetic fixtures outside Git and do not establish broad model accuracy, stable inference latency or cloud-sync completion. Do not seed personal storage, import the old scaffold or delete preserved worktrees/data.

## Where to resume

| Location | Purpose |
| --- | --- |
| `C:\GitHub\maestro` | Main checkout; start with this handoff and the development plan. |
| `C:\GitHub\maestro-bounded-source-content` | Preserved PR #57 worktree on `feat/bounded-source-content`. |
| [DEVELOPMENT_PLAN.md](../DEVELOPMENT_PLAN.md) | Detailed implementation, test and review worklog. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target research workflow and delivered boundaries. |
| [ARCHIVE_POLICY.md](ARCHIVE_POLICY.md) / [SEARCH_CONTEXT_STORAGE.md](SEARCH_CONTEXT_STORAGE.md) | Candidate contract, limits and remaining acquisition/storage work. |

Inspect current state before modifying either checkout:

```powershell
Set-Location C:\GitHub\maestro
git fetch origin
git status --short
gh pr view 57 --repo erikmalm/maestro --json state,headRefOid,latestReviews,reviewRequests

Set-Location C:\GitHub\maestro-bounded-source-content
git status --short
git log -5 --oneline
```

Use the project's pinned `.venv`, not system Python, when runtime changes require verification:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

The important new regressions are in [test_context_content_bounds.py](https://github.com/erikmalm/maestro/blob/30a78cd/tests/test_context_content_bounds.py), [test_large_passage_bounds.py](https://github.com/erikmalm/maestro/blob/30a78cd/tests/test_large_passage_bounds.py) and [test_research_stage_source_bounds.py](https://github.com/erikmalm/maestro/blob/30a78cd/tests/test_research_stage_source_bounds.py) on the PR branch. Those files land on main only after the PR merges.

If Git Credential Manager hangs during a push, the verified per-command fallback is:

```powershell
$env:GIT_TERMINAL_PROMPT='0'
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin <branch>
```

No background review watcher remains active after this wrap-up. The next session should check GitHub afresh.
