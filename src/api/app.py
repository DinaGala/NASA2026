"""PSI Flame API: scenarios and inverse flames from Postgres, verdicts from the verdict agent."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query
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
from src.agent.verdict_agent import Deps, answer

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def load_fixture[T: BaseModel](name: str, model: type[T]) -> T:
    return model.model_validate_json((FIXTURES / name).read_text())


SCENARIO_MOON = load_fixture("scenario_moon.json", ScenarioResponse)
SCENARIO_MOON_SOLID = load_fixture("scenario_moon_solid.json", ScenarioResponse)

app = FastAPI(title="PSI Flame API")


@app.get("/scenario", response_model=ScenarioResponse)
def get_scenario(query: Annotated[ScenarioQuery, Query()]) -> ScenarioResponse:
    return get_scenario_data(engine, query)


@app.get("/inverse-flames", response_model=InverseFlameResponse)
def get_inverse_flames(query: Annotated[InverseFlameQuery, Query()]) -> InverseFlameResponse:
    return get_inverse_flames_data(engine, query)


def verdict_deps() -> Callable[[ScenarioQuery], Deps]:
    """How /verdict builds the agent's dependencies; tests override it."""
    return lambda query: Deps(engine=engine, query=query)


@app.post("/verdict", response_model=VerdictResponse)
async def post_verdict(
    query: ScenarioQuery, make_deps: Annotated[Callable[[ScenarioQuery], Deps], Depends(verdict_deps)]
) -> VerdictResponse:
    return await answer(make_deps(query))
