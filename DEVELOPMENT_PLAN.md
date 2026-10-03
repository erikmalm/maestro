# Maestro development plan

Plan date: 2026-10-03. The current product supports real chat, persistent history, manual to-dos, provider setup and usage accounting. This branch adds private memory, shared local generation and opt-in background reflection. Local Ollama generation has been verified; remote API/accounting and bounded hosted search have synthetic integration coverage. Hosted search requires a successful test with the user's key.

There is one active connection with saved model preferences and per-message local choices. The chat UI always uses the chat role; the API retains a planning/orchestrator preference. Accepted private memory is recalled in relevant local chats. Background reflection optionally curates normalized memories through two-model review and maintains scheduled identity/lesson notes with a private journal. Maestro cannot execute a to-do, delegate work or automatically route specialist tasks yet. The [architecture](docs/ARCHITECTURE.md) and [memory/reflection design](docs/MEMORY_AND_REFLECTION.md) distinguish working behavior from upcoming increments.

## Product goal

Build a personal AI coordinator that runs locally and turns conversation and to-dos into useful, bounded work. It should select appropriate local or remote models, delegate selected assignments, review outcomes and keep a useful result or a clear stop reason. Progress, token usage and estimated spend should remain visible.

Keep credentials and personal workspace data outside the public checkout. Private memory should carry provenance and project scope, support user corrections and deletion, and eventually help later tasks without indiscriminate sharing. Integrations should operate only on configured sources and permitted actions. MarketPulse document access and GitHub coding tasks are intended capabilities, with no assumption that either integration already works.

The first supported setup is one user on one Windows computer. Deliver each capability as a small working increment; add navigation and controls when their behavior is implemented.

## Current foundation

- **Workspace:** separate persistent chats with live Ollama or compatible API replies, editable titles and optional one-search-per-message Ollama web search. New chat preserves earlier conversations; deleting one retains shared usage accounting.
- **Tasks:** manual create, complete/reopen and delete operations without model calls.
- **Settings and usage:** one connection with chat/orchestrator model preferences, per-message local choices, OS/session/mounted credentials, search setup, output/context controls, spend/token limits, actual model usage and uncertain-charge reconciliation.
- **Private storage:** SQLite outside Git; real conversations and accounting survive restart. Previous live history migrates into one conversation. Earlier prototype-only records remain archived privately, with simulated messages excluded from active chat.
- **Private memory:** dedicated Memory view with interactive map, searchable list, sources, proposals and reflection journal; save/edit/forget, workspace/conversation scopes and bounded lexical recall. Settings contains configuration and links to Memory. Memory and derived replies stay local; relevant IDs are recorded. Human edits pin records; optional automatic curation preserves provenance and deletion barriers.
- **Background reflection:** durable chat jobs plus periodic working-note maintenance, formation/review with at most two calls, one idle worker, conservative budgets, source/revision rechecks, restart recovery and private journal.
- **Task configuration:** independent saved reflection/extraction/coding models, output/recall caps and worker timing/budgets. Reflection can be enabled or paused; see [saved configuration](docs/MEMORY_AND_REFLECTION.md#saved-configuration).

Chat history stays separate while usage and limits are shared. A first successful reply may trigger one bounded title call using the original model, a short first-message excerpt, no tools and the remaining request allowance. A first-message title is the fallback; manual names always win.

The current generation path supports one request at a time. API restart recovery assumes that the API process owns all generation. These are useful foundations for a task runtime, but neither establishes delegation.

## Podman deployment

The application container, lifecycle launcher, trusted host endpoint, mounted secrets and private snapshots are implemented; the [runbook](docs/CONTAINERS.md) describes setup and verification. Isolated Podman checks verify model choices, access controls, resource limits, recreation, backup/restore and mounted secrets. The acceptance tasks below remain the checklist when migrating a real workspace or changing deployment configuration. Maestro serves FastAPI and the built frontend from one container, with a private data volume and the existing Windows Ollama service. Moving inference into a container follows a measured need and verified GPU access.

Host model discovery and a bounded `qwen2.5:7b` reply have also been verified through the Windows loopback tunnel, with actual token accounting and no provider API charge.

| ID | Task | Acceptance | Depends on |
| --- | --- | --- | --- |
| POD-01 | Build a reproducible application image. | A multi-stage build uses locked dependencies and runs the built app as a non-root user. A `.containerignore` excludes credentials, private data and local build artifacts. The image serves the frontend without checkout mounts or Node in the runtime. | None |
| POD-02 | Verify and configure host Ollama access. | The Podman container can list installed models and generate through the Windows Ollama service. A narrowly configured trusted endpoint works at setup and dispatch; arbitrary remote Ollama endpoints, credential forwarding and cloud fallback remain rejected. Verify the required Windows/WSL networking before changing host service bindings. | POD-01 |
| POD-03 | Preserve the local browser boundary. | Bind the API for container port forwarding and publish it only on host loopback. Existing Host, Origin, session and CSRF checks pass; neither Maestro nor Ollama needs LAN exposure. | POD-01 |
| POD-04 | Persist, migrate and restore the private workspace. | A consistent SQLite backup imports into a writable private volume with correct permissions. Chats, tasks, archived records and accounting survive container recreation and a restore rehearsal. Document endpoint adjustments, migration and rollback; credentials remain outside the database and image. | POD-01 |
| POD-05 | Adapt credential storage and settings UI. | Define supported session-only and externally managed secret modes for provider and search keys. Expose actual storage capabilities; forms use a supported default and accurate labels. Rotation, removal and restart behavior match the chosen mechanism, including read-only mounted secrets. Windows Credential Manager continues to work in the native runtime. | POD-01 |
| POD-06 | Implement container lifecycle and exclusive workspace ownership. | Start, stop, readiness, restart and update/rollback commands identify the owned container. Run one API worker/replica and prevent simultaneous Windows/container ownership of the same database. Interruption recovery preserves recorded usage and uncertain charges; missing Ollama produces a recoverable connection error. Keep this ownership rule until the durable-worker lease design is implemented. | POD-03, POD-04 |
| POD-07 | Set and observe deployment resource limits. | Record effective WSL memory/GPU availability and explicit CPU/RAM limits for the Maestro container. Keep inference resource policy separate because Ollama remains on the host. Health checks and bounded logs diagnose startup, storage and provider failures without exposing keys or conversation content. | POD-01 |
| POD-08 | Verify the container path and publish its runbook. | Isolated Podman fixtures exercise the served frontend, provider/search setup, local access checks, credential redaction, recreation persistence, interruption recovery and restore. Automated checks use synthetic services and no personal keys or paid calls. Document build, start/stop, secret setup, backup and update/rollback. | POD-02 through POD-07 |

## 1. Separate generation from chat

Implemented foundation: `Provider.generate_context` accepts explicit bounded instructions/messages and an installed local model through the same reservation, dispatch and settlement path as chat. It snapshots configuration, forces model unloading and excludes search, automatic recall, titles and chat mutations. Named profiles and durable attempt ownership remain future extensions; background callers must initialize the workspace first.

Acceptance:

- Existing local/API chat, search, context checks and uncertain-charge handling still work.
- A synthetic non-chat caller can supply its own context and profile without receiving unrelated conversation history.
- Every call reserves and settles through the same ledger, with no duplicate accounting or bypass of limits.

## 2. Complete one durable task

Background reflection implements REF-01–03 in the [memory design](docs/MEMORY_AND_REFLECTION.md#recorded-next-tasks), using chat evidence and a worker inside the sole API process. REF-04 now implements optional automatic formation/review; quality evaluation continues. REF-05 adds a persistent six-hour default schedule. The separate task-worker design below still requires ownership-aware recovery before cross-process dispatch.

REF-06 adds optional answer ratings/comments and selects complete exchanges for the same scheduled worker, using a short intent/accuracy/clarity rubric. Feedback uses existing messages and SQLite, with no inference on submission. Background context, exchange sampling and recall are configurable; compare repeated mistakes before/after reviewed lessons to evaluate quality. Per-answer model checks and session summaries remain deferred.

MEM-02 adds the interactive map of existing memory and source references. REF-07 makes the same worker prefer useful refinement, working-note consolidation, removal or abstention; it rotates managed records, retains pins, skips identical updates and handles overlapping dependency removals. Evaluate whether repeated passes preserve unique information and reduce generic or duplicate notes. Both increments reuse existing storage and generation; general task delegation remains the later execution step.

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

Record these model-specific tasks within this milestone:

| ID | Task | Acceptance | Depends on |
| --- | --- | --- | --- |
| MODEL-01 | Discover and validate installed models. | Refresh the installed-model list separately from loaded-model status. Check completion/tool capabilities needed by the task, detect missing or changed model versions, and show an actionable error without automatic downloads or cloud fallback. | Shared generation service; saved profiles |
| MODEL-02 | Define model switching and memory policy. | Chat, tasks, search and titles share one generation slot. Begin with one resident model, profile-specific context limits and a deliberate idle-unload policy. Avoid unloading an in-flight model; document the effect of server-wide Ollama settings on other clients. Test switching, cancellation and failure without losing accounting or leaving a slot reserved. | Durable task ownership; saved profiles |
| MODEL-03 | Benchmark role suitability and memory use. | A preliminary [4K memory probe](docs/LOCAL_MODELS.md) compares Qwen, gpt-oss and Devstral. Extend it with repeated realistic tasks, warm/switch latency, complete RAM/VRAM and 4K/8K context measurements. Include chat and 8K search; set defaults from measured quality/responsiveness before routing. | MODEL-01, MODEL-02 |

Acceptance: synthetic tasks dispatch to distinct configured profiles, retain separate context, obey the shared ledger and reject disallowed remote sharing. A local task can run without cloud inference calls.

## 4. Delegate and review within one allowance

Give the coordinator a typed handoff to one specialist, including assignment, profile, selected context, criteria and limits. Persist parent/child ownership, result, usage and stop reason. Start with a coordinator and one specialist; use a reviewer only when a concrete task needs a check against its criteria.

All descendants share the parent's call/token/time/spend allowance and tool scope. Bounded revision can address unmet criteria, stopping on completion, cancellation, no progress or exhausted limits. Model agreement alone is not proof that work succeeded.

Acceptance: a synthetic task delegates an assignment to another profile, receives its result and records the actual calls. An incomplete result can trigger one useful revision; every configured bound stops further work with a visible reason. A child cannot grant itself more context, tools or budget.

## Later increments

**Projects:** group existing chats and tasks when shared project context becomes useful. Add project behavior with its UI rather than introducing unused records or navigation now.

**Memory evaluation (REF-04):** compare no-memory, explicit-memory and automatic curation; measure usefulness, false/stale facts, abstention, latency and resource use. Automatic mode requires source validation, protected human edits and independent model review. Its synthetic checks and local probes do not establish broad quality. The durable worker and user-reviewed exact-excerpt proposals implement REF-01–03; details are in [MEMORY_AND_REFLECTION.md](docs/MEMORY_AND_REFLECTION.md#recorded-next-tasks).

**Scoped integrations:** start with read-only retrieval from a user-selected MarketPulse API, export or document directory. Inspect its actual contract during that increment. Enforce source/path scope and show citations, dates and unavailable evidence. External writes require a configured action scope and stable action identity so restart cannot duplicate them.

**Coding tasks:** add isolated worktrees in explicitly selected repositories, meaningful checks and public-safe draft PRs. Keep private task evidence local. Repository access and draft preparation do not authorize merging, deploying or replacing the running application. Execution controls and credential isolation need enforcement beyond merely using a worktree.

**EXEC-01 — Isolate future coding execution:** give coding attempts their own selected repository mounts, credentials, tool/network scope, resource/time limits and cleanup. Application-container deployment does not complete this task; execution containers must enforce the task's permissions independently.

**Code improvements:** use concrete failures and user feedback to propose a change with measurable criteria. Start with a private proposal the user can turn into a task. Later connect it to isolated coding, baseline/regression checks, a draft PR and post-activation outcomes. Keep revision/evaluation within fixed allowances; tie code-change reflection to verified task outcomes.

**Automation and polish:** add opt-in schedules, replayable live status and streaming when they improve the working task path. Scheduled work uses the same limits and crash recovery. Remote access needs its own authentication design.

## Verification

Use synthetic models and public-safe fixtures for automated checks; CI requires no personal keys or paid calls. Cover reservation concurrency, attempt ownership, restart recovery, limit/cancel enforcement, context separation, credential redaction and session/CSRF protection as those capabilities are added. Use browser checks for the implemented chat, tasks, settings and usage flows.

Live checks remain deliberate and bounded: a running local model for generation, or an explicitly configured remote profile and allowance. A model-list response does not prove generation; simulated results do not prove delegation; a reviewer accepting a proposal does not prove an improvement.

The next memory target is a measured comparison of explicit and automatically curated records, including periodic working notes, correction and forgetting. One real durable task remains the execution target before expanding profiles/delegation. Both reuse chat's generation/accounting path. Podman checks preserve the single-owner runtime.
