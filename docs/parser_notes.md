# PSI Data Parser - Implementation Notes

This document describes the parser and normalization logic implemented for PSI-69 (FLEX) and PSI-159 (CFI-G) as part of Step 3 of the PSI flame-data plan.

## How to Run

To run the parser on the current source files and generate normalized JSON output:

```bash
PYTHONPATH=. python3 scripts/parse_psi_data.py
```

Output files will be generated in `data/processed/`:
- `PSI-69_FLEX_normalized.json`
- `PSI-159_CFI-G_Normal_normalized.json`
- `PSI-159_CFI-G_Inverse_normalized.json`
- `parsing_report.json` (Summary stats and any recorded errors/flags)

## Normalization Logic

### Common Fields
- **Source Provenance**: Every record includes `psi_id`, `source_file`, `source_row_number`, and `test_id`.
- **Raw Data**: The original row from the CSV is preserved in the `raw` field of each record. Header normalization (trimming whitespace) is performed for field lookups but does not alter the original keys in the `raw` data.
- **is_eligible**: A boolean flag indicating if the record is suitable for dashboard analysis. Records with critical quality issues (like zero pressure) are marked `is_eligible: false`.
- **Quality Flags**: The `quality_flags` list contains labels for data issues (e.g., `invalid_pressure: zero`, `missing_pressure`, `multiple_diluents`).
- **Pressure**: Converted to `atm`.
  - FLEX: `mmHg / 760`
  - CFI-G: `bar / 1.01325`
  - Original value and unit are preserved in `pressure_source_value` and `pressure_source_unit`.
- **Outcome**: Mapped to canonical values: `extinction`, `disruption`, `completion`, `ignited`, `no_ignition`, or `unknown`.

### FLEX (PSI-69)
- **Identity**: `PSI-69:flex:<FLEX Test #>`. This ensures uniqueness even when `FLEX Identifier` is repeated.
- **Encoding**: Reads as `utf-8-sig` (BOM support) or `cp1252` if UTF-8 fails.
- **Header Normalization**: Trims leading/trailing whitespace (e.g., handles " FLEX Test #").
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

## Known Limitations / Flagged Issues
- **FLEX "Burning rate; mm"**: Preserved in `raw` but not normalized into a rate measurement because the time denominator is unconfirmed in the header.
- **CFI-G "flash"**: Treated as `unknown` outcome for now, pending further definition.
- **CFI-G "SE" / "FT"**: Preserved in `outcome_detail` but not mapped to a canonical outcome.
- **Multiple Diluents**: FLEX parser flags records with more than one non-zero diluent (CO/He).
