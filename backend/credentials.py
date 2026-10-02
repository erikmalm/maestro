"""API keys in Windows Credential Manager, never in the workspace database."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path

session_keys = {}


def secret_file(base_url):
    variable = "MAESTRO_SEARCH_KEY_FILE" if base_url == "https://ollama.com/api/web_search" else "MAESTRO_API_KEY_FILE" if base_url == os.environ.get("MAESTRO_API_KEY_URL", "https://api.openai.com/v1").rstrip("/") else None
    return os.environ.get(variable, "") if variable else ""


def storage_options(base_url):
    managed = bool(secret_file(base_url))
    return {"persist_supported": os.name == "nt" and not managed, "managed_credentials": managed}


def status(base_url):
    try:
        key, source = read(base_url)
        error = {}
    except ValueError as failure:
        key, source = None, "unavailable"
        error = {"credential_error": str(failure)}
    return {"credentials_present": bool(key), "credential_source": source, **error, **storage_options(base_url)}


def reject_managed_change(base_url):
    if secret_file(base_url):
        raise ValueError("This key is managed by the server. Update or remove its mounted secret and recreate Maestro.")


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def target(base_url):
    return "Maestro/provider/" + hashlib.sha256(base_url.encode()).hexdigest()


def redact(text, key):
    """Remove the dispatched key without reproducing it in the marker."""
    if not key:
        return text
    redacted = text.replace(key, "[redacted]")
    return text.replace(key, "\u2588") if key in redacted else redacted


def vault():
    if os.name != "nt":
        raise ValueError("Persistent key storage requires Windows. Choose session-only storage.")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def read(base_url):
    path = secret_file(base_url)
    if path:
        try:
            with Path(path).open(encoding="utf-8") as stream:
                raw = stream.read(4098)
            key = raw.strip()
            if len(raw) > 4097 or not key or len(key) > 4096 or not key.isascii() or not key.isprintable():
                raise ValueError
        except (OSError, UnicodeError, ValueError):
            raise ValueError("The server's mounted API key is missing or invalid. Check its secret configuration.") from None
        return key, "mounted secret"
    if base_url in session_keys:
        return session_keys[base_url], "session"
    if os.name == "nt":
        api, pointer = vault(), ctypes.POINTER(Credential)()
        if api.CredReadW(target(base_url), 1, 0, ctypes.byref(pointer)):
            try:
                return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode("utf-8"), "Windows Credential Manager"
            finally:
                api.CredFree(pointer)
        if ctypes.get_last_error() != 1168:
            raise ValueError("Could not read Windows Credential Manager.")
    if base_url == "https://api.openai.com/v1" and os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"], "server environment"
    return None, "missing"


def save(base_url, key, persist):
    reject_managed_change(base_url)
    if not key.isascii() or not key.isprintable():
        raise ValueError("Enter a valid API key without control or non-ASCII characters.")
    if not persist:
        session_keys[base_url] = key
        return
    api = vault()
    encoded = key.encode("utf-8")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = Credential(Type=1, TargetName=target(base_url), CredentialBlobSize=len(encoded),
                            CredentialBlob=blob, Persist=2, UserName="Maestro")
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise ValueError("Could not save the API key in Windows Credential Manager. Try session-only storage.")
    session_keys.pop(base_url, None)


def delete(base_url):
    reject_managed_change(base_url)
    if base_url == "https://api.openai.com/v1" and os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Remove OPENAI_API_KEY from the server environment and restart Maestro before removing this API key.")
    if os.name == "nt":
        api = vault()
        if not api.CredDeleteW(target(base_url), 1, 0) and ctypes.get_last_error() != 1168:
            raise ValueError("Could not remove the API key from Windows Credential Manager.")
    session_keys.pop(base_url, None)
