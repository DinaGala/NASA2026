# Verdict agent (`POST /verdict`)

`POST /verdict` answers one fixed question for a scenario: **"What does NASA's microgravity evidence show about fire at these conditions, and how sure is it?"** A language model writes the answer from two sources, the ISS test runs and the investigations' papers, and code checks everything that must not be wrong before the answer leaves the API.

```bash
curl -X POST http://127.0.0.1:8000/verdict -H 'content-type: application/json' \
     -d '{"o2_frac": 0.30, "pressure_atm": 0.69}'
```

An illustrative response (the wording varies from run to run; the status and the numbers do not):

```json
{
  "status": "tested",
  "text": "Tested. 17 FLEX droplet runs sat near 0.3 O2 and 0.69 atm: 15 ended in disruption, 2 in completion, none in extinction. ...",
  "citations": [{"psi_id": "PSI-69", "doc_title": "FLEX Test Plan", "page": 6}]
}
```

## How it works

```
POST /verdict {o2_frac, pressure_atm, flame_config?}
   │
   ▼
answer(deps)  ── src/agent/verdict_agent.py
   │
   ├─ verdict_agent (PydanticAI, model = LLM_MODEL)
   │     ├─ tool find_runs()             → RunEvidence: status, counts, closest runs  (code)
   │     ├─ tool search_papers(question) → passages with page numbers                 (code)
   │     └─ writes a VerdictResponse                                                  (model)
   │            │
   │            ▼
   │     output validator: status, numbers, citations   ── fails → ModelRetry, model fixes it (max 2)
   │
   └─ any failure (bad key, model down, retries or limits used up)
         → fallback_verdict(): written by code from find_runs, no citations
```

The split is deliberate: **the model writes, code decides.** The model is good at turning counts and passages into three readable sentences. It is not trusted with anything a reader could act on wrongly: the status, the numbers, the sources.

### The tools

**`find_runs()`** calls the same function as `GET /scenario` (`get_scenario_data`), directly, not over HTTP. It takes no arguments: the scenario is the one in the request, so the model cannot look up different conditions. It returns a `RunEvidence`, not the whole `ScenarioResponse`:

| Field | Example (Moon) | Why |
| --- | --- | --- |
| `status` | `tested` | From the rule below, so the model never decides it |
| `n_nearby`, `runs_compared` | 17, 573 | |
| `nearby_outcomes` | `{"disruption": 15, "completion": 2}` | Counted by code, so the model never counts |
| `nearby_o2_range`, `nearby_pressure_range` | `[0.3, 0.34]`, `[0.7, 0.71]` | |
| `nearby_studies` | `["PSI-69"]` | Which investigations the paper search is limited to |
| `closest` | 5 runs | Each with study, label, conditions, outcome, distance |

`ScenarioResponse.points` lists every run for the map, hundreds of them. Sending it to the model would add tens of thousands of tokens to every request and invite it to count by itself; the counts it needs are computed in code instead.

**`search_papers(question)`** wraps [`search_papers`](paper_search.md), restricted to the studies of the nearby runs, or of the closest runs when none is nearby. Hits from Word files are dropped: they have no page number, and a `Citation` requires one.

### The status rule

| Nearby runs (`distance <= 1`) | `status` |
| --- | --- |
| 3 or more | `tested` |
| 1 or 2 | `sparse` |
| 0 | `untested` |

`status_for()` in `src/agent/verdict_agent.py` (`TESTED_MIN_RUNS = 3`). It is a rule, not a judgement call: a model could return "tested" with 0 runs nearby, the one bug that would hurt the demo most. Note that `in_envelope` in `/scenario` is true from 1 run, so it means "tested or sparse".

### The output validator

`check_verdict()` runs on every verdict the model proposes. When a check fails, it raises `ModelRetry` with what to fix, and the model gets another try (at most 2):

1. **Status** equals the rule's.
2. **Untested:** the text starts with "Untested" and names the closest run (its label or run ID).
3. **Every number in the text appears in the `find_runs` output.** PSI IDs, run IDs, page references, identifiers such as CO2 or FLEX-2, and numbers inside cited document titles are ignored. A number may be rounded as written (0.703 → "0.70") or a fraction written as a percent (0.3 → "30%"). Numbers written as words ("seventeen") are not checked. In fire safety an invented number is the one mistake that cannot be afforded.
4. **Every citation exists** in `psi_dashboard.chunks` (same study, document title and page).
5. **Zero citations** only when `search_papers` ran and found nothing.

### The fallback

`answer()` catches any exception from the agent (authentication, network, model errors, retries or the request limit used up) and returns `fallback_verdict()`: status from the rule, numbers straight from `find_runs`, no citations, and a sentence saying the written analysis is unavailable. The demo always gets an answer. If the database itself is down, `find_runs` fails too and the request errors.

### Limits

- `retries={"output": 2}`: at most 2 corrections of a rejected verdict. (The issue says `output_retries=2`; in PydanticAI 2.x it is written this way.)
- `UsageLimits(request_limit=8)`: at most 8 model calls per verdict.

## Configuration

In `.env`:

```bash
LLM_MODEL=anthropic:claude-opus-5-5   # any PydanticAI model string; this is the default
ANTHROPIC_API_KEY=sk-ant-...          # the key of that provider
```

Claude Opus 5.5 rejects a forced tool choice; PydanticAI knows this and asks for the structured verdict without forcing it, so the default works as is.

## Tests

`tests/test_verdict_agent.py`:

| Group | Model | What |
| --- | --- | --- |
| `StatusRuleTests` | none | 0 / 1-2 / 3+ |
| `OutputValidatorTests` | none | Each check, including **an invented number is rejected** |
| `AgentWiringTests` | `TestModel`, `FunctionModel` | Both tools are called; an invented number is sent back and corrected; a failing model falls back |
| `VerdictEndpointTests` | `TestModel`, `FunctionModel` | `POST /verdict` returns the agent's verdict, or the fallback |
| `LiveVerdictTests` | the real one | Moon droplet → tested, counts match, ≥ 1 citation; Moon solid → untested, names the closest run, ≥ 1 citation; a broken API key → valid fallback |

`make test` runs everything except the live tests (no LLM cost, no network). `make test-live` runs the live ones: it needs the database loaded (`make load-chunks` and the runs loaders) and, for the two smoke tests, a real API key in `.env`.

## Changes from the issue

The project moved on after #8 was written:

- **`find_runs` uses the real `/scenario` code.** #11 landed `get_scenario_data`, so the fixture is no longer needed; `Deps.find_scenario` can still swap the source without touching the agent.
- **`find_runs` takes no query and returns `RunEvidence`**, for the reasons above, instead of `find_runs(query) → ScenarioResponse`.
- **"tested: n_nearby ≥ 3 (the same rule as `in_envelope`)":** `in_envelope` is `n_nearby > 0` in the merged `/scenario` code, so the two rules differ. The status follows the issue's 3-level rule; `in_envelope` is unchanged.
- **Moon solid's closest run is BASS (PSI-26), not BASS-II.** The BASS-II runs are loaded without their O2 value (#12 does not map "Calibrated initial O2 % by vol"), so they never appear in solid scenarios. The agent names whichever run `find_runs` reports as closest, so it picks BASS-II up once the loader maps that column.
