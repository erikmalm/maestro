# Maestro

A local web interface for persistent chats with your chosen model, usage tracking and to-dos. Workspace, Tasks, Memory and Settings expose live conversation, user-created and reviewed AI-created tasks, private memory and configuration.

Maestro saves one provider connection with chat and orchestrator models. Local chat can use either as its default route, with per-message model choices, Markdown and private memory. Opt-in background reflection curates reviewed memories automatically, maintains working notes and provides a private journal. It can also create independently reviewed task suggestions. Task execution and delegation remain future work. Optional Ollama web search and saved-source lookup share one bounded source tool. The [development plan](DEVELOPMENT_PLAN.md) and [memory/reflection architecture](docs/MEMORY_AND_REFLECTION.md) describe working behavior and the next increments.

## Run locally

Windows, Python 3.11+, and Node.js 20.19+ or 22.12+:

```powershell
.\scripts\start.ps1 -NoBrowser
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Stop with `.\scripts\stop.ps1`. If script execution is blocked, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 -NoBrowser`.

For a non-root Podman container, private workspace volume and host Ollama, follow the [container runbook](docs/CONTAINERS.md). It includes build/start/stop, loopback networking, mounted secrets, migration and backup/restore.

## Connect and chat

For local Ollama:

1. Start Ollama and install a chat model using its app or `ollama pull <model>`.
2. In Maestro **Settings**, choose **Model provider → Ollama · local**. The default server is `http://127.0.0.1:11434`.
3. Click **Connect and load models**, select an installed model, then **Save connection**.
4. Return to **Workspace** and send a message. Tokens are reported by Ollama and provider API charges are $0; hardware and electricity costs are not estimated.

The installed-model list is the same inventory shown by `ollama list`. In **Settings → Model provider → Chat routing**, choose **Chat model** or **Orchestrator model**, then save the connection. The orchestrator route uses its configured installed model to handle ordinary chat and decide whether the available source tool is needed; a blank orchestrator choice uses the chat model. Click the model name below the message field to override that choice for a message. Switching preserves the conversation and draft, and each answer records the model actually used. After adding or removing models in Ollama, use **Connect and load models** in Settings to update the list. Chat still uses the chat role, one shared generation slot and the same answer budget. A separate assessment call, live stage events and specialist delegation remain planned. Saving a task does not execute it.

New replies reveal progressively after generation finishes; saved history appears immediately. This display effect respects reduced-motion preferences and does not stream tokens from the provider or shorten the initial wait.

Ollama mode uses the native local API without a key or price entry. Only loopback endpoints, the exact trusted container host endpoint and installed local chat models are accepted; cloud models are excluded and there is no cloud fallback. Output and conversation token limits still apply, including when dollar budgets are zero. Models and Ollama's own configuration stay outside this repository. See [Ollama's local API](https://docs.ollama.com/api/authentication).

Expand **Local worker settings** to control context size (default 32,768 tokens), CPU threads (0 lets Ollama choose), and idle keep-loaded time (default 5 minutes; 0 unloads after each reply). Save changes before the next message. Larger contexts use more memory; these settings do not limit GPU utilization. Maestro conservatively checks conversation size plus the reply allowance before dispatch and asks for a new chat or a larger context when it would not fit. Ollama runs inference on demand; these settings do not start a background task runner. See [Ollama's context and keep-alive settings](https://docs.ollama.com/faq).

For OpenAI or another API provider:

1. Open **Settings** and choose **Model provider → OpenAI** or **Custom API**. For a custom provider, enter its compatible HTTPS endpoint/local loopback server and API protocol.
2. Enter the API key and **Connect and load models**. This saves the key and fetches the model list without generating a response. Default storage is Windows Credential Manager. Uncheck persistence to keep a new key only in server memory.
3. Select/type the desired model ID. The key authorizes your account/project; the saved model is used for subsequent chat requests. Models listed by an API are not necessarily chat-capable.
4. Selecting the exact `gpt-6-luna`, `gpt-6.1-sol` or `gpt-6-astra` ID fills standard OpenAI short-context prices when the dated snapshot is at most 30 days old. For other models/providers or an older snapshot, enter verified input/output prices in USD per million tokens and enable **Use these prices for cost estimates**. Save the connection. Saved prices remain in effect until updated; zero prices are valid only for genuinely free inference.
5. Return to **Workspace**, send a message, and inspect the answer, token counts and estimated cost. Successful model-list access alone does not verify generation.

OpenAI uses the Responses API. The Chat Completions option supports compatible providers; compatibility and model access depend on that provider. Chat uses actual provider responses and has no canned fallback. Missing settings, unsupported models, exhausted limits and provider errors produce explicit errors. The first version waits for a complete response rather than streaming it.

Each chat keeps its own history, and only that chat's messages are sent with later requests. **New chat** preserves previous chats; select them in the sidebar to continue. Chats can be renamed or deleted; deletion removes the conversation and keeps usage accounting. Earlier live history migrates into one **Previous conversation**.

After the first successful reply, Maestro tries once to name the chat using the same model, a bounded first message and the remaining request allowance, without tools. If naming fails, model settings change or the allowance is insufficient, a short title from the first message remains. A manual rename always takes precedence. Usage totals include title calls. Project grouping is a later increment.

To-dos can be added, completed/reopened and deleted without model calls. Each task identifies its initiator and suggests either you or Maestro as its worker; you can change that suggestion without changing the initiator. Optional periodic reflection can add up to two independently reviewed tasks per pass to the same list. Matching open/completed task titles and dismissed AI titles suppress repeats. Saving or suggesting a worker does not execute a task.

Shared local generation and opt-in background reflection reuse the chat usage ledger. Reusable search excerpts and local keyword lookup are implemented. General grounding and verification is the next milestone, covering source-assisted claims and claims about actions Maestro performed; selected full-page capture follows. The later execution increment is one durable text-only task, completing with the browser closed and supporting pause/restart recovery. Multiple saved model profiles and bounded delegation follow that working path. See the [build sequence](docs/ARCHITECTURE.md#next-build-sequence).

## Optional automatic web search

Ollama web search is a hosted tool, separate from local model inference. Creating an Ollama API key does not automatically add it to chat. Maestro explicitly offers one search tool to a compatible local model; when the model requests it, the backend calls the fixed hosted search API and supplies bounded excerpts for one final local reply. Result websites are not crawled or fetched by Maestro. See [Ollama's web search API](https://docs.ollama.com/capabilities/web-search).

1. In **Settings**, select your local Ollama model, expand **Local worker settings**, start with **32768** context tokens, and save the model connection. Smaller contexts need a smaller output allowance to leave room for prompts.
2. In **Optional web search**, enter the Ollama search API key. Leave **Save search key in Windows Credential Manager** checked for persistence outside Git, or uncheck it for server-memory storage.
3. Set a **Daily search cap** (default 20; 0 stops search) and **Results per search** (1-3). Click **Test search connection**. It saves the key if necessary and sends one fixed public query, which counts against the cap. Testing keeps automatic search disabled. If source saving is enabled with **Save eligible public search results**, eligible test excerpts are also archived; tests always make a fresh hosted request.
4. After a successful test, check **Let Maestro decide when to search** and **Save search settings**. Chat will show the actual query and numbered source links when search is used. The key remains separate from the local model connection.

Automatic search sends the model's chosen query to Ollama.com; queries can contain terms from the conversation. The key is sent only to the hosted search endpoint and never to the local model. Local models must report tool support. Maestro permits at most one search and two local model calls for an answer, sharing the reply token allowance and recording both calls' actual usage. The optional first-chat title call uses only the remaining allowance. Ordinary replies can complete without search. Failed searches show explicit errors and preserve known model usage; they do not silently generate a purported search answer.

Open the globe button at the bottom-left of the local chat composer for **Search options**. It shows online readiness and missing setup, limits or a cooldown separately from source saving. Explicit search requests and recognized dated Local Ollama weather requests require source evidence, including in **Prefer saved**. If fresh search is required and unavailable, Maestro stops before inference; if the model ignores the available tool, it saves no unsupported answer and still records consumed tokens. **Saved only** keeps lookup local.

Relative days such as tomorrow resolve once against the application's local date, and hosted queries include that calendar date. Recognized dated local weather requires the full requested date in the fitted excerpt body; a retrieval timestamp, HTTP(S) link or repeated title alone cannot pass this check. Without qualifying evidence, the backend reports that it cannot verify the forecast and skips final synthesis, while preserving search captures and consumed usage. Date presence still does not establish forecast accuracy. A conversation containing memory-derived replies offers **New chat for online search**, preserving the draft and earlier conversation.

Search attempts, including tests, failures and interrupted requests, count toward the daily cap. Requests are at least five seconds apart, have a 30-second search timeout and no automatic retry. Rate-limit responses pause further searches and respect `Retry-After` (60 seconds if absent). These controls use the hosted service normally and do not evade site or service restrictions. Search counts are visible in usage, and cooldowns appear beside the search controls in Settings; the search API supplies no billing feed, so search fees are not included in the local model's $0 inference figure. [Ollama's announcement](https://ollama.com/blog/web-search) describes free searches and subscription rate limits without a numerical quota or per-search price.

Rejected key/account access disables automatic search until the key is successfully retested and search is explicitly enabled again. Ordinary local chat remains available.

## Saved web sources

An optional public-source archive preserves search excerpts for reuse, with their original retrieval dates and numbered citations. Configure a directory outside Git using the [container archive option](docs/CONTAINERS.md#saved-public-web-sources) or [native startup environment](docs/WINDOWS_BACKGROUND.md#application-lifetime-and-startup). Then enable **Settings → Saved web sources**. New configurations select **Save eligible public search results** but leave saving disabled until you enable it. This policy accepts eligible public HTTP/HTTPS results without a site list, while excluding unsafe, credential-bearing, authenticated and personal URL forms. The archive can use OneDrive; query mappings, conversations, private memory, credentials and its rebuildable index stay in private local storage.

Choose **Only approved URLs** to limit saving to public HTTPS scopes. Existing configurations retain that policy and their saved limits. Site/path scopes apply only to URLs without query parameters. To approve a public parameterized URL, enter its complete HTTPS URL: the query values, order and encoding must match exactly. Search can work while saving is off or a result is ineligible; check the globe menu's separate saving status and counters. Enabling saving preserves future eligible excerpts and does not fill old unarchived citations or download full pages.

Each fresh retrieval creates immutable timestamped source snapshots; repeated identical excerpt bytes share an object rather than replacing history. New configurations allow **10 GiB** and **200,000 files and directories**, with adjustable limits in Settings. Settings shows measured archive usage and the last capture's received/saved source counts, excerpt bytes and newly stored object/manifest bytes. These limits stop further capture when reached; there is no automatic pruning.

**Rebuild source index** restores the private index from verified archive manifests in resumable batches. Settings shows progress and lets you pause or continue after restart; an existing index remains usable until the replacement is complete. Changes to captures, deletion or saved configuration invalidate unfinished reconstruction and require a new job.

With a compatible local model, the globe menu offers **Prefer saved**, **Fresh search** and **Saved only**. Prefer saved first reuses an exact matching query, then tries keyword matches within the configured window when the model identifies stable information; current or uncertain lookup requests, including weather, require fresh search. Fresh search requires a ready hosted connection and bypasses reuse. Saved only makes no hosted request and may return an older match, clearly labelled. Saved lookup works without a hosted key or remaining search quota; local generation still uses the normal token allowance.

In **Settings → Saved web sources → Find saved sources**, search saved titles and text using focused keywords. All keywords must match; this can find existing evidence for a differently worded question. Filter by an exact domain and inclusive UTC retrieval dates, choose up to 20 results and open their saved text. This manual search makes no AI or online request. Newer eligible versions of a URL suppress older conflicting matches; a historical date range can select an earlier version. A lookup that reaches its work limit shows a narrowing hint rather than claiming an exhaustive result.

Open a saved citation to inspect its text, source URL, retrieval date and completeness, or remove it. Publisher dates remain unknown in this increment; a search excerpt is not a downloaded webpage. Missing or changed snapshots show an unavailable error. The [search context design](docs/SEARCH_CONTEXT_STORAGE.md) records the implemented foundation and the next steps: full page capture, publisher dates and bounded PDF extraction.

Local follow-ups about earlier search results receive a bounded server-built source record, including saving status and verified evidence when available. Explicit rewrites can reuse the original excerpt and its values without a new hosted request; unrelated new questions follow the normal source path. Recognized English/Swedish instructions prohibiting a new online search block hosted dispatch. Deleted or changed saved evidence cannot support a late answer. A JSON answer does not create archive files; the backend saves eligible search excerpts.

## Private memory

Open **Memory** from the sidebar or **Open memory** in Settings to explore the map of facts, preferences, working identity and lessons. Select a memory to read its sources, edit it or forget it. Search and scope filters narrow the map; **List** provides a text view. You can also save a fact/preference for all local chats or just the current conversation. Facts/preferences use scoped keyword recall; working notes share the configured recall allowance. Memory stays in local Ollama chat without hosted search. Saving memory makes no model call.

Memories and replies derived from them stay local: start a new conversation before switching that history to a remote provider or active hosted search. An enabled archive permits saved lookup in those conversations while hosted dispatch stays blocked. Chat deletion removes scoped/source-linked memory and reflection proposals; unlinked workspace records remain. Forgetting affects future recall and retains earlier messages/backups.

**Settings → Task models & reflection** saves task models and resource limits. Set background context to at least 8,192 tokens, then enable automatic memory curation and independent reflection for reviewed updates and working notes every six hours. Durable jobs use their captured background context/output independently of foreground chat caps while sharing the generation slot and workspace/daily budgets. Read results in **Memory → Private reflection journal**; edit to pin a memory or forget it. Chat curation uses the memory model followed by the reflection model; periodic reflection reverses those roles. Blank choices use recommended installed models or chat fallback. Automatic options default off; the earlier accept/reject proposal workflow remains available in Memory. See the [usage guide and design](docs/MEMORY_AND_REFLECTION.md#using-background-reflection), [model assessment](docs/LOCAL_MODELS.md) and [Windows lock/sleep guidance](docs/WINDOWS_BACKGROUND.md).

The interval accepts 5 minutes to seven days; try 15 minutes while evaluating repeated reflection passes. The default remains six hours. Runs wait for chat to be idle and share one generation slot. At 15 minutes there can be 96 scheduled passes per day, so allow enough daily jobs alongside chat curation; the daily token limit still bounds work. Missed intervals do not accumulate a catch-up backlog.

Optional thumbs and comments on answers provide local feedback for the next scheduled reflection. It reviews complete selected exchanges against intent, accuracy and clarity without adding a model call when you rate an answer. Background context defaults to 32,768 tokens, with 16 exchanges, 8,192 output tokens per call, 50 jobs/2,000,000 tokens per day, and recall of up to 40 memories/32,000 characters. These limits are configurable separately from chat; saved settings retain their values after upgrades.

Independent reflection also reviews Maestro's capabilities and working practices without new chats. It can refine practices and propose measurable self-improvement experiments, distinguishing untested ideas from observed issues. Enable **AI can create tasks during reflection** in **Task models & reflection** to save reviewed follow-ups and improvement ideas as to-dos. It defaults off and reuses the same two model calls, source checks and budgets. Results appear in **Tasks**, labeled **Initiated by** and **Suggested for**, with attribution in the private reflection journal. There is no task runner or automatic execution.

## Usage and limits

The header shows today's tokens and estimated USD. Each answer shows its model, input/output tokens and estimated cost. Usage details include day/month totals and reserved/uncertain charges.

Tokens come from provider responses. USD uses your configured prices and excludes cache discounts; it is not an invoice or a billing feed. Maestro does not adjust saved rates for longer contexts; update them manually using the provider's applicable rates. [OpenAI pricing](https://developers.openai.com/api/docs/pricing) lists separate short- and long-context rates. Changing prices does not recalculate earlier requests. Spend and conservative token reservations are checked before dispatch. One chat request runs at a time, with a configured output cap and no automatic retries.

Paid API requests with unknown usage retain a reservation and block further chat until their billed amount is verified in **Usage & limits** (open it from the header or **Open usage** in Settings). Known access/quota rejections release the reservation. Confirmed local Ollama failures release their zero-cost reservation. A generation timeout, interrupted local call, or response without confirmed completion keeps the generation slot reserved. An empty Ollama model list can omit loading or queued work, so it does not release the slot automatically. Stop other work on the original Ollama server, quit/stop that server completely and restart it. In **Usage & limits**, confirm that you restarted the server shown for the interrupted request and click **Verify and release slot**. Maestro checks that original server is reachable and has no loaded models before releasing that request. Restarting Maestro alone preserves the block; recorded usage remains accounted.

An older interrupted local call may lack a saved server address. Enter its original **Ollama server URL** in the recovery form and confirm that you restarted that server. Saving or testing a new model connection does not assign an address to the interrupted request. This confirmation is your acknowledgement of restarting the original server; Maestro cannot detect a daemon restart from its model list. Keep other Ollama clients idle during recovery. Restarting the server interrupts their work too.

## Private credentials and data

Keys never go into Git, the workspace database, browser storage, prompts, read API responses or logs. The password field clears on submission. Windows persists keys in endpoint-scoped Credential Manager entries. Linux supports session-only keys and externally managed mounted secrets; the UI reflects those capabilities. Session-only keys disappear when the server stops. Remove `OPENAI_API_KEY` from the server environment and restart Maestro before removing an OpenAI key through the UI.

The Ollama search key uses its own credential scope for `https://ollama.com/api/web_search`. Removing it disables automatic search and leaves the local model connection intact.

Tasks, conversations, model settings and accounting live in `%LOCALAPPDATA%\Maestro\preview`, outside the checkout. An alternative `MAESTRO_DATA_DIR` must also resolve outside it. The launcher reuses a running server only for the same private directory; stop that instance before switching directories. A public repository contains only reusable source, documentation and synthetic fixtures.

Earlier prototype runs, memory records and simulated accounting remain archived in the private database; their APIs and demo screens have been removed. Simulated chat messages are excluded from active history and model context. Real conversations, provider settings and usage accounting are preserved.

When you send API chat, its history and your key go to the configured provider. In Ollama mode, history goes to your local server without any saved provider key. Provider retention rules still apply. OpenAI requests use `store: false`; see [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data). The backend binds to loopback and requires local session/CSRF protection.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
cd frontend
npm run build
npx playwright install chromium
npm run test:ui
```

Browser tests exercise setup, separate chats, rename/delete, usage and automatic search with source persistence against synthetic API/Ollama/search services in a temporary workspace. Run the full UI suite: its scenarios share that workspace and build on earlier provider setup. Windows credential tests use a synthetic key and remove it afterward. Automated checks require no personal credentials or paid calls. Real local tool selection and final generation are verified with synthetic hosted search results; hosted authentication must be verified with the user's key through **Test search connection**. A real LLM check requires either a running local Ollama model or a configured provider key, model and prices.

The [architecture](docs/ARCHITECTURE.md) distinguishes the implemented runtime from planned task execution. Automated checks use synthetic providers; they do not establish remote model access or hosted search authentication.
