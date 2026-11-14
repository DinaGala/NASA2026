"""Values the backend and the dashboard must agree on.

Distance between a run and a queried atmosphere (docs/api_contracts.md, "Distance and nearby"):

    distance = sqrt(((Δo2) / O2_SCALE)^2 + ((Δpressure) / PRESSURE_SCALE)^2)

A run is nearby when distance <= 1. /scenario computes it in SQL and the risk-surface chart
computes it per grid cell, so both read the scales from here.
"""

O2_SCALE = 0.05  # O2 mole fraction that counts as a distance of 1
PRESSURE_SCALE = 0.2  # atm that count as a distance of 1
