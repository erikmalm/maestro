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

Automatic restart at Windows sign-in is a separate integration task. A future Task Scheduler entry should run the launcher as the same user, with the same workspace and container arguments, after checking dependencies. A logon task is sufficient for a locked session; operation after sign-out or before login needs separately tested service/dependency setup. Microsoft's [Task Scheduler guidance](https://learn.microsoft.com/en-us/windows/win32/taskschd/about-the-task-scheduler) supports event-triggered startup without frequent polling.

The reflection worker remains planned in this increment. Saving its configuration does not start autonomous reflection. Once implemented, it belongs in the server lifecycle, uses durable queued jobs and works with the browser closed. Keeping Windows awake makes that server available; it does not itself enqueue work or keep an LLM loaded. See [MEMORY_AND_REFLECTION.md](MEMORY_AND_REFLECTION.md).
