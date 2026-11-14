"""Shared API contracts between the data track (A) and the dashboard track (B).

Every request and response of the API has its shape defined here. Both tracks
code against these models, so the names are fixed: renaming a field means both
tracks edit their code. Guide with examples: docs/api_contracts.md.

    ScenarioQuery --GET /scenario--> ScenarioResponse (map points + closest Runs)
    ScenarioQuery --POST /verdict--> VerdictResponse  (text + Citations)
"""

from typing import Literal

from pydantic import BaseModel, Field

# Closed sets of strings: a value outside the list is rejected (422 at the API).
# Outcome and FlameConfig use the same values as the CHECK constraints on
# psi_dashboard.runs, so database rows pass through without conversion.
Outcome = Literal[
    "extinction",
    "disruption",
    "completion",
    "ignited",
    "no_ignition",
    "blowoff",
    "unknown",
]
FlameConfig = Literal["droplet", "gas_normal", "gas_inverse", "solid", "smoke"]
VerdictStatus = Literal["tested", "sparse", "untested"]


class ScenarioQuery(BaseModel):
    """The question: has a flame been tested in this atmosphere?

    Query string of GET /scenario, JSON body of POST /verdict.
    """

    o2_frac: float = Field(ge=0, le=1, description="Oxygen mole fraction, 0 to 1")
    pressure_atm: float = Field(gt=0, description="Ambient pressure in atm")
    flame_config: FlameConfig | None = Field(
        default=None, description="Only consider runs of this flame type; all types when omitted"
    )


class Run(BaseModel):
    """One test run on the ISS (one row of psi_dashboard.runs), as the dashboard shows it."""

    run_id: str = Field(description="Unique run id, e.g. PSI-69-25 (study + test number)")
    psi_id: str = Field(description="Study the run belongs to, e.g. PSI-69 (FLEX)")
    label: str = Field(description="Fuel, or material for solid samples")
    o2_frac: float | None = Field(description="Oxygen mole fraction the run was done at")
    pressure_atm: float | None = Field(description="Pressure the run was done at, in atm")
    outcome: Outcome | None = Field(description="How the run ended; null when not extracted yet")
    distance: float = Field(
        ge=0,
        description="Distance to the queried scenario, computed per query (not stored). "
        "1 = the edge of 'nearby'",
    )


class ScenarioResponse(BaseModel):
    """The answer of GET /scenario: which runs sit near the queried atmosphere."""

    in_envelope: bool = Field(description="True when at least one run is nearby")
    n_nearby: int = Field(ge=0, description="Number of runs with distance <= 1")
    closest: list[Run] = Field(max_length=5, description="The 5 nearest runs, nearest first")
    points: list[Run] = Field(
        description="Every run matching flame_config, for the map, nearest first"
    )


class Citation(BaseModel):
    """A pointer to the evidence behind a verdict: one page of one document."""

    psi_id: str = Field(description="Study the document belongs to")
    doc_title: str = Field(description="Document title, e.g. FLEX Test Plan")
    page: int = Field(ge=1, description="Page number in the PDF, from 1")


class VerdictResponse(BaseModel):
    """The answer of POST /verdict: a written judgement with its sources."""

    status: VerdictStatus = Field(
        description="tested: runs nearby; sparse: only a few; untested: none nearby"
    )
    text: str = Field(description="Plain-language explanation for the user")
    citations: list[Citation] = Field(description="Where the claims in text come from")
