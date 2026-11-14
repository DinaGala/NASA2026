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
| `answer` | same file | Runs the agent with its limits; returns the verdict or the fallback, and records a `RunReport` |
| `RunReport` | same file | One verdict's cost: latency, model requests, retries, input/output tokens, USD, fallback or not |
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
4. **`answer` starts the agent** with a one-line prompt (the scenario and the question), the model from `LLM_MODEL` (read on every run, the only place it is set), and `UsageLimits(request_limit=8, total_tokens_limit=50_000)`.
5. **PydanticAI sends the first request** to the model: the instructions (the rules in plain words), the prompt, and three tool schemas: `find_runs` (no arguments), `search_papers(question: str)`, and an output tool whose arguments are a `VerdictResponse`. Calling the output tool is how the model hands in its answer as structured data. PydanticAI adapts that request to each model (some models refuse to be forced to call a tool), so any model set in `LLM_MODEL` works as is.
6. **In its first turn, the model calls `find_runs` and `search_papers` together** (the instructions ask for it), which saves a round trip: a verdict takes 2 model calls instead of 3. For `find_runs`, PydanticAI runs the Python function and sends its result back as JSON. The function summarises the runs loaded in step 3 (the same `get_scenario_data` as `GET /scenario`); a lock keeps it to one query even when tools run in parallel threads.
7. **`search_papers`** gets a question the model words itself, such as "droplet flame extinction at elevated oxygen". The tool restricts the search to the studies of the nearby runs (here PSI-69), or of the closest runs when none is nearby; drops hits from Word files (no page to cite); keeps 5; and records them in `Deps.hits`. Behind it, [paper search](paper_search.md) runs meaning + BM25 keywords, then a re-ranker.
8. **The model calls the output tool** with its verdict. Two checks follow:
   - **Shape**, by Pydantic: `status` is one of the three values, every citation has a page ≥ 1, and so on.
   - **Content**, by `check_verdict` (below). A failure raises `ModelRetry("These numbers are not in the find_runs output: 23 …")`; PydanticAI sends that message back to the model as the result of its output call, and the model tries again, at most twice.
9. **The verdict is returned** and FastAPI serialises it as a `VerdictResponse`. A run typically takes 2 to 3 model calls and 6-15k tokens (9k on average); the limits (8 calls, 50k tokens) stop a model that loops, or an expensive model set by mistake, and the request then gets the fallback.
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
| `studies` | `{"PSI-69": "FLEX (Flame Extinguishment Experiment)"}` | Readers know experiments by name, not by PSI ID; read once from `investigations`. If that lookup fails, the PSI IDs are used: names only shape the wording, so they never block a verdict (same for flame types, which then count as "unknown"). |
| `nearby_runs_by_flame_type`, `nearby_outcomes_by_flame_type` | `{"droplet": 17}`, `{"droplet": {"disruption": 15, "completion": 2}}` | A scenario without a flame type must never blend droplets and gas flames into one count; read once from `runs` (a `Run` does not carry its flame type). Each closest run also carries its `flame_config`. |

Values are rounded (O₂ to 3 decimals, pressure and distance to 2), so the model sees the same precision it should write.

### The instructions

`INSTRUCTIONS` in `src/agent/verdict_agent.py`, tuned on the six demo scenarios (#9). They state the reader (someone who has never heard of these NASA experiments), ask for both tools in the first turn, list the rules the validator enforces, and fix the shape of `text`: 3 or 4 short sentences under 90 words, starting with the status and a colon ("Tested:", "Sparse:", "Untested:"), with no sentence labels and no mention of the instructions.

1. **The answer:** how many runs sit nearby, in which experiment and with which flame type, naming the experiment ("NASA's FLEX experiment"), never a PSI ID or run ID.
2. **What happened**, per flame type, never one blended count; when the scenario names no flame type and all nearby runs share one, it says so. Outcomes come with plain words, so a judge never meets a bare "disruption": extinction = the flame went out; disruption = the droplet broke apart; completion = the fuel burned away; for gas flames, ignited / no ignition, never extinction; blowoff; unknown = not recorded.
3. **At most one sentence from a cited passage**, saying what the passage says, and not presenting it as the cause of these runs' results unless the passage itself does. This rule came from the citation check: a verdict once linked a true test-plan sentence to 2 runs it said nothing about.
4. **How sure:** how many runs and experiments; for sparse, say plainly that the evidence is thin; for untested, how far the closest run is.

**Disruption wording.** Whether FLEX's "disruption" means the flame was still burning is not confirmed (the open question from #7: the only source is the CFI document, not FLEX). So the glossary says only "the droplet broke apart", and a verdict claiming the flame "kept burning" or was "still burning" is sent back.

**No `temperature`.** Claude Haiku 5.5 does not support sampling settings: PydanticAI ignores `temperature=0` with a warning. Stability comes instead from the fixed shape above and the code checks, verified by running every scenario twice in a row.

### The output validator (`check_verdict`)

1. **Status** equals the rule's.
2. **Untested:** the text starts with "Untested" and names the closest run (its label or run ID).
3. **`search_papers` was called.** A verdict written without searching is sent back.
4. **Every citation is a page `search_papers` returned** in this run (same study, title and page), so a row of `psi_dashboard.chunks` that was actually retrieved. A page the model remembers, even a real one, is rejected. Checked in memory, no database query.
5. **Zero citations** only when the search found nothing.
6. **No unconfirmed claim:** "still burning", "kept burning", "continued to burn" are sent back (see "Disruption wording" above).
7. **Gas-flame scenarios never speak of extinction** ("extinct…", "extinguish…", "went out"), even when quoting a paper: a gas flame is described by whether it ignited. The instructions say so too, but the model slipped once in six live verdicts, so code enforces it.
8. **Every number in the text appears in the `find_runs` output** (next section). Checked last, once the citations are known to be real, because their titles are removed from the text first.

Each failure says what to fix, so the retry is a correction, not a reroll.

### Inside the number check

`unsupported_numbers(text, evidence, citations)`:

1. **Remove what is not a quantity:** the titles of the cited documents, already checked to be search hits ("SRD 201306" is a title, not a figure); the closest runs' IDs (`PSI-69:flex:41`) and labels ("100 micron PMMA film"), and the experiment names ("FLEX-2 (Flame Extinguishment Experiment - 2)", whole or either part), all of which come from `find_runs`; page references, one page or one range only (`p. 6`, `pp. 17-20`), never the numbers after them; and identifiers with letters before digits (`PSI-69`, `CO2`, `FLEX-2`).
2. **Extract the remaining numbers**, including those with a unit attached: "1500K", "45s" and "0.3atm" are checked as 1500, 45 and 0.3.
3. **Collect every number in `RunEvidence`**, wherever it sits: the query, counts, ranges, each closest run's values.
4. **Match each number at the precision it is written:** "0.70" matches 0.703 (2 decimals), "17" matches 17. A percent ("30%", "30 percent") matches only a fraction (0.3), never a count: a "13%" from a paper does not pass because 13 runs happened to complete. A bare number matches only a value as written.
5. **Anything left unmatched is invented**, and the verdict goes back to the model.

Example: *"Tested at 30% O2 and 0.69 atm: 17 FLEX runs (PSI-69), 15 ended in disruption; see FLEX Test Plan p. 6."* → after step 1: `30`, `0.69`, `17`, `15` → all found (0.3 as percent, the query, `n_nearby`, the disruption count) → accepted. Rejected: "23 runs", "reached 1500K", "see page 6 and 99 runs ignited" (the 99), and "tests across 13% oxygen" when no fraction 0.13 is in the evidence.

Known limits: numbers written as words ("seventeen") are not checked, and a wrong bare number that happens to equal another value in the evidence ("2 hours", when 2 runs completed) passes. The check catches invented figures, not every misuse of a real one. It errs the other way too: an ordinal such as "2nd" is checked as 2, so a harmless phrase can cost a retry.

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
| **Limits: 2 retries, 8 model calls, 50k tokens** | Defaults | Bounds cost and latency; a model that cannot follow the rules in 2 tries falls back. A normal verdict uses 6-15k tokens; one using both retries makes ~5 calls, each resending the conversation (~25-35k). The token cap sits above that worst case, so it never cuts a correction, only a runaway run. |
| **Claude Haiku 5.5, through `LLM_MODEL`** | A larger model (Sonnet, Opus) | The cheapest model that passes every check: about $0.0012 per verdict, the 6 demo scenarios twice in a row with no retry (#9). Code enforces the rules, so a stronger model would cost several times more for the same verdicts. Read on every run, in one place, so a change in `.env` needs no code change. |
| **Live tests behind `make test-live`** | Run them in `make test` | They cost money and need a key and the loaded database. `make test` stays free and offline. |
| **Study names given to the model** | Let it use PSI IDs | A judge has never heard of PSI-69; "NASA's FLEX experiment" is the first thing the answer needs to be understood. |
| **A fixed 4-part shape and an outcome glossary in the prompt** | A free-form "3-5 plain sentences" | The first live round produced accurate but dense answers: bare jargon ("disruption"), side details from the papers. The shape keeps every answer short and in the same order. |
| **Both tools in the first turn** | Let the model call them one after the other | Saves a model round trip: 2 calls per verdict instead of 3, about 2-3 s less. The search question doesn't depend on `find_runs`' result. |
| **Paper-search models loaded at API startup, in the background** | Load them on the first search | The first `/verdict` would otherwise pay several seconds of model loading; in the background, `/scenario` is never held up. |
| **Gas-flame and "still burning" rules in code** | Instructions only | Both are in the instructions, but a model slips now and then (once in six for the gas rule); a check makes them hold every time. |
| **A percent matches only a fraction** | Accept a percent of any value | Found by reading a live verdict: a "13%" from a paper passed because 13 runs had completed. |

## Configuration

In `.env`:

```bash
LLM_MODEL=anthropic:claude-haiku-5-5  # any PydanticAI model string; this is the default
ANTHROPIC_API_KEY=sk-ant-...          # the key of that provider
```

Without `LLM_MODEL`, the code uses Claude Haiku 5.5 (`DEFAULT_MODEL`), the model the prompt was tuned on. Another model for one run: `make test-live LLM_MODEL=...`; re-run the live tests before switching, since the prompt was tuned on Haiku 5.5.

`pydantic-ai-slim[anthropic]>=2.68`: older versions accept only an integer for `retries`, and the agent would fail to start (silently, into the fallback). Limits, in `src/agent/verdict_agent.py`: `retries={"output": 2}` (the issue's `output_retries=2`, as PydanticAI 2.x writes it), `USAGE_LIMITS = UsageLimits(request_limit=8, total_tokens_limit=50_000)`, `PAPER_HITS = 5`, `TESTED_MIN_RUNS = 3`.

## Tests

`tests/test_verdict_agent.py`:

| Group | Model | What |
| --- | --- | --- |
| `StatusRuleTests` | none | 0 / 1-2 / 3+ |
| `OutputValidatorTests` | none | Each check, including **an invented number is rejected**, a number with a unit, a number hidden behind a page reference, a citation search did not return, a verdict written without searching |
| `AgentWiringTests` | `TestModel`, `FunctionModel` | Both tools are called; an invented number is sent back and corrected; a failing model falls back; one runs query per request, parallel tools and fallback included; unavailable runs raise |
| `VerdictEndpointTests` | `TestModel`, `FunctionModel` | `POST /verdict` returns the agent's verdict, the fallback, or a 503 when the runs cannot be loaded; importing the API does not load torch |
| `tests/live/test_verdict_live.py` | the real one | The six demo scenarios (table below), each with ≥ 1 citation, no fallback and under 15k input tokens; a broken API key → valid fallback |

`TestModel` and `FunctionModel` are PydanticAI's stand-in models: the first calls the tools and returns a given output; the second is a Python function playing the model turn by turn (here: call `find_runs`, call `search_papers`, hand in a verdict with an invented number, then a correct one).

`make test` runs `tests/test_*.py` only: no LLM cost, no network. `make test-live` runs `tests/live/test_verdict_live.py` with `LIVE_LLM_TESTS=1`: it needs the database loaded (`make load-chunks` and the runs loaders) and a real API key in `.env`. The scenario tests can only pass with a real model answer: they require a citation, and the fallback has none. Each verdict is printed with its `RunReport` and appended to `data/processed/live_verdicts.jsonl` (not committed).

| Scenario | Query | Expected | Must mention |
| --- | --- | --- | --- |
| Moon droplet | 0.30 O₂, 0.69 atm, droplet | `tested` | the nearby count (17) |
| Moon solid | 0.30 O₂, 0.69 atm, solid | `untested`, starts "Untested" | the closest run (BASS SIBAL today; BASS-II accepted) |
| Earth air droplet | 0.21 O₂, 1 atm, droplet | `tested` | the extinction count (62 of 100) |
| Sparse droplet | 0.21 O₂, 2 atm, droplet | `sparse` | that the evidence is thin; only 2 FLEX runs nearby, and every O₂ from 0.17 to 0.25 at 2 atm gives the same 2 |
| Moon, any flame type | 0.30 O₂, 0.69 atm | `tested` | each flame type's count separately; today all 17 nearby runs are droplets, so the verdict says so |
| Moon, gas flame | 0.30 O₂, 0.69 atm, `gas_normal` | `untested`, names the closest run | ignited / no-ignition terms, never extinction. No gas run sits within the "nearby" rule: the closest, CFI-G "30% ethane" at 0.33 O₂ and 0.5 atm, is at distance 1.15 |

Every live verdict is also checked for no fallback, ≥ 1 citation, under 15k input tokens, and never "kept burning" / "still burning". Latency is reported against a 10 s target, not asserted (it depends on the network). The test class first warms up the paper-search models, as a running API does at startup.

**Measured on 2026-11-14** (#9), with `LLM_MODEL=anthropic:claude-haiku-5-5` ($0.50 / $2.50 per million input / output tokens): with the final prompt and checks, all six scenarios passed twice in a row (rounds F and G), with no retry and no fallback.

| Per verdict, final prompt (12 verdicts) | Average | Range |
| --- | --- | --- |
| Cost | $0.0012 | up to $0.0016 |
| Input tokens | 7.2k | up to 10.2k (limit 15k) |
| Model requests | 2.2 | 2–3 |
| Latency | 8.2 s (median 8.0 s) | up to 10.8 s; paper search ~1.7 s of it |

- **Latency:** most verdicts take 6–9 s, but 6 of the 42 six-scenario verdicts took 10 s or more (1 of 12 in the final rounds; one early outlier took 34.6 s). The time is mostly the model's two calls, which vary from run to run. **The dashboard should show a loading state for `/verdict`.**
- **Citations checked by hand:** in the final round, each verdict's paper sentence was compared with its cited page; all 6 are supported by the page (one names the FLEX EIRD a "test plan").
- **Total:** the 62 live verdicts of #9 (4-scenario and 6-scenario rounds) cost $0.075.

## Changes from the issues

The project moved on after #8 was written:

- **`find_runs` uses the real `/scenario` code.** #11 landed `get_scenario_data`, so the fixture is no longer needed; `Deps.find_scenario` can still swap the source.
- **`find_runs` takes no query and returns `RunEvidence`**, instead of `find_runs(query) → ScenarioResponse` (see the decisions above).
- **"tested: n_nearby ≥ 3 (the same rule as `in_envelope`)":** `in_envelope` is `n_nearby > 0` in the merged `/scenario` code, so the two rules differ. The status follows the issue's 3-level rule; `in_envelope` is unchanged.
- **#9: the two new Moon scenarios follow the data.** The issue expected Moon with a gas flame to be `tested`, and Moon without a flame type to describe droplet and gas runs separately. Under the "nearby" rule (±0.05 O₂, ±0.2 atm), no gas run is near the Moon point (CFI-G's runs are at 0.5 atm), and all 17 nearby runs are FLEX droplets. So Moon, gas flame is `untested`, and Moon, any flame type says all nearby runs are droplets. The data audit's wider Moon window (with 20 CFI-G gas flames) is a different rule.
- **#9: no `temperature=0`:** Claude Haiku 5.5 has no sampling settings (see "No `temperature`" above).
- **#9: unittest instead of `@pytest.mark.live`.** The project's tests are unittest; the live tests are skipped unless `LIVE_LLM_TESTS=1`, which has the same effect: a normal `make test` never calls the API.
- **Moon solid's closest run is BASS (PSI-26), not BASS-II.** Since #14, the 250 BASS and BASS-II runs are all compared, and a BASS run is simply closer: SIBAL at 0.227 O₂ and 0.99 atm (distance 2.11), against BASS-II's best at 0.222 O₂ and an assumed 1.0 atm (distance 2.20, 42nd). The agent names whichever run `find_runs` reports as closest.
