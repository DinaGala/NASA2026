import json
import os
import sys
from typing import List, Dict, Any

from src.ingestion.parsers import FlexParser, CfiGParser, BassParser

def run_parsers():
    # Define paths
    flex_csv = "psi_data/PSI-69/Experimental table/PSI-69_Experimental table_FLEX.csv"
    cfi_g_normal_csv = "psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Normal Flames.csv"
    cfi_g_inverse_csv = "psi_data/PSI-159/Experimental table/PSI-159_Experimental table_CFI-G_Inverse Flames.csv"
    bass_ii_csv = "psi_data/PSI-25/Experimental table/PSI-25_Experimental table_BASS-II.csv"
    bass_xlsx = "psi_data/PSI-26/Science Documents/PSI-26_Science Documents_As Run_Test Matrix.xlsx"

    output_dir = "data/processed"
    os.makedirs(output_dir, exist_ok=True)

    all_stats = {}
    all_errors = {}

    # 1. FLEX
    print(f"Parsing FLEX: {flex_csv}")
    flex_parser = FlexParser(flex_csv, "PSI-69")
    flex_records, flex_stats = flex_parser.parse()
    all_stats["FLEX"] = flex_stats
    all_errors["flex_errors"] = flex_parser.errors
    
    flex_out = os.path.join(output_dir, "PSI-69_FLEX_normalized.json")
    with open(flex_out, "w") as f:
        json.dump(flex_records, f, indent=2)
    print(f"  Done. {len(flex_records)} records saved to {flex_out}")

    # 2. CFI-G Normal
    print(f"Parsing CFI-G Normal: {cfi_g_normal_csv}")
    normal_parser = CfiGParser(cfi_g_normal_csv, "PSI-159", "normal")
    normal_records, normal_stats = normal_parser.parse()
    all_stats["CFI-G_Normal"] = normal_stats
    all_errors["normal_errors"] = normal_parser.errors
    
    normal_out = os.path.join(output_dir, "PSI-159_CFI-G_Normal_normalized.json")
    with open(normal_out, "w") as f:
        json.dump(normal_records, f, indent=2)
    print(f"  Done. {len(normal_records)} records saved to {normal_out}")

    # 3. CFI-G Inverse
    print(f"Parsing CFI-G Inverse: {cfi_g_inverse_csv}")
    inverse_parser = CfiGParser(cfi_g_inverse_csv, "PSI-159", "inverse")
    inverse_records, inverse_stats = inverse_parser.parse()
    all_stats["CFI-G_Inverse"] = inverse_stats
    all_errors["inverse_errors"] = inverse_parser.errors
    
    inverse_out = os.path.join(output_dir, "PSI-159_CFI-G_Inverse_normalized.json")
    with open(inverse_out, "w") as f:
        json.dump(inverse_records, f, indent=2)
    print(f"  Done. {len(inverse_records)} records saved to {inverse_out}")

    # 4. PSI-25 BASS-II
    print(f"Parsing BASS-II: {bass_ii_csv}")
    bass_ii_parser = BassParser(bass_ii_csv, "PSI-25")
    bass_ii_records, bass_ii_stats = bass_ii_parser.parse()
    all_stats["BASS-II"] = bass_ii_stats
    all_errors["bass_ii_errors"] = bass_ii_parser.errors
    
    bass_ii_out = os.path.join(output_dir, "PSI-25_BASS-II_normalized.json")
    with open(bass_ii_out, "w") as f:
        json.dump(bass_ii_records, f, indent=2)
    print(f"  Done. {len(bass_ii_records)} records saved to {bass_ii_out}")

    # 5. PSI-26 BASS
    print(f"Parsing BASS: {bass_xlsx}")
    bass_parser = BassParser(bass_xlsx, "PSI-26")
    bass_records, bass_stats = bass_parser.parse()
    all_stats["BASS"] = bass_stats
    all_errors["bass_errors"] = bass_parser.errors
    
    bass_out = os.path.join(output_dir, "PSI-26_BASS_normalized.json")
    with open(bass_out, "w") as f:
        json.dump(bass_records, f, indent=2)
    print(f"  Done. {len(bass_records)} records saved to {bass_out}")

    # Summary report
    report_out = os.path.join(output_dir, "parsing_report.json")
    with open(report_out, "w") as f:
        json.dump({
            "stats": all_stats,
            **all_errors
        }, f, indent=2)
    
    print("\n--- Parsing Summary ---")
    for source, stats in all_stats.items():
        print(f"{source}:")
        header_rows = stats.get("header_rows", 1)
        print(f"  Physical rows including headers: {stats['total_rows'] + header_rows}")
        print(f"  Total rows processed: {stats['total_rows']}")
        print(f"  Parsed records: {stats['parsed_records']}")
        print(f"  Eligible records: {stats['eligible_records']}")
        print(f"  Skipped blank: {stats['skipped_blank_rows']}")
        print(f"  Flagged/Rejected: {stats['flagged_records']}")
        print(f"  Missing Pressure: {stats['missing_pressure_count']}")
        print(f"  Missing O2: {stats['missing_o2_count']}")
        print(f"  Duplicate test_id: {stats['duplicate_test_id_count']}")
        print(f"  Outcomes: {stats['outcome_counts']}")

if __name__ == "__main__":
    run_parsers()
