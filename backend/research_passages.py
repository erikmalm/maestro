"""Bounded, exact saved-text passages for local requirement evidence review."""
from copy import deepcopy
import hashlib
import json
import re

from backend.context_policy import HASH, ID, SOURCE_BYTES, public_url, timestamp


MAX_SOURCES = 8
MAX_PASSAGES = 6
MAX_PASSAGE_CHARACTERS = 1200
MAX_PACKET_BYTES = 7000
MAX_CANDIDATES = 20
SOURCE_FIELDS = ("capture_id", "content_hash", "manifest_hash", "title", "url", "retrieved_at",
                 "published_at", "modified_at", "stale", "completeness")
LOOKUP_STATES = ("ready", "limited", "disabled", "unconfigured", "unavailable")
_WORDS = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_STOPWORDS = frozenset("a an and are as at be by compare for from give how in is it of on or research the to what which with".split())


def _invalid():
    raise ValueError("Saved evidence passages are invalid or unavailable.")


def evidence_packet_hash(packet):
    """Bind an owned stage to the exact fitted packet, independent of key order."""
    return hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def packet_size(packet):
    """Bytes in the canonical, compact UTF-8 JSON representation."""
    try:
        return len(json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _invalid()


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _invalid()


def _text(value, maximum, *, empty=False, nul=False):
    if (not isinstance(value, str) or not empty and not value.strip() or len(value) > maximum
            or not nul and "\x00" in value):
        _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid()
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()


def _lookup(value):
    if type(value) is not dict:
        _invalid()
    result = {field: value.get(field) for field in ("state", "search_limited", "skipped_count")}
    if result["state"] not in LOOKUP_STATES or type(result["search_limited"]) is not bool:
        _invalid()
    _integer(result["skipped_count"], 0, 1000)
    return result


def _source(value, *, stale=None):
    if (type(value) is not dict or any(field not in value for field in SOURCE_FIELDS if field != "stale")
            or "stale" not in value and stale is None):
        _invalid()
    result = {field: deepcopy(value.get(field)) for field in SOURCE_FIELDS}
    if stale is not None:
        result["stale"] = stale
    if (not isinstance(result["capture_id"], str) or not ID.fullmatch(result["capture_id"])
            or any(not isinstance(result[field], str) or not HASH.fullmatch(result[field])
                   for field in ("content_hash", "manifest_hash"))
            or type(result["stale"]) is not bool):
        _invalid()
    _text(result["title"], 200, empty=True, nul=True)
    _text(result["url"], 2048)
    public_url(result["url"], allow_query=True, allow_http=True)
    for field in ("retrieved_at", "published_at", "modified_at"):
        if result[field] is None and field != "retrieved_at":
            continue
        _text(result[field], 64)
        timestamp(result[field])
    _shape(result["completeness"], ("maestro_truncated", "full_page"))
    if any(type(flag) is not bool for flag in result["completeness"].values()):
        _invalid()
    return result


def _content(value, source):
    content = _text(value.get("content"), SOURCE_BYTES, empty=True, nul=True)
    raw = content.encode("utf-8")
    if not 1 <= len(raw) <= SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != source["content_hash"]:
        _invalid()
    return content


def _terms(assessment):
    if type(assessment) is not dict:
        _invalid()
    terms = {}

    def add(value, weight, *, words=False):
        if value is None:
            return
        _text(value, 4000)
        values = [match.group() for match in _WORDS.finditer(value)] if words else [value]
        for term in values:
            if words and (len(term) < 3 or term.casefold() in _STOPWORDS):
                continue
            key = term.casefold()
            if key not in terms or terms[key][1] < weight:
                terms[key] = (term, weight)

    subjects, items = assessment.get("subjects"), assessment.get("items")
    if type(subjects) is not list or len(subjects) > 8 or type(items) is not list or len(items) > 16:
        _invalid()
    for subject in subjects:
        if type(subject) is not dict or type(subject.get("identifiers")) is not list or len(subject["identifiers"]) > 8:
            _invalid()
        add(subject.get("name"), 4)
        for identifier in subject["identifiers"]:
            if type(identifier) is not dict:
                _invalid()
            add(identifier.get("text"), 4)
    for item in items:
        if type(item) is not dict:
            _invalid()
        criterion = item.get("criterion")
        _text(criterion, 4000)
        if len(criterion) <= 160:
            add(criterion, 3)
        add(criterion, 1, words=True)
        for field in ("period", "unit", "currency", "freshness"):
            add(item.get(field), 3)
    # All input is bounded canonical assessment data. Keep every literal term;
    # dropping later items here could silently bias passage selection.
    return [(re.compile(re.escape(term), re.IGNORECASE), weight) for term, weight in terms.values()]


def _windows(content, patterns):
    width = min(MAX_PASSAGE_CHARACTERS, len(content))
    # Overlapping windows cover the complete saved text. Their count depends on
    # source length, not the potentially large number of repeated keyword hits.
    starts = set(range(0, max(1, len(content) - width + 1), MAX_PASSAGE_CHARACTERS // 2))
    starts.add(len(content) - width)
    windows = []
    for start in sorted(starts):
        end = start + width
        hits = [(weight, match) for pattern, weight in patterns
                if (match := pattern.search(content, start, end)) is not None]
        if not hits:
            continue
        _, focus = max(hits, key=lambda hit: (hit[0], -hit[1].start()))
        windows.append((sum(weight for weight, _ in hits), len(hits), start, end,
                        (focus.start() + focus.end()) // 2, focus.end() - focus.start()))
    ranked = sorted(windows, key=lambda item: (-item[0], -item[1], item[2]))
    distinct = []
    for window in ranked:
        start, end = window[2:4]
        if any(max(0, min(end, other[3]) - max(start, other[2])) >= 0.8 * min(end - start, other[3] - other[2])
               for other in distinct):
            continue
        distinct.append(window)
    return distinct


def _uncovered(content, start, end, passages, source_id, patterns):
    intervals = [(start, end)]
    for passage in passages:
        if passage["source_id"] != source_id:
            continue
        remaining = []
        for left, right in intervals:
            if right <= passage["start"] or left >= passage["end"]:
                remaining.append((left, right))
            else:
                if left < passage["start"]:
                    remaining.append((left, passage["start"]))
                if passage["end"] < right:
                    remaining.append((passage["end"], right))
        intervals = remaining
    choices = []
    for left, right in intervals:
        hits = [(weight, match) for pattern, weight in patterns
                if (match := pattern.search(content, left, right)) is not None]
        if hits:
            _, focus = max(hits, key=lambda hit: (hit[0], -hit[1].start()))
            choices.append((-sum(weight for weight, _ in hits), -len(hits), left, right,
                            (focus.start() + focus.end()) // 2, focus.end() - focus.start()))
    return min(choices)[2:] if choices else None


def _passage(identifier, source_id, content, start, end):
    text = content[start:end]
    return {"id": identifier, "source_id": source_id, "start": start, "end": end,
            "text": text, "passage_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def select_packet(candidates, assessment, lookup, *, max_passages=MAX_PASSAGES, max_bytes=MAX_PACKET_BYTES):
    """Select relevant windows anywhere in verified saved text, without changing it.

    Selection is a bounded nomination, never a claim that lookup was exhaustive.
    Omitted count describes distinct nominated windows excluded by packet caps;
    limited also covers windows shortened to fit the packet byte allowance.
    """
    _integer(max_passages, 1, MAX_PASSAGES)
    _integer(max_bytes, 1, MAX_PACKET_BYTES)
    if type(candidates) is not list or len(candidates) > MAX_CANDIDATES:
        _invalid()
    packet = {"version": 1, "offset_unit": "unicode_codepoint", "lookup": _lookup(lookup),
              "selection": {"limited": False, "omitted_count": 0}, "sources": [], "passages": []}
    # Reserve the longer true flag and bounded omission counter during fitting.
    if packet_size({**packet, "selection": {"limited": True, "omitted_count": 999999}}) > max_bytes:
        _invalid()
    patterns, records, windows = _terms(assessment), {}, []
    for position, candidate in enumerate(candidates):
        source = _source(candidate)
        content = _content(candidate, source)
        identity = source["capture_id"]
        if identity in records:
            if records[identity] != (source, content):
                _invalid()
            continue
        records[identity] = (source, content)
        for score, hits, start, end, anchor, minimum in _windows(content, patterns):
            windows.append((-score, -hits, position, start, end, anchor, minimum, identity))
    source_ids, shortened = {}, False
    for _, _, _, start, end, anchor, minimum, identity in sorted(windows):
        if len(packet["passages"]) >= max_passages:
            break
        source, content = records[identity]
        source_id = source_ids.get(identity, "c" + str(len(source_ids) + 1))
        if identity not in source_ids and len(source_ids) == MAX_SOURCES:
            continue
        uncovered = _uncovered(content, start, end, packet["passages"], source_id, patterns)
        if uncovered is None:
            continue
        start, end, anchor, minimum = uncovered
        proposed_source = {"id": source_id, **source}
        identifier = "p" + str(len(packet["passages"]) + 1)

        def proposal(length):
            left = max(start, min(anchor - length // 2, end - length))
            result = {**packet,
                      "selection": {"limited": True, "omitted_count": len(windows)},
                      "sources": packet["sources"] + ([] if identity in source_ids else [proposed_source]),
                      "passages": packet["passages"] + [_passage(identifier, source_id, content, left, left + length)]}
            return result

        full_length = end - start
        chosen = proposal(full_length)
        if packet_size(chosen) > max_bytes:
            low, high, fitting = minimum, full_length - 1, None
            while low <= high:
                length = (low + high) // 2
                proposed = proposal(length)
                if packet_size(proposed) <= max_bytes:
                    fitting, low = proposed, length + 1
                else:
                    high = length - 1
            if fitting is None:
                continue
            chosen, shortened = fitting, True
        if identity not in source_ids:
            source_ids[identity] = source_id
        packet["sources"], packet["passages"] = chosen["sources"], chosen["passages"]
    omitted = len(windows) - len(packet["passages"])
    packet["selection"] = {"limited": bool(omitted or shortened), "omitted_count": omitted}
    if packet_size(packet) > max_bytes:
        _invalid()
    return packet


def validate_packet(packet, load_capture_callback):
    """Reopen exact pins and reconstruct every passage before use or publication.

    The loader must enforce current eligibility and credential screening. Stale
    is lookup-time metadata; if the loader supplies stale, it must match too.
    """
    _shape(packet, ("version", "offset_unit", "lookup", "selection", "sources", "passages"))
    if type(packet["version"]) is not int or packet["version"] != 1 or packet["offset_unit"] != "unicode_codepoint":
        _invalid()
    _shape(packet["lookup"], ("state", "search_limited", "skipped_count"))
    lookup = _lookup(packet["lookup"])
    _shape(packet["selection"], ("limited", "omitted_count"))
    if type(packet["selection"]["limited"]) is not bool:
        _invalid()
    _integer(packet["selection"]["omitted_count"], 0, 999999)
    if packet["selection"]["omitted_count"] and not packet["selection"]["limited"]:
        _invalid()
    if (type(packet["sources"]) is not list or len(packet["sources"]) > MAX_SOURCES
            or type(packet["passages"]) is not list or len(packet["passages"]) > MAX_PASSAGES
            or not callable(load_capture_callback) or packet_size(packet) > MAX_PACKET_BYTES):
        _invalid()
    sources, captures, identities = {}, {}, set()
    for number, value in enumerate(packet["sources"], 1):
        _shape(value, ("id", *SOURCE_FIELDS))
        if value["id"] != "c" + str(number):
            _invalid()
        source = _source(value)
        if source["capture_id"] in identities:
            _invalid()
        identities.add(source["capture_id"])
        try:
            current = load_capture_callback(source["capture_id"], source["content_hash"], source["manifest_hash"])
            refreshed = _source(current, stale=source["stale"] if "stale" not in current else None)
            if refreshed != source:
                _invalid()
            content = _content(current, refreshed)
        except Exception:
            _invalid()
        sources[value["id"]], captures[value["id"]] = {"id": value["id"], **refreshed}, content
    passages, used, spans = [], set(), set()
    for number, value in enumerate(packet["passages"], 1):
        _shape(value, ("id", "source_id", "start", "end", "text", "passage_hash"))
        if value["id"] != "p" + str(number) or not isinstance(value["source_id"], str) or value["source_id"] not in sources:
            _invalid()
        content = captures[value["source_id"]]
        _integer(value["start"], 0, len(content))
        _integer(value["end"], value["start"] + 1, min(len(content), value["start"] + MAX_PASSAGE_CHARACTERS))
        expected = _passage(value["id"], value["source_id"], content, value["start"], value["end"])
        if value != expected or not value["text"].strip():
            _invalid()
        span = (value["source_id"], value["start"], value["end"])
        if any(span[0] == other[0] and span[1] < other[2] and other[1] < span[2] for other in spans):
            _invalid()
        spans.add(span)
        used.add(value["source_id"])
        passages.append(expected)
    if used != set(sources):
        _invalid()
    return {"version": 1, "offset_unit": "unicode_codepoint", "lookup": lookup,
            "selection": deepcopy(packet["selection"]), "sources": list(sources.values()), "passages": passages}
