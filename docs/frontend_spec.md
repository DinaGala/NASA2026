# Flame in Freefall — Frontend spec (Streamlit)

**Purpose:** you pick a cabin atmosphere (O₂, pressure, flame type), and the app shows what NASA's microgravity experiments found near that point, how much evidence there is, and a cited verdict. It never extrapolates: if there's no evidence near a point, the app says "untested".

**Priorities, in order:** honest about the data → readable in 10 seconds → fast to build (about 2 hours).

---

## 1. Stack and data flow

- **Streamlit** (one app), **Plotly** for the charts, **requests** for the API, and the Pydantic models from `src/contracts.py` to parse the responses.
- The API base URL comes from the `API_URL` environment variable (default `http://localhost:8000`). Streamlit calls the API from Python, so CORS isn't needed.

| Call | When | Returns | Cache |
|---|---|---|---|
| `GET /scenario?o2_frac=&pressure_atm=&flame_config=` | Every time a control changes | `ScenarioResponse`: `in_envelope`, `n_nearby`, `closest` (5 runs), `points` (all runs for that flame type) | `st.cache_data`, keyed on the rounded inputs |
| `POST /verdict` (body: `ScenarioQuery`) | **Only on the "Get verdict" button** | `VerdictResponse`: `status`, `text`, `citations` | `st.cache_data`, keyed on the rounded inputs |

**Why the verdict sits behind a button:** Streamlit reruns the whole script every time a slider moves. A live verdict would call the LLM on every drag, which is slow (seconds) and costs money.

**Units:** the UI shows psi; the API takes atm. Convert with `atm = psi / 14.7`, and show both values.

---

## 2. Layout

Two tabs (`st.tabs`): **Explore** and **Presets**. Each tab renders its own results, so the app never needs to switch tabs from code (Streamlit can't do that).

---

## 3. Explore tab

### 3.1 Controls
| Control | Widget | Range | Default |
|---|---|---|---|
| Oxygen | `st.slider` (%) | 10–45, step 1 | 30 |
| Pressure | `st.slider` (psi), with atm shown in the label | 5–20, step 0.1 | 10.2 |
| Flame type | `st.segmented_control` | Droplet / Gas / Solid | Droplet |

- O₂ stops at 45% because that's about where the cabin-air data ends. 100% O₂ appears only as a preset (the spacesuit).
- The flame type is always one value, never "all", because the outcomes differ by type (see 3.3).

### 3.2 Metrics row (4 × `st.metric`)
| Metric | Value | Source |
|---|---|---|
| Evidence | `Tested` / `Sparse` / `Untested`, plus the number of runs | From `n_nearby`: 3 or more → Tested, 1–2 → Sparse, 0 → Untested |
| Runs nearby | `n_nearby` | `/scenario` |
| O₂ partial pressure | `o2_frac × psi`, in psi | Computed in the frontend |
| Kept burning nearby | % of nearby runs with a known outcome that kept burning; "no outcomes" if none is known; "—" if there are no runs | Computed from `points` with distance ≤ 1 |

Status colours: Tested = green, Sparse = amber, Untested = red.

### 3.3 Risk surface (the main chart)

**What it shows:** across O₂ × pressure, the locally smoothed share of runs that kept burning. Red reads as risk, blue as "went out". Areas with fewer than 3 runs nearby stay **blank**, so the surface never extrapolates.

**Outcome mapping, per flame type**
| Flame type | Counts as "kept burning" (1) | Counts as "went out / didn't burn" (0) | Colour bar label |
|---|---|---|---|
| Droplet (FLEX, CFI) | `disruption`, `completion` | `extinction` | "kept burning" |
| Gas (CFI-G normal) | `ignited` | `no_ignition` | "ignited" |
| Solid (BASS, BASS-II) | — | — | No surface: outcomes aren't recorded yet. Show only the run dots, with the note "Outcomes not recorded for solids". |

Runs with outcome `unknown` or `None` count toward the run total and the blank mask, but not toward the share.

**Computation** (numpy, from `points`, in milliseconds)
- Scaled distance: `d² = ((o2 − o2ᵢ)/0.05)² + ((p − pᵢ)/0.2)²`, the **same scales as `/scenario`**. Import them from one shared constants module; never hard-code them twice.
- Grid: O₂ from 0.10 to 0.45 in steps of 0.005; pressure from 0.3 to 1.5 atm in steps of 0.01.
- At each grid cell, `n` = the number of runs with `d² ≤ 1`. If `n < 3`, set the cell to `None` (blank).
- Otherwise, take a weighted average of the outcomes with weight `w = exp(−1.25·d²)`.
- **Averaging radius (decided in #16):** the average runs over every run with a known outcome within `d² ≤ 4` (distance 2); the mask stays at `d² ≤ 1`. With `d² ≤ 1` the real data drew hard steps and coloured rings at the edges of each cluster (only one or two runs in the average there); `d² ≤ 4` gives a smooth surface and keeps every reading: Moon droplet 88% kept burning (100% with `d² ≤ 1`), Earth air 36% (39%).

**Plotly figure**
- `go.Contour(x, y, z, zmin=0, zmax=1, connectgaps=False, contours=dict(coloring="heatmap", start=0, end=1, size=0.1), line=dict(width=0.4, smoothing=1.2))`
- Colour scale: `[[0, "#2F6FB8"], [0.5, "#EDE8DE"], [1, "#C8423D"]]`. Thin colour bar, ticks in %.
- **Neutral on black (decided in #16):** keep `#EDE8DE`. A dark neutral (`#3A3A3A`) makes the 50% band merge with the black background, so "half the runs kept burning" reads like "no test data".
- Runs: `go.Scatter`, markers of size 4, dark grey at 55% opacity, no hover.
- **Scenario marker:** a crosshair, not a star. Two dotted lines (`layout.shapes`) through the scenario's O₂ and pressure, plus an open-ring marker (size 15) at the point. A small annotation with the status in capitals (TESTED / SPARSE / UNTESTED).
- A "No test data" annotation in a blank corner.
- Axes: O₂ formatted as %, pressure in atm, light grid, no zero lines. Transparent paper background, and a plot background of `rgba(128,128,128,0.07)` so the blank areas read as "no data", not white.
- Hover: "O₂ 30% · 0.69 atm · kept burning 0%". `displayModeBar=False`.
- Pressures above 1.5 atm aren't in view but still count. Optional later: a toggle that extends the axis to 3.2 atm.

**Optional, only if there's time:** a 3D `go.Surface` toggle. It looks impressive, but the crosshair and the blank areas are harder to read, so it should never be the default.

### 3.4 Research behind this point (`st.pills`, read-only)
- One tag per investigation among the nearby runs, or among the 5 closest when none are nearby. Format: `FLEX · PSI-69 · 17 runs`.
- With no runs nearby, the tags are labelled "Closest studies (not nearby)".
- Acronyms: **an open decision** (see §7).

### 3.5 Verdict panel
- Header "Verdict" with a **Get verdict** button.
- While it loads: `st.spinner("Analysing the evidence…")`. A verdict takes a few seconds.
- Displays:
  - a **status badge** in the same colours as the metrics;
  - the **text** (2–4 sentences, written by the LLM);
  - the **citations**, one line each: `doc_title, p. N (PSI-xx)`.
- **Fallback verdict** (the backend returns it when the LLM fails; you can tell it apart because it has no citations even though runs exist): show the text with a note, "Explanation unavailable — showing the facts only".
- Network error: show `st.error("API unreachable")`. Don't crash.
- Never generate or edit the verdict text in the frontend. The backend owns all of it.

---

## 4. Presets tab

**These are fixed scenarios, with no controls.** One row of 3 picture cards. Clicking a card selects it, and its full analysis appears **below the cards, in the same tab**.

| Preset | O₂ | Pressure | Flame type | Expected status | Role in the demo |
|---|---|---|---|---|---|
| Earth air conditions in space | 21% | 14.7 psi (1.0 atm) | Droplet | Tested | The reference: most flames went out |
| Moon habitat | 30% | 10.2 psi (0.69 atm) | Droplet | Tested | The warning: 17 runs, none extinguished |
| Spacesuit | 100% | 4.3 psi (0.29 atm) | Droplet | Untested | The extreme: beyond all the data |

**Each card:** a picture (16:9), the name, the conditions on one line, and a status badge.

**The analysis below a card:** the same metrics row, the same risk surface with a fixed crosshair (for the spacesuit, which is off the chart, show an arrow annotation at the edge, "100% O₂ →"), the research tags, and the **verdict, already computed**.

**Precomputed verdicts:** `scripts/make_preset_verdicts.py` calls `/verdict` once for each preset and writes `data/presets/<key>.json`. The app reads those files, so preset clicks never wait on the LLM and cost nothing during the demo. Regenerate them whenever the prompt or the data changes.

**Pictures:**
- Use either simple illustrations (Earth from orbit, a lunar habitat dome, a spacesuit helmet) or NASA public-domain photos.
- **No NASA logo** anywhere in the app (challenge rule: don't imply NASA endorsement).
- Check each image's credit line, and store the images in `frontend/assets/`.

---

## 5. Visual style

- Sober and professional, like a technical dashboard: no emoji, no stars, no cartoon visuals.
- Palette: blue `#2F6FB8` (went out), neutral `#EDE8DE`, red `#C8423D` (kept burning). Status colours use Streamlit's success / warning / error colours.
- One Plotly template for every chart (transparent background, light grid, 11 px fonts).
- Copy: sentence case, short labels, and units on every number.

---

## 6. States and edge cases

| Situation | Behaviour |
|---|---|
| Scenario in a blank area | Status "Untested", tags labelled "Closest studies", and the verdict names the closest run |
| Solid selected | Run dots only, plus the note "Outcomes not recorded for solids" |
| Runs nearby, none with a known outcome | "Kept burning nearby" shows "no outcomes" |
| API down | `st.error`, and the rest of the page still renders |
| Verdict loading | Spinner; the button is disabled until the verdict arrives |
| Fallback verdict | Text plus the note "Explanation unavailable — showing the facts only" |

---

## 7. Open decisions (S2, to settle with the backend owner)

1. **Rule for `in_envelope`:** the backend currently uses "1 or more runs nearby", while the status, the surface mask and the verdict all use "3 or more". Pick one, and make every place use it. *#17: the frontend never reads `in_envelope`; it derives the status from `n_nearby`, so it already agrees with the verdict. To confirm with the backend owner.*
2. ~~**Acronyms for the tags:**~~ *#17: `Run.acronym` (optional, null when the study has none), filled by `/scenario` from `investigations`. To confirm with the backend owner.*
3. **Clickable citations:** add a `url` field to `Citation`, or keep plain text.
4. **"No outcome":** use either `None` or `"unknown"` everywhere. *#17: still open; the frontend treats both the same (they count toward the mask, not the share).*
5. ~~**Distance scales (0.05 O₂, 0.2 atm):**~~ settled in #16: `O2_SCALE` and `PRESSURE_SCALE` in `src/constants.py`, imported by `/scenario`, the fixtures script and the chart.

---

## 8. Files

```
frontend/
  app.py        # tabs, layout, state
  api.py        # get_scenario(), get_verdict(), each with st.cache_data
  charts.py     # risk_surface(points, scenario, flame_config) -> go.Figure
  presets.py    # the PRESETS list and loading data/presets/*.json
  assets/       # preset pictures
scripts/make_preset_verdicts.py
data/presets/*.json
```

---

## 9. Done when

- Moving a slider updates the metrics and the surface in under 1 second, with no LLM call.
- "Get verdict" returns in under 10 seconds and shows the status, the text and at least 1 citation.
- The 4 presets open instantly, each with its verdict already computed.
- The Moon habitat preset shows Tested; Spacesuit shows Untested.
- No NASA logo, no stars, and no surface drawn in areas with fewer than 3 runs.

## Out of scope
A 3D surface by default, user accounts, free-text questions to the agent, mobile layout tuning.