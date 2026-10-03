# Private memory and background reflection

Updated: 2026-10-03. Maestro supports manual memory, optional automatic curation and scheduled reflection. It uses the existing API process, private SQLite database and host Ollama, without new services or dependencies. Task execution and delegation remain planned.

## Using background reflection

1. Select Local Ollama in Settings. Set **Local worker settings → Context tokens** to at least **8,192** for automatic curation.
2. In **Task models & reflection**, enable background reflection and automatic memory curation. Enable independent reflection and choose **360 minutes** for a six-hour interval. Save.
3. Continue chatting. After the configured quiet period, Maestro can normalize durable facts/preferences and save reviewed changes automatically.
4. Read **Task models & reflection → Private reflection journal** for summaries, changes, source references and model names. Edit or forget any memory; editing pins it against automatic changes.

Independent reflection has an initial pass once the worker is idle, then follows the saved interval. It considers eligible conversation evidence, current memories and earlier working notes. It develops concise identity/lesson notes about how Maestro should work. These are revisable practices, not consciousness, user facts or new permissions.

Automatic curation and periodic reflection default off for existing installations. With automatic curation off, new eligible chat evidence produces exact-quote proposals requiring acceptance before recall. Existing proposals remain accessible in automatic mode. Only accepted, unedited proposals with matching fingerprints can become managed memories; manual entries and corrections stay pinned.

Settings shows queued/running work, usage, the next reflection and stop reasons. Closing the browser or locking Windows does not stop the server. Windows must stay awake and Maestro/Ollama must remain running. Reboot startup is not installed; see [Windows background operation](WINDOWS_BACKGROUND.md).

## Saved configuration

Private work_config settings are validated through GET/PUT /api/work-config. Chat/orchestrator choices remain in provider settings. Background work requires Local Ollama and has no remote fallback.

| Setting | Default | Effect |
| --- | --- | --- |
| Background reflection | Off | Allows new work; pausing invalidates late results. |
| Automatic memory curation | Off | Formation, independent review and automatic promotion. |
| Independent reflection | Off | Scheduled identity/lesson maintenance when automatic curation is on. |
| Reflection interval | 360 minutes | Initial eligible pass, then six-hour intervals; range 30 minutes to seven days. |
| Reflection / memory / coding model | Recommended installed tag | gpt-oss:20b / qwen2.5:7b / devstral-small-2:24b; otherwise local chat fallback. |
| Output tokens | 512 | Per-call ceiling, further reduced by request/provider limits. |
| Debounce / chat idle time | 60 / 30 seconds | Coalesces evidence and waits for inactivity. |
| Daily jobs / total tokens | 10 / 10,000 | One workflow counts as one job; both calls consume tokens. Zero pauses dispatch. |
| Job timeout | 180 seconds | One deadline covers both model checks and generations. |
| Memories per reply / characters | 5 / 1,000 | Recall ceilings; either zero disables recall. |

Explicit model choices must be installed and support completion. Missing choices fail visibly; Maestro never downloads or silently substitutes them. Blank choices use the recommended installed tag or chat fallback. Coding configuration supports text generation, not autonomous execution. See [Local model assessment](LOCAL_MODELS.md).

## Formation, review and persistence

Chat curation uses the memory model to normalize and sanitize facts/preferences, then the reflection model reviews numbered changes. Periodic reflection reverses these roles: the reflection model develops working notes and the memory model reviews them. It can also remove an obsolete managed fact, but cannot invent or rewrite user facts without new user evidence. Either can abstain. Review approves indices rather than adding a rewrite loop; each workflow permits at most two calls.

Drafts stay in RAM. SQLite stores source references/hashes, captured settings, attempt/stage, request IDs and usage. Promotion requires strict JSON, exact user evidence for facts/preferences, valid scope, matching source/memory revisions, credential checks and independent approval. Explicit memories are protected. Model review can still err; schema validation establishes shape, not truth.

The private journal stores brief summaries, applied changes, source references, models and timestamps. It retains at most 100 entries and exposes the newest 20 in Settings. Hidden reasoning transcripts are not saved. Actual entries stay in private SQLite outside the public repository.

Records distinguish fact, preference, identity and lesson, with explicit, curated or reflective origin. Metadata contains evidence and provenance. Human corrections become explicit pinned authority; automation changes managed records only. Earlier model ideas cannot justify invented user facts. Working notes enter local prompts as quoted suggestions.

## Runtime and resource bounds

One worker runs in FastAPI's lifespan under exclusive workspace ownership. Processing runs off the API event loop; the idle loop makes no inference calls. Jobs share chat's single reservation slot and usage ledger. Chat can run between formation and review; new activity or changed sources can invalidate review. In-flight generation can briefly delay chat; instant preemption is not implemented.

Both stages reserve conservative usage before dispatch. First-call usage remains accounted if review fails or is blocked. Every call requests keep_alive=0, without tools, hosted search, titles or implicit recall. Unknown completion retains the slot until Ollama reports no loaded models. Another application's resident model can prolong this wait; Maestro never forcibly unloads other applications' models.

Source context is bounded and reduced to fit the configured allowance. Long messages contribute prefixes, so later facts can be missed. New exchanges record eligibility: only normal local chat without active hosted search supplies periodic user evidence. Historical remote/search turns are not guessed eligible. Supported saved memories also provide context. Periodic reflection excludes conversation-scoped memories and their chats; bounded assistant response excerpts are labelled unverified and never serve as user-fact evidence.

Queued work and schedule timestamps survive restart. Interrupted dispatched or between-stage work is abandoned without replay; drafts are not resumed. Attempt tokens, source hashes, memory revisions and invalidation epochs reject stale commits. Settings changes, forgetting and source deletion invalidate late results. Shutdown waits for in-flight processing to finish or reach its deadline.

## Recall, correction and forgetting

The store permits 200 records of at most 1,000 characters. Recall combines scoped Unicode keyword overlap with a small allowance for working-identity notes. It stays capped at five records and 1,000 total characters and omits entries that cannot fit generation limits. Measure retrieval misses before adding embeddings or another database.

Only normal local Ollama chat without hosted search receives memory. Replies record supplied memory IDs, indicating context provided rather than proof of use. Conversations with memory-derived history cannot later dispatch remotely or use hosted search; start a new conversation for those modes.

Editing pins a correction and invalidates pending work. Forgetting removes the record and dependent derived material. Human corrections and forgetting clear journal entries conservatively so old copies cannot bypass forgetting. Compact source barriers prevent old chats from recreating forgotten/superseded facts under different wording; fresh messages can supply new evidence. Chat deletion removes scoped/source-linked memories and dependent jobs. Unlinked explicit workspace records survive.

Forgetting affects future recall, not dispatched prompts, earlier replies, snapshots or backups. SQLite secure deletion is enabled. Known active credentials and common credential patterns are rejected, but AI sanitation and pattern checks cannot detect every secret. Keep passwords and keys out of chat and memory.

Management routes remain GET/POST /api/memory and PATCH/DELETE /api/memory/{id}. GET /api/reflection provides read-only status, current memories and journal for guarded UI polling. Legacy proposal accept/reject routes remain. Session, Origin and CSRF protections apply; errors do not echo private input.

## Recorded next tasks

[LangMem's concepts](https://github.com/langchain-ai/langmem/blob/main/docs/docs/concepts/conceptual_guide.md) support separating facts, experiences and practices, and consolidating memory between interactions. Maestro uses those concepts without its framework. [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) provide bounded shapes. [MINJA](https://arxiv.org/abs/2503.03704) demonstrates memory poisoning risks; model review supplements deterministic source, scope, pin and deletion checks.

| ID | State | Acceptance / remaining work |
| --- | --- | --- |
| MEM-01 | Implemented | Explicit memory, scoped recall, edit/pin/forget and private storage. |
| GEN-01 | Implemented | Local generation shares accounting without tools or chat persistence. |
| REF-01–03 | Implemented | Durable jobs, source revisions, bounded worker and optional reviewed proposals. |
| REF-04 | Automatic mode implemented; evaluation ongoing | Two-stage promotion and journal; measure false/stale facts, relevance, abstention and correction quality over repeated trials. |
| REF-05 | Scheduled reflection implemented | Initial idle pass, persistent interval, shared limits and editable working notes. |

Synthetic checks cover deletion, forgetting, pins, interrupted stages and accounting. Small local probes establish execution, not broad memory quality. Compare no-memory, explicit-memory and automatic modes on repeated realistic tasks before relaxing limits or expanding autonomy.