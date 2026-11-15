import unittest

from frontend.presets import PRESETS, load_verdict


class PresetTests(unittest.TestCase):
    def test_every_preset_has_a_picture_and_a_precomputed_verdict(self):
        for preset in PRESETS:
            with self.subTest(preset=preset.key):
                self.assertTrue(preset.picture.exists())
                self.assertIsNotNone(load_verdict(preset), "run make preset-verdicts")

    def test_precomputed_statuses_match_the_demo(self):
        expected = {"earth_air": "tested", "moon_habitat": "tested", "spacesuit": "untested"}
        self.assertEqual({p.key: load_verdict(p).status for p in PRESETS}, expected)

    def test_preset_pressures_are_converted_from_psi(self):
        moon = next(p for p in PRESETS if p.key == "moon_habitat")
        self.assertEqual(moon.pressure_atm, 0.69)


if __name__ == "__main__":
    unittest.main()
