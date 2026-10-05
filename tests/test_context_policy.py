"""Synthetic public-export policy checks without storage, network or credentials."""
import unittest

from backend.context_policy import (
    DEFAULT, MAX_ARCHIVE_ITEMS, approved, configuration, contains_source_secret,
    contains_url_secret, decoded_url_variants, eligible, manifest_hash, public_url, timestamp,
)


class ContextPolicyTests(unittest.TestCase):
    def test_defaults_remain_disabled_and_legacy_policy_stays_restricted(self):
        self.assertEqual(configuration(DEFAULT), DEFAULT)
        self.assertFalse(DEFAULT["enabled"])
        self.assertEqual((DEFAULT["capture_policy"], DEFAULT["max_bytes"], DEFAULT["max_items"]),
                         ("all_public", 10 * 1024 ** 3, 200000))
        legacy = {key: value for key, value in DEFAULT.items() if key != "capture_policy"}
        legacy.update(enabled=True, public_sources=["https://docs.example.org/public/"],
                      max_bytes=256 * 1024 ** 2, max_items=50000)
        self.assertEqual(configuration(legacy), {**legacy, "capture_policy": "approved_sources"})
        self.assertNotIn("capture_policy", legacy)
        with self.assertRaises(ValueError):
            configuration({**DEFAULT, "enabled": True, "capture_policy": "approved_sources"})
        self.assertTrue(configuration({**DEFAULT, "enabled": True})["enabled"])

    def test_configuration_rejects_unknown_fields_and_invalid_types(self):
        invalid = [None, [], {}, {**DEFAULT, "last_capture": {}}, {**DEFAULT, "enabled": 1},
                   {**DEFAULT, "capture_policy": "all_private"}, {**DEFAULT, "public_sources": "https://example.org/"},
                   {**DEFAULT, "public_sources": ["https://example.org/"] * 101}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                configuration(value)

    def test_configured_limits_accept_boundaries_and_reject_bool_float_or_outside_values(self):
        for field, minimum, maximum in (("reuse_hours", 1, 168), ("max_bytes", 1024 ** 2, 100 * 1024 ** 3),
                                         ("max_items", 100, MAX_ARCHIVE_ITEMS)):
            for value in (minimum, maximum):
                with self.subTest(field=field, value=value):
                    self.assertEqual(configuration({**DEFAULT, field: value})[field], value)
            for value in (minimum - 1, maximum + 1, True, float(minimum), str(minimum)):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    configuration({**DEFAULT, field: value})

    def test_public_url_normalizes_hosts_and_normal_ports_only(self):
        cases = (("HTTPS://DOCS.example.org:443", "https://docs.example.org/"),
                 ("https://b\u00fccher.example.org/guide", "https://xn--bcher-kva.example.org/guide"),
                 ("http://WEATHER.example.org:80/forecast", "http://weather.example.org/forecast"))
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(public_url(original, allow_http=True), expected)
        for original in ("http://weather.example.org/forecast", "https://weather.example.org/?date=2026"):
            with self.subTest(original=original), self.assertRaises(ValueError):
                public_url(original)

    def test_public_url_rejects_private_hosts_credentials_and_ambiguous_paths(self):
        invalid = (None, "", "https://localhost/", "https://192.168.1.2/", "https://device.local/",
                   "https://127.0.0.1/", "https://2130706433/", "https://0x7f000001/",
                   "https://user:password@docs.example.org/", "https://docs.example.org:444/",
                   "https://docs.example.org./", "https://docs.example.org/page#", "https://docs.example.org/a\\b",
                   "https://docs.example.org/a/../private", "https://docs.example.org/%2e%2e/private",
                   "https://docs.example.org/%252e%252e/", "https://docs.example.org/%2fprivate",
                   "https://docs.example.org/%5cprivate", "https://docs.example.org/white space")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_url(value, allow_query=True, allow_http=True)

    def test_scope_approval_uses_exact_origin_and_path_components(self):
        scope = ["https://docs.example.org/docs/"]
        for path, expected in (("/docs", True), ("/docs/", True), ("/docs/page", True),
                               ("/docs-private", False), ("/docs?part=1", False)):
            with self.subTest(path=path):
                self.assertEqual(approved("https://docs.example.org" + path, scope), expected)
        for url in ("https://sub.docs.example.org/docs/page", "http://docs.example.org/docs/page"):
            self.assertFalse(approved(url, scope))
        config = {**DEFAULT, "capture_policy": "approved_sources", "public_sources": scope}
        self.assertTrue(eligible("https://docs.example.org/docs/page", config))
        self.assertFalse(eligible("https://unlisted.example.org/docs/page", config))

    def test_query_scopes_preserve_complete_query_identity(self):
        url = "https://weather.example.org/forecast?latitude=59.3&longitude=18.0&date=2026-10-05"
        self.assertEqual(public_url(url, allow_query=True), url)
        canonical = url.replace("weather.example.org", "WEATHER.example.org:443")
        self.assertTrue(approved(canonical, [url]))
        variants = (url.replace("2026-10-05", "2026-10-06"),
                    "https://weather.example.org/forecast?date=2026-10-05&latitude=59.3&longitude=18.0",
                    url.replace("date=2026", "date=%32%30%32%36"), url + "&date=2026-10-05",
                    url.replace("/forecast?", "/forecast/child?"), "https://weather.example.org/forecast")
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertFalse(approved(variant, [url]))
        for scope in ("https://weather.example.org/", "https://weather.example.org/forecast"):
            self.assertFalse(approved(url, [scope]))
        opaque = "https://weather.example.org/forecast?location=%C3%85rsta%20Stockholm&label=%2f&label=%2F"
        self.assertEqual(public_url(opaque, allow_query=True), opaque)
        self.assertEqual(configuration({**DEFAULT, "public_sources": [canonical, url, opaque]})["public_sources"],
                         [url, opaque])

    def test_query_scope_rejects_fragments_controls_and_repeated_encoded_backslashes(self):
        base = "https://weather.example.org/forecast"
        for value in ("\n", "\t", "\x00", "\x7f", "\x85", "%00", "%09", "%0a", "%1f", "%7f",
                      "%85", "%FF", "%C2%85", "%250A", "%255c"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_url(base + "?value=" + value, allow_query=True)
        for url in (base + "?", base + "?date=2026#fragment", base + "?date=2026\\private",
                    base + "/%7F", base + "/%C2%85", base + "/%85"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url, allow_query=True)

    def test_all_public_accepts_public_http_and_distinct_query_urls(self):
        for url in ("http://weather.example.org/forecast?date=2026-10-05",
                    "https://weather.example.org/forecast?date=2026-10-05",
                    "https://weather.example.org/forecast?date=2026-10-06",
                    "https://weather.example.org/forecast?place[0]=Stockholm"):
            with self.subTest(url=url):
                self.assertTrue(eligible(url, DEFAULT))
                self.assertEqual(public_url(url, allow_query=True, allow_http=True), url)

    def test_all_public_excludes_authenticated_signed_personal_and_internal_urls(self):
        base = "https://weather.example.org/forecast"
        rejected = ["https://weather.internal/forecast", "https://weather.lan/forecast",
                    "https://weather.home/forecast", "https://weather.corp/forecast", "https://weather.onion/forecast",
                    "https://weather.example.org/account/profile", "https://weather.example.org/ac%63ount/profile"]
        rejected += [base + "?" + query for query in (
            "access_token=synthetic", "%2574oken=synthetic", "X-Amz-Signature=synthetic", "session_id=synthetic",
            "download_token=synthetic", "email=person%40example.org", "place=person%2540example.org",
            "user_id=synthetic", "place=%250Asecret", "token[0]=synthetic", "api_key[0]=synthetic",
            "session[active]=synthetic", "email[primary]=synthetic", "download_token[primary]=synthetic",
            "filter[password]=synthetic", "query[access_token]=synthetic", "filter[password][0]=synthetic",
            "query[access_token][0]=synthetic", "filter[password][primary]=synthetic")]
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(eligible(url, DEFAULT))
        for suffix in ("internal", "lan", "home", "corp", "onion"):
            host = "weather\uff0e" + "".join(chr(ord(character) + 0xFEE0) for character in suffix)
            url = "https://" + host + "/forecast"
            self.assertEqual(public_url(url), "https://weather." + suffix + "/forecast")
            for config in (DEFAULT, {**DEFAULT, "capture_policy": "approved_sources", "public_sources": [url]}):
                with self.subTest(host=host, policy=config["capture_policy"]):
                    self.assertFalse(eligible(url, config))

    def test_credential_checks_detect_percent_html_and_mixed_encodings(self):
        secret = "synthetic-known-archive-secret"
        percent = "".join("%" + format(ord(character), "02X") for character in secret)
        entities = "".join("&#x" + format(ord(character), "x") + ";" for character in secret)
        for encoded in (secret, percent, percent.lower(), percent.replace("%", "%25"), entities,
                        "".join("&#" + str(ord(character)) + ";" for character in secret),
                        entities.replace("&", "%26"), percent.replace("%", "&#37;")):
            with self.subTest(encoded=encoded):
                self.assertTrue(contains_source_secret("Quoted value " + encoded, (secret, "")))
        for encoded in (secret, percent, percent.replace("%", "%25")):
            for known in ((secret,), iter((secret,))):
                self.assertTrue(contains_url_secret("https://docs.example.org/?value=" + encoded, known))
        benign = "Examples: %41, &lt;div&gt;, &#37;20, invalid %FF and 100% complete."
        self.assertFalse(contains_source_secret(benign, (secret,)))
        self.assertFalse(contains_source_secret(benign, ()))

    def test_decoding_is_bounded_fails_closed_and_does_not_replace_identity(self):
        url = "https://docs.example.org/?location=%C3%85rsta"
        variants = decoded_url_variants(url)
        self.assertEqual(variants[0], url)
        self.assertEqual(variants[-1], "https://docs.example.org/?location=\u00c5rsta")
        deeply_encoded = "%41"
        for _ in range(8):
            deeply_encoded = deeply_encoded.replace("%", "%25")
        for invalid in (None, "x" * 2049, "%FF", deeply_encoded):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decoded_url_variants(invalid)
            self.assertTrue(contains_url_secret(invalid, ("synthetic-secret",)))
        self.assertTrue(contains_source_secret("x" * 131073, ("synthetic-secret",)))
        self.assertTrue(contains_source_secret(deeply_encoded, ("synthetic-secret",)))

    def test_timestamps_normalize_aware_utc_without_inventing_dates(self):
        expected = "2026-10-05T08:00:00+00:00"
        for value in ("2026-10-05T08:00:00Z", expected, "2026-10-05T10:00:00+02:00"):
            self.assertEqual(timestamp(value), expected)
        invalid = (None, 42, "2026-10-05", "2026-10-05T08:00:00", "2026-02-30T08:00:00Z",
                   "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                timestamp(value)

    def test_manifest_hash_pins_unicode_metadata_independently_of_key_order(self):
        manifest = {"title": "\u00c5rsta \u2600", "retrieved_at": "2026-10-05T08:00:00+00:00",
                    "object": {"sha256": "abc", "bytes": 12}}
        expected = "dddad7a72ef7d564f0a355f0de5c6d1ca8217fc05b307e463e53ddb77334b0e1"
        self.assertEqual(manifest_hash(manifest), expected)
        self.assertEqual(manifest_hash({"object": {"bytes": 12, "sha256": "abc"},
                                        "retrieved_at": manifest["retrieved_at"], "title": manifest["title"]}), expected)
        for field, value in (("title", "Changed source"), ("retrieved_at", "2026-10-06T08:00:00+00:00"),
                             ("object", {"sha256": "def", "bytes": 12})):
            self.assertNotEqual(manifest_hash({**manifest, field: value}), expected)
