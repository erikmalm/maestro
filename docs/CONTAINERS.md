# Podman deployment

Maestro runs as one Linux amd64 container: FastAPI serves the built React UI, and a named volume holds the private SQLite workspace at `/data`. Ollama stays on the host, with its own model storage and RAM usage. No Compose service or second Maestro worker is needed.

## Build and lifecycle

Run these commands from the checkout in PowerShell, with the Podman machine running:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action build
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action stop
```

The UI is at `http://127.0.0.1:8765`. To avoid another local Maestro instance, pass `-Port 8770`; use the same arguments whenever starting that container. `-Name maestro-test` gives it a separate `maestro-test-data` volume. `-Volume` selects an explicitly named volume, and `-Image` selects a previously built local image. Pass the same name and volume on `status` and `stop` too.

The launcher checks its ownership label, volume, image, configuration and health response before reusing a container. It restarts a matching stopped container, keeps its volume, and refuses conflicting names or configuration. It never replaces or removes a container. Changing the image or startup options requires an explicit upgrade:

```powershell
$previousImage = podman inspect --type container --format '{{.Image}}' maestro
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the running container image.' }
podman tag $previousImage localhost/maestro:previous
if ($LASTEXITCODE -ne 0) { throw 'Could not preserve the current image for rollback.' }
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action stop
if ($LASTEXITCODE -ne 0) { throw 'Stop failed; leave the container untouched.' }
podman rm maestro
if ($LASTEXITCODE -ne 0) { throw 'Could not remove the stopped Maestro container.' }
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action build
if ($LASTEXITCODE -ne 0) { throw 'Build failed; start the previous image to roll back.' }
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start
```

Keep `maestro-data` when upgrading. `podman rm` removes the stopped container, while the named volume remains. If readiness fails, inspect `podman logs maestro` and `podman inspect maestro`; the launcher preserves the container and its workspace for diagnosis.

Take a workspace snapshot before upgrading. To roll back a started replacement, stop it and remove its container with the guarded commands above, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start -Image localhost/maestro:previous
```

Include the original name, volume, ports, tunnel and secret options. If a release changes the workspace format, restore its pre-upgrade snapshot before starting the previous image.

The runtime uses UID/GID 1000, one worker, a read-only root filesystem, no added Linux capabilities, a private data volume, and loopback-only access. Defaults are two CPUs, 1 GiB RAM, no additional swap, 64 processes, a 64 MiB temporary filesystem at `/tmp`, and a 10 MB container log. Override CPU/RAM with `-Cpus 4 -Memory 2g`. These limits cover Maestro, not the host's Ollama models. The [Podman run reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html) explains volume ownership, resource limits, logs and secrets.

Check effective WSL memory with `podman machine ssh free -h` and, for NVIDIA hardware, host VRAM with `nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv`. Changing models does not start another Maestro container. Ollama may keep several models resident; set **Keep model loaded (minutes)** to 0 to release each model after its reply when memory matters. Model suitability and switching latency still need benchmarking before automatic specialist selection.

The build uses registry-pinned Node/Python bases, `npm ci`, and the repository's pinned Python requirements. It uses Docker image format so Podman retains the image health check. Its allowlist excludes private files, Git history, local dependencies and build output from the build context. Update base digests deliberately when upgrading the Linux amd64 images.

## Saved public web sources

To make a public source archive available to Maestro, first create a dedicated directory outside the checkout, then supply it explicitly on `start`:

```powershell
$contextArchive = 'C:\Users\malme\OneDrive\maestro\web-context'
New-Item -ItemType Directory -Force -Path $contextArchive | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start -ContextArchivePath $contextArchive
```

Include your existing name, volume, image, port, tunnel and secret options. This option currently requires the default running Windows WSL Podman machine and an active connection to that machine. The launcher accepts an existing local drive directory, rejects paths inside the checkout and symbolic links/junctions, and verifies the corresponding `/mnt/<drive>/...` directory in the selected machine. UNC paths, remote servers and non-WSL machines are unsupported. It does not create the archive directory or change its permissions.

The directory is mounted at `/archive` with `MAESTRO_CONTEXT_ARCHIVE_DIR=/archive`. It has no `:U` or relabel option; UID 1000 must already have write access. The private database, query mappings and disposable index remain in `/data`. The mount only makes the archive available. In **Settings → Saved web sources**, enable saving and choose **Save eligible public search results** or **Only approved URLs**. New configurations default to the former with saving disabled; existing approved-source policy and limits persist through upgrades. The approved-only choice accepts public HTTPS path scopes or exact complete query URLs. Disabled startup does not initialize or write the archive.

New configurations use a 24-hour reuse window, a 10 GiB archive allowance and a limit of 200,000 files and directories. Adjust limits in Settings, which reports measured archive usage and last-capture sizes. Eligible HTTP/HTTPS excerpts can be saved under the broad policy without fetching their origin sites. Repeated retrievals keep immutable captures with deduplicated objects; this increment has no full-page capture or automatic pruning.

Adding, changing or removing `-ContextArchivePath` changes the launch fingerprint. For an existing container, follow the explicit stop/remove/start procedure above, keep its private volume, and pass the same archive path on subsequent starts. Do not start another writer against the same archive. Archive ownership is separate from private workspace ownership; a stale writer claim requires checking that all writers are stopped before manual cleanup.

OneDrive synchronizes the public source objects and metadata, not the live database. Keep required files on this device for offline use. Unavailable files, incomplete synchronization or access failures must be reported as unavailable evidence. Do not recursively change ownership on the OneDrive tree. See [search context storage](SEARCH_CONTEXT_STORAGE.md) for source eligibility, date provenance and reuse behavior, and the [Podman volume reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html#volume-v-source-volume-host-dir-container-dir-options) for mount semantics.

An isolated synthetic fixture under the supplied OneDrive root passed write, flush, rename and read checks at UID 1000 on 2026-10-04, using the reviewed image, disabled networking and these mount options. The fixture and temporary container were removed. This verifies local filesystem access; offline hydration and cloud synchronization behavior need separate checks.

## Reach host Ollama

The default container endpoint is `http://host.containers.internal:11434`. Check it before configuring chat:

```powershell
podman exec maestro python -c "import urllib.request; print(urllib.request.urlopen('http://host.containers.internal:11434/api/tags', timeout=3).status)"
ollama list
```

Both commands describe the host's installed models; Maestro discovers them using Ollama's `/api/tags` endpoint. Select the chat default and, for orchestrator routing, its installed model in Settings. **Chat routing** chooses the local default; the model picker in the chat composer overrides it without another provider or container. After pulling or removing models through Ollama, use **Connect and load models** in Settings to update the list.

`host.containers.internal` must reach the host's Ollama listener. On Windows, a WSL Podman machine without user-mode networking may not reach an Ollama server bound only to `127.0.0.1`; its published UI port may also be unreachable. Use the optional SSH tunnel to connect both directions without restarting the machine or changing Ollama's listener:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start -Name maestro-local -Tunnel -Port 8790 -OllamaTunnelPort 11435
podman exec maestro-local python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:11435/api/tags', timeout=3).status)"
```

This mode binds Maestro to the Podman machine's loopback at port 8790, forwards the Windows loopback UI port through OpenSSH, and forwards machine loopback 11435 to Windows Ollama's loopback 11434. It requires Windows OpenSSH, the running default Podman machine's SSH identity, and an active Podman connection to that machine. It uses the host network namespace so it can reach the SSH listener; the application and both forwarded ports bind only `127.0.0.1`. Use a different `-OllamaTunnelPort` for each simultaneous tunneled instance.

Keep `-Tunnel` and the same port options on subsequent starts. The launcher safely reuses its recorded SSH process and closes it on `stop`; process ID, start time, executable and full command must still match. Its record, diagnostic log and SSH host keys stay under `%LOCALAPPDATA%\Maestro\container-tunnels`. The first loopback machine host key is accepted and remembered; a changed key is rejected. After Windows restarts, run `start` to restore forwarding. If another process reused a recorded PID, the launcher leaves it untouched and asks you to inspect the private record.

Alternatively, check the [Podman machine networking documentation](https://docs.podman.io/en/latest/markdown/podman-machine-set.1.html) and plan a user-mode networking change around other containers. Recheck the container-side probe afterward. Do not expose Ollama broadly merely to bypass this check. OpenSSH's [port forwarding documentation](https://man.openbsd.org/ssh.1) explains the loopback bindings used by the tunnel.

For a separately verified bridge endpoint, use `-OllamaUrl http://host.containers.internal:11434`. Tunnel mode sets its trusted `MAESTRO_OLLAMA_URL` to the reverse tunnel's loopback port automatically; the browser cannot redirect the container to arbitrary internal services. Bridge-mode container loopback refers to the container, not the Windows host.

## Provider and search credentials

Windows Credential Manager entries do not travel into Linux containers. Session keys entered in the UI last only until the container process restarts. For persistent provider/search access, create Podman secrets from private files outside the checkout; secret values belong in neither command arguments nor environment variables:

```powershell
$providerKeyPath = Join-Path $env:LOCALAPPDATA 'Maestro\provider-key.txt'
$searchKeyPath = Join-Path $env:LOCALAPPDATA 'Maestro\search-key.txt'
podman secret create maestro-provider-key $providerKeyPath
podman secret create maestro-search-key $searchKeyPath
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start -ProviderSecret maestro-provider-key -ApiKeyUrl https://api.openai.com/v1 -SearchSecret maestro-search-key
```

Create only the secrets you need. The script mounts them read-only at `/run/secrets/maestro-provider-key` and `/run/secrets/maestro-search-key`, mode `0400`, owned by UID 1000. Maestro reads `MAESTRO_API_KEY_FILE` and `MAESTRO_SEARCH_KEY_FILE`; `MAESTRO_API_KEY_URL` binds the provider key to one exact API endpoint. Never pass a remote key to Ollama. Podman inspection exposes secret names and paths, not these values. Protect the private input files and the Podman machine; Podman's default secret storage is not a promise of encrypted storage.

To rotate a secret, stop and remove only the owned Maestro container, remove/recreate the named secret from its private file, then start with the same secret arguments. Keep the data volume. Mounted secrets are managed outside the UI and cannot be removed through the UI.

## Workspace migration, backup and restore

Keep backups outside the checkout and protect them like the live workspace: chats, tasks and accounting are private. Migrate the SQLite workspace, not Windows credential vault files, session keys, PID records or logs. Reconfigure mounted secrets separately.

Stop the host launcher before switching that workspace to Podman. Take a consistent SQLite snapshot rather than copying a live database:

```powershell
$dataDirectory = Join-Path $env:LOCALAPPDATA 'Maestro\preview'
$backupDirectory = Join-Path $env:LOCALAPPDATA 'Maestro\backups\container-migration'
New-Item -ItemType Directory -Path $backupDirectory -ErrorAction Stop
.\.venv\Scripts\python.exe -m backend.storage "$dataDirectory\workspace.sqlite3" "$backupDirectory\workspace.sqlite3"
if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed; do not migrate this workspace.' }
```

The snapshot tool reads the source through SQLite and refuses an existing destination. Import into a new private volume before its first Maestro startup:

```powershell
podman volume create --label io.maestro.managed=container.ps1 --uid 1000 --gid 1000 maestro-data
if ($LASTEXITCODE -ne 0) { throw 'Choose a new volume; do not overwrite an existing workspace.' }
podman create --name maestro-import --volume maestro-data:/data:U --entrypoint python localhost/maestro:dev -c "from pathlib import Path; Path('/data').chmod(0o700); Path('/data/workspace.sqlite3').chmod(0o600)"
if ($LASTEXITCODE -ne 0) { throw 'Could not create the temporary import container.' }
podman cp "$backupDirectory\workspace.sqlite3" maestro-import:/data/workspace.sqlite3
if ($LASTEXITCODE -ne 0) { throw 'Could not import the snapshot.' }
podman start --attach maestro-import
if ($LASTEXITCODE -ne 0) { throw 'Could not set private workspace permissions.' }
podman rm maestro-import
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\container.ps1 -Action start
```

The temporary import container only sets permissions, then exits. [Podman cp](https://docs.podman.io/en/latest/markdown/podman-cp.1.html) assigns imported files to the container's UID/GID 1000 by default; its short import command ensures `/data` is `0700` and the database is `0600`. The normal launcher then uses the prepared, labeled volume.

Migration preserves the saved provider URL. In Settings, change a native Ollama URL such as `http://127.0.0.1:11434` to the container's host endpoint (`http://host.containers.internal:11434`, or `http://127.0.0.1:11435` with the tunnel above), then reconnect and verify the installed models before chatting.

The SQLite workspace snapshot preserves private query mappings and answer references but does not include the disposable `/data/context/index.sqlite3` or public archive files. Preserve and mount the original public archive separately. After migration or restore, enable its saved policy and use **Settings → Saved web sources → Rebuild source index** to restore lookup and historical saved-source views. Reconstruction resumes in bounded batches, keeps an existing index usable until completion, and can pause or continue. Missing or changed public files remain unavailable; rebuilding cannot recover their original bytes or invent lost query mappings.

For an online container backup:

```powershell
$backupPath = Join-Path $backupDirectory 'workspace-backup.sqlite3'
if (Test-Path -LiteralPath $backupPath) { throw 'Choose a new backup filename.' }
podman exec maestro python -m backend.storage /data/workspace.sqlite3 /tmp/workspace-backup.sqlite3
if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed; leave any previous snapshot untouched.' }
podman cp maestro:/tmp/workspace-backup.sqlite3 $backupPath
if ($LASTEXITCODE -ne 0) { throw 'Export failed; retain the snapshot inside the container.' }
podman exec maestro python -c "from pathlib import Path; Path('/tmp/workspace-backup.sqlite3').unlink()"
```

Choose a new private destination for each backup; `/tmp` is ephemeral and its snapshot disappears on container recreation. For snapshots larger than 64 MiB, choose a new temporary filename under `/data` instead, then remove it after exporting. Restore into a stopped container, preserving the current database as a separate backup first. Start Maestro and verify tasks/chats and provider settings before deleting an old workspace or backup. The application takes a workspace lock, so another Maestro process must not write the same private data directory concurrently.

## Verify an image

```powershell
.\.venv\Scripts\python.exe tests/verify_container.py localhost/maestro:dev --tunnel
```

Omit `--tunnel` where normal Podman port forwarding works. This opt-in check creates unique temporary images, containers, volumes and synthetic secrets, then removes them. It verifies build-context exclusions, the served UI, local access checks, resource limits, separate model choices, workspace ownership, restart/recreation persistence, backup/restore and mounted-key protection without paid calls or personal data. It uses a synthetic Ollama service; verify the real host connection separately with the probe above and a short local chat.

Verify the archive separately against an existing parent directory:

```powershell
.\.venv\Scripts\python.exe tests/verify_context_container.py localhost/maestro:dev --archive-root 'C:\Users\malme\OneDrive\maestro'
```

Omit `--archive-root` to use a temporary directory. This check creates a unique synthetic archive fixture and private volumes, runs with networking disabled at UID 1000 and a read-only root, and removes its own resources afterward. It verifies eligible-public capture, repeated observations/object deduplication, capture-size reports, exact-query and keyword reuse, domain/UTC date filters, conservative version selection, content/metadata hashes, FTS5, restart/recreation persistence, independent second-writer rejection, rebuild and deletion without resurrecting old evidence. It does not change the running workspace or configure live source scopes. It verifies locally available storage; cloud synchronization and offline hydration remain separate checks.
