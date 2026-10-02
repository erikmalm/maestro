"""Exclusive runtime ownership and consistent private SQLite snapshots."""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3


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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Back up a private SQLite workspace to a new file.")
    parser.add_argument("source")
    parser.add_argument("destination")
    arguments = parser.parse_args()
    backup_database(arguments.source, arguments.destination)
