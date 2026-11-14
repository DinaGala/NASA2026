"""Temporary test page (#16): the risk surface on real data, at a fixed scenario.

Sliders, metrics, presets and the verdict come in the next issues (spec §3–4).
"""

import requests
import streamlit as st

from frontend.api import API_URL, get_scenario
from frontend.charts import risk_surface
from src.contracts import ScenarioQuery

MOON = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69)
FLAME_TYPES = {"droplet": "Droplet", "gas_normal": "Gas", "solid": "Solid"}

st.set_page_config(page_title="Flame in Freefall", layout="wide")
st.title("Flame in Freefall")

flame_config = st.selectbox("Flame type", list(FLAME_TYPES), format_func=FLAME_TYPES.get)
st.caption(f"Scenario: Moon habitat · O₂ 30% · 0.69 atm ({0.69 * 14.7:.1f} psi)")

try:
    scenario = get_scenario(MOON.o2_frac, MOON.pressure_atm, flame_config)
except requests.RequestException:
    st.error(f"API unreachable ({API_URL})")
else:
    fig = risk_surface(scenario.points, MOON, flame_config)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(f"{len(scenario.points)} runs · {scenario.n_nearby} within distance 1")
