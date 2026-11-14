# API contracts

`src/contracts.py` defines the shape of every request and response of the API. The data track (A) fills these shapes from Postgres; the dashboard track (B) reads them. Both code against the same models, so **the names are fixed**: renaming a field means both tracks edit their code.

`/scenario` and `/inverse-flames` read run data from PostgreSQL. `/verdict` currently returns fixed JSON files from `data/fixtures/`. Fixture responses follow the contract shapes so the dashboard can be built before verdict generation is connected to evidence retrieval.

## Try it

```bash
make api            # serves http://127.0.0.1:8000, interactive docs at /docs
curl "http://127.0.0.1:8000/scenario?o2_frac=0.3&pressure_atm=0.69"
curl -X POST http://127.0.0.1:8000/verdict \
     -H 'content-type: application/json' \
     -d '{"o2_frac": 0.3, "pressure_atm": 0.69, "flame_config": "solid"}'
```

`/docs` lists every model with its field descriptions, taken from `src/contracts.py`.

## Endpoints

| Endpoint | Input | Output | Question it answers |
| --- | --- | --- | --- |
| `GET /scenario` | `ScenarioQuery` as query string | `ScenarioResponse` | Which ambient-atmosphere runs sit near this atmosphere? |
| `GET /inverse-flames` | `InverseFlameQuery` as query string | `InverseFlameResponse` | What inverse-flame tests were run at these burner-stream conditions? |
| `POST /verdict` | `ScenarioQuery` as JSON body | `VerdictResponse` | In plain words: has this been tested, and where is the evidence? |

Today `/verdict` returns fixtures: `flame_config=solid` gets the "solid" fixture, anything else gets the "Moon" fixture. `/scenario` and `/inverse-flames` are database-backed.

`/scenario` is for ambient-atmosphere comparisons. It must exclude rows whose `o2_is_ambient` is false. Inverse-flame runs use burner-stream oxygen and are queried through `/inverse-flames` instead.

## Models

### `ScenarioQuery` (input)

The atmosphere the user asks about.

| Field | Type | Rule | Example |
| --- | --- | --- | --- |
| `o2_frac` | float | 0 to 1 | `0.30` (Moon-base plan) |
| `pressure_atm` | float | > 0 | `0.69` |
| `flame_config` | `AmbientFlameConfig` or null | optional | `"solid"` to look at solid materials only |

A value outside the rules is rejected with HTTP 422 before any code runs.

### `Run`

One test run on the ISS, i.e. one row of `psi_dashboard.runs`, cut down to what the dashboard needs.

```json
{
  "run_id": "PSI-69-41",
  "psi_id": "PSI-69",
  "label": "Methanol",
  "o2_frac": 0.3,
  "pressure_atm": 0.703,
  "pressure_source": "measured",
  "outcome": "disruption",
  "distance": 0.065
}
```

- `label` is the fuel, or the material for solid samples (e.g. `"100 micron PMMA film 2 cm wide"`).
- `pressure_source` is `measured`, `assumed`, or `unknown`. BASS-II uses an audit-specified 1 atm assumption; its scenario points and distances should be visibly labeled as assumed-pressure data.
- `outcome` can be `null` when not extracted, or `unknown` when the source does not support a safe classification; original BASS observations remain in raw data.
- `distance` is **not stored** in the database: it is computed for each query, so the same run has a different distance for every question. See [Distance and "nearby"](#distance-and-nearby).

### `ScenarioResponse` (output of `GET /scenario`)

| Field | Meaning | Moon fixture |
| --- | --- | --- |
| `in_envelope` | `true` when at least one run is nearby: "we have tested something like this" | `true` |
| `n_nearby` | Number of runs with `distance <= 1` | `17` |
| `closest` | The 5 nearest runs, nearest first: the "evidence" list | 5 FLEX runs |
| `points` | Every run matching `flame_config`, with its distance: the dots of the scatter map | 402 runs |

### `InverseFlameQuery` and `InverseFlameResponse`

Inverse-flame oxygen is a burner-stream condition, not cabin or ambient oxygen. The endpoint uses explicitly named `burner_o2_min` and `burner_o2_max` filters and returns `burner_o2_frac`; it does not calculate ambient-scenario distance.

Query filters are optional except for pagination defaults. Bounds are inclusive; if both ends of a range are supplied, the minimum must be no greater than the maximum. Fuel matching is case-insensitive. Invalid bounds are rejected with HTTP 422.

| Field | Type / rule | Meaning |
| --- | --- | --- |
| `burner_o2_min`, `burner_o2_max` | float, 0 to 1 | Inclusive burner-stream oxygen bounds |
| `pressure_min_atm`, `pressure_max_atm` | float, > 0 | Inclusive pressure bounds in atm |
| `fuel` | string | Fuel filter |
| `outcome` | `Outcome` | Canonical outcome filter |
| `limit` | integer, 1 to 500; default 200 | Maximum runs returned |
| `offset` | integer, >= 0; default 0 | Pagination offset |

Each response run includes `run_id`, `psi_id`, `label`, `burner_o2_frac`, `pressure_atm`, `outcome`, and optional `outcome_detail` (for source details such as `flash`, `SE`, or `FT`). `total` is the count of matching runs before pagination. A response example:

```json
{
  "total": 154,
  "runs": [
    {
      "run_id": "PSI-159:inverse:21196 A1",
      "psi_id": "PSI-159",
      "label": "Methane",
      "burner_o2_frac": 0.21,
      "pressure_atm": 1.0,
      "outcome": "ignited",
      "outcome_detail": "SE"
    }
  ]
}
```

### `VerdictResponse` (output of `POST /verdict`)

```json
{
  "status": "tested",
  "text": "Tested. 17 FLEX droplet runs on the ISS sat at 0.30 to 0.34 O2 and 0.70 to 0.71 atm. ...",
  "citations": [{"psi_id": "PSI-69", "doc_title": "FLEX Test Plan", "page": 6}]
}
```

- `status`: `tested` (runs nearby), `sparse` (only a few), `untested` (none).
- `text`: the explanation shown to the user. Later written by an LLM from the document chunks.
- `citations`: one `Citation` per source page: study, document title, page number in the PDF.

## Allowed values

These are `Literal` types: plain strings restricted to a closed list. They hold the same values as the CHECK constraints on `psi_dashboard.runs`, so a database row passes straight through, and a test (`tests/test_api_contracts.py`) fails if the two drift apart.

| Type | Values |
| --- | --- |
| `Outcome` | `extinction`, `disruption`, `completion`, `ignited`, `no_ignition`, `blowoff`, `unknown` |
| `FlameConfig` (stored run categories) | `droplet`, `gas_normal`, `gas_inverse`, `solid`, `smoke` |
| `AmbientFlameConfig` (`/scenario`) | `droplet`, `gas_normal`, `solid`, `smoke` |
| `VerdictStatus` | `tested`, `sparse`, `untested` |

To list them in Python: `typing.get_args(Outcome)`.

## Distance and "nearby"

```
distance = sqrt( ((run.o2_frac - query.o2_frac) / 0.05)^2 + ((run.pressure_atm - query.pressure_atm) / 0.2)^2 )
```

A difference of 0.05 in O2, or of 0.2 atm, each count as 1. A run is **nearby** when `distance <= 1`: roughly within ±0.05 O2 and ±0.2 atm of the query.

**This rule is a placeholder** (in `scripts/make_fixtures.py`), not an agreed definition. Changing it changes `n_nearby` and `in_envelope`: for the Moon query, the next run outside the 17 sits at distance 1.003.

## Fixtures

| File | Query | What it shows |
| --- | --- | --- |
| `scenario_moon.json` | 0.30 O2, 0.69 atm | In envelope: the 17 FLEX droplet runs at O2 >= 0.29 and 0.70–0.71 atm (15 disruption, 2 completion) |
| `scenario_moon_solid.json` | same, `flame_config=solid` | Out of envelope: the closest solid run is BASS-II at 0.222 O2 / 1 atm |
| `verdict_tested.json` | — | Hand-written `tested` verdict, cites the FLEX Test Plan p. 6 |
| `verdict_untested.json` | — | Hand-written `untested` verdict, cites the BASS-II Summary Report p. 113 |

The two scenario fixtures are built from the FLEX (PSI-69) and BASS-II (PSI-25) CSVs in `psi_data/`, because the `runs` table is not loaded yet. Regenerate them with `make fixtures`; the API validates every fixture against its model when it starts.

## Changing a contract

1. Agree on the change with the other track first.
2. Edit `src/contracts.py`, then run `make fixtures` and `make test`.
3. New `Outcome` or `FlameConfig` values go in the `runs` table's CHECK constraint too.
4. Do not start any name with `Test`: pytest would try to collect it as a test class.

## Open questions

- The real "nearby" rule (scales and threshold), and where `sparse` starts.
- BASS-II has no pressure column: the fixtures assume 1 atm.
- The `run_id` format (`PSI-69-25` here) should match whatever the `runs` loader uses.
