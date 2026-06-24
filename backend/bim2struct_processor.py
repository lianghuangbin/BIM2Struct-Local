import math
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as Element
import numpy as np
from ifcopenshell.util.placement import get_local_placement


DEFINED_PARAMS = ["b", "h", "Ec", "Ey", "fc", "fy"]


def apply_T(T, p3):
    p4 = np.array([p3[0], p3[1], p3[2], 1.0], dtype=float)
    q4 = T @ p4
    return q4[:3]


def robust_extract_point(curve):
    if curve is None:
        return None

    if curve.is_a("IfcIndexedPolyCurve"):
        pts = getattr(curve, "Points", None)
        if pts and hasattr(pts, "CoordList") and len(pts.CoordList) > 0:
            c = pts.CoordList[0]
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    if curve.is_a("IfcPolyline"):
        if hasattr(curve, "Points") and len(curve.Points) > 0:
            c = curve.Points[0].Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    if curve.is_a("IfcLine"):
        if hasattr(curve, "Pnt") and hasattr(curve.Pnt, "Coordinates"):
            c = curve.Pnt.Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    if curve.is_a("IfcTrimmedCurve") or curve.is_a("IfcOffsetCurve2D"):
        return robust_extract_point(curve.BasisCurve)

    try:
        if hasattr(curve, "Location") and hasattr(curve.Location, "Coordinates"):
            c = curve.Location.Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]
    except Exception:
        pass

    return None


def get_grid_xy_compatible(ifc_file, round_ndigits=3):
    x_set, y_set = set(), set()
    grids = ifc_file.by_type("IfcGrid")
    if not grids:
        return [], []

    for grid in grids:
        T = np.array(get_local_placement(grid.ObjectPlacement), dtype=float)

        for u_axis in getattr(grid, "UAxes", []) or []:
            p_local = robust_extract_point(u_axis.AxisCurve)
            if p_local is None:
                continue
            p_world = apply_T(T, p_local)
            x_set.add(round(float(p_world[0]), round_ndigits))

        for v_axis in getattr(grid, "VAxes", []) or []:
            p_local = robust_extract_point(v_axis.AxisCurve)
            if p_local is None:
                continue
            p_world = apply_T(T, p_local)
            y_set.add(round(float(p_world[1]), round_ndigits))

    return sorted(x_set), sorted(y_set)


def get_storey_elevations_compatible(ifc_file, round_ndigits=3, use_world=True):
    elevations = set()
    storeys = ifc_file.by_type("IfcBuildingStorey")
    for storey in storeys:
        z_world = None
        try:
            T = np.array(get_local_placement(storey.ObjectPlacement), dtype=float)
            z_world = float(T[2, 3])
        except Exception:
            z_world = None

        z_elev = getattr(storey, "Elevation", None)
        if z_elev is not None:
            z_elev = float(z_elev)

        z_rel = None
        try:
            loc = storey.ObjectPlacement.RelativePlacement.Location.Coordinates
            if len(loc) >= 3:
                z_rel = float(loc[2])
        except Exception:
            z_rel = None

        if use_world:
            z_out = z_world if z_world is not None else (z_elev if z_elev is not None else z_rel)
        else:
            z_out = z_elev if z_elev is not None else (z_world if z_world is not None else z_rel)

        if z_out is not None:
            elevations.add(round(z_out, round_ndigits))
    return sorted(elevations)


def clean_sorted_coords(coords, tol=1e-3):
    coords = sorted([float(c) for c in coords])
    clean = []
    for c in coords:
        if len(clean) == 0 or abs(c - clean[-1]) > tol:
            clean.append(c)
    return np.array(clean, dtype=float)


def build_candidate_node_pool(x_coords, y_coords, z_coords):
    candidate_node_xyz = {}
    tag = 1
    for z in z_coords:
        for y in y_coords:
            for x in x_coords:
                candidate_node_xyz[tag] = np.array([x, y, z], dtype=float)
                tag += 1
    return candidate_node_xyz


def get_vertices_global(elem):
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, elem)
    return np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) * 1000.0


def read_column_endpoints(col, z_tol=1e-3):
    verts = get_vertices_global(col)
    zmin = np.min(verts[:, 2])
    zmax = np.max(verts[:, 2])
    bot = verts[np.abs(verts[:, 2] - zmin) <= z_tol]
    top = verts[np.abs(verts[:, 2] - zmax) <= z_tol]

    if len(bot) == 0:
        bot = verts[np.argsort(verts[:, 2])[:4]]
    if len(top) == 0:
        top = verts[np.argsort(verts[:, 2])[-4:]]

    return np.mean(bot, axis=0), np.mean(top, axis=0)


def read_beam_endpoints(beam, end_frac=0.05, min_end_pts=4):
    verts = get_vertices_global(beam)
    c = np.mean(verts, axis=0)
    X = verts - c
    _, _, Vt = np.linalg.svd(X, full_matrices=False)
    axis = Vt[0]
    s = X @ axis
    smin, smax = np.min(s), np.max(s)
    L = smax - smin
    if L < 1e-9:
        return c, c

    ds = max(end_frac * L, 1e-6)
    idx_start = np.where(s <= smin + ds)[0]
    idx_end = np.where(s >= smax - ds)[0]
    if len(idx_start) < min_end_pts or len(idx_end) < min_end_pts:
        ds = max(0.10 * L, ds)
        idx_start = np.where(s <= smin + ds)[0]
        idx_end = np.where(s >= smax - ds)[0]

    return np.mean(verts[idx_start], axis=0), np.mean(verts[idx_end], axis=0)


def read_wall_corners(wall):
    verts = get_vertices_global(wall)
    zmin = np.min(verts[:, 2])
    zmax = np.max(verts[:, 2])

    xy = verts[:, :2]
    cxy = np.mean(xy, axis=0)
    X = xy - cxy
    _, _, Vt = np.linalg.svd(X, full_matrices=False)
    wall_axis = Vt[0]

    s = X @ wall_axis
    smin, smax = np.min(s), np.max(s)
    xy_left = cxy + smin * wall_axis
    xy_right = cxy + smax * wall_axis
    if xy_left[0] > xy_right[0] or (abs(xy_left[0] - xy_right[0]) < 1e-6 and xy_left[1] > xy_right[1]):
        xy_left, xy_right = xy_right, xy_left

    p1 = np.array([xy_left[0], xy_left[1], zmin], dtype=float)
    p2 = np.array([xy_right[0], xy_right[1], zmin], dtype=float)
    p3 = np.array([xy_right[0], xy_right[1], zmax], dtype=float)
    p4 = np.array([xy_left[0], xy_left[1], zmax], dtype=float)
    return p1, p2, p3, p4


def to_local_xyz(p, x0, y0):
    p = np.asarray(p, dtype=float)
    return np.array([p[0] - x0, p[1] - y0, p[2]], dtype=float)


def snap_point_to_candidate_node(p_local, candidate_node_xyz, tol=800.0):
    best_tag = None
    best_dist = 1.0e30

    for tag, q in candidate_node_xyz.items():
        d = np.linalg.norm(p_local - q)
        if d < best_dist:
            best_dist = d
            best_tag = tag

    if best_dist <= tol:
        return best_tag, best_dist
    return None, best_dist


def snap_points_to_candidate_nodes(points_world, candidate_node_xyz, x0, y0, tol=800.0):
    node_tags = []
    snap_dists = []
    points_local = []

    for p_world in points_world:
        p_local = to_local_xyz(p_world, x0, y0)
        tag, dist = snap_point_to_candidate_node(p_local, candidate_node_xyz, tol=tol)
        node_tags.append(tag)
        snap_dists.append(dist)
        points_local.append(p_local)

    return node_tags, snap_dists, points_local


def get_ifc_params(elem, defined_params=DEFINED_PARAMS, preferred_pset="Structural"):
    out = {key: None for key in defined_params}
    psets = Element.get_psets(elem)

    if preferred_pset in psets:
        props = psets[preferred_pset]
        for key in defined_params:
            if key in props:
                out[key] = props[key]

    for props in psets.values():
        for key in defined_params:
            if out[key] is None and key in props:
                out[key] = props[key]

    return out


def build_column_table(columns, candidate_node_xyz, x0, y0, snap_tol=800.0):
    column_table = []
    skipped = []
    used_nodes = set()
    ele_tag = 1

    for col in columns:
        try:
            p_bot, p_top = read_column_endpoints(col)
            if p_top[2] <= -100:
                continue

            node_tags, dists, _ = snap_points_to_candidate_nodes([p_bot, p_top], candidate_node_xyz, x0, y0, tol=snap_tol)
            nI, nJ = node_tags
            dI, dJ = dists

            if nI is None or nJ is None:
                skipped.append({"type": "column_not_snapped", "GlobalId": col.GlobalId, "Name": col.Name, "dI": dI, "dJ": dJ})
                continue

            if nI == nJ:
                skipped.append({"type": "column_zero_length", "GlobalId": col.GlobalId, "Name": col.Name, "dI": dI, "dJ": dJ})
                continue

            zI = candidate_node_xyz[nI][2]
            zJ = candidate_node_xyz[nJ][2]
            if zI > zJ:
                nI, nJ = nJ, nI
                dI, dJ = dJ, dI

            params = get_ifc_params(col)
            column_table.append({
                "eleTag": ele_tag,
                "type": "column",
                "nodeI": nI,
                "nodeJ": nJ,
                "GlobalId": col.GlobalId,
                "Name": col.Name,
                "snapDistI": dI,
                "snapDistJ": dJ,
                **params,
            })
            used_nodes.update([nI, nJ])
            ele_tag += 1
        except Exception as exc:
            skipped.append({"type": "column_error", "GlobalId": getattr(col, "GlobalId", None), "Name": getattr(col, "Name", None), "error": str(exc)})

    return column_table, used_nodes, skipped


def build_beam_table(beams, candidate_node_xyz, x0, y0, start_eleTag=1, snap_tol=800.0):
    beam_table = []
    skipped = []
    used_nodes = set()
    ele_tag = start_eleTag

    for beam in beams:
        try:
            p_start, p_end = read_beam_endpoints(beam)
            if p_start[2] <= 0 or p_end[2] <= 0:
                continue

            node_tags, dists, _ = snap_points_to_candidate_nodes([p_start, p_end], candidate_node_xyz, x0, y0, tol=snap_tol)
            nI, nJ = node_tags
            dI, dJ = dists

            if nI is None or nJ is None:
                skipped.append({"type": "beam_not_snapped", "GlobalId": beam.GlobalId, "Name": beam.Name, "dI": dI, "dJ": dJ})
                continue

            if nI == nJ:
                skipped.append({"type": "beam_zero_length", "GlobalId": beam.GlobalId, "Name": beam.Name, "dI": dI, "dJ": dJ})
                continue

            params = get_ifc_params(beam)
            beam_table.append({
                "eleTag": ele_tag,
                "type": "beam",
                "nodeI": nI,
                "nodeJ": nJ,
                "GlobalId": beam.GlobalId,
                "Name": beam.Name,
                "snapDistI": dI,
                "snapDistJ": dJ,
                **params,
            })
            used_nodes.update([nI, nJ])
            ele_tag += 1
        except Exception as exc:
            skipped.append({"type": "beam_error", "GlobalId": getattr(beam, "GlobalId", None), "Name": getattr(beam, "Name", None), "error": str(exc)})

    return beam_table, used_nodes, skipped


def build_wall_table(walls, candidate_node_xyz, x0, y0, start_eleTag=1, snap_tol=800.0):
    wall_table = []
    skipped = []
    used_nodes = set()
    ele_tag = start_eleTag

    for wall in walls:
        try:
            p1, p2, p3, p4 = read_wall_corners(wall)
            if p1[2] <= -100 or p2[2] <= -100 or p3[2] <= -100 or p4[2] <= -100:
                continue

            node_tags, dists, _ = snap_points_to_candidate_nodes([p1, p2, p3, p4], candidate_node_xyz, x0, y0, tol=snap_tol)
            if any(tag is None for tag in node_tags):
                skipped.append({"type": "wall_not_snapped", "GlobalId": wall.GlobalId, "Name": wall.Name, "dists": dists})
                continue

            if len(set(node_tags)) < 4:
                skipped.append({"type": "wall_degenerate", "GlobalId": wall.GlobalId, "Name": wall.Name, "nodes": node_tags, "dists": dists})
                continue

            n1, n2, n3, n4 = node_tags
            params = get_ifc_params(wall)
            wall_table.append({
                "eleTag": ele_tag,
                "type": "wall_MVLEM_3D",
                "node1": n1,
                "node2": n2,
                "node3": n3,
                "node4": n4,
                "GlobalId": wall.GlobalId,
                "Name": wall.Name,
                "snapDists": dists,
                **params,
            })
            used_nodes.update(node_tags)
            ele_tag += 1
        except Exception as exc:
            skipped.append({"type": "wall_error", "GlobalId": getattr(wall, "GlobalId", None), "Name": getattr(wall, "Name", None), "error": str(exc)})

    return wall_table, used_nodes, skipped


def process_ifc(ifc_path):
    ifc_path = Path(ifc_path)
    ifc_file = ifcopenshell.open(str(ifc_path))

    x_coords_raw, y_coords_raw = get_grid_xy_compatible(ifc_file)
    z_coords_raw = get_storey_elevations_compatible(ifc_file, use_world=True)
    x_coords_raw = clean_sorted_coords(x_coords_raw)
    y_coords_raw = clean_sorted_coords(y_coords_raw)
    z_coords_raw = clean_sorted_coords(z_coords_raw)

    if len(x_coords_raw) == 0 or len(y_coords_raw) == 0 or len(z_coords_raw) == 0:
        raise ValueError("Could not extract grid coordinates or storey elevations from IFC.")

    x0 = float(np.min(x_coords_raw))
    y0 = float(np.min(y_coords_raw))
    x_coords = x_coords_raw - x0
    y_coords = y_coords_raw - y0
    z_coords = np.array([z for z in z_coords_raw if z >= -1e-6], dtype=float)

    candidate_node_xyz = build_candidate_node_pool(x_coords, y_coords, z_coords)

    columns = ifc_file.by_type("IfcColumn")
    beams = ifc_file.by_type("IfcBeam")
    walls = ifc_file.by_type("IfcWall")

    column_table, column_nodes, skipped_columns = build_column_table(columns, candidate_node_xyz, x0, y0, snap_tol=1000.0)
    beam_table, beam_nodes, skipped_beams = build_beam_table(
        beams,
        candidate_node_xyz,
        x0,
        y0,
        start_eleTag=len(column_table) + 1,
        snap_tol=800,
    )
    wall_table, wall_nodes, skipped_walls = build_wall_table(
        walls,
        candidate_node_xyz,
        x0,
        y0,
        start_eleTag=len(column_table) + len(beam_table) + 1,
        snap_tol=1200,
    )

    used_nodes = set()
    used_nodes.update(column_nodes)
    used_nodes.update(beam_nodes)
    used_nodes.update(wall_nodes)

    node_table = []
    for tag in sorted(used_nodes):
        p = candidate_node_xyz[tag]
        node_table.append({
            "nodeTag": int(tag),
            "x": float(p[0]),
            "y": float(p[1]),
            "z": float(p[2]),
            "is_fixed": bool(abs(p[2]) < 1e-6),
        })

    return {
        "counts": {
            "nodes": len(node_table),
            "columns": len(column_table),
            "beams": len(beam_table),
            "walls": len(wall_table),
            "skipped_columns": len(skipped_columns),
            "skipped_beams": len(skipped_beams),
            "skipped_walls": len(skipped_walls),
        },
        "coordinates": {
            "x": [float(v) for v in x_coords],
            "y": [float(v) for v in y_coords],
            "z": [float(v) for v in z_coords],
        },
    }
