import unittest

import numpy as np

from frontend.charts import O2_GRID, PRESSURE_GRID, risk_surface, surface_grid
from src.contracts import Run, ScenarioQuery

MOON = ScenarioQuery(o2_frac=0.30, pressure_atm=0.69)


def run(o2, pressure, outcome, distance=5.0):
    return Run(run_id=f"T-{o2}-{pressure}-{outcome}", psi_id="PSI-69", label="Heptane",
               o2_frac=o2, pressure_atm=pressure, outcome=outcome, distance=distance)


def cell(share, o2, pressure):
    return share[np.abs(PRESSURE_GRID - pressure).argmin(), np.abs(O2_GRID - o2).argmin()]


class SurfaceGridTests(unittest.TestCase):
    def test_cells_with_fewer_than_3_runs_nearby_stay_blank(self):
        points = [run(0.30, 0.70, "disruption"), run(0.30, 0.70, "completion")]
        share = surface_grid(points, "droplet")
        self.assertTrue(np.isnan(share).all())

    def test_droplet_outcomes_map_to_kept_burning_or_went_out(self):
        burning = [run(0.30, 0.70, o) for o in ("disruption", "completion", "disruption")]
        out = [run(0.15, 1.0, "extinction")] * 3
        share = surface_grid(burning + out, "droplet")
        self.assertEqual(cell(share, 0.30, 0.70), 1.0)
        self.assertEqual(cell(share, 0.15, 1.0), 0.0)

    def test_unknown_outcomes_count_toward_the_mask_but_not_the_share(self):
        points = [run(0.30, 0.70, "ignited"), run(0.30, 0.70, "unknown"), run(0.30, 0.70, None)]
        share = surface_grid(points, "gas_normal")
        self.assertEqual(cell(share, 0.30, 0.70), 1.0)

    def test_runs_with_only_unknown_outcomes_draw_nothing(self):
        share = surface_grid([run(0.30, 0.70, "unknown")] * 3, "gas_normal")
        self.assertTrue(np.isnan(share).all())


class RiskSurfaceTests(unittest.TestCase):
    def test_solid_draws_dots_and_the_note_but_no_surface(self):
        fig = risk_surface([run(0.21, 1.0, "unknown")] * 3, MOON, "solid")
        self.assertNotIn("contour", {trace.type for trace in fig.data})
        self.assertIn("Outcomes not recorded for solids", [a.text for a in fig.layout.annotations])

    def test_status_counts_runs_within_distance_1(self):
        points = [run(0.30, 0.70, "disruption", distance=0.1)] * 3
        fig = risk_surface(points, MOON, "droplet")
        self.assertIn("TESTED", [a.text for a in fig.layout.annotations])


if __name__ == "__main__":
    unittest.main()
