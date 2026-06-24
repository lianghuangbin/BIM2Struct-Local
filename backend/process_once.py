import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import BIM2Struct_IFC2tables as b  # noqa: E402


def json_value(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def enrich_member(item, node_keys):
    out = {}
    for key, value in item.items():
        if key in node_keys:
            out[key] = int(value)
        elif key == "eleTag":
            out["id"] = int(value)
        elif key == "GlobalId":
            out["globalId"] = json_value(value)
        elif key == "Name":
            out["name"] = json_value(value)
        else:
            out[key] = json_value(value)
    return out


def dominant_direction(mode_shape):
    totals = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    for shape in mode_shape:
        totals["X"] += abs(shape["dx"])
        totals["Y"] += abs(shape["dy"])
        totals["Z"] += abs(shape["dz"])
    return max(totals, key=totals.get)


DEFAULT_GRAVITY_LOADS = {
    "q_dead": 5.0,
    "q_live": 2.5,
    "psi": 0.3,
}

DEFAULT_LOAD_CASES = {
    "service_gravity": {
        "id": "service_gravity",
        "label": "Service gravity: 1.0D + 1.0L",
        "dead_factor": 1.0,
        "live_factor": 1.0,
    },
    "uls_gravity": {
        "id": "uls_gravity",
        "label": "ULS gravity: 1.35D + 1.5L",
        "dead_factor": 1.35,
        "live_factor": 1.5,
    },
}

DEFAULT_MODEL_OPTIONS = {
    "expansion_joint_springs": True,
    "expansion_joint_stiffness": 9.0e3,
}

DEFAULT_SEISMIC_INPUTS = {
    "direction": "X",
    "damping": 0.05,
    "target_pga_g": 0.2,
    "dt": 0.01,
    "analysis_dt": None,
    "max_subdivisions": 5,
    "collapse_idr": 0.04,
    "acceleration_unit": "g",
}


def normalized_gravity_loads(gravity_loads=None):
    gravity_loads = gravity_loads or {}
    load_case_id = gravity_loads.get("load_case", "service_gravity")
    load_case = DEFAULT_LOAD_CASES.get(load_case_id, DEFAULT_LOAD_CASES["service_gravity"])
    return {
        "q_dead": float(gravity_loads.get("q_dead", DEFAULT_GRAVITY_LOADS["q_dead"])),
        "q_live": float(gravity_loads.get("q_live", DEFAULT_GRAVITY_LOADS["q_live"])),
        "psi": float(gravity_loads.get("psi", DEFAULT_GRAVITY_LOADS["psi"])),
        "load_case": load_case["id"],
        "load_case_label": load_case["label"],
        "dead_factor": float(gravity_loads.get("dead_factor", load_case["dead_factor"])),
        "live_factor": float(gravity_loads.get("live_factor", load_case["live_factor"])),
    }


def normalized_model_options(model_options=None):
    model_options = model_options or {}
    return {
        "expansion_joint_springs": bool(
            model_options.get(
                "expansion_joint_springs",
                DEFAULT_MODEL_OPTIONS["expansion_joint_springs"],
            )
        ),
        "expansion_joint_stiffness": float(
            model_options.get(
                "expansion_joint_stiffness",
                DEFAULT_MODEL_OPTIONS["expansion_joint_stiffness"],
            )
        ),
    }


def normalized_seismic_inputs(seismic=None, peak_input=None):
    seismic = seismic or {}
    direction_label = str(seismic.get("direction", DEFAULT_SEISMIC_INPUTS["direction"])).upper()
    direction = 1 if direction_label == "X" else 2
    acceleration_unit = str(
        seismic.get("acceleration_unit", DEFAULT_SEISMIC_INPUTS["acceleration_unit"])
    ).lower()
    target_pga_g = float(
        seismic.get(
            "target_pga_g",
            seismic.get("intensity", DEFAULT_SEISMIC_INPUTS["target_pga_g"]),
        )
    )
    peak_input = float(peak_input) if peak_input is not None else None
    if acceleration_unit == "g":
        if peak_input is None or peak_input <= 0:
            record_scale = 1.0
        else:
            record_scale = target_pga_g / peak_input
        scale_factor = record_scale * 9810.0
    else:
        record_scale = 1.0
        scale_factor = 1.0
    analysis_dt = seismic.get("analysis_dt", DEFAULT_SEISMIC_INPUTS["analysis_dt"])
    return {
        "direction": direction_label if direction_label in {"X", "Y"} else "Y",
        "direction_dof": direction,
        "damping": float(seismic.get("damping", DEFAULT_SEISMIC_INPUTS["damping"])),
        "target_pga_g": target_pga_g,
        "record_peak_input": peak_input,
        "record_scale": float(record_scale),
        "dt": float(seismic.get("dt", DEFAULT_SEISMIC_INPUTS["dt"])),
        "analysis_dt": float(analysis_dt) if analysis_dt not in (None, "") else None,
        "max_subdivisions": int(seismic.get("max_subdivisions", DEFAULT_SEISMIC_INPUTS["max_subdivisions"])),
        "collapse_idr": float(seismic.get("collapse_idr", DEFAULT_SEISMIC_INPUTS["collapse_idr"])),
        "acceleration_unit": acceleration_unit,
        "scale_factor": float(scale_factor),
    }


def gravity_summary_from_inputs(node_table, x_coords, y_coords, z_coords, gravity_loads):
    q_dead = float(gravity_loads["q_dead"])
    q_live = float(gravity_loads["q_live"])
    psi = float(gravity_loads["psi"])
    dead_factor = float(gravity_loads["dead_factor"])
    live_factor = float(gravity_loads["live_factor"])
    floor_count = max(len(z_coords) - 1, 0)
    lx_total = float(np.max(x_coords) - np.min(x_coords))
    ly_total = float(np.max(y_coords) - np.min(y_coords))
    floor_area_m2 = (lx_total / 1000.0) * (ly_total / 1000.0)
    q_gravity = dead_factor * q_dead + live_factor * q_live
    q_seismic = q_dead + psi * q_live
    gravity_floor_kn = q_gravity * floor_area_m2
    seismic_floor_kn = q_seismic * floor_area_m2
    story_nodes = b.get_story_nodes_from_table(node_table, z_coords)
    floors = []

    for k in range(1, len(z_coords)):
        nodes_k = story_nodes.get(k, [])
        floors.append({
            "level": int(k),
            "z": float(z_coords[k]),
            "node_count": len(nodes_k),
            "gravity_load_kN": float(gravity_floor_kn),
            "seismic_weight_kN": float(seismic_floor_kn),
            "nodal_load_kN": float(-gravity_floor_kn / len(nodes_k)) if nodes_k else 0.0,
        })

    return {
        "parameters": {
            "q_dead_kN_m2": q_dead,
            "q_live_kN_m2": q_live,
            "psi": psi,
            "load_case": gravity_loads["load_case"],
            "load_case_label": gravity_loads["load_case_label"],
            "dead_factor": dead_factor,
            "live_factor": live_factor,
        },
        "method": (
            "Gravity load is currently treated as an equivalent uniformly distributed floor load. "
            "For each above-ground floor, the selected load combination is multiplied by the plan area from the IFC grid extents, "
            "then distributed equally as vertical nodal loads to the structural nodes on that floor. "
            "The seismic/modal mass uses q_dead + psi*q_live at diaphragm master nodes."
        ),
        "floor_count": floor_count,
        "floor_area_m2": float(floor_area_m2),
        "q_gravity_kN_m2": float(q_gravity),
        "q_seismic_kN_m2": float(q_seismic),
        "gravity_load_per_floor_kN": float(gravity_floor_kn),
        "seismic_weight_per_floor_kN": float(seismic_floor_kn),
        "total_gravity_load_kN": float(gravity_floor_kn * floor_count),
        "total_seismic_weight_kN": float(seismic_floor_kn * floor_count),
        "floors": floors,
    }


def safe_ele_response(ele_tag, response_name):
    try:
        value = b.ops.eleResponse(int(ele_tag), response_name)
    except Exception:
        return []
    if value is None:
        return []
    return [float(v) for v in value]


def get_float(item, key, default):
    try:
        return float(item.get(key, default))
    except Exception:
        return float(default)


def preliminary_capacity(item, member_type):
    fc = max(get_float(item, "fc", 28.0), 1.0)
    fy = max(get_float(item, "fy", 300.0), 1.0)
    b_dim = max(get_float(item, "b", 400.0), 1.0)
    h_dim = max(get_float(item, "h", 400.0), 1.0)
    rho = max(get_float(item, "rho", 0.01), 0.0)

    if member_type == "wall":
        thickness = max(get_float(item, "t", b_dim), 1.0)
        length = max(get_float(item, "wallLength", h_dim), 1.0)
        area = thickness * length
        axial_capacity = 0.30 * fc * area
        shear_capacity = 0.17 * (fc ** 0.5) * area
        moment_capacity = 0.10 * fc * thickness * length**2
    elif member_type == "beam":
        area = max(get_float(item, "A", b_dim * h_dim), 1.0)
        depth = h_dim
        effective_depth = max(depth - 40.0, 0.75 * depth)
        steel_area = max(rho, 0.01) * area
        axial_capacity = 0.30 * fc * area
        shear_capacity = 0.17 * (fc ** 0.5) * b_dim * effective_depth
        moment_capacity = 0.90 * steel_area * fy * effective_depth
    else:
        area = b_dim * h_dim
        steel_area = rho * area
        axial_capacity = 0.35 * fc * area + fy * steel_area
        shear_capacity = 0.17 * (fc ** 0.5) * b_dim * h_dim
        moment_capacity = 0.12 * fc * b_dim * h_dim**2 + 0.50 * steel_area * fy * h_dim

    return {
        "method": "preliminary_simplified_capacity",
        "axial_capacity_N": float(max(axial_capacity, 1.0)),
        "shear_capacity_N": float(max(shear_capacity, 1.0)),
        "moment_capacity_Nmm": float(max(moment_capacity, 1.0)),
    }


def member_force_summary(item, member_type):
    ele_tag = int(item["eleTag"])
    local = safe_ele_response(ele_tag, "localForce")
    basic = safe_ele_response(ele_tag, "basicForce")
    force = local or safe_ele_response(ele_tag, "globalForce") or safe_ele_response(ele_tag, "force")

    if len(local) >= 12:
        axial = max(abs(local[0]), abs(local[6]))
        shear = max(abs(local[1]), abs(local[2]), abs(local[7]), abs(local[8]))
        moment = max(abs(local[3]), abs(local[4]), abs(local[5]), abs(local[9]), abs(local[10]), abs(local[11]))
    elif len(force) >= 12:
        axial = max(abs(force[0]), abs(force[6]))
        shear = max(abs(force[1]), abs(force[2]), abs(force[7]), abs(force[8]))
        moment = max(abs(force[3]), abs(force[4]), abs(force[5]), abs(force[9]), abs(force[10]), abs(force[11]))
    elif basic:
        axial = abs(basic[0])
        shear = 0.0
        moment = max(abs(v) for v in basic[1:]) if len(basic) > 1 else 0.0
    else:
        axial = 0.0
        shear = 0.0
        moment = 0.0

    capacity = preliminary_capacity(item, member_type)
    axial_dcr = axial / capacity["axial_capacity_N"]
    shear_dcr = shear / capacity["shear_capacity_N"]
    moment_dcr = moment / capacity["moment_capacity_Nmm"]

    return {
        "id": ele_tag,
        "type": member_type,
        "axial_N": float(axial),
        "shear_N": float(shear),
        "moment_Nmm": float(moment),
        **capacity,
        "axial_dcr": float(axial_dcr),
        "shear_dcr": float(shear_dcr),
        "moment_dcr": float(moment_dcr),
        "dcr_max": float(max(axial_dcr, shear_dcr, moment_dcr)),
        "response_available": bool(force or basic),
    }


def collect_gravity_member_demands(column_table, beam_table, wall_table):
    columns = [member_force_summary(item, "column") for item in column_table]
    beams = [member_force_summary(item, "beam") for item in beam_table]
    walls = [member_force_summary(item, "wall") for item in wall_table]
    members = columns + beams + walls

    maxima = {
        "axial_N": max((item["axial_N"] for item in members), default=0.0),
        "shear_N": max((item["shear_N"] for item in members), default=0.0),
        "moment_Nmm": max((item["moment_Nmm"] for item in members), default=0.0),
    }

    for item in members:
        for key, max_value in maxima.items():
            norm_key = key.replace("_Nmm", "").replace("_N", "") + "_ratio"
            item[norm_key] = float(item[key] / max_value) if max_value > 0 else 0.0

    critical = max(members, key=lambda item: item["dcr_max"], default=None)

    return {
        "method": (
            "Member demand values are extracted from OpenSees element responses after gravity analysis. "
            "They are intended for relative visualization, not demand-capacity safety checks."
        ),
        "capacity_method": (
            "Preliminary simplified capacity estimates from section dimensions and material parameters. "
            "Use for screening only; not a code-compliant final design check."
        ),
        "maxima": maxima,
        "dcr_summary": {
            "max_dcr": float(critical["dcr_max"]) if critical else 0.0,
            "critical_member_id": int(critical["id"]) if critical else None,
            "critical_member_type": critical["type"] if critical else None,
            "members_over_1_0": sum(1 for item in members if item["dcr_max"] > 1.0),
            "members_over_0_8": sum(1 for item in members if item["dcr_max"] > 0.8),
        },
        "columns": columns,
        "beams": beams,
        "walls": walls,
    }


def run_gravity_from_tables(node_table, column_table, beam_table, wall_table, x_coords, y_coords, z_coords, gravity_loads, model_options):
    summary = gravity_summary_from_inputs(node_table, x_coords, y_coords, z_coords, gravity_loads)
    model_info = b.build_opensees_model(
        node_table=node_table,
        column_table=column_table,
        beam_table=beam_table,
        wall_table=wall_table,
        xCoords=x_coords,
        yCoords=y_coords,
        zCoords=z_coords,
        add_mass=True,
        add_expansion_joint_springs=model_options["expansion_joint_springs"],
        expansion_joint_stiffness=model_options["expansion_joint_stiffness"],
        plot_model=False,
        gravity_loads=gravity_loads,
    )
    ok = b.run_gravity_analysis(n_steps=10)

    max_uz = 0.0
    max_horizontal = 0.0
    story_nodes = b.get_story_nodes_from_table(node_table, z_coords)
    floor_results = []
    for floor in summary["floors"]:
        nodes_k = story_nodes.get(floor["level"], [])
        uz_values = []
        horizontal_values = []
        for node_id in nodes_k:
            try:
                disp = b.ops.nodeDisp(int(node_id))
                ux, uy, uz = float(disp[0]), float(disp[1]), float(disp[2])
            except Exception:
                ux, uy, uz = 0.0, 0.0, 0.0
            uz_values.append(uz)
            horizontal_values.append((ux**2 + uy**2) ** 0.5)

        if uz_values:
            max_uz = max(max_uz, max(abs(v) for v in uz_values))
            max_horizontal = max(max_horizontal, max(horizontal_values))
            floor_results.append({
                **floor,
                "mean_uz_mm": float(np.mean(uz_values)),
                "min_uz_mm": float(np.min(uz_values)),
                "max_uz_mm": float(np.max(uz_values)),
                "max_horizontal_disp_mm": float(np.max(horizontal_values)),
            })
        else:
            floor_results.append({
                **floor,
                "mean_uz_mm": 0.0,
                "min_uz_mm": 0.0,
                "max_uz_mm": 0.0,
                "max_horizontal_disp_mm": 0.0,
            })

    summary.update({
        "status": "completed" if ok == 0 else "failed",
        "ok": int(ok),
        "max_abs_vertical_disp_mm": float(max_uz),
        "max_horizontal_disp_mm": float(max_horizontal),
        "floors": floor_results,
        "diaphragmMasters": {str(k): int(v) for k, v in model_info["diaphragmMaster"].items()},
        "model_options": {
            **model_options,
            "expansion_joint_spring_count": int(model_info["expansion_joint"]["spring_count"]),
        },
        "member_demands": collect_gravity_member_demands(column_table, beam_table, wall_table),
    })
    return summary


def collect_modal_from_current_model(node_table, diaphragm_masters, num_modes=6):
    _, modal_results = b.run_modal_analysis(numModes=num_modes)

    modes = []
    for mode_id, result in modal_results.items():
        raw_shape = []
        max_abs = 0.0
        for node in node_table:
            node_id = int(node["nodeTag"])
            try:
                vec = b.ops.nodeEigenvector(node_id, int(mode_id))[0:3]
                dx, dy, dz = float(vec[0]), float(vec[1]), float(vec[2])
            except Exception:
                dx, dy, dz = 0.0, 0.0, 0.0
            max_abs = max(max_abs, abs(dx), abs(dy), abs(dz))
            raw_shape.append({"node": node_id, "dx": dx, "dy": dy, "dz": dz})

        if max_abs > 0:
            for shape in raw_shape:
                shape["dx"] /= max_abs
                shape["dy"] /= max_abs
                shape["dz"] /= max_abs

        modes.append({
            "mode": int(mode_id),
            "lambda": float(result["lambda"]),
            "omega": float(result["omega"]),
            "frequency": float(result["freq"]),
            "period": float(result["T"]),
            "direction": dominant_direction(raw_shape),
            "shape": raw_shape,
        })

    return {
        "modes": modes,
        "diaphragmMasters": {str(k): int(v) for k, v in diaphragm_masters.items()},
    }


def run_modal_from_tables(node_table, column_table, beam_table, wall_table, x_coords, y_coords, z_coords, num_modes=6, gravity_loads=None, model_options=None):
    gravity_loads = normalized_gravity_loads(gravity_loads)
    model_options = normalized_model_options(model_options)
    model_info = b.build_opensees_model(
        node_table=node_table,
        column_table=column_table,
        beam_table=beam_table,
        wall_table=wall_table,
        xCoords=x_coords,
        yCoords=y_coords,
        zCoords=z_coords,
        add_mass=True,
        add_expansion_joint_springs=model_options["expansion_joint_springs"],
        expansion_joint_stiffness=model_options["expansion_joint_stiffness"],
        plot_model=False,
        gravity_loads=gravity_loads,
    )
    return collect_modal_from_current_model(node_table, model_info["diaphragmMaster"], num_modes)


def process_ifc_counts(ifc_path, run_modal=False, num_modes=6, run_gravity=True, gravity_loads=None, model_options=None):
    gravity_loads = normalized_gravity_loads(gravity_loads)
    model_options = normalized_model_options(model_options)
    ifc_path = Path(ifc_path)
    if not ifc_path.exists():
        raise FileNotFoundError(f"IFC file not found: {ifc_path}")

    ifc_file = b.ifcopenshell.open(str(ifc_path))

    x_coords_raw, y_coords_raw = b.get_grid_xy_compatible(ifc_file)
    z_coords_raw = b.get_storey_elevations_compatible(ifc_file, use_world=True)

    unit_scale = b.get_length_unit_scale_to_mm(ifc_file)
    x_coords_raw = b.clean_sorted_coords(x_coords_raw) * unit_scale
    y_coords_raw = b.clean_sorted_coords(y_coords_raw) * unit_scale
    z_coords_raw = b.clean_sorted_coords(z_coords_raw) * unit_scale

    if len(x_coords_raw) == 0 or len(y_coords_raw) == 0 or len(z_coords_raw) == 0:
        raise ValueError("Could not extract grid coordinates or storey elevations from IFC.")

    b.x0 = float(np.min(x_coords_raw))
    b.y0 = float(np.min(y_coords_raw))

    x_coords = x_coords_raw - b.x0
    y_coords = y_coords_raw - b.y0
    z_coords = np.array([z for z in z_coords_raw if z >= -1e-6], dtype=float)

    candidate_node_xyz, _ = b.build_candidate_node_pool(x_coords, y_coords, z_coords)

    columns = ifc_file.by_type("IfcColumn")
    beams = ifc_file.by_type("IfcBeam")
    walls = ifc_file.by_type("IfcWall")

    column_table, column_nodes, skipped_columns = b.build_column_table(
        columns,
        candidate_node_xyz,
        snap_tol=1000.0,
    )

    beam_table, beam_nodes, skipped_beams = b.build_beam_table(
        beams,
        candidate_node_xyz,
        start_eleTag=len(column_table) + 1,
        snap_tol=800,
    )

    wall_table, wall_nodes, skipped_walls = b.build_wall_table(
        walls,
        candidate_node_xyz,
        start_eleTag=len(column_table) + len(beam_table) + 1,
        snap_tol=1200,
    )

    used_nodes = set()
    used_nodes.update(column_nodes)
    used_nodes.update(beam_nodes)
    used_nodes.update(wall_nodes)

    node_table = []
    node_table_internal = []
    for tag in sorted(used_nodes):
        p = candidate_node_xyz[tag]
        node_table.append({
            "id": int(tag),
            "x": float(p[0]),
            "y": float(p[1]),
            "z": float(p[2]),
            "is_fixed": bool(abs(p[2]) < 1e-6),
        })
        node_table_internal.append({
            "nodeTag": int(tag),
            "x": float(p[0]),
            "y": float(p[1]),
            "z": float(p[2]),
            "is_fixed": bool(abs(p[2]) < 1e-6),
        })

    column_view = [enrich_member(item, {"nodeI", "nodeJ"}) for item in column_table]
    beam_view = [enrich_member(item, {"nodeI", "nodeJ"}) for item in beam_table]
    wall_view = [enrich_member(item, {"node1", "node2", "node3", "node4"}) for item in wall_table]

    result = {
        "file": str(ifc_path),
        "counts": {
            "nodes": len(node_table),
            "columns": len(column_table),
            "beams": len(beam_table),
            "walls": len(wall_table),
            "skipped_columns": len(skipped_columns),
            "skipped_beams": len(skipped_beams),
            "skipped_walls": len(skipped_walls),
        },
        "raw": {
            "columns": len(columns),
            "beams": len(beams),
            "walls": len(walls),
        },
        "grid": {
            "x": len(x_coords),
            "y": len(y_coords),
            "z": len(z_coords),
            "candidate_nodes": len(candidate_node_xyz),
            "unit_scale_to_mm": unit_scale,
        },
        "tables": {
            "nodes": node_table,
            "columns": column_view,
            "beams": beam_view,
            "walls": wall_view,
        },
        "skipped": {
            "columns": skipped_columns,
            "beams": skipped_beams,
            "walls": skipped_walls,
        },
        "model_options": model_options,
    }

    if run_gravity:
        with contextlib.redirect_stdout(io.StringIO()):
            result["gravity"] = run_gravity_from_tables(
                node_table=node_table_internal,
                column_table=column_table,
                beam_table=beam_table,
                wall_table=wall_table,
                x_coords=x_coords,
                y_coords=y_coords,
                z_coords=z_coords,
                gravity_loads=gravity_loads,
                model_options=model_options,
            )

    if run_modal:
        with contextlib.redirect_stdout(io.StringIO()):
            if run_gravity and "gravity" in result:
                result["modal"] = collect_modal_from_current_model(
                    node_table=node_table_internal,
                    diaphragm_masters=result["gravity"]["diaphragmMasters"],
                    num_modes=num_modes,
                )
            else:
                result["modal"] = run_modal_from_tables(
                    node_table=node_table_internal,
                    column_table=column_table,
                    beam_table=beam_table,
                    wall_table=wall_table,
                    x_coords=x_coords,
                    y_coords=y_coords,
                    z_coords=z_coords,
                    num_modes=num_modes,
                    gravity_loads=gravity_loads,
                    model_options=model_options,
                )

    return result


def _sample_series(time, values, max_points=600):
    time = np.asarray(time, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(time) == 0 or len(values) == 0:
        return []
    step = max(int(np.ceil(len(time) / max_points)), 1)
    return [
        {"time": float(t), "value": float(v)}
        for t, v in zip(time[::step], values[::step])
    ]


def _sample_story_idr_series(time, idr, max_points=600):
    if len(time) == 0 or not idr:
        return []

    step = max(int(np.ceil(len(time) / max_points)), 1)
    stories = []
    for story in sorted(idr.keys()):
        values = np.asarray(idr[story], dtype=float) * 100.0
        stories.append({
            "story": int(story),
            "series": [
                {"time": float(t), "value": float(v)}
                for t, v in zip(time[::step], values[::step])
            ],
        })
    return stories


def _sample_animation_frames(master_disp_file, diaphragm_masters, z_coords, idr=None, max_frames=240):
    if not Path(master_disp_file).exists():
        return {"floors": [], "frames": []}

    time, disp, floors = b.load_master_disp(master_disp_file, diaphragm_masters)
    if len(time) == 0:
        return {"floors": [], "frames": []}

    step = max(int(np.ceil(len(time) / max_frames)), 1)
    floor_info = [
        {
            "story": int(k),
            "height_m": float(z_coords[k] / 1000.0) if k < len(z_coords) else float(k),
            "master_node": int(diaphragm_masters[k]),
        }
        for k in floors
    ]
    frames = []
    idr = idr or {}
    idr_floors = sorted(idr.keys())
    for row_index in range(0, len(time), step):
        frame_idrs = {
            k: abs(float(idr[k][row_index]))
            for k in idr_floors
            if row_index < len(idr[k])
        }
        critical_story = max(frame_idrs, key=frame_idrs.get) if frame_idrs else None
        max_idr = frame_idrs[critical_story] if critical_story is not None else 0.0
        frames.append({
            "time": float(time[row_index]),
            "floor_displacements_mm": {
                str(k): float(disp[row_index, col_index])
                for col_index, k in enumerate(floors)
            },
            "max_idr": float(max_idr),
            "max_idr_percent": float(max_idr * 100.0),
            "critical_story": int(critical_story) if critical_story is not None else None,
        })

    return {
        "floors": floor_info,
        "frames": frames,
    }


def _peak_displacement_profile(master_disp_file, diaphragm_masters, z_coords):
    if not Path(master_disp_file).exists():
        return []

    _, disp, floors = b.load_master_disp(master_disp_file, diaphragm_masters)
    if disp.size == 0:
        return []

    return [
        {
            "story": int(k),
            "height_m": float(z_coords[k] / 1000.0) if k < len(z_coords) else float(k),
            "peak_disp_mm": float(np.max(np.abs(disp[:, col_index]))),
        }
        for col_index, k in enumerate(floors)
    ]


def _modal_dict_from_view(modal):
    modal = modal or {}
    out = {}
    for item in modal.get("modes", []):
        mode_id = int(item["mode"])
        out[mode_id] = {
            "lambda": float(item["lambda"]),
            "omega": float(item["omega"]),
            "freq": float(item["frequency"]),
            "T": float(item["period"]),
        }
    return out


def _read_two_column_recorder(path):
    if not Path(path).exists():
        return np.array([], dtype=float), np.array([], dtype=float)
    data = np.loadtxt(path)
    if data.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data[:, 0], data[:, 1]


def _parse_failed_element(log_text):
    matches = re.findall(r"element:\s*(\d+)", log_text or "", flags=re.IGNORECASE)
    return int(matches[-1]) if matches else None


def process_ifc_seismic(
    ifc_path,
    gm_path,
    seismic=None,
    gravity_loads=None,
    model_options=None,
    num_modes=6,
    output_prefix=None,
):
    gm_values = np.loadtxt(gm_path, dtype=float)
    gm_values = np.atleast_1d(gm_values)
    n_steps = int(len(gm_values))
    if n_steps <= 0:
        raise ValueError("Ground motion file is empty.")
    peak_input = float(np.max(np.abs(gm_values)))
    seismic = normalized_seismic_inputs(seismic, peak_input=peak_input)

    if output_prefix is None:
        output_prefix = str(Path(gm_path).with_suffix(""))

    processed = process_ifc_counts(
        ifc_path,
        run_modal=True,
        num_modes=num_modes,
        run_gravity=True,
        gravity_loads=gravity_loads,
        model_options=model_options,
    )

    modal_dict = _modal_dict_from_view(processed.get("modal"))
    if len(modal_dict) >= 2:
        mode_j = 3 if 3 in modal_dict else sorted(modal_dict.keys())[-1]
        b.apply_rayleigh_damping_from_modes(
            modal_dict,
            zeta=seismic["damping"],
            mode_i=1,
            mode_j=mode_j,
        )

    diaphragm_masters = {
        int(k): int(v)
        for k, v in processed["gravity"]["diaphragmMasters"].items()
    }
    z_coords = np.array(
        sorted({float(node["z"]) for node in processed["tables"]["nodes"]}),
        dtype=float,
    )

    analysis_log = io.StringIO()
    with contextlib.redirect_stdout(analysis_log), contextlib.redirect_stderr(analysis_log):
        analysis_details = b.run_time_history(
            gm_file=str(gm_path),
            dt=seismic["dt"],
            n_steps=n_steps,
            diaphragmMaster=diaphragm_masters,
            direction=seismic["direction_dof"],
            scale_factor=seismic["scale_factor"],
            analysis_dt=seismic["analysis_dt"],
            output_prefix=str(output_prefix),
            max_subdivisions=seismic["max_subdivisions"],
            return_details=True,
    )
    ok = int(analysis_details["ok"])
    log_text = analysis_log.getvalue()

    try:
        b.ops.remove("recorders")
    except Exception:
        pass

    master_disp_file = f"{output_prefix}_master_disp.out"
    roof_disp_file = f"{output_prefix}_roof_disp.out"
    time, roof_disp = _read_two_column_recorder(roof_disp_file)
    if Path(master_disp_file).exists():
        _, idr = b.compute_idr_from_master_disp(master_disp_file, diaphragm_masters, z_coords)
    else:
        idr = {}
    animation = _sample_animation_frames(master_disp_file, diaphragm_masters, z_coords, idr=idr)
    peak_disp_profile = _peak_displacement_profile(master_disp_file, diaphragm_masters, z_coords)
    floors = sorted(idr.keys())
    idr_matrix = np.column_stack([idr[k] for k in floors]) if floors else np.zeros((len(time), 0))
    max_abs_idr_t = np.max(np.abs(idr_matrix), axis=1) if idr_matrix.size else np.zeros(len(time))
    peak_idr = {
        str(k): float(np.max(np.abs(idr[k])))
        for k in floors
    }
    peak_idr_profile = [
        {
            "story": int(k),
            "height_m": float(z_coords[k] / 1000.0) if k < len(z_coords) else float(k),
            "peak_idr": peak_idr[str(k)],
            "peak_idr_percent": float(peak_idr[str(k)] * 100.0),
        }
        for k in floors
    ]
    global_peak_idr = max(peak_idr.values(), default=0.0)
    critical_story = max(peak_idr, key=peak_idr.get) if peak_idr else None
    collapsed = global_peak_idr >= seismic["collapse_idr"]

    return {
        "status": "completed" if ok == 0 else "failed",
        "ok": int(ok),
        "parameters": seismic,
        "diagnostics": {
            **analysis_details,
            "failed_element": _parse_failed_element(log_text),
            "log_tail": log_text[-3000:],
        },
        "ground_motion": {
            "points": n_steps,
            "duration_s": float((n_steps - 1) * seismic["dt"]) if n_steps else 0.0,
            "peak_input": peak_input,
            "target_pga_g": seismic["target_pga_g"],
            "record_scale": seismic["record_scale"],
        },
        "summary": {
            "peak_roof_disp_mm": float(np.max(np.abs(roof_disp))) if len(roof_disp) else 0.0,
            "peak_idr": float(global_peak_idr),
            "peak_idr_percent": float(global_peak_idr * 100.0),
            "critical_story": int(critical_story) if critical_story is not None else None,
            "collapse_idr": seismic["collapse_idr"],
            "collapsed": bool(collapsed),
        },
        "peak_idr_by_story": peak_idr,
        "peak_idr_profile": peak_idr_profile,
        "peak_displacement_profile": peak_disp_profile,
        "series": {
            "roof_disp": _sample_series(time, roof_disp),
            "max_idr": _sample_series(time, max_abs_idr_t * 100.0),
            "story_idr": _sample_story_idr_series(time, idr),
        },
        "animation": {
            "direction": seismic["direction"],
            "scale_hint": 20.0,
            **animation,
        },
        "processed_counts": processed.get("counts"),
    }


def main():
    parser = argparse.ArgumentParser(description="Run BIM2Struct IFC processing once and print counts as JSON.")
    parser.add_argument("ifc_path", help="Path to the IFC file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--modal", action="store_true", help="Also run OpenSees modal analysis")
    parser.add_argument("--num-modes", type=int, default=6, help="Number of modes for modal analysis")
    parser.add_argument("--no-gravity", action="store_true", help="Skip OpenSees gravity analysis")
    parser.add_argument("--q-dead", type=float, default=DEFAULT_GRAVITY_LOADS["q_dead"], help="Dead load in kN/m2")
    parser.add_argument("--q-live", type=float, default=DEFAULT_GRAVITY_LOADS["q_live"], help="Live load in kN/m2")
    parser.add_argument("--psi", type=float, default=DEFAULT_GRAVITY_LOADS["psi"], help="Live-load combination factor for seismic/modal mass")
    parser.add_argument("--load-case", choices=sorted(DEFAULT_LOAD_CASES), default="service_gravity", help="Gravity load case or load combination")
    parser.add_argument("--no-expansion-joint-springs", action="store_true", help="Disable X-direction expansion-joint boundary springs")
    parser.add_argument("--expansion-joint-stiffness", type=float, default=DEFAULT_MODEL_OPTIONS["expansion_joint_stiffness"], help="Total X spring stiffness per side per floor in N/mm")
    args = parser.parse_args()

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = process_ifc_counts(
                args.ifc_path,
                run_modal=args.modal,
                num_modes=args.num_modes,
                run_gravity=not args.no_gravity,
                gravity_loads={
                    "q_dead": args.q_dead,
                    "q_live": args.q_live,
                    "psi": args.psi,
                    "load_case": args.load_case,
                },
                model_options={
                    "expansion_joint_springs": not args.no_expansion_joint_springs,
                    "expansion_joint_stiffness": args.expansion_joint_stiffness,
                },
            )
        print(json.dumps(result, indent=2 if args.pretty else None))
    except Exception as exc:
        error = {
            "file": args.ifc_path,
            "error": str(exc),
        }
        print(json.dumps(error, indent=2 if args.pretty else None), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
