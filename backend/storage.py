"""Runtime ownership, private SQLite snapshots and bounded archive file access."""
from contextlib import closing, contextmanager
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import threading
import time
import uuid


_owners = {}
_owner_lock = threading.RLock()


@contextmanager
def workspace_owner(directory):
    with (Path(directory) / "runtime.lock").open("a+b") as lock:
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("This private workspace is already running. Stop its other Maestro instance first.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("The source workspace database does not exist.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"):
        pass  # Never replace an existing workspace or backup.
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(destination)) as snapshot:
            original.backup(snapshot)
    except Exception:
        destination.unlink()
        raise


def archive_path(root, relative):
    if root is None or not isinstance(relative, str):
        raise ValueError("The source archive is unavailable.")
    root, parts = Path(root), Path(relative)
    reserved = getattr(ntpath, "isreserved", lambda part: PureWindowsPath(part).is_reserved())
    if (parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative
            or any(part.endswith((".", " ")) or reserved(part) for part in parts.parts)):
        raise ValueError("Invalid archived source path.")
    path = root / parts
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("Archived source links are not supported.")
    if root not in path.resolve().parents:
        raise ValueError("Invalid archived source path.")
    return path


def read_archive(root, relative, limit):
    if type(limit) is not int or limit < 0:
        raise ValueError("Use a non-negative archive read limit.")
    with archive_path(root, relative).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("An archived source exceeds its supported size.")
    return data


def read_archive_json(root, relative, limit):
    try:
        return json.loads(read_archive(root, relative, limit))
    except RecursionError:
        raise ValueError("An archived JSON document is too deeply nested.") from None


def publish_archive(root, relative, data):
    """Publish flushed bytes without replacement; the caller owns the archive."""
    path = archive_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_archive(root, relative, len(data)) != data:
            raise ValueError("Existing archive content does not match its hash.")
        return False
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    stream = temporary.open("xb")
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def archive_available(root, token):
    """Inspect writer availability without creating directories or claims."""
    if root is None or not Path(root).is_dir():
        return False
    try:
        claim = archive_path(root, "writer-owner.tmp")
        with _owner_lock:
            owner = _owners.get(os.path.normcase(str(root)))
            if not claim.exists():
                return owner is None
            return (owner is not None and owner[0] == token
                    and read_archive(root, "writer-owner.tmp", 64).decode("ascii") == token)
    except (OSError, ValueError, UnicodeError):
        return False


@contextmanager
def archive_owner(root, token, write_lock):
    """Claim one archive locally; stale claims require explicit cleanup."""
    if root is None:
        raise ValueError("Configure a context archive directory on the server first.")
    claim = archive_path(root, "writer-owner.tmp")
    root, key = claim.parent, os.path.normcase(str(root))
    with _owner_lock:
        if key in _owners:
            if _owners[key][0] != token:
                raise ValueError("Another Maestro workspace owns this archive. Stop it before enabling a second writer.")
            if not archive_available(root, token):
                raise ValueError("The archive writer claim changed. Stop capture and check its ownership.")
            _owners[key][1] += 1
        else:
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise ValueError("The context archive directory is unavailable or cannot be written.") from None
            try:
                with os.fdopen(os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="ascii") as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                raise ValueError("This archive has an existing writer claim. Stop its previous Maestro instance before removing writer-owner.tmp and retrying.") from None
            except OSError:
                raise ValueError("The context archive writer claim could not be created. Check its directory permissions.") from None
            _owners[key] = [token, 1]
    try:
        yield
    finally:
        with write_lock, _owner_lock:
            _owners[key][1] -= 1
            if not _owners[key][1]:
                _owners.pop(key, None)
                try:
                    if read_archive(root, "writer-owner.tmp", 64).decode("ascii") == token:
                        claim.unlink()
                except (OSError, ValueError, UnicodeError):
                    pass  # An uncleared claim blocks the next writer safely.


def archive_inventory(root, max_items):
    """Count all entries and bytes within one item and ten-second scan allowance."""
    if type(max_items) is not int or max_items < 0:
        raise ValueError("Use a non-negative archive item allowance.")
    root = archive_path(root, "writer-owner.tmp").parent
    if not root.is_dir():
        raise ValueError("The context archive is unavailable.")
    count = total = 0
    captures, pending = [], [root]
    started = time.monotonic()
    while pending:
        directory = pending.pop()
        # A child path validates the root itself as well as queued directories.
        archive_path(root, (directory / "writer-owner.tmp").relative_to(root).as_posix())
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > max_items or time.monotonic() - started > 10:
                    raise ValueError("The archive exceeds its item or scan allowance.")
                relative = Path(entry.path).relative_to(root).as_posix()
                path = archive_path(root, relative)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    total += path.stat().st_size
                    if re.fullmatch(r"records/captures/\d{4}-\d{2}/[a-f0-9]{32}\.json", relative):
                        captures.append(relative)
        if time.monotonic() - started > 10:
            raise ValueError("The archive exceeds its item or scan allowance.")
    return total, count, captures


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Back up a private SQLite workspace to a new file.")
    parser.add_argument("source")
    parser.add_argument("destination")
    arguments = parser.parse_args()
    backup_database(arguments.source, arguments.destination)
