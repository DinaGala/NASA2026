import json
import typing
import unittest
from pathlib import Path

from fastapi import testclient

from src.api.app import app
from src.contracts import FlameConfig, Outcome, ScenarioResponse, VerdictResponse

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
SCHEMA_SQL = Path(__file__).resolve().parents[1] / "scripts" / "apply_schema.py"


class FixtureValidationTests(unittest.TestCase):
    def test_every_fixture_validates_against_its_model(self):
        models = {
            "scenario_moon.json": ScenarioResponse,
            "scenario_moon_solid.json": ScenarioResponse,
            "verdict_tested.json": VerdictResponse,
            "verdict_untested.json": VerdictResponse,
        }
        self.assertEqual(sorted(models), sorted(p.name for p in FIXTURES.glob("*.json")))
        for name, model in models.items():
            with self.subTest(fixture=name):
                model.model_validate_json((FIXTURES / name).read_text())

    def test_moon_fixture_holds_the_17_flex_runs(self):
        moon = ScenarioResponse.model_validate_json((FIXTURES / "scenario_moon.json").read_text())
        nearby = [run for run in moon.points if run.distance <= 1]
        self.assertTrue(moon.in_envelope)
        self.assertEqual(moon.n_nearby, 17)
        self.assertEqual({run.psi_id for run in nearby}, {"PSI-69"})

    def test_moon_solid_fixture_is_outside_the_envelope(self):
        solid = ScenarioResponse.model_validate_json(
            (FIXTURES / "scenario_moon_solid.json").read_text()
        )
        self.assertFalse(solid.in_envelope)
        self.assertEqual(solid.closest[0].psi_id, "PSI-25")
        self.assertAlmostEqual(solid.closest[0].o2_frac, 0.222)


class LiteralTypesTests(unittest.TestCase):
    def test_literals_match_the_runs_table_check_constraints(self):
        sql = SCHEMA_SQL.read_text()
        for literal in (Outcome, FlameConfig):
            for value in typing.get_args(literal):
                with self.subTest(value=value):
                    self.assertIn(f"'{value}'", sql)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = testclient.TestClient(app)

    def test_get_scenario_returns_the_moon_fixture(self):
        response = self.client.get("/scenario", params={"o2_frac": 0.3, "pressure_atm": 0.69})
        self.assertEqual(response.status_code, 200)
        expected = json.loads((FIXTURES / "scenario_moon.json").read_text())
        self.assertEqual(response.json(), expected)

    def test_get_scenario_rejects_out_of_range_oxygen(self):
        response = self.client.get("/scenario", params={"o2_frac": 1.5, "pressure_atm": 0.69})
        self.assertEqual(response.status_code, 422)

    def test_docs_show_both_response_schemas(self):
        schemas = self.client.get("/openapi.json").json()["components"]["schemas"]
        self.assertIn("ScenarioResponse", schemas)
        self.assertIn("VerdictResponse", schemas)


if __name__ == "__main__":
    unittest.main()
