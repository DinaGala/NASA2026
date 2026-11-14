import asyncio
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import testclient
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel as PydanticTestModel  # not a test class
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.agent.search import PaperHit
from src.agent.verdict_agent import Deps, RunDataUnavailable, answer, check_verdict, status_for, verdict_agent
from src.api.app import app, verdict_deps
from src.contracts import Citation, ScenarioQuery, ScenarioResponse, VerdictResponse
from src.database import engine

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
MOON = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69)
MOON_SOLID = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69, flame_config="solid")
HIT = PaperHit("PSI-69", "FLEX Test Plan", 6, "Test matrix at 0.75 atm and 0.30 O2 ...", 0.9)
HIT_CITATION = Citation(psi_id="PSI-69", doc_title="FLEX Test Plan", page=6)

TESTED = VerdictResponse(
    status="tested",
    text="Tested. 17 FLEX runs sat near these conditions: 15 disruption, 2 completion, none extinguished.",
    citations=[HIT_CITATION],
)
INVENTED = TESTED.model_copy(update={"text": "Tested. 23 FLEX runs sat near these conditions."})


def scenario(name: str) -> ScenarioResponse:
    return ScenarioResponse.model_validate_json((FIXTURES / name).read_text())


def fake_deps(query: ScenarioQuery = MOON, hits: list[PaperHit] | None = None, calls: list | None = None) -> Deps:
    """Deps from the committed fixtures and a fake paper search: no database, no LLM.

    `calls` collects one entry per scenario query, to count them.
    """
    fixture = "scenario_moon_solid.json" if query.flame_config == "solid" else "scenario_moon.json"
    found = [HIT] if hits is None else hits

    def find_scenario(_engine, _query):
        if calls is not None:
            calls.append(_query)
        return scenario(fixture)

    return Deps(
        engine=None,
        query=query,
        find_scenario=find_scenario,
        search=lambda question, psi_ids=None, k=5: [hit for hit in found if not psi_ids or hit.psi_id in psi_ids],
    )


def searched(deps: Deps) -> Deps:
    deps.searched, deps.hits = True, [HIT]
    return deps


class StatusRuleTests(unittest.TestCase):
    def test_status_comes_from_the_number_of_nearby_runs(self):
        self.assertEqual([status_for(n) for n in (0, 1, 2, 3, 17)],
                         ["untested", "sparse", "sparse", "tested", "tested"])


class OutputValidatorTests(unittest.TestCase):
    def test_a_correct_verdict_passes(self):
        check_verdict(TESTED, searched(fake_deps()))

    def test_an_invented_number_is_rejected(self):
        with self.assertRaisesRegex(ModelRetry, "23"):
            check_verdict(INVENTED, searched(fake_deps()))

    def test_fractions_may_be_written_as_percent_and_ids_and_pages_are_ignored(self):
        verdict = TESTED.model_copy(update={"text": "Tested at 30% O2: 17 PSI-69 runs, see FLEX Test Plan p. 6."})
        check_verdict(verdict, searched(fake_deps()))

    def test_a_status_against_the_rule_is_rejected(self):
        with self.assertRaisesRegex(ModelRetry, "status must be 'tested'"):
            check_verdict(TESTED.model_copy(update={"status": "sparse"}), searched(fake_deps()))

    def test_untested_must_start_with_untested_and_name_the_closest_run(self):
        deps = searched(fake_deps(MOON_SOLID))
        closest = deps.evidence().closest[0]
        with self.assertRaisesRegex(ModelRetry, "Untested"):
            check_verdict(VerdictResponse(status="untested", text="No run nearby.", citations=[HIT_CITATION]), deps)
        with self.assertRaisesRegex(ModelRetry, "closest run"):
            check_verdict(VerdictResponse(status="untested", text="Untested. No run nearby.", citations=[HIT_CITATION]), deps)
        check_verdict(
            VerdictResponse(status="untested", text=f"Untested. The closest is {closest.label}.", citations=[HIT_CITATION]),
            deps,
        )

    def test_a_number_with_a_unit_attached_is_checked(self):
        verdict = TESTED.model_copy(update={"text": "Tested. Flames reached 1500K in 45s at 0.3atm."})
        with self.assertRaisesRegex(ModelRetry, "1500, 45"):
            check_verdict(verdict, searched(fake_deps()))

    def test_a_page_reference_does_not_hide_the_numbers_after_it(self):
        verdict = TESTED.model_copy(update={"text": "Tested. See page 6 and 99 runs ignited."})
        with self.assertRaisesRegex(ModelRetry, "99"):
            check_verdict(verdict, searched(fake_deps()))

    def test_a_citation_that_search_did_not_return_is_rejected(self):
        # A real page the model might remember, but search_papers never returned it.
        verdict = TESTED.model_copy(update={"citations": [Citation(psi_id="PSI-69", doc_title="FLEX Test Plan", page=1)]})
        with self.assertRaisesRegex(ModelRetry, "did not return"):
            check_verdict(verdict, searched(fake_deps()))

    def test_search_is_required_even_with_citations(self):
        with self.assertRaisesRegex(ModelRetry, "Call search_papers"):
            check_verdict(TESTED, fake_deps())

    def test_zero_citations_only_when_search_found_nothing(self):
        bare = TESTED.model_copy(update={"citations": []})
        with self.assertRaisesRegex(ModelRetry, "Call search_papers"):
            check_verdict(bare, fake_deps())
        with self.assertRaisesRegex(ModelRetry, "cite the pages"):
            check_verdict(bare, searched(fake_deps()))
        nothing_found = fake_deps(hits=[])
        nothing_found.searched = True
        check_verdict(bare, nothing_found)


class AgentWiringTests(unittest.TestCase):
    """The agent with stand-in models (TestModel, FunctionModel): no LLM cost."""

    def test_the_scenario_is_queried_once_even_with_parallel_tools_and_a_fallback(self):
        calls = []
        with verdict_agent.override(model=PydanticTestModel(call_tools="all", custom_output_args=TESTED.model_dump())):
            asyncio.run(answer(fake_deps(calls=calls)))
        self.assertEqual(len(calls), 1)

        calls.clear()

        def broken(messages, info):
            raise RuntimeError("model unavailable")

        with verdict_agent.override(model=FunctionModel(broken)), self.assertLogs("src.agent.verdict_agent"):
            asyncio.run(answer(fake_deps(calls=calls)))
        self.assertEqual(len(calls), 1)  # the fallback reused the loaded runs

    def test_unavailable_run_data_raises_instead_of_a_verdict(self):
        deps = fake_deps()

        def down(_engine, _query):
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        deps.find_scenario = down
        with self.assertRaises(RunDataUnavailable):
            asyncio.run(answer(deps))

    def test_the_agent_calls_both_tools_and_returns_the_verdict(self):
        deps = fake_deps()
        with verdict_agent.override(model=PydanticTestModel(call_tools="all", custom_output_args=TESTED.model_dump())):
            verdict = asyncio.run(answer(deps))
        self.assertEqual(verdict, TESTED)
        self.assertIsNotNone(deps.scenario)
        self.assertTrue(deps.searched)

    def test_an_invented_number_is_sent_back_and_corrected(self):
        replies = []

        def model(messages, info: AgentInfo) -> ModelResponse:
            output_tool = info.output_tools[0].name
            step = [
                ToolCallPart("find_runs", {}),
                ToolCallPart("search_papers", {"question": "droplet flames at 0.30 O2"}),
                ToolCallPart(output_tool, INVENTED.model_dump()),
                ToolCallPart(output_tool, TESTED.model_dump()),
            ][len(replies)]
            replies.append(step.tool_name)
            return ModelResponse(parts=[step])

        with verdict_agent.override(model=FunctionModel(model)):
            verdict = asyncio.run(answer(fake_deps()))
        self.assertEqual(verdict, TESTED)
        self.assertEqual(replies.count(replies[-1]), 2)  # the invented verdict was rejected once

    def test_a_failing_model_falls_back_to_a_verdict_written_by_code(self):
        def broken(messages, info):
            raise RuntimeError("model unavailable")

        with verdict_agent.override(model=FunctionModel(broken)), self.assertLogs("src.agent.verdict_agent"):
            verdict = asyncio.run(answer(fake_deps(MOON_SOLID)))
        self.assertEqual(verdict.status, "untested")
        self.assertTrue(verdict.text.startswith("Untested"))
        self.assertEqual(verdict.citations, [])


class VerdictEndpointTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[verdict_deps] = lambda: fake_deps
        self.client = testclient.TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_post_verdict_returns_the_agent_verdict(self):
        with verdict_agent.override(model=PydanticTestModel(custom_output_args=TESTED.model_dump())):
            response = self.client.post("/verdict", json=MOON.model_dump())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VerdictResponse.model_validate(response.json()), TESTED)

    def test_post_verdict_returns_503_when_the_run_data_is_unavailable(self):
        def make_deps(query):
            deps = fake_deps(query)
            deps.find_scenario = lambda _engine, _query: (_ for _ in ()).throw(OperationalError("SELECT", {}, Exception("down")))
            return deps

        app.dependency_overrides[verdict_deps] = lambda: make_deps
        response = self.client.post("/verdict", json=MOON.model_dump())
        self.assertEqual(response.status_code, 503)

    def test_importing_the_api_does_not_load_torch(self):
        # /scenario and /inverse-flames must not pay for the paper-search models.
        check = "import sys, src.api.app; sys.exit('torch' in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable, "-c", check], capture_output=True).returncode, 0)

    def test_post_verdict_falls_back_when_the_agent_fails(self):
        def broken(messages, info):
            raise RuntimeError("model unavailable")

        with verdict_agent.override(model=FunctionModel(broken)), self.assertLogs("src.agent.verdict_agent"):
            response = self.client.post("/verdict", json=MOON.model_dump())
        self.assertEqual(response.status_code, 200)
        verdict = VerdictResponse.model_validate(response.json())
        self.assertEqual(verdict.status, "tested")
        self.assertIn("17", verdict.text)


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


@unittest.skipUnless(LIVE, "set LIVE_LLM_TESTS=1 (calls the real LLM; needs the database loaded)")
class LiveVerdictTests(unittest.TestCase):
    """Real model, real database. Costs a few cents per run: `make test-live`."""

    @unittest.skipUnless(os.getenv("ANTHROPIC_API_KEY"), "needs ANTHROPIC_API_KEY")
    def test_moon_droplet_is_tested_with_the_find_runs_counts(self):
        deps = Deps(engine=engine, query=MOON)
        verdict = asyncio.run(answer(deps))
        evidence = deps.evidence()
        self.assertEqual(verdict.status, "tested")
        self.assertIn(str(evidence.n_nearby), verdict.text)
        self.assertGreaterEqual(len(verdict.citations), 1, verdict.text)

    @unittest.skipUnless(os.getenv("ANTHROPIC_API_KEY"), "needs ANTHROPIC_API_KEY")
    def test_moon_solid_is_untested_and_names_the_closest_run(self):
        deps = Deps(engine=engine, query=MOON_SOLID)
        verdict = asyncio.run(answer(deps))
        closest = deps.evidence().closest[0]
        self.assertEqual(verdict.status, "untested")
        self.assertTrue(verdict.text.startswith("Untested"), verdict.text)
        self.assertIn(closest.label.lower(), verdict.text.lower())
        self.assertGreaterEqual(len(verdict.citations), 1, verdict.text)

    def test_a_broken_api_key_still_returns_a_valid_fallback_verdict(self):
        broken_key = {"ANTHROPIC_API_KEY": "sk-ant-broken", "LLM_MODEL": "anthropic:claude-opus-5-5"}
        with mock.patch.dict(os.environ, broken_key), self.assertLogs("src.agent.verdict_agent"):
            response = testclient.TestClient(app).post("/verdict", json=MOON.model_dump())
        self.assertEqual(response.status_code, 200)
        verdict = VerdictResponse.model_validate(response.json())
        self.assertEqual(verdict.status, "tested")
        self.assertEqual(verdict.citations, [])


if __name__ == "__main__":
    unittest.main()
