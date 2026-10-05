# Private memory and background reflection

Updated: 2026-10-05. Maestro supports manual memory, optional automatic curation, scheduled reflection and reviewed task suggestions. It uses the existing API process, private SQLite database and host Ollama, without new services or dependencies. Task execution and delegation remain planned.

## Using background reflection

1. Select Local Ollama in Settings. In **Task models & reflection**, **Background context tokens** defaults to **32,768** and requires at least 8,192. Durable jobs use this allowance independently of foreground **Local worker settings → Context tokens**.
2. In **Task models & reflection**, enable background reflection and automatic memory curation. Enable independent reflection and choose an interval: **15 minutes** for faster evaluation of recurring passes, or the **360-minute** default for six-hour intervals. Save.
3. Continue chatting. After the configured quiet period, Maestro can normalize durable facts/preferences and save reviewed changes automatically.
4. Open **Memory** from the sidebar or **Open memory** in Settings. Read **Private reflection journal** for summaries, changes, source references and model names. Edit or forget any memory; editing pins it against automatic changes.

Independent reflection has an initial pass once the worker is idle, then follows the saved interval. Chat activity or a running generation can delay a pass; daily job/token allowances can pause dispatch. It reviews Maestro's declared capabilities, improvement goals, current memories and working practices; eligible exchanges add observations when available. A pass can assess its practices without new chats or saved memories. It distinguishes observed problems from untested hypotheses and develops concise, revisable identity/lesson notes or measurable experiments. These notes are not consciousness, user facts or new permissions, and changing them does not train model weights.

Optionally enable **AI can create tasks during reflection** in **Task models & reflection**. Each periodic pass can suggest at most two supported follow-ups or self-improvement experiments and save independently approved ones directly in **Tasks**. Improvement tasks need a concrete outcome or evaluation check; an untested idea cannot claim a demonstrated problem or completed improvement. Rows show **Initiated by** You/Maestro and an editable **Suggested for** You/Maestro choice. This setting defaults off, requires automatic curation and independent reflection, and adds no model call or task execution.

Use the thumbs beside an answer to record whether it helped, with an optional correction or explanation. Feedback is saved locally without a model call. The next scheduled reflection prioritizes rated exchanges and reviews complete user/assistant pairs against intent, accuracy and clarity. A rating is a user report, not proof that an answer is correct. Model suggestions remain independently reviewed working notes. Editing or clearing feedback invalidates unfinished review, removes stale derived notes, and clears journal entries that could retain the old comment; it does not erase earlier chats or backups.

Automatic curation and periodic reflection default off for existing installations. With automatic curation off, new eligible chat evidence produces exact-quote proposals requiring acceptance before recall. Existing proposals remain accessible in automatic mode. Only accepted, unedited proposals with matching fingerprints can become managed memories; manual entries and corrections stay pinned.

Memory shows queued/running work, reflection usage, the next reflection and stop reasons alongside the journal. Settings contains configuration controls. Closing the browser or locking Windows does not stop the server. Windows must stay awake and Maestro/Ollama must remain running. Reboot startup is not installed; see [Windows background operation](WINDOWS_BACKGROUND.md).

## Exploring the memory map

**Memory → Private memory** opens a map grouped into facts, preferences, working identity and lessons. Select a node for its full text, origin, scope and sources, with the existing edit/pin and forget actions. Search and scope filters narrow the records; **List** provides a text view. Zoom and scrolling let you inspect the map without a continuously moving layout. Only a bounded page of nodes is drawn, so the 2,000-record capacity does not require displaying every memory at once.

The map draws at most 20 memory nodes and eight source conversations at once. Arrows connect recorded conversations and source memories to their derived notes; selecting a source highlights its connections. These are provenance links, not inferred topic similarity, confidence scores or proof of an AI's reasoning. Full sources remain available in the selected memory's details. The map uses the existing records and APIs; drawing, filtering and selecting nodes makes no model call.

## Saved configuration

Private work_config settings are validated through GET/PUT /api/work-config. Chat/orchestrator choices remain in provider settings. Background work requires Local Ollama and has no remote fallback.

| Setting | Default | Effect |
| --- | --- | --- |
| Background reflection | Off | Allows new work; pausing invalidates late results. |
| Automatic memory curation | Off | Formation, independent review and automatic promotion. |
| Independent reflection | Off | Scheduled identity/lesson maintenance and self-improvement review when automatic curation is on. |
| AI can create tasks during reflection | Off | Allows periodic reflection to add up to two independently approved tasks to the existing list. |
| Reflection interval | 360 minutes | Initial eligible pass, then the saved interval; range five minutes to seven days. 15 minutes supports faster evaluation. |
| Reflection / memory / coding model | Recommended installed tag | gpt-oss:20b / qwen2.5:7b / devstral-small-2:24b; otherwise local chat fallback. |
| Background context tokens | 32,768 | Captured per-job Ollama context, up to 131,072; independent of foreground chat context, with workspace/daily budget checks. |
| Exchanges per reflection / source characters | 16 / 64,000 | Complete exchange sampling; configurable up to 32 / 200,000, with an additional context fit check. |
| Output tokens | 8,192 | Captured per-call ceiling up to 32,768; independent of foreground output settings, with workspace/daily budget checks. |
| Debounce / chat idle time | 60 / 30 seconds | Coalesces evidence and waits for inactivity. |
| Daily jobs / total tokens | 50 / 2,000,000 | One workflow counts as one job; both calls consume tokens. Zero pauses dispatch. |
| Job timeout | 600 seconds | One deadline covers both model checks and generations. |
| Memories per reply / characters | 40 / 32,000 | Recall ceilings, up to 100 / 200,000; either zero disables recall. |

Saved settings retain their explicit values when the application is upgraded; new fields receive their defaults. Increase existing limits in Settings to use the new allowances. Chat input supports 32,000 characters, individual memories 8,000, and task details 16,000. Larger maxima allow useful context; they do not cause calls to run continuously or consume their full output allowance. A larger daily token budget permits more work without requiring longer answers. Per-request token allowance defaults to 500,000. Monetary and hosted search limits are independent.

Explicit model choices must be installed and support completion. Missing choices fail visibly; Maestro never downloads or silently substitutes them. Blank choices use the recommended installed tag or chat fallback. Coding configuration supports text generation, not autonomous execution. See [Local model assessment](LOCAL_MODELS.md).

## Formation, review and persistence

Chat curation uses the memory model to normalize and sanitize facts/preferences, then the reflection model reviews numbered changes. Factual wording must match a contiguous passage of quoted user evidence, allowing first-person and verb agreement changes while preserving word order and negation. Periodic reflection reverses these roles: the reflection model develops working notes and the memory model reviews them. It can also remove an obsolete managed fact, but cannot invent or rewrite user facts without new user evidence. Either can abstain. Review approves indices rather than adding a rewrite loop; each workflow permits at most two calls.

Maintenance prompts examine existing managed entries before adding new ones. Prefer refining a relevant entry under its existing ID, consolidating duplicate working notes with an update and removal, or removing a superseded, unsupported or misclassified entry. Age alone is not a reason to delete useful memory. An empty change list is a valid result. Identity/lesson notes should describe specific, revisable practices; they should not require constant clarification, claim unverified privacy guarantees or grant permissions. User facts/preferences still require exact current user evidence for additions or updates; merging older factual details without that evidence is not supported.

This uses the add/update/remove/no-op pattern described in the [Mem0 research](https://arxiv.org/html/2504.19413v1#S2.SS1), implemented through Maestro's existing bounded workflow rather than another agent framework. Independent review evaluates necessity as well as support, and rejects redundant additions or destructive consolidation that loses distinct useful information. Automation never edits or removes a user-pinned record.

Formation uses separate schema branches for a new memory (add with an empty ID) and an existing memory (update/remove with a selected editable ID). The review schema permits only indices for the actual proposed changes and an empty approval list when no changes were proposed. The captured draft count is checked between stages; duplicate edits to one memory are rejected before review.

When `auto_create_tasks` is enabled for a periodic job, formation also supplies bounded task drafts and review supplies separately bounded approved-task indices. Up to 100 existing titles within 12,000 characters provide task context; duplicate checks still cover the full list. Both calls reuse the same source/configuration checks, deadline and daily allowances. Approved tasks are persisted with `initiated_by=maestro` and a suggested assignee; API-created tasks always use `initiated_by=user`. Existing open/completed task titles prevent duplicates, and deleting an AI-created task retains a bounded title hash to suppress the same suggestion. Suggested assignment does not authorize execution or delegation.

AI-created tasks retain source-message, feedback and memory dependencies. Deleting a source chat or correcting/forgetting a source memory removes affected tasks; feedback changes also remove tasks whose captured answer revision is stale. Changing completion or suggested assignee preserves those dependencies. Independent user-created tasks and older records without recorded provenance remain.

Drafts stay in RAM. SQLite stores source references/hashes, captured settings, attempt/stage, request IDs and usage. Promotion requires strict JSON, exact user evidence for facts/preferences, valid scope, matching source/memory revisions, credential checks and independent approval. Scheduled working notes leave source-message/evidence fields empty; Maestro attaches the selected exchanges' provenance, rather than asking the model to turn an assistant quote into user evidence. Explicit memories are protected. Model review can still err; schema validation establishes shape, not truth. Rejected output shows a fixed diagnostic category in Memory; private responses and exception details are not saved as errors.

The private journal stores summaries, applied changes, source references, models and timestamps. New periodic entries appear as **Self-improvement reflection**, with their capabilities-and-practices assessment basis; zero source messages means no exchanges were used, not missing chat evidence. Earlier entries retain their original labels. Journal prose is not fed back as evidence for later passes. It retains at most 1,000 entries and exposes the newest 100 in Memory. Summaries allow up to 2,000 characters. Hidden reasoning transcripts are not saved. Actual entries stay in private SQLite outside the public repository.

Records distinguish fact, preference, identity and lesson, with explicit, curated or reflective origin. Metadata contains evidence and provenance. Human corrections become explicit pinned authority; automation changes managed records only. Earlier model ideas cannot justify invented user facts. Working notes enter local prompts as quoted suggestions.

New working notes retain revisioned dependencies on unchanged source memories, so correcting or forgetting a source also removes dependent notes and tasks. When one reviewed batch updates or consolidates source memories, Maestro carries their earlier source lineage forward while avoiding references that would become stale or cyclic in that same batch.

## Runtime and resource bounds

One worker runs in FastAPI's lifespan under exclusive workspace ownership. Processing runs off the API event loop; the idle loop makes no inference calls. Ready jobs proceed ahead of debouncing or previously paused work. Jobs share chat's single reservation slot and usage ledger. Chat can run between formation and review; new activity or changed sources can invalidate review. In-flight generation can briefly delay chat; instant preemption is not implemented.

Durable jobs fit and dispatch both stages using their captured background context/output ceilings (32,768/8,192 by default), independently of foreground caps. Workspace request limits, daily work budgets, the shared slot and job deadline still apply. Changed provider/work/limit settings or sources invalidate pending stages. Ad hoc explicit-context calls without a durable job retain provider caps. Higher context increases Ollama's memory allocation even with a short prompt; CPU offloading can slow generation. Calls remain sequential and request keep_alive=0, so background work releases models after completion. Both stages reserve conservative usage before dispatch. First-call usage remains accounted if review fails or is blocked. No tools, hosted search or conversation titles enter background calls. Unknown completion retains the slot until the user restarts the original Ollama server and explicitly confirms that restart in **Usage & limits**. Recovery then verifies an empty model list on that captured endpoint; the model list alone cannot establish termination of loading or queued work. Interrupted passes are not retried. Keep other clients idle during recovery; Maestro never forcibly restarts their server or unloads their models. See [Ollama context guidance](https://docs.ollama.com/context-length) and [memory/concurrency settings](https://docs.ollama.com/faq).

Source context is bounded and reduced to fit the configured allowance. Chat curation can use user-message prefixes, so later facts can be missed. Periodic reflection samples complete exchanges, prioritizing negative feedback, then positive feedback, then recent unrated pairs. Oversized pairs are skipped rather than judging a truncated answer. Reviewed exchanges, selected memories, task context and draft output share the actual model context; configured character and record maxima are ceilings, not guaranteed prompt sizes. Both calls must fit; if review cannot fit, no memory is promoted. New exchanges record eligibility: only normal local chat without active hosted web search supplies periodic evidence. Historical remote/search turns are not guessed eligible. Periodic reflection excludes conversation-scoped memories and their chats; assistant replies and ratings are observations, never user-fact evidence.

Memory selection interleaves protected pins with managed records rather than filling every slot with pins. Chat curation ranks managed facts/preferences by overlap with new user evidence. Periodic maintenance rotates older managed records using the previous job's selected IDs, while retaining the canonical working identity in context. Selection is limited to 40 memories and 32,000 content/evidence characters within both calls' fit checks. When present, new source evidence and existing task titles each reserve a quarter of the formation input allowance before memory packing; task titles reserve at most 12,000 bytes. A pass without chats or tasks can use the full memory allowance. Stored user evidence is shown for assessing an entry's durability. Useful older memories remain eligible; no age-based expiry is introduced.

Queued work and schedule timestamps survive restart. Interrupted dispatched or between-stage work is abandoned without replay; drafts are not resumed. Attempt tokens, source hashes, memory revisions and invalidation epochs reject stale commits. Settings changes, forgetting and source deletion invalidate late results. Shutdown waits for in-flight processing to finish or reach its deadline.

## Recall, correction and forgetting

The store permits 2,000 records of at most 8,000 characters. Recall combines scoped Unicode keyword overlap with an 8,000-character allowance for working-identity notes inside the configured overall recall limit. Defaults allow 40 records and 32,000 total characters; entries that cannot fit generation limits are omitted. Measure retrieval misses before adding embeddings or another database.

Only normal local Ollama chat without hosted search receives memory. Replies record supplied memory IDs, indicating context provided rather than proof of use. Conversations with memory-derived history cannot later dispatch remotely or use hosted search; start a new conversation for those modes.

Editing pins a correction and invalidates pending work. Forgetting removes the record and dependent derived material. Human corrections and forgetting clear journal entries conservatively so old copies cannot bypass forgetting. Compact source barriers prevent old chats from recreating forgotten/superseded facts under different wording; fresh messages can supply new evidence. Chat deletion removes scoped/source-linked memories and dependent jobs. Unlinked explicit workspace records survive.

Reviewed updates preserve the memory's ID, kind, scope and creation date. Consolidation uses a snapshot taken before any mutation so retained details keep their earlier source references regardless of operation order. Identical updates are skipped before invalidating dependent notes. If an earlier approved mutation already removed a dependent target, later operations skip it rather than recreating it under a new ID. Applied changes and abstentions remain visible in the journal; changes to a source memory still invalidate dependent material through the existing provenance checks.

Forgetting affects future recall, not dispatched prompts, earlier replies, snapshots or backups. SQLite secure deletion is enabled. Known active credentials and common credential patterns are rejected, but AI sanitation and pattern checks cannot detect every secret. Keep passwords and keys out of chat and memory.

Management routes remain GET/POST /api/memory and PATCH/DELETE /api/memory/{id}. PATCH /api/chats/{chat_id}/messages/{message_id}/feedback stores or clears feedback on an existing assistant answer. Feedback lives on that message, is never added to ordinary chat history sent to providers, and disappears when its chat is deleted. GET /api/reflection provides read-only status, current memories and journal for guarded UI polling. Legacy proposal accept/reject routes remain. Session, Origin and CSRF protections apply; errors do not echo private input.

## Recorded next tasks

[LangMem's concepts](https://github.com/langchain-ai/langmem/blob/main/docs/docs/concepts/conceptual_guide.md) support separating facts, experiences and practices, and consolidating memory between interactions. Maestro uses those concepts without its framework. [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) provide bounded shapes. [MINJA](https://arxiv.org/abs/2503.03704) demonstrates memory poisoning risks; model review supplements deterministic source, scope, pin and deletion checks.

| ID | State | Acceptance / remaining work |
| --- | --- | --- |
| MEM-01 | Implemented | Explicit memory, scoped recall, edit/pin/forget and private storage. |
| MEM-02 | Interactive map implemented | Type groups, bounded memory/source nodes, search/scope filters, selection details and shared edit/forget actions; text list retained. |
| GEN-01 | Implemented | Local generation shares accounting without tools or chat persistence. |
| REF-01–03 | Implemented | Durable jobs, source revisions, bounded worker and optional reviewed proposals. |
| REF-04 | Automatic mode implemented; evaluation ongoing | Two-stage promotion and journal; measure false/stale facts, relevance, abstention and correction quality over repeated trials. |
| REF-05 | Scheduled reflection implemented | Initial idle pass, persistent interval, shared limits and editable working notes. |
| REF-06 | Optional answer feedback and selective review implemented | Editable ratings/comments, complete exchange sampling, short rubric, source revisions and existing six-hour worker. Evaluate whether it reduces repeated mistakes. |
| REF-07 | Maintenance prompts and selection implemented | Prefer meaningful same-ID refinement, working-note consolidation, supported removal or abstention; rotate managed entries, preserve pins, skip identical rewrites and handle cascaded removals. Evaluate usefulness and preservation of distinct information over repeated passes. |
| TASK-01 | Reviewed task creation implemented | Optional periodic drafts and independent review, immutable initiator, editable suggested worker, duplicate/dismissal suppression and source attribution. Evaluate usefulness; task execution remains planned. |

Synthetic checks cover deletion, forgetting, pins, feedback edits, interrupted stages and accounting. Small local probes establish execution, not broad memory quality. Compare no-memory, explicit-memory and automatic modes on repeated realistic tasks to assess whether review improves results. [A critical survey of LLM self-correction](https://arxiv.org/abs/2406.01297) supports collecting external feedback; unaided self-review does not reliably establish correctness. Session summaries and a separate review call after every answer are deferred until evidence shows a need.
