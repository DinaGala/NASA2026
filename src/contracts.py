"""Shared API contracts between the data track (A) and the dashboard track (B).

The names are fixed: renaming a field means both tracks edit their code.
"""

from typing import Literal

from pydantic import BaseModel, Field

# Same values as the CHECK constraints on psi_dashboard.runs.
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
    o2_frac: float = Field(ge=0, le=1, description="Oxygen mole fraction, 0 to 1")
    pressure_atm: float = Field(gt=0, description="Ambient pressure in atm")
    flame_config: FlameConfig | None = None


class Run(BaseModel):
    run_id: str
    psi_id: str
    label: str = Field(description="Fuel, or material for solid samples")
    o2_frac: float | None
    pressure_atm: float | None
    outcome: Outcome | None
    distance: float = Field(ge=0, description="Distance to the queried scenario")


class ScenarioResponse(BaseModel):
    in_envelope: bool
    n_nearby: int = Field(ge=0)
    closest: list[Run] = Field(max_length=5)
    points: list[Run] = Field(description="All runs, for the map")


class Citation(BaseModel):
    psi_id: str
    doc_title: str
    page: int = Field(ge=1)


class VerdictResponse(BaseModel):
    status: VerdictStatus
    text: str
    citations: list[Citation]
