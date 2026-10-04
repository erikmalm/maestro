"""Opt-in: python tests/verify_context_container.py <image> [--archive-root <dir>].

Uses only UUID-owned fixtures, volumes and offline containers. No keys, model,
API listener or preview deployment is involved. Default archive root is temporary.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid


LABEL = "io.maestro.context-verifier"
PROGRAM = r'''
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from backend.context_store import ContextStore, DEFAULT

assert os.getuid() == 1000
with closing(sqlite3.connect(':memory:')) as db:
    db.execute('CREATE VIRTUAL TABLE synthetic_fts USING fts5(content)')
store = ContextStore('/data/workspace.sqlite3')
parameterized_url = 'https://docs.example.org/public/forecast?latitude=59.3&longitude=18.0&date=2026-10-05'
config = {**DEFAULT, 'enabled': True, 'capture_policy': 'approved_sources',
          'public_sources': ['https://docs.example.org/public/', parameterized_url]}
query = 'synthetic-private-query-not-for-export'
parameterized_query = 'synthetic-private-parameterized-query-not-for-export'
source = {'title': 'Synthetic public source', 'url': 'https://docs.example.org/public/source',
          'content': 'Synthetic historical evidence for offline retrieval.',
          'completeness': {'maestro_truncated': False, 'full_page': False}}
parameterized_source = {**source, 'title': 'Synthetic parameterized public source', 'url': parameterized_url,
                        'content': 'Synthetic forecast for 2026-10-05 retained as an exact public URL.'}
fixture = Path('/data/context-verifier.json')
parameterized_fixture = Path('/data/parameterized-context-verifier.json')
step = sys.argv[1]

if step == 'contender':
    try:
        store.configure(config)
    except ValueError as error:
        assert 'writer' in str(error) or 'owns' in str(error), str(error)
        print(json.dumps({'second_writer_blocked': True}))
    else:
        raise AssertionError('Second private workspace acquired the held archive')
elif step == 'hold':
    with store.archive_owner():
        print('READY', flush=True)
        deadline = time.monotonic() + 45
        while not Path('/data/release-context-verifier').exists():
            assert time.monotonic() < deadline, 'Host did not release the synthetic owner'
            time.sleep(0.1)
    print('RELEASED', flush=True)
else:
    with store.archive_owner():
        if step == 'bootstrap':
            if fixture.exists():
                original = json.loads(fixture.read_text())
                hit = store.lookup(query, 3)
                assert hit and hit['from_cache']
                saved = hit['sources'][0]
                assert all(saved[key] == original[key] for key in ('capture_id', 'content_hash', 'manifest_hash', 'retrieved_at'))
            else:
                store.configure(config)
                saved = store.capture(query, store.now().isoformat(), [source], 3)['sources'][0]
                assert saved['archive_status'] == 'saved'
                fixture.write_text(json.dumps(saved))
            if parameterized_fixture.exists():
                original_parameterized = json.loads(parameterized_fixture.read_text())
                parameterized = store.lookup(parameterized_query, 3)['sources'][0]
                assert all(parameterized[key] == original_parameterized[key]
                           for key in ('capture_id', 'content_hash', 'manifest_hash', 'url', 'retrieved_at'))
            else:
                parameterized = store.capture(parameterized_query, store.now().isoformat(), [parameterized_source], 3)['sources'][0]
                assert parameterized['archive_status'] == 'saved'
                parameterized_fixture.write_text(json.dumps(parameterized))
            exact_view = store.get_capture(parameterized['capture_id'], expected_hash=parameterized['content_hash'],
                                           expected_manifest_hash=parameterized['manifest_hash'])
            assert exact_view['url'] == parameterized_url and exact_view['content'] == parameterized_source['content']
            wrong_date = store.capture('synthetic-private-unapproved-parameter-query', store.now().isoformat(),
                                      [{**parameterized_source, 'url': parameterized_url.replace('2026-10-05', '2026-10-06')}], 3)
            assert wrong_date['sources'][0]['archive_status'] == 'skipped'
            assert store.search('parameterized')['sources'][0]['capture_id'] == parameterized['capture_id']
            view = store.get_capture(saved['capture_id'], expected_hash=saved['content_hash'], expected_manifest_hash=saved['manifest_hash'])
            assert view['content'] == source['content']
            # A new keyword query must retrieve the original observation read-only.
            keyword = 'historical evidence'
            assert store.lookup(keyword, 3) is None
            private_before = store.database.read_bytes(), store.index.read_bytes()
            archive_before = {str(path.relative_to('/archive')): path.read_bytes()
                              for path in Path('/archive').rglob('*') if path.is_file()}
            hit = store.search(keyword, domain='DoCs.Example.Org')
            assert hit['from_cache'] and hit['retrieval'] == 'keyword' and not hit['stale']
            assert len(hit['sources']) == 1
            assert all(hit['sources'][0][key] == saved[key]
                       for key in ('capture_id', 'content_hash', 'manifest_hash', 'retrieved_at'))
            assert not store.search(keyword, domain='other.example.org')['sources']
            assert not store.search(keyword, domain='sub.docs.example.org')['sources']
            original_day = saved['retrieved_at'][:10]
            assert store.search(keyword, retrieved_from=original_day, retrieved_to=original_day)['sources']
            previous_day = (datetime.fromisoformat(saved['retrieved_at']) - timedelta(days=1)).date().isoformat()
            assert not store.search(keyword, retrieved_to=previous_day)['sources']
            assert private_before == (store.database.read_bytes(), store.index.read_bytes())
            assert archive_before == {str(path.relative_to('/archive')): path.read_bytes()
                                      for path in Path('/archive').rglob('*') if path.is_file()}
            for args in ({'expected_hash': '0' * 64}, {'expected_manifest_hash': '0' * 64}):
                try:
                    store.get_capture(saved['capture_id'], **args)
                except KeyError:
                    pass
                else:
                    raise AssertionError('A wrong historical hash was accepted')
            with closing(sqlite3.connect('/data/workspace.sqlite3')) as db:
                assert {row[0] for row in db.execute('SELECT query FROM context_queries')} == {query, parameterized_query}
            exported = '\n'.join(path.read_text(encoding='utf-8') for path in Path('/archive').rglob('*') if path.is_file())
            assert query not in exported and parameterized_query not in exported and 'chat_id' not in exported and 'context-verifier.json' not in exported
            print(json.dumps({'uid': os.getuid(), 'fts5': True, 'capture_id': saved['capture_id'],
                              'content_hash': saved['content_hash'], 'manifest_hash': saved['manifest_hash'], 'retrieved_at': saved['retrieved_at'],
                              'parameterized_capture_id': parameterized['capture_id'], 'parameterized_manifest_hash': parameterized['manifest_hash']}))
        elif step == 'rebuild-delete':
            saved = json.loads(fixture.read_text())
            parameterized = json.loads(parameterized_fixture.read_text())
            store.index.unlink()
            assert store.rebuild(limit=100)['indexed_count'] == 2
            assert store.get_capture(saved['capture_id'], expected_hash=saved['content_hash'], expected_manifest_hash=saved['manifest_hash'])['content'] == source['content']
            exact_view = store.get_capture(parameterized['capture_id'], expected_hash=parameterized['content_hash'],
                                           expected_manifest_hash=parameterized['manifest_hash'])
            assert exact_view['url'] == parameterized_url and exact_view['content'] == parameterized_source['content']
            assert store.search('parameterized')['sources'][0]['capture_id'] == parameterized['capture_id']
            with closing(sqlite3.connect(store.index)) as db:
                assert db.execute("SELECT COUNT(*) FROM captures_fts WHERE captures_fts MATCH 'historical'").fetchone()[0] == 1
            # Date filters select historical versions; ordinary keyword lookup must
            # suppress an older matching version when a newer eligible one differs.
            legacy_at = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=2)
            updated_at = legacy_at + timedelta(days=1)
            versioned = {**source, 'title': 'Versioned synthetic source',
                         'url': 'https://docs.example.org/public/versioned'}
            old_query = 'synthetic-private-old-version-query'
            new_query = 'synthetic-private-new-version-query'
            older = store.capture(old_query, legacy_at.isoformat(),
                                  [{**versioned, 'content': 'Heliotrope evidence from the older version.'}], 3)['sources'][0]
            newer = store.capture(new_query, updated_at.isoformat(),
                                  [{**versioned, 'content': 'Current replacement without the prior keyword.'}], 3)['sources'][0]
            assert older['archive_status'] == newer['archive_status'] == 'saved'
            assert not store.search('heliotrope', allow_stale=True)['sources']
            versions = store.search('versioned', allow_stale=True)['sources']
            assert len(versions) == 1 and versions[0]['capture_id'] == newer['capture_id']
            historical = store.search('heliotrope', retrieved_from=legacy_at.date().isoformat(),
                                      retrieved_to=legacy_at.date().isoformat(), allow_stale=True)
            assert historical['stale'] and historical['sources'][0]['stale']
            assert historical['sources'][0]['capture_id'] == older['capture_id']
            assert historical['sources'][0]['retrieved_at'] == older['retrieved_at']
            assert store.rebuild(limit=100)['indexed_count'] == 4
            assert store.search('versioned', allow_stale=True)['sources'][0]['capture_id'] == newer['capture_id']
            assert store.get_capture(saved['capture_id'], expected_hash=saved['content_hash'], expected_manifest_hash=saved['manifest_hash'])['content'] == source['content']
            exported = '\n'.join(path.read_text(encoding='utf-8') for path in Path('/archive').rglob('*') if path.is_file())
            assert all(private_query not in exported for private_query in (query, parameterized_query, old_query, new_query))
            store.delete_capture(saved['capture_id'])
            assert store.lookup(query, 3) is None
            assert not store.search('historical evidence')['sources']
            try:
                store.get_capture(saved['capture_id'], expected_hash=saved['content_hash'], expected_manifest_hash=saved['manifest_hash'])
            except KeyError:
                pass
            else:
                raise AssertionError('Deleted source remains visible')
            store.delete_capture(older['capture_id'])
            assert not store.search('heliotrope', retrieved_to=legacy_at.date().isoformat(), allow_stale=True)['sources']
            store.delete_capture(newer['capture_id'])
            store.delete_capture(parameterized['capture_id'])
            assert store.lookup(parameterized_query, 3) is None
            assert not store.search('parameterized')['sources']
            assert store.rebuild(limit=100)['indexed_count'] == 0
            assert not store.search('versioned', allow_stale=True)['sources']
            # The broad policy saves new public result links without changing
            # their scheme/query identity or reusing an old capture timestamp.
            store.configure({**config, 'capture_policy': 'all_public', 'public_sources': []})
            broad_query = 'synthetic-private-broad-query-not-for-export'
            broad_urls = ['http://weather.example.org/forecast?date=2026-10-05',
                          'https://other.example.org/forecast?date=2026-10-06']
            broad_sources = [{**source, 'url': url, 'content': 'Synthetic broad public weather evidence.'}
                             for url in broad_urls]
            first_at = store.now().isoformat()
            broad = store.capture(broad_query, first_at, broad_sources, 3)['sources']
            assert all(item['archive_status'] == 'saved' for item in broad)
            assert [item['url'] for item in broad] == broad_urls
            first_size = store.status()['last_capture']
            assert first_size['sources_received'] == first_size['sources_saved'] == 2
            assert first_size['excerpt_bytes'] == 2 * len(broad_sources[0]['content'].encode())
            assert first_size['object_bytes'] == len(broad_sources[0]['content'].encode())
            assert first_size['new_bytes'] == first_size['object_bytes'] + first_size['manifest_bytes']
            repeated_at = store.now().isoformat()
            repeated = store.capture(broad_query, repeated_at, [broad_sources[0]], 3)['sources'][0]
            repeat_size = store.status()['last_capture']
            assert repeated['capture_id'] != broad[0]['capture_id']
            assert repeated['retrieved_at'] == repeated_at
            assert repeat_size['object_bytes'] == 0 and repeat_size['new_bytes'] == repeat_size['manifest_bytes'] > 0
            assert store.rebuild(limit=100)['indexed_count'] == 3
            for item in [*broad, repeated]:
                assert store.get_capture(item['capture_id'], item['content_hash'], item['manifest_hash']) == item
            assert {item['url'] for item in store.search('broad weather')['sources']} == set(broad_urls)
            unsafe = store.capture('synthetic-private-rejected-query', store.now().isoformat(),
                                   [{**source, 'url': 'https://weather.example.org/forecast?access_token=synthetic'}], 3)
            assert unsafe['sources'][0]['archive_status'] == 'skipped'
            assert store.status()['last_capture']['sources_saved'] == 0
            exported = '\n'.join(path.read_text(encoding='utf-8') for path in Path('/archive').rglob('*') if path.is_file())
            assert broad_query not in exported and 'synthetic-private-rejected-query' not in exported
            print(json.dumps({'keyword_retrieval': True, 'filters': True, 'url_versions': True,
                              'lookup_read_only': True, 'exact_query_url': True, 'rebuild': True, 'delete': True,
                              'resurrection_blocked': True, 'all_public': True, 'capture_sizes': True, 'repeated_captures': True}))
        else:
            raise AssertionError('Unknown verifier step')
'''


def podman(*arguments, check=True):
    result = subprocess.run(["podman", *arguments], capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError(f"Podman {arguments[0]} failed: {result.stderr[-1200:]}")
    return result


def remove_fixture(fixture, root, token):
    resolved = fixture.resolve()
    if resolved.parent != root or resolved.name != "context-verify-" + token or fixture.is_symlink() or getattr(fixture, "is_junction", lambda: False)():
        raise RuntimeError("Refusing archive cleanup outside this UUID fixture.")
    def clear_readonly(operation, path, exc_info):
        error = exc_info[1]
        candidate = Path(path)
        checked = candidate.resolve()
        if (os.name != "nt" or not isinstance(error, PermissionError)
                or checked != resolved and resolved not in checked.parents
                or candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)()):
            raise error
        # OneDrive marks directories ReadOnly; change only this verified fixture.
        candidate.chmod(stat.S_IREAD | stat.S_IWRITE)
        operation(path)
    shutil.rmtree(resolved, onerror=clear_readonly)


def mount_source(path):
    if os.name != "nt":
        return str(path)
    machines = json.loads(podman("machine", "inspect").stdout)
    machine = machines[0]
    if machine["State"] != "running" or Path(machine["ConfigDir"]["Path"]).name != "wsl":
        raise RuntimeError("This Windows check requires a running local WSL Podman machine.")
    connections = json.loads(podman("system", "connection", "list", "--format", "json").stdout)
    configured = os.environ.get("CONTAINER_CONNECTION")
    current = next((item for item in connections if item["Name"] == configured), None) if configured else next(item for item in connections if item["Default"])
    endpoint = urlsplit(os.environ.get("CONTAINER_HOST") or current["URI"])
    if endpoint.scheme != "ssh" or endpoint.hostname not in ("127.0.0.1", "localhost", "::1") or endpoint.port != machine["SSHConfig"]["Port"]:
        raise RuntimeError("Archive mounting requires the active connection to use the inspected local machine.")
    if len(path.drive) != 2 or path.drive[1] != ":":
        raise RuntimeError("Choose an archive root on a local Windows drive.")
    mapped = "/mnt/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])
    canonical = podman("machine", "ssh", machine["Name"], "readlink -e -- " + shlex.quote(mapped)).stdout.strip()
    if canonical != mapped:
        raise RuntimeError("The archive fixture resolves differently in the selected machine.")
    return mapped


def verify(image, archive_root):
    token = uuid.uuid4().hex
    prefix = "maestro-context-verify-" + token[:12]
    root = Path(archive_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose an existing archive root directory.")
    fixture = root / ("context-verify-" + token)
    fixture.mkdir()  # Exclusive UUID fixture; never reuse another directory.
    containers, volumes = [], []

    def owned(kind, name):
        exists = podman(kind, "exists", name, check=False)
        if exists.returncode == 1:
            return False
        if exists.returncode:
            raise RuntimeError("Could not verify resource ownership; preserving resources for inspection.")
        value = json.loads(podman(kind, "inspect", name).stdout)[0]
        labels = value.get("Labels", {}) if kind == "volume" else value["Config"].get("Labels", {})
        if labels.get(LABEL) != token:
            raise RuntimeError("Refusing cleanup of a resource without this verification token.")
        return True

    def remove_container(name):
        if owned("container", name):
            podman("rm", "--force", name)

    try:
        source_path = mount_source(fixture)
        image_id = json.loads(podman("image", "inspect", image).stdout)[0]["Id"]
        for suffix in ("data", "other-data"):
            name = prefix + "-" + suffix
            assert podman("volume", "exists", name, check=False).returncode == 1
            volumes.append(name)
            podman("volume", "create", "--label", LABEL + "=" + token, "--uid", "1000", "--gid", "1000", name)

        def run(name, volume, step, detach=False):
            assert podman("container", "exists", name, check=False).returncode == 1
            containers.append(name)
            options = ["--detach"] if detach else []
            result = podman("run", *options, "--name", name, "--pull", "never", "--network", "none", "--user", "1000:1000",
                            "--read-only", "--cap-drop", "all", "--security-opt", "no-new-privileges", "--umask", "0077",
                            "--volume", volume + ":/data:U", "--volume", source_path + ":/archive:rw,noexec,nosuid,nodev",
                            "--env", "MAESTRO_CONTEXT_ARCHIVE_DIR=/archive", "--label", LABEL + "=" + token,
                            "--entrypoint", "python", image_id, "-u", "-c", PROGRAM, step)
            info = json.loads(podman("container", "inspect", name).stdout)[0]
            assert info["Config"]["User"] == "1000:1000" and info["HostConfig"]["ReadonlyRootfs"]
            assert info["HostConfig"]["NetworkMode"] == "none"
            return result

        first = prefix + "-first"
        original = json.loads(run(first, volumes[0], "bootstrap").stdout)
        restarted = json.loads(podman("start", "--attach", first).stdout)
        assert original == restarted, "Restart changed historical evidence"
        remove_container(first)
        recreated = json.loads(run(prefix + "-recreated", volumes[0], "bootstrap").stdout)
        assert original == recreated, "Container recreation changed persisted evidence"

        holder = prefix + "-holder"
        run(holder, volumes[0], "hold", detach=True)
        deadline = time.monotonic() + 10
        while "READY" not in podman("logs", holder).stdout:
            if time.monotonic() >= deadline:
                raise RuntimeError("The synthetic archive owner did not become ready.")
            time.sleep(0.1)
        blocked = json.loads(run(prefix + "-contender", volumes[1], "contender").stdout)
        assert blocked["second_writer_blocked"]
        podman("exec", holder, "python", "-c", "from pathlib import Path; Path('/data/release-context-verifier').write_text('release')")
        assert podman("wait", holder).stdout.strip() == "0"
        assert not (fixture / "writer-owner.tmp").exists()
        maintenance = json.loads(run(prefix + "-maintenance", volumes[0], "rebuild-delete").stdout)
        print(json.dumps({"image": image_id, "uid": original["uid"], "fts5": original["fts5"],
                          "restart": True, "recreation": True, **blocked, **maintenance,
                          "archive_export_excludes_private_query": True, "network": "none"}))
    finally:
        for name in reversed(containers):
            remove_container(name)
        for name in reversed(volumes):
            if owned("volume", name):
                podman("volume", "rm", name)
        remove_fixture(fixture, root, token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="An already-built reviewed Maestro image; never pulled or rebuilt.")
    parser.add_argument("--archive-root", type=Path, help="Existing parent for a removable UUID synthetic archive fixture.")
    arguments = parser.parse_args()
    if arguments.archive_root is not None:
        verify(arguments.image, arguments.archive_root)
    else:
        with tempfile.TemporaryDirectory(prefix="maestro-context-container-root-") as temporary:
            verify(arguments.image, Path(temporary))
