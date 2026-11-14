import unittest
import os
import json
import tempfile
from src.ingestion.parsers import FlexParser, CfiGParser

class TestParsers(unittest.TestCase):
    def test_flex_normalization(self):
        # Create a tiny FLEX CSV with whitespace in header
        csv_content = (
            " FLEX Test #,FLEX Identifier,Fuel,Ambient pressure; mmHg,O initial ambient composition; mole fraction,"
            "CO initial ambient composition; mole fraction,He initial ambient composition; mole fraction,"
            "Droplet initial diameter; mm,Visible flame extinction diameter; mm,Burn time; s,Test end\n"
            "1,ID1,Methanol,760,0.21,0.1,0,2.0,1.0,5.0,Extinction\n"
            "114,ID114,Methanol,0,0.21,0.1,0,2.0,1.0,5.0,Extinction\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tf:
            tf.write(csv_content)
            temp_path = tf.name

        try:
            parser = FlexParser(temp_path, "PSI-69")
            records, stats = parser.parse()
            
            self.assertEqual(len(records), 2)
            self.assertEqual(stats["parsed_records"], 2)
            self.assertEqual(stats["eligible_records"], 1)
            
            # Row 1
            r1 = records[0]
            self.assertEqual(r1["test_id"], "PSI-69:flex:1")
            self.assertAlmostEqual(r1["pressure_atm"], 1.0)
            self.assertEqual(r1["outcome"], "extinction")
            self.assertEqual(r1["diluent"], "CO")
            self.assertEqual(r1["diluent_frac"], 0.1)
            self.assertTrue(r1["is_eligible"])
            
            # Row 114
            r114 = records[1]
            self.assertEqual(r114["test_id"], "PSI-69:flex:114")
            self.assertEqual(r114["pressure_atm"], 0.0)
            self.assertIn("invalid_pressure: zero", r114["quality_flags"])
            self.assertFalse(r114["is_eligible"])
            
        finally:
            os.remove(temp_path)

    def test_cfi_g_normalization(self):
        # Normal with leading space in oxygen header
        normal_content = (
            "Flame Type,Fuel,Test, XO2 before test,XO2  after test,approx p (bar),Hot ignition,burn time (sec)\n"
            "Normal,ethane,T1,0.4,0.38,1.0,Yes,50\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tf:
            tf.write(normal_content)
            norm_path = tf.name
        
        # Inverse
        inverse_content = (
            "Flame Type,Fuel,Test,XO2,approx p (bar),Hot ignition,burn time (sec)\n"
            "Inverse,ethane,I1,0.21,1.01325,flash,30\n"
            ",,,,,,,\n" # blank row
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tf:
            tf.write(inverse_content)
            inv_path = tf.name

        try:
            # Test Normal
            parser_n = CfiGParser(norm_path, "PSI-159", "normal")
            recs_n, stats_n = parser_n.parse()
            self.assertEqual(len(recs_n), 1)
            self.assertEqual(recs_n[0]["o2_frac"], 0.4)
            self.assertTrue(recs_n[0]["o2_is_ambient"])
            self.assertEqual(recs_n[0]["outcome"], "ignited")
            
            # Test Inverse
            parser_i = CfiGParser(inv_path, "PSI-159", "inverse")
            recs_i, stats_i = parser_i.parse()
            self.assertEqual(len(recs_i), 1) # blank row skipped
            self.assertEqual(recs_i[0]["o2_frac"], 0.21)
            self.assertFalse(recs_i[0]["o2_is_ambient"])
            self.assertEqual(recs_i[0]["outcome"], "unknown")
            self.assertEqual(recs_i[0]["outcome_detail"], "flash")
            self.assertAlmostEqual(recs_i[0]["pressure_atm"], 1.0)
            self.assertEqual(stats_i["skipped_blank_rows"], 1)

        finally:
            os.remove(norm_path)
            os.remove(inv_path)

if __name__ == "__main__":
    unittest.main()
