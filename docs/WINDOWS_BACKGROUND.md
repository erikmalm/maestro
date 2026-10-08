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

After a Windows restart, start Ollama and the Podman machine, then run the container launcher with the same name, volume, image and port/tunnel arguments. See [CONTAINERS.md](CONTAINERS.md). Following sleep, check connection health and rerun the matching launcher if forwarding has been lost; the tunnel has no automatic reconnect supervisor.

For native operation, select an existing public archive directory outside the checkout before starting the server:

```powershell
$contextArchive = 'C:\Users\malme\OneDrive\maestro\web-context'
New-Item -ItemType Directory -Force -Path $contextArchive | Out-Null
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = $contextArchive
.\scripts\start.ps1 -NoBrowser
```

Then enable **Settings → Saved web sources** and choose **Save eligible public search results** or **Only approved URLs**. The latter accepts public HTTPS path scopes or exact complete query URLs, one per line. New configurations select the broad policy with saving disabled; existing approved-source settings remain unchanged. Setting the environment variable alone does not enable capture or write the archive. The private workspace and query mappings stay in the existing `MAESTRO_DATA_DIR`; do not move them into OneDrive. If Maestro is already running, stop it with its original private data directory before changing the archive environment and restarting: the native launcher reuses a healthy server for the same workspace, and that process retains its original environment. Set the archive variable again after Windows restart or include it in your own startup environment. Container operation uses the explicit `-ContextArchivePath` option instead; preserve that option on each start.

Automatic restart at Windows sign-in is a separate integration task. A future Task Scheduler entry should run the launcher as the same user, with the same workspace and container arguments, after checking dependencies. A logon task is sufficient for a locked session; operation after sign-out or before login needs separately tested service/dependency setup. Microsoft's [Task Scheduler guidance](https://learn.microsoft.com/en-us/windows/win32/taskschd/about-the-task-scheduler) supports event-triggered startup without frequent polling.

The reflection worker runs in the server lifecycle when explicitly enabled in Settings, processes durable jobs from new eligible local chat evidence and works with the browser closed. Keeping Windows awake makes that server available; it does not itself enqueue work or keep an LLM loaded. Model calls occur only for eligible chat work or an enabled scheduled reflection pass, within the same daily limits. See [MEMORY_AND_REFLECTION.md](MEMORY_AND_REFLECTION.md).
