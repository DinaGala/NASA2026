import csv
import io
import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

class BaseParser(ABC):
    def __init__(self, file_path: str, psi_id: str):
        self.file_path = file_path
        self.psi_id = psi_id
        self.results: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.stats = {
            "source_file": file_path,
            "total_rows": 0,
            "parsed_records": 0,
            "eligible_records": 0,
            "skipped_blank_rows": 0,
            "flagged_records": 0,
            "missing_pressure_count": 0,
            "missing_o2_count": 0,
            "duplicate_test_id_count": 0,
            "outcome_counts": {},
        }
        self.seen_test_ids = set()

    @abstractmethod
    def parse(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        pass

    def _normalize_headers(self, fieldnames: List[str]) -> Dict[str, str]:
        """Map trimmed headers to original headers."""
        return {f.strip(): f for f in fieldnames}

    def _add_outcome_stat(self, outcome: str):
        self.stats["outcome_counts"][outcome] = self.stats["outcome_counts"].get(outcome, 0) + 1

    def _record_error(self, row_num: int, field: str, value: Any, reason: str):
        self.errors.append({
            "row_number": row_num,
            "field": field,
            "value": value,
            "reason": reason
        })

class FlexParser(BaseParser):
    def parse(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        # Requirement 1: Read FLEX with encoding required by source mapping (CP1252)
        # Handle UTF-8 BOM is covered by utf-8-sig
        try:
            with open(self.file_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
                encoding_used = "utf-8-sig"
        except UnicodeDecodeError:
            with open(self.file_path, "r", encoding="cp1252") as f:
                content = f.read()
                encoding_used = "cp1252"

        self.stats["encoding"] = encoding_used
        
        # We need to preserve original headers for 'raw' but trim for lookup
        f_obj = io.StringIO(content)
        reader = csv.DictReader(f_obj)
        header_map = self._normalize_headers(reader.fieldnames or [])
        
        for i, row in enumerate(reader, start=2): # Start at 2 (1 is header)
            self.stats["total_rows"] += 1
            
            # Helper to get value using trimmed key
            def get_val(trimmed_key):
                orig_key = header_map.get(trimmed_key)
                return row.get(orig_key, "").strip() if orig_key else ""

            test_num = get_val("FLEX Test #")
            
            # Requirement 5: Use FLEX Test # as identity.
            if not test_num:
                self.stats["skipped_blank_rows"] += 1
                continue

            test_id = f"PSI-69:flex:{test_num}"
            if test_id in self.seen_test_ids:
                self.stats["duplicate_test_id_count"] += 1
            self.seen_test_ids.add(test_id)
            
            # Requirement 4: Preserve raw data
            # header normalization must not discard or rewrite source values in raw
            record = {
                "test_id": test_id,
                "psi_id": self.psi_id,
                "source_file": self.file_path,
                "source_row_number": i,
                "run_label": get_val("FLEX Identifier"),
                "granularity": "test",
                "flame_config": "droplet",
                "o2_is_ambient": True,
                "is_eligible": True, # Default to True
                "raw": row,
                "measurements": [],
                "quality_flags": []
            }

            # Normalization rules
            # Fuel
            record["fuel"] = get_val("Fuel")

            # Pressure mmHg -> atm
            p_raw = get_val("Ambient pressure; mmHg")
            if not p_raw:
                self.stats["missing_pressure_count"] += 1
                record["is_eligible"] = False
                record["quality_flags"].append("missing_pressure")
            else:
                try:
                    p_val = float(p_raw)
                    # Requirement: Handle FLEX Test #114 (pressure 0)
                    if p_val == 0:
                        record["is_eligible"] = False
                        record["quality_flags"].append("invalid_pressure: zero")
                        self.stats["flagged_records"] += 1
                        self._record_error(i, "Ambient pressure; mmHg", p_raw, "Pressure is zero; excluded from dashboard")
                    
                    record["pressure_atm"] = p_val / 760.0
                    record["pressure_source_value"] = p_val
                    record["pressure_source_unit"] = "mmHg"
                except ValueError:
                    self._record_error(i, "Ambient pressure; mmHg", p_raw, "Non-numeric pressure")
                    record["is_eligible"] = False
                    record["quality_flags"].append("invalid_pressure: non-numeric")

            # O2 fraction
            o2_raw = get_val("O initial ambient composition; mole fraction")
            if not o2_raw:
                self.stats["missing_o2_count"] += 1
            else:
                try:
                    record["o2_frac"] = float(o2_raw)
                except ValueError:
                    self._record_error(i, "O initial ambient composition; mole fraction", o2_raw, "Non-numeric O2")

            # Diluent
            co_frac = get_val("CO initial ambient composition; mole fraction")
            he_frac = get_val("He initial ambient composition; mole fraction")
            try:
                co_v = float(co_frac) if co_frac else 0.0
                he_v = float(he_frac) if he_frac else 0.0
                if co_v > 0 and he_v > 0:
                    record["quality_flags"].append("multiple_diluents")
                elif co_v > 0:
                    record["diluent"] = "CO"
                    record["diluent_frac"] = co_v
                elif he_v > 0:
                    record["diluent"] = "He"
                    record["diluent_frac"] = he_v
            except ValueError:
                self._record_error(i, "diluents", f"CO:{co_frac}, He:{he_frac}", "Non-numeric diluent fraction")

            # Measurements
            self._add_meas(record, "initial_droplet_diameter", get_val("Droplet initial diameter; mm"), "mm")
            self._add_meas(record, "extinction_diameter", get_val("Visible flame extinction diameter; mm"), "mm")
            self._add_meas(record, "burn_time", get_val("Burn time; s"), "s")
            
            # Burning rate ambiguity
            # "Do not convert FLEX “Burning rate; mm” into a rate until its meaning and unit are verified."
            # It's already in raw.

            # Outcome
            outcome_raw = get_val("Test end")
            outcome_map = {
                "extinction": "extinction",
                "disruption": "disruption",
                "completion": "completion"
            }
            norm_outcome = outcome_map.get(outcome_raw.lower())
            if norm_outcome:
                record["outcome"] = norm_outcome
                self._add_outcome_stat(norm_outcome)
            else:
                record["outcome"] = "unknown"
                self._add_outcome_stat("unknown")
                if outcome_raw:
                     self._record_error(i, "Test end", outcome_raw, "Unknown outcome")

            if record["is_eligible"]:
                self.stats["eligible_records"] += 1
            
            self.results.append(record)
            self.stats["parsed_records"] += 1

        return self.results, self.stats

    def _add_meas(self, record, var, val, unit):
        if val and val.strip():
            try:
                record["measurements"].append({
                    "variable": var,
                    "value": float(val.strip()),
                    "unit": unit
                })
            except ValueError:
                pass

class CfiGParser(BaseParser):
    def __init__(self, file_path: str, psi_id: str, flame_type: str):
        super().__init__(file_path, psi_id)
        self.flame_type = flame_type # 'normal' or 'inverse'

    def parse(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        # Requirement 1: Read CFI-G with UTF-8 BOM support
        with open(self.file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        f_obj = io.StringIO(content)
        reader = csv.DictReader(f_obj)
        header_map = self._normalize_headers(reader.fieldnames or [])
        
        for i, row in enumerate(reader, start=2):
            self.stats["total_rows"] += 1

            # Helper to get value using trimmed key
            def get_val(trimmed_key):
                orig_key = header_map.get(trimmed_key)
                return row.get(orig_key, "").strip() if orig_key else ""

            test_label = get_val("Test")
            
            # Requirement 6: Ignore completely blank rows
            if not any(v and isinstance(v, str) and v.strip() for v in row.values()) or not test_label:
                self.stats["skipped_blank_rows"] += 1
                continue

            test_id = f"PSI-159:{self.flame_type}:{test_label}"
            if test_id in self.seen_test_ids:
                self.stats["duplicate_test_id_count"] += 1
            self.seen_test_ids.add(test_id)
            
            record = {
                "test_id": test_id,
                "psi_id": self.psi_id,
                "source_file": self.file_path,
                "source_row_number": i,
                "run_label": test_label,
                "granularity": "test",
                "flame_config": f"gas_{self.flame_type}",
                "o2_is_ambient": (self.flame_type == "normal"),
                "is_eligible": True,
                "raw": row,
                "measurements": [],
                "quality_flags": []
            }

            record["fuel"] = get_val("Fuel")

            # Pressure bar -> atm
            p_raw = get_val("approx p (bar)")
            if p_raw:
                try:
                    p_val = float(p_raw)
                    record["pressure_atm"] = p_val / 1.01325
                    record["pressure_source_value"] = p_val
                    record["pressure_source_unit"] = "bar"
                except ValueError:
                    self._record_error(i, "approx p (bar)", p_raw, "Non-numeric pressure")
                    record["is_eligible"] = False
                    record["quality_flags"].append("invalid_pressure: non-numeric")
            else:
                self.stats["missing_pressure_count"] += 1

            # Oxygen
            if self.flame_type == "normal":
                # Requirement: XO2 before test as pre-test oxygen condition
                # The actual source header has a leading space: ` XO2 before test`
                # Our trimmed lookup handles this.
                o2_before = get_val("XO2 before test")
                if o2_before:
                    try:
                        val = float(o2_before)
                        record["o2_frac"] = val
                        if not (0 <= val <= 1):
                            record["quality_flags"].append("o2_out_of_range")
                    except ValueError:
                        self._record_error(i, "XO2 before test", o2_before, "Non-numeric O2")
                else:
                    self.stats["missing_o2_count"] += 1
                
                o2_after = get_val("XO2 after test")
                if o2_after:
                    try:
                        record["measurements"].append({
                            "variable": "o2_after_test",
                            "value": float(o2_after),
                            "unit": "fraction"
                        })
                    except ValueError:
                        pass
            else:
                # Inverse
                # Requirement 7: Treat CFI-G Inverse XO2 as burner-stream oxygen
                o2_raw = get_val("XO2")
                if o2_raw:
                    try:
                        val = float(o2_raw)
                        record["o2_frac"] = val
                        if not (0 <= val <= 1):
                            record["quality_flags"].append("o2_out_of_range")
                    except ValueError:
                        self._record_error(i, "XO2", o2_raw, "Non-numeric O2")
                else:
                    self.stats["missing_o2_count"] += 1

            # Outcome
            ign_raw = get_val("Hot ignition").lower()
            if ign_raw == "yes":
                record["outcome"] = "ignited"
            elif ign_raw == "no":
                record["outcome"] = "no_ignition"
            elif ign_raw == "flash":
                record["outcome"] = "unknown"
                record["outcome_detail"] = "flash"
            else:
                record["outcome"] = "unknown"
            
            self._add_outcome_stat(record["outcome"])

            # outcome_detail
            # Trimmed keys for both variants
            detail = get_val("Self-ext or flow term") or get_val("Self -ext or flow term")
            if detail and detail != "-":
                # Preserve existing flash if already set
                if "outcome_detail" in record:
                    if detail not in record["outcome_detail"]:
                        record["outcome_detail"] = f"{record['outcome_detail']}; {detail}"
                else:
                    record["outcome_detail"] = detail
            
            # Notes
            record["notes"] = get_val("Comments")

            # Measurements
            self._add_meas(record, "burn_time", get_val("burn time (sec)"), "s")
            self._add_meas(record, "adiabatic_flame_temperature", get_val("Tad (K)"), "K")
            self._add_meas(record, "stoichiometric_mixture_fraction", get_val("Zst"), "1")
            
            mhc_val = get_val("mHC (mg/s)") or get_val("HC burnrate (mg/s)")
            self._add_meas(record, "hydrocarbon_burn_rate", mhc_val, "mg/s")

            if self.flame_type == "inverse":
                self._add_meas(record, "oxygen_flow_rate", get_val("O2 flow rate (mg/s)"), "mg/s")

            # Peak burner temp
            pb_t_c = get_val("Peak burner T (C )") or get_val("Peak burner Temp (C)") or get_val("Peak burner temp (C)")
            pb_t_k = get_val("Peak burner temp (K)")
            
            t_k = None
            if pb_t_k:
                try:
                    t_k = float(pb_t_k)
                except ValueError:
                    pass
            
            if t_k is None and pb_t_c:
                try:
                    t_k = float(pb_t_c) + 273.15
                except ValueError:
                    pass
            
            if t_k is not None:
                record["measurements"].append({
                    "variable": "peak_burner_temperature",
                    "value": t_k,
                    "unit": "K"
                })

            if record["is_eligible"]:
                self.stats["eligible_records"] += 1

            self.results.append(record)
            self.stats["parsed_records"] += 1

        return self.results, self.stats

    def _add_meas(self, record, var, val, unit):
        if val and val.strip():
            try:
                record["measurements"].append({
                    "variable": var,
                    "value": float(val.strip()),
                    "unit": unit
                })
            except ValueError:
                pass
