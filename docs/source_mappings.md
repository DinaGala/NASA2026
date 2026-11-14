# FLEX and CFI-G source mappings

This document freezes the source-to-database mapping for the first experiment-data import. It covers the PSI-69 FLEX and PSI-159 CFI-G CSVs only. Source files remain unchanged; the import preserves each source row in `psi_dashboard.runs.raw`.

Some destinations here are proposed additions to the current DDL: `source_row_number`, `outcome_detail`, and measurement-level source/quality fields. Add them in the schema step before implementing the loader. Until then, record rejected-row reasons in the dry-run/import report rather than discarding rows.

## Import identity and file handling

| Source | Encoding | Physical/data rows | Import rule |
|---|---|---:|---|
| `psi_data/PSI-69/Experimental table/PSI-69_Experimental table_FLEX.csv` | CP1252 (UTF-8 decoding fails) | 274 | Import rows with a valid `FLEX Test #` except the pressure-zero row, which is quarantined from dashboard analysis. Use `FLEX Test #` as the unique source identity. |
| `psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Normal Flames.csv` | UTF-8 with optional BOM (`utf-8-sig`) | 179 | Import records with a nonblank `Test` value. The 179 test IDs are unique in the current file. |
| `psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Inverse Flames.csv` | UTF-8 with optional BOM (`utf-8-sig`) | 1,015 physical rows; 154 with nonblank `Test` | Import only records with a nonblank `Test` value. The other 861 rows are blank/template rows and should be reported as skipped, not imported as runs. |

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

## Demonstration filters

For FLEX, the audit counts reproduce with an explicit pressure tolerance of 0.03 atm:

- Moon-like: `o2_frac >= 0.29` and `abs(pressure_atm - 0.70) < 0.03` → 17 rows: 15 disruption, 2 completion, 0 extinction.
- Earth-air comparison: `o2_frac <= 0.21` and `abs(pressure_atm - 1.00) < 0.03` → 145 rows: 112 extinction (77%).

Keep these thresholds and the pressure tolerance in one named query/configuration constant so the dashboard and validation report use the same definition.

## Deferred data rules

The following rules apply to later study adapters, not FLEX/CFI-G imports: BASS/BASS-II free-text outcomes; BASS-II material/geometry/size parsing; design-range/list-valued conditions; Flame Design sentinels; SAFFIRE percentage conversion. Keep them out of these first two adapters until their source maps are reviewed.
