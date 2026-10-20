# Maestro development plan

Checkpoint: 2026-10-20. The starting main `c63692f` contains the archive/read-only Evidence foundation (#17/#41/#42), architecture #40 and PR #44's accepted Source archive settings/status and disposable-store restart verification. That merged baseline passed 432 backend tests, 89 browser tests and the frontend build. This #22 branch adds bounded local candidate lookup and `POST /api/context/search`, separate from hosted acquisition, without chat or frontend changes. Broader storage protections/accounting remain in #36/#20. The [architecture](docs/ARCHITECTURE.md) defines the reviewed research workflow, which remains planned. These changes do not migrate personal storage or establish deployment.

The #22 increment passes 475 backend tests, including 29 candidate-store and 14 candidate-API tests. Review regressions cover decomposed source text, non-composing marks and Unicode stale postings using the existing SQLite tokenizer, plus rejection of ambiguous provider credential scopes. The unchanged frontend passed all 89 browser tests and the production build at `ecc015a`. Fixtures use disposable storage with no hosted searches or live-model calls. These checks establish local candidate behavior; they do not establish evidence acceptance or personal archive deployment.

There is one active connection with saved model preferences and per-message local choices. Local `chat_routing` selects the chat or configured orchestrator model for ordinary messages unless the user overrides it. Chat still uses one hosted-search round and at most two answer-model calls; it does not yet query the saved-source catalogue or automatically capture its hosted results. Separate requirements assessment, evidence acceptance, final answer review, live run events and specialist delegation remain planned. Accepted private memory is recalled in relevant local chats. Background reflection optionally curates normalized memories through review and maintains scheduled working notes with a private journal. Maestro cannot execute a to-do or delegate work yet. The [memory/reflection design](docs/MEMORY_AND_REFLECTION.md) describes that separate subsystem.

## Product goal

Architecture [PR #40](https://github.com/erikmalm/maestro/pull/40) is merged, completing #19. [PR #44](https://github.com/erikmalm/maestro/pull/44) delivered the first setup/status/restart slice of [#36](https://github.com/erikmalm/maestro/issues/36); bounded local lookup #22 is this branch's next increment. Old integration [PR #8](https://github.com/erikmalm/maestro/pull/8) is closed as superseded; its branch remains reference material, not a prerequisite to merge wholesale. Reuse only useful, narrowly reviewed foundations from it.

Build a personal AI coordinator that runs locally and turns conversation and to-dos into useful, bounded work. It should select appropriate local or remote models, delegate selected assignments, review outcomes and keep a useful result or a clear stop reason. Progress, token usage and estimated spend should remain visible.

For evidence-based requests, the architectural direction is requirements -> local candidates -> orchestrator evidence review -> targeted acquisition and ingestion -> answer writer -> final review. Every necessary stage receives the relevant shared objective and cumulative consumed/reserved/remaining allowance; application code enforces that same run-wide limit. Reuse valid evidence per requirement, research specific gaps and distinguish a writing correction from a need for new evidence. The initial store contains searches and source results, not a consolidated knowledge database. Finance, football and later domains can share those records and table/graph views. Curated knowledge and existing personal memory are separate capabilities. The overview defines this target; its roles do not require separate models or general task delegation, and documentation does not establish implementation or deployment.

Keep credentials and personal workspace data outside the public checkout. Private memory should carry provenance and project scope, support user corrections and deletion, and eventually help later tasks without indiscriminate sharing. Integrations should operate only on configured sources and permitted actions. MarketPulse document access and GitHub coding tasks are intended capabilities, with no assumption that either integration already works.

The first supported setup is one user on one Windows computer. Deliver each capability as a small working increment; add navigation and controls when their behavior is implemented.

Keep simplicity and removal of duplication explicit. Reuse old code only where it improves the replacement; do not force the entire archive integration into a new slice. Code retirement happens with each replacement and a final removal-only check, while source eviction/cache management remain later infrastructure. New frontend work uses JS React pages, cohesive data hooks and small reusable parameterized components following the Argus Stock page composition; leave existing typed modules in place until feature work needs to change them. The first Evidence page is a read-only list/detail view, not a port of the old finder/globe/replay machinery.

## Current foundation

- **Workspace:** separate persistent chats with live Ollama or compatible API replies, editable titles and optional bounded Ollama web search. New chat preserves earlier conversations; deleting one retains shared usage accounting.
- **Tasks:** manual create, complete/reopen, suggested-assignee changes and deletion without model calls. Every task records its initiator; optional periodic reflection adds up to two independently reviewed AI-created tasks per pass to the same list, without execution.
- **Settings and usage:** one connection with direct/orchestrator local chat routing, per-message model overrides, OS/session/mounted credentials, search setup, output/context controls, spend/token limits, actual model usage and uncertain-charge reconciliation.
- **Private storage:** SQLite outside Git; real conversations and accounting survive restart. Previous live history migrates into one conversation. Earlier prototype-only records remain archived privately, with simulated messages excluded from active chat.
- **Saved evidence:** immutable capture core, private SQLite catalogue/FTS storage, bounded metadata pagination and exact-version readback. Settings/Evidence expose actual storage locations and status, with enable/disable preserving saved source permissions. The Evidence page updates automatically while visible, preserves valid selected text through quiet checks, and withholds unavailable or revoked content. Current-credential checks screen source exports and settings. Native setup/restart checks use disposable fixtures; personal archive setup and cloud availability are not inferred from those tests. This #22 branch adds exact-query/literal-keyword candidates and domain/retrieval-date filters through one local operation/API, with full saved-byte verification and explicit limited results. Chat capture/reuse, search UI, full-page capture and automated eviction remain planned.
- **Private memory:** dedicated Memory view with interactive map, searchable list, sources, proposals and reflection journal; save/edit/forget, workspace/conversation scopes and bounded lexical recall. Settings contains configuration and links to Memory. Memory and derived replies stay local; relevant IDs are recorded. Human edits pin records; optional automatic curation preserves provenance and deletion barriers.
- **Background reflection:** durable chat jobs plus periodic working-note maintenance, formation/review with at most two calls, one idle worker, conservative budgets, source/revision rechecks, restart recovery and private journal.
- **Task configuration:** independent saved reflection/extraction/coding models, output/recall caps and worker timing/budgets. Reflection can be enabled or paused; see [saved configuration](docs/MEMORY_AND_REFLECTION.md#saved-configuration).

Chat history stays separate while usage and limits are shared. A first successful reply may trigger one bounded title call using the original model, a short first-message excerpt, no tools and the remaining request allowance. A first-message title is the fallback; manual names always win.

The current generation path supports one request at a time. API restart recovery assumes that the API process owns all generation. These are useful foundations for a task runtime, but neither establishes delegation.

The proposed [chat assessment and delegation workflow](docs/CHAT_ORCHESTRATION.md) makes assessment the first model step for each message, followed by a direct answer, a necessary clarification, the reviewed research branch, or a bounded available specialist assignment. Recorded runtime events keep the user informed. Observable runs and direct/clarification routes support the research path; general specialist execution follows durable attempt ownership. These behaviors are planned, not part of the current chat.

## Next delivery sequence

The [architectural overview](docs/ARCHITECTURE.md) records the agreed direction. [Research tracker #18](https://github.com/erikmalm/maestro/issues/18) owns dependencies, acceptance and cleanup. The initial visibility slice is delivered by #41 and #42; keep work moving toward one functioning saved-source research loop rather than waiting for every inspection feature.

1. Architecture #19/#40, baseline #35 and the first Evidence browser #21/#42 are delivered. Old integration #8 and its fixture PR #39 are closed as superseded; their history remains available without requiring a broad merge.
2. PR #44 delivered #36's first slice: Source archive settings/status over the existing service, explicit native startup paths and restart verification in a separate disposable synthetic store. Do not insert fixtures into personal storage; the normal archive may honestly remain empty until acquisition wiring exists. Preserve current data and ignore the earlier scaffold without import, move or deletion. Broader complete-content, capacity and accounting refinements remain #36/#20; they do not block #22, while #29 requires the bounded-content path.
3. This branch implements bounded local saved-source candidate lookup, kept separate from hosted acquisition ([#22](https://github.com/erikmalm/maestro/issues/22)). Prefer private exact-query mappings, then literal FTS or filtered inventory; verify full saved bytes, manifest/content pins, current eligibility and credentials. Preserve original dates and label mechanically stale candidates without accepting them as current evidence. The operation/API makes no model/hosted calls or lookup writes, and does not connect chat or add search UI. See the [contract and work bounds](docs/SEARCH_CONTEXT_STORAGE.md#local-candidate-lookup-22).
4. Complete the first saved-source research pipeline: owned runs ([#23](https://github.com/erikmalm/maestro/issues/23)), the shared model-stage/allowance contract ([#37](https://github.com/erikmalm/maestro/issues/37)), requirements and per-item evidence review ([#24](https://github.com/erikmalm/maestro/issues/24), [#25](https://github.com/erikmalm/maestro/issues/25)), writer and final review ([#26](https://github.com/erikmalm/maestro/issues/26), [#27](https://github.com/erikmalm/maestro/issues/27)). Use relevant saved passages, retain date/best-before reasoning and share cumulative limits across every stage. Initial routing is memory-free; inherited privacy restrictions remain. Demonstrate the loop with fixed sources and a small intended-local-model trial, without hosted searches.
5. Journal/deduplicate acquisition before one targeted hosted search and bounded corrections ([#28](https://github.com/erikmalm/maestro/issues/28), [#29](https://github.com/erikmalm/maestro/issues/29), [#30](https://github.com/erikmalm/maestro/issues/30)). Reserve linked search/quota records consistently, work outside database locks and preserve uncertain outcomes. Keep at most 20 hosted searches/day, honoring lower settings, and initially one/run. Validate automatic routing/caller migration ([#38](https://github.com/erikmalm/maestro/issues/38)) before final obsolete-code removal ([#31](https://github.com/erikmalm/maestro/issues/31)). These changes do not delete source files, history or personal memory.
6. Build a searchable Evidence index ([#43](https://github.com/erikmalm/maestro/issues/43)) and bounded provenance graph ([#34](https://github.com/erikmalm/maestro/issues/34)) in parallel or later when their record dependencies exist; neither gates the working research loop. Extend freshness policies ([#32](https://github.com/erikmalm/maestro/issues/32)) and optional entity annotations ([#33](https://github.com/erikmalm/maestro/issues/33)) independently. An initial domain -> source URL -> saved capture graph can use existing metadata; recorded request/run and acquisition links follow #23/#28. A graph database engine, curated knowledge, compression and automatic eviction remain later decisions.

Selected full webpage capture and origin validation, then bounded PDF extraction, extend the same evidence contract after general grounding is established. A durable text-only task and attempt ownership precede general specialist execution; saved profiles, scoped integrations and isolated coding follow their own source/action permissions. These later acquisition/execution paths remain separate from the initial reviewed research issues.

## Search context milestone

The [storage design](docs/SEARCH_CONTEXT_STORAGE.md) separates the merged capture/configuration/read APIs from historical draft integration and the planned research path. The following SEARCH milestones refer to that older integration plan; their preview validation does not mean the whole feature is on main. The merged foundation is #17/#41/#42 plus PR #44's first #36 slice; this branch adds the narrow #22 candidate operation.

| ID | Status | Delivery and acceptance | Depends on |
| --- | --- | --- | --- |
| SEARCH-01 | Core/configuration, read-only browser and first setup slice merged; broader integration historical | Opt-in external archive with eligible-public or approved-URL capture policy, immutable repeated captures and deduplicated objects, independent writer ownership and pinned readback. #41/#42 add bounded metadata browsing; #44 adds setup/status/restart proof. Remaining #36/#20 work and full rebuild/deletion integration stay separate. | Existing private storage and explicit archive setup |
| SEARCH-02 | Historical draft/preview; not wired to current chat | Capture before answer generation, exact-query reuse, saved-only/refresh modes and source-preserving follow-ups were exercised in the earlier integration. Reintroduce only the useful parts through #22 and the reviewed research increments, with their current permission, provenance and accounting contracts. | Saved lookup and reviewed research stages |
| SEARCH-03 | Narrow candidate operation/API in this #22 branch; old UI/integration historical | Exact-query/literal-keyword lookup and domain/retrieval-date filters inspect verified saved versions without chat integration. #43 owns the later searchable Evidence UI. The current metadata browser is not that search index. | #22; optional UI #43 |
| SEARCH-04 | Planned | Download selected public HTML/text sources with original bytes, extracted text and conditional origin validation. Fence DNS/redirects, private addresses, compressed/decoded sizes and deadlines. Fresh discovery remains distinct from validating old URLs. | SEARCH-03; reviewed fetch transport |
| SEARCH-05 | Planned | Add bounded PDF extraction and evaluate retrieval quality on dated sources. Introduce embeddings only if measured lexical misses justify their cost. | SEARCH-04 |

Definition of done for the first delivery: repeated eligible evidence is reused without hosted HTTP, its date/hash survives restart, fresh/saved-only choices are enforced by the server, private queries/memories/credentials never enter the synced archive, and source loss or failed capture produces truthful citations. Use synthetic tests and a bounded container storage fixture. Deploy the archive mount and enable the selected capture policy before claiming it is active in the running preview.

The 2026-10-04 implementation checks passed 443 backend tests and 117 browser tests, plus a network-disabled UID-1000 archive fixture under the supplied OneDrive root. A bounded live search saved three eligible excerpts, used one fully dated source and excluded two from synthesis; local lookup, source readback, source-preserving follow-up and archive-status answers made no extra hosted request. GPT-oss initially mislabelled a May 2 forecast as October 5, demonstrating that changing models alone did not solve stale evidence. Server date guards and provenance checks are essential; matching a later source's values does not independently verify a forecast or establish model-quality superiority. The [model assessment](docs/LOCAL_MODELS.md) retains that distinction.

The 2026-10-05 audit-fix checks passed 485 backend tests, 140 browser tests, the frontend production build and the isolated network-disabled OneDrive container fixture. Regressions cover settings-preserved reuse, credential rotation before inference, no-search boundaries, composed source provenance, offline memory eligibility, viewer deletion/revocation races, concurrent settings refreshes, checkout/archive separation, full-cap deletion/recovery, corrupt metadata/timestamps and resumable reconstruction of 5,001 captures. This validation used synthetic evidence and did not deploy the audit fixes or independently verify live facts.

## PR #8 review and decomposition

The subjects below describe the older draft and are reference boundaries, not a requirement to ship every old path. The revised read-only Evidence slice is already on main through #17/#41/#42 and uses the capture core directly. It did not import the old keyword engine, deletion/rebuild controls, globe modes or replay machinery. Preserve that narrow starting point for #36/#22 and retire superseded draft subjects as useful replacements land.

PR #8 is closed as superseded; its branch and unpublished local work are preserved. Reduce duplication and unnecessary structure when extracting useful behavior, preserving public metadata validation, credential checks, configuration races, archive/checkout separation, partial-capacity receipts and unambiguous citations. Earlier audit history is retained in [the previous checkpoint](https://github.com/erikmalm/maestro/blob/a08191b765ee8543050f9d42db2231d728e100ad/DEVELOPMENT_PLAN.md#pr-8-review-and-decomposition); the behavior contracts remain in the archive design and regression suites.

This historical extraction map records the earlier review boundaries, not an active sequence that must all merge. Foundations #9 through #17 are merged, as are the replacement Evidence API/UI in #41/#42. Historical #12 checks covered file publication and workspace locks/backups; disposable native OneDrive and WSL fixtures verified hard-link support and refusal to replace a destination. New feature PRs should remove the code they replace rather than copy entire draft paths.

| Unit | Scope and simplification boundary | Depends on | Acceptance |
| --- | --- | --- | --- |
| R8-01 (merged #9) | Background context/output allowances. | Main | Captured limits retain existing budgets, deadlines and uncertain-charge handling. |
| R8-02 (merged #10) | Local coordinator chat default. | Main | Explicit choices, installed-model checks, context bounds and actual model accounting remain correct. |
| R8-03a (merged #11) | Pure public-source policy and credential screening. | Main | Disabled defaults, legacy restrictions, exact URL identity and bounded decoding. |
| R8-03b (merged #12) | Four file access/publication functions in existing storage module. | Main | Bounded reads, path rejection, synced no-replace publication, collision preservation and staging cleanup. |
| R8-03c (merged #13) | Archive ownership and bounded inventory. Keep workspace ownership separate; use one existing claim map and direct filesystem helpers. | R8-03b | Nested/overlapping claims release after the last owner; stale claims block writes; redirected roots never receive claims; inventory stops at its item cap. |
| R8-03d (merged #14) | Pure manifest metadata validation and saved-source projection in the existing policy module. | R8-03a | Exact schema, Unicode, timestamps, public policy, source identity and object/observation paths are checked without I/O. Actual bytes, deletion and private-index/requested capture pins remain caller responsibilities in R8-03e. |
| R8-03e (merged #16) | Existing storage core: local configuration/status, capture batch, minimal private index insertion and pinned readback. Keep API and acquisition outside this unit. | R8-03c, R8-03d | Public-only exports respect byte/item caps, reuse objects and report saved/refused/failed sources truthfully. Actual bytes, deletion and index/requested pins remain checked; publication receipts distinguish confirmed writes from full inventory; recovery cannot restore concurrently revoked permissions or overwrite a successful retry with a stale error; optional root/index access errors stay managed. |
| R8-03f (merged #17) | Archive configuration/status API and lifespan setup in the existing application. | R8-03e | Disabled startup creates no archive; failed enables release new claims; lifecycle failures clear ownership metadata; successful restart/retry restores readiness and known operational refusals report unavailable; bounded credential screening preserves original scope identity. |
| R8-03g | Launcher path checks and archive mount. | R8-03f | Native/container ownership, private/checkout separation and persistent archive access work with disposable fixtures. |
| R8-04a | Exact-query reuse and harmless configuration saves. | R8-03e | TTL, current policy and pins gate reuse; unchanged/cap/window saves preserve mappings without another hosted acquisition. |
| R8-04b | Keyword lookup and version selection. | R8-03d, R8-03e | Read-only bounded literal search applies current filters and selects eligible versions without initializing or repairing storage. |
| R8-04c | Deletion and portable tombstones. | R8-04a, R8-04b | Revoked/deleted captures cannot be served, replayed or restored by rebuilding; shared immutable objects remain. |
| R8-04d | Resumable index rebuild. | R8-04c | Bounded scans survive interruption, count corrupt/missing records and publish a complete index atomically. |
| R8-05a | Source modes, freshness/date policy and request validation. | R8-04a, R8-04b | No-search, saved-only and refresh retain current permissions, filters and dated-source regressions. |
| R8-05b | Bounded source tool dispatch, capture and usage. | R8-05a, R8-03e | One tool round/two calls; credential screening, privacy, acquisition/token limits and consumed usage survive failures. |
| R8-06a | Prior-source selection and replay packets. | R8-05b | Exact referenced subsets, mixed saved/transient origins and two-report/4,096-byte bounds remain explicit. |
| R8-06b | Evidence guards at inference dispatch and commit. | R8-06a, R8-04c | Current credential, deletion, eligibility and content-pin checks fence stale packets without losing consumed usage. |
| R8-07a | Archive Settings and configuration refresh. | R8-03f, R8-04d | Pending saves/drafts survive navigation; concurrent refreshes retain permissions and routing changes. |
| R8-07b | Manual saved-source finder. | R8-04b, R8-04d | Filters, bounded results and rebuild progress remain usable on keyboard/mobile; delayed reads cannot restore revoked evidence. |
| R8-07c | Pinned saved-source viewer. | R8-04c, R8-03f | Safe text and original metadata render only while the pinned capture remains available. |
| R8-08a | Compact composer options and authoritative readiness. | R8-05b, R8-07a | Source mode persists with drafts and current readiness reflects backend state without a large setup box. |
| R8-08b | Dated citations and combined-source numbering. | R8-06b, R8-07c | Saved and prior sources share unambiguous citation numbers and links to their original pinned snapshots. |

Historical review checkpoints: draft #8 at `74a137d` included main `0be5589`; #17 at `30980c0` had 388 isolated backend tests. Earlier API/integration snapshots recorded 383/540 tests. Those 2026-10-16 counts and stack positions are history, not current main status. The subsequent #41/#42 baseline `555591e` passed 417 backend tests, 83 browser tests including 12 Evidence cases, and the frontend build; the current starting baseline is `c63692f` above.

The 2026-10-16 dirty integration audit initially found two backend fixture errors and one browser failure. Narrow corrections subsequently passed 583 backend and 145 browser tests on that older integration, with unchanged production code and no hosted calls. [PR #39](https://github.com/erikmalm/maestro/pull/39) contains those draft-specific changes and is closed as superseded for the current rollout. It must not block #36/#22 or force merging #8. [#35](https://github.com/erikmalm/maestro/issues/35) is completed with fresh current-main verification. Bring a historical fixture correction with a future extraction only if that extraction actually needs it.

Other legacy integration work, including larger downloads, complete returned-text storage, five-lookups continuation and source replay, remains separate from current main. Evaluate it against the narrower replacement contracts when needed; there is no requirement to import it before permanent storage or the first saved-source loop.

## Next milestone: general grounding and verification

The archive can prove which bytes were saved; the research path must also record which passages reached a model. Neither establishes support for every generated claim or the publisher's factual correctness. Historical draft #8's date gates covered recognized dated local weather; they are regression references, not a current-main capability or general verification. Coordinator routing and a second model's agreement alone do not close that gap. The following work is planned.

| ID | Delivery | Acceptance |
| --- | --- | --- |
| REL-01 | Bind search-assisted factual claims to evidence. | Retain claim-to-capture references and supporting spans from the fitted evidence. Unsupported or conflicting claims produce a bounded correction, qualified answer or inability-to-verify result. Exercise unrelated topics and distinguish quoted facts from calculations and suggestions. |
| REL-02 | Bind claims about performed actions to backend outcomes. | Server-authored receipts record attempted, completed, partial and failed actions, affected IDs and measured counts/bytes. Replies claiming searches, captures, deletion or other supported actions must agree with those receipts. Generated JSON and proposed actions cannot count as completed work. |
| REL-03 | Check dates, quantities, units and freshness consistently. | Validate answer values against the supporting evidence or explicit derivation; retain source/version/date boundaries and disclose conflicting values. Synthetic fixtures cover numbers changed during rewriting, incorrect units, stale publication dates, unsupported citations and explicit no-search requests across topics. |
| REL-04 | Measure the verification path and its limits. | Compare direct/coordinator routes on a public-safe cross-topic corpus with seeded unsupported and conflicting claims. Report detection misses, false rejection, extra calls, latency and token cost. Independent origin checks establish source observations separately from factual truth; model agreement is never the sole success criterion. |

These increments share existing generation and search allowances. Start with deterministic receipt and value checks plus explicit evidence attribution; add bounded model review only where measured failures justify it. General verification must land before broader source acquisition or specialist execution is described as reliable.

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

TASK-01 adds opt-in task suggestions to periodic formation/review. Independent assessment also considers declared capabilities and current practices without new chats, allowing measurable improvement experiments while distinguishing hypotheses from observed problems. The server records Maestro as initiator and lets the user change the suggested worker. Repeated open/completed titles and deleted AI suggestions are suppressed. Suggestions enter the existing list without another call, approval queue or execution process. Evaluate whether they identify useful follow-up work and avoid repeated or unsupported tasks; executing or sweeping the list remains out of scope for this increment.

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

For chat, follow [CHAT-01 through CHAT-04](docs/CHAT_ORCHESTRATION.md#implementation-increments): assess the message before selecting a route, preserve one parent allowance, report actual runtime progress, and bring the specialist result back to the original conversation. The first local specialist can reuse validated saved model choices; named profiles extend the available endpoints and permissions. Internal child attempts do not automatically become task-list to-dos.

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

The archive, settings/status and Evidence browser expose persistent dated source versions; this #22 branch adds bounded candidate lookup over them. Byte/manifest verification and mechanical stale labels do not establish request-specific freshness or evidence acceptance; general grounding/verification remains planned in #23/#37/#24–#27. The memory target remains a measured comparison of explicit and automatically curated records, including periodic working notes, correction and forgetting. One real durable task remains the execution target before expanding profiles/delegation. These paths reuse chat's generation/accounting limits. Podman checks preserve the single-owner runtime; separate archive mounting remains later work and must be verified before activation.
