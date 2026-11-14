"""PSI Flame API: scenarios and inverse flames from Postgres, verdicts from the verdict agent."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from src.contracts import (
    ScenarioQuery,
    ScenarioResponse,
    VerdictResponse,
    InverseFlameQuery,
    InverseFlameResponse,
)
from src.database import engine
from src.api.queries import get_scenario_data, get_inverse_flames_data
from src.agent.verdict_agent import Deps, RunDataUnavailable, answer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The paper-search models take seconds to load. Load them in the background at startup, so
    # the first /verdict does not pay for it and /scenario is never held up.
    from src.agent.search import warm_up

    app.state.warm_up = asyncio.create_task(asyncio.to_thread(warm_up))
    yield


app = FastAPI(title="PSI Flame API", lifespan=lifespan)


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
    try:
        return await answer(make_deps(query))
    except RunDataUnavailable as error:
        # Without the runs there is nothing true to say, not even the fallback verdict.
        raise HTTPException(status_code=503, detail=str(error)) from error
