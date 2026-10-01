"""API keys in Windows Credential Manager, never in the workspace database."""
import ctypes
from ctypes import wintypes
import hashlib
import os

session_keys = {}


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def target(base_url):
    return "Maestro/provider/" + hashlib.sha256(base_url.encode()).hexdigest()


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
    if os.name == "nt":
        api = vault()
        if not api.CredDeleteW(target(base_url), 1, 0) and ctypes.get_last_error() != 1168:
            raise ValueError("Could not remove the API key from Windows Credential Manager.")
    session_keys.pop(base_url, None)
