"""Runtime ownership, private SQLite snapshots and bounded archive file access."""
from contextlib import closing, contextmanager
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import uuid


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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Back up a private SQLite workspace to a new file.")
    parser.add_argument("source")
    parser.add_argument("destination")
    arguments = parser.parse_args()
    backup_database(arguments.source, arguments.destination)
