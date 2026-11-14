# Flame in Freefall — current database milestone

## Study inventory

The local `psi_data` directory contains investigation metadata for these 24 studies:

| PSI ID | Acronym | Study |
|---|---|---|
| PSI-10 | ACME/Flame Design | Advanced Combustion via Microgravity Experiments / Flame Design |
| PSI-20 | ACME BRE | Advanced Combustion via Microgravity Experiments - Burning Rate Emulator |
| PSI-21 | ACME CLD Flame | Advanced Combustion via Microgravity Experiments / Coflow Laminar Diffusion Flame |
| PSI-22 | ACME E-FIELD Flames | Advanced Combustion via Microgravity Experiments Electric-Field Effects on Laminar Diffusion Flames |
| PSI-23 | ACME s-Flame | Advanced Combustion via Microgravity Experiments-Structure and Response of Spherical Diffusion Flames |
| PSI-25 | BASS-II | Burning and Suppression of Solids - II |
| PSI-26 | BASS | Burning and Suppression of Solids |
| PSI-39 | CFI | Cool Flames Investigation |
| PSI-47 | DAFT / DAFT-2 | Dust and Aerosol Measurement Feasibility Test / Test-2 |
| PSI-60 | — | Computational and Experimental Analysis of SPICE Data Sets |
| PSI-62 | — | Concurrent Flame Spread Modeling using Flamelet Generated Manifolds in Microgravity with Comparison to BASS Experiments using Two-Color Tomography |
| PSI-68 | FLEX-2 | Flame Extinguishment Experiment - 2 |
| PSI-69 | FLEX | Flame Extinguishment Experiment |
| PSI-98 | SAFFIRE-I | Spacecraft Fire Safety Experiment-I |
| PSI-99 | SAFFIRE-II | Spacecraft Fire Experiment-II |
| PSI-100 | SAFFIRE-III | Spacecraft Fire Experiment-III |
| PSI-101 | SAME-R | Smoke Aerosol Measurement Experiment-Reflight |
| PSI-102 | SAME | Smoke and Aerosol Measurement Experiment |
| PSI-106 | SLICE | Structure and Liftoff In Combustion Experiment |
| PSI-107 | SPICE | Smoke Point In Co-flow Experiment |
| PSI-115 | — | Utilization of the Smoke Aerosol Measurement Experiment Data for Advanced Modeling and Simulation of Smoke Generation in Micro-Gravity |
| PSI-117 | — | Effect of External Thermo-Convective Perturbation on Cool Flame Dynamics: A Multidimensional Multi-Physics CFD Analysis |
| PSI-142 | — | Quantitative Studies of Cool Flame Transitions at Radiation / Stretch Extinction Using Counterflow Flames |
| PSI-159 | ACME CFI-G | Advanced Combustion via Microgravity Experiments / Cool Flames Investigation with Gases |

## Database contract for this milestone

The source of truth for field definitions is [`data_audit.md`](data_audit.md). The PostgreSQL database is `psi_flame`, with tables in the `psi_dashboard` schema:

- `investigations`: one metadata record per PSI study.
- `scenarios`: planned experiment/test-level records; this is the agreed table name in place of `tests`.
- `measurements`: numeric measurements associated with a scenario through `test_id`.

The schema setup creates all three tables. The current seed/import step populates only `investigations` from `psi_data/PSI-*/investigation.json`; scenario and measurement data remain empty for now. `raw` retains the original PSI metadata JSON. Re-running the investigation import upserts by `psi_id`.

## Current work boundary

For this cleanup, only the database container/configuration, the three-table schema, and investigation metadata import are in scope. Scenario/measurement loaders, APIs, dashboards, publications, documents, chunks, embeddings, and time-series ingestion are later work and are not part of this milestone.
