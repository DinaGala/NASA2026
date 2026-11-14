"""The verdict agent against the real LLM and the loaded database: the demo's four scenarios.

Costs real money (about a cent per run on a Haiku model), so it only runs with
LIVE_LLM_TESTS=1: `make test-live`. `make test` never calls the API. Each verdict is printed
with its cost report and appended to data/processed/live_verdicts.jsonl (not committed).
"""

import asyncio
import json
import os
import re
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi import testclient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.agent.verdict_agent import Deps, RunEvidence, answer
from src.api.app import app
from src.contracts import ScenarioQuery, VerdictResponse
from src.database import engine

LOG = Path(__file__).resolve().parents[2] / "data" / "processed" / "live_verdicts.jsonl"
MAX_INPUT_TOKENS = 15_000  # more means the full list of runs reached the model

MOON_DROPLET = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69, flame_config="droplet")
MOON_SOLID = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69, flame_config="solid")
EARTH_AIR_DROPLET = ScenarioQuery(o2_frac=0.21, pressure_atm=1.0, flame_config="droplet")
# Earth's oxygen at twice its pressure: only 2 FLEX runs nearby (every O2 from 0.17 to 0.25
# at 2 atm gives the same 2), so the point stays sparse whatever the rounding.
SPARSE_DROPLET = ScenarioQuery(o2_frac=0.21, pressure_atm=2.0, flame_config="droplet")
THIN_EVIDENCE = re.compile(r"\b(thin|limited|few|only|sparse|little|scarce|small number)\b", re.IGNORECASE)


def database_ready() -> bool:
    try:
        with engine.connect() as connection:
            return bool(connection.execute(text(
                "SELECT (SELECT count(*) FROM psi_dashboard.chunks) > 0"
                " AND (SELECT count(*) FROM psi_dashboard.runs) > 0"
            )).scalar())
    except OperationalError:
        return False


LIVE = os.getenv("LIVE_LLM_TESTS") == "1" and database_ready()
HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))


@unittest.skipUnless(LIVE, "set LIVE_LLM_TESTS=1 (calls the real LLM; needs the database loaded)")
class LiveScenarioTests(unittest.TestCase):
    def verdict(self, name: str, query: ScenarioQuery) -> tuple[VerdictResponse, RunEvidence]:
        """Run one verdict, record it, and check what every scenario must satisfy."""
        deps = Deps(engine=engine, query=query)
        verdict = asyncio.run(answer(deps))
        evidence, report = deps.evidence(), deps.report
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as log:
            log.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scenario": name,
                "query": query.model_dump(),
                "verdict": verdict.model_dump(),
                "report": asdict(report),
            }) + "\n")
        cost = "?" if report.cost_usd is None else f"${report.cost_usd:.4f}"
        print(
            f"\n  [{name}] {verdict.status} | {report.latency_s:.1f}s, {report.requests} requests, "
            f"{report.retries} retries, {report.input_tokens} in / {report.output_tokens} out, {cost}"
            f"\n  {verdict.text}\n  cites: {[f'{c.psi_id} {c.doc_title} p.{c.page}' for c in verdict.citations]}"
        )
        self.assertFalse(report.fell_back, "the agent failed and the fallback verdict was returned")
        self.assertLess(report.input_tokens, MAX_INPUT_TOKENS)
        self.assertGreaterEqual(len(verdict.citations), 1, verdict.text)
        return verdict, evidence

    @unittest.skipUnless(HAS_KEY, "needs ANTHROPIC_API_KEY")
    def test_moon_droplet_is_tested_with_the_find_runs_count(self):
        verdict, evidence = self.verdict("moon droplet", MOON_DROPLET)
        self.assertEqual(verdict.status, "tested")
        self.assertIn(str(evidence.n_nearby), verdict.text)

    @unittest.skipUnless(HAS_KEY, "needs ANTHROPIC_API_KEY")
    def test_moon_solid_is_untested_and_names_the_closest_run(self):
        verdict, evidence = self.verdict("moon solid", MOON_SOLID)
        closest = evidence.closest[0]  # BASS (PSI-26) today; BASS-II would be accepted too
        self.assertEqual(verdict.status, "untested")
        self.assertTrue(verdict.text.startswith("Untested"), verdict.text)
        self.assertIn(closest.label.lower(), verdict.text.lower())

    @unittest.skipUnless(HAS_KEY, "needs ANTHROPIC_API_KEY")
    def test_earth_air_droplet_is_tested_and_gives_the_extinction_count(self):
        verdict, evidence = self.verdict("earth air droplet", EARTH_AIR_DROPLET)
        self.assertEqual(verdict.status, "tested")
        self.assertIn(str(evidence.nearby_outcomes["extinction"]), verdict.text)

    @unittest.skipUnless(HAS_KEY, "needs ANTHROPIC_API_KEY")
    def test_a_point_with_two_runs_nearby_is_sparse_and_says_the_evidence_is_thin(self):
        verdict, evidence = self.verdict("sparse droplet", SPARSE_DROPLET)
        self.assertIn(evidence.n_nearby, (1, 2))
        self.assertEqual(verdict.status, "sparse")
        self.assertRegex(verdict.text, THIN_EVIDENCE)

    def test_a_broken_api_key_still_returns_a_valid_fallback_verdict(self):
        broken_key = {"ANTHROPIC_API_KEY": "sk-ant-broken"}
        with mock.patch.dict(os.environ, broken_key), self.assertLogs("src.agent.verdict_agent"):
            response = testclient.TestClient(app).post("/verdict", json=MOON_DROPLET.model_dump())
        self.assertEqual(response.status_code, 200)
        verdict = VerdictResponse.model_validate(response.json())
        self.assertEqual(verdict.status, "tested")
        self.assertEqual(verdict.citations, [])


if __name__ == "__main__":
    unittest.main()
