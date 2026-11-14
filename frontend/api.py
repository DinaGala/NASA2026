"""The dashboard's only way to the data: HTTP calls to the PSI Flame API (spec §1)."""

import os

import requests
import streamlit as st

from src.contracts import AmbientFlameConfig, ScenarioResponse

API_URL = os.environ.get("API_URL", "http://localhost:8000")


@st.cache_data(show_spinner=False)
def get_scenario(
    o2_frac: float, pressure_atm: float, flame_config: AmbientFlameConfig | None
) -> ScenarioResponse:
    """GET /scenario: every run of this flame type, with its distance to the atmosphere."""
    params = {"o2_frac": round(o2_frac, 3), "pressure_atm": round(pressure_atm, 3)}
    if flame_config is not None:
        params["flame_config"] = flame_config
    response = requests.get(f"{API_URL}/scenario", params=params, timeout=30)
    response.raise_for_status()
    return ScenarioResponse.model_validate_json(response.content)
