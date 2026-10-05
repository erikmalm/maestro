# Maestro development plan

Plan date: 2026-10-04. The current product supports real chat, persistent history, attributed to-dos, provider setup and usage accounting. Private memory, shared local generation and opt-in background reflection support reviewed AI task suggestions. Local Ollama generation and bounded hosted search have been verified in the running preview with a mounted search key; remote API/accounting retains synthetic integration coverage. Other installations must test and enable their own hosted search connection. Reusable search context provides the current foundation; general grounding and verification is the next milestone.

There is one active connection with saved model preferences and per-message local choices. Local `chat_routing` selects the chat or configured orchestrator model for ordinary messages unless the user overrides it. The UI still uses the chat role and the same one-tool/two-call answer allowance; a separate typed assessment, live run events and specialist delegation remain planned. Accepted private memory is recalled in relevant local chats. Background reflection optionally curates normalized memories through two-model review and maintains scheduled identity/lesson notes with a private journal. Maestro cannot execute a to-do, delegate work or automatically route specialist tasks yet. The [architecture](docs/ARCHITECTURE.md) and [memory/reflection design](docs/MEMORY_AND_REFLECTION.md) distinguish working behavior from upcoming increments.

## Product goal

Draft [PR #8](https://github.com/erikmalm/maestro/pull/8) is being extracted into independently reviewed subjects. Background limits (#9), local chat defaults (#10), [archive policy (#11) and file publication (#12)](docs/ARCHIVE_POLICY.md) are merged. Ownership/inventory, manifest verification, capture receipts, API setup and mounting follow separately before retrieval, chat and UI integration. Main has no connected archive capture yet; this draft retains the pending implementation.

Build a personal AI coordinator that runs locally and turns conversation and to-dos into useful, bounded work. It should select appropriate local or remote models, delegate selected assignments, review outcomes and keep a useful result or a clear stop reason. Progress, token usage and estimated spend should remain visible.

Keep credentials and personal workspace data outside the public checkout. Private memory should carry provenance and project scope, support user corrections and deletion, and eventually help later tasks without indiscriminate sharing. Integrations should operate only on configured sources and permitted actions. MarketPulse document access and GitHub coding tasks are intended capabilities, with no assumption that either integration already works.

The first supported setup is one user on one Windows computer. Deliver each capability as a small working increment; add navigation and controls when their behavior is implemented.

## Current foundation

- **Workspace:** separate persistent chats with live Ollama or compatible API replies, editable titles and optional one-search-per-message Ollama web search. New chat preserves earlier conversations; deleting one retains shared usage accounting.
- **Tasks:** manual create, complete/reopen, suggested-assignee changes and deletion without model calls. Every task records its initiator; optional periodic reflection adds up to two independently reviewed AI-created tasks per pass to the same list, without execution.
- **Settings and usage:** one connection with direct/orchestrator local chat routing, per-message model overrides, OS/session/mounted credentials, search setup, output/context controls, spend/token limits, actual model usage and uncertain-charge reconciliation.
- **Private storage:** SQLite outside Git; real conversations and accounting survive restart. Previous live history migrates into one conversation. Earlier prototype-only records remain archived privately, with simulated messages excluded from active chat.
- **Search context:** opt-in immutable public excerpt archive, private exact-query mappings and keyword retrieval, exact-domain/UTC retrieval-date filters, saved-only/refresh choices, dated citations and a saved-source finder/viewer. New configurations default to eligible-public capture, disabled until enabled, with adjustable 10 GiB/200,000 file-and-directory caps; legacy approved-source settings remain unchanged. Repeated retrievals keep separate captures and deduplicate objects. Measured usage and last-capture sizes are visible. The compact globe menu separates online readiness from saving. Manual approval remains available for HTTPS path scopes or exact complete query URLs. Bounded local source follow-ups use server-authored provenance and hash guards. The preview now enables all-public capture and GPT-oss coordinator routing; live source readback, local lookup and follow-ups were verified. Hosted search is tested and enabled, with evidence enforcement for recognized dated local weather and mechanical date checks. Full document downloads, automatic pruning and publisher-date extraction are planned.
- **Private memory:** dedicated Memory view with interactive map, searchable list, sources, proposals and reflection journal; save/edit/forget, workspace/conversation scopes and bounded lexical recall. Settings contains configuration and links to Memory. Memory and derived replies stay local; relevant IDs are recorded. Human edits pin records; optional automatic curation preserves provenance and deletion barriers.
- **Background reflection:** durable chat jobs plus periodic working-note maintenance, formation/review with at most two calls, one idle worker, conservative budgets, source/revision rechecks, restart recovery and private journal.
- **Task configuration:** independent saved reflection/extraction/coding models, output/recall caps and worker timing/budgets. Reflection can be enabled or paused; see [saved configuration](docs/MEMORY_AND_REFLECTION.md#saved-configuration).

Chat history stays separate while usage and limits are shared. A first successful reply may trigger one bounded title call using the original model, a short first-message excerpt, no tools and the remaining request allowance. A first-message title is the fallback; manual names always win.

The current generation path supports one request at a time. API restart recovery assumes that the API process owns all generation. These are useful foundations for a task runtime, but neither establishes delegation.

The proposed [chat assessment and delegation workflow](docs/CHAT_ORCHESTRATION.md) makes assessment the first model step for each message, followed by a direct answer, a necessary clarification, or a bounded specialist assignment. Recorded runtime events keep the user informed. Deliver observable chat runs and the direct/clarification routes first; enable a local specialist after durable attempt ownership works. These behaviors are planned, not part of the current chat.

## Next delivery sequence

Complete the search-context foundation and prioritize general grounding and verification next. Reliability applies to every search-assisted topic and to claims about actions Maestro performed. Keep private-memory evaluation and generation/recovery checks as regression requirements.

1. Deliver a public-source archive and exact-query reuse with dated citations, explicit freshness choices and a working saved-source view.
2. Deliver general grounding and verification: bind factual claims to supporting evidence and action claims to server-recorded outcomes. Evaluate unsupported, conflicting and stale evidence across topics before expanding acquisition or delegation.
3. Add selected full webpage capture and origin validation, then bounded PDF extraction, using the same verification contract.
4. Implement observable chat assessment and direct/clarification routes. Add a durable text-only task and attempt ownership before specialist execution.
5. Extend saved profiles and bounded delegation after the execution path is verified. Scoped integrations and isolated coding follow their own source/action permissions.

## Search context milestone

The [search context storage design](docs/SEARCH_CONTEXT_STORAGE.md) defines immutable public evidence under the selected OneDrive folder, a private local index, separate publisher/retrieval/verification dates, and the archive's privacy and deployment boundaries. The first delivery covers SEARCH-01 and SEARCH-02 together. Preserve the excerpts received from hosted search before final answer generation; consult matching saved evidence without consuming another hosted search allowance.

| ID | Status | Delivery and acceptance | Depends on |
| --- | --- | --- | --- |
| SEARCH-01 | Implemented; preview all-public capture verified | Opt-in external archive with eligible-public or approved-URL capture policy, immutable repeated captures and deduplicated objects, independent writer ownership, content/metadata hashes, measured usage/last-capture bytes, adjustable limits and a rebuildable private index. New defaults are disabled/10 GiB/200,000 files and directories; legacy policy/limits persist. Synthetic tests and an isolated OneDrive container fixture cover reuse, restart/recreation, writer rejection, rebuild and deletion. Live capture and hash-bound file readback passed; offline hydration remains an acceptance check. | Existing private storage and container launcher |
| SEARCH-02 | Implemented; preview capture/follow-ups verified | Save excerpts even if answer generation fails. Reuse exact queries with original retrieval dates; support saved-only and forced-refresh choices. Bounded local follow-ups receive verified prior evidence and saving status; live follow-up retained its pinned source values without hosted dispatch. Cache hits need no hosted key/quota, while model calls keep existing limits. Connection tests always go online and capture eligible results only under enabled eligible-public policy; citations identify saved, stale or transient evidence. Deleted/changed evidence cannot commit a late answer. | SEARCH-01; existing bounded chat tool path |
| SEARCH-03 | Implemented; preview local lookup verified | Read-only literal keyword search over saved titles/text, exact-domain and inclusive UTC retrieval-date filters, conservative URL-version selection and bounded ranking/verification. Stable chat requests try exact reuse then keywords; saved filters never go online. A local source finder opens hash-bound snapshots without model/search calls. Publisher dates remain unknown. Backend/browser regressions and the isolated OneDrive container fixture pass. | SEARCH-02 |
| SEARCH-04 | Planned | Download selected public HTML/text sources with original bytes, extracted text and conditional origin validation. Fence DNS/redirects, private addresses, compressed/decoded sizes and deadlines. Fresh discovery remains distinct from validating old URLs. | SEARCH-03; reviewed fetch transport |
| SEARCH-05 | Planned | Add bounded PDF extraction and evaluate retrieval quality on dated sources. Introduce embeddings only if measured lexical misses justify their cost. | SEARCH-04 |

Definition of done for the first delivery: repeated eligible evidence is reused without hosted HTTP, its date/hash survives restart, fresh/saved-only choices are enforced by the server, private queries/memories/credentials never enter the synced archive, and source loss or failed capture produces truthful citations. Use synthetic tests and a bounded container storage fixture. Deploy the archive mount and enable the selected capture policy before claiming it is active in the running preview.

The 2026-10-04 implementation checks passed 443 backend tests and 117 browser tests, plus a network-disabled UID-1000 archive fixture under the supplied OneDrive root. A bounded live search saved three eligible excerpts, used one fully dated source and excluded two from synthesis; local lookup, source readback, source-preserving follow-up and archive-status answers made no extra hosted request. GPT-oss initially mislabelled a May 2 forecast as October 5, demonstrating that changing models alone did not solve stale evidence. Server date guards and provenance checks are essential; matching a later source's values does not independently verify a forecast or establish model-quality superiority. The [model assessment](docs/LOCAL_MODELS.md) retains that distinction.

The 2026-10-05 audit-fix checks passed 485 backend tests, 140 browser tests, the frontend production build and the isolated network-disabled OneDrive container fixture. Regressions cover settings-preserved reuse, credential rotation before inference, no-search boundaries, composed source provenance, offline memory eligibility, viewer deletion/revocation races, concurrent settings refreshes, checkout/archive separation, full-cap deletion/recovery, corrupt metadata/timestamps and resumable reconstruction of 5,001 captures. This validation used synthetic evidence and did not deploy the audit fixes or independently verify live facts.

## PR #8 review and decomposition

Review began against `879cd69` on 2026-10-04. Mixed delivery scope and duplicated policy/tests remain reviewability concerns. Prior fixes through `ee5163d` are recorded below. Review after main merge `f78ce57` exposed citation-number collisions and lost reuse after partial capacity refusals. The fixes passed 482 backend tests, 142 browser tests and the production build; independent reviews found no remaining actionable issues in these fixes. They add 26 production lines; removing a redundant background-limit fixture removes 43 test lines covered by main's consolidated tests. General grounding remains planned, and PR #8 remains a draft while its remaining units are extracted and reviewed.

| Fix task | Resolution | Regression |
| --- | --- | --- |
| R8-F1: Preserve exact-query reuse | One configuration path retains exact mappings for unchanged/numeric saves; eligibility transitions still clear them. | No-op/cap/window saves reuse the same capture without hosted acquisition or file changes. Current TTL, revocation, deletion and rebuild barriers remain effective. |
| R8-F2: Reject malformed public metadata | Validate title UTF-8 encoding at the existing manifest boundary before hashing/indexing. | Escaped high/low lone surrogates are counted as corrupt; bounded rebuilding completes with healthy Unicode evidence, original pins and immutable bytes. API rebuilding returns HTTP 200. |
| R8-F3: Rescreen evidence before inference | One current-credential scan checks the retained server packet and newly fitted sources at their dispatch boundaries. | Literal/encoded key rotation during metadata, planning or final validation fences saved/transient evidence. Planning failures retain 37 input/11 output tokens, one model call and captures, with no reply; safe controls complete. Ordinary user/history text is excluded. |
| R8-F4: Retain concurrent settings updates | A superseded workspace refresh retries only when still latest, preserving the archive revision fence. | Overlapping routing/search saves and archive revocation update composer defaults/readiness without restoring permissions or losing drafts; older refreshes cannot start competing retries. |
| R8-F5: Separate host archive and checkout | Launcher validation rejects checkout ancestors as well as equal/descendant paths, matching native storage separation. | Mocked launcher checks reject every overlap and accept an unrelated archive without Podman or live mutations. |
| R8-F6: Keep partial capacity captures reusable | Capacity refusals continue through fitting sources and finalize the verified saved subset under the existing writer lock; unexpected write/index failures retain their no-new-mapping path. | Item-cap refusal followed by a fitting deduplicated source retains exact reuse, truthful counts/warning and immutable pins; wholly refused captures create no mapping. |
| R8-F7: Keep citation numbers unambiguous | Current lookup and available earlier evidence share one numbered citation list; original message/source/hash references remain unchanged. | Combined and chained replies retain both origins, distinct citation numbers and matching saved-source links without inventing another search action. |

Earlier fixes and cleanup use separate small commits. Backend-owned readiness, shared thinking setup, finder reset/inherited styles and configuration simplification remove 41 net production lines; correctness guards add 26, leaving 15 fewer production lines than `a33eeb1`. Small test locators/source factories remove 70 net test lines; new regressions leave tests 230 lines larger overall. All existing cases and privacy, race, corruption, accounting, rebuild and mobile assertions remain. A date-rollover fixture now reads daily usage under the request's mocked clock. No general mock framework was added; archive queues, deletion state, hash/eligibility checks and dispatch/commit guards remain.

Keep the remaining changes in the following review units, with relevant behavior tests and documentation beside each subject. The merged foundations are background limits (#9), local chat defaults (#10), archive policy (#11) and bounded file access/publication (#12). PR #12 contains 197 additions across four files, passes 329 backend tests and independent filesystem review, and preserves existing workspace locks/backups. Disposable native OneDrive and running WSL fixtures verified hard-link support and refusal to replace a destination. Each extraction must remove its duplicate implementation from this draft; moving code alone does not reduce total LOC.

| Unit | Scope and simplification boundary | Depends on | Acceptance |
| --- | --- | --- | --- |
| R8-01 (merged #9) | Background context/output allowances. | Main | Captured limits retain existing budgets, deadlines and uncertain-charge handling. |
| R8-02 (merged #10) | Local coordinator chat default. | Main | Explicit choices, installed-model checks, context bounds and actual model accounting remain correct. |
| R8-03a (merged #11) | Pure public-source policy and credential screening. | Main | Disabled defaults, legacy restrictions, exact URL identity and bounded decoding. |
| R8-03b (merged #12) | Four file access/publication functions in existing storage module. | Main | Bounded reads, path rejection, synced no-replace publication, collision preservation and staging cleanup. |
| R8-03c | Archive ownership and bounded inventory. Keep workspace ownership separate; use one existing claim map and direct filesystem helpers. | R8-03b | Nested/overlapping claims release after the last owner; stale claims block writes; redirected roots never receive claims; inventory stops at its item cap. |
| R8-03d | Manifest contract, object hashes and capture pins. Reuse policy/file helpers without a second validation layer. | R8-03a, R8-03b | Corrupt schema, Unicode, timestamps, links and hash mismatches fail; immutable bytes and original metadata pins remain unchanged. |
| R8-03e | Capture batch, minimal private index insertion and receipts. Keep acquisition outside this unit. | R8-03c, R8-03d | Public-only exports respect byte/item caps, reuse objects and report saved/refused/failed sources truthfully; partial writes cannot create complete query mappings. |
| R8-03f | Archive configuration/status API and lifespan setup. Reuse existing application setup. | R8-03e | Disabled startup creates no archive; ownership failures and configuration remain truthful; one runtime owns writes. |
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

Integration after #12 passes all 511 backend tests and two independent adoption reviews. Removing four duplicate file methods and two lock-forwarding pairs leaves `ContextStore` 60 lines shorter; counting the extracted helpers and ownership fixes, total backend production LOC falls by two against `be67515`. The owner regressions fail against the previous implementation. Frontend behavior is unchanged since the previous build and 142-scenario coverage.

PR #8 includes main at `74b10ff`. The next extraction is R8-03c, archive ownership/inventory. Subjects define review boundaries, not mandatory new files or classes; keep direct functions and explicit inputs rather than new services, caches or event layers. Extract selected hunks instead of whole mixed commits. Every extracted head must pass its relevant regressions; the assembled stack must pass integration checks before closing PR #8. General grounding remains the next product milestone and is not implemented by this decomposition.

## Next milestone: general grounding and verification

The archive proves which bytes were saved and supplied to a model. It does not establish that those bytes support every generated claim or that their publisher is correct. The current date gates cover recognized dated local weather; they are a regression example, not general verification. Coordinator routing and a second model's agreement alone do not close that gap. The following work is planned.

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

The search-context foundation provides dated evidence and enforced freshness choices. General grounding and verification is the next delivery target. The memory target remains a measured comparison of explicit and automatically curated records, including periodic working notes, correction and forgetting. One real durable task remains the execution target before expanding profiles/delegation. These paths reuse chat's generation/accounting limits. Podman checks preserve the single-owner runtime and verify the separate archive mount before activation.
