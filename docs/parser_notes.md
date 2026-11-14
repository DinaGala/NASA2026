# PSI Data Parser - Implementation Notes

This document describes the parser and normalization logic for PSI-69 (FLEX), PSI-159 (CFI-G), PSI-25 (BASS-II), and PSI-26 (BASS).

## How to Run

To run the parser on the current source files and generate normalized JSON output:

```bash
PYTHONPATH=. python3 scripts/parse_psi_data.py
```

Output files will be generated in `data/processed/`:
- `PSI-69_FLEX_normalized.json`
- `PSI-159_CFI-G_Normal_normalized.json`
- `PSI-159_CFI-G_Inverse_normalized.json`
- `PSI-25_BASS-II_normalized.json`
- `PSI-26_BASS_normalized.json`
- `parsing_report.json` (Summary stats and any recorded errors/flags)

## Normalization Logic

### Common Fields
- **Source Provenance**: Every record includes `psi_id`, `source_file`, `source_row_number`, and `test_id`.
- **Raw Data**: The original source row is preserved in `raw`. Header whitespace is collapsed for lookup but original source keys and values are retained. `_source` stores file/sheet/row provenance; `_normalization` stores assumptions and quality flags.
- **is_eligible**: A boolean flag indicating if the record is suitable for dashboard analysis. Records with critical quality issues (like zero pressure) are marked `is_eligible: false`.
- **Quality Flags**: The `quality_flags` list contains labels for data issues (e.g., `invalid_pressure: zero`, `missing_pressure`, `multiple_diluents`).
- **Pressure**: Converted to `atm`.
  - FLEX: `mmHg / 760`
  - CFI-G: `bar / 1.01325`
  - BASS-II: `1.0 atm` assumption; explicitly marked `source: assumed` in `raw._normalization`.
  - BASS: measured `pressure, atm` used directly.
  - Original value and unit are preserved in `pressure_source_value` and `pressure_source_unit`.
- **Outcome**: Mapped to canonical values: `extinction`, `disruption`, `completion`, `ignited`, `no_ignition`, or `unknown`.

### FLEX (PSI-69)
- **Identity**: `PSI-69:flex:<FLEX Test #>`. This ensures uniqueness even when `FLEX Identifier` is repeated.
- **Encoding**: Reads as `utf-8-sig` (BOM support) or `cp1252` if UTF-8 fails.
- **Header Normalization**: Collapses repeated/internal whitespace and trims header edges for lookup (e.g., handles " FLEX Test #" and doubled spaces); original header keys remain in `raw`.
- **Oxygen**: `O initial ambient composition; mole fraction` used directly as `o2_frac`.
- **Diluent**: Extracts `CO` or `He` if present. Flags if both are non-zero.
- **Data Quality**: 
  - Test #114 is marked `is_eligible: false` and flagged with `invalid_pressure: zero` because its source pressure is `0`. It is retained for audit but excluded from dashboard-eligible results.

### CFI-G (PSI-159)
- **Encoding**: Reads with UTF-8 BOM support (`utf-8-sig`).
- **Normal Flames**:
  - `XO2 before test` is used as `o2_frac`.
  - `XO2 after test` is stored as a measurement.
  - `o2_is_ambient` is set to `true`.
- **Inverse Flames**:
  - `XO2` is used as `o2_frac`.
  - `o2_is_ambient` is set to `false` (O2 is burner stream).
  - Skips blank rows (861 rows skipped in current source).
- **Outcomes**: 
  - `Yes` -> `ignited`, `No` -> `no_ignition`, `flash` -> `unknown` (with `outcome_detail: flash`).
  - `Self-ext or flow term` (SE/FT) preserved in `outcome_detail`.

## Measurements
Numeric measurements are extracted into a `measurements` list with `variable`, `value`, and `unit`.
- `burn_time` (s)
- `initial_droplet_diameter` (mm)
- `extinction_diameter` (mm)
- `adiabatic_flame_temperature` (K)
- `stoichiometric_mixture_fraction` (1)
- `hydrocarbon_burn_rate` (mg/s)
- `peak_burner_temperature` (K) - Converted from Celsius if necessary.
- `final_o2_frac` (fraction) - PSI-25
- `initial_co2_frac`, `final_co2_frac` (fraction) - PSI-25
- `initial_co_ppm`, `final_co_ppm` (ppm) - PSI-25
- `co2_partial_pressure` (mmHg) - PSI-26
- `sample_thickness`, `sample_width`, `sample_diameter` (mm) - BASS material normalization

## BASS-II (PSI-25) and BASS (PSI-26) Implementation details

### PSI-25 BASS-II
- **Source**: `psi_data/PSI-25/Experimental table/PSI-25_Experimental table_BASS-II.csv`
- **Identity**: `PSI-25:bassii:<Test #>`
- **Pressure**: Assumed `1.0 atm` per audit specification (recorded in `raw._normalization`).
- **Oxygen**: `Calibrated initial O2 % by vol` divided by 100. Current output has all 129 values, range 0.136–0.222.
- **Outcomes**: Only explicit terms from Air/Fan display fields are classified; no ignition is inferred from numeric flow sequences. Current counts: 105 unknown, 16 extinction, 7 blowoff, 1 ignited.
- **CO readings**: Negative readings and non-numeric sentinels stay in the original row and are flagged; they are not stored as physical concentrations.

### PSI-26 BASS
- **Source**: `psi_data/PSI-26/Science Documents/PSI-26_Science Documents_As Run_Test Matrix.xlsx`, sheet `as run matrix`.
- **Identity**: `PSI-26:bass:<As run test no.>:<Test no.>`
- **Row filtering**: The sheet has 131 scanned data rows (including one blank row). 121 rows have both identifiers and are imported; nine non-test/incomplete rows and one blank row are skipped and counted separately.
- **Oxygen**: `O2 % by volume` values are used directly as fractions (approx 0.159-0.227).
- **Pressure**: The measured `pressure, atm` values are used directly. Source-to-output comparisons found no mismatches for O₂ or pressure among the 121 imported records.
- **Outcomes**: Explicit no-ignition wording takes precedence over positive ignition wording; other outcomes use conservative keyword matching against observations. Current counts: 6 no_ignition, 42 ignited, 32 extinction, 1 blowoff, 40 unknown.

### Common BASS Logic
- **Material/Geometry**: Normalized from `Fuel Sample Material` or `material` strings into `runs.material`, `runs.geometry`, `runs.size_mm`, and typed measurements (`sample_thickness`, `sample_width`, `sample_diameter`). A bare size is never labeled as width; sphere/rod sizes become diameters, and ambiguous dimensions remain untyped in `raw._normalization`.
- **Flow Display**: Air/Fan display strings are parsed into structured objects in `raw._normalization.flow_display`.

## Known Limitations / Flagged Issues
- **FLEX "Burning rate; mm"**: Preserved in `raw` but not normalized into a rate measurement because the time denominator is unconfirmed in the header.
- **CFI-G "flash"**: Treated as `unknown` outcome for now, pending further definition.
- **CFI-G "SE" / "FT"**: Preserved in `outcome_detail` but not mapped to a canonical outcome.
- **Multiple Diluents**: FLEX parser flags records with more than one non-zero diluent (CO/He).
- **BASS-II pressure**: The 1 atm value is an audit assumption, not measured pressure. The scenario API exposes pressure provenance so the dashboard can label it.
