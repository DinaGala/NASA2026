"""API skeleton: every endpoint returns a fixture from data/fixtures/ until the real queries land."""

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import BaseModel

from src.contracts import (
    ScenarioQuery,
    ScenarioResponse,
    VerdictResponse,
    InverseFlameQuery,
    InverseFlameResponse,
)
from src.database import engine
from src.api.queries import get_scenario_data, get_inverse_flames_data

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def load_fixture[T: BaseModel](name: str, model: type[T]) -> T:
    return model.model_validate_json((FIXTURES / name).read_text())


SCENARIO_MOON = load_fixture("scenario_moon.json", ScenarioResponse)
SCENARIO_MOON_SOLID = load_fixture("scenario_moon_solid.json", ScenarioResponse)
VERDICT_TESTED = load_fixture("verdict_tested.json", VerdictResponse)
VERDICT_UNTESTED = load_fixture("verdict_untested.json", VerdictResponse)

app = FastAPI(title="PSI Flame API")


@app.get("/scenario", response_model=ScenarioResponse)
def get_scenario(query: Annotated[ScenarioQuery, Query()]) -> ScenarioResponse:
    return get_scenario_data(engine, query)


@app.get("/inverse-flames", response_model=InverseFlameResponse)
def get_inverse_flames(query: Annotated[InverseFlameQuery, Query()]) -> InverseFlameResponse:
    return get_inverse_flames_data(engine, query)


@app.post("/verdict", response_model=VerdictResponse)
def post_verdict(query: ScenarioQuery) -> VerdictResponse:
    if query.flame_config == "solid":
        return VERDICT_UNTESTED
    return VERDICT_TESTED
