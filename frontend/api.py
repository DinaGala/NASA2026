"""The dashboard's only way to the data: HTTP calls to the PSI Flame API (spec §1).

Both calls are cached on their (rounded) inputs, so a slider moving back to a value it had is
free, and the same verdict is never paid for twice. Failed calls raise and are not cached.
"""

import os

import requests
import streamlit as st

from src.contracts import AmbientFlameConfig, ScenarioQuery, ScenarioResponse, VerdictResponse

API_URL = os.environ.get("API_URL", "http://localhost:8000")


def _query(o2_frac: float, pressure_atm: float, flame_config: AmbientFlameConfig | None) -> dict:
    query = {"o2_frac": round(o2_frac, 3), "pressure_atm": round(pressure_atm, 3)}
    if flame_config is not None:
        query["flame_config"] = flame_config
    return query


@st.cache_data(show_spinner=False)
def get_scenario(
    o2_frac: float, pressure_atm: float, flame_config: AmbientFlameConfig | None
) -> ScenarioResponse:
    """GET /scenario: every run of this flame type, with its distance to the atmosphere."""
    params = _query(o2_frac, pressure_atm, flame_config)
    response = requests.get(f"{API_URL}/scenario", params=params, timeout=30)
    response.raise_for_status()
    return ScenarioResponse.model_validate_json(response.content)


@st.cache_data(show_spinner=False)
def get_verdict(
    o2_frac: float, pressure_atm: float, flame_config: AmbientFlameConfig | None
) -> VerdictResponse:
    """POST /verdict: the cited verdict. Calls the LLM, so only on the "Get verdict" button."""
    body = ScenarioQuery(**_query(o2_frac, pressure_atm, flame_config)).model_dump()
    response = requests.post(f"{API_URL}/verdict", json=body, timeout=60)
    response.raise_for_status()
    return VerdictResponse.model_validate_json(response.content)
