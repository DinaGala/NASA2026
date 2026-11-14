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

## What we built

| Piece | Where | Role |
| --- | --- | --- |
| `verdict_agent` | `src/agent/verdict_agent.py` | A PydanticAI agent: instructions, two tools, a structured output (`VerdictResponse`) |
| `find_runs` tool | same file | The ISS runs near the scenario, summarised by code (`RunEvidence`) |
| `search_papers` tool | same file, wraps `src/agent/search.py` | Passages from the papers of the relevant studies, with pages |
| `status_for` | same file | The status rule: 3+ nearby runs tested, 1-2 sparse, 0 untested |
| `check_verdict` | same file | The output validator: status, wording, numbers, citations |
| `fallback_verdict` | same file | A verdict written by code alone, for when the agent fails |
| `answer` | same file | Runs the agent with its limits; returns the verdict or the fallback |
| `POST /verdict` | `src/api/app.py` | Builds the agent's dependencies and calls `answer` |
| Tests | `tests/test_verdict_agent.py` | Rules, validator, wiring with stand-in models, endpoint, live runs |

The principle behind all of it: **the model writes, code decides.** The model is good at turning counts and passages into three readable sentences. It is not trusted with anything a reader could act on wrongly: the status, the numbers, the sources.

```
POST /verdict {o2_frac, pressure_atm, flame_config?}
   │
   ▼
answer(deps)
   │
   ├─ verdict_agent (PydanticAI, model = LLM_MODEL)
   │     ├─ tool find_runs()             → RunEvidence: status, counts, closest runs  (code)
   │     ├─ tool search_papers(question) → passages with page numbers                 (code)
   │     └─ writes a VerdictResponse                                                  (model)
   │            │
   │            ▼
   │     output validator: status, numbers, citations   ── fails → ModelRetry, model fixes it (max 2)
   │
   └─ any agent failure (bad key, model down, retries or limits used up)
         → fallback_verdict(): written by code from the runs already loaded, no citations

   runs cannot be loaded (database down) → 503: nothing true can be said
```

## Inner mechanics: one request, step by step

1. **FastAPI validates the body** as a `ScenarioQuery` (O₂ between 0 and 1, pressure above 0, a known flame type). Anything else is a 422; the agent never sees it.
2. **The endpoint builds `Deps`** through the `verdict_deps` dependency: the database engine, the request's query, and two swappable functions (`find_scenario`, `search`). `Deps` also holds the run's state: the scenario once loaded, whether the papers were searched, and the hits returned.
3. **`answer` loads the runs first**, once, in a worker thread (`asyncio.to_thread`) so the database query never blocks the event loop. If that fails, it raises `RunDataUnavailable` and the endpoint returns a 503: without the runs, not even the fallback can be written truthfully.
4. **`answer` starts the agent** with a one-line prompt (the scenario and the question), the model from `LLM_MODEL` (read on every run, the only place it is set), and `UsageLimits(request_limit=8)`.
5. **PydanticAI sends the first request** to the model: the instructions (the rules in plain words), the prompt, and three tool schemas: `find_runs` (no arguments), `search_papers(question: str)`, and an output tool whose arguments are a `VerdictResponse`. Calling the output tool is how the model hands in its answer as structured data. Claude Opus 5.5 rejects *forcing* a tool call; PydanticAI knows this and leaves the choice open, so the default model works as is.
6. **The model calls `find_runs`.** PydanticAI runs the Python function and sends its result back as JSON. The function summarises the runs loaded in step 3 (the same `get_scenario_data` as `GET /scenario`); a lock keeps it to one query even when tools run in parallel threads.
7. **The model calls `search_papers`** with a question it words itself, such as "droplet flame extinction at elevated oxygen". The tool restricts the search to the studies of the nearby runs (here PSI-69), or of the closest runs when none is nearby; drops hits from Word files (no page to cite); keeps 5; and records them in `Deps.hits`. Behind it, [paper search](paper_search.md) runs meaning + BM25 keywords, then a re-ranker.
8. **The model calls the output tool** with its verdict. Two checks follow:
   - **Shape**, by Pydantic: `status` is one of the three values, every citation has a page ≥ 1, and so on.
   - **Content**, by `check_verdict` (below). A failure raises `ModelRetry("These numbers are not in the find_runs output: 23 …")`; PydanticAI sends that message back to the model as the result of its output call, and the model tries again, at most twice.
9. **The verdict is returned** and FastAPI serialises it as a `VerdictResponse`. A run typically takes 3 to 5 model calls; the limit of 8 stops a model that loops.
10. **If the agent raises** (authentication, network, a model error, retries or the request limit used up), `answer` logs it and returns `fallback_verdict(deps)`, built from the runs loaded in step 3, with no new query.

## The rules, inside

### Status

| Nearby runs (`distance <= 1`) | `status` |
| --- | --- |
| 3 or more | `tested` |
| 1 or 2 | `sparse` |
| 0 | `untested` |

`status_for()` (`TESTED_MIN_RUNS = 3`). `find_runs` returns the status already decided, the instructions tell the model to copy it, and the validator rejects anything else. `in_envelope` in `/scenario` is true from 1 run, so it means "tested or sparse".

### What `find_runs` returns

`RunEvidence`, a summary computed by code, not the full `ScenarioResponse`:

| Field | Example (Moon) | Why |
| --- | --- | --- |
| `status` | `tested` | From the rule, so the model never decides it |
| `n_nearby`, `runs_compared` | 17, 573 | |
| `nearby_outcomes` | `{"disruption": 15, "completion": 2}` | Counted by code, so the model never counts |
| `nearby_o2_range`, `nearby_pressure_range` | `[0.3, 0.34]`, `[0.7, 0.71]` | |
| `nearby_studies` | `["PSI-69"]` | Which investigations the paper search is limited to |
| `closest` | 5 runs | Study, label, conditions, outcome, distance |

Values are rounded (O₂ to 3 decimals, pressure and distance to 2), so the model sees the same precision it should write.

### The output validator (`check_verdict`)

1. **Status** equals the rule's.
2. **Untested:** the text starts with "Untested" and names the closest run (its label or run ID).
3. **`search_papers` was called.** A verdict written without searching is sent back.
4. **Every citation is a page `search_papers` returned** in this run (same study, title and page), so a row of `psi_dashboard.chunks` that was actually retrieved. A page the model remembers, even a real one, is rejected. Checked in memory, no database query.
5. **Zero citations** only when the search found nothing.
6. **Every number in the text appears in the `find_runs` output** (next section). Checked last, once the citations are known to be real, because their titles are removed from the text first.

Each failure says what to fix, so the retry is a correction, not a reroll.

### Inside the number check

`unsupported_numbers(text, evidence, citations)`:

1. **Remove what is not a quantity:** the titles of the cited documents, already checked to be search hits ("SRD 201306" is a title, not a figure); the closest runs' IDs (`PSI-69:flex:41`); page references, one page or one range only (`p. 6`, `pp. 17-20`), never the numbers after them; and identifiers with letters before digits (`PSI-69`, `CO2`, `FLEX-2`).
2. **Extract the remaining numbers**, including those with a unit attached: "1500K", "45s" and "0.3atm" are checked as 1500, 45 and 0.3.
3. **Collect every number in `RunEvidence`**, wherever it sits: the query, counts, ranges, each closest run's values.
4. **Match each number at the precision it is written:** "0.70" matches 0.703 (2 decimals), "17" matches 17. A fraction may be written as a percent: "30%" matches 0.3.
5. **Anything left unmatched is invented**, and the verdict goes back to the model.

Example: *"Tested at 30% O2 and 0.69 atm: 17 FLEX runs (PSI-69), 15 ended in disruption; see FLEX Test Plan p. 6."* → after step 1: `30`, `0.69`, `17`, `15` → all found (0.3 as percent, the query, `n_nearby`, the disruption count) → accepted. Rejected: "23 runs", "reached 1500K", and "see page 6 and 99 runs ignited" (the 99).

Known limits: numbers written as words ("seventeen") are not checked, and a wrong number that happens to equal another value in the evidence ("2 hours", when 2 runs completed) passes. The check catches invented figures, not every misuse of a real one. It errs the other way too: an ordinal such as "2nd" is checked as 2, so a harmless phrase can cost a retry.

### The fallback (`fallback_verdict`)

Built from `RunEvidence` only: the status, then either the nearby count and outcomes ("Tested. 17 ISS runs sit near 0.3 O2 and 0.69 atm (PSI-69): 15 disruption, 2 completion.") or "Untested" with the closest run, then a sentence saying the written analysis is unavailable. No citations, since no model read the papers. It passes the same number check as the model's text, and it makes no query: the runs were loaded before the agent started. If they could not be loaded, `answer` never gets this far: the endpoint returns a 503.

## Why we built it this way

| Decision | Alternatives | Why this one |
| --- | --- | --- |
| **Status decided by code** | Let the model judge it | It is a rule, not a judgement. A model could say "tested" with 0 runs nearby, the one bug that would hurt the demo most. |
| **Validator + retry** for wrong outputs | Overwrite the model's status or numbers in code | Overwriting the status would leave a text that contradicts it. Sending the verdict back keeps text, status and numbers consistent. |
| **Every number checked against `find_runs`** | Trust the instructions | In fire safety an invented number is the one mistake that cannot be afforded; instructions reduce it, only a check stops it. |
| **`find_runs` returns a summary computed by code** | The full `ScenarioResponse` (as the issue says) | The map's 573 runs would add tens of thousands of tokens per request, and leave the model to count. Counted by code, the numbers are right by construction, and checkable. |
| **`find_runs` takes no arguments** | `find_runs(query)`, as the issue says | The scenario is the request's. A model passing its own conditions could answer a different question than the one asked. |
| **Paper search limited to the runs' studies** | Search all papers | The verdict is about those runs; FLEX's papers explain FLEX's results. It also avoids citing a study that has nothing to do with the scenario. |
| **Word-file hits dropped** | Make `Citation.page` optional | A contract change touches the dashboard too. The cost is small: the PDFs carry most of the text. To revisit with the team. |
| **An agent with tools** | One call with the runs and papers fetched in advance | The issue asks for an agent, and the model chooses what to search for in the papers. The flow is nearly fixed, though: a single call would work too, and would be a fair simplification later. |
| **A fallback written by code, catching every agent exception** | Return an error | The demo must always answer. The fallback says less but is never wrong. |
| **Runs loaded before the agent, off the event loop** | Load them lazily in the first tool | The fallback then has the runs without a new query, so it cannot fail on the database; the tools never race on the same query; and a slow database never blocks other requests. If the runs cannot be loaded, a 503 is honest: nothing true can be said. |
| **Citations must be search hits** | Check the page exists anywhere in `chunks` | A page the model remembers may exist and still not support the claim. Only pages retrieved for this question were read; checking in memory also saves a query per citation. |
| **Paper-search models imported on first use** | Import them with the module | `sentence_transformers` loads torch, which takes seconds; `/scenario` and `/inverse-flames` never search papers and should not pay for it. |
| **`Deps` with swappable functions** | Call the database directly in the tools | Tests run the agent without a database or an LLM, and the scenario source can change without touching the agent. |
| **Limits: 2 retries, 8 model calls** | Defaults | Bounds cost and latency; a model that cannot follow the rules in 2 tries falls back. |
| **`LLM_MODEL`, default Claude Opus 5.5, set per run** | Hard-coded model; or set in the `Agent` constructor | Opus 5.5 is the most careful with strict rules; a cheaper model (e.g. a Haiku) suits development and tests. One line in `.env`. Read on every run, in one place, so a change to `.env` needs no code change. |
| **Live tests behind `make test-live`** | Run them in `make test` | They cost money and need a key and the loaded database. `make test` stays free and offline. |

## Configuration

In `.env`:

```bash
LLM_MODEL=anthropic:claude-opus-5-5   # any PydanticAI model string; this is the default
ANTHROPIC_API_KEY=sk-ant-...          # the key of that provider
```

Without `LLM_MODEL`, the code uses Claude Opus 5.5. A cheaper model for development: set it in `.env`, or for one run, `make test-live LLM_MODEL=anthropic:claude-haiku-4-5`.

`pydantic-ai-slim[anthropic]>=2.68`: older versions accept only an integer for `retries`, and the agent would fail to start (silently, into the fallback). Limits, in `src/agent/verdict_agent.py`: `retries={"output": 2}` (the issue's `output_retries=2`, as PydanticAI 2.x writes it), `USAGE_LIMITS = UsageLimits(request_limit=8)`, `PAPER_HITS = 5`, `TESTED_MIN_RUNS = 3`.

## Tests

`tests/test_verdict_agent.py`:

| Group | Model | What |
| --- | --- | --- |
| `StatusRuleTests` | none | 0 / 1-2 / 3+ |
| `OutputValidatorTests` | none | Each check, including **an invented number is rejected**, a number with a unit, a number hidden behind a page reference, a citation search did not return, a verdict written without searching |
| `AgentWiringTests` | `TestModel`, `FunctionModel` | Both tools are called; an invented number is sent back and corrected; a failing model falls back; one runs query per request, parallel tools and fallback included; unavailable runs raise |
| `VerdictEndpointTests` | `TestModel`, `FunctionModel` | `POST /verdict` returns the agent's verdict, the fallback, or a 503 when the runs cannot be loaded; importing the API does not load torch |
| `LiveVerdictTests` | the real one | Moon droplet → tested, count in the text, ≥ 1 citation; Moon solid → untested, names the closest run, ≥ 1 citation; a broken API key → valid fallback |

`TestModel` and `FunctionModel` are PydanticAI's stand-in models: the first calls the tools and returns a given output; the second is a Python function playing the model turn by turn (here: call `find_runs`, call `search_papers`, hand in a verdict with an invented number, then a correct one).

`make test` runs everything except the live tests (no LLM cost, no network). `make test-live` runs them all: it needs the database loaded (`make load-chunks` and the runs loaders) and a real API key in `.env`. The two smoke tests can only pass with a real model answer: they require a citation, and the fallback has none.

**Verified on 2026-11-14:** `make test-live` passed 16/16 with `LLM_MODEL=anthropic:claude-haiku-5-5`, before and after merging #14. The code-review fixes (stricter citation and number checks) were verified offline; re-run `make test-live` to confirm them with the real model. Not yet run on the default Opus 5.5.

## Changes from the issue

The project moved on after #8 was written:

- **`find_runs` uses the real `/scenario` code.** #11 landed `get_scenario_data`, so the fixture is no longer needed; `Deps.find_scenario` can still swap the source.
- **`find_runs` takes no query and returns `RunEvidence`**, instead of `find_runs(query) → ScenarioResponse` (see the decisions above).
- **"tested: n_nearby ≥ 3 (the same rule as `in_envelope`)":** `in_envelope` is `n_nearby > 0` in the merged `/scenario` code, so the two rules differ. The status follows the issue's 3-level rule; `in_envelope` is unchanged.
- **Moon solid's closest run is BASS (PSI-26), not BASS-II.** Since #14, the 250 BASS and BASS-II runs are all compared, and a BASS run is simply closer: SIBAL at 0.227 O₂ and 0.99 atm (distance 2.11), against BASS-II's best at 0.222 O₂ and an assumed 1.0 atm (distance 2.20, 42nd). The agent names whichever run `find_runs` reports as closest.
