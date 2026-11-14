"""The risk surface: the share of runs that kept burning across O₂ × pressure (spec §3.3).

The surface never extrapolates: a grid cell with fewer than MIN_RUNS runs within distance 1
stays blank. Distances use the same scales as /scenario (src/constants.py).
"""

import numpy as np
import plotly.graph_objects as go

from src.constants import O2_SCALE, PRESSURE_SCALE
from src.contracts import AmbientFlameConfig, Run, ScenarioQuery

# "Kept burning" (1) or "went out / didn't burn" (0), per flame type. Outcomes missing here
# (unknown, None, blowoff...) count toward the blank mask but not toward the share.
OUTCOME_VALUE: dict[str, dict[str, float]] = {
    "droplet": {"disruption": 1.0, "completion": 1.0, "extinction": 0.0},
    "gas_normal": {"ignited": 1.0, "no_ignition": 0.0},
}
COLORBAR_LABEL = {"droplet": "kept burning", "gas_normal": "ignited"}

O2_GRID = np.round(np.arange(0.10, 0.45 + 1e-9, 0.005), 3)
PRESSURE_GRID = np.round(np.arange(0.3, 1.5 + 1e-9, 0.01), 2)
MIN_RUNS = 3  # runs within distance 1 needed to draw a cell
WEIGHT_K = 1.25  # w = exp(-WEIGHT_K * d²)
# Runs with d² <= this enter the weighted average. Wider than the mask: with d² <= 1 the
# surface showed steps and rings at the edges of each cluster (spec §3.3, #16).
AVERAGE_RADIUS_SQ = 4.0

BLUE, NEUTRAL, RED = "#2F6FB8", "#EDE8DE", "#C8423D"
RUN_DOT = "rgba(85,85,85,0.55)"
STATUS_COLOR = {"TESTED": "#21C354", "SPARSE": "#FFBD45", "UNTESTED": "#FF4B4B"}


def evidence_status(n_nearby: int) -> str:
    """Tested (3+ runs nearby), Sparse (1–2) or Untested (0), spec §3.2."""
    if n_nearby >= MIN_RUNS:
        return "Tested"
    return "Sparse" if n_nearby > 0 else "Untested"


def surface_grid(
    points: list[Run],
    flame_config: AmbientFlameConfig,
    average_radius_sq: float = AVERAGE_RADIUS_SQ,
) -> np.ndarray:
    """Share kept burning on the (pressure, O₂) grid; NaN where the cell stays blank.

    Shape (len(PRESSURE_GRID), len(O2_GRID)), rows = pressure, as go.Contour wants z.
    """
    runs = [r for r in points if r.o2_frac is not None and r.pressure_atm is not None]
    mapping = OUTCOME_VALUE.get(flame_config, {})
    o2 = np.array([r.o2_frac for r in runs])
    pressure = np.array([r.pressure_atm for r in runs])
    value = np.array([mapping.get(r.outcome, np.nan) for r in runs])
    known = ~np.isnan(value)

    # d²[pressure, o2, run]
    d_sq = ((O2_GRID[None, :, None] - o2) / O2_SCALE) ** 2 + (
        (PRESSURE_GRID[:, None, None] - pressure) / PRESSURE_SCALE
    ) ** 2
    n_nearby = (d_sq <= 1).sum(axis=2)

    weight = np.exp(-WEIGHT_K * d_sq) * ((d_sq <= average_radius_sq) & known)
    total = weight.sum(axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = (weight * np.nan_to_num(value)).sum(axis=2) / total
    share[(n_nearby < MIN_RUNS) | (total == 0)] = np.nan
    return share


def _blank_corner(share: np.ndarray) -> tuple[float, float, str, str]:
    """A corner of the grid with no surface, for the "No test data" note."""
    corners = [
        (-1, 0, "left", "top"),
        (-1, -1, "right", "top"),
        (0, 0, "left", "bottom"),
        (0, -1, "right", "bottom"),
    ]
    for row, col, xanchor, yanchor in corners:
        if np.isnan(share[row, col]):
            return O2_GRID[col], PRESSURE_GRID[row], xanchor, yanchor
    return O2_GRID[0], PRESSURE_GRID[-1], "left", "top"


def risk_surface(
    points: list[Run],
    scenario: ScenarioQuery,
    flame_config: AmbientFlameConfig,
    *,
    average_radius_sq: float = AVERAGE_RADIUS_SQ,
    neutral: str = NEUTRAL,
) -> go.Figure:
    """The main chart: smoothed share kept burning, run dots, and a crosshair on the scenario."""
    fig = go.Figure()
    has_surface = flame_config in OUTCOME_VALUE
    annotations = []

    if has_surface:
        share = surface_grid(points, flame_config, average_radius_sq)
        label = COLORBAR_LABEL[flame_config]
        fig.add_trace(
            go.Contour(
                x=O2_GRID,
                y=PRESSURE_GRID,
                z=share,
                zmin=0,
                zmax=1,
                connectgaps=False,
                contours=dict(coloring="heatmap", start=0, end=1, size=0.1),
                line=dict(width=0.4, smoothing=1.2),
                colorscale=[[0, BLUE], [0.5, neutral], [1, RED]],
                colorbar=dict(
                    title=dict(text=label, side="right"),
                    thickness=8,
                    tickformat=".0%",
                    outlinewidth=0,
                ),
                hoverongaps=False,
                hovertemplate=f"O₂ %{{x:.0%}} · %{{y:.2f}} atm · {label} %{{z:.0%}}<extra></extra>",
            )
        )
        x, y, xanchor, yanchor = _blank_corner(share)
        annotations.append(
            dict(x=x, y=y, xanchor=xanchor, yanchor=yanchor, text="No test data",
                 showarrow=False, font=dict(color="rgba(237,232,222,0.45)"))
        )
    else:
        annotations.append(
            dict(x=0.5, y=1.0, xref="paper", yref="paper", yanchor="bottom",
                 text="Outcomes not recorded for solids", showarrow=False,
                 font=dict(color="rgba(237,232,222,0.7)"))
        )

    visible = [r for r in points if r.o2_frac is not None and r.pressure_atm is not None]
    fig.add_trace(
        go.Scatter(
            x=[r.o2_frac for r in visible],
            y=[r.pressure_atm for r in visible],
            mode="markers",
            marker=dict(size=4, color=RUN_DOT),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    # Crosshair: two dotted lines through the scenario, an open ring, and the status.
    status = evidence_status(sum(1 for r in points if r.distance <= 1)).upper()
    crosshair = dict(color="rgba(237,232,222,0.6)", width=1, dash="dot")
    fig.add_shape(type="line", xref="x", yref="paper", x0=scenario.o2_frac, x1=scenario.o2_frac,
                  y0=0, y1=1, line=crosshair)
    fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1,
                  y0=scenario.pressure_atm, y1=scenario.pressure_atm, line=crosshair)
    fig.add_trace(
        go.Scatter(
            x=[scenario.o2_frac],
            y=[scenario.pressure_atm],
            mode="markers",
            marker=dict(size=15, symbol="circle-open", color=NEUTRAL, line=dict(width=2)),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    annotations.append(
        dict(x=scenario.o2_frac, y=scenario.pressure_atm, xshift=12, yshift=12,
             xanchor="left", yanchor="bottom", text=status, showarrow=False,
             font=dict(color=STATUS_COLOR[status], size=11))
    )

    grid = "rgba(237,232,222,0.08)"
    fig.update_layout(
        annotations=annotations,
        font=dict(size=11, family="monospace"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(128,128,128,0.07)",
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(title="O₂", tickformat=".0%", range=[O2_GRID[0], O2_GRID[-1]],
                   gridcolor=grid, zeroline=False),
        yaxis=dict(title="pressure (atm)", range=[PRESSURE_GRID[0], PRESSURE_GRID[-1]],
                   gridcolor=grid, zeroline=False),
    )
    return fig
