# Public-source archive policy

This is the archive foundation extracted from draft PR #8. `backend/context_policy.py` contains pure source-policy validation (R8-03a) and capture-manifest validation/projection (R8-03d); `backend/storage.py` provides bounded file access/publication (R8-03b) and local ownership/inventory (R8-03c). `backend/context_store.py` integrates configuration, capture/private indexing and pinned readback (R8-03e). `backend/app.py` adds configuration/status API and runtime ownership (R8-03f). Acquisition, lookup, deletion and reconstruction follow in separate review units.

New archive settings propose `all_public` with capture disabled, a 24-hour reuse window, 10 GiB and at most 200,000 files/directories. Legacy settings that omit the policy retain `approved_sources`; they require an approved HTTPS scope before enabling capture. Validation accepts byte caps up to 100 GiB and reuse windows from one to 168 hours.

Public HTTP/HTTPS links use their normal ports under `all_public`. Approved scopes require HTTPS. URLs with credentials, fragments, private destinations, unsafe characters or ambiguous encoded paths are rejected. Host casing, IDNA and normal ports are normalized; scheme, path and the complete query retain their identity.

A path scope matches its exact origin and path components, excluding query URLs. An explicitly approved query URL matches only that complete normalized URL; changing values, parameter order or encoding requires another approval. Eligibility also excludes known authenticated paths and credential, signed, session or personal query forms.

Credential checks inspect bounded percent-encoded and HTML-escaped safety views without changing stored text or URL identity. Invalid or excessively escaped inputs are rejected when they cannot be checked. Callers must supply the known credentials and apply these checks before export.

Retrieval timestamps require a timezone and normalize to UTC. Canonical metadata hashes preserve the metadata values and ignore dictionary key order. Manifest metadata validation does not establish that source claims are correct; general grounding and backend action verification remain a separate milestone.

The policy performs no network request, file publication or settings mutation. URL eligibility is a conservative export filter; it cannot prove the access policy or factual reliability of a publisher behind a public link.

## Capture metadata

`validate_capture_manifest` checks the supported exact schema, strict integer/boolean fields, eligible URL and canonical source identity, bounded UTF-8 title, UTC observation path, excerpt completeness and object path/hash/encoding/byte contract. It preserves the supplied metadata and URL/query identity. `capture_source` projects an already verified durable capture for callers; it performs no storage action.

`ContextStore.get_capture` combines those checks with bounded object reads, size/hash/UTF-8 verification, current eligibility and portable deletion markers, binding the index and requested capture IDs and metadata/content pins. Known-credential screening remains required at export and inference boundaries. The pure policy functions perform no filesystem, private-index or chat action.

Cold construction/state/status reads create no storage. Enabled initialization reuses configuration recovery under the mutation lock to clear stale availability errors and refresh bounded byte/item inventory without changing source permissions or private query mappings. Operational configuration failures, including refused writer claims, record unavailability under the same mutation lock; invalid settings leave healthy state intact. Root/index attribute-access errors use the existing unavailable/index-error responses. Explicit opt-in capture screens an independent source snapshot, takes writer ownership, enforces byte/item/free-space limits and publishes the immutable object, manifest and private index in that order. The capture mutation lock remains held through failure recovery so an older error cannot overwrite a successful retry's receipt. Repeated observations share objects while retaining distinct capture IDs/dates; private query mappings never enter the archive. Receipts count confirmed new bytes and successfully indexed captures independently of full inventory, reporting skipped, refused or partially failed evidence truthfully. The configuration/status API owns no acquisition or chat capture. Disabled startup creates no archive; failed enables release new claims, and lifecycle failures clear ownership metadata. Scope and private-content credential checks inspect bounded safety views while retaining original URL/text identity. Reconstruction will add its mutation-invalidation hooks with R8-04d.

API configuration validates source scopes and limits before claiming lifetime ownership. Initial claim refusals record unavailable status and a fixed error while retaining the previous settings; failure recording shares the mutation lock with retries. A successful enable clears the error, and invalid settings leave readiness and ownership unchanged.

## File access and publication

Callers supply a resolved archive root, enforce ownership/capacity and choose read limits. Paths reject traversal, drive/alternate-stream syntax, reserved Windows names, symlinks and junctions, including replaced roots and ancestors. Reads consume at most the limit plus one byte and reject oversized or excessively nested JSON documents.

Publication writes an exclusive staging file, flushes and syncs its bytes, then uses an atomic hard link to create the destination without replacing it. Identical existing content is reused; conflicting content or a publication race raises an error. Owned staging cleanup is attempted after success or failure; cleanup errors propagate even if the complete destination already exists. Unrelated staging collisions remain untouched.

The destination filesystem must support hard links. Unsupported publication fails without a replacement fallback. File helpers do not validate manifests or connect capture to chat; callers obtain ownership separately. Existing workspace locking and SQLite snapshots retain their behavior.

## Local ownership and inventory

Archive ownership uses an exclusive, synced `writer-owner.tmp` claim and one process-local token/count map. Nested and overlapping lifetimes for the same owner retain the claim until their last exit, and each reentrant entry verifies its current claim. Other owners are refused. Entry guards the archive path before creating directories; release verifies the token and waits for the caller's write lock before removing its claim. No thread lock remains held across the ownership context's body.

A stale, missing or changed live claim blocks further writes; stale/changed claims are preserved for explicit cleanup after their previous runtime has stopped. Readiness inspects the guarded path, registry and current token without creating storage. This is local filesystem ownership for the supported Windows/container setup, not a cross-machine lease over synced copies.

Inventory accepts an explicit item cap, checking a ten-second scan allowance during traversal and after every directory, including empty ones. Filesystem calls are synchronous. It counts directories and files, measures logical file bytes and returns capture-manifest paths without parsing content or initializing an index. Capture and recovery callers choose their existing caps; inventory itself neither changes configuration nor prunes data. These helpers do not claim protection against hostile concurrent directory replacement.
