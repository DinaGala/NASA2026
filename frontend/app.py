"""Flame in Freefall: pick a cabin atmosphere, see what NASA's microgravity runs found near it.

Two tabs (spec §2): Explore (sliders, live surface, verdict on a button) and Presets (four fixed
scenarios with precomputed verdicts). Each tab renders its own results, since Streamlit can't
switch tabs from code. All data comes from the API (frontend/api.py).
"""

from collections import Counter

import requests
import streamlit as st

from frontend.api import get_scenario, get_verdict
from frontend.charts import OUTCOME_VALUE, evidence_status, risk_surface
from frontend.presets import PRESETS, PSI_PER_ATM, Preset, load_verdict
from src.contracts import ScenarioQuery, ScenarioResponse, VerdictResponse

FLAME_TYPES = {"droplet": "Droplet", "gas_normal": "Gas", "solid": "Solid"}
STATUS_COLOR = {"Tested": "green", "Sparse": "orange", "Untested": "red"}
CHART_CONFIG = {"displayModeBar": False}


def fetch_scenario(query: ScenarioQuery) -> ScenarioResponse | None:
    """/scenario, or None after showing the error, so the rest of the page still renders."""
    try:
        return get_scenario(query.o2_frac, query.pressure_atm, query.flame_config)
    except (requests.ConnectionError, requests.Timeout):
        st.error("API unreachable")
    except requests.RequestException as error:
        st.error(f"API error: {error}")
    return None


def kept_burning_nearby(scenario: ScenarioResponse, flame_config: str) -> str:
    """Share of nearby runs with a known outcome that kept burning (spec §3.2)."""
    nearby = [r for r in scenario.points if r.distance <= 1]
    if not nearby:
        return "—"
    mapping = OUTCOME_VALUE.get(flame_config, {})
    known = [mapping[r.outcome] for r in nearby if r.outcome in mapping]
    if not known:
        return "no outcomes"
    return f"{sum(known) / len(known):.0%}"


def metrics_row(query: ScenarioQuery, psi: float, scenario: ScenarioResponse) -> None:
    status = evidence_status(scenario.n_nearby)
    runs = f"{scenario.n_nearby} run{'s' * (scenario.n_nearby != 1)}"
    evidence, nearby, partial, burning = st.columns(4)
    evidence.metric("Evidence", status, delta=runs, delta_color=STATUS_COLOR[status],
                    delta_arrow="off")
    nearby.metric("Runs nearby", scenario.n_nearby)
    partial.metric("O₂ partial pressure", f"{query.o2_frac * psi:.1f} psi")
    burning.metric("Kept burning nearby", kept_burning_nearby(scenario, query.flame_config))


def research_tags(scenario: ScenarioResponse, key: str) -> None:
    """One read-only tag per study among the nearby runs, else among the 5 closest (§3.4)."""
    nearby = [r for r in scenario.points if r.distance <= 1]
    runs, label = (nearby, "Research behind this point") if nearby else (
        scenario.closest, "Closest studies (not nearby)")
    if not runs:
        st.caption("No runs of this flame type.")
        return
    counts = Counter((r.acronym or r.psi_id, r.psi_id) for r in runs)
    tags = [f"{name} · {psi_id} · {n} run{'s' * (n != 1)}"
            for (name, psi_id), n in counts.most_common()]
    st.pills(label, tags, disabled=True, key=f"{key}_tags")


def show_verdict(verdict: VerdictResponse, scenario: ScenarioResponse | None) -> None:
    """Badge, text and citations, exactly as the backend wrote them (§3.5)."""
    status = verdict.status.capitalize()
    st.badge(status, color=STATUS_COLOR[status])
    st.write(verdict.text)
    if verdict.citations:
        st.markdown("\n".join(
            f"- {c.doc_title}, p. {c.page} ({c.psi_id})" for c in verdict.citations))
    elif scenario is not None and scenario.points:
        # The backend's fallback verdict: written by code when the LLM fails, never cited.
        st.caption("Explanation unavailable — showing the facts only")


def live_verdict(query: ScenarioQuery, scenario: ScenarioResponse | None) -> None:
    """The "Get verdict" button: the only place the Explore tab calls the LLM."""
    state = st.session_state
    state.setdefault("verdicts", {})
    key = (query.o2_frac, query.pressure_atm, query.flame_config)

    header, button = st.columns([4, 1], vertical_alignment="center")
    header.subheader("Verdict")
    button.button("Get verdict", disabled=state.get("verdict_loading", False),
                  on_click=lambda: state.update(verdict_loading=True, verdict_error=None))

    if state.get("verdict_loading"):
        with st.spinner("Analysing the evidence…"):
            try:
                state.verdicts[key] = get_verdict(*key)
            except (requests.ConnectionError, requests.Timeout):
                state.verdict_error = "API unreachable"
            except requests.RequestException as error:
                state.verdict_error = f"Verdict unavailable: {error}"
        state.verdict_loading = False
        st.rerun()  # re-enables the button

    if state.get("verdict_error"):
        st.error(state.verdict_error)
    elif key in state.verdicts:
        show_verdict(state.verdicts[key], scenario)
    else:
        st.caption("Press Get verdict for a cited explanation of this point (takes a few seconds).")


def analysis(
    query: ScenarioQuery, psi: float, key: str, verdict: VerdictResponse | None = None
) -> None:
    """Metrics, risk surface and research tags; then the verdict (live, or precomputed)."""
    scenario = fetch_scenario(query)
    if scenario is not None:
        metrics_row(query, psi, scenario)
        fig = risk_surface(scenario.points, query, query.flame_config)
        st.plotly_chart(fig, width="stretch", config=CHART_CONFIG, key=f"{key}_chart")
        research_tags(scenario, key)

    if key == "explore":
        live_verdict(query, scenario)
    else:
        st.subheader("Verdict")
        if verdict is None:
            st.caption("No precomputed verdict: run `make preset-verdicts`.")
        else:
            show_verdict(verdict, scenario)


def explore_tab() -> None:
    o2_col, psi_col, flame_col = st.columns([2, 2, 1.4])
    o2_percent = o2_col.slider("Oxygen (%)", 10, 45, 30, step=1)
    psi_now = st.session_state.get("psi", 10.2)
    psi = psi_col.slider(f"Pressure (psi) · {psi_now / PSI_PER_ATM:.2f} atm", 5.0, 20.0, 10.2,
                         step=0.1, key="psi")
    flame_config = flame_col.segmented_control(
        "Flame type", list(FLAME_TYPES), format_func=FLAME_TYPES.get, default="droplet",
        required=True,
    )
    query = ScenarioQuery(o2_frac=o2_percent / 100, pressure_atm=round(psi / PSI_PER_ATM, 3),
                          flame_config=flame_config)
    analysis(query, psi, key="explore")


def preset_status(preset: Preset) -> str | None:
    try:
        scenario = get_scenario(preset.o2_frac, preset.pressure_atm, preset.flame_config)
    except requests.RequestException:
        verdict = load_verdict(preset)
        return verdict.status.capitalize() if verdict else None
    return evidence_status(scenario.n_nearby)


def presets_tab() -> None:
    state = st.session_state
    state.setdefault("preset", "moon_habitat")
    for row in (PRESETS[:2], PRESETS[2:]):
        for column, preset in zip(st.columns(2), row):
            with column.container(border=True):
                st.image(str(preset.picture), width="stretch")
                st.markdown(f"**{preset.name}**")
                st.caption(f"O₂ {preset.o2_frac:.0%} · {preset.psi} psi ({preset.pressure_atm:.2f} atm)"
                           f" · {FLAME_TYPES[preset.flame_config]} — {preset.role}")
                badge, button = st.columns([2, 1], vertical_alignment="center")
                status = preset_status(preset)
                if status:
                    with badge:
                        st.badge(status, color=STATUS_COLOR[status])
                selected = state.preset == preset.key
                button.button("Selected" if selected else "Open", key=f"open_{preset.key}",
                              type="primary" if selected else "secondary", width="stretch",
                              on_click=lambda key=preset.key: state.update(preset=key))

    preset = next(p for p in PRESETS if p.key == state.preset)
    st.divider()
    st.subheader(preset.name)
    analysis(preset.query, preset.psi, key=f"preset_{preset.key}", verdict=load_verdict(preset))


st.set_page_config(page_title="Flame in Freefall", layout="wide")
st.title("Flame in Freefall")
st.caption("What NASA's microgravity combustion runs found near a cabin atmosphere. "
           "No evidence nearby means untested: the app never extrapolates.")

explore, presets = st.tabs(["Explore", "Presets"])
with explore:
    explore_tab()
with presets:
    presets_tab()
