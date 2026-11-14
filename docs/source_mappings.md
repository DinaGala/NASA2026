# FLEX, CFI-G, and BASS source mappings

This document freezes source-to-database mappings for the experiment-data imports. It covers PSI-69 FLEX, PSI-159 CFI-G, PSI-25 BASS-II, and PSI-26 BASS. Source files remain unchanged. Use the existing `psi_dashboard.investigations`, `runs`, and `measurements` tables; do not add schema migrations for these imports.

Keep the original source columns in `runs.raw`. Because the existing schema has no `source_row_number`, `outcome_detail`, or quality-flag columns, store sheet/row provenance and parser assumptions in reserved JSON keys such as `_source` and `_normalization` inside `raw`. Preserve the original source fields at the top level of `raw` so existing API extraction from JSONB continues to work. Store numeric values that fit the measurements model in `measurements` as `(variable, value, unit)` rows.

## Import identity and file handling

| Source | Encoding | Physical/data rows | Import rule |
|---|---|---:|---|
| `psi_data/PSI-69/Experimental table/PSI-69_Experimental table_FLEX.csv` | CP1252 (UTF-8 decoding fails) | 274 | Import rows with a valid `FLEX Test #` except the pressure-zero row, which is quarantined from dashboard analysis. Use `FLEX Test #` as the unique source identity. |
| `psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Normal Flames.csv` | UTF-8 with optional BOM (`utf-8-sig`) | 179 | Import records with a nonblank `Test` value. The 179 test IDs are unique in the current file. |
| `psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Inverse Flames.csv` | UTF-8 with optional BOM (`utf-8-sig`) | 1,015 physical rows; 154 with nonblank `Test` | Import only records with a nonblank `Test` value. The other 861 rows are blank/template rows and should be reported as skipped, not imported as runs. |
| `psi_data/PSI-25/Experimental table/PSI-25_Experimental table_BASS-II.csv` | UTF-8 with BOM (`utf-8-sig`) | 129 | Canonical BASS-II input. All 129 `Test #` values are unique. Use the workbook as a cross-check, not a second import source. |
| `psi_data/PSI-26/Science Documents/PSI-26_Science Documents_As Run_Test Matrix.xlsx`, sheet `as run matrix` | XLSX | 130 nonempty rows; 121 rows have both `As run test no.` and `Test no.` | Use this consolidated as-run sheet only. Rows without both identifiers include setup/stow/maintenance or incomplete entries; report and skip them as runs. The `2012` and `2013` sheets are overlapping views and must not be imported again. |

For PSI-26, the BASS CSV does not contain the O₂ and pressure conditions needed for atmosphere comparisons. Use the as-run workbook sheet above. Do not substitute the `Planned_Test Matrix` workbook for actual results.

For FLEX, read using UTF-8 with BOM handling first, then CP1252 if UTF-8 decoding fails. Record the chosen encoding. Do not use lossy replacement decoding. For every file, retain the source file path, source row number (including the header row in the count), and original row values.

## Canonical identity

Use the existing `psi_dashboard.runs.test_id` primary key:

- FLEX: `PSI-69:flex:<FLEX Test #>`; retain `FLEX Identifier` as `run_label`.
- CFI-G normal: `PSI-159:normal:<Test>`.
- CFI-G inverse: `PSI-159:inverse:<Test>`.

Trim IDs and collapse internal whitespace consistently. Keep the original identifier in the raw row. The prefix prevents collisions between studies and between normal/inverse configurations.

### FLEX repeated label correction

`FLEX Identifier` value `193F001` appears on FLEX Test #70 and #73. These rows have different droplet diameters, burning-rate values, and burn times. They are distinct source records, not an exact duplicate. Do not deduplicate on `FLEX Identifier`; use `FLEX Test #` for identity. This corrects the earlier audit note that treated the repeated identifier as a duplicate to drop.

## PSI-69 FLEX mapping

All FLEX records use `psi_id = PSI-69`, `granularity = test`, `flame_config = droplet`, and `o2_is_ambient = true`.

| Source column | Destination | Normalization / notes |
|---|---|---|
| `FLEX Test #` | `runs.test_id` | Namespace as `PSI-69:flex:<value>`; authoritative row identity. |
| `FLEX Identifier` | `runs.run_label` | Preserve as supplied; not unique. |
| `Fuel` | `runs.fuel` | Trim whitespace; retain source spelling in `raw`. |
| `Ambient pressure; mmHg` | `runs.pressure_atm` | Parse numeric value and divide by 760. A value of zero is invalid for this analysis and must be quarantined with a reason, not silently deleted. |
| `O initial ambient composition; mole fraction` | `runs.o2_frac` | Already a fraction; do not divide by 100. |
| `CO initial ambient composition; mole fraction`, `He initial ambient composition; mole fraction` | `runs.diluent`, `runs.diluent_frac` | Current rows have at most one nonzero diluent: set `diluent` to `CO` or `He` and use its fraction. If a future row has both nonzero, preserve both components in `raw` and flag it for a schema decision. |
| `N initial ambient composition; mole fraction` | `raw` | Preserve; not a dashboard filter in the current contract. |
| `Test Date`, `Test GMT` | `raw` | Preserve; add typed date/time columns only if a dashboard view needs them. |
| `Droplet initial diameter; mm` | `measurements` | `initial_droplet_diameter`, unit `mm`. |
| `Visible flame extinction diameter; mm` | `measurements` | `extinction_diameter`, unit `mm`. |
| `Burn time; s` | `measurements` | `burn_time`, unit `s`. |
| `Burning rate; mm` | `raw` pending unit confirmation | The header does not state a time denominator. Do not label or convert it as mm/s or mm²/s until verified from authoritative source documentation. |
| `Test end` | `runs.outcome` | Case-insensitive mapping: `Extinction` → `extinction`; `Disruption` → `disruption`; `Completion` → `completion`. Preserve the original text in `raw`. |

Current file checks: 274 rows; outcomes are 186 extinction, 61 disruption, and 27 completion. `FLEX Test #` is the unique source key. One row has pressure zero and is excluded from normalized comparison results with an explicit quality/rejection reason.

## PSI-159 CFI-G mappings

Both CFI-G files use `psi_id = PSI-159`, `granularity = test`, and `fuel` from the source. Normalize `Flame Type` case-insensitively. The normal and inverse files are separate configurations.

| Source field | Normal flames | Inverse flames | Normalization / notes |
|---|---|---|---|
| `Test` | `runs.test_id`, `run_label` | `runs.test_id`, `run_label` | Namespace separately as described above. Ignore rows with blank `Test` after counting/reporting them. |
| Flame configuration | `gas_normal` | `gas_inverse` | Fixed by source file; do not infer from text in other columns. |
| Oxygen | `XO2 before test` → `runs.o2_frac`; `XO2 after test` → measurement `o2_after_test` | `XO2` → `runs.o2_frac` | Both are fractions in the current files. Normal is treated as ambient oxygen per the audit. Inverse must set `o2_is_ambient = false`; never plot it on a cabin-O₂ axis. |
| `approx p (bar)` | `runs.pressure_atm` | `runs.pressure_atm` | Divide by 1.01325. Preserve the source value in `raw`. |
| `Fuel` | `runs.fuel` | `runs.fuel` | Trim and retain original spelling in `raw`. |
| `Hot ignition` | `runs.outcome` | `runs.outcome` | Case-insensitive: `Yes` → `ignited`, `No` → `no_ignition`. In inverse data, `flash` is not equivalent to either; use `unknown` plus `outcome_detail = flash` until its meaning is confirmed. |
| `Self-ext or flow term` / `Self -ext or flow term` | `runs.outcome_detail` and `raw` | `runs.outcome_detail` and `raw` | Preserve values such as `SE`, `FT`, and `-`. Do not map `SE`/`FT` to a canonical outcome until their definitions are verified. |
| `Comments` | `runs.notes` | `runs.notes` | Preserve verbatim. Any later LLM classification must keep the source comment, return a confidence/review state, and not overwrite the direct `Hot ignition` result. |
| `burn time (sec)` | measurement `burn_time`, unit `s` | measurement `burn_time`, unit `s` | Numeric parse; retain original in `raw`. |
| `Tad (K)` | measurement `adiabatic_flame_temperature`, unit `K` | same | Numeric parse. |
| `Zst` | measurement `stoichiometric_mixture_fraction`, unit `1` | same | Dimensionless. |
| `mHC (mg/s)` / `HC burnrate (mg/s)` | measurement `hydrocarbon_burn_rate`, unit `mg/s` | measurement `hydrocarbon_burn_rate`, unit `mg/s` | The source headers differ; map to the same canonical variable. |
| `O2 flow rate (mg/s)` | — | measurement `oxygen_flow_rate`, unit `mg/s` | Inverse only. |
| `Peak burner T (C )`, `Peak burner Temp (C)`, `Peak burner temp (K)` | measurement `peak_burner_temperature` | same | Canonical unit `K`. Convert Celsius by adding 273.15. In inverse rows with both C and K, use the K value and retain both source values in `raw` for QA. |
| `Cool flame?`, `TFP`, `Xf`, `Color`, `Day` | `raw` pending field definitions/use | `raw` pending field definitions/use | Preserve original columns. Do not turn unfamiliar abbreviations or categories into numeric measurements. |

Current row checks: normal has 179 records (132 `Yes`, 47 `No`). Inverse has 154 records after blank `Test` rows are excluded (131 yes/Yes, 15 no/No, 8 `flash`). Keep case normalization separate from interpretation of `flash`, `SE`, and `FT`.

## PSI-25 BASS-II mappings

Use `psi_data/PSI-25/Experimental table/PSI-25_Experimental table_BASS-II.csv` as the canonical source. It has 129 data rows and 129 unique `Test #` values. The PSI-25 Test Matrices workbook has PI-specific sheets that duplicate this table; use it only for cross-checking, never as a second import source.

All imported rows use `psi_id = PSI-25`, `granularity = test`, `flame_config = solid`, and `o2_is_ambient = true`.

| Source column | Destination | Normalization / notes |
|---|---|---|
| `Test #` | `runs.test_id`, `runs.run_label` | ID format `PSI-25:bassii:<Test #>`; unique in the current CSV. |
| `Fuel Sample Material` | `runs.material`, `runs.geometry`, `runs.size_mm`, `measurements` | Normalize clear material/geometry families (PMMA, SIBAL, Nomex, wax; film, rod, slab, sphere). The text can encode multiple dimensions and colour. Store unambiguous dimensions as named measurements in mm (`sample_thickness`, `sample_width`, `sample_diameter`); set `runs.size_mm` only when one primary dimension is clear. Keep colour and unparsed description in `raw._normalization.material`; preserve original wording at the top level of `raw`. |
| `Calibrated initial O2 % by vol` | `runs.o2_frac` | Divide percent by 100 (e.g. 22.2 → 0.222). This is the pre-test ambient O₂. |
| `Calibrated final O2 % by vol` | measurement `final_o2_frac`, unit `fraction` | Divide percent by 100; invalid/sentinel values stay null and are flagged in `raw._normalization`. |
| Pressure | `runs.pressure_atm` | No pressure column exists in this source. For comparison use the audit's 1.0 atm assumption, and record `pressure_source = assumed` and the reason in `raw._normalization`. Do not present it as measured pressure. |
| `Initial CO2 % by vol`, `Final CO2 % by vol` | measurements `initial_co2_frac`, `final_co2_frac`, unit `fraction` | Divide percent by 100. |
| `Initial CO (ppm)`, `Final CO (ppm)` | measurements `initial_co_ppm`, `final_co_ppm`, unit `ppm` | Parse numeric values only. Keep values such as `none` or `>500` as source text and flag rather than coercing. |
| `Air display` | `raw._normalization.flow_display` | Preserve the exact display text and, only where unambiguous, parse ordered numeric sequence/start/minimum values. Do not put display settings into `runs.flow_cm_s` without verified calibration and units. |
| `Fan display`, `Flow restrictor`, `Radiometer gain`, `Total Frames Shot`, date, GMT, PI, sample number | `raw` | Preserve source values. Frame/camera metadata is not flame imagery or a normalized combustion measurement. |

The CSV does not provide a dedicated outcome or observations column. Set `runs.outcome` to `unknown` unless an explicit outcome term is present in an available source field (for example, `ext` in the flow display); preserve that source text. Do not infer outcomes from numeric flow sequences alone. The current outcome CHECK constraint does not include `extinction_flow`: if a source explicitly describes extinction during a flow ramp, store `extinction` in `runs.outcome` and preserve the flow-specific detail in `raw._normalization.outcome_detail`.

## PSI-26 BASS mappings

The PSI-26 BASS CSV does not carry the atmosphere fields needed for scenario comparisons. Use the consolidated `as run matrix` sheet in `psi_data/PSI-26/Science Documents/PSI-26_Science Documents_As Run_Test Matrix.xlsx`. Do not import the overlapping `2012` and `2013` sheets as well. Do not import planned-test records; only as-run records are evidence of completed tests.

The consolidated sheet has 130 nonempty rows, but only 121 rows currently have both `As run test no.` and `Test no.`. Those 121 candidate test rows have material, O₂, pressure, and observations. The remaining rows include setup/stow/maintenance or incomplete entries; report and skip them rather than manufacturing test IDs. Recheck these counts when parsing and report any source drift.

All imported test rows use `psi_id = PSI-26`, `granularity = test`, `flame_config = solid`, and `o2_is_ambient = true`.

| Source column | Destination | Normalization / notes |
|---|---|---|
| `As run test no.` + `Test no.` | `runs.test_id` | ID format `PSI-26:bass:<As run test no.>:<Test no.>`. The pair is unique for the 121 candidate rows; neither field alone is a reliable key. Preserve both original values. |
| `Test no.` | `runs.run_label` | Retain original identifier, including suffixes such as `a`/`b` and lettered values. |
| `material` | `runs.material`, `runs.geometry`, `runs.size_mm`, `measurements` | Normalize only clear material/geometry/dimensions using the PSI-25 rules. Preserve the full original description in `raw`. |
| `O2 % by volume` | `runs.o2_frac` | Current as-run values are already fractions (approximately 0.159–0.227), despite the percent sign in the header. Do not divide them by 100. Keep the original value in `raw`. |
| `pressure, atm` | `runs.pressure_atm` | Measured source value already in atm. |
| `CO2, mm Hg` | measurement `co2_partial_pressure`, unit `mmHg` | Numeric values only; preserve missing/non-numeric source text in `raw` and record a quality flag. |
| `Test Observations and Anomalies ...` | `runs.notes`; `runs.outcome`; `runs.outcome_source` | Preserve the complete text verbatim in `notes` and `raw`. Extract an outcome only when the text supports a canonical value in the current schema: `ignited`, `no_ignition`, `extinction`, `blowoff`, or `unknown`. Keep the source wording and any flow-specific interpretation in `raw._normalization`. Do not add `extinction_flow` to the database enum. |
| `Air display`, `Fan display` | `raw._normalization.flow_display` | Preserve source strings and optionally parse a clearly ordered sequence. Do not treat fan/air display settings as cm/s without an authoritative calibration. |
| Date, approximate GMT, sample number, igniter, restrictor, sample distance, camera settings | `raw` | Preserve as source context; do not import image/video data. |

For text outcomes, use a configured structured LLM only if the project already has an approved client and credentials. Otherwise use conservative explicit-keyword rules and leave ambiguous cases as `unknown`; keep source observations unchanged. Classifier output must use only the current database outcome values and must not infer from images or video.

## Demonstration filters

For FLEX, the audit counts reproduce with an explicit pressure tolerance of 0.03 atm:

- Moon-like: `o2_frac >= 0.29` and `abs(pressure_atm - 0.70) < 0.03` → 17 rows: 15 disruption, 2 completion, 0 extinction.
- Earth-air comparison: `o2_frac <= 0.21` and `abs(pressure_atm - 1.00) < 0.03` → 145 rows: 112 extinction (77%).

Keep these thresholds and the pressure tolerance in one named query/configuration constant so the dashboard and validation report use the same definition.

## Deferred data rules

The following rules apply to later study adapters: design-range/list-valued conditions outside the BASS flow-display fields, Flame Design sentinels, and SAFFIRE percentage conversion. BASS-II and BASS mappings are now specified above; do not treat their flow-display sequences as calibrated `flow_cm_s` values without a documented conversion.
