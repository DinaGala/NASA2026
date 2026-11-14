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
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.messages import ModelRequest, RetryPromptPart
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy.engine import Engine

from src.agent.search import PaperHit, search_papers
from src.api.queries import get_scenario_data
from src.contracts import Citation, Outcome, ScenarioQuery, ScenarioResponse, VerdictResponse, VerdictStatus

logger = logging.getLogger(__name__)

# The cheapest model that passes every check (tuned on #9: $0.0013 per verdict). Code, not the
# model, enforces the rules, so a stronger model is not needed.
DEFAULT_MODEL = "anthropic:claude-haiku-5-5"
QUESTION = (
    "What does NASA's microgravity evidence show about fire at these conditions, and how sure is it?"
)
TESTED_MIN_RUNS = 3
PAPER_HITS = 5
# A verdict uses 6-15k tokens (9k on average, #9); the token cap, about twice the largest,
# only cuts a runaway run (or an expensive model set by mistake), which then gets the fallback.
USAGE_LIMITS = UsageLimits(request_limit=8, total_tokens_limit=30_000)


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
    # "PSI-69" -> "FLEX (Flame Extinguishment Experiment)": readers know names, not IDs.
    studies: dict[str, str]


def involved_studies(scenario: ScenarioResponse) -> list[str]:
    """The studies `find_runs` talks about: those of the nearby runs and of the closest runs."""
    return sorted({run.psi_id for run in scenario.points if run.distance <= 1} | {run.psi_id for run in scenario.closest})


def study_names_from_db(engine: Engine, psi_ids: list[str]) -> dict[str, str]:
    query = text(
        "SELECT psi_id, acronym, title FROM psi_dashboard.investigations WHERE psi_id = ANY(:psi_ids)"
    ).bindparams(bindparam("psi_ids", type_=ARRAY(TEXT)))
    with engine.connect() as connection:
        rows = connection.execute(query, {"psi_ids": psi_ids})
        return {row.psi_id: f"{row.acronym} ({row.title})" if row.acronym else row.title for row in rows}


def evidence_from(query: ScenarioQuery, scenario: ScenarioResponse, names: dict[str, str]) -> RunEvidence:
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
        studies={psi_id: names.get(psi_id, psi_id) for psi_id in involved_studies(scenario)},
    )


class RunDataUnavailable(Exception):
    """The runs could not be loaded: no verdict, not even the fallback, can be written."""


@dataclass
class RunReport:
    """What one verdict cost: time, model calls, corrections, tokens. Logged by `answer`."""

    model: str
    latency_s: float
    fell_back: bool
    requests: int = 0
    retries: int = 0  # verdicts or tool calls sent back to the model (ModelRetry)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # from PydanticAI's price data; None when the model is unknown


@dataclass
class Deps:
    """What the agent works from. Swap `find_scenario` or `search` without touching the agent."""

    engine: Engine
    query: ScenarioQuery
    find_scenario: Callable[[Engine, ScenarioQuery], ScenarioResponse] = get_scenario_data
    search: Callable[..., list[PaperHit]] = search_papers
    find_study_names: Callable[[Engine, list[str]], dict[str, str]] = study_names_from_db
    # Filled during the run, then read by the output validator and the fallback.
    scenario: ScenarioResponse | None = None
    study_names: dict[str, str] = field(default_factory=dict)
    searched: bool = False
    hits: list[PaperHit] = field(default_factory=list)
    report: RunReport | None = None  # set by `answer`
    # PydanticAI runs sync tools in parallel threads: one scenario query, however many callers.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def load_scenario(self) -> ScenarioResponse:
        with self._lock:
            if self.scenario is None:
                scenario = self.find_scenario(self.engine, self.query)
                self.study_names = self.find_study_names(self.engine, involved_studies(scenario))
                self.scenario = scenario
            return self.scenario

    def evidence(self) -> RunEvidence:
        return evidence_from(self.query, self.load_scenario(), self.study_names)


INSTRUCTIONS = f"""\
You answer one question about a scenario (an oxygen fraction and a pressure): "{QUESTION}"
Your reader has never heard of these NASA experiments: a judge, or an engineer from another field.

First call `find_runs` (the ISS test runs near the scenario), then `search_papers` (what the
experiments' documents say). Then write the verdict. A verdict that breaks a rule is sent back:
- `status` is the `status` that `find_runs` returns. Do not change it.
- Every number in `text` comes from the `find_runs` output, written as it appears there (a
  fraction such as 0.3 may be written as 30%). No other numbers: no years, no values from papers.
- `citations`: one entry per page you used, copied from a `search_papers` hit (`psi_id`,
  `doc_title`, `page`). Cite at least one.
- If the status is untested, `text` starts with "Untested" and names the closest run by its label.

Write `text` as 3 or 4 short sentences, under 90 words, in plain English:
1. The answer: the status in words, and how many runs sit near these conditions, in which
   experiment. Name experiments as in `studies` ("NASA's FLEX experiment"), never by PSI ID or run ID.
2. What happened in those runs, with the outcome counts in plain words: extinction = the flame
   went out; disruption = the burning droplet broke apart while its flame was still burning;
   completion = the fuel burned away; ignited / no_ignition = the flame lit, or did not;
   blowoff = the gas flow blew the flame out; unknown = not recorded.
3. Only if a cited passage explains these runs or the experiment, one sentence from it. Leave
   out side details (other gases, other conditions, other experiments).
4. How sure: for tested, how many runs and experiments the evidence rests on; for sparse, say
   plainly that the evidence is thin; for untested, how far the closest run is (its conditions).
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
PERCENT = re.compile(r"\s*(?:%|percent\b|per cent\b)", re.IGNORECASE)
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
    for run in evidence.closest:  # run IDs and labels ("100 micron PMMA film") come from find_runs
        cleaned = cleaned.replace(run.run_id, " ").replace(run.label, " ")
    cleaned = IDENTIFIER.sub(" ", PAGE_REFERENCE.sub(" ", cleaned))

    allowed = numbers_in(evidence)
    bad = []
    for match in NUMBER.finditer(cleaned):
        token = match.group()
        value = float(token)
        decimals = len(token.split(".")[1]) if "." in token else 0
        tolerance = 0.5 * 10**-decimals + 1e-9
        # "13%" or "13 percent" can only be a fraction (0.13), never a count that happens to be 13;
        # a bare number can only be a value as written.
        if PERCENT.match(cleaned, match.end()):
            supported = any(v <= 1 and math.isclose(v * 100, value, abs_tol=tolerance) for v in allowed)
        else:
            supported = any(math.isclose(v, value, abs_tol=tolerance) for v in allowed)
        if not supported:
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
    model = llm_model()
    started = time.perf_counter()
    try:
        result = await verdict_agent.run(prompt_for(deps.query), deps=deps, model=model, usage_limits=USAGE_LIMITS)
    except Exception:  # bad key, model down, retries or limits exhausted: the demo still answers
        logger.exception("Verdict agent failed; returning the fallback verdict")
        deps.report = RunReport(model=model, latency_s=time.perf_counter() - started, fell_back=True)
        return fallback_verdict(deps)
    usage = result.usage
    deps.report = RunReport(
        model=model,
        latency_s=time.perf_counter() - started,
        fell_back=False,
        requests=usage.requests,
        retries=sum(
            isinstance(part, RetryPromptPart)
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
        ),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=None if usage.cost is None else float(usage.cost),
    )
    logger.info("Verdict %s: %s", result.output.status, deps.report)
    return result.output
