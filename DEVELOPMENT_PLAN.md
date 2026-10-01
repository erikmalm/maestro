# Maestro development plan

Plan date: 2026-10-01. The current product supports real chat, persistent history, manual to-dos, provider setup and usage accounting. Local Ollama generation and two-turn context have been verified. Optional remote API setup/accounting and bounded hosted Ollama search have synthetic integration coverage. Hosted search access requires a successful test with the user's key.

There is one active provider/model connection. Maestro cannot execute a to-do, delegate work, choose models for specialist tasks or recall private memory automatically. Demo dashboards, simulated runs and the reflection prototype have been removed from the active application. The [architecture](docs/ARCHITECTURE.md) describes the actual runtime and its next boundaries.

## Product goal

Build a personal AI coordinator that runs locally and turns conversation and to-dos into useful, bounded work. It should select appropriate local or remote models, delegate selected assignments, review outcomes and keep a useful result or a clear stop reason. Progress, token usage and estimated spend should remain visible.

Keep credentials and personal workspace data outside the public checkout. Private memory should carry provenance and project scope, support user corrections and deletion, and eventually help later tasks without indiscriminate sharing. Integrations should operate only on configured sources and permitted actions. MarketPulse document access and GitHub coding tasks are intended capabilities, with no assumption that either integration already works.

The first supported setup is one user on one Windows computer. Deliver each capability as a small working increment; add navigation and controls when their behavior is implemented.

## Current foundation

- **Workspace:** separate persistent chats with live Ollama or compatible API replies, editable titles and optional one-search-per-message Ollama web search. New chat preserves earlier conversations; deleting one retains shared usage accounting.
- **Tasks:** manual create, complete/reopen and delete operations without model calls.
- **Settings and usage:** one active model connection, OS/session credentials, search setup, output/context controls, spend/token limits, actual model usage and uncertain-charge reconciliation.
- **Private storage:** SQLite outside Git; real conversations and accounting survive restart. Previous live history migrates into one conversation. Earlier prototype-only records remain archived privately, with simulated messages excluded from active chat.

Chat history stays separate while usage and limits are shared. A first successful reply may trigger one bounded title call using the original model, a short first-message excerpt, no tools and the remaining request allowance. A first-message title is the fallback; manual names always win.

The current generation path supports one request at a time. API restart recovery assumes that the API process owns all generation. These are useful foundations for a task runtime, but neither establishes delegation.

## 1. Separate generation from chat

Create one generation service that accepts an explicit model profile and selected context per call. Extract the existing reservations, provider dispatch, search behavior and usage settlement into that service. Keep history selection in the chat layer and capture configuration/pricing per request.

Acceptance:

- Existing local/API chat, search, context checks and uncertain-charge handling still work.
- A synthetic non-chat caller can supply its own context and profile without receiving unrelated conversation history.
- Every call reserves and settles through the same ledger, with no duplicate accounting or bypass of limits.

## 2. Complete one durable task

Add one separate task worker using local Ollama and the shared generation service. A manually queued text-only task has selected context, completion criteria, a persisted attempt and a saved result. It can run with the browser closed. Start with one generation slot and show persisted status in Tasks.

Implement claims and leases before allowing another process to dispatch. Associate reservations and results with their owning attempts so API restart cannot interrupt a live worker and stale workers cannot overwrite newer results. Pause/cancel and cumulative call/token/time allowances block subsequent calls; retries keep consumed usage and unresolved reservations.

Acceptance:

- One queued task reaches a saved result with the browser closed.
- Pause, cancel and exhausted allowances stop new dispatches and show a reason.
- API and worker restarts preserve progress and accounting without duplicate claims or results.
- Chat and tasks share the generation allowance without oversubscribing it.

## 3. Route between saved model profiles

Add named local/remote model profiles after the task path works. Each profile records its endpoint, protocol, model, resource settings, pricing and a credential reference. The user selects a profile for a task; model, provider and prices are recorded for every call. Automatic routing can follow explicit selection.

Keep context sharing explicit. A task using a remote profile must not receive local-only source material or derived summaries. Credentials remain endpoint-scoped and never enter task prompts, memory or the database.

Acceptance: synthetic tasks dispatch to distinct configured profiles, retain separate context, obey the shared ledger and reject disallowed remote sharing. A local task can run without cloud inference calls.

## 4. Delegate and review within one allowance

Give the coordinator a typed handoff to one specialist, including assignment, profile, selected context, criteria and limits. Persist parent/child ownership, result, usage and stop reason. Start with a coordinator and one specialist; use a reviewer only when a concrete task needs a check against its criteria.

All descendants share the parent's call/token/time/spend allowance and tool scope. Bounded revision can address unmet criteria, stopping on completion, cancellation, no progress or exhausted limits. Model agreement alone is not proof that work succeeded.

Acceptance: a synthetic task delegates an assignment to another profile, receives its result and records the actual calls. An incomplete result can trigger one useful revision; every configured bound stops further work with a visible reason. A child cannot grant itself more context, tools or budget.

## Later increments

**Projects:** group existing chats and tasks when shared project context becomes useful. Add project behavior with its UI rather than introducing unused records or navigation now.

**Private memory:** add inspectable remember/forget, scoped recall and provenance before inferred memory or automatic curation. Corrections take precedence, deletion removes live derived indexes, and summaries inherit source-sharing restrictions. Evaluate prompt or memory changes against a baseline before promoting them; keep rollback available. All curation calls use the shared usage gate.

**Scoped integrations:** start with read-only retrieval from a user-selected MarketPulse API, export or document directory. Inspect its actual contract during that increment. Enforce source/path scope and show citations, dates and unavailable evidence. External writes require a configured action scope and stable action identity so restart cannot duplicate them.

**Coding tasks:** add isolated worktrees in explicitly selected repositories, meaningful checks and public-safe draft PRs. Keep private task evidence local. Repository access and draft preparation do not authorize merging, deploying or replacing the running application. Execution controls and credential isolation need enforcement beyond merely using a worktree.

**Code improvements:** use concrete task failures and user feedback to propose an explained change with measurable criteria. Start with a private proposal that the user can turn into an ordinary task. Later connect it to isolated coding, baseline/regression checks, a draft PR and measured post-activation outcomes. Keep revision and evaluation within fixed allowances; do not rebuild a reflection system before task outcomes exist.

**Automation and polish:** add opt-in schedules, replayable live status and streaming when they improve the working task path. Scheduled work uses the same limits and crash recovery. Remote access needs its own authentication design.

## Verification

Use synthetic models and public-safe fixtures for automated checks; CI requires no personal keys or paid calls. Cover reservation concurrency, attempt ownership, restart recovery, limit/cancel enforcement, context separation, credential redaction and session/CSRF protection as those capabilities are added. Use browser checks for the implemented chat, tasks, settings and usage flows.

Live checks remain deliberate and bounded: a running local model for generation, or an explicitly configured remote profile and allowance. A model-list response does not prove generation; simulated results do not prove delegation; a reviewer accepting a proposal does not prove an improvement.

The next acceptance target is one real durable task using the same generation and accounting path as chat. Complete that before expanding the model profile and delegation system.
