# Maestro architecture

Maestro's current runtime is a local chat application with persistent user-created and reviewed AI-created tasks, private memory and opt-in background reflection. It has one active provider connection, direct/orchestrator local chat-model selection and bounded local recall. Task execution, separate coordinator assessment and specialist delegation remain planned.

The proposed [chat assessment and delegation design](CHAT_ORCHESTRATION.md) adds a coordinator assessment before each response and durable status events for direct answers, necessary clarifications, and specialist assignments. That workflow is future work; the current endpoint waits for the selected model's complete provider reply.

## Implemented boundaries

```mermaid
flowchart LR
    subgraph Computer[Your computer]
        UI[React interface]
        API[FastAPI local API]
        Chat[Provider chat and accounting]
        Memory[Typed memory and bounded recall]
        Reflection[Curation, scheduled reflection and journal]
        DB[(Private SQLite workspace)]
        Keys[Windows credentials or server memory]
        Ollama[Local Ollama model]
        Context[Saved-source validation and reuse]
        Index[(Private search index)]
        UI --> API
        API --> DB
        API --> Chat
        Chat --> DB
        Chat --> Memory
        API --> Memory
        API --> Reflection
        Reflection --> DB
        Reflection --> Chat
        Memory --> DB
        Chat --> Keys
        Chat --> Ollama
        Chat --> Context
        Context --> Index
    end
    Context --> Archive[(Opt-in public OneDrive archive)]
    Chat --> Remote[Configured Responses or Chat Completions API]
    Chat --> Search[Optional hosted Ollama web search]
```

The built frontend is served by FastAPI on one loopback origin. Workspace contains chat, Tasks contains attributed to-dos, and Memory contains the map/list, source evidence, proposals and reflection journal. Settings configures models, credentials, search, reflection and limits, with links to Memory and usage. The header and usage view report actual model usage and estimated spend.

`backend/app.py` owns session/CSRF protection, validation, CRUD and the application lifespan. `backend/provider.py` owns the single connection, shared generation slot and usage ledger. `backend/reflection.py` owns durable idle jobs, source validation, two-stage curation, scheduled working notes and a private journal. `backend/work_config.py` validates private task models and worker policy; `backend/memory.py` owns accepted records and read-only recall. `backend/web_search.py` owns hosted search readiness and limits. `backend/context_store.py` owns public capture eligibility, immutable objects/manifests, private query mappings/indexing and independent archive ownership; `backend/context_tools.py` defines freshness decisions. Reads avoid writes after initialization. SQLite writes use immediate transactions; generation runs outside locks. One worker runs inside the sole API process under exclusive workspace ownership.

Ollama uses its native loopback API, accepts installed local models and has no cloud fallback. Other adapters use Responses or Chat Completions. The UI always uses the chat role. Saved `chat_routing` selects the direct chat default or configured orchestrator model for local chat; a blank orchestrator selection uses the chat model and an explicit per-message choice wins. Existing connections default to direct routing. Coordinator instructions use the existing source-tool path, without another assessment call or specialist dispatch. Chat sends the selected conversation and optional local memory; the frontend progressively reveals the completed backend reply and renders safe Markdown. Reflection receives explicitly selected bounded evidence/memory, without implicit recall, tools, hosted search or conversation titles. Chat curation forms then reviews facts/preferences; scheduled reflection forms then reviews identity/lesson notes. Both use one shared slot and at most two calls.

Chats have stable IDs, separate message lists and editable titles. Workspace responses include chat summaries, `active_chat_id` and the selected chat's messages; generation captures an explicit chat ID. New chat preserves existing conversations. Deletion removes a chat's content while retaining the shared usage ledger, and is blocked while that chat has a reserved request. Previous live history migrates once into one conversation. Projects remain a later feature.

Manual task creation, completion/reopening, suggested-assignee changes and deletion only update local records. The server assigns immutable `initiated_by` (`user` or `maestro`); `suggested_assignee` uses the same values and remains editable. Older records expose `user` defaults without rewriting storage on reads.

Periodic reflection receives a server-authored capability overview and improvement goals alongside current practices and optional exchanges. `backend/capabilities.py` shares declared capabilities with the workspace API. It may propose useful practices or experiments without chat evidence; prompts distinguish untested ideas from observed outcomes. The journal records this assessment basis without inventing source messages or feeding journal prose back as evidence.

With `auto_create_tasks` enabled, periodic reflection may propose at most two follow-up or self-improvement tasks in its existing formation call, and the independent reviewer approves task indices in its existing second call. Approved tasks enter the same list in the final checked transaction; no extra model call or approval queue is added. Existing open/completed titles and bounded hashes of deleted AI titles suppress repeats. A suggested assignee is a label, not a dispatch instruction. There are no tools for task execution, repository changes, integration access or agent handoffs.

## Generation and usage

1. Read the active model configuration and the requested chat's context.
2. Check context/output token limits and configured spend allowances. Reserve conservative estimated usage in an immediate transaction.
3. Dispatch one generation, or the bounded Ollama search flow described below. The reservation permits one request at a time across chat and explicit-context calls.
4. Persist the reply and settle provider-reported token usage with the configured price snapshot. After a chat's first successful exchange, optionally generate its title once with the original model and remaining request allowance. The title call uses a bounded first message and no tools, and is skipped if model settings change. Failure keeps the first-message fallback and the successful reply; a manual rename wins over a delayed generated title. Unknown paid title usage still requires reconciliation.
5. Keep unresolved paid usage reserved until the user verifies and reconciles its amount. Known access/quota rejections and confirmed local failures release their reservations. Local chat, titles, and background calls with unknown completion retain a zero-cost slot. Only an explicit acknowledgement that the original Ollama server was restarted, followed by an empty model check on that original endpoint, releases the interrupted request. Loading and queued work can be absent from the model list, so it cannot acknowledge termination automatically. Recovery rechecks request ownership and endpoint under the ledger lock and never releases an active foreground call or reflection phase.

Local model API charges are zero; hardware and electricity costs are outside this accounting. Remote charges are estimates from saved prices, not invoices. Earlier entries keep their recorded prices. An API restart marks interrupted paid requests uncertain. This startup recovery assumes there is no independent live worker and must change before cross-process dispatch is enabled.

## Optional search

When automatic search is explicitly enabled and a separate search key has been tested, a tool-capable local Ollama model may request one `web_search`. With the archive enabled, `search_context` replaces that tool and first checks eligible saved evidence according to the request's freshness mode. A cache miss requires the existing hosted readiness, privacy and quota checks. Recognized dated local-weather requests require source evidence even in default mode; unavailable fresh setup fails before inference and unsupported direct replies cannot commit. Raw and fitted weather excerpts must contain the requested full dates; if none qualify, the server records an inability-to-verify reply with source metadata and actual first-call usage. Date presence is not factual validation. Hosted requests use only `https://ollama.com/api/web_search`; eligible bounded, untrusted excerpts go to one final local generation with tools removed. Query and source provenance persist beside the answer.

At most one search and two model calls are allowed for an answer; a one-time title call may use the remaining request allowance. The answer calls share the output allowance and cumulative token check; known usage remains accounted if search or the final response fails. Search tests and interrupted/failed attempts count toward the daily cap. One in-flight search, five-second spacing, a 30-second timeout, response-size limits and provider cooldowns apply. There are no automatic retries or result-page fetches. Hosted search fees are unknown and excluded from local inference's zero API cost.

This is a bounded tool call inside chat. It does not delegate a task or create an autonomous agent.

The opt-in [search context archive](SEARCH_CONTEXT_STORAGE.md) saves eligible public excerpts in immutable files with portable metadata and keeps a rebuildable full-text index in private local storage. New configurations default to disabled `all_public` capture with adjustable 10 GiB/200,000 file-and-directory limits; existing approved-URL policy and limits persist. Broad capture accepts eligible public HTTP/HTTPS snippets without origin fetching, with credential/private/authenticated URL exclusions. Restricted HTTPS scopes remain available. Repeated acquisitions create new capture records and deduplicate objects; status exposes measured usage and last-capture sizes. Fixed public connection tests always go online and save eligible results only with enabled broad capture.

The local `search_context` tool tries exact-query reuse then keyword retrieval independently of hosted readiness, with server-enforced saved-only/refresh modes and original dates. Optional exact-domain and inclusive UTC retrieval-date filters restrict lookup to saved evidence and prohibit hosted fallback. Bounded local follow-ups reconstruct source status and fitted evidence from server-held provenance; old source bodies are not used to plan fresh/current requests or sent to hosted search. Content/manifest hashes bind historical citations; source validation guards final dispatch and answer persistence while consumed usage remains recorded. Generated JSON cannot write archive files. The Settings archive controls, local source finder and authenticated source viewer use the existing session/CSRF boundary. Publisher dates, full page/PDF downloads and automatic pruning remain later increments.

## Private state and credentials

Tasks, live messages, typed memories, reflection journals, limits, settings and ledgers live in SQLite outside the checkout. The default directory is `%LOCALAPPDATA%\Maestro\preview`; `MAESTRO_DATA_DIR` must also resolve outside the repository. Provider/search credentials live in Windows Credential Manager, server memory or mounted secret files. The OpenAI endpoint can use a server-environment key fallback. Read APIs expose credential presence, never values.

The separate `MAESTRO_CONTEXT_ARCHIVE_DIR` exports only eligible public source excerpts and portable metadata under the enabled capture policy. Queries and capture references stay in the workspace database; the disposable index lives alongside it at `context/index.sqlite3`. Neither private workspace data nor a live SQLite file belongs in the synced archive. Container setup uses the explicit `-ContextArchivePath` mount option.

The API validates Host and Origin, requires a local session and protects mutations with CSRF checks. Remote provider requests send the selected conversation history to that provider; Ollama sends it to the local server. OpenAI requests set `store: false`. The repository and synthetic test fixtures contain no private workspace records or personal credentials.

Earlier prototype-only records remain archived in the private database, and old reflection tables remain inactive. Simulated messages are excluded from active chat and provider context. Private memory uses a separate table with typed origin/provenance metadata, save/edit/pin/forget and bounded local recall. Optional automatic curation protects human edits and records changes in a private journal. Memory-derived conversations cannot later dispatch to remote providers or active hosted search. The [memory/reflection design](MEMORY_AND_REFLECTION.md) documents formation/review, persistent schedules, correction and deletion boundaries.

The memory map is a frontend view of those records: type groups, bounded selectable nodes, search/scope filters and existing source references. It shares editing and forgetting with the list view and requires neither another database nor model inference. Recorded source dependencies describe how a memory was derived; they do not establish semantic similarity.

## API surface

| Routes | Purpose |
| --- | --- |
| `GET /health`, `GET /api/session` | Local health and session/CSRF setup. |
| `GET /api/workspace?chat_id={id}` | Chat summaries, selected messages, tasks, limits, shared usage and capability status; selection is optional. |
| `POST /api/chats`, `PATCH /api/chats/{id}`, `DELETE /api/chats/{id}` | Create, rename or delete a conversation, retaining accounting. |
| `POST /api/tasks`, `PATCH /api/tasks/{id}`, `DELETE /api/tasks/{id}` | Create a user-initiated task; change completion/suggested assignee or delete it. Initiator is server-owned. |
| `POST /api/chat` | Generate a reply for the submitted `chat_id` and persist it in that conversation. |
| `PATCH /api/chats/{chat_id}/messages/{message_id}/feedback` | Save/edit/clear an optional rating and comment on an existing answer, without inference. |
| `GET /api/memory`, `POST /api/memory`, `PATCH /api/memory/{id}`, `DELETE /api/memory/{id}` | Inspect, save, edit/pin or forget private memory; snapshots also include records. |
| `GET /api/work-config`, `PUT /api/work-config` | Saved task models, recall allowances and reflection enable/pause policy. |
| `GET /api/reflection` | Read-only worker status, daily usage, current memories, journal and legacy proposals. |
| `POST /api/reflection/candidates/{id}/accept`, `DELETE /api/reflection/candidates/{id}` | Accept a source-backed proposal into scoped private memory or reject it. |
| `GET /api/provider`, `PUT /api/provider`, `POST /api/provider/test`, `DELETE /api/provider/key` | Active connection settings including local chat routing, model-list access and credential removal. |
| `POST /api/provider/charges/{id}/reconcile` | Settle a provider-verified uncertain amount. |
| `GET /api/web-search`, `PUT /api/web-search`, `POST /api/web-search/test`, `DELETE /api/web-search/key` | Search readiness, settings and separate credential lifecycle. |
| `GET /api/context`, `PUT /api/context`, `POST /api/context/rebuild` | Archive readiness, capture policy/scopes, measured usage and last-capture sizes, reuse/storage limits and bounded index reconstruction. |
| `POST /api/context/search` | Read-only keyword lookup with exact-domain, UTC retrieval-date and historical-source controls. Private terms stay out of request URLs; no model/search calls or query-map writes. |
| `GET /api/context/sources/{id}`, `DELETE /api/context/sources/{id}` | Inspect a saved excerpt with optional historical content/manifest hashes, or publish its deletion marker. |
| `PUT /api/limits` | Save the enforced chat spend/token allowances. |

## Next build sequence

The [Podman deployment checklist](../DEVELOPMENT_PLAN.md#podman-deployment) runs alongside this functional sequence. The [container runbook](CONTAINERS.md) covers one application container, persistent private storage and host Ollama. Native and container runtimes acquire an exclusive workspace lock before recovery; keep one API owner per workspace until worker ownership and leases are implemented. Local chat and planning roles can choose distinct models through the same reservation ledger; named endpoint profiles and task routing remain separate work.

The shared generation foundation is implemented: `Provider.generate_context` accepts bounded selected context and an installed local model through the same ledger as chat, without tools, titles or chat persistence.

The next sequence is general grounding and verification, selected full-page/PDF capture, observable chat assessment and direct/clarification routes, one durable text-only task, saved local/remote profiles, then bounded specialist delegation and review. The [development plan](../DEVELOPMENT_PLAN.md#next-delivery-sequence) owns delivery and acceptance; [CHAT-01 through CHAT-04](CHAT_ORCHESTRATION.md#implementation-increments) define assessment, status, cancellation and recovery. Grounding must bind source claims to evidence and action claims to backend outcomes before broader acquisition or delegation; model agreement alone is not verification.

Task execution requires durable claims, leases and ownership-aware recovery: API restart must not release a live worker's reservation, and stale attempts cannot commit after a newer claim. Descendants share the parent's bounded context, tools and call/token/time/spend allowance, with cancellation and no-progress stops. A first local specialist can reuse saved model choices once attempt ownership works; named profiles broaden routing afterward. Explicit memory and [durable background reflection](MEMORY_AND_REFLECTION.md) are already usable independently; automatic promotion, scheduled notes and reviewed task suggestions are optional, with broader quality evaluation still needed. Scoped integrations and isolated coding require the execution path. [Local model research](LOCAL_MODELS.md) records preliminary measurements.
