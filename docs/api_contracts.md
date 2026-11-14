# API contracts

`src/contracts.py` defines the shape of every request and response of the API. The data track (A) fills these shapes from Postgres; the dashboard track (B) reads them. Both code against the same models, so **the names are fixed**: renaming a field means both tracks edit their code.

Until the real queries land, the API returns fixed JSON files from `data/fixtures/`. They follow the same shapes, so the dashboard can be built now and keeps working when the real data replaces them.

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
| `GET /scenario` | `ScenarioQuery` as query string | `ScenarioResponse` | Which ISS runs sit near this atmosphere? |
| `POST /verdict` | `ScenarioQuery` as JSON body | `VerdictResponse` | In plain words: has this been tested, and where is the evidence? |

Today both return fixtures: `flame_config=solid` gets the "solid" fixture, anything else gets the "Moon" fixture.

## Models

### `ScenarioQuery` (input)

The atmosphere the user asks about.

| Field | Type | Rule | Example |
| --- | --- | --- | --- |
| `o2_frac` | float | 0 to 1 | `0.30` (Moon-base plan) |
| `pressure_atm` | float | > 0 | `0.69` |
| `flame_config` | `FlameConfig` or null | optional | `"solid"` to look at solid materials only |

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
  "outcome": "disruption",
  "distance": 0.065
}
```

- `label` is the fuel, or the material for solid samples (e.g. `"100 micron PMMA film 2 cm wide"`).
- `outcome` can be `null` when it has not been extracted yet (BASS-II outcomes are free text for now).
- `distance` is **not stored** in the database: it is computed for each query, so the same run has a different distance for every question. See [Distance and "nearby"](#distance-and-nearby).

### `ScenarioResponse` (output of `GET /scenario`)

| Field | Meaning | Moon fixture |
| --- | --- | --- |
| `in_envelope` | `true` when at least one run is nearby: "we have tested something like this" | `true` |
| `n_nearby` | Number of runs with `distance <= 1` | `17` |
| `closest` | The 5 nearest runs, nearest first: the "evidence" list | 5 FLEX runs |
| `points` | Every run matching `flame_config`, with its distance: the dots of the scatter map | 402 runs |

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
| `FlameConfig` | `droplet`, `gas_normal`, `gas_inverse`, `solid`, `smoke` |
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
