"""Default local output sizing fits saved finishing capacity without lowering explicit requests."""
import unittest
from unittest.mock import patch

from backend.provider import ProviderFailure
from tests import test_research_stage_integration as fixtures


class ResearchGenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchStageIntegrationTests(
            "test_provider_and_run_reservation_roll_back_as_one_transaction")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.config["max_output_tokens"] = 8192
        self.fixture.provider.configure(self.fixture.config, "", False)

    def test_default_output_cap_fits_the_authoritative_finishing_slot(self):
        stage = self.fixture.stage("writer")
        calls = []

        def response(*args):
            calls.append(args)
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response):
            result = self.fixture.generate(stage)
        generation = next(args[2] for args in calls if args[1] == "/api/chat")
        self.assertGreater(generation["options"]["num_predict"], 0)
        self.assertLess(generation["options"]["num_predict"], 8192)
        self.assertEqual(set(result), {"reply", "request_id", "input_tokens", "output_tokens"})
        budget = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertEqual(budget["consumed"]["tokens"], 48)
        self.assertEqual(budget["reserved"]["model_calls"], 1)  # Reviewer capacity remains held.

    def test_explicit_output_cap_is_rejected_instead_of_silently_reduced(self):
        stage = self.fixture.stage("writer")
        before = self.fixture.store.get(stage.run_id)["budget"]
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.fixture.provider.generate_research_stage(
                stage, "Return the assigned JSON object.", [{"role": "user", "content": "Synthetic input."}],
                max_output_tokens=8192)
        network.assert_not_called()
        self.assertEqual(self.fixture.provider.read_state()["ledger"], [])
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"], before)

    def test_rejected_model_check_does_not_record_a_model_generation_call(self):
        stage = self.fixture.stage()
        with patch("backend.provider.background_network", side_effect=ProviderFailure("Synthetic model unavailable.", False)) as network:
            with self.assertRaises(ValueError):
                self.fixture.generate(stage)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(network.call_args.args[1], "/api/show")
        self.assertEqual(self.fixture.provider.read_state()["ledger"][-1]["status"], "failed")
        self.assertEqual(self.fixture.provider.usage()["calls"], 0)
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"]["consumed"]["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
