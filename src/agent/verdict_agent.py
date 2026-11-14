"""The verdict agent: answers one fixed question about a scenario, from runs and papers.

"What does NASA's microgravity evidence show about fire at these conditions, and how sure is it?"

The language model writes the text and picks the citations; code decides everything that must
not be wrong. The status comes from a rule on the number of nearby runs, every number in the
text must come from `find_runs`, and every citation must be a page `search_papers` returned
(a row of psi_dashboard.chunks). A verdict breaking one of those rules is sent back to the model
(ModelRetry). If the agent still fails, `answer` returns a verdict written entirely by code.
Guide: docs/verdict_agent.md.
"""

import asyncio
import logging
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from sqlalchemy.engine import Engine

from src.agent.search import PaperHit, search_papers
from src.api.queries import get_scenario_data
from src.contracts import Citation, Outcome, ScenarioQuery, ScenarioResponse, VerdictResponse, VerdictStatus

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic:claude-opus-5-5"
QUESTION = (
    "What does NASA's microgravity evidence show about fire at these conditions, and how sure is it?"
)
TESTED_MIN_RUNS = 3
PAPER_HITS = 5
USAGE_LIMITS = UsageLimits(request_limit=8)


def status_for(n_nearby: int) -> VerdictStatus:
    """The status rule. It is decided here, never by the model."""
    if n_nearby >= TESTED_MIN_RUNS:
        return "tested"
    if n_nearby >= 1:
        return "sparse"
    return "untested"


class RunFact(BaseModel):
    run_id: str
    psi_id: str
    label: str
    o2_frac: float | None
    pressure_atm: float | None
    outcome: Outcome | None
    distance: float


class RunEvidence(BaseModel):
    """What `find_runs` gives the model: the facts it may quote, counted by code.

    The full ScenarioResponse lists every run for the map (hundreds); the model only needs
    the counts and the closest runs, and should not have to count anything itself.
    """

    o2_frac: float
    pressure_atm: float
    flame_config: str | None
    status: VerdictStatus
    n_nearby: int
    runs_compared: int
    nearby_outcomes: dict[str, int]
    nearby_o2_range: list[float] | None
    nearby_pressure_range: list[float] | None
    nearby_studies: list[str]
    closest: list[RunFact]


def evidence_from(query: ScenarioQuery, scenario: ScenarioResponse) -> RunEvidence:
    nearby = [run for run in scenario.points if run.distance <= 1]
    o2 = [run.o2_frac for run in nearby if run.o2_frac is not None]
    pressure = [run.pressure_atm for run in nearby if run.pressure_atm is not None]
    return RunEvidence(
        o2_frac=query.o2_frac,
        pressure_atm=query.pressure_atm,
        flame_config=query.flame_config,
        status=status_for(scenario.n_nearby),
        n_nearby=scenario.n_nearby,
        runs_compared=len(scenario.points),
        nearby_outcomes=dict(Counter(run.outcome or "unknown" for run in nearby)),
        nearby_o2_range=[round(min(o2), 3), round(max(o2), 3)] if o2 else None,
        nearby_pressure_range=[round(min(pressure), 2), round(max(pressure), 2)] if pressure else None,
        nearby_studies=sorted({run.psi_id for run in nearby}),
        closest=[
            RunFact(
                run_id=run.run_id,
                psi_id=run.psi_id,
                label=run.label,
                o2_frac=None if run.o2_frac is None else round(run.o2_frac, 3),
                pressure_atm=None if run.pressure_atm is None else round(run.pressure_atm, 2),
                outcome=run.outcome,
                distance=round(run.distance, 2),
            )
            for run in scenario.closest
        ],
    )


class RunDataUnavailable(Exception):
    """The runs could not be loaded: no verdict, not even the fallback, can be written."""


@dataclass
class Deps:
    """What the agent works from. Swap `find_scenario` or `search` without touching the agent."""

    engine: Engine
    query: ScenarioQuery
    find_scenario: Callable[[Engine, ScenarioQuery], ScenarioResponse] = get_scenario_data
    search: Callable[..., list[PaperHit]] = search_papers
    # Filled during the run, then read by the output validator and the fallback.
    scenario: ScenarioResponse | None = None
    searched: bool = False
    hits: list[PaperHit] = field(default_factory=list)
    # PydanticAI runs sync tools in parallel threads: one scenario query, however many callers.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def load_scenario(self) -> ScenarioResponse:
        with self._lock:
            if self.scenario is None:
                self.scenario = self.find_scenario(self.engine, self.query)
            return self.scenario

    def evidence(self) -> RunEvidence:
        return evidence_from(self.query, self.load_scenario())


INSTRUCTIONS = f"""\
You answer one question for a scenario (an oxygen fraction and a pressure), for engineers
planning fire safety beyond Earth: "{QUESTION}"

Call `find_runs` for the ISS test runs near the scenario, then `search_papers` for what the
investigations' documents say. Then write the verdict:
- `status` is the `status` that `find_runs` returns. Do not change it.
- Every number in `text` comes from the `find_runs` output, written as it appears there (you
  may write a fraction such as 0.3 as 30%). No other numbers: no years, no sample sizes, no
  values from the papers.
- Every claim drawn from a paper cites it: one entry in `citations` per page used, with the
  `psi_id`, `doc_title` and `page` of a `search_papers` hit. Cite nothing that search did not return.
- If the status is untested, `text` starts with "Untested" and names the closest run (its label
  and study).
- Keep `text` to 3-5 plain sentences: what was tested near these conditions, what happened,
  how sure the evidence is.
"""


def llm_model() -> str:
    """The model, from LLM_MODEL in .env (its API key goes there too, e.g. ANTHROPIC_API_KEY)."""
    return os.getenv("LLM_MODEL") or DEFAULT_MODEL


# No model here: `answer` passes `llm_model()` on every run, so LLM_MODEL is read in one place.
verdict_agent = Agent(
    deps_type=Deps,
    output_type=VerdictResponse,
    instructions=INSTRUCTIONS,
    retries={"tools": 1, "output": 2},
)


@verdict_agent.tool
def find_runs(ctx: RunContext[Deps]) -> RunEvidence:
    """ISS test runs near the scenario: status, counts, outcomes and the closest runs."""
    return ctx.deps.evidence()


@verdict_agent.tool(name="search_papers")
def search_papers_tool(ctx: RunContext[Deps], question: str) -> list[dict]:
    """Passages of the investigations' documents that answer `question`, with page numbers.

    Searches only the studies of the nearby runs (or of the closest runs when none is nearby).
    """
    evidence = ctx.deps.evidence()
    studies = evidence.nearby_studies or sorted({run.psi_id for run in evidence.closest})
    # Word files have no page number, which a Citation requires: they cannot be cited.
    hits = [hit for hit in ctx.deps.search(question, psi_ids=studies, k=2 * PAPER_HITS) if hit.page]
    hits = hits[:PAPER_HITS]
    ctx.deps.searched = True
    ctx.deps.hits.extend(hit for hit in hits if hit not in ctx.deps.hits)
    return [
        {"psi_id": hit.psi_id, "doc_title": hit.doc_title, "page": hit.page, "text": hit.text}
        for hit in hits
    ]


# --- Output checks -------------------------------------------------------------------------

# A number may have a unit attached ("1500K", "0.3atm"); it is still checked.
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?")
# One page or one page range ("p. 6", "pp. 17-20"), never the numbers after it.
PAGE_REFERENCE = re.compile(r"\b(?:pp?\.|pages?)\s*\d+(?:\s*[-–]\s*\d+)?", re.IGNORECASE)
IDENTIFIER = re.compile(r"\b[A-Za-z]+[-_]?\d+[A-Za-z0-9_:-]*")  # PSI-69, CO2, FLEX-2, run ids


def numbers_in(evidence: RunEvidence) -> list[float]:
    found: list[float] = []

    def walk(value: object) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            found.append(float(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(evidence.model_dump())
    return found


def unsupported_numbers(text_: str, evidence: RunEvidence, citations: list[Citation]) -> list[str]:
    """Numbers in the text that `find_runs` did not return (PSI IDs and page numbers ignored).

    `citations` must already be checked against the search hits: their titles are removed.
    """
    cleaned = text_
    for citation in citations:
        cleaned = cleaned.replace(citation.doc_title, " ")
    for run in evidence.closest:
        cleaned = cleaned.replace(run.run_id, " ")
    cleaned = IDENTIFIER.sub(" ", PAGE_REFERENCE.sub(" ", cleaned))

    allowed = numbers_in(evidence)
    bad = []
    for token in NUMBER.findall(cleaned):
        value = float(token)
        decimals = len(token.split(".")[1]) if "." in token else 0
        tolerance = 0.5 * 10**-decimals + 1e-9
        as_written = any(math.isclose(v, value, abs_tol=tolerance) for v in allowed)
        as_percent = any(v <= 1 and math.isclose(v * 100, value, abs_tol=tolerance) for v in allowed)
        if not (as_written or as_percent):
            bad.append(token)
    return bad


def check_verdict(verdict: VerdictResponse, deps: Deps) -> None:
    """Raise ModelRetry, with what to fix, when the verdict breaks a rule."""
    evidence = deps.evidence()
    if verdict.status != evidence.status:
        raise ModelRetry(
            f"status must be {evidence.status!r}: {evidence.n_nearby} runs are nearby "
            f"(tested >= {TESTED_MIN_RUNS}, sparse 1-2, untested 0)."
        )
    if evidence.status == "untested":
        closest = evidence.closest[0] if evidence.closest else None
        if not verdict.text.lstrip().lower().startswith("untested"):
            raise ModelRetry('An untested verdict must start with "Untested".')
        if closest and closest.label.lower() not in verdict.text.lower() and closest.run_id not in verdict.text:
            raise ModelRetry(f"Name the closest run: {closest.label} ({closest.psi_id}).")
    if not deps.searched:
        raise ModelRetry("Call search_papers before writing the verdict.")
    found = {(hit.psi_id, hit.doc_title, hit.page) for hit in deps.hits}
    not_found = [c for c in verdict.citations if (c.psi_id, c.doc_title, c.page) not in found]
    if not_found:
        listed = "; ".join(f"{c.psi_id} {c.doc_title!r} p. {c.page}" for c in not_found)
        raise ModelRetry(f"search_papers did not return these pages: {listed}. Cite only its hits.")
    if not verdict.citations and deps.hits:
        raise ModelRetry("search_papers found passages: cite the pages you used.")
    bad = unsupported_numbers(verdict.text, evidence, verdict.citations)
    if bad:
        raise ModelRetry(
            f"These numbers are not in the find_runs output: {', '.join(bad)}. "
            "Use only numbers find_runs returned, or remove them."
        )


@verdict_agent.output_validator
def validate_verdict(ctx: RunContext[Deps], verdict: VerdictResponse) -> VerdictResponse:
    check_verdict(verdict, ctx.deps)
    return verdict


# --- Entry point ---------------------------------------------------------------------------


def conditions(o2_frac: float | None, pressure_atm: float | None) -> str:
    parts = [f"{o2_frac:g} O2" if o2_frac is not None else None,
             f"{pressure_atm:g} atm" if pressure_atm is not None else None]
    return " and ".join(part for part in parts if part) or "unknown conditions"


def fallback_verdict(deps: Deps) -> VerdictResponse:
    """A verdict written by code alone: status from the rule, numbers from find_runs, no citations."""
    evidence = deps.evidence()
    if evidence.status == "untested":
        text_ = f"Untested. No ISS run sits near {conditions(evidence.o2_frac, evidence.pressure_atm)}."
        if evidence.closest:
            run = evidence.closest[0]
            text_ += f" The closest is {run.label} ({run.psi_id}), at {conditions(run.o2_frac, run.pressure_atm)}."
    else:
        outcomes = ", ".join(f"{count} {outcome}" for outcome, count in evidence.nearby_outcomes.items())
        text_ = (
            f"{evidence.status.capitalize()}. {evidence.n_nearby} ISS runs sit near"
            f" {conditions(evidence.o2_frac, evidence.pressure_atm)}"
            f" ({', '.join(evidence.nearby_studies)}): {outcomes}."
        )
    text_ += " The written analysis is unavailable right now, so no paper is cited."
    return VerdictResponse(status=evidence.status, text=text_, citations=[])


def prompt_for(query: ScenarioQuery) -> str:
    flame = query.flame_config or "any flame type"
    return (
        f"Scenario: oxygen fraction {query.o2_frac:g}, pressure {query.pressure_atm:g} atm, {flame}.\n"
        f"Question: {QUESTION}"
    )


async def answer(deps: Deps) -> VerdictResponse:
    """Load the runs, run the agent; if the agent fails, return the code-written fallback verdict.

    Raises RunDataUnavailable when the runs cannot be loaded.
    """
    # Loaded once, before the agent and off the event loop: the tools, the validator and the
    # fallback then read it from memory, so the fallback cannot fail on the database.
    try:
        await asyncio.to_thread(deps.load_scenario)
    except Exception as error:
        raise RunDataUnavailable("The run data is unavailable; try again shortly.") from error
    try:
        result = await verdict_agent.run(
            prompt_for(deps.query), deps=deps, model=llm_model(), usage_limits=USAGE_LIMITS
        )
        return result.output
    except Exception:  # bad key, model down, retries or limits exhausted: the demo still answers
        logger.exception("Verdict agent failed; returning the fallback verdict")
        return fallback_verdict(deps)
