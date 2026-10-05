# Public-source archive policy

This is the archive foundation extracted from draft PR #8. `backend/context_policy.py` contains pure source-policy validation (R8-03a) and capture-manifest validation/projection (R8-03d); `backend/storage.py` adds bounded file access/publication (R8-03b). Ownership, capture/index integration, saved lookup and chat/UI integration follow in separate review units.

New archive settings propose `all_public` with capture disabled, a 24-hour reuse window, 10 GiB and at most 200,000 files/directories. Legacy settings that omit the policy retain `approved_sources`; they require an approved HTTPS scope before enabling capture. Validation accepts byte caps up to 100 GiB and reuse windows from one to 168 hours.

Public HTTP/HTTPS links use their normal ports under `all_public`. Approved scopes require HTTPS. URLs with credentials, fragments, private destinations, unsafe characters or ambiguous encoded paths are rejected. Host casing, IDNA and normal ports are normalized; scheme, path and the complete query retain their identity.

A path scope matches its exact origin and path components, excluding query URLs. An explicitly approved query URL matches only that complete normalized URL; changing values, parameter order or encoding requires another approval. Eligibility also excludes known authenticated paths and credential, signed, session or personal query forms.

Credential checks inspect bounded percent-encoded and HTML-escaped safety views without changing stored text or URL identity. Invalid or excessively escaped inputs are rejected when they cannot be checked. Callers must supply the known credentials and apply these checks before export.

Retrieval timestamps require a timezone and normalize to UTC. Canonical metadata hashes preserve the metadata values and ignore dictionary key order. Manifest metadata validation does not establish that source claims are correct; general grounding and backend action verification remain a separate milestone.

The policy performs no network request, file publication or settings mutation. URL eligibility is a conservative export filter; it cannot prove the access policy or factual reliability of a publisher behind a public link.

## Capture metadata

`validate_capture_manifest` checks the supported exact schema, strict integer/boolean fields, eligible URL and canonical source identity, bounded UTF-8 title, UTC observation path, excerpt completeness and object path/hash/encoding/byte contract. It preserves the supplied metadata and URL/query identity. `capture_source` projects an already verified durable capture for callers; it performs no storage action.

Callers must still check deletion, read bounded object bytes, verify their size/hash/UTF-8 and bind index/requested capture IDs and metadata/content pins. Known-credential screening remains required at export and inference boundaries. No filesystem, private index or chat integration is enabled by these pure functions.

## File access and publication

Callers supply a resolved archive root, enforce ownership/capacity and choose read limits. Paths reject traversal, drive/alternate-stream syntax, reserved Windows names, symlinks and junctions, including replaced roots and ancestors. Reads consume at most the limit plus one byte and reject oversized or excessively nested JSON documents.

Publication writes an exclusive staging file, flushes and syncs its bytes, then uses an atomic hard link to create the destination without replacing it. Identical existing content is reused; conflicting content or a publication race raises an error. Owned staging files are removed after success or failure; unrelated staging collisions remain untouched.

The destination filesystem must support hard links. Unsupported publication fails without a replacement fallback. These helpers do not claim protection against hostile concurrent directory replacement, enforce archive ownership, validate manifests or connect capture to chat. Existing workspace locking and SQLite snapshots retain their behavior.
