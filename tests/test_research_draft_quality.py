"""The opt-in draft oracle rejects typed but unsupported fixture prose offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unicodedata
import unittest

from backend.research_draft import gap_draft, has_support, validate_response
from scripts.check_research_evidence import check_run, load_cases
from tests.test_research_evidence_quality import canonical


# Explicit test inputs remain independent of the oracle's matching grammar.
QUANTITY_ORDERS = ("{code} {value} {scale}", "{value} {code} {scale}", "{value} {scale} {code}",
                   "{code} {scale} {value}", "{scale} {code} {value}", "{scale} {value} {code}")


class ResearchDraftQualityTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case for case in load_cases()}

    def draft_run(self, case):
        run, sources, loader, _, _ = canonical(case)
        supported = has_support(run["evidence_review"])
        if supported:
            raw = {"version": 1, "items": {row["item_id"]: {
                "kind": "fact", "text": row["supports"][0]["quote"],
                "supports": [{key: row["supports"][0][key] for key in ("passage_id", "quote")}],
                "calculation": None} for row in run["evidence_review"]["items"] if row["state"] == "covered"}}
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
        else:
            run["draft"] = gap_draft(run["assessment"], run["evidence_review"])
        run.update(workflow="research_draft", state="draft" if supported else "partial")
        run["budget"]["consumed"]["model_calls"] += int(supported)
        return run, sources, loader

    def replace_fact(self, run, text, *, calculation=None):
        row = run["draft"]["items"][0]
        raw = {"version": 1, "items": {row["item_id"]: {
            "kind": "calculation" if calculation else "fact", "text": text,
            "supports": [{key: support[key] for key in ("passage_id", "quote")} for support in row["supports"]],
            "calculation": calculation}}}
        run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])

    def check_text(self, case, text, *, error=None, calculation=None):
        run, sources, loader = self.draft_run(case)
        self.replace_fact(run, text, calculation=calculation)
        if error is None:
            check_run(case, run, sources, loader, workflow="research_draft")
        else:
            with self.assertRaisesRegex(AssertionError, error):
                check_run(case, run, sources, loader, workflow="research_draft")

    def case_with_value(self, value):
        case = deepcopy(self.cases["historical_tail"])
        case["sources"][0]["quote"] = "Acme revenue for FY2025 was USD " + value + " million."
        case["expected"]["support_fragments"] = [["FY2025", "USD", value]]
        case["expected"]["draft_quantities"][0]["value"] = value
        return case

    def test_exponent_mantissa_separators_cannot_split_into_supported_numbers(self):
        case = self.cases["historical_tail"]
        separators = (".", " .", ". ", " . ", "..", ". .", ".,", ",.", "_", "/",
                      "\uff0e", "\u2024", "\u0338")
        for separator in separators:
            for exponent, spacing, sign in (("e", "", ""), ("E", "", "+"),
                                             ("e", " ", "-"), ("E", "\t", ""),
                                             ("\uff45", " ", "")):
                amount = "42" + separator + exponent + spacing + sign + spacing + "2025"
                with self.subTest(amount=ascii(amount)):
                    self.check_text(case, case["sources"][0]["quote"] + " Another amount was USD " + amount + ".", error="numeric")
        for sentence in ("The amount was 42. 2025 was the reporting year.",
                         "The amount was 42.\n2025 was the reporting year."):
            with self.subTest(control=sentence):
                self.check_text(case, case["sources"][0]["quote"] + " " + sentence)

    def test_explicit_quantity_orders_and_currency_wrappers_keep_whole_units(self):
        case = self.cases["historical_tail"]
        orders = QUANTITY_ORDERS + ("{value} {scale} in {code}",)
        # These expected outcomes are independent of the candidate grammar:
        # the only supported fixture tuple is (42, USD, million).
        variants = (("USD", "million", True), ("uSd", "MillionS", True),
                    ("EUR", "million", False), ("eur", "MillionS", False),
                    ("GBP", "million", False), ("USD", "billion", False),
                    ("USD", "BILLIONS", False), ("EUR", "trillion", False))
        for code, scale, supported in variants:
            for wrapper in ("{}", "({})", "( {} )", "(({}))", "( ( {} ) )"):
                for order in orders:
                    amount = order.format(code=wrapper.format(code), value="42", scale=scale)
                    for appended in (False, True):
                        prefix = case["sources"][0]["quote"] + " Another amount was " if appended else "Acme revenue for FY2025 was "
                        with self.subTest(amount=amount, appended=appended, supported=supported):
                            self.check_text(case, prefix + amount + ".", error=None if supported else "quantity|currency|scale")

    def test_wrapped_unscaled_currency_pairs_are_checked_before_row_fallback(self):
        case = self.cases["historical_tail"]
        for code, supported in (("USD", True), ("usd", True), ("EUR", False),
                                ("eUr", False), ("GBP", False)):
            for wrapper in ("({})", "( {} )", "(({}))"):
                for order in ("{code} 42", "42 {code}", "42 in {code}"):
                    amount = order.format(code=wrapper.format(code))
                    with self.subTest(amount=amount, supported=supported):
                        self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".",
                                        error=None if supported else "currency")

    def test_currency_wrapper_matrix_refuses_malformed_or_unsupported_wrappers(self):
        case = self.cases["historical_tail"]
        wrappers = (("{}", True), ("({})", True), ("( {} )", True), ("(({}))", True),
                    ("({}", False), ("{})", False), ("(({})", False), ("({}))", False),
                    ("[{}]", False), ("{{{}}}", False))
        for code in ("USD", "EUR"):
            for wrapper, valid in wrappers:
                amount = "42 million " + wrapper.format(code)
                with self.subTest(amount=amount, valid=valid):
                    self.check_text(case, "Acme revenue for FY2025 was " + amount + ".",
                                    error=None if valid and code == "USD" else "currency|quantity")

    def test_all_quantity_orders_keep_accounting_amount_parentheses_unsupported(self):
        case = self.cases["historical_tail"]
        orders = QUANTITY_ORDERS + ("{value} {scale} in {code}",)
        for code in ("USD", "(USD)", "((USD))"):
            for scale in ("million", "MILLIONS"):
                for order in orders:
                    amount = order.format(code=code, value="42", scale=scale)
                    for wrapper in ("({})", "( {} )", "(({}))"):
                        with self.subTest(amount=wrapper.format(amount)):
                            self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + wrapper.format(amount) + ".",
                                            error="numeric")

    def test_code_only_wrappers_cannot_hide_prefix_or_postfix_amount_signs(self):
        case = self.cases["historical_tail"]
        orders = ("-{code} 42 million", "- {code}42 million", "+{code} 42 million",
                  "42 million {code}-", "42 million {code} +", "42 {code}- million",
                  "42 {code} + million", "42 million in {code}-", "- {code} million 42",
                  "- million {code} 42", "- in {code} 42 million", "- {code} MILLIONS 42",
                  "+ million {code} 42")
        for code in ("USD", "usd", "uSd"):
            for wrapper in ("({})", "( {} )", "(({}))", "( ( {} ) )"):
                for order in orders:
                    amount = order.format(code=wrapper.format(code))
                    with self.subTest(amount=amount):
                        self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="numeric")

    def test_known_scales_in_unsupported_wrappers_cannot_disappear_from_extra_quantities(self):
        case = self.cases["historical_tail"]
        orders = QUANTITY_ORDERS + ("{code}{value} {scale}", "{value} {scale}")
        wrappers = ("({})", "( {} )", "(({}))", "[{}]", "{{{}}}",
                    "\uff08{}\uff09", "\uff3b{}\uff3d", "\u3010{}\u3011")
        for scale in ("million", "billion", "BILLIONS"):
            for wrapper in wrappers:
                for code in ("USD", "(USD)"):
                    for order in orders:
                        amount = order.format(code=code, value="42", scale=wrapper.format(scale))
                        with self.subTest(amount=ascii(amount)):
                            self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".",
                                            error="currency|scale|numeric")

    def test_separated_postfix_sign_cannot_hide_when_units_precede_the_value(self):
        case = self.cases["historical_tail"]
        orders = QUANTITY_ORDERS + ("{code} {scale}{value}", "{scale} {code}{value}", "{value}")
        for code in ("USD", "uSd"):
            for wrapper in ("{}", "({})", "(({}))"):
                for order in orders:
                    for spacing, sign in ((" ", "-"), ("\t", "+"), ("\n", "-")):
                        amount = order.format(code=wrapper.format(code), value="42", scale="MillionS") + spacing + sign
                        with self.subTest(amount=ascii(amount)):
                            self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="numeric")

    def test_whole_signed_literals_keep_all_quantity_orders_without_trailing_signs(self):
        for value in ("42", "-42", "+42", "42.5"):
            case = self.case_with_value(value)
            for wrapper in ("{}", "({})", "(({}))"):
                for order in QUANTITY_ORDERS:
                    amount = order.format(code=wrapper.format("uSd"), value=value, scale="MillionS")
                    with self.subTest(amount=amount):
                        self.check_text(case, "Acme revenue (FY2025) was " + amount + ".")

    def test_unicode_and_mixed_currency_wrappers_cannot_hide_explicit_units(self):
        case = self.cases["historical_tail"]
        orders = QUANTITY_ORDERS + ("{value} {scale} in {code}", "{code} {value}",
                                    "{value} {code}", "{value} in {code}")
        wrappers = ("\uff08{}\uff09", "\uff3b{}\uff3d", "\uff5b{}\uff5d", "\u3010{}\u3011",
                    "\u2768{}\u2769", "(\uff08{}\uff09)", "\uff08({})\uff09",
                    "\uff08{})", "({}\uff09")
        for code in ("USD", "usd", "EUR", "eur"):
            for wrapper in wrappers:
                for order in orders:
                    amount = order.format(code=wrapper.format(code), value="42", scale="million")
                    with self.subTest(amount=ascii(amount)):
                        self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="currency|numeric")

    def test_unicode_amount_wrappers_remain_accounting_not_positive_plain_values(self):
        case = self.cases["historical_tail"]
        for amount in ("USD 42 million", "42 million USD", "42 USD million", "USD million 42",
                       "million USD 42", "million 42 USD", "42 million (USD)", "42"):
            for wrapper in ("\uff08{}\uff09", "\uff3b{}\uff3d", "\u3010{}\u3011", "\uff08{})", "({}\uff09"):
                with self.subTest(amount=ascii(wrapper.format(amount))):
                    self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + wrapper.format(amount) + ".", error="numeric")

    def test_supported_ascii_code_wrappers_preserve_whole_signed_values_and_prose_brackets(self):
        for value in ("42", "-42", "+42", "42.5"):
            case = self.case_with_value(value)
            for wrapper in ("({})", "( {} )", "(({}))"):
                for amount in (wrapper.format("uSd") + " " + value + " MillionS",
                               value + " million " + wrapper.format("USD"),
                               value + " million in " + wrapper.format("usd")):
                    for text in ("Acme revenue \uff08FY2025\uff09 was " + amount + ". A note (historical evidence) follows.",
                                 "Acme revenue was " + amount + " (FY2025).",
                                 "Acme revenue - as reported - for FY2025 was " + amount + "."):
                        with self.subTest(text=text):
                            self.check_text(case, text)

    def test_grouped_and_spaced_exponent_candidates_cannot_borrow_supported_digits(self):
        case = self.cases["historical_tail"]
        amounts = ["42" + separator + "2025" for separator in
                   ("_", "__", "_ _", "_ ", " _", " _ ", "\t_\t", "\u00a0_\u00a0")]
        amounts += ["42" + spacing + exponent + gap + "2025"
                    for spacing, gap in (("", " "), (" ", ""), (" ", " "), ("\t", "\t"))
                    for exponent in ("e", "E")]
        for amount in amounts:
            with self.subTest(amount=ascii(amount)):
                self.check_text(case, case["sources"][0]["quote"] + " Another amount was USD " + amount + ".", error="numeric")

    def test_separator_clusters_cannot_borrow_revenue_and_reporting_period_digits(self):
        case = self.cases["historical_tail"]
        separators = (" / ", " , ", " /, ", ", /", " / / ", " . ", "\t/\t",
                      "\u00a0,\u00a0", "\uff0e", "\u2024", "\uff0c", "\uff0f", "\u2044")
        for separator in separators:
            with self.subTest(separator=ascii(separator)):
                self.check_text(case, case["sources"][0]["quote"] + " Another amount was 42" + separator + "2025.", error="numeric")
        for whitespace in (" ", "\n", "\r\n", "\t"):
            with self.subTest(sentence=ascii(whitespace)):
                self.check_text(case, case["sources"][0]["quote"] + " The amount was 42." + whitespace
                              + "2025 was the reporting year.")

    def test_nonwhitespace_controls_cannot_hide_a_numeric_sign(self):
        case = self.cases["historical_tail"]
        for control in ("\x01", "\x08", "\x1b", "\x7f", "\x80", "\x9f"):
            with self.subTest(control=ascii(control)):
                self.check_text(case, case["sources"][0]["quote"] + " Another amount was USD -" + control + "42 million.", error="numeric")
        for whitespace in ("\t", "\n", "\r\n"):
            with self.subTest(control=ascii(whitespace)):
                self.check_text(case, "Acme revenue for FY2025 was USD" + whitespace + "42 million.")

    def test_case_varied_plural_scales_cannot_disappear_from_quantity_checks(self):
        case = self.cases["historical_tail"]
        for scale in ("BILLIONS", "BillionS", "billionS", "THOUSANDS", "TRILLIONS"):
            for amount in ("USD 42 " + scale, "42 " + scale + " USD", "42 " + scale):
                with self.subTest(amount=amount):
                    self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="quantity|scale")
        for amount in ("USD 42 MILLIONS", "42 MillionS USD", "42 millionS"):
            with self.subTest(control=amount):
                self.check_text(case, "Acme revenue for FY2025 was " + amount + ".")

    def test_explicit_currencies_cannot_be_replaced_by_copied_row_currency(self):
        case = self.cases["historical_tail"]
        for label in ("EUR", "eur", "eUr", "GBP"):
            for amount in ("42 million in " + label, label + " 42", "42 " + label, "42 in " + label):
                for prefix in ("", case["sources"][0]["quote"] + " Another amount was "):
                    with self.subTest(amount=amount, appended=bool(prefix)):
                        self.check_text(case, prefix + "Acme revenue for FY2025 was " + amount + ".", error="quantity|currency")
        for text in ("Acme revenue for FY2025 was 42 million in USD.",
                     "Acme revenue for FY2025 was 42 million in usd.",
                     case["sources"][0]["quote"] + " The amount was USD 42.",
                     case["sources"][0]["quote"] + " The amount was 42 USD."):
            with self.subTest(control=text):
                self.check_text(case, text)

    def test_numeric_combining_marks_cannot_join_supported_digit_fragments(self):
        case = self.cases["historical_tail"]
        for mark in ("\ufe0f", "\u0338", "\u0301", "\u20e3"):
            for spacing in ("", " ", "\t", "\u00a0"):
                for separator in (mark, "_" + mark + "_"):
                    amount = "42" + spacing + separator + spacing + "2025"
                    with self.subTest(amount=ascii(amount)):
                        self.check_text(case, case["sources"][0]["quote"] + " Another amount was USD " + amount + ".", error="numeric")
        for prose in ("The cafe\u0301 note is prose.", "The cafe\u0301 42 note is prose.",
                      "The cafe\u0301\u0327 42 note is prose.", "The cafe\u0301\t42 note is prose."):
            with self.subTest(prose=ascii(prose)):
                self.check_text(case, case["sources"][0]["quote"] + " " + prose)

    def test_postfix_sign_cannot_hide_after_scale_and_currency_labels(self):
        case = self.cases["historical_tail"]
        for amount in ("42 million USD-", "42 million USD -", "42 million usd-",
                       "42 millionUSD-", "42millionUSD-", "42 million USD+",
                       "42 billion USD-", "42 million\tUSD\t-"):
            with self.subTest(amount=amount):
                self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="numeric")

    def test_plural_scale_and_currency_cannot_hide_a_postfix_sign(self):
        for scale in ("thousand", "million", "billion", "trillion"):
            case = deepcopy(self.cases["historical_tail"])
            case["sources"][0]["quote"] = "Acme revenue for FY2025 was USD 42 " + scale + "."
            case["expected"]["draft_quantities"][0]["scale"] = scale
            for separator in (" ", "", "\t"):
                amount = "42 " + scale + "s" + separator + "USD-"
                with self.subTest(amount=amount):
                    self.check_text(case, case["sources"][0]["quote"] + " Another amount was " + amount + ".", error="numeric")

    def test_leading_arabic_separator_cannot_authorize_its_integer_suffix(self):
        case = self.cases["historical_tail"]
        for separator in ("\u066b", "\u066c"):
            for sign in ("", "-", "+", "- ", "+\t", "-\u00a0"):
                for spacing in ("", " ", "\t", "\u00a0", "\u202f"):
                    amount = sign + separator + spacing + "42"
                    with self.subTest(amount=ascii(amount)):
                        self.check_text(case, "Acme revenue for FY2025 was USD 42 million. "
                                      "Another amount was USD " + amount + " million.", error="numeric")

    def test_signed_ascii_decimal_separator_cannot_hide_before_whitespace(self):
        case = self.cases["historical_tail"]
        for sign in ("-", "+"):
            for separator in (".", ","):
                for sign_spacing in ("", " "):
                    for spacing in (" ", "\t", "\u00a0", "\u202f"):
                        amount = sign + sign_spacing + separator + spacing + "42"
                        with self.subTest(amount=ascii(amount)):
                            self.check_text(case, "Acme revenue for FY2025 was USD 42 million. "
                                          "Another amount was USD " + amount + " million.", error="numeric")

    def test_plain_prose_period_and_comma_before_supported_number_remain_valid(self):
        case = self.cases["historical_tail"]
        for text in ("Acme revenue for FY2025 was reported. 42 million USD was the amount.",
                     "Acme revenue for FY2025 was the reported amount, 42 million USD."):
            with self.subTest(text=text):
                self.check_text(case, text)

    def test_whitespace_separated_sign_cannot_borrow_a_supported_unsigned_value(self):
        for value in ("42", "0.42"):
            case = self.case_with_value(value)
            for sign in ("-", "+"):
                for spacing in (" ", "\t", "\u00a0", "\u202f"):
                    amount = sign + spacing + value
                    with self.subTest(amount=ascii(amount)):
                        self.check_text(case, case["sources"][0]["quote"] +
                                      " Another amount was USD " + amount + " million.", error="numeric")

    def test_currency_glyphs_cannot_inherit_the_copied_usd_qualifier(self):
        case = self.cases["historical_tail"]
        symbols = [chr(point) for point in range(0x110000) if unicodedata.category(chr(point)) == "Sc"]
        self.assertTrue({"$", "\u20ac", "\u00a3", "\u20b9", "\ufdfc"} <= set(symbols))
        for symbol in symbols:
            for side, amount in (("prefix", symbol + "42 million"), ("suffix", "42 million " + symbol)):
                with self.subTest(symbol=f"U+{ord(symbol):04X}", side=side):
                    self.check_text(case, "Acme revenue for FY2025 was USD 42 million. "
                                  "Another amount was " + amount + ".", error="numeric")

    def test_source_backed_plain_signed_values_and_prose_punctuation_remain_valid(self):
        for value in ("42", "+42", "-42", "0.42", "+0.42", "-0.42"):
            case = self.case_with_value(value)
            with self.subTest(value=value):
                self.check_text(case, "Acme revenue (FY2025) - as reported - was USD " + value + " million.")

    def test_changed_quantity_scale_cannot_borrow_a_unit_elsewhere_in_the_row(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue for FY2025 was USD 42 billion.",
            "Acme revenue for FY2025 was USD 42 thousand.",
            "Acme revenue for FY2025 was USD 42 billion. The source uses the word million.",
            "Acme revenue for FY2025 was USD 42 million, also stated as USD 42 billion.",
            "Acme revenue for FY2025 was USD 42 million, also stated as 42 billion.",
            "Acme revenue for FY2025 was EUR 42 million.",
            "Acme revenue for FY2025 was USD 42 million, also stated as EUR 42 million.",
        ):
            with self.subTest(text=text):
                self.check_text(case, text, error="quantity")

    def test_unicode_and_accounting_numbers_cannot_disappear_from_provenance(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue for FY2025 was USD \u221242 million.",
            "Acme revenue for FY2025 was USD \u207b42 million.",
            "Acme revenue for FY2025 was USD (42) million.",
            "Acme revenue for FY2025 was (USD 42 million).",
            "Acme revenue for FY2025 was (USD 42 Million).",
            "Acme revenue for FY2025 was -USD 42 million.",
            "Acme revenue for FY2025 was -$42 million, in USD.",
            "Acme revenue for FY2025 was \u2212USD 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42-.",
            "Acme revenue for FY2025 was USD 42 million-.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was -usd 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was \u2212eur 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42usd-.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42 USD\u2212.",
            "Acme revenue for FY2025 was USD 42 million\u2212.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42$\u2212.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was \u2212\u20b942 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was -\u20b942 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD \uff19\uff19 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD 9\u200b9 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD 42,2025 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42, 2025.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42,\t2025.",
        ):
            with self.subTest(text=ascii(text)):
                self.check_text(case, text, error="numeric")

    def test_plain_punctuation_and_parenthesized_qualifiers_preserve_expected_quantity(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue (FY2025) was USD 42 million; this is historical evidence.",
            "Acme revenue for FY2025 - as reported - was USD 42 million.",
            "Acme revenue for FY2025 was 42 million USD.",
            "Acme revenue was 42 million.",
        ):
            with self.subTest(text=text):
                self.check_text(case, text)

    def test_conflicting_fixture_currency_is_rejected_independently_of_label_case(self):
        case = self.cases["historical_tail"]
        for label in ("EUR", "eur", "eUr"):
            for amount in (f"{label} 42 million", f"42 million {label}", f"42million{label}"):
                with self.subTest(amount=amount):
                    self.check_text(case, "Acme revenue for FY2025 was " + amount + ".", error="quantity")

    def test_correct_fixture_currency_case_and_copied_qualifier_remain_valid(self):
        case = self.cases["historical_tail"]
        amounts = [f"{label} 42 million" for label in ("USD", "usd", "uSd")]
        amounts += [f"42 million {label}" for label in ("USD", "usd", "uSd")]
        amounts += ["42 million", "42millionusd"]
        for amount in amounts:
            with self.subTest(amount=amount):
                self.check_text(case, "Acme revenue was " + amount + ".")

    def test_unknown_uppercase_currency_keeps_legacy_conflict_guard(self):
        case = self.cases["historical_tail"]
        self.assertNotIn("GBP", case["expected"]["draft_currency_codes"])
        self.assertNotIn("JPY", case["expected"]["draft_currency_codes"])
        for label in ("GBP", "JPY"):
            for amount in (f"{label} 42 million", f"42 million {label}", f"42million{label}"):
                with self.subTest(amount=amount):
                    self.check_text(case, "Acme revenue for FY2025 was " + amount + ".", error="quantity")

    def test_leading_decimal_cannot_borrow_the_supported_integer_suffix(self):
        case = self.cases["historical_tail"]
        for amount in (".42", "-.42", "+.42", ",42", "-,42", "1..42"):
            with self.subTest(amount=amount):
                self.check_text(case, "Acme revenue for FY2025 was USD 42 million. "
                              "Another amount was USD " + amount + " million.", error="numeric")

    def test_complete_plain_decimal_requires_its_own_exact_fixture_support(self):
        for value in ("0.42", "+0.42"):
            case = self.case_with_value(value)
            run, sources, loader = self.draft_run(case)
            with self.subTest(value=value):
                check_run(case, run, sources, loader, workflow="research_draft")
            # A complete decimal must not fall back to its integer suffix even
            # when the row still cites the original supported value of 42.
            original = self.cases["historical_tail"]
            with self.subTest(unsupported=value):
                self.check_text(original, "Acme revenue for FY2025 was USD 42 million. "
                              "Another amount was USD " + value + " million.", error="numeric")

    def test_quantity_fixture_requires_a_valid_bounded_currency_vocabulary(self):
        bad = (None, "USD", [], ["EUR"], ["USD", "USD"], ["usd", "EUR"],
               ["USD", "EURO"], ["USD", 7],
               ["USD"] + ["AA" + chr(ord("A") + index) for index in range(26)] + ["AB" + chr(ord("A") + index) for index in range(6)])
        with tempfile.TemporaryDirectory(prefix="maestro-draft-currency-vocabulary-") as temporary:
            path = Path(temporary) / "corpus.json"
            for vocabulary in bad:
                case = deepcopy(self.cases["historical_tail"])
                case["expected"]["draft_currency_codes"] = vocabulary
                path.write_text(json.dumps({"version": 2, "cases": [case]}), encoding="utf-8")
                with self.subTest(vocabulary=vocabulary), self.assertRaisesRegex(AssertionError, "currency"):
                    load_cases(path)
            case["expected"].pop("draft_currency_codes")
            path.write_text(json.dumps({"version": 2, "cases": [case]}), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "currency"):
                load_cases(path)

    def test_server_calculation_retains_scale_and_cannot_authorize_a_different_one(self):
        case = deepcopy(self.cases["historical_tail"])
        case["sources"][0]["quote"] += " Amounts recorded."
        calculation = {"operation": "sum", "operands": [
            {"value": "42", "support_index": 0}, {"value": "42", "support_index": 0}], "precision": 2}
        for scale in ("million", "billion"):
            text = "Acme revenue for FY2025 was USD 42 million; twice that amount is USD 84.00 " + scale + "."
            with self.subTest(scale=scale):
                self.check_text(case, text, calculation=calculation, error=None if scale == "million" else "quantity")

    def test_all_frozen_cases_preserve_values_and_gaps_with_expected_call_counts(self):
        for case in self.cases.values():
            with self.subTest(case=case["id"]):
                run, sources, loader = self.draft_run(case)
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_typed_wrong_value_and_extra_unsupported_figure_fail_semantic_oracle(self):
        case = self.cases["historical_tail"]
        for text in ("Acme revenue for FY2025 was USD 99 million.",
                     "Acme revenue for FY2025 was USD 42 million and profit was USD 99 million."):
            # Prose truth is the later final-review gate; production validation
            # deliberately does not claim to prove arbitrary natural language.
            with self.subTest(text=text):
                self.check_text(case, text, error="expected fixture value|unsupported numeric")

    def test_missing_draft_changed_binding_hidden_calls_and_holds_fail(self):
        case = self.cases["partial_revenue_only"]
        for change in (lambda run: run.update(draft=None),
                       lambda run: run["draft"].update(coverage_hash="f" * 64),
                       lambda run: run["budget"]["consumed"].update(model_calls=4),
                       lambda run: run["budget"]["reserved"].update(tokens=1)):
            run, sources, loader = self.draft_run(case)
            change(run)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_numbers_from_uncited_sources_other_rows_or_unrelated_request_text_fail(self):
        for origin in ("uncited_source", "other_row", "unrelated_request"):
            case = deepcopy(self.cases["partial_revenue_only" if origin == "other_row" else "historical_tail"])
            if origin == "unrelated_request":
                case["request"] += " Internal reference 99 is not a financial value."
            else:
                case["sources"].append({
                    "title": "Synthetic Acme other disclosure",
                    "url": "https://reports.example.org/acme/other",
                    "retrieved_at": "2026-01-11T12:00:00+00:00",
                    "quote": "Acme operating profit for FY2025 was USD 99 million.",
                })
                if origin == "other_row":
                    case["expected"]["items"][1].update(
                        states=["covered"], minimum_sources=1,
                        support_fragments=[["operating profit", "FY2025", "USD", "99"]])
            run, sources, loader = self.draft_run(case)
            # This remains canonically cited, but 99 is outside the revenue
            # row's citations and qualifiers. Other packet/request numbers
            # cannot supply numeric provenance for this row's added prose.
            raw = {"version": 1, "items": {row["item_id"]: {
                "kind": row["kind"], "text": row["text"],
                "supports": [{key: support[key] for key in ("passage_id", "quote")} for support in row["supports"]],
                "calculation": None} for row in run["draft"]["items"]}}
            raw["items"]["i1"]["text"] += " Operating profit was USD 99 million."
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
            with self.subTest(origin=origin), self.assertRaisesRegex(AssertionError, "unsupported numeric literal"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_same_row_citations_qualifiers_and_server_calculation_remain_allowed(self):
        case = deepcopy(self.cases["historical_tail"])
        case["sources"][0]["quote"] += " Amounts recorded."
        for calculated in (False, True):
            run, sources, loader = self.draft_run(case)
            support = run["draft"]["items"][0]["supports"][0]
            # FY2025 is supplied only by this requirement's copied qualifier;
            # the narrower citation still supplies the value and currency.
            raw = {"version": 1, "items": {"i1": {
                "kind": "calculation" if calculated else "fact",
                "text": "Acme revenue for FY2025 was USD 42 million." +
                        (" Twice that reported amount is USD 84.00 million." if calculated else ""),
                "supports": [{"passage_id": support["passage_id"], "quote": "USD 42 million."}],
                "calculation": {"operation": "sum", "operands": [
                    {"value": "42", "support_index": 0}, {"value": "42", "support_index": 0}],
                    "precision": 2} if calculated else None,
            }}}
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
            with self.subTest(calculated=calculated):
                if calculated:
                    self.assertEqual(run["draft"]["items"][0]["calculation"]["result"], "84.00")
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_malformed_draft_shapes_fail_with_controlled_non_echoing_assertion(self):
        case = self.cases["historical_tail"]
        marker = "UNTRUSTED_DRAFT_CONTENT"
        changes = (
            lambda run: run.update(draft=[{"state": "unreviewed", "text": marker}]),
            lambda run: run.update(draft=marker),
            lambda run: run.update(draft=7),
            lambda run: run["draft"].update(items={"i1": marker}),
            lambda run: run["draft"].update(items=[marker]),
            lambda run: run["draft"]["items"][0].update(supports=[marker]),
            lambda run: run["draft"]["items"][0].update(calculation=marker),
        )
        for index, change in enumerate(changes):
            run, sources, loader = self.draft_run(case)
            change(run)
            with self.subTest(shape=index):
                with self.assertRaisesRegex(AssertionError, "^Missing or noncanonical draft$") as caught:
                    check_run(case, run, sources, loader, workflow="research_draft")
                self.assertNotIn(marker, str(caught.exception))

    def test_capacity_gap_is_not_counted_as_a_successful_supported_draft(self):
        case = self.cases["historical_tail"]
        run, sources, loader = self.draft_run(case)
        run["draft"] = gap_draft(run["assessment"], run["evidence_review"], reason="writer_capacity")
        run["state"] = "partial"
        run["budget"]["consumed"]["model_calls"] = 2
        with self.assertRaisesRegex(AssertionError, "supported fixture did not produce"):
            check_run(case, run, sources, loader, workflow="research_draft")


if __name__ == "__main__":
    unittest.main()
