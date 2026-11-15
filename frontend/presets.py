"""The four fixed scenarios of the Presets tab (spec §4) and their precomputed verdicts.

scripts/make_preset_verdicts.py writes data/presets/<key>.json once, so clicking a preset never
calls the LLM. Regenerate the files whenever the prompt or the data changes.
"""

from dataclasses import dataclass
from pathlib import Path

from src.contracts import AmbientFlameConfig, ScenarioQuery, VerdictResponse

ROOT = Path(__file__).resolve().parents[1]
PRESETS_DIR = ROOT / "data" / "presets"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
PSI_PER_ATM = 14.7


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    o2_frac: float
    psi: float
    flame_config: AmbientFlameConfig
    role: str

    @property
    def pressure_atm(self) -> float:
        return round(self.psi / PSI_PER_ATM, 2)

    @property
    def query(self) -> ScenarioQuery:
        return ScenarioQuery(
            o2_frac=self.o2_frac, pressure_atm=self.pressure_atm, flame_config=self.flame_config
        )

    @property
    def picture(self) -> Path:
        return ASSETS_DIR / f"{self.key}.svg"


PRESETS = [
    Preset("earth_air", "Earth air", 0.21, 14.7, "droplet",
           "The reference: most flames went out"),
    Preset("moon_habitat", "Moon habitat", 0.30, 10.2, "droplet",
           "The warning: 17 runs, none extinguished"),
    Preset("moon_fabric", "Moon habitat · fabric", 0.30, 10.2, "solid",
           "The gap: no solid tested there"),
    Preset("spacesuit", "Spacesuit", 1.00, 4.3, "droplet",
           "The extreme: beyond all the data"),
]


def load_verdict(preset: Preset) -> VerdictResponse | None:
    """The precomputed verdict, or None when make_preset_verdicts.py hasn't been run."""
    path = PRESETS_DIR / f"{preset.key}.json"
    if not path.exists():
        return None
    return VerdictResponse.model_validate_json(path.read_text())
