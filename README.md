# Maestro

A local web interface for persistent chats with your chosen model, usage tracking and to-dos. Workspace, Tasks and Settings expose the working core: live conversation, manual task management and provider configuration.

Maestro saves one provider connection with a chat model and a separate orchestrator preference. Chat supports per-message model choices, Markdown and private memory. Opt-in background reflection proposes evidence-backed memories for user approval. Task execution and delegation remain future work. Optional Ollama web search is the only model-selected tool. The [development plan](DEVELOPMENT_PLAN.md) and [memory/reflection architecture](docs/MEMORY_AND_REFLECTION.md) describe working behavior and the next increments.

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

The installed-model list is the same inventory shown by `ollama list`. Click the model name below the message field to choose a different chat model; the configured chat default is selected initially. Switching preserves the conversation and draft, and each answer records the model actually used. After adding or removing models in Ollama, use **Connect and load models** in Settings to update the list. Settings also retains a separate orchestrator model preference for future orchestration; the chat window always sends chat messages. Saving a task does not execute it.

New replies reveal progressively after generation finishes; saved history appears immediately. This display effect respects reduced-motion preferences and does not stream tokens from the provider or shorten the initial wait.

Ollama mode uses the native local API without a key or price entry. Only loopback endpoints, the exact trusted container host endpoint and installed local chat models are accepted; cloud models are excluded and there is no cloud fallback. Output and conversation token limits still apply, including when dollar budgets are zero. Models and Ollama's own configuration stay outside this repository. See [Ollama's local API](https://docs.ollama.com/api/authentication).

Expand **Local worker settings** to control context size (default 4,096 tokens), CPU threads (0 lets Ollama choose), and idle keep-loaded time (default 5 minutes; 0 unloads after each reply). Save changes before the next message. Larger contexts use more memory; these settings do not limit GPU utilization. Maestro conservatively checks conversation size plus the reply allowance before dispatch and asks for a new chat or a larger context when it would not fit. Ollama runs inference on demand; these settings do not start a background task runner. See [Ollama's context and keep-alive settings](https://docs.ollama.com/faq).

For OpenAI or another API provider:

1. Open **Settings** and choose **Model provider → OpenAI** or **Custom API**. For a custom provider, enter its compatible HTTPS endpoint/local loopback server and API protocol.
2. Enter the API key and **Connect and load models**. This saves the key and fetches the model list without generating a response. Default storage is Windows Credential Manager. Uncheck persistence to keep a new key only in server memory.
3. Select/type the desired model ID. The key authorizes your account/project; the saved model is used for subsequent chat requests. Models listed by an API are not necessarily chat-capable.
4. Selecting the exact `gpt-6-luna`, `gpt-6.1-sol` or `gpt-6-astra` ID fills standard OpenAI short-context prices when the dated snapshot is at most 30 days old. For other models/providers or an older snapshot, enter verified input/output prices in USD per million tokens and enable **Use these prices for cost estimates**. Save the connection. Saved prices remain in effect until updated; zero prices are valid only for genuinely free inference.
5. Return to **Workspace**, send a message, and inspect the answer, token counts and estimated cost. Successful model-list access alone does not verify generation.

OpenAI uses the Responses API. The Chat Completions option supports compatible providers; compatibility and model access depend on that provider. Chat uses actual provider responses and has no canned fallback. Missing settings, unsupported models, exhausted limits and provider errors produce explicit errors. The first version waits for a complete response rather than streaming it.

Each chat keeps its own history, and only that chat's messages are sent with later requests. **New chat** preserves previous chats; select them in the sidebar to continue. Chats can be renamed or deleted; deletion removes the conversation and keeps usage accounting. Earlier live history migrates into one **Previous conversation**.

After the first successful reply, Maestro tries once to name the chat using the same model, a bounded first message and the remaining request allowance, without tools. If naming fails, model settings change or the allowance is insufficient, a short title from the first message remains. A manual rename always takes precedence. Usage totals include title calls. Project grouping is a later increment.

To-dos can be added, completed/reopened and deleted without model calls; saving a task does not execute it.

Shared local generation and opt-in background reflection now reuse the chat usage ledger. The next execution increment is one durable text-only task, completing with the browser closed and supporting pause/restart recovery. Multiple saved model profiles and bounded delegation follow that working path. See the [build sequence](docs/ARCHITECTURE.md#next-build-sequence).

## Optional automatic web search

Ollama web search is a hosted tool, separate from local model inference. Creating an Ollama API key does not automatically add it to chat. Maestro explicitly offers one search tool to a compatible local model; when the model requests it, the backend calls the fixed hosted search API and supplies bounded excerpts for one final local reply. Result websites are not crawled or fetched by Maestro. See [Ollama's web search API](https://docs.ollama.com/capabilities/web-search).

1. In **Settings**, select your local Ollama model, expand **Local worker settings**, set context size to at least **8192**, and save the model connection.
2. In **Optional web search**, enter the Ollama search API key. Leave **Save search key in Windows Credential Manager** checked for persistence outside Git, or uncheck it for server-memory storage.
3. Set a **Daily search cap** (default 20; 0 stops search) and **Results per search** (1-3). Click **Test search connection**. It saves the key if necessary and sends one fixed public query, which counts against the cap. Testing keeps automatic search disabled.
4. After a successful test, check **Let Maestro decide when to search** and **Save search settings**. Chat will show the actual query and numbered source links when search is used. The key remains separate from the local model connection.

Automatic search sends the model's chosen query to Ollama.com; queries can contain terms from the conversation. The key is sent only to the hosted search endpoint and never to the local model. Local models must report tool support. Maestro permits at most one search and two local model calls for an answer, sharing the reply token allowance and recording both calls' actual usage. The optional first-chat title call uses only the remaining allowance. Ordinary replies can complete without search. Failed searches show explicit errors and preserve known model usage; they do not silently generate a purported search answer.

Search attempts, including tests, failures and interrupted requests, count toward the daily cap. Requests are at least five seconds apart, have a 30-second search timeout and no automatic retry. Rate-limit responses pause further searches and respect `Retry-After` (60 seconds if absent). These controls use the hosted service normally and do not evade site or service restrictions. Search counts and cooldowns are visible in Settings/usage; the search API supplies no billing feed, so search fees are not included in the local model's $0 inference figure. [Ollama's announcement](https://ollama.com/blog/web-search) describes free searches and subscription rate limits without a numerical quota or per-search price.

Rejected key/account access disables automatic search until the key is successfully retested and search is explicitly enabled again. Ordinary local chat remains available.

## Private memory

In **Settings → Private memory**, save a fact/preference for all local chats or just the current conversation. You can edit or forget it later. Relevant records are recalled by keyword overlap, with a small context allowance, for local Ollama chat when hosted search is inactive. Saving memory makes no model call.

Memories and replies derived from them stay local: start a new conversation before switching that history to a remote provider or active hosted search. Chat deletion removes scoped/source-linked memory and reflection proposals; unlinked workspace records remain. Forgetting affects future recall and retains earlier messages/backups.

**Settings → Task models & reflection** saves separate reflection, extraction and coding models and resource limits. Enable background reflection to process new local chat evidence after a quiet period. It proposes source quotes in **Private memory**; accept a proposal for its conversation or all local chats, or reject it. Proposals are not recalled before acceptance. Blank model choices use the recommended installed model or chat fallback. The worker uses the reflection model; coding and standalone extraction are separate future paths. See the [usage guide and design](docs/MEMORY_AND_REFLECTION.md#using-background-reflection), [model assessment](docs/LOCAL_MODELS.md) and [Windows lock/sleep guidance](docs/WINDOWS_BACKGROUND.md).

## Usage and limits

The header shows today's tokens and estimated USD. Each answer shows its model, input/output tokens and estimated cost. Usage details include day/month totals and reserved/uncertain charges.

Tokens come from provider responses. USD uses your configured prices and excludes cache discounts; it is not an invoice or a billing feed. Maestro does not adjust saved rates for longer contexts; update them manually using the provider's applicable rates. [OpenAI pricing](https://developers.openai.com/api/docs/pricing) lists separate short- and long-context rates. Changing prices does not recalculate earlier requests. Spend and conservative token reservations are checked before dispatch. One chat request runs at a time, with a configured output cap and no automatic retries.

Paid API requests with unknown usage retain a reservation and block further chat until their billed amount is verified in Settings. Known access/quota rejections release the reservation. Local Ollama failures release their zero-cost reservation. Restart preserves accounting and marks interrupted paid requests uncertain.

## Private credentials and data

Keys never go into Git, the workspace database, browser storage, prompts, read API responses or logs. The password field clears on submission. Windows persists keys in endpoint-scoped Credential Manager entries. Linux supports session-only keys and externally managed mounted secrets; the UI reflects those capabilities. Session-only keys disappear when the server stops. Remove `OPENAI_API_KEY` from the server environment and restart Maestro before removing an OpenAI key through the UI.

The Ollama search key uses its own credential scope for `https://ollama.com/api/web_search`. Removing it disables automatic search and leaves the local model connection intact.

Tasks, conversations, model settings and accounting live in `%LOCALAPPDATA%\Maestro\preview`, outside the checkout. An alternative `MAESTRO_DATA_DIR` must also resolve outside it. The launcher reuses a running server only for the same private directory; stop that instance before switching directories. A public repository contains only reusable source, documentation and synthetic fixtures.

Earlier prototype runs, memory records and simulated accounting remain archived in the private database; their APIs and demo screens have been removed. Existing reflection tables are retained without a runtime. Simulated chat messages are excluded from active history and model context. Real conversations, provider settings and usage accounting are preserved.

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
