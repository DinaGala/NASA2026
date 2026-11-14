"""Write the API fixtures to data/fixtures/ from the FLEX and BASS-II test tables.

The runs table is not loaded yet, so the runs come straight from the CSVs in psi_data/.
"""

import csv
import json
import math
from pathlib import Path

from src.constants import O2_SCALE, PRESSURE_SCALE
from src.contracts import Citation, Run, ScenarioQuery, ScenarioResponse, VerdictResponse

ROOT = Path(__file__).resolve().parents[1]
PSI_DATA = ROOT / "psi_data"
FIXTURES = ROOT / "data" / "fixtures"

FLEX_CSV = PSI_DATA / "PSI-69" / "Experimental table" / "PSI-69_Experimental table_FLEX.csv"
BASS_II_CSV = PSI_DATA / "PSI-25" / "Experimental table" / "PSI-25_Experimental table_BASS-II.csv"

MMHG_PER_ATM = 760.0
# The BASS-II table has no pressure column; the runs were at ISS cabin pressure.
BASS_II_PRESSURE_ATM = 1.0

N_CLOSEST = 5

MOON = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69)
MOON_SOLID = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69, flame_config="solid")

VERDICT_TESTED = VerdictResponse(
    status="tested",
    text=(
        "Tested. 17 FLEX droplet runs on the ISS sat at 0.30 to 0.34 O2 and 0.70 to 0.71 atm. "
        "15 ended in disruption and 2 burned to completion; none extinguished."
    ),
    citations=[Citation(psi_id="PSI-69", doc_title="FLEX Test Plan", page=6)],
)
VERDICT_UNTESTED = VerdictResponse(
    status="untested",
    text=(
        "Untested for solid materials. No solid run sits near 0.30 O2 and 0.69 atm. "
        "The closest is BASS-II, at 0.22 O2 and about 1 atm."
    ),
    citations=[Citation(psi_id="PSI-25", doc_title="BASS-II Summary Report", page=113)],
)


def to_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def load_flex() -> list[dict]:
    """FLEX droplet runs (PSI-69). The file is cp1252, not UTF-8."""
    outcomes = {"extinction", "disruption", "completion"}
    runs = []
    with FLEX_CSV.open(encoding="cp1252", newline="") as handle:
        for row in csv.DictReader(handle):
            row = {key.strip(): value for key, value in row.items()}
            pressure_mmhg = to_float(row["Ambient pressure; mmHg"])
            outcome = row["Test end"].strip().lower()
            runs.append(
                {
                    "run_id": f"PSI-69-{row['FLEX Test #'].strip()}",
                    "psi_id": "PSI-69",
                    "flame_config": "droplet",
                    "label": row["Fuel"].strip(),
                    "o2_frac": to_float(row["O initial ambient composition; mole fraction"]),
                    "pressure_atm": pressure_mmhg / MMHG_PER_ATM if pressure_mmhg else None,
                    "outcome": outcome if outcome in outcomes else "unknown",
                }
            )
    return runs


def load_bass_ii() -> list[dict]:
    """BASS-II solid runs (PSI-25). Outcomes are free text, so they stay empty."""
    runs = []
    with BASS_II_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row = {key.strip(): value for key, value in row.items()}
            o2_percent = to_float(row["Calibrated  initial O2 % by vol"])
            runs.append(
                {
                    "run_id": f"PSI-25-{row['Test #'].strip()}",
                    "psi_id": "PSI-25",
                    "flame_config": "solid",
                    "label": " ".join(row["Fuel Sample Material"].split()),
                    "o2_frac": o2_percent / 100 if o2_percent is not None else None,
                    "pressure_atm": BASS_II_PRESSURE_ATM,
                    "outcome": None,
                }
            )
    return runs


def distance(run: dict, query: ScenarioQuery) -> float:
    return math.hypot(
        (run["o2_frac"] - query.o2_frac) / O2_SCALE,
        (run["pressure_atm"] - query.pressure_atm) / PRESSURE_SCALE,
    )


def answer(query: ScenarioQuery, runs: list[dict]) -> ScenarioResponse:
    candidates = [
        run
        for run in runs
        if run["o2_frac"] is not None
        and run["pressure_atm"] is not None
        and query.flame_config in (None, run["flame_config"])
    ]
    points = sorted(
        (
            Run(
                run_id=run["run_id"],
                psi_id=run["psi_id"],
                label=run["label"],
                o2_frac=round(run["o2_frac"], 4),
                pressure_atm=round(run["pressure_atm"], 3),
                outcome=run["outcome"],
                distance=round(distance(run, query), 3),
            )
            for run in candidates
        ),
        key=lambda run: (run.distance, run.run_id),
    )
    n_nearby = sum(run.distance <= 1 for run in points)
    return ScenarioResponse(
        in_envelope=n_nearby > 0,
        n_nearby=n_nearby,
        closest=points[:N_CLOSEST],
        points=points,
    )


def write(name: str, model) -> None:
    path = FIXTURES / name
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2) + "\n")
    print(f"Wrote {path.relative_to(ROOT)}")


def main() -> None:
    runs = load_flex() + load_bass_ii()
    run_ids = [run["run_id"] for run in runs]
    assert len(run_ids) == len(set(run_ids)), "duplicate run_id"

    moon = answer(MOON, runs)
    nearby = [run for run in moon.points if run.distance <= 1]
    assert moon.n_nearby == 17, moon.n_nearby
    assert all(run.psi_id == "PSI-69" and run.o2_frac >= 0.29 for run in nearby)
    assert all(0.6 <= run.pressure_atm <= 0.8 for run in nearby)

    moon_solid = answer(MOON_SOLID, runs)
    assert not moon_solid.in_envelope
    assert moon_solid.closest[0].psi_id == "PSI-25"

    FIXTURES.mkdir(parents=True, exist_ok=True)
    write("scenario_moon.json", moon)
    write("scenario_moon_solid.json", moon_solid)
    write("verdict_tested.json", VERDICT_TESTED)
    write("verdict_untested.json", VERDICT_UNTESTED)


if __name__ == "__main__":
    main()
