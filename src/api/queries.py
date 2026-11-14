from sqlalchemy import text, func, and_, select, literal_column
from sqlalchemy.engine import Engine
from src.contracts import (
    ScenarioQuery,
    ScenarioResponse,
    Run,
    InverseFlameQuery,
    InverseFlameResponse,
    InverseFlameRun,
)
from src.constants import O2_SCALE, PRESSURE_SCALE

def get_scenario_data(engine: Engine, query: ScenarioQuery) -> ScenarioResponse:
    # distance = sqrt(((run.o2_frac - query.o2_frac) / O2_SCALE)^2 + ((run.pressure_atm - query.pressure_atm) / PRESSURE_SCALE)^2)
    # We use a subquery or a CTE to calculate distance so we can filter and sort by it efficiently.
    
    # `text()` is for SQL snippets and doesn't support arithmetic operators.
    # These identifiers are fixed by the query, so literal_column is appropriate.
    o2_column = literal_column("o2_frac")
    pressure_column = literal_column("pressure_atm")
    distance_expr = func.sqrt(
        func.pow((o2_column - query.o2_frac) / O2_SCALE, 2)
        + func.pow((pressure_column - query.pressure_atm) / PRESSURE_SCALE, 2)
    )

    sql = select(
        text("test_id"),
        text("psi_id"),
        text("fuel"),
        text("material"),
        text("run_label"),
        text("o2_frac"),
        text("pressure_atm"),
        text("outcome"),
        text("raw"),
        distance_expr.label("distance")
    ).select_from(text("psi_dashboard.runs")).where(
        and_(
            text("o2_frac IS NOT NULL"),
            text("pressure_atm IS NOT NULL"),
            text("pressure_atm > 0"),
            text("o2_is_ambient IS TRUE"),
            text("flame_config != 'gas_inverse'")
        )
    )

    if query.flame_config:
        sql = sql.where(text("flame_config = :fc")).params(fc=query.flame_config)

    sql = sql.order_by(text("distance"))

    with engine.connect() as conn:
        results = conn.execute(sql)
        all_runs = []
        for row in results:
            # Map fuel, falling back to material, then run_label, to label.
            label = row.fuel or row.material or row.run_label or "Unknown"
            raw = row.raw if isinstance(row.raw, dict) else {}
            normalization = raw.get("_normalization", {})
            pressure_info = normalization.get("pressure", {}) if isinstance(normalization, dict) else {}
            pressure_source = pressure_info.get("source") if isinstance(pressure_info, dict) else None
            if pressure_source not in {"measured", "assumed", "unknown"}:
                pressure_source = (
                    "measured"
                    if any(key in raw for key in (
                        "Ambient pressure; mmHg", "approx p (bar)", "pressure, atm"
                    ))
                    else "unknown"
                )
            all_runs.append(Run(
                run_id=row.test_id,
                psi_id=row.psi_id,
                label=label,
                o2_frac=row.o2_frac,
                pressure_atm=row.pressure_atm,
                pressure_source=pressure_source,
                outcome=row.outcome,
                distance=row.distance
            ))

    n_nearby = sum(1 for r in all_runs if r.distance <= 1)
    return ScenarioResponse(
        in_envelope=n_nearby > 0,
        n_nearby=n_nearby,
        closest=all_runs[:5],
        points=all_runs
    )

def get_inverse_flames_data(engine: Engine, query: InverseFlameQuery) -> InverseFlameResponse:
    # Select only flame_config = 'gas_inverse' and o2_is_ambient IS FALSE.
    base_where = and_(
        text("flame_config = 'gas_inverse'"),
        text("o2_is_ambient IS FALSE")
    )
    
    filters = []
    params = {}
    
    if query.burner_o2_min is not None:
        filters.append(text("o2_frac >= :o2_min"))
        params["o2_min"] = query.burner_o2_min
    if query.burner_o2_max is not None:
        filters.append(text("o2_frac <= :o2_max"))
        params["o2_max"] = query.burner_o2_max
    if query.pressure_min_atm is not None:
        filters.append(text("pressure_atm >= :p_min"))
        params["p_min"] = query.pressure_min_atm
    if query.pressure_max_atm is not None:
        filters.append(text("pressure_atm <= :p_max"))
        params["p_max"] = query.pressure_max_atm
    if query.fuel:
        filters.append(func.lower(text("fuel")) == func.lower(query.fuel))
    if query.outcome:
        filters.append(text("outcome = :outcome"))
        params["outcome"] = query.outcome

    count_sql = select(func.count()).select_from(text("psi_dashboard.runs")).where(and_(base_where, *filters)).params(**params)
    
    data_sql = select(
        text("test_id"),
        text("psi_id"),
        text("fuel"),
        text("material"),
        text("run_label"),
        text("o2_frac"), # interpreted as burner_o2_frac
        text("pressure_atm"),
        text("outcome"),
        text("raw")
    ).select_from(text("psi_dashboard.runs")).where(and_(base_where, *filters)).params(**params)
    
    # Stable ordering is required so offset pagination doesn't reshuffle rows.
    data_sql = data_sql.order_by(text("test_id")).limit(query.limit).offset(query.offset)

    with engine.connect() as conn:
        total = conn.scalar(count_sql)
        results = conn.execute(data_sql)
        runs = []
        for row in results:
            label = row.fuel or row.material or row.run_label or "Unknown"
            
            # outcome_detail logic
            # If raw ->> 'Hot ignition' is flash (case-insensitive), return flash.
            # Otherwise read the self-extinction/flow value from the available source key,
            # including Self -ext or flow term and Self-ext or flow term; trim whitespace and return null for blank or -.
            raw = row.raw or {}
            hot_ignition = raw.get("Hot ignition", "")
            outcome_detail = None
            if isinstance(hot_ignition, str) and hot_ignition.strip().lower() == "flash":
                outcome_detail = "flash"
            else:
                # Try both variants of the key
                v1 = raw.get("Self -ext or flow term")
                v2 = raw.get("Self-ext or flow term")
                val = v1 if v1 is not None else v2
                if val is not None:
                    val = str(val).strip()
                    if val and val != "-":
                        outcome_detail = val
            
            runs.append(InverseFlameRun(
                run_id=row.test_id,
                psi_id=row.psi_id,
                label=label,
                burner_o2_frac=row.o2_frac,
                pressure_atm=row.pressure_atm,
                outcome=row.outcome,
                outcome_detail=outcome_detail
            ))
            
    return InverseFlameResponse(total=total, runs=runs)
