# Data audit — psi_data (2026-11-14)

Bottom line: ~2,400 test rows across 24 investigations, but only ~1,400 have O2 + pressure + an outcome. The scenario map can stand on 6 datasets. The Moon-base corner (≥0.30 O2, ~0.7 atm) holds 37 µg tests: 17 FLEX droplets and 20 CFI-G gas flames. No solid-material data sits there.

## What is on disk
- 3.2 GB, 782 files, 24 investigations (PSI-10 … PSI-159)
- `investigation.json` ×24: objective / hypothesis / approach / researchImpacts (35–400 words each), hardware, subResearchArea, DOI, 352 publications in total
- Experimental tables: 22 CSVs (0.2 MB)
- Hidden test tables in xlsx (more valuable than the CSVs):
  - `PSI-39 CFI_DataSummary_Spreadsheet_Jan2021.xlsx`: 227 tests (CSV has 87), with outcomes D0, Dext hot/cool, k hot/cool, burn time, radiation, igniter power
  - `PSI-26 As Run_Test Matrix.xlsx`: 130 BASS tests with O2 %, pressure atm, CO2 mmHg, free-text observations (BASS CSV has no conditions)
  - `PSI-25 BASS-II_Test.Matrices.xlsx`: same as the BASS-II CSV, one sheet per PI
- Documents: 313 PDFs, 8,678 pages, 83 image-only (mostly PSI-26/101/102 engineering docs); plus doc/docx/ppt/xls
- Time series: SAME/SAME-R detector logs (137 xls, 1 Hz, ~5k rows/day, 9 detector channels); PSI-115 CFD grids (.dat); PSI-117 simulation CSVs (Dext, K)
- Useless for us: SAFFIRE MergeResult xlsx (image file indices), zip archives (raw/metadata)

## Test tables, by usefulness
**Tier A: per-test rows with O2 + pressure + outcome**
| Dataset | Rows | O2 | Pressure atm | Outcome |
|---|---|---|---|---|
| FLEX (PSI-69) droplets | 274 | 0.12–0.34 | 0.7 / 1 / 2–3 | Test end: Extinction 186 / Disruption 61 / Completion 27; Dext, k, burn time |
| CFI (PSI-39, xlsx) droplets | 227 | 0.17–0.21 | 0.5–5 | Dext hot/cool, k, burn time, cool-flame flag |
| CFI-G normal (PSI-159) | 179 | 0.18–0.40 | 0.5–3 | Hot ignition yes 132 / no 47; SE vs FT; burn time |
| CFI-G inverse (PSI-159) | 154 | 0.12–0.85* | 0.5–3.3 | Hot ignition yes/no/flash; SE/FT |
| Flame Design normal / inverse (PSI-10) | 207 / 120 | 0.20–0.40 / 0.21–0.85* | 0.4–3 | No outcome; Tad, Zst (design data) |
| BASS-II (PSI-25) solids | 129 | 0.14–0.22 | ~1 | Flow ramp in "Air display"; ext / blowoff in free text |
| BASS (PSI-26, xlsx) solids | 130 | ~0.21 | ~0.97 | Free-text observations only |

*Inverse flames: O2 is the burner stream, not the cabin atmosphere. Do not plot them on the same axis as cabin O2.

**Tier B: ranges / day summaries** — ACME BRE (18, O2 21–40 %), CLD (32), E-FIELD (21), s-Flame (18), FLEX-2 (8, design matrix), SLICE (12), SAFFIRE-I/II/III (2+9+2 large samples, 1-g vs µg spread)
**Tier C: detection, no O2 axis** — SAME-R (109: material, inlet velocity, aging time, Rt/R0), SAME (8), DAFT (2), SPICE (469: fuel, nozzle, fuel rate, air rate, no outcome)
**Meta** — PSI-142 (8 rows = list of figures in papers)

## Key finding for the demo
FLEX at Moon-like conditions (O2 ≥ 0.29, 0.70 atm): 17 tests, 0 extinctions (15 disruption, 2 completion). At ≤0.21 O2 / 1 atm: 112 of 145 extinguished (77 %).

## Cleaning list
- Units: mmHg → atm (FLEX), "1 atm" text (CFI), bar → atm (CFI-G), % → fraction (BASS-II, SAFFIRE, BRE), units inside cells ("20 cm/s", "~ 22.1", "9.2 s")
- Sentinels → NULL + flag: RAMP / RAMPED / LOW O2 (Flame Design, 69 rows), #VALUE!, "-", none, n/a, Insignificant
- Case: inverse/Inverse, Yes/yes, Blue/blue
- Lists in cells → arrays or ranges: "21; 26.5; 40", "0.5; 1.0; 1.5; 2.0; or 3.0", "1.5 - 5"
- Excel autofill: CH5…CH14 → CH4, C2H5…C2H9 → C2H4, N3/N4 → N2, 3400% → 34
- Duplicates / junk: CFI CSV rows 81–88; FLEX identifier 193F001 twice; FLEX row with pressure 0; SAFFIRE "pre-test flow visualisation" rows
- Encoding: BOM in BASS-II header (read utf-8-sig), trailing spaces in headers
- BASS-II material: 51 free-text variants → material (PMMA / SIBAL / Nomex / wax), geometry (rod / film / slab / sphere), thickness, width, colour
- BASS-II "Air display" ("10, 5, 3, 0", "5 down to ext") → flow_start, flow_min, flow sequence
- Free-text outcomes (BASS, BASS-II, CFI-G comments): LLM extraction → ignited, extinguished, extinction_flow, blowoff, with the source text kept

## Proposed Postgres schema (6 tables)
1. `investigations` (24): psi_id PK, acronym, title, pillar, fuel_phase, hardware, objective, hypothesis, approach, impacts, doi, start/end, raw jsonb
2. `publications` (352): psi_id FK, title, authors, doi, status
3. `runs` (~2,400): test_id PK, psi_id FK, source_file, run_label, granularity (test / range), flame_config (droplet / gas_normal / gas_inverse / solid / smoke), fuel, fuel_phase, material, geometry, size_mm, o2_frac, o2_is_ambient bool, pressure_atm, diluent, diluent_frac, flow_cm_s, gravity, outcome enum (extinction / disruption / completion / ignited / no_ignition / blowoff / unknown), outcome_source (column / llm), notes, raw jsonb
4. `measurements` (long, ~6k): test_id FK, variable, value, unit — Dext, k, burn_time, Tad, spread_rate, Rt/R0 …
5. `documents` (~780): doc_id, psi_id, category, file_name, pages, has_text
6. `chunks` (~25k): doc_id, psi_id, page, text, embedding vector(384)

Optional later: `timeseries` (SAME detectors) — skip for the MVP.
