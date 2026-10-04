"""Reject date substitution even when discovery titles/URLs appear current."""
from datetime import date
import unittest
from backend.context_tools import (forecast_date_supported, gate_forecast_sources,
                                   is_weather_request, requested_calendar_dates)


class ForecastDateTests(unittest.TestCase):
    def source(self, text, **fields):
        return {"title": "Public weather excerpt", "url": "https://weather.example.org/?date=2026-10-05",
                "retrieved_at": "2026-10-04T14:38:40+00:00", "content": text, **fields}

    def test_stale_relative_day_and_acquisition_metadata_cannot_date_forecast(self):
        for body in ("I morgon\n2/5 Max23°C Min11°C", "Monday October 5. Morning11°C afternoon15°C.",
                     "Tomorrow:23°C. https://weather.example.org/?date=2026-10-05", "Tomorrow23°C"):
            with self.subTest(body=body):
                self.assertFalse(forecast_date_supported(self.source(body), ("2026-10-05",)))

    def test_source_title_alone_cannot_supply_requested_date(self):
        title = "Forecast for2026-10-05"
        self.assertFalse(forecast_date_supported(self.source(title + "\nTomorrow23°C", title=title), ("2026-10-05",)))

    def test_uppercase_links_leading_blank_titles_and_publication_dates_are_not_forecast_dates(self):
        title = "Weather for 2026-10-05"
        for body in ("Source: HTTPS://example.org/forecast?date=2026-10-05\nMay 2: 23°C and 11°C",
                     "\n" + title + "\nMay 2: 23°C and 11°C",
                     "Published 2026-10-05\nMay 2: 23°C and 11°C",
                     "## Uppdaterad 2026-10-05\nMay 2: 23°C and 11°C"):
            with self.subTest(body=body):
                self.assertFalse(forecast_date_supported(self.source(body, title=title), ("2026-10-05",)))

    def test_unambiguous_complete_dates_in_body_support_that_day_only(self):
        for body in ("Forecast 2026-10-05: 1.5mm", "Forecast 5 October 2026: 1.5mm", "Forecast October 5, 2026: 1.5mm",
                     "Forecast 5 oktober 2026: 1.5mm"):
            with self.subTest(body=body):
                self.assertTrue(forecast_date_supported(self.source(body), ("2026-10-05",)))
                self.assertFalse(forecast_date_supported(self.source(body), ("2026-10-06",)))

    def test_ambiguous_numeric_dates_and_invalid_days_are_not_guessed(self):
        for body in ("Forecast5/10/2026:1.5mm", "Forecast2026-02-30:1.5mm", "ForecastFebruary30,2026:1.5mm"):
            self.assertFalse(forecast_date_supported(self.source(body), ("2026-10-05",)))
        self.assertTrue(forecast_date_supported(self.source("Forecast 13/10/2026: 1.5mm"), ("2026-10-13",)))

    def test_gate_keeps_original_identity_and_does_not_mutate_acquisitions(self):
        old = self.source("Tomorrow2/5:23°C")
        current = self.source("Forecast2026-10-05:11°C and1.5mm")
        eligible, report = gate_forecast_sources([old, current], ("2026-10-05",))
        self.assertEqual(eligible, [current])
        self.assertIs(eligible[0], current)
        self.assertEqual(report, {"requested_dates": ["2026-10-05"], "supported_source_count": 1, "excluded_source_count": 1})
        self.assertEqual(old["content"], "Tomorrow2/5:23°C")

    def test_multiple_requested_days_require_body_evidence_for_each(self):
        source = self.source("Forecast2026-10-05:11°C")
        self.assertFalse(forecast_date_supported(source, ("2026-10-05", "2026-10-06")))
        source["content"] += "\nForecast2026-10-06:12°C"
        self.assertTrue(forecast_date_supported(source, ("2026-10-05", "2026-10-06")))

    def test_relative_and_explicit_days_use_the_same_local_snapshot(self):
        self.assertEqual(requested_calendar_dates("Weather tomorrow,2026-10-05", date(2026, 10, 4)), ("2026-10-05",))
        self.assertEqual(requested_calendar_dates("Vädret2026-10-05", date(2026, 10, 4)), ("2026-10-05",))
        self.assertTrue(is_weather_request("vädret i Årsta"))
        self.assertFalse(is_weather_request("Translate documentation"))
        with self.assertRaises(ValueError):
            requested_calendar_dates("Weather2026-02-30", date(2026, 10, 4))
        with self.assertRaises(ValueError):
            requested_calendar_dates("Weather2026-10-01,2026-10-02 and2026-10-03", date(2026, 10, 4))

    def test_named_requested_dates_use_the_same_gate_without_guessing_numeric_order(self):
        for text in ("Weather on 5 October 2026", "Vädret 5 oktober 2026", "Forecast October 5, 2026"):
            self.assertEqual(requested_calendar_dates(text, date(2026, 10, 4)), ("2026-10-05",))
        self.assertEqual(requested_calendar_dates("Weather on 13/10/2026", date(2026, 10, 4)), ("2026-10-13",))
        self.assertEqual(requested_calendar_dates("Weather on 05/5/2026", date(2026, 10, 4)), ("2026-05-05",))
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            requested_calendar_dates("Weather on 5/10/2026", date(2026, 10, 4))
