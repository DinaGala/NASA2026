import os
import json
import pandas as pd
import numpy as np
from sqlalchemy import text
from src.database import engine
from pathlib import Path
import datetime

DATA_DIR = Path("/Users/dinaz/nasa/nasa2026/psi_data")

FILES_TO_IMPORT = [
    ("PSI-69", "Experimental table/PSI-69_Experimental table_FLEX.csv", "FLEX"),
    ("PSI-68", "Experimental table/PSI-68_Experimental table_Flex2.csv", "FLEX-2"),
    ("PSI-26", "Experimental table/PSI-26_Experimental table_BASS.csv", "BASS"),
    ("PSI-25", "Experimental table/PSI-25_Experimental table_BASS-II.csv", "BASS-II"),
    ("PSI-98", "Experimental table/PSI-98_Experimental table_SAFFIRE-1.csv", "SAFFIRE-I"),
    ("PSI-99", "Experimental table/PSI-99_Experimental table_SAFFIRE-2.csv", "SAFFIRE-II"),
    ("PSI-100", "Experimental table/PSI-100_Experimental table_SAFFIRE-3.csv", "SAFFIRE-III"),
    ("PSI-20", "Experimental table/PSI-20_Experimental table_ACME BRE.csv", "ACME-BRE"),
    ("PSI-21", "Experimental table/PSI-21_Experimental table_CLD Flame.csv", "CLD-Flame"),
    ("PSI-22", "Experimental table/PSI-22_Experimental table_ACME E-FIELD Flames.csv", "ACME-E-FIELD"),
    ("PSI-23", "Experimental table/PSI-23_Experimental table_ACME s-Flame.csv", "ACME-S-FLAME"),
    ("PSI-47", "Experimental table/PSI-47_Experimental table_DAFT.csv", "DAFT"),
    ("PSI-101", "Experimental table/PSI-101_Experimental table_SAME-R.csv", "SAME-R"),
    ("PSI-102", "Experimental table/PSI-102_Experimental table_SAME.csv", "SAME"),
]

def to_float(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        if isinstance(val, str):
            if "-" in val and not val.startswith("-"):
                return None
            val = val.replace("~", "").replace(">", "").replace("<", "").strip().split()[0]
        f = float(val)
        return f if not np.isnan(f) else None
    except:
        return None

def import_investigation(conn, psi_id):
    inv_path = DATA_DIR / psi_id / "investigation.json"
    name = psi_id
    metadata = {}
    if inv_path.exists():
        with open(inv_path, 'r', encoding='utf-8', errors='replace') as f:
            metadata = json.load(f)
            name = metadata.get("title", psi_id)
    
    conn.execute(text("""
        INSERT INTO psi_dashboard.investigations (psi_id, name, metadata_json)
        VALUES (:psi_id, :name, :metadata)
        ON CONFLICT (psi_id) DO UPDATE SET name = EXCLUDED.name, metadata_json = EXCLUDED.metadata_json
    """), {"psi_id": psi_id, "name": name, "metadata": json.dumps(metadata)})
    
    res = conn.execute(text("SELECT id FROM psi_dashboard.investigations WHERE psi_id = :psi_id"), {"psi_id": psi_id})
    return res.scalar()

def log_source_file(conn, inv_id, rel_path):
    conn.execute(text("""
        INSERT INTO psi_dashboard.source_files (investigation_id, relative_path, import_status)
        VALUES (:inv_id, :path, 'imported')
        ON CONFLICT (investigation_id, relative_path) DO UPDATE SET imported_at = CURRENT_TIMESTAMP
    """), {"inv_id": inv_id, "path": rel_path})
    
    res = conn.execute(text("SELECT id FROM psi_dashboard.source_files WHERE investigation_id = :inv_id AND relative_path = :path"), {"inv_id": inv_id, "path": rel_path})
    return res.scalar()

def get_run_id(conn, inv_id, source_run_id, source_file):
    res = conn.execute(text("SELECT id FROM psi_dashboard.runs WHERE investigation_id = :inv_id AND source_run_id = :run_id AND source_file = :file"), 
                       {"inv_id": inv_id, "run_id": source_run_id, "file": source_file})
    return res.scalar()

def import_daft_timeseries(conn, inv_id):
    # DAFT DustTrak and P-Trak files
    daft_dir = DATA_DIR / "PSI-47" / "Analyzed Data" / "Reduced Gravity"
    
    # Process the individual DustTrak and P-Trak files
    for instrument in ["DustTrak", "P-TRAK"]:
        instr_dir = daft_dir / instrument
        if not instr_dir.exists(): continue
        for xls_file in instr_dir.glob("*.xls"):
            rel_path = str(xls_file.relative_to(DATA_DIR))
            file_id = log_source_file(conn, inv_id, rel_path)
            
            xl = pd.ExcelFile(xls_file)
            for sheet in xl.sheet_names:
                if sheet == "TIMING":
                    # Timing sheet usually applies to multiple runs in the same file
                    # But we need a run_id to link it. We'll skip for now or link to a generic run.
                    continue
                
                df = xl.parse(sheet)
                if df.empty: continue
                
                # Identify run from sheet name or metadata
                # Sheets like DTrk0002, PTrk0002. TestID is in row 1 col 0
                test_id = None
                metadata = {}
                for i, row in df.iterrows():
                    if i > 10: break
                    cell0 = str(row.iloc[0])
                    if "TestID:" in cell0:
                        test_id = cell0.split(":")[-1].strip()
                        break
                
                if not test_id: continue
                
                # Ensure run exists (DAFT runs are from Experimental Table)
                # But these might be additional runs.
                source_run_id = f"Test {test_id}"
                # Try to find run_id from main experimental table
                run_id = get_run_id(conn, inv_id, source_run_id, "Experimental table/PSI-47_Experimental table_DAFT.csv")
                if not run_id:
                    # Create run if not exists
                    res = conn.execute(text("""
                        INSERT INTO psi_dashboard.runs (investigation_id, source_run_id, source_file)
                        VALUES (:inv_id, :run_id, :file)
                        ON CONFLICT DO NOTHING RETURNING id
                    """), {"inv_id": inv_id, "run_id": source_run_id, "file": rel_path})
                    run_id = res.scalar()
                    if not run_id:
                        res = conn.execute(text("SELECT id FROM psi_dashboard.runs WHERE investigation_id = :inv_id AND source_run_id = :run_id"), {"inv_id": inv_id, "run_id": source_run_id})
                        run_id = res.scalar()

                # Measurements start after header (usually row 7 or 8)
                # Let's find "Time (sec)" column
                time_col = None
                val_col = None
                unit = "mg/m3" if "Dust" in instrument else "particles/cc"
                
                for col in df.columns:
                    if "Time" in str(col): time_col = col
                    if "mg/m" in str(col) or "pt/cc" in str(col) or "particles" in str(col): 
                        val_col = col
                        if "(" in str(col): unit = str(col).split("(")[-1].split(")")[0]

                if time_col is not None and val_col is not None:
                    # Identify if it's the instrument sheet for this file. 
                    # For DustTrak instrument, only import from DTrk sheets.
                    if "Dust" in instrument and not str(sheet).startswith("DTrk"): continue
                    if "P-TRAK" in instrument and not str(sheet).startswith("PTrk"): continue

                    # Clear existing for idempotency
                    conn.execute(text("DELETE FROM psi_dashboard.sensor_measurements WHERE run_id = :run_id AND source_file_id = :file_id AND variable = :var"), 
                                 {"run_id": run_id, "file_id": file_id, "var": str(val_col)})
                    
                    data_rows = []
                    for i, row in df.iterrows():
                        t = to_float(row[time_col])
                        v = to_float(row[val_col])
                        if t is not None:
                            data_rows.append({
                                "run_id": run_id, "file_id": file_id, "t": t, "sensor": instrument,
                                "var": val_col, "num": v, "unit": unit, "col": str(val_col), "row": i+2
                            })
                    
                    if data_rows:
                        conn.execute(text("""
                            INSERT INTO psi_dashboard.sensor_measurements (run_id, source_file_id, time_seconds, sensor, variable, numeric_value, unit, source_column, source_row_number)
                            VALUES (:run_id, :file_id, :t, :sensor, :var, :num, :unit, :col, :row)
                            ON CONFLICT (run_id, sensor, variable, time_seconds, source_row_number) DO UPDATE SET numeric_value = EXCLUDED.numeric_value
                        """), data_rows)

def import_same_timeseries(conn, inv_id, psi_id):
    same_dir = DATA_DIR / psi_id / "Analyzed Data" / "Reduced Gravity"
    if not same_dir.exists(): return
    
    # Traverse subdirectories for runs
    for root, dirs, files in os.walk(same_dir):
        for f in files:
            if f.endswith(".xls") and "run" in f.lower():
                xls_path = Path(root) / f
                rel_path = str(xls_path.relative_to(DATA_DIR))
                file_id = log_source_file(conn, inv_id, rel_path)
                
                # Extract run number from filename or path
                run_name = f.split(".")[0] # e.g. PSI-101_Analyzed Data_2010_200_12_lampwick_run49
                run_num = None
                if "run" in run_name.lower():
                    run_num = run_name.lower().split("run")[-1].strip("_")
                
                if not run_num: continue
                source_run_id = str(int(run_num)) if run_num.isdigit() else run_num
                
                # Find matching run from Experimental Table
                exp_table = f"Experimental table/{psi_id}_Experimental table_{'SAME-R' if '101' in psi_id else 'SAME'}.csv"
                run_id = get_run_id(conn, inv_id, source_run_id, exp_table)
                if not run_id: continue
                
                xl = pd.ExcelFile(xls_path)
                if "Original (cut and paste)" in xl.sheet_names:
                    df = xl.parse("Original (cut and paste)")
                    # Clear existing
                    conn.execute(text("DELETE FROM psi_dashboard.sensor_measurements WHERE run_id = :run_id AND source_file_id = :file_id"), {"run_id": run_id, "file_id": file_id})
                    
                    # Columns to import
                    cols_to_import = [c for c in df.columns if any(x in c for x in ["Volts", "DegC", "PtPerCC", "MgPerM3", "PSIA", "PSIG", "SCCM", "LPM", "Ohms"])]
                    
                    data_rows = []
                    for i, row in df.iterrows():
                        t = to_float(row.get("ElapsedTestTime(Secs)"))
                        if t is None: continue
                        
                        for col in cols_to_import:
                            v = to_float(row[col])
                            unit = None
                            if "(" in col: unit = col.split("(")[-1].split(")")[0]
                            
                            data_rows.append({
                                "run_id": run_id, "file_id": file_id, "t": t, "sensor": "SAME",
                                "var": col, "num": v, "unit": unit, "col": col, "row": i+2
                            })
                        
                        # Batch insert every 1000 rows to avoid huge queries
                        if len(data_rows) >= 1000:
                            conn.execute(text("""
                                INSERT INTO psi_dashboard.sensor_measurements (run_id, source_file_id, time_seconds, sensor, variable, numeric_value, unit, source_column, source_row_number)
                                VALUES (:run_id, :file_id, :t, :sensor, :var, :num, :unit, :col, :row)
                                ON CONFLICT (run_id, sensor, variable, time_seconds, source_row_number) DO UPDATE SET numeric_value = EXCLUDED.numeric_value
                            """), data_rows)
                            data_rows = []
                    
                    if data_rows:
                        conn.execute(text("""
                            INSERT INTO psi_dashboard.sensor_measurements (run_id, source_file_id, time_seconds, sensor, variable, numeric_value, unit, source_column, source_row_number)
                            VALUES (:run_id, :file_id, :t, :sensor, :var, :num, :unit, :col, :row)
                            ON CONFLICT (run_id, sensor, variable, time_seconds, source_row_number) DO UPDATE SET numeric_value = EXCLUDED.numeric_value
                        """), data_rows)

def import_bass_metadata(conn, inv_id, psi_id):
    # Import Test Matrices as run_values
    science_dir = DATA_DIR / psi_id / "Science Documents"
    if not science_dir.exists(): return
    
    matrix_file = None
    if psi_id == "PSI-25": matrix_file = science_dir / "PSI-25_Science Documents_BASS-II_Test.Matrices.xlsx"
    elif psi_id == "PSI-26": matrix_file = science_dir / "PSI-26_Science Documents_As Run_Test Matrix.xlsx"
    
    if matrix_file and matrix_file.exists():
        rel_path = str(matrix_file.relative_to(DATA_DIR))
        file_id = log_source_file(conn, inv_id, rel_path)
        xl = pd.ExcelFile(matrix_file)
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            # Find Run column
            run_col = None
            for col in df.columns:
                if "Test #" in str(col) or "Test#" in str(col) or "Test Point" in str(col):
                    run_col = col
                    break
            
            if run_col is None: continue
            
            for i, row in df.iterrows():
                source_run_id = str(row[run_col])
                if source_run_id == "nan" or source_run_id == "None": continue
                
                # Match to existing run. Try with and without prefix B/F/M/O/T
                exp_table = f"Experimental table/{psi_id}_Experimental table_{'BASS-II' if '25' in psi_id else 'BASS'}.csv"
                run_id = get_run_id(conn, inv_id, source_run_id, exp_table)
                if not run_id and psi_id == "PSI-25":
                    # Try adding/removing 'B' or 'F' etc.
                    prefix = sheet[0].upper() # B, F, M, O, T
                    # Try both integer and string matching
                    try:
                        rid_int = int(float(source_run_id))
                        run_id = get_run_id(conn, inv_id, f"{prefix}{rid_int}", exp_table)
                    except:
                        run_id = get_run_id(conn, inv_id, f"{prefix}{source_run_id}", exp_table)
                
                if not run_id: continue
                
                # Import other columns as run_values
                for col in df.columns:
                    if col == run_col or "Date" in str(col): continue
                    val = row[col]
                    if val is None or str(val) == "nan": continue
                    
                    num_val = to_float(val)
                    txt_val = str(val)
                    unit = None
                    if "(" in str(col): unit = str(col).split("(")[-1].split(")")[0]
                    
                    conn.execute(text("""
                        INSERT INTO psi_dashboard.run_values (run_id, variable, numeric_value, text_value, unit, source_column, source_file)
                        VALUES (:run_id, :var, :num, :txt, :unit, :col, :file)
                    """), {"run_id": run_id, "var": f"{sheet}: {col}", "num": num_val, "txt": txt_val, "unit": unit, "col": str(col), "file": rel_path})

    # Fan calibration for BASS-II
    if psi_id == "PSI-25":
        cal_file = DATA_DIR / "PSI-25" / "Reference Data" / "PSI-25_Reference Data_Fan calibration data.xlsx"
        if cal_file.exists():
            rel_path = str(cal_file.relative_to(DATA_DIR))
            file_id = log_source_file(conn, inv_id, rel_path)
            # This workbook is complex, we'll just log it as source file for now.
            # A more detailed parser could be added if specific mapping is provided.

def main():
    with engine.connect() as conn:
        for psi_id, rel_path, family in FILES_TO_IMPORT:
            print(f"Importing {psi_id} - {rel_path}...")
            inv_id = import_investigation(conn, psi_id)
            log_source_file(conn, inv_id, rel_path)
            
            csv_path = DATA_DIR / psi_id / rel_path
            if not csv_path.exists():
                print(f"File not found: {csv_path}")
                continue
            
            try:
                df = pd.read_csv(csv_path, skipinitialspace=True, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(csv_path, skipinitialspace=True, encoding='latin1')
            
            df = df.replace({np.nan: None})

            if family == "FLEX-2":
                conn.execute(text("DELETE FROM psi_dashboard.design_ranges WHERE investigation_id = :inv_id"), {"inv_id": inv_id})
                for _, row in df.iterrows():
                    fuel = row.get("Sample (Fuel Type)")
                    if fuel is None: continue
                    for col in df.columns:
                        if col == "Sample (Fuel Type)": continue
                        conn.execute(text("""
                            INSERT INTO psi_dashboard.design_ranges (investigation_id, fuel, parameter, range_text, source_file)
                            VALUES (:inv_id, :fuel, :param, :range, :file)
                        """), {"inv_id": inv_id, "fuel": str(fuel), "param": col, "range": str(row[col]) if row[col] is not None else None, "file": rel_path})
                conn.commit()
                continue

            # Standard run import
            for _, row in df.iterrows():
                source_run_id = None
                if family == "FLEX": source_run_id = str(row.get("FLEX Test #"))
                elif family == "BASS": source_run_id = str(row.get("As Run Test #"))
                elif family == "BASS-II": source_run_id = str(row.get("Test #"))
                elif family.startswith("SAFFIRE"): source_run_id = str(row.get("Sample Number"))
                elif family in ["CLD-Flame", "ACME-E-FIELD"]: source_run_id = str(row.get("Test Day"))
                elif family == "ACME-S-FLAME": source_run_id = str(row.get("Test Day*"))
                elif family == "DAFT": source_run_id = str(row.get("Session Name"))
                elif family == "SAME-R": source_run_id = str(row.get("SAME Test #"))
                elif family == "SAME": source_run_id = str(row.get("Sample #"))
                elif family == "ACME-BRE":
                    source_run_id = f"{row.get('Year')}-{row.get('Month')}-{row.get('Date')}-{row.get('Burner Size (mm)')}"
                
                if not source_run_id or source_run_id == "None":
                    continue

                fuel = row.get("Fuel") or row.get("Fuel Sample Material") or row.get("Material")
                date = row.get("Test Date") or row.get("Date")
                gmt = row.get("Test GMT") or row.get("GMT")
                flow = row.get("Flow Direction") or row.get("Direction")
                
                res = conn.execute(text("""
                    INSERT INTO psi_dashboard.runs (investigation_id, source_run_id, date, gmt, fuel, flow_direction, source_file)
                    VALUES (:inv_id, :run_id, :date, :gmt, :fuel, :flow, :file)
                    ON CONFLICT (investigation_id, source_run_id, source_file) DO UPDATE 
                    SET date = EXCLUDED.date, gmt = EXCLUDED.gmt, fuel = EXCLUDED.fuel, flow_direction = EXCLUDED.flow_direction
                    RETURNING id
                """), {
                    "inv_id": inv_id, "run_id": source_run_id, "date": str(date) if date else None,
                    "gmt": str(gmt) if gmt else None, "fuel": str(fuel) if fuel else None,
                    "flow": str(flow) if flow else None, "file": rel_path
                })
                run_id = res.scalar()
                
                # For run_values, we want to update/add without duplication. 
                # Existing code deletes all run_values for the run and re-inserts. 
                # This is safe for idempotency since we are re-processing the whole file.
                conn.execute(text("DELETE FROM psi_dashboard.run_values WHERE run_id = :run_id AND source_file = :file"), {"run_id": run_id, "file": rel_path})

                for col in df.columns:
                    if col in ["FLEX Test #", "As Run Test #", "Test #", "Sample Number", "Test Date", "Date", "Test GMT", "GMT", "Fuel", "Fuel Sample Material", "Material", "Flow Direction", "Direction", 
                               "Test Day", "Test Day*", "Session Name", "SAME Test #", "Sample #", "Year", "Month", "Sample Type", "Sample Material  "]:
                        continue
                    
                    val = row[col]
                    if val is None: continue
                    
                    num_val = to_float(val)
                    txt_val = str(val)
                    
                    unit = None
                    if ";" in col: unit = col.split(";")[-1].strip()
                    elif "(" in col: unit = col.split("(")[-1].replace(")", "").strip()
                    
                    phase = None
                    if "initial" in col.lower(): phase = "initial"
                    elif "final" in col.lower(): phase = "final"

                    conn.execute(text("""
                        INSERT INTO psi_dashboard.run_values (run_id, variable, numeric_value, text_value, unit, phase, source_column, source_file)
                        VALUES (:run_id, :var, :num, :txt, :unit, :phase, :col, :file)
                    """), {
                        "run_id": run_id, "var": col, "num": num_val, "txt": txt_val,
                        "unit": unit, "phase": phase, "col": col, "file": rel_path
                    })
            conn.commit()

            # Post-processing for specific studies
            if family == "DAFT":
                import_daft_timeseries(conn, inv_id)
            elif family in ["SAME", "SAME-R"]:
                import_same_timeseries(conn, inv_id, psi_id)
            elif family in ["BASS", "BASS-II"]:
                import_bass_metadata(conn, inv_id, psi_id)
            conn.commit()

        print("Import completed.")

if __name__ == "__main__":
    main()
