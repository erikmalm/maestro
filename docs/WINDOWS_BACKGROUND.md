# Windows background operation

Locking Windows with **Win + L** keeps the signed-in session and its applications open. Maestro's server, the Podman machine, Ollama and the SSH tunnel can continue running while the display is locked or switched off. Closing the browser also leaves the server running. Signing out closes applications in that session. See Microsoft's [account access guidance](https://support.microsoft.com/en-us/accounts-billing/security/user-account-access-in-windows).

Sleep and hibernation suspend computation, including local inference. This machine uses conventional S3 sleep, where the processor is off; it does not support Modern Standby. See Microsoft's [sleeping states reference](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/system-sleeping-states).

## Power settings

In Windows 11, open **Settings > System > Power & battery > Screen, sleep, & hibernate timeouts**. Some desktop/build versions label this **Power** or **Screen and sleep**. Set **When plugged in, make my device sleep after** to **Never** for unattended processing. Leave the screen-off timeout enabled and continue locking the computer normally. If a separate plugged-in hibernation timeout is shown, set that to **Never** too. The battery timeout can remain conservative. Microsoft documents the separate [screen and sleep settings](https://support.microsoft.com/en-gb/windows/experience/power-battery/power-settings-in-windows-11).

Read-only inspection on the development computer, 2026-10-02, found:

| Setting | Verified value |
| --- | --- |
| Active plan | Power saver (`Energisparläge`) |
| Automatic sleep, plugged in | 45 minutes |
| Automatic sleep, on battery | 10 minutes |
| Automatic hibernation timeout | 0 seconds, disabled, for both power sources |
| Available sleep states | S3, hibernation and Fast Startup |
| Modern Standby | Unavailable |

These are policy values, not a guarantee that Windows will enter sleep at exactly that time: active power requests or other policy can affect it. No power settings were changed. Recheck after changing plans or Windows settings:

```powershell
powercfg /getactivescheme
powercfg /query scheme_current sub_sleep
powercfg /a
```

## Application lifetime and startup

The native launcher, `scripts/start.ps1 -NoBrowser`, starts a hidden server process. The container launcher starts Maestro detached and sets `--restart unless-stopped`. Neither launcher installs a Windows service or scheduled task. The container restart policy does not start the Windows Ollama process, the Podman machine or a missing Windows SSH tunnel.

### Native ports and reopening a server

Choose a native port through `scripts/start.ps1 -Port <number>` or the `MAESTRO_PORT` environment variable. The explicit argument takes precedence. With neither configured, a new server uses 8765. Port 0 asks Windows to assign an available loopback port; the server keeps the bound socket while starting, rather than probing a free port and releasing it.

```powershell
.\scripts\start.ps1 -Port 0 -NoBrowser
```

Use the URL printed when startup completes. The launcher sets `MAESTRO_PORT` to the actual listening port in its PowerShell session and saves `server.json` beside `server.pid` in the selected private data directory. This runtime record includes the actual port, launch/process identity and workspace/archive identities. It is separate from defaults and saved provider settings. Repeating startup without an explicit port, or with port 0, checks the record and health endpoint before reopening the matching instance. To use a different fixed port, stop that instance first; shutdown follows its verified process record rather than a port number. Keep the same data/archive environment and run the launcher from the checkout that owns it.

For Vite development, run `npm run dev` from `frontend` in that same PowerShell session after backend startup. Its API proxy reads `MAESTRO_PORT`, defaulting to 8765. A separate frontend shell must set the variable to the backend's printed port. Vite requires a concrete target between 1 and 65535; port 0 is valid for starting the backend, not for connecting to it. The production frontend makes relative API requests and needs no separately configured backend URL.

The Ollama server address is an independent saved connection in **Settings → Model provider**. `MAESTRO_OLLAMA_URL` supplies its default and the exact trusted container host endpoint; it does not replace an existing saved address. Changing Maestro's listening port does not move Ollama, create a tunnel or repair a stopped one. If the saved model endpoint is unavailable, restore that endpoint or deliberately reconnect to the intended Ollama server in Settings; startup does not silently substitute a different model service.

### Permanent source archive on Windows

Keep the same `MAESTRO_DATA_DIR` used by your existing workspace. The private database and source index remain there. Select a separate absolute public-source archive path through `MAESTRO_CONTEXT_ARCHIVE_DIR` in the server's environment. Maestro does not load `.env` files automatically.

For native startup, run from the checkout that owns the current server:

```powershell
.\scripts\stop.ps1
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = Join-Path $env:USERPROFILE 'OneDrive\Maestro-SearchArchive'
.\scripts\start.ps1 -NoBrowser
```

Adjust the path if your OneDrive root is elsewhere. This example sets the current PowerShell session only; start later sessions with the same data and archive paths. It does not import or scan the earlier `web-content` scaffold. Changing the archive path requires a server restart; rerunning the launcher against a different or unverified active archive configuration asks you to stop the old instance first.

In **Settings**, inspect the actual archive, private database and index locations, then enable source saving. The initial control preserves the existing capture policy and approved sources. A disabled archive retains its files. Invalid paths, unavailable storage and withheld configuration have separate explanations; configuration withheld by credential checks cannot be edited as an empty replacement.

The **Evidence** page reads the configured archive and index, including an honest empty state after new archive setup. Normal chat searches are not automatically captured yet; that acquisition wiring is a separate increment. Enabling storage or browsing Evidence makes no hosted search or model call. Restart verification uses synthetic captures in a separate disposable store, never demo records inserted into a personal archive.

The displayed archive bytes are the last recorded logical size. They do not establish local disk allocation, cloud quota or upload completion. This increment does not release local OneDrive copies or evict archived content. The current container launcher does not configure this Windows archive mount; use native setup for this first slice rather than treating a host path as an automatically mounted container directory.

After a Windows restart, start Ollama and the Podman machine, then run the container launcher with the same name, volume, image and port/tunnel arguments. See [CONTAINERS.md](CONTAINERS.md). Following sleep, check connection health and rerun the matching launcher if forwarding has been lost; the tunnel has no automatic reconnect supervisor.

Automatic restart at Windows sign-in is a separate integration task. A future Task Scheduler entry should run the launcher as the same user, with the same workspace and container arguments, after checking dependencies. A logon task is sufficient for a locked session; operation after sign-out or before login needs separately tested service/dependency setup. Microsoft's [Task Scheduler guidance](https://learn.microsoft.com/en-us/windows/win32/taskschd/about-the-task-scheduler) supports event-triggered startup without frequent polling.

The reflection worker runs in the server lifecycle when explicitly enabled in Settings, processes durable jobs from new eligible local chat evidence and works with the browser closed. Keeping Windows awake makes that server available; it does not itself enqueue work or keep an LLM loaded. Model calls occur only for eligible chat work or an enabled scheduled reflection pass, within the same daily limits. See [MEMORY_AND_REFLECTION.md](MEMORY_AND_REFLECTION.md).
