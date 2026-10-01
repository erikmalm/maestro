# Maestro

Maestro is a planned local AI assistant and agent coordinator with a web interface. It will help you discuss ideas, organize to-dos, delegate work to specialist agents, inspect their progress, and improve results through bounded review and refinement.

**Status: planning.** This repository currently contains documentation and Git exclusions. There is no runnable application yet. The build sequence and acceptance criteria are in [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

## What Maestro will do

| Area | Intended experience |
| --- | --- |
| Chat | Talk to the coordinator with relevant context from your private memory bank. |
| To-dos | Capture work, set priorities and completion criteria, and turn selected items into agent runs. |
| Orchestration | Assign specialist agents different prompts, models, tools, and limited permissions. |
| Run browser | Browse queued and active tasks, delegation trees, actions, review feedback, results, costs, and stop reasons. |
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

The planned default on Windows is `%LOCALAPPDATA%\Maestro`. Other operating systems will use their standard per-user application-data location. `MAESTRO_DATA_DIR` will allow an external location to be selected; startup must reject locations inside the repository, including paths resolved through links.

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

The planned Settings screen will let you add, replace, test, and remove a provider key. The backend will save credentials in the operating system credential store and return only a configured/missing status. Keys must stay out of frontend bundles, browser storage, URLs, task prompts, and logs, in line with [OpenAI authentication guidance](https://developers.openai.com/api/reference/overview#authentication).

[.env.example](.env.example) contains blank fields for the proposed development configuration. It is a specification for the upcoming application; no configuration loader exists yet. Real keys must never be entered in that tracked file. A session environment variable can be used for development; the settings screen and OS credential store are the intended everyday setup.

OpenAI is the first planned provider. Provider adapters will keep the task and memory system independent of a specific vendor. Additional cloud providers and a local model adapter can follow. Models will be selected explicitly in Settings rather than fixed to a changing "latest" alias.

## Controlled iteration and improvement

Each task will share one budget across the coordinator, workers, reviewers, retries, and memory updates. The backend will enforce limits on spend, model calls, tokens, review passes, delegation depth, concurrent agents, and elapsed time. Daily and monthly spending ceilings apply across runs. The run browser will show usage, reservations for in-flight requests, and stop reasons; an emergency stop will block new work.

Improvement means learning from your feedback, preserving useful context, and proposing better prompts or workflows. Prompt changes will be versioned, evaluated, and reversible. Background learning and scheduling will be opt-in and use the same budgets. Changes to application code will go through the normal development and review process.

## Planned local setup

The first runnable milestone will provide a Windows launcher that starts the backend, serves the web interface on a loopback address, opens the browser, and reports any missing prerequisites. The initial setup screen will configure the private storage location, provider/model, credential, and limits. To-dos and stored history will remain usable without an API key; AI execution will require a configured provider.

The proposed implementation is a Python/FastAPI backend, a React/TypeScript web interface, and SQLite for local state and search. One installation will support a single local user initially. Exact dependency versions and executable startup instructions will be added when that milestone exists.

## Development

Start with [the development plan](DEVELOPMENT_PLAN.md). It defines the architecture, task lifecycle, privacy boundaries, default budgets, milestones, and verification requirements. Documentation is the only completed milestone so far.

Do not place real user content or credentials in this repository while developing. Use synthetic fixtures and a mock provider for automated checks.
