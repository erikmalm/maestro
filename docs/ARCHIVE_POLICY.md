# Public-source archive policy

This is the validation foundation extracted from draft PR #8 (R8-03a). `backend/context_policy.py` contains pure functions for export eligibility, configuration, credential screening, retrieval timestamps and canonical metadata hashes. File publication, storage/API integration, saved lookup and chat/UI integration follow in separate review units.

New archive settings propose `all_public` with capture disabled, a 24-hour reuse window, 10 GiB and at most 200,000 files/directories. Legacy settings that omit the policy retain `approved_sources`; they require an approved HTTPS scope before enabling capture. Validation accepts byte caps up to 100 GiB and reuse windows from one to 168 hours.

Public HTTP/HTTPS links use their normal ports under `all_public`. Approved scopes require HTTPS. URLs with credentials, fragments, private destinations, unsafe characters or ambiguous encoded paths are rejected. Host casing, IDNA and normal ports are normalized; scheme, path and the complete query retain their identity.

A path scope matches its exact origin and path components, excluding query URLs. An explicitly approved query URL matches only that complete normalized URL; changing values, parameter order or encoding requires another approval. Eligibility also excludes known authenticated paths and credential, signed, session or personal query forms.

Credential checks inspect bounded percent-encoded and HTML-escaped safety views without changing stored text or URL identity. Invalid or excessively escaped inputs are rejected when they cannot be checked. Callers must supply the known credentials and apply these checks before export.

Retrieval timestamps require a timezone and normalize to UTC. Canonical metadata hashes preserve the metadata values and ignore dictionary key order. These helpers do not validate an entire capture manifest or establish that source claims are correct. Publication and capture-pin checks belong to the next storage unit; general grounding and backend action verification remain a separate milestone.

The policy performs no network request, file publication or settings mutation. URL eligibility is a conservative export filter; it cannot prove the access policy or factual reliability of a publisher behind a public link.
