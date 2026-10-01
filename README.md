# Maestro

A local web interface for chat with your chosen model, usage tracking and persistent to-dos. The UI and broader architecture are retained; orchestration, background reflection and code self-improvement are paused until the basic flow is verified.

## Run locally

Windows, Python 3.11+, and Node.js 20.19+ or 22.12+:

```powershell
.\scripts\start.ps1 -NoBrowser
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Stop with `.\scripts\stop.ps1`. If script execution is blocked, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 -NoBrowser`.

## Connect and chat

1. Open **Settings**. Keep the OpenAI base URL, or enter a compatible HTTPS endpoint/local loopback server and choose its API protocol.
2. Enter the API key and **Connect and load models**. This saves the key and fetches the model list without generating a response. Default storage is Windows Credential Manager. Uncheck persistence to keep a new key only in server memory.
3. Select/type the desired model ID. The key authorizes your account/project; the model is chosen separately for each chat request. Models listed by an API are not necessarily chat-capable.
4. Selecting the exact `gpt-6-luna`, `gpt-6.1-sol` or `gpt-6-astra` ID fills standard OpenAI prices when the dated snapshot is at most 30 days old. For other models/providers or an older snapshot, enter verified input/output prices in USD per million tokens and enable **Use these prices for cost estimates**. Save the connection. Saved prices remain in effect until updated; zero prices are valid only for genuinely free inference.
5. Return to **Workspace**, send a message, and inspect the answer, token counts and estimated cost. Successful model-list access alone does not verify generation.

OpenAI uses the Responses API. The Chat Completions option supports compatible providers; compatibility and model access depend on that provider. Chat uses actual provider responses and has no canned fallback. Missing settings, unsupported models, exhausted limits and provider errors produce explicit errors. The first version waits for a complete response rather than streaming it.

Conversation history is sent with later messages. **New chat** clears history while keeping usage accounting. Existing demo messages are labelled and excluded from model context. To-dos can be added, completed/reopened and deleted without model calls; automatic agent execution is paused.

## Usage and limits

The header shows today's tokens and estimated USD. Each answer shows its model, input/output tokens and estimated cost. Usage details include day/month totals and reserved/uncertain charges.

Tokens come from provider responses. USD uses your configured prices and excludes cache discounts; it is not an invoice or a billing feed. Changing prices does not recalculate earlier requests. Spend and conservative token reservations are checked before dispatch. One chat request runs at a time, with a configured output cap and no automatic retries.

Failed requests with unknown usage retain a reservation and block further chat until their billed amount is verified in Settings. Known access/quota rejections release the reservation. Restart preserves accounting and marks interrupted requests uncertain.

## Private credentials and data

Keys never go into Git, the workspace database, browser storage, prompts, read API responses or logs. The password field clears on submission. Persisted keys are scoped to the exact provider endpoint in Windows Credential Manager. Session-only keys disappear when the server stops; previously saved credentials remain until removed. A server-environment OpenAI key can also be used and is controlled outside the UI.

Tasks, conversations, model settings and accounting live in `%LOCALAPPDATA%\Maestro\preview`, outside the checkout. An alternative `MAESTRO_DATA_DIR` must also resolve outside it. A public repository contains only reusable source, documentation and synthetic fixtures.

When you send chat, its history and your key go to the configured provider. Provider retention rules still apply. OpenAI requests use `store: false`; see [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data). The backend binds to loopback and requires local session/CSRF protection.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
cd frontend
npm run build
npm run test:ui
```

Browser tests exercise the complete setup/chat/history/usage flow against a synthetic HTTP provider in a temporary workspace. Windows credential tests use a synthetic key and remove it afterward. Automated checks require no personal credentials or paid calls. A real LLM check requires a locally configured user key, model and prices.

The broader [development plan](DEVELOPMENT_PLAN.md) and [architecture](docs/ARCHITECTURE.md) describe planned capabilities. They are not acceptance evidence for the current chat milestone.
