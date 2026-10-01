# Maestro

Maestro is a planned local AI assistant and agent coordinator with a web interface. It will help you discuss ideas, organize to-dos, delegate work to specialist agents, inspect their progress, and improve results through bounded review and refinement.

**Status: local UI preview.** Run and inspect the interface, save tasks and manual memories, try simulated chat/agent runs, and change budget limits locally. Usage figures are labelled demo data. Live AI, secure key setup, automatic recall, and integrations remain planned. See the [architecture draft](docs/ARCHITECTURE.md) and [development plan](DEVELOPMENT_PLAN.md).

## Run the local preview

Prerequisites: Windows, Python 3.11+, and Node.js 20.19+ or 22.12+. From this repository:

```powershell
.\scripts\start.ps1
```

The launcher installs missing dependencies, builds the interface, starts a hidden local server, and opens `http://127.0.0.1:8765`. Use `-NoBrowser` to start without opening a browser. Stop with:

```powershell
.\scripts\stop.ps1
```

If PowerShell blocks local scripts, use `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1` for this invocation; no permanent policy change is needed.

Preview state is saved under `%LOCALAPPDATA%\Maestro\preview`. Initial examples and usage are synthetic. Entered tasks, conversations, manual memories, and limits persist across restart. No API key is needed, and the preview makes no paid calls or GitHub submissions.

## What Maestro will do

| Area | Intended experience |
| --- | --- |
| Chat | Talk to the coordinator with relevant context from your private memory bank. |
| To-dos | Capture work, set priorities and completion criteria, and turn selected items into agent runs. |
| Orchestration | Assign specialist agents different prompts, models, tools, and limited permissions. |
| Run browser | Browse queued and active tasks, delegation trees, actions, review feedback, results, costs, and stop reasons. |
| GitHub collaboration | Turn authorized coding tasks into draft PRs in selected repositories, with reviewable changes and validation. |
| Refinement | Review a result against the task's criteria and iterate within enforced limits. |
| Memory | Keep useful preferences, project context, decisions, and lessons across sessions; inspect, correct, and forget them. |
| Integrations | Retrieve financial documentation through a locally configured MarketPulse connector, then add other scoped tools. |
| Settings | Configure a provider, API key, model, spending limits, agent profiles, memory policy, and integrations. |

## A typical task

1. Enter a to-do, for example: "Summarize the selected financial documents and identify follow-up questions."
2. Maestro saves it and any user-entered completion criteria locally.
3. Start the task, or let a previously configured automation rule start eligible tasks on entry. Maestro then creates a plan. The default is to save new to-dos without making paid API calls.
4. The coordinator assigns the work to a document analyst and requests an independent review when useful.
5. A reviewer checks coverage, evidence, and the requested format. Maestro refines incomplete work while the shared run budget permits it.
6. The web interface shows the outcome, document references, remaining questions, and why execution stopped.
7. Useful lessons become proposed local memory entries or prompt improvements under your chosen memory policy.

The coordinator remains responsible for the task and the final response. Specialist agents receive only the context and tools needed for their assignment. This manager pattern follows the [OpenAI orchestration guidance](https://developers.openai.com/api/docs/guides/agents/orchestration).

## Public code, private workspace

The GitHub repository is intended to be public. It contains reusable code, generic agent templates, documentation, and synthetic examples. Your personal workspace belongs outside the Git checkout.

The preview uses `%LOCALAPPDATA%\Maestro\preview` on Windows. `MAESTRO_DATA_DIR` can select another external location; the backend rejects locations inside the repository, including paths resolved through links. The full workspace will use `%LOCALAPPDATA%\Maestro`; other operating systems will use their standard per-user application-data location.

| Public repository | Private local storage |
| --- | --- |
| Application source and documentation | Chats, to-dos, projects, and user preferences |
| Generic prompts and synthetic test fixtures | Custom prompts and agent settings containing personal context |
| Blank configuration examples | API credentials in the operating system credential store |
| Dependency manifests and build configuration | Memory records, search indexes, and task history |
| Public integration interfaces | MarketPulse connection details and retrieved documents |
| | Generated results, attachments, logs, exports, and backups |

[.gitignore](.gitignore) already excludes common credentials and runtime files as a second layer of protection. Git exclusions do not protect files that were previously committed or explicitly force-added. Before publishing changes, inspect the staged files and scan for secrets. Personal content must also stay out of GitHub issues, pull requests, screenshots, and CI logs.

Local hosting means the interface and workspace run on your computer. When a cloud model is selected, the chosen conversation, memory excerpts, and document excerpts are sent to that provider for processing. The interface will expose those sharing settings. An OpenAI adapter will use locally managed conversation history and request `store: false`; this does not eliminate all provider-side retention. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

## API keys and providers

The preview Settings screen saves budget controls locally. API key entry is disabled until secure credential storage is implemented. Upcoming provider settings will let you add, replace, test, and remove a key. The backend will save credentials in the OS credential store and return only configured/missing status. Keys must stay out of frontend bundles, browser storage, URLs, prompts, and logs, in line with [OpenAI authentication guidance](https://developers.openai.com/api/reference/overview#authentication).

[.env.example](.env.example) contains blank fields for proposed development configuration. The preview reads selected server environment variables; it does not load `.env` files or use provider keys. Real keys must never be entered in that tracked file. Settings and the OS credential store are the intended everyday setup.

OpenAI is the first planned provider. Provider adapters will keep the task and memory system independent of a specific vendor. Additional cloud providers and a local model adapter can follow. Models will be selected explicitly in Settings rather than fixed to a changing "latest" alias.

## Controlled iteration and improvement

Each task will share one budget across the coordinator, workers, reviewers, retries, and memory updates. The backend will enforce limits on spend, model calls, tokens, review passes, delegation depth, concurrent agents, and elapsed time. Daily and monthly spending ceilings apply across runs. The run browser will show usage, reservations for in-flight requests, and stop reasons; an emergency stop will block new work.

Improvement means learning from feedback, preserving useful context, and proposing better prompts/workflows. Changes are versioned, evaluated, and reversible. Background work is opt-in and uses the same budgets. Authorized coding tasks may create isolated branches and submit draft PRs to allowed repositories after validation and secret scanning. You control merging; the running application does not rewrite itself automatically.

## Planned local setup

The preview launcher starts the backend and serves the built interface on loopback. The next milestone adds provider/model setup, secure credentials, and bounded live chat. Tasks and history already work without an API key; live AI execution will require a configured provider.

The preview uses Python/FastAPI, React/TypeScript, and SQLite local persistence, with pinned direct dependencies and a frontend lockfile. The full runner adds normalized records and scoped search. One installation supports one local user.

## Development

Start with [the architecture draft](docs/ARCHITECTURE.md) and [development plan](DEVELOPMENT_PLAN.md). The visual preview precedes the full provider/agent runtime. For frontend development, start the backend and run `npm run dev` in `frontend`; it proxies `/api` to the local server.

Checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
cd frontend
npm run build
npm run test:ui
```

For the first UI test run, install the test browser with `npx playwright install chromium`. Browser tests use a separate temporary private workspace and synthetic fixtures.

Do not place real user content or credentials in this repository while developing. Use synthetic fixtures and a mock provider for automated checks.
