"""Write data/presets/<key>.json: the verdict of each preset, computed once through /verdict.

The Presets tab reads these files, so a preset click never waits on the LLM and the demo works
even when it is down. Calls the real LLM once per preset (a few cents); needs `make api`
running. Rerun it whenever the verdict prompt or the run data changes.
"""

import requests

from frontend.api import API_URL
from frontend.presets import PRESETS, PRESETS_DIR
from src.contracts import VerdictResponse


def main() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    for preset in PRESETS:
        response = requests.post(f"{API_URL}/verdict", json=preset.query.model_dump(), timeout=120)
        response.raise_for_status()
        verdict = VerdictResponse.model_validate_json(response.content)
        path = PRESETS_DIR / f"{preset.key}.json"
        path.write_text(verdict.model_dump_json(indent=2) + "\n")
        print(f"{preset.key}: {verdict.status}, {len(verdict.citations)} citation(s) -> {path}")


if __name__ == "__main__":
    main()
