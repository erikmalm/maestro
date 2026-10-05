"""Public export policy and metadata validation; no storage or network operations."""
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from backend.web_search import safe_url


MAX_ARCHIVE_ITEMS = 200000
SCHEMA = 1
SOURCE_BYTES = 8192
MANIFEST_BYTES = 16384
ID = re.compile(r"[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
DEFAULT = {"enabled": False, "capture_policy": "all_public", "public_sources": [], "reuse_hours": 24,
           "max_bytes": 10 * 1024 ** 3, "max_items": MAX_ARCHIVE_ITEMS}


def decoded_url_variants(value):
    """Bounded safety views only; callers must retain the original URL identity."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Use a valid public source URL.")
    variants = [value]
    try:
        for _ in range(8):
            decoded = unquote(variants[-1], errors="strict")
            if decoded == variants[-1]:
                return tuple(variants)
            variants.append(decoded)
    except UnicodeError:
        pass
    raise ValueError("Use a valid public source URL.")


def contains_url_secret(value, secrets):
    secrets = tuple(secrets)
    try:
        return any(secret and secret in variant for variant in decoded_url_variants(value) for secret in secrets)
    except ValueError:
        return True


def contains_source_secret(value, secrets):
    """Inspect bounded safety views without changing source text or URL identity."""
    secrets = tuple(secret for secret in secrets if secret)
    if not secrets:
        return False
    # Search responses already have this wire bound. Keep direct callers bounded
    # too, and fail closed if repeated escaping cannot be checked within it.
    if not isinstance(value, str) or len(value) > 131072:
        return True
    for _ in range(8):
        if any(secret in value for secret in secrets):
            return True
        decoded = html.unescape(unquote(value, errors="replace"))
        if decoded == value:
            return False
        value = decoded
    return True


def public_url(value, *, allow_query=False, allow_http=False):
    """A conservative export scope; explicit query scopes retain their identity."""
    error = ("Use a public HTTPS source without credentials, fragments or control characters." if allow_query
             else "Use a public HTTPS origin and path without query parameters or credentials.")
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(c) <= 32 or unicodedata.category(c) == "Cc" for c in value)):
        raise ValueError(error)
    try:
        parts = urlsplit(value)
        if (parts.scheme not in (("https", "http") if allow_http else ("https",)) or not safe_url(value)
                or parts.port not in (None, 443 if parts.scheme == "https" else 80)
                or parts.fragment or "#" in value or "\\" in value
                or ("?" in value and (not allow_query or not parts.query))):
            raise ValueError()
        path = parts.path or "/"
        # Encoded separators/dot segments and repeated decoding make path scopes ambiguous.
        decoded = unquote(path, errors="strict")
        if ("%" in decoded or "\\" in decoded or any(ord(c) <= 32 or unicodedata.category(c) == "Cc" for c in decoded)
                or re.search(r"%(?:2f|5c)", path, re.I) or any(p in (".", "..") for p in decoded.split("/"))):
            raise ValueError()
        # Decode only for validation, never for URL identity. Bound repeated
        # escaping so encoded controls/backslashes cannot evade the check.
        if any("\\" in decoded or any(unicodedata.category(c) == "Cc" for c in decoded)
               for decoded in decoded_url_variants(parts.query)):
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if host.endswith("."):
            raise ValueError()
        return urlunsplit((parts.scheme, "[" + host + "]" if ":" in host else host, path, parts.query, ""))
    except (ValueError, UnicodeError):
        raise ValueError(error) from None


def approved(value, scopes):
    try:
        normalized = public_url(value, allow_query=True)
        url = urlsplit(normalized)
        for item in scopes:
            scope_url = public_url(item, allow_query=True)
            scope = urlsplit(scope_url)
            if url.query or scope.query:
                if normalized == scope_url:
                    return True
            elif (url.netloc == scope.netloc and (scope.path == "/" or url.path == scope.path.rstrip("/")
                                                  or url.path.startswith(scope.path.rstrip("/") + "/"))):
                return True
        return False
    except ValueError:
        return False


def eligible(value, config):
    """Public excerpts only; URL checks cannot establish the publisher's access policy."""
    try:
        normalized = public_url(value, allow_query=True, allow_http=config["capture_policy"] == "all_public")
        # Decode only safety views, retaining the original query and URL identity.
        sensitive = {"token", "accesstoken", "refreshtoken", "idtoken", "apikey", "key", "auth", "authorization",
                     "password", "passwd", "pwd", "secret", "session", "sessionid", "sid", "cookie",
                     "credential", "credentials", "signature", "sig", "signed", "jwt", "bearer",
                     "accesskey", "awsaccesskeyid", "googleaccessid", "privatekey", "email", "emailaddress",
                     "username", "userid", "user", "account", "accountid", "customerid", "patientid",
                     "member", "memberid", "profile", "profileid", "employeeid", "personid",
                     "personal", "private", "ssn"}
        private_paths = {"login", "signin", "sign-in", "oauth", "authorize", "account", "accounts", "my-account",
                         "inbox", "admin", "dashboard", "private"}
        for variant in decoded_url_variants(normalized):
            parts = urlsplit(variant)
            if (parts.hostname or "").casefold().endswith((".internal", ".lan", ".home", ".corp", ".onion")):
                return False
            if any(segment.casefold() in private_paths for segment in parts.path.split("/")):
                return False
            for key, item in parse_qsl(parts.query, keep_blank_values=True, errors="strict", max_num_fields=100):
                names = (re.sub(r"[^a-z0-9]", "", part.casefold()) for part in (key, *re.split(r"[\[\]]", key)))
                if (any(name in sensitive or name.startswith(("xamz", "xgoog", "oauth", "auth"))
                        or name.endswith(("token", "password", "secret", "signature", "apikey", "sessionid"))
                        for name in names)
                        or re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", item)):
                    return False
        return config["capture_policy"] == "all_public" or approved(value, config["public_sources"])
    except (ValueError, UnicodeError, TypeError, KeyError):
        return False


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("Source retrieval time must include its timezone.") from None


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_capture_manifest(data, relative, config):
    """Validate metadata; callers still check deletion, object bytes and pins."""
    required = {"schema_version", "capture_id", "source_id", "source_url", "title", "object", "retrieved_at", "content_kind", "completeness", "published_at", "modified_at"}
    if (not isinstance(relative, str) or not isinstance(data, dict) or set(data) != required
            or type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA
            or not isinstance(data["capture_id"], str) or not ID.fullmatch(data["capture_id"])
            or not eligible(data["source_url"], config)
            or data["source_id"] != hashlib.sha256(public_url(data["source_url"], allow_query=True, allow_http=True).encode()).hexdigest()
            or not isinstance(data["title"], str) or len(data["title"]) > 200
            or data["content_kind"] != "search_excerpt" or data["published_at"] is not None or data["modified_at"] is not None
            or not isinstance(data["completeness"], dict) or set(data["completeness"]) != {"maestro_truncated", "full_page"}
            or type(data["completeness"]["maestro_truncated"]) is not bool or data["completeness"]["full_page"] is not False):
        raise ValueError("Invalid or unavailable source capture.")
    data["title"].encode("utf-8")
    if relative != "records/captures/" + timestamp(data["retrieved_at"])[:7] + "/" + data["capture_id"] + ".json":
        raise ValueError("Invalid capture observation path.")
    obj = data["object"]
    if (not isinstance(obj, dict) or set(obj) != {"path", "sha256", "bytes", "encoding"}
            or not isinstance(obj["sha256"], str) or not HASH.fullmatch(obj["sha256"])
            or obj["path"] != "objects/sha256/" + obj["sha256"][:2] + "/" + obj["sha256"] + ".txt"
            or obj["encoding"] != "utf-8" or type(obj["bytes"]) is not int or not 1 <= obj["bytes"] <= SOURCE_BYTES):
        raise ValueError("Invalid archived source object.")
    return data


def capture_source(manifest, content):
    """Project an already verified durable capture; perform no storage action."""
    return {"capture_id": manifest["capture_id"], "title": manifest["title"], "url": manifest["source_url"], "content": content,
            "content_hash": manifest["object"]["sha256"], "manifest_hash": manifest_hash(manifest), "archive_status": "saved",
            **{key: manifest[key] for key in ("content_kind", "completeness", "retrieved_at", "published_at", "modified_at")}}


def configuration(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid context archive settings.")
    value = {"capture_policy": "approved_sources", **value}
    if (set(value) != set(DEFAULT) or type(value["enabled"]) is not bool
            or value["capture_policy"] not in ("approved_sources", "all_public")):
        raise ValueError("Invalid context archive settings.")
    for field, minimum, maximum in (("reuse_hours", 1, 168), ("max_bytes", 1024 * 1024, 100 * 1024 ** 3), ("max_items", 100, MAX_ARCHIVE_ITEMS)):
        if type(value[field]) is not int or not minimum <= value[field] <= maximum:
            raise ValueError("Context archive limits are outside their supported range.")
    scopes = value["public_sources"]
    if not isinstance(scopes, list) or len(scopes) > 100:
        raise ValueError("Choose at most 100 approved public source scopes.")
    scopes = list(dict.fromkeys(public_url(scope, allow_query=True) for scope in scopes))
    if value["enabled"] and value["capture_policy"] == "approved_sources" and not scopes:
        raise ValueError("Approve public HTTPS source scopes before enabling the archive.")
    return {**value, "public_sources": scopes}
