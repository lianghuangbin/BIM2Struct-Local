# %%
import math
import numpy as np
import ifcopenshell
import ifcopenshell.geom
from ifcopenshell.util.placement import get_local_placement
import ifcopenshell.util.element as Element
import matplotlib
import matplotlib.pyplot as plt
import openseespy.opensees as ops
from pyparsing import col



def get_grid_xy_compatible(ifcFile, round_ndigits=3):
    """
    Read IfcGrid U/V axis coordinates for IFC2x3 and IFC4 models.

    Returned coordinates are transformed into the IFC model/world coordinate
    system, consistent with ifcopenshell's USE_WORLD_COORDS setting.
    """
    x_set, y_set = set(), set()
    grids = ifcFile.by_type("IfcGrid")
    if not grids:
        return [], []

    for grid in grids:
        # Transform grid-local coordinates into the model/world coordinate system.
        T = np.array(get_local_placement(grid.ObjectPlacement), dtype=float)  # 4x4

        # U axes contribute X coordinates after the world transform.
        for u_axis in getattr(grid, "UAxes", []) or []:
            p_local = robust_extract_point(u_axis.AxisCurve)  # [x,y,z] in grid-local (often z=0)
            if p_local is None:
                continue
            p_world = apply_T(T, p_local)
            x_set.add(round(float(p_world[0]), round_ndigits))

        # V axes contribute Y coordinates after the world transform.
        for v_axis in getattr(grid, "VAxes", []) or []:
            p_local = robust_extract_point(v_axis.AxisCurve)
            if p_local is None:
                continue
            p_world = apply_T(T, p_local)
            y_set.add(round(float(p_world[1]), round_ndigits))

    return sorted(x_set), sorted(y_set)

def apply_T(T, p3):
    """Apply a 4x4 homogeneous transform to a 3D point."""
    p4 = np.array([p3[0], p3[1], p3[2], 1.0], dtype=float)
    q4 = T @ p4
    return q4[:3]


def robust_extract_point(curve):
    """
    Extract a "representative point" (usually the first point) from various AxisCurve types:
    - Returns [x, y, z] (if 2D point, then z=0)
    """
    if curve is None:
        return None

    # IFC4: IfcIndexedPolyCurve
    if curve.is_a("IfcIndexedPolyCurve"):
        pts = getattr(curve, "Points", None)
        if pts and hasattr(pts, "CoordList") and len(pts.CoordList) > 0:
            c = pts.CoordList[0]
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    # IFC2x3/IFC4: IfcPolyline
    if curve.is_a("IfcPolyline"):
        if hasattr(curve, "Points") and len(curve.Points) > 0:
            c = curve.Points[0].Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    # IfcLine: use the line start point.
    if curve.is_a("IfcLine"):
        if hasattr(curve, "Pnt") and hasattr(curve.Pnt, "Coordinates"):
            c = curve.Pnt.Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]

    # Wrapped curve types: resolve through the basis curve.
    if curve.is_a("IfcTrimmedCurve") or curve.is_a("IfcOffsetCurve2D"):
        return robust_extract_point(curve.BasisCurve)

    # Fallback for curve-like objects that expose a Location.
    try:
        if hasattr(curve, "Location") and hasattr(curve.Location, "Coordinates"):
            c = curve.Location.Coordinates
            if len(c) == 2:
                return [float(c[0]), float(c[1]), 0.0]
            return [float(c[0]), float(c[1]), float(c[2])]
    except:
        pass

    return None

def get_storey_elevations_compatible(ifcFile, round_ndigits=3, use_world=True):
    """
    Read storey elevations in a way compatible with IFC2x3, IFC4, and
    ifcopenshell world-coordinate settings.
    """
    elevations = set()
    storeys = ifcFile.by_type("IfcBuildingStorey")
    for s in storeys:
        z_world = None
        # 1) Prefer the world/model Z coordinate from the object placement.
        try:
            T = np.array(get_local_placement(s.ObjectPlacement), dtype=float)  # 4x4
            z_world = float(T[2, 3])
        except:
            z_world = None
        # 2) Fall back to the IFC Elevation attribute.
        z_elev = getattr(s, "Elevation", None)
        if z_elev is not None:
            z_elev = float(z_elev)
        # 3) Last fallback: RelativePlacement.Location.
        z_rel = None
        try:
            loc = s.ObjectPlacement.RelativePlacement.Location.Coordinates
            if len(loc) >= 3:
                z_rel = float(loc[2])
        except:
            z_rel = None
        # Select the output convention requested by the caller.
        if use_world:
            # Prefer world Z, then Elevation, then relative Z.
            z_out = z_world if z_world is not None else (z_elev if z_elev is not None else z_rel)
        else:
            # Prefer Elevation, then world Z, then relative Z.
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


def get_length_unit_scale_to_mm(ifcFile):
    """
    Return the multiplier from IFC project length units to millimetres.
    IfcOpenShell geometry is converted to metres and then multiplied by 1000
    in get_vertices_global(), so grid/storey coordinates must be converted to
    the same millimetre convention before snapping.
    """
    prefix_scale = {
        None: 1.0,
        "EXA": 1e18,
        "PETA": 1e15,
        "TERA": 1e12,
        "GIGA": 1e9,
        "MEGA": 1e6,
        "KILO": 1e3,
        "HECTO": 1e2,
        "DECA": 1e1,
        "DECI": 1e-1,
        "CENTI": 1e-2,
        "MILLI": 1e-3,
        "MICRO": 1e-6,
        "NANO": 1e-9,
        "PICO": 1e-12,
        "FEMTO": 1e-15,
        "ATTO": 1e-18,
    }

    for unit in ifcFile.by_type("IfcSIUnit"):
        try:
            if unit.UnitType == "LENGTHUNIT" and unit.Name == "METRE":
                return prefix_scale.get(getattr(unit, "Prefix", None), 1.0) * 1000.0
        except Exception:
            continue

    return 1000.0


# # %%  Read IFC file, extract grid coordinates and storey elevations, and transform them to the local coordinate system.
# ifcFile = ifcopenshell.open("HUG_unitB.ifc")
# xCoords_raw, yCoords_raw = get_grid_xy_compatible(ifcFile)

# zCoords_raw = get_storey_elevations_compatible(ifcFile, use_world=True)

# xCoords_raw = clean_sorted_coords(xCoords_raw)
# yCoords_raw = clean_sorted_coords(yCoords_raw)
# zCoords_raw = clean_sorted_coords(zCoords_raw)

# x0 = float(np.min(xCoords_raw))
# y0 = float(np.min(yCoords_raw))

# xCoords = xCoords_raw - x0
# yCoords = yCoords_raw - y0
# zCoords = np.array([z for z in zCoords_raw if z >= -1e-6], dtype=float)

# print("Local xCoords:", xCoords)
# print("Local yCoords:", yCoords)
# print("Local zCoords:", zCoords)

# %%

def to_local_xyz(p):
    p = np.asarray(p, dtype=float)
    return np.array([p[0] - x0, p[1] - y0, p[2]], dtype=float)


def build_candidate_node_pool(xCoords, yCoords, zCoords):
    candidate_node_xyz = {}
    candidate_node_index = {}

    tag = 1
    for iz, z in enumerate(zCoords):
        for iy, y in enumerate(yCoords):
            for ix, x in enumerate(xCoords):
                candidate_node_index[(ix, iy, iz)] = tag
                candidate_node_xyz[tag] = np.array([x, y, z], dtype=float)
                tag += 1

    return candidate_node_xyz, candidate_node_index


# candidate_node_xyz, candidate_node_index = build_candidate_node_pool(xCoords, yCoords, zCoords)

# print("Candidate nodes:", len(candidate_node_xyz))



# %%
# =========================================================
# 1. read_column_endpoints
# =========================================================
def get_vertices_global(elem):
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, elem)
    verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) * 1000.0  # convert to millimeters
    return verts


def read_column_endpoints(col, z_tol=1e-3):
    """
    IfcColumn -> bottom center + top center
    Return raw world coordinates.
    """
    verts = get_vertices_global(col)
    zmin = np.min(verts[:, 2])
    zmax = np.max(verts[:, 2])
    bot = verts[np.abs(verts[:, 2] - zmin) <= z_tol]
    top = verts[np.abs(verts[:, 2] - zmax) <= z_tol]

    if len(bot) == 0:
        bot = verts[np.argsort(verts[:, 2])[:4]]
    if len(top) == 0:
        top = verts[np.argsort(verts[:, 2])[-4:]]

    p_bot = np.mean(bot, axis=0)
    p_top = np.mean(top, axis=0)

    return p_bot, p_top


# =========================================================
# 2. read_beam_endpoints
# =========================================================
def read_beam_endpoints(beam, end_frac=0.05, min_end_pts=4):
    """
    IfcBeam -> start point + end point using PCA major axis.
    Return raw world coordinates.
    """
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
    p_start = np.mean(verts[idx_start], axis=0)
    p_end = np.mean(verts[idx_end], axis=0)

    return p_start, p_end


# =========================================================
# 3. read_wall_corners
# =========================================================
def read_wall_corners(wall):
    """
    IfcWall -> four MVLEM_3D panel corners:
    n1 bottom-left, n2 bottom-right, n3 top-right, n4 top-left.
    """
    verts = get_vertices_global(wall)

    zmin = np.min(verts[:, 2])
    zmax = np.max(verts[:, 2])

    xy = verts[:, :2]
    cxy = np.mean(xy, axis=0)
    X = xy - cxy

    _, _, Vt = np.linalg.svd(X, full_matrices=False)

    wall_axis = Vt[0]  # wall length direction in XY plane

    s = X @ wall_axis
    smin, smax = np.min(s), np.max(s)

    xy_left = cxy + smin * wall_axis
    xy_right = cxy + smax * wall_axis
    if xy_left[0] > xy_right[0] or (abs(xy_left[0] - xy_right[0]) < 1e-6 and xy_left[1] > xy_right[1]):
        xy_left, xy_right = xy_right, xy_left  # keep a consistent left/right order

    p1 = np.array([xy_left[0],  xy_left[1],  zmin], dtype=float)
    p2 = np.array([xy_right[0], xy_right[1], zmin], dtype=float)
    p3 = np.array([xy_right[0], xy_right[1], zmax], dtype=float)
    p4 = np.array([xy_left[0],  xy_left[1],  zmax], dtype=float)

    return p1, p2, p3, p4


# %%

# columns = ifcFile.by_type("IfcColumn")
# beams = ifcFile.by_type("IfcBeam")
# walls = ifcFile.by_type("IfcWall")

# p_bot, p_top = read_column_endpoints(columns[10])
# p_start, p_end = read_beam_endpoints(beams[0])
# p1, p2, p3, p4 = read_wall_corners(walls[20])

# %%
# =========================================================
# 4. snap_points_to_candidate_nodes
# =========================================================
def snap_point_to_candidate_node(p_local, candidate_node_xyz, tol=800.0):
    """
    Snap one local point to the nearest candidate grid-storey node.
    """
    p_local = np.asarray(p_local, dtype=float)

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


def snap_points_to_candidate_nodes(points_world, candidate_node_xyz, tol=800.0):
    """
    Snap multiple raw world-coordinate points to candidate nodes.
    Return:
        node_tags, snap_dists, points_local
    """
    node_tags = []
    snap_dists = []
    points_local = []

    for p_world in points_world:
        p_local = to_local_xyz(p_world)
        tag, dist = snap_point_to_candidate_node(
            p_local,
            candidate_node_xyz,
            tol=tol
        )

        node_tags.append(tag)
        snap_dists.append(dist)
        points_local.append(p_local)

    return node_tags, snap_dists, points_local


# snap_points_to_candidate_nodes([p_bot, p_top], candidate_node_xyz, tol=1000)
# snap_points_to_candidate_nodes([p_start, p_end], candidate_node_xyz, tol=1000)
# snap_points_to_candidate_nodes([p1, p2, p3, p4], candidate_node_xyz, tol=1000)



# %%
# =========================================================
# 5. build_column_table
# =========================================================

defined_params = ["b", "h", "Ec", "Ey", "fc", "fy"]

def get_ifc_params(elem, defined_params, preferred_pset="Structural"):
    """
    Read selected shared parameters from IFC property sets.
    Priority:
      1) preferred_pset, e.g. Structural
      2) all instance psets
    """
    out = {key: None for key in defined_params}

    # 1) instance psets
    psets = Element.get_psets(elem)

    if preferred_pset in psets:
        props = psets[preferred_pset]
        for key in defined_params:
            if key in props:
                out[key] = props[key]

    # 2) fallback: search all instance psets
    for pset_name, props in psets.items():
        for key in defined_params:
            if out[key] is None and key in props:
                out[key] = props[key]

    return out

def fill_default_params(params):
    defaults = {
        "b": 400.0,
        "h": 400.0,
        "Ec": 30000.0,
        "Ey": 200000.0,
        "fc": 28.0,
        "fy": 300.0,
    }
    return {k: defaults[k] if params.get(k) is None else float(params[k]) for k in defaults}


def build_column_table(columns, candidate_node_xyz, snap_tol=800.0):
    column_table = []
    skipped = []
    used_nodes = set()

    eleTag = 1

    for col in columns:
        try:
            p_bot, p_top = read_column_endpoints(col)

            if p_top[2] <= -100:
                continue

            node_tags, dists, _ = snap_points_to_candidate_nodes([p_bot, p_top], candidate_node_xyz, tol=snap_tol)

            nI, nJ = node_tags
            dI, dJ = dists

            if nI is None or nJ is None:
                skipped.append({
                    "type": "column_not_snapped",
                    "GlobalId": col.GlobalId,
                    "Name": col.Name,
                    "dI": dI,
                    "dJ": dJ
                })
                continue

            if nI == nJ:
                skipped.append({
                    "type": "column_zero_length",
                    "GlobalId": col.GlobalId,
                    "Name": col.Name,
                    "dI": dI,
                    "dJ": dJ
                })
                continue

            zI = candidate_node_xyz[nI][2]
            zJ = candidate_node_xyz[nJ][2]

            # Ensure I is bottom, J is top
            if zI > zJ:
                nI, nJ = nJ, nI
                dI, dJ = dJ, dI

            params = fill_default_params(get_ifc_params(col, defined_params))

            column_table.append({
                "eleTag": eleTag,
                "type": "column",
                "nodeI": nI,
                "nodeJ": nJ,
                "GlobalId": col.GlobalId,
                "Name": col.Name,
                "snapDistI": dI,
                "snapDistJ": dJ,

                # IFC shared parameters
                "b": params["b"],
                "h": params["h"],
                "Ec": params["Ec"],
                "Ey": params["Ey"],
                "fc": params["fc"],
                "fy": params["fy"],
                'cover': 40.0,  # default cover, can be overridden by shared params
                'rho': 0.02  # default total reinforcement ratio, can be overridden by shared params
            })

            used_nodes.update([nI, nJ])
            eleTag += 1

        except Exception as e:
            skipped.append({
                "type": "column_error",
                "GlobalId": getattr(col, "GlobalId", None),
                "Name": getattr(col, "Name", None),
                "error": str(e)
            })

    return column_table, used_nodes, skipped


# =========================================================
# 6. build_beam_table
# =========================================================

def build_beam_table(beams, candidate_node_xyz, start_eleTag=1, snap_tol=800.0):
    beam_table = []
    skipped = []
    used_nodes = set()

    eleTag = start_eleTag

    for beam in beams:
        try:
            p_start, p_end = read_beam_endpoints(beam)
            if p_start[2] <= 0 or p_end[2] <= 0:
                continue

            node_tags, dists, _ = snap_points_to_candidate_nodes([p_start, p_end],candidate_node_xyz,tol=snap_tol)

            nI, nJ = node_tags
            dI, dJ = dists

            if nI is None or nJ is None:
                skipped.append({"type": "beam_not_snapped", "GlobalId": beam.GlobalId,"Name": beam.Name,"dI": dI,"dJ": dJ})
                continue

            if nI == nJ:
                skipped.append({
                    "type": "beam_zero_length",
                    "GlobalId": beam.GlobalId,
                    "Name": beam.Name,
                    "dI": dI,
                    "dJ": dJ
                })
                continue

            # pI = candidate_node_xyz[nI]
            # pJ = candidate_node_xyz[nJ]
            # v = pJ - pI
            # if abs(v[0]) >= abs(v[1]): direction = "X", transfTag = 11
            # else: direction = "Y", transfTag = 12

            params = fill_default_params(get_ifc_params(beam, defined_params))
            G = params["Ec"] / 2.4
            A = params["b"] * params["h"]
            Iy = params["b"] * params["h"]**3 / 12.0
            Iz = params["h"] * params["b"]**3 / 12.0
            J = 1.0e9

            beam_table.append({
                "eleTag": eleTag,
                "type": "beam",
                "nodeI": nI,
                "nodeJ": nJ,
                "GlobalId": beam.GlobalId,
                "Name": beam.Name,
                "snapDistI": dI,
                "snapDistJ": dJ,

                # IFC shared parameters
                "b": params["b"],
                "h": params["h"],
                "Ec": params["Ec"],
                "Ey": params["Ey"],
                "fc": params["fc"],
                "fy": params["fy"],

                # Derived section properties
                "A": A,
                "Iy": Iy,
                "Iz": Iz,
                "G": G,
                "J": J
            })

            used_nodes.update([nI, nJ])
            eleTag += 1

        except Exception as e:
            skipped.append({
                "type": "beam_error",
                "GlobalId": getattr(beam, "GlobalId", None),
                "Name": getattr(beam, "Name", None),
                "error": str(e)
            })

    return beam_table, used_nodes, skipped

# =========================================================
# 7. build_wall_table
# =========================================================

def build_wall_table(walls, candidate_node_xyz, start_eleTag=1, snap_tol=800.0):
    wall_table = []
    skipped = []
    used_nodes = set()

    eleTag = start_eleTag

    for wall in walls:
        try:
            p1, p2, p3, p4 = read_wall_corners(wall)
            if p1[2] <= -100 or p2[2] <= -100 or p3[2] <= -100 or p4[2] <= -100:
                continue


            node_tags, dists, _ = snap_points_to_candidate_nodes(
                [p1, p2, p3, p4],
                candidate_node_xyz,
                tol=snap_tol
            )

            n1, n2, n3, n4 = node_tags

            if any(tag is None for tag in node_tags):
                skipped.append({
                    "type": "wall_not_snapped",
                    "GlobalId": wall.GlobalId,
                    "Name": wall.Name,
                    "dists": dists
                })
                continue

            if len(set(node_tags)) < 4:
                skipped.append({
                    "type": "wall_degenerate",
                    "GlobalId": wall.GlobalId,
                    "Name": wall.Name,
                    "nodes": node_tags,
                    "dists": dists
                })
                continue

            params = fill_default_params(get_ifc_params(wall, defined_params))
            
            p_node1 = candidate_node_xyz[n1]
            p_node2 = candidate_node_xyz[n2]
            wallLength = np.linalg.norm(p_node2[:2] - p_node1[:2])
            Gc = params["Ec"] / 2.4
            Av = params["b"] * wallLength


            wall_table.append({
                "eleTag": eleTag,
                "type": "wall_MVLEM_3D",
                "node1": n1,
                "node2": n2,
                "node3": n3,
                "node4": n4,
                "GlobalId": wall.GlobalId,
                "Name": wall.Name,
                "snapDists": dists,

                # IFC shared parameters
                "t": params["b"],
                "Ec": params["Ec"],
                "Ey": params["Ey"],
                "fc": params["fc"],
                "fy": params["fy"],

                # Derived section properties
                "wallLength": wallLength,
                "Gc": Gc,
                "Av": Av,

                # MVLEM parameters
                "m": 10,
                "rho_boundary": 0.020,
                "rho_web": 0.004
            })

            used_nodes.update(node_tags)
            eleTag += 1

        except Exception as e:
            skipped.append({
                "type": "wall_error",
                "GlobalId": getattr(wall, "GlobalId", None),
                "Name": getattr(wall, "Name", None),
                "error": str(e)
            })

    return wall_table, used_nodes, skipped




# column_table, column_nodes, skipped_columns = build_column_table(
#     columns,
#     candidate_node_xyz,
#     snap_tol=1000.0
# )
# print("Columns:", len(column_table))


# beam_table, beam_nodes, skipped_beams = build_beam_table(
#     beams,
#     candidate_node_xyz,
#     start_eleTag=len(column_table) + 1,
#     snap_tol=800
# )
# print("Beams:", len(beam_table))


# wall_table, wall_nodes, skipped_walls = build_wall_table(
#     walls,
#     candidate_node_xyz,
#     start_eleTag=len(column_table) + len(beam_table) + 1,
#     snap_tol=1200
# )
# print("Walls:", len(wall_table))


# %%
# =========================================================
# 8. collect used nodes
# =========================================================

# used_nodes = set()
# used_nodes.update(column_nodes)
# used_nodes.update(beam_nodes)
# used_nodes.update(wall_nodes)

# node_table = []

# for tag in sorted(used_nodes):
#     p = candidate_node_xyz[tag]
#     node_table.append({
#         "nodeTag": tag,
#         "x": p[0],
#         "y": p[1],
#         "z": p[2],
#         "is_fixed": abs(p[2]) < 1e-6
#     })

# print("Nodes:", len(node_table))
# print("Columns:", len(column_table))
# print("Beams:", len(beam_table))
# print("Walls:", len(wall_table))

# print("Skipped columns:", len(skipped_columns))
# print("Skipped beams:", len(skipped_beams))
# print("Skipped walls:", len(skipped_walls))



# %%
# =========================================================
# 9. build_opensees_model from node/column/beam/wall tables
# =========================================================
def make_tags(eleTag):
    """
    Generate unique OpenSees tags for each component.
    """
    return {
        "coverTag": 10000 + eleTag * 10 + 1,
        "coreTag":  10000 + eleTag * 10 + 2,
        "steelTag": 10000 + eleTag * 10 + 3,
        "secTag":   20000 + eleTag,
        "intTag":   30000 + eleTag,
        "shearTag": 40000 + eleTag,
    }


def define_component_rc_section(ele):
    """
    Define independent RC fiber section for each column.
    Unit: N-mm-MPa.
    """
    eleTag = int(ele["eleTag"])
    tags = make_tags(eleTag)

    b = float(ele["b"])
    h = float(ele["h"])
    cover = float(ele["cover"])
    rho = float(ele["rho"])

    fc = float(ele["fc"])
    Ec = float(ele["Ec"])
    fy = float(ele["fy"])
    Es = float(ele["Ey"])

    # concrete material
    ops.uniaxialMaterial("Concrete02", tags["coverTag"], -fc, -0.002, -0.20*fc, -0.006, 0.1, 0.1*fc, 0.1*fc/0.002)

    ops.uniaxialMaterial("Concrete02", tags["coreTag"], -1.15*fc, -0.003, -0.40*fc, -0.012, 0.1, 0.1*fc, 0.1*fc/0.002)

    ops.uniaxialMaterial("Steel02", tags["steelTag"], fy, Es, 0.002, 18, 0.925, 0.15)

    # section geometry
    y1, y2 = -b / 2.0, b / 2.0
    z1, z2 = -h / 2.0, h / 2.0

    cy1, cy2 = y1 + cover, y2 - cover
    cz1, cz2 = z1 + cover, z2 - cover

    A_total = rho * b * h

    # simple rule:
    # normal column: 8 bars
    # elongated column: 16 bars
    if max(b, h) / min(b, h) > 2.0:
        n_bar_total = 16
        n_side = 5
    else:
        n_bar_total = 8
        n_side = 1

    A_bar = A_total / n_bar_total

    ops.section("Fiber", tags["secTag"], "-GJ", 1.0e12)

    # core
    ops.patch("rect", tags["coreTag"], 10, 10, cy1, cz1, cy2, cz2)

    # cover
    ops.patch("rect", tags["coverTag"], 10, 2, y1, z1, y2, cz1)
    ops.patch("rect", tags["coverTag"], 10, 2, y1, cz2, y2, z2)
    ops.patch("rect", tags["coverTag"], 2, 10, y1, cz1, cy1, cz2)
    ops.patch("rect", tags["coverTag"], 2, 10, cy2, cz1, y2, cz2)

    # top and bottom rebars
    ops.layer("straight", tags["steelTag"], 3, A_bar, cy1, cz2, cy2, cz2)
    ops.layer("straight", tags["steelTag"], 3, A_bar, cy1, cz1, cy2, cz1)

    # side rebars
    if n_side == 1:
        ops.layer("straight", tags["steelTag"], 1, A_bar, cy1, 0.0, cy1, 0.0)
        ops.layer("straight", tags["steelTag"], 1, A_bar, cy2, 0.0, cy2, 0.0)
    else:
        ops.layer("straight", tags["steelTag"], n_side, A_bar, cy1, cz1 + 200.0, cy1, cz2 - 200.0)
        ops.layer("straight", tags["steelTag"], n_side, A_bar, cy2, cz1 + 200.0, cy2, cz2 - 200.0)

    ops.beamIntegration("Lobatto", tags["intTag"], tags["secTag"], 5)

    return tags


def define_wall_materials_and_get_tags(wall):
    """
    Define independent materials for each wall element.
    """
    eleTag = int(wall["eleTag"])
    tags = make_tags(eleTag)

    fc = float(wall["fc"])
    fy = float(wall["fy"])
    Es = float(wall["Ey"])
    Ec = float(wall["Ec"])

    fcc = 1.2 * fc

    ops.uniaxialMaterial("Steel02", tags["steelTag"], fy, Es, 0.002, 20, 0.925, 0.15)

    ops.uniaxialMaterial("Concrete04", tags["coverTag"], -fc, -0.002, -0.005, Ec)

    ops.uniaxialMaterial("Concrete04", tags["coreTag"], -fcc, -0.003, -0.010, Ec)

    # shear spring
    Gc = float(wall["Gc"])
    Av = float(wall["Av"])
    ops.uniaxialMaterial("Elastic", tags["shearTag"], Gc * Av)

    return tags


def get_story_nodes_from_table(node_table, zCoords, tol=1e-3):
    """
    Group used structural nodes by floor elevation.
    """
    story_nodes = {k: [] for k in range(len(zCoords))}

    for nd in node_table:
        nodeTag = int(nd["nodeTag"])
        z = float(nd["z"])

        k = int(np.argmin(np.abs(zCoords - z)))

        if abs(zCoords[k] - z) <= tol:
            story_nodes[k].append(nodeTag)

    return story_nodes


# %%
# =========================================================
# Visualization function
# =========================================================
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def plot_opensees_model(
    node_table,
    column_table,
    beam_table,
    wall_table,
    xCoords,
    yCoords,
    zCoords,
    show_nodes=True,
    show_slabs=True
):
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection='3d')

    # columns
    for col in column_table:
        nI = int(col["nodeI"])
        nJ = int(col["nodeJ"])
        pI = np.array(ops.nodeCoord(nI))
        pJ = np.array(ops.nodeCoord(nJ))
        b = float(col["b"])
        h = float(col["h"])

        if max(b, h) / min(b, h) > 2.0:
            lw = 2.0
        else:
            lw = 1.2

        ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color="black",lw=lw)

    # beams
    for beam in beam_table:
        nI = int(beam["nodeI"])
        nJ = int(beam["nodeJ"])
        pI = np.array(ops.nodeCoord(nI))
        pJ = np.array(ops.nodeCoord(nJ))

        ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color="steelblue",lw=0.7,alpha=0.8)

    # walls
    for wall in wall_table:
        nodes = [int(wall["node1"]),int(wall["node2"]),int(wall["node3"]),int(wall["node4"])]
        pts = np.array([ops.nodeCoord(nd) for nd in nodes])

        poly = Poly3DCollection([pts],facecolor="red",alpha=0.25,edgecolor="red",linewidth=0.5)
        ax.add_collection3d(poly)

    # slabs
    if show_slabs:
        x_min, x_max = float(np.min(xCoords)), float(np.max(xCoords))
        y_min, y_max = float(np.min(yCoords)), float(np.max(yCoords))

        slab_corners = np.array([[x_min, y_min],[x_max, y_min],[x_max, y_max],[x_min, y_max]])

        for z in zCoords[1:]:
            slab_pts = np.column_stack([slab_corners[:, 0],slab_corners[:, 1],np.full(4, z)])

            slab = Poly3DCollection(
                [slab_pts], facecolor="lightgray",alpha=0.30,edgecolor="gray",linewidth=0.4)
            ax.add_collection3d(slab)

    # nodes
    if show_nodes:
        P = np.array([[nd["x"], nd["y"], nd["z"]] for nd in node_table])
        ax.scatter(P[:, 0], P[:, 1], P[:, 2], s=5, color="tab:blue")

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_zlabel("Z (mm)")

    ax.set_box_aspect([
        float(np.max(xCoords) - np.min(xCoords)),
        float(np.max(yCoords) - np.min(yCoords)),
        float(np.max(zCoords) - np.min(zCoords))
    ])

    plt.tight_layout()
    plt.show()



# %%
# =========================================================
# Gravity load and gravity analysis
# =========================================================

def apply_gravity_loads(
    node_table,
    zCoords,
    xCoords,
    yCoords,
    q_dead=5.0,   # kN/m2
    q_live=2.5,   # kN/m2
    psi=0.6,
    q_gravity=None,
    q_seismic=None,
    patternTag=1
):
    """
    Apply gravity load to structural nodes floor by floor.
    Unit:
      q_dead/q_live: kN/m2
      load: N
      geometry: mm
    """
    ops.timeSeries("Linear", patternTag)
    ops.pattern("Plain", patternTag, patternTag)

    Lx_total = float(np.max(xCoords) - np.min(xCoords))
    Ly_total = float(np.max(yCoords) - np.min(yCoords))
    A_floor_m2 = (Lx_total / 1000.0) * (Ly_total / 1000.0)
    q_gravity = float(q_gravity) if q_gravity is not None else q_dead + q_live
    q_seismic = float(q_seismic) if q_seismic is not None else q_dead + psi * q_live
    WG_floor = q_gravity * A_floor_m2 * 1000.0  # N
    WEQ_floor  = q_seismic * A_floor_m2 * 1000.0  # N

    story_nodes = get_story_nodes_from_table(node_table, zCoords)

    for k in range(1, len(zCoords)):
        nodes_k = story_nodes.get(k, [])
        WG_k = WG_floor
        gLoad_node = -WG_k / len(nodes_k)

        for nd in nodes_k:
            ops.load(nd, 0.0, 0.0, gLoad_node, 0.0, 0.0, 0.0)

    print("Gravity loads assigned.")


    return {"WEQ_floor": WEQ_floor}


def run_gravity_analysis(n_steps=10):
    """
    Basic gravity analysis.
    """
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0 / n_steps)
    ops.analysis("Static")

    ok = ops.analyze(n_steps)

    if ok == 0:
        print("Gravity analysis completed.")
        ops.loadConst("-time", 0.0)
    else:
        print("Gravity analysis failed. Trying ModifiedNewton...")
        ops.algorithm("ModifiedNewton")
        ok = ops.analyze(n_steps)

        if ok == 0:
            print("Gravity analysis completed with ModifiedNewton.")
            ops.loadConst("-time", 0.0)
        else:
            print("Gravity analysis still failed.")

    return ok

# =========================================================
# modal analysis and mode shape plotting
# =========================================================
def run_modal_analysis(numModes=6):
    ops.wipeAnalysis()
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("FullGeneral")

    eigVals = ops.eigen(numModes)

    modal_results = {}

    print("\nModal analysis results:")
    for i, lam in enumerate(eigVals, start=1):
        omega = np.sqrt(lam)
        freq = omega / (2.0 * np.pi)
        period = 1.0 / freq

        modal_results[i] = {
            "lambda": lam,
            "omega": omega,
            "freq": freq,
            "T": period
        }

        print(
            f"Mode {i}: "
            f"lambda = {lam:.4e}, "
            f"omega = {omega:.4f} rad/s, "
            f"f = {freq:.4f} Hz, "
            f"T = {period:.4f} s"
        )

    return eigVals, modal_results


def plot_mode_shape_3d(
    mode_id,
    eigVals,
    column_table,
    beam_table,
    wall_table,
    xCoords,
    yCoords,
    zCoords,
    scale=100000.0
):
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")

    # -----------------------------
    # columns
    # -----------------------------
    for col in column_table:
        nI = int(col["nodeI"])
        nJ = int(col["nodeJ"])

        pI0 = np.array(ops.nodeCoord(nI), dtype=float)
        pJ0 = np.array(ops.nodeCoord(nJ), dtype=float)

        phiI = np.array(ops.nodeEigenvector(nI, mode_id)[0:3], dtype=float)
        phiJ = np.array(ops.nodeEigenvector(nJ, mode_id)[0:3], dtype=float)

        pI = pI0 + scale * phiI
        pJ = pJ0 + scale * phiJ

        ax.plot([pI0[0], pJ0[0]],[pI0[1], pJ0[1]],[pI0[2], pJ0[2]],color="lightgray",lw=0.7,alpha=0.5)

        ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color="black",lw=1.3)

    # -----------------------------
    # beams
    # -----------------------------
    for beam in beam_table:
        nI = int(beam["nodeI"])
        nJ = int(beam["nodeJ"])

        pI0 = np.array(ops.nodeCoord(nI), dtype=float)
        pJ0 = np.array(ops.nodeCoord(nJ), dtype=float)

        phiI = np.array(ops.nodeEigenvector(nI, mode_id)[0:3], dtype=float)
        phiJ = np.array(ops.nodeEigenvector(nJ, mode_id)[0:3], dtype=float)

        pI = pI0 + scale * phiI
        pJ = pJ0 + scale * phiJ

        color = "steelblue" if beam.get("direction", "X") == "X" else "royalblue"

        ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color=color,lw=0.9,alpha=0.8)

    # -----------------------------
    # walls
    # -----------------------------
    for wall in wall_table:
        node_list = [
            int(wall["node1"]),
            int(wall["node2"]),
            int(wall["node3"]),
            int(wall["node4"])
        ]

        pts = []
        for nd in node_list:
            p0 = np.array(ops.nodeCoord(nd), dtype=float)
            phi = np.array(ops.nodeEigenvector(nd, mode_id)[0:3], dtype=float)
            pts.append(p0 + scale * phi)

        pts = np.array(pts)

        poly = Poly3DCollection([pts],facecolor="red",alpha=0.25,edgecolor="red",linewidth=0.8)
        ax.add_collection3d(poly)

    # -----------------------------
    # plot settings
    # -----------------------------
    x_min, x_max = float(np.min(xCoords)), float(np.max(xCoords))
    y_min, y_max = float(np.min(yCoords)), float(np.max(yCoords))
    z_min, z_max = float(np.min(zCoords)), float(np.max(zCoords))

    margin_x = 0.15 * (x_max - x_min)
    margin_y = 0.15 * (y_max - y_min)
    margin_z = 0.05 * (z_max - z_min)

    ax.set_xlim(x_min - margin_x, x_max + margin_x)
    ax.set_ylim(y_min - margin_y, y_max + margin_y)
    ax.set_zlim(z_min - margin_z, z_max + margin_z)

    ax.set_box_aspect([
        x_max - x_min,
        y_max - y_min,
        z_max - z_min
    ])

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_zlabel("Z (mm)")

    lam = eigVals[mode_id - 1]
    omega = np.sqrt(lam)
    freq = omega / (2.0 * np.pi)
    period = 1.0 / freq

    ax.set_title(f"Mode {mode_id}: T = {period:.3f} s, f = {freq:.3f} Hz")
    ax.view_init(elev=22, azim=-55)

    plt.tight_layout()
    plt.show()



def plot_2d_mode_profile(mode_id, eigVals, diaphragmMaster):
    heights = []
    phi_x = []
    phi_y = []

    for k in sorted(diaphragmMaster.keys()):
        master = diaphragmMaster[k]
        phi = ops.nodeEigenvector(master, mode_id)

        z = ops.nodeCoord(master)[2]

        heights.append(z / 1000.0)
        phi_x.append(phi[0])
        phi_y.append(phi[1])

    heights = np.array(heights)
    phi_x = np.array(phi_x)
    phi_y = np.array(phi_y)

    roof_x = abs(phi_x[-1])
    roof_y = abs(phi_y[-1])

    if roof_x >= roof_y:
        direction = "X"
        phi_main = phi_x.copy()
    else:
        direction = "Y"
        phi_main = phi_y.copy()

    max_val = np.max(np.abs(phi_main))
    if max_val > 0:
        phi_main = phi_main / max_val

    lam = eigVals[mode_id - 1]
    freq = np.sqrt(lam) / (2.0 * np.pi)
    period = 1.0 / freq

    plt.figure(figsize=(4.5, 6))
    plt.plot(phi_main, heights, "-o", lw=2, ms=5)

    for floor_id, (x, y) in enumerate(zip(phi_main, heights), start=1):
        plt.text(x + 0.01, y, f"{floor_id}", fontsize=9, ha="left", va="center")

    plt.axvline(0, color="gray", lw=0.8)
    plt.xlabel(f"Normalized {direction} mode shape")
    plt.ylabel("Height (m)")
    plt.title(f"Mode {mode_id} - {direction}-dominant")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    print(
        f"Mode {mode_id}: {direction}-dominant, "
        f"f = {freq:.3f} Hz, T = {period:.3f} s"
    )


# =========================================================
# Nonlinear EQ dynamic analysis helpers and plotting
# =========================================================
def apply_rayleigh_damping_from_modes(modal_results, zeta=0.05, mode_i=1, mode_j=3):
    """
    Use mode_i and mode_j to compute Rayleigh damping.
    modal_results[i]["omega"] should exist.
    """

    wi = modal_results[mode_i]["omega"]
    wj = modal_results[mode_j]["omega"]

    betaK = 2.0 * zeta / (wi + wj)
    alphaM = betaK * wi * wj

    # alphaM, betaK, betaKinit, betaKcomm
    ops.rayleigh(alphaM, 0.0, 0.0, betaK)

    print(f"Rayleigh damping:")
    print(f"alphaM = {alphaM:.4e}")
    print(f"betaK  = {betaK:.4e}")

    return alphaM, betaK


def run_time_history(
    gm_file,
    dt,
    n_steps,
    diaphragmMaster,
    direction=2,
    scale_factor=9810.0,
    analysis_dt=None,
    output_prefix="THA",
    min_analysis_dt=None,
    max_subdivisions=5,
    return_details=False
):
    """
    Nonlinear earthquake time-history analysis.

    gm_file:
        ground motion txt file, one acceleration value per line.
        If values are in g, use scale_factor=9810.0.
        If values are already in mm/s2, use scale_factor=1.0.

    direction:
        1 = global X
        2 = global Y
    """

    if analysis_dt is None:
        analysis_dt = dt
    if min_analysis_dt is None:
        min_analysis_dt = analysis_dt / (2 ** max_subdivisions)

    roof_story = max(diaphragmMaster.keys())
    roof_master = diaphragmMaster[roof_story]

    # -----------------------------------------------------
    # Ground motion input
    # -----------------------------------------------------
    tsTag = 1001
    patternTag = 1001

    ops.timeSeries("Path",tsTag,"-dt", dt,"-filePath", gm_file, "-factor", scale_factor)

    ops.pattern("UniformExcitation", patternTag, direction, "-accel", tsTag)

    # -----------------------------------------------------
    # Recorders
    # -----------------------------------------------------
    ops.recorder(
        "Node",
        "-file", f"{output_prefix}_roof_disp.out",
        "-time",
        "-node", roof_master,
        "-dof", direction,
        "disp"
    )

    ops.recorder(
        "Node",
        "-file", f"{output_prefix}_roof_accel.out",
        "-time",
        "-node", roof_master,
        "-dof", direction,
        "accel"
    )

    # record all diaphragm master displacements
    master_nodes = [diaphragmMaster[k] for k in sorted(diaphragmMaster.keys())]

    ops.recorder(
        "Node",
        "-file", f"{output_prefix}_master_disp.out",
        "-time",
        "-node", *master_nodes,
        "-dof", direction,
        "disp"
    )

    # base reactions
    ops.recorder(
        "Node",
        "-file", f"{output_prefix}_base_reaction.out",
        "-time",
        "-nodeRange", 1, 999999,
        "-dof", direction,
        "reaction"
    )

    # -----------------------------------------------------
    # Analysis settings
    # -----------------------------------------------------
    ops.wipeAnalysis()
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")

    # -----------------------------------------------------
    # Step-by-step analysis with adaptive fallback
    # -----------------------------------------------------
    ok = 0
    current_step = 0
    failed_time = None
    failed_dt = None
    failed_reason = None
    total_time = float(n_steps) * float(dt)
    target_steps = int(np.ceil(total_time / float(analysis_dt)))

    def try_analyze_increment(increment_dt):
        strategies = [
            ("Newton", "NormDispIncr", 1.0e-5, 50),
            ("NewtonLineSearch", "NormDispIncr", 1.0e-5, 80),
            ("ModifiedNewton", "NormDispIncr", 1.0e-5, 100),
            ("KrylovNewton", "NormDispIncr", 1.0e-5, 100),
            ("NewtonLineSearch", "EnergyIncr", 1.0e-7, 120),
            ("ModifiedNewton", "EnergyIncr", 1.0e-7, 150),
        ]

        for algorithm, test_type, tol, iterations in strategies:
            ops.test(test_type, tol, iterations)
            if algorithm == "NewtonLineSearch":
                ops.algorithm("NewtonLineSearch", "-type", "Bisection")
            else:
                ops.algorithm(algorithm)
            ok_try = ops.analyze(1, increment_dt)
            if ok_try == 0:
                return 0, f"{algorithm}/{test_type}"
        return ok_try, "all fallback algorithms failed"

    def analyze_adaptive(increment_dt, depth=0):
        ok_try, strategy = try_analyze_increment(increment_dt)
        if ok_try == 0:
            return 0, strategy

        half_dt = increment_dt / 2.0
        if depth >= max_subdivisions or half_dt < min_analysis_dt:
            return ok_try, strategy

        print(
            f"Adaptive retry: dt={increment_dt:.6g} failed, "
            f"splitting into dt={half_dt:.6g}."
        )
        ok_left, left_strategy = analyze_adaptive(half_dt, depth + 1)
        if ok_left != 0:
            return ok_left, left_strategy
        return analyze_adaptive(half_dt, depth + 1)

    for step in range(target_steps):
        remaining = total_time - ops.getTime()
        if remaining <= 1.0e-12:
            break
        increment_dt = min(float(analysis_dt), remaining)
        ok, strategy = analyze_adaptive(increment_dt)

        if ok != 0:
            failed_time = float(ops.getTime())
            failed_dt = float(increment_dt)
            failed_reason = strategy
            print(f"Dynamic analysis failed at step {step}, time {failed_time:.6f}s.")
            current_step = step
            break

        current_step = step + 1

    if ok == 0:
        print("Dynamic analysis completed.")
    else:
        print(f"Dynamic analysis stopped at step {current_step}/{n_steps}.")

    if return_details:
        return {
            "ok": int(ok),
            "completed_steps": int(current_step),
            "target_steps": int(target_steps),
            "failed_time": failed_time,
            "failed_dt": failed_dt,
            "failed_reason": failed_reason,
            "analysis_dt": float(analysis_dt),
            "min_analysis_dt": float(min_analysis_dt),
            "max_subdivisions": int(max_subdivisions),
            "end_time": float(ops.getTime()),
        }

    return ok


# -----------------------------------------------------
# Animate 3D building response from master displacement recorder
# -----------------------------------------------------
from matplotlib.animation import FuncAnimation

def animate_building_response_3d(
    master_disp_file,
    diaphragmMaster,
    story_nodes,
    column_table,
    beam_table,
    wall_table,
    direction=2,
    scale=50.0,
    frame_step=5,
    interval=40,
    save_path=None
):
    """
    direction:
        1 = X
        2 = Y

    scale:
        visual amplification factor
    """
    # -----------------------------------------------------
    # 1. Load recorder data.
    # -----------------------------------------------------
    data = np.loadtxt(master_disp_file)

    time = data[:, 0]
    disp = data[:, 1:]
    floors = sorted(diaphragmMaster.keys())
    floor_to_col = {k: i for i, k in enumerate(floors)}

    # -----------------------------------------------------
    # 2. Build node-to-story mapping.
    # -----------------------------------------------------
    node_to_story = {}
    for k, nodes_k in story_nodes.items():
        for nd in nodes_k:
            node_to_story[int(nd)] = k

    # -----------------------------------------------------
    # 3. Define the nodal deformation helper.
    # -----------------------------------------------------
    def get_deformed_coord(node_id, frame_id):
        p0 = np.array(ops.nodeCoord(node_id), dtype=float)
        
        k = node_to_story.get(int(node_id), None)

        if k is None or k == 0 or k not in floor_to_col:
            return p0

        u = disp[frame_id, floor_to_col[k]]

        p = p0.copy()

        if direction == 1:
            p[0] += scale * u
        elif direction == 2:
            p[1] += scale * u

        return p

    # -----------------------------------------------------
    # 4. Initialize the figure.
    # -----------------------------------------------------
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")

    # Compute plotting limits.
    all_nodes = ops.getNodeTags()
    coords = np.array([ops.nodeCoord(nd) for nd in all_nodes])

    x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
    y_min, y_max = coords[:, 1].min(), coords[:, 1].max()
    z_min, z_max = coords[:, 2].min(), coords[:, 2].max()

    margin_x = 0.2 * (x_max - x_min)
    margin_y = 0.2 * (y_max - y_min)
    margin_z = 0.05 * (z_max - z_min)

    # Downsample animation frames.
    frame_ids = np.arange(0, len(time), frame_step)

    # -----------------------------------------------------
    # 5. Update the animated artists for each frame.
    # -----------------------------------------------------
    def update(frame_id):

        ax.clear()

        # ===== columns =====
        for col in column_table:
            nI = int(col["nodeI"])
            nJ = int(col["nodeJ"])
            pI = get_deformed_coord(nI, frame_id)
            pJ = get_deformed_coord(nJ, frame_id)
            ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color="black",lw=1.2)

        # ===== beams =====
        for beam in beam_table:
            nI = int(beam["nodeI"])
            nJ = int(beam["nodeJ"])
            pI = get_deformed_coord(nI, frame_id)
            pJ = get_deformed_coord(nJ, frame_id)
            ax.plot([pI[0], pJ[0]],[pI[1], pJ[1]],[pI[2], pJ[2]],color="steelblue",lw=0.8,alpha=0.8)

        # ===== walls =====
        for wall in wall_table:
            nodes = [int(wall["node1"]),int(wall["node2"]),int(wall["node3"]),int(wall["node4"])]

            pts = np.array([get_deformed_coord(nd, frame_id) for nd in nodes])

            poly = Poly3DCollection(
                [pts],
                facecolor="red",
                alpha=0.25,
                edgecolor="red",
                linewidth=0.5
            )
            ax.add_collection3d(poly)

        # ===== axes =====
        ax.set_xlim(x_min - margin_x, x_max + margin_x)
        ax.set_ylim(y_min - margin_y, y_max + margin_y)
        ax.set_zlim(z_min - margin_z, z_max + margin_z)

        ax.set_box_aspect([
            x_max - x_min,
            y_max - y_min,
            z_max - z_min
        ])

        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_zlabel("Z (mm)")

        ax.set_title(f"Time = {time[frame_id]:.2f} s")
        ax.view_init(elev=22, azim=-55)

    # -----------------------------------------------------
    # 6. Create the animation object.
    # -----------------------------------------------------
    ani = FuncAnimation(
        fig,
        update,
        frames=frame_ids,
        interval=interval,
        blit=False
    )

    # Save the animation when a path is provided.
    if save_path is not None:
        ani.save(save_path, fps=1000 // interval)

    plt.show()

    return ani



def load_master_disp(master_disp_file, diaphragmMaster):
    data = np.loadtxt(master_disp_file)
    time = data[:, 0]
    disp = data[:, 1:]  # each column = one floor master displacement
    floors = sorted(diaphragmMaster.keys())
    return time, disp, floors

def compute_idr_from_master_disp(master_disp_file, diaphragmMaster, zCoords):
    time, disp, floors = load_master_disp(master_disp_file, diaphragmMaster)
    idr = {}
    for i, k in enumerate(floors):
        z_top = zCoords[k]
        if k - 1 == 0:
            u_bot = 0.0
            z_bot = zCoords[0]
        else:
            j = floors.index(k - 1)
            u_bot = disp[:, j]
            z_bot = zCoords[k - 1]
        u_top = disp[:, i]
        storyH = z_top - z_bot
        idr[k] = (u_top - u_bot) / storyH

    return time, idr

def plot_dynamic_response(master_disp_file, diaphragmMaster, zCoords):
    time, disp, floors = load_master_disp(master_disp_file, diaphragmMaster)
    roof_disp = disp[:, -1]
    plt.figure(figsize=(7, 3.5))
    plt.plot(time, roof_disp, lw=1.5)
    plt.xlabel("Time (s)")
    plt.ylabel("Roof displacement (mm)")
    plt.title("Roof displacement time history")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
    time, idr = compute_idr_from_master_disp(master_disp_file,diaphragmMaster,zCoords)
    idr_matrix = np.column_stack([idr[k] for k in sorted(idr.keys())])
    max_abs_idr_t = np.max(np.abs(idr_matrix), axis=1) * 100.0 
    plt.figure(figsize=(7, 3.5))
    plt.plot(time, max_abs_idr_t, lw=1.5)
    plt.xlabel("Time (s)")
    plt.ylabel("Max |IDR| %")
    plt.title("Maximum inter-story drift ratio time history")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
    print("Peak roof displacement =", np.max(np.abs(roof_disp)), "mm")
    print("Peak max IDR =", np.max(max_abs_idr_t))
    return {
        "time": time,
        "roof_disp": roof_disp,
        "idr": idr,
        "max_abs_idr_t": max_abs_idr_t
    }

def plot_peak_idr_profile(master_disp_file, diaphragmMaster, zCoords):
    time, idr = compute_idr_from_master_disp(
        master_disp_file,
        diaphragmMaster,
        zCoords
    )
    floors = sorted(idr.keys())
    peak_idr = np.array([np.max(np.abs(idr[k])) for k in floors])
    heights = np.array([zCoords[k] / 1000.0 for k in floors])
    plt.figure(figsize=(4.5, 6))
    plt.plot(peak_idr, heights, "-o", lw=2)
    for k, x, y in zip(floors, peak_idr, heights):
        plt.text(x + 0.0002, y, f"{k}", fontsize=9, va="center")
    plt.xlabel("Peak |IDR|")
    plt.ylabel("Height (m)")
    plt.title("Peak inter-story drift ratio profile")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
    return peak_idr

def check_collapse_by_idr(master_disp_file, diaphragmMaster, zCoords, collapse_idr=0.04):
    time, idr = compute_idr_from_master_disp(
        master_disp_file,
        diaphragmMaster,
        zCoords
    )
    floors = sorted(idr.keys())
    peak_idr = {k: np.max(np.abs(idr[k])) for k in floors}
    global_peak_idr = max(peak_idr.values())
    critical_story = max(peak_idr, key=peak_idr.get)
    collapsed = global_peak_idr >= collapse_idr
    print("========== Collapse check ==========")
    print(f"Peak IDR = {global_peak_idr:.4f}")
    print(f"Critical story = {critical_story}")
    print(f"Collapse threshold = {collapse_idr:.4f}")
    if collapsed:
        print("Result: Collapse / near-collapse indicated")
    else:
        print("Result: No collapse indicated by IDR criterion")
    return {
        "collapsed": collapsed,
        "peak_idr": peak_idr,
        "global_peak_idr": global_peak_idr,
        "critical_story": critical_story,
        "collapse_idr": collapse_idr
    }






import matplotlib.gridspec as gridspec

def animate_building_response_3d_with_timeseries(
    master_disp_file,
    gm_file,
    gm_dt,
    diaphragmMaster,
    story_nodes,
    column_table,
    beam_table,
    wall_table,
    direction=2,
    scale=50.0,
    frame_step=5,
    interval=40,
    gm_scale_factor=1.0,
    save_path=None
):
    # =========================
    # 1. Load response data
    # =========================
    data = np.loadtxt(master_disp_file)
    time = data[:, 0]
    disp = data[:, 1:]

    floors = sorted(diaphragmMaster.keys())
    floor_to_col = {k: i for i, k in enumerate(floors)}

    roof_story = max(floors)
    roof_col = floor_to_col[roof_story]
    roof_disp = disp[:, roof_col]

    # =========================
    # 2. Load ground motion
    # =========================
    gm = np.loadtxt(gm_file).flatten() * gm_scale_factor
    gm_time = np.arange(len(gm)) * gm_dt

    # =========================
    # 3. node -> story mapping
    # =========================
    node_to_story = {}
    for k, nodes_k in story_nodes.items():
        for nd in nodes_k:
            node_to_story[int(nd)] = k

    def get_deformed_coord(node_id, frame_id):
        p0 = np.array(ops.nodeCoord(node_id), dtype=float)

        k = node_to_story.get(int(node_id), None)
        if k is None or k == 0 or k not in floor_to_col:
            return p0

        u = disp[frame_id, floor_to_col[k]]

        p = p0.copy()
        if direction == 1:
            p[0] += scale * u
        elif direction == 2:
            p[1] += scale * u

        return p

    # =========================
    # 4. Figure layout
    # =========================
    fig = plt.figure(figsize=(13, 7))
    gs = gridspec.GridSpec(
        2, 2,
        width_ratios=[1.1, 2.0],
        height_ratios=[1, 1],
        wspace=0.25,
        hspace=0.35
    )

    ax_roof = fig.add_subplot(gs[0, 0])
    ax_gm = fig.add_subplot(gs[1, 0])
    ax3d = fig.add_subplot(gs[:, 1], projection="3d")

    # =========================
    # 5. Axis limits
    # =========================
    all_nodes = ops.getNodeTags()
    coords = np.array([ops.nodeCoord(nd) for nd in all_nodes])

    x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
    y_min, y_max = coords[:, 1].min(), coords[:, 1].max()
    z_min, z_max = coords[:, 2].min(), coords[:, 2].max()

    margin_x = 0.2 * (x_max - x_min)
    margin_y = 0.2 * (y_max - y_min)
    margin_z = 0.05 * (z_max - z_min)

    frame_ids = np.arange(0, len(time), frame_step)

    # =========================
    # 6. Static time-history curves
    # =========================
    ax_roof.plot(time, roof_disp, lw=1.2)
    roof_marker, = ax_roof.plot([], [], "o", ms=5)

    ax_roof.set_xlabel("Time (s)")
    ax_roof.set_ylabel("Roof disp. (mm)")
    ax_roof.set_title("Roof displacement")
    ax_roof.grid(True, alpha=0.3)

    ax_gm.plot(gm_time, gm, lw=1.2)
    gm_marker, = ax_gm.plot([], [], "o", ms=5)

    ax_gm.set_xlabel("Time (s)")
    ax_gm.set_ylabel("Ground acc.")
    ax_gm.set_title("Ground motion input")
    ax_gm.grid(True, alpha=0.3)

    # =========================
    # 7. Animation update
    # =========================
    def update(frame_id):
        ax3d.clear()

        t_now = time[frame_id]

        # roof displacement marker
        roof_marker.set_data([time[frame_id]], [roof_disp[frame_id]])

        # ground motion marker
        gm_idx = int(round(t_now / gm_dt))
        gm_idx = min(max(gm_idx, 0), len(gm) - 1)
        gm_marker.set_data([gm_time[gm_idx]], [gm[gm_idx]])

        # columns
        for col in column_table:
            nI = int(col["nodeI"])
            nJ = int(col["nodeJ"])

            pI = get_deformed_coord(nI, frame_id)
            pJ = get_deformed_coord(nJ, frame_id)

            ax3d.plot(
                [pI[0], pJ[0]],
                [pI[1], pJ[1]],
                [pI[2], pJ[2]],
                color="black",
                lw=1.2
            )

        # beams
        for beam in beam_table:
            nI = int(beam["nodeI"])
            nJ = int(beam["nodeJ"])

            pI = get_deformed_coord(nI, frame_id)
            pJ = get_deformed_coord(nJ, frame_id)

            ax3d.plot(
                [pI[0], pJ[0]],
                [pI[1], pJ[1]],
                [pI[2], pJ[2]],
                color="steelblue",
                lw=0.8,
                alpha=0.8
            )

        # walls
        for wall in wall_table:
            nodes = [
                int(wall["node1"]),
                int(wall["node2"]),
                int(wall["node3"]),
                int(wall["node4"])
            ]

            pts = np.array([
                get_deformed_coord(nd, frame_id)
                for nd in nodes
            ])

            poly = Poly3DCollection(
                [pts],
                facecolor="red",
                alpha=0.25,
                edgecolor="red",
                linewidth=0.5
            )
            ax3d.add_collection3d(poly)

        ax3d.set_xlim(x_min - margin_x, x_max + margin_x)
        ax3d.set_ylim(y_min - margin_y, y_max + margin_y)
        ax3d.set_zlim(z_min - margin_z, z_max + margin_z)

        ax3d.set_box_aspect([
            x_max - x_min,
            y_max - y_min,
            z_max - z_min
        ])

        ax3d.set_xlabel("X (mm)")
        ax3d.set_ylabel("Y (mm)")
        ax3d.set_zlabel("Z (mm)")
        ax3d.set_title(f"3D dynamic response, t = {t_now:.2f} s")
        ax3d.view_init(elev=22, azim=-55)

        return roof_marker, gm_marker

    ani = FuncAnimation(
        fig,
        update,
        frames=frame_ids,
        interval=interval,
        blit=False
    )

    if save_path is not None:
        ani.save(save_path, fps=1000 // interval)

    plt.show()

    return ani



# %%
def build_opensees_model(
    node_table,
    column_table,
    beam_table,
    wall_table,
    xCoords,
    yCoords,
    zCoords,
    add_mass=True,
    add_expansion_joint_springs=True,
    expansion_joint_stiffness=9.0e3,
    plot_model=True,
    gravity_loads=None
):
    """
    automatic OpenSees model builder based on: node_table + column_table + beam_table + wall_table.
    """

    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)

    # -----------------------------------------------------
    # 1. Create nodes and base fixity
    # -----------------------------------------------------
    for nd in node_table:
        tag = int(nd["nodeTag"])
        x = float(nd["x"])
        y = float(nd["y"])
        z = float(nd["z"])

        ops.node(tag, x, y, z)

        if nd.get("is_fixed", False):
            ops.fix(tag, 1, 1, 1, 1, 1, 1)

    print("Created nodes:", len(node_table))

    # -----------------------------------------------------
    # 2. Geometric transformations
    # -----------------------------------------------------
    ops.geomTransf("PDelta", 1, 0, 1, 0)   # columns
    ops.geomTransf("Linear", 11, 0, 0, 1)  # beams


    # -----------------------------------------------------
    # 3. Columns: independent fiber section per element
    # -----------------------------------------------------
    for col in column_table:
        tags = define_component_rc_section(col)
        ops.element("forceBeamColumn",int(col["eleTag"]),int(col["nodeI"]),int(col["nodeJ"]),1,tags["intTag"])   # 1 represents Geometric Transformation Tag 

    print("Created columns:", len(column_table))

    # -----------------------------------------------------
    # 4. Beams: elastic equivalent slab-band beams
    # -----------------------------------------------------
    for beam in beam_table:
        ops.element("elasticBeamColumn",int(beam["eleTag"]),int(beam["nodeI"]),int(beam["nodeJ"]),float(beam["A"]),
                    float(beam["Ec"]),float(beam["G"]),float(beam["J"]),float(beam["Iy"]),float(beam["Iz"]),11)  # 11 represents Geometric Transformation Tag)

    print("Created beams:", len(beam_table))

    # -----------------------------------------------------
    # 5. Walls: MVLEM_3D
    # -----------------------------------------------------
    for wall in wall_table:
        tags = define_wall_materials_and_get_tags(wall)
        m_wall = int(wall["m"])
        twall = float(wall["t"])
        wallLength = float(wall["wallLength"])
        rho_boundary = float(wall["rho_boundary"])
        rho_web = float(wall["rho_web"])

        thicks_wall = [twall] * m_wall
        widths_wall = [wallLength / m_wall] * m_wall
        rhos_wall = ([rho_boundary, rho_boundary] + [rho_web]*(m_wall-4) + [rho_boundary, rho_boundary])
        concTags_wall = ([tags["coreTag"], tags["coreTag"]] + [tags["coverTag"]]*(m_wall-4) + [tags["coreTag"], tags["coreTag"]])
        steelTags_wall = [tags["steelTag"]] * m_wall

        ops.element(
            "MVLEM_3D", int(wall["eleTag"]),
            int(wall["node1"]),int(wall["node2"]),int(wall["node3"]),int(wall["node4"]),m_wall,
            "-thick", *thicks_wall,
            "-width", *widths_wall,
            "-rho", *rhos_wall,
            "-matConcrete", *concTags_wall,
            "-matSteel", *steelTags_wall,
            "-matShear", tags["shearTag"]
        )

    print("Created walls:", len(wall_table))

    # -----------------------------------------------------
    # 6. Story node grouping
    # -----------------------------------------------------
    story_nodes = get_story_nodes_from_table(node_table, zCoords)

    # -----------------------------------------------------
    # 7. Rigid diaphragm master nodes
    # -----------------------------------------------------
    diaphragmMaster = {}

    master_start = 900000

    x_center = 0.5 * (np.min(xCoords) + np.max(xCoords))
    y_center = 0.5 * (np.min(yCoords) + np.max(yCoords))

    for k in range(1, len(zCoords)):
        slave_nodes = story_nodes.get(k, [])

        if len(slave_nodes) < 2:
            continue

        master = master_start + k
        diaphragmMaster[k] = master

        z = float(zCoords[k])

        ops.node(master, x_center, y_center, z)
        # vertical translation and rotations about X/Y fixed
        ops.fix(master, 0, 0, 1, 1, 1, 0)

        ops.rigidDiaphragm(3, master, *slave_nodes)
    print("Created diaphragm masters:", len(diaphragmMaster))

    # -----------------------------------------------------
    # 8. Gravity loading and Mass assignment to diaphragm master nodes
    # -----------------------------------------------------
    gravity_loads = gravity_loads or {}
    q_dead = float(gravity_loads.get("q_dead", 5.0))
    q_live = float(gravity_loads.get("q_live", 2.5))
    psi = float(gravity_loads.get("psi", 0.3))
    dead_factor = float(gravity_loads.get("dead_factor", 1.0))
    live_factor = float(gravity_loads.get("live_factor", 1.0))
    gravity_info = apply_gravity_loads(
        node_table=node_table,
        zCoords=zCoords,
        xCoords=xCoords,
        yCoords=yCoords,
        q_dead=q_dead,
        q_live=q_live,
        psi=psi,
        q_gravity=dead_factor * q_dead + live_factor * q_live,
        q_seismic=q_dead + psi * q_live,
        patternTag=1,
    )
    

    if add_mass:
        # OpenSees N-mm-s unit:
        # mass = W / g, unit = N / (mm/s2) = N*s2/mm
        WEQ_floor = gravity_info["WEQ_floor"]
        g = 9810.0  # mm/s^2
        m_floor = WEQ_floor / g

        Lx_total = float(np.max(xCoords) - np.min(xCoords))
        Ly_total = float(np.max(yCoords) - np.min(yCoords))
        Izz = m_floor * (Lx_total**2 + Ly_total**2) / 12.0

        for k, master in diaphragmMaster.items():
            ops.mass(master, m_floor, m_floor, 1e-9, 0.0, 0.0, Izz)

        print(f"Mass per floor master = {m_floor:.4e} N*s2/mm")

    # -----------------------------------------------------
    # 9. Expansion-joint weak springs at X boundaries
    # -----------------------------------------------------
    expansion_joint_spring_count = 0
    if add_expansion_joint_springs:
        springNodeTag = 820000
        springEleTag = 850000
        springMatTag = 880000

        x_min = float(np.min(xCoords))
        x_max = float(np.max(xCoords))

        k_joint_total_per_side_per_floor = float(expansion_joint_stiffness)  # N/mm

        for k in range(1, len(zCoords)):
            nodes_k = story_nodes.get(k, [])
            if len(nodes_k) == 0:
                continue

            left_nodes = []
            right_nodes = []

            for nd in nodes_k:
                x, y, z = ops.nodeCoord(nd)

                if abs(x - x_min) < 1e-3:
                    left_nodes.append(nd)

                if abs(x - x_max) < 1e-3:
                    right_nodes.append(nd)

            boundary_nodes = left_nodes + right_nodes

            if len(boundary_nodes) == 0:
                continue

            k_node = k_joint_total_per_side_per_floor / max(len(left_nodes), 1)

            matTag_k = springMatTag + k
            ops.uniaxialMaterial("Elastic", matTag_k, k_node)

            for nd in boundary_nodes:
                x, y, z = ops.nodeCoord(nd)

                ghost = springNodeTag
                ops.node(ghost, x, y, z)
                ops.fix(ghost, 1, 1, 1, 1, 1, 1)

                ops.element(
                    "zeroLength",
                    springEleTag,
                    nd, ghost,
                    "-mat", matTag_k,
                    "-dir", 1
                )

                springNodeTag += 1
                springEleTag += 1
                expansion_joint_spring_count += 1

        print("Expansion-joint weak X springs added:", expansion_joint_spring_count)

    if plot_model:
        plot_opensees_model(
            node_table=node_table,
            column_table=column_table,
            beam_table=beam_table,
            wall_table=wall_table,
            xCoords=xCoords,
            yCoords=yCoords,
            zCoords=zCoords,
            show_nodes=True,
            show_slabs=True
        )

    run_gravity_analysis(n_steps=10)



    return {
        "story_nodes": story_nodes,
        "diaphragmMaster": diaphragmMaster,
        "expansion_joint": {
            "enabled": bool(add_expansion_joint_springs),
            "stiffness_N_per_mm_per_side_per_floor": float(expansion_joint_stiffness),
            "spring_count": int(expansion_joint_spring_count),
        },
    }


# # %%
# # =========================================================
# # 10. Build model
# # =========================================================
# # Read IFC file, extract grid coordinates and storey elevations, and transform them to the local coordinate system.
# ifcFile = ifcopenshell.open("HUG_unitB.ifc")

# xCoords_raw, yCoords_raw = get_grid_xy_compatible(ifcFile)
# zCoords_raw = get_storey_elevations_compatible(ifcFile, use_world=True)
# xCoords_raw = clean_sorted_coords(xCoords_raw)
# yCoords_raw = clean_sorted_coords(yCoords_raw)
# zCoords_raw = clean_sorted_coords(zCoords_raw)
# x0 = float(np.min(xCoords_raw))
# y0 = float(np.min(yCoords_raw))
# xCoords = xCoords_raw - x0
# yCoords = yCoords_raw - y0
# zCoords = np.array([z for z in zCoords_raw if z >= -1e-6], dtype=float)
# print("Local xCoords:", xCoords)
# print("Local yCoords:", yCoords)
# print("Local zCoords:", zCoords)



# candidate_node_xyz, candidate_node_index = build_candidate_node_pool(xCoords, yCoords, zCoords)
# print("Candidate nodes:", len(candidate_node_xyz))


# columns = ifcFile.by_type("IfcColumn")
# beams = ifcFile.by_type("IfcBeam")
# walls = ifcFile.by_type("IfcWall")
# # p_bot, p_top = read_column_endpoints(columns[10])
# # p_start, p_end = read_beam_endpoints(beams[0])
# # p1, p2, p3, p4 = read_wall_corners(walls[20])



# column_table, column_nodes, skipped_columns = build_column_table(
#     columns,
#     candidate_node_xyz,
#     snap_tol=1000.0
# )
# print("Columns:", len(column_table))

# beam_table, beam_nodes, skipped_beams = build_beam_table(
#     beams,
#     candidate_node_xyz,
#     start_eleTag=len(column_table) + 1,
#     snap_tol=800
# )
# print("Beams:", len(beam_table))

# wall_table, wall_nodes, skipped_walls = build_wall_table(
#     walls,
#     candidate_node_xyz,
#     start_eleTag=len(column_table) + len(beam_table) + 1,
#     snap_tol=1200
# )
# print("Walls:", len(wall_table))



# used_nodes = set()
# used_nodes.update(column_nodes)
# used_nodes.update(beam_nodes)
# used_nodes.update(wall_nodes)
# node_table = []
# for tag in sorted(used_nodes):
#     p = candidate_node_xyz[tag]
#     node_table.append({
#         "nodeTag": tag,
#         "x": p[0],
#         "y": p[1],
#         "z": p[2],
#         "is_fixed": abs(p[2]) < 1e-6
#     })
# print("Nodes:", len(node_table))
# print("Columns:", len(column_table))
# print("Beams:", len(beam_table))
# print("Walls:", len(wall_table))
# print("Skipped columns:", len(skipped_columns))
# print("Skipped beams:", len(skipped_beams))
# print("Skipped walls:", len(skipped_walls))

# # out_dir = "HUG_unitB_tables"
# # import os
# # os.makedirs(out_dir, exist_ok=True)
# # node_df = pd.DataFrame([
# #     {
# #         "nodeTag": tag,
# #         "x": xyz[0],
# #         "y": xyz[1],
# #         "z": xyz[2],
# #     }
# #     for tag, xyz in candidate_node_xyz.items()
# # ])
# # column_df = pd.DataFrame(column_table)
# # beam_df   = pd.DataFrame(beam_table)
# # wall_df   = pd.DataFrame(wall_table)

# # node_df.to_csv(os.path.join(out_dir, "node_table.csv"), index=False)
# # column_df.to_csv(os.path.join(out_dir, "column_table.csv"), index=False)
# # beam_df.to_csv(os.path.join(out_dir, "beam_table.csv"), index=False)
# # wall_df.to_csv(os.path.join(out_dir, "wall_table.csv"), index=False)


# model_info = build_opensees_model(
#     node_table=node_table,
#     column_table=column_table,
#     beam_table=beam_table,
#     wall_table=wall_table,
#     xCoords=xCoords,
#     yCoords=yCoords,
#     zCoords=zCoords,
#     add_mass=True,
#     add_expansion_joint_springs=True,
#     plot_model=True
# )

# # %%
# # =========================================================
# # 11. Run modal analysis
# # =========================================================
# story_nodes = model_info["story_nodes"]
# diaphragmMaster = model_info["diaphragmMaster"]

# eigVals, modal_results = run_modal_analysis(numModes=6)

# for mode_id in range(1, 7):
#     plot_mode_shape_3d(
#         mode_id=mode_id,
#         eigVals=eigVals,
#         column_table=column_table,
#         beam_table=beam_table,
#         wall_table=wall_table,
#         xCoords=xCoords,
#         yCoords=yCoords,
#         zCoords=zCoords,
#         scale=100000.0
#     )

# for mode_id in range(1, 7):
#     plot_2d_mode_profile(
#         mode_id=mode_id,
#         eigVals=eigVals,
#         diaphragmMaster=diaphragmMaster
#     )



# # %%
# # =========================================================
# # 11. Run EQ analysis
# # =========================================================
# apply_rayleigh_damping_from_modes(modal_results,zeta=0.05,mode_i=1,mode_j=3)

# ok = run_time_history(
#     gm_file="gm_0p1g.txt",
#     dt=0.005,
#     n_steps=10000,
#     diaphragmMaster=model_info["diaphragmMaster"],
#     direction=2,
#     scale_factor=9810.0,
#     analysis_dt=0.005,
#     output_prefix="EQ_Y"
# )


# response = plot_dynamic_response(
#     master_disp_file="EQ_Y_master_disp.out",
#     diaphragmMaster=model_info["diaphragmMaster"],
#     zCoords=zCoords
# )

# peak_idr = plot_peak_idr_profile(
#     master_disp_file="EQ_Y_master_disp.out",
#     diaphragmMaster=model_info["diaphragmMaster"],
#     zCoords=zCoords
# )

# collapse_info = check_collapse_by_idr(
#     master_disp_file="EQ_Y_master_disp.out",
#     diaphragmMaster=model_info["diaphragmMaster"],
#     zCoords=zCoords,
#     collapse_idr=0.02
# )


# # %%
# ani = animate_building_response_3d(
#     master_disp_file="EQ_Y_master_disp.out",
#     diaphragmMaster=model_info["diaphragmMaster"],
#     story_nodes=model_info["story_nodes"],
#     column_table=column_table,
#     beam_table=beam_table,
#     wall_table=wall_table,
#     direction=2,
#     scale=50.0,
#     frame_step=10,
#     interval=50,
#     save_path="HUG_EQ.gif"
# )

# # %%
# # %%
# gm = np.loadtxt("gm_1g.txt")

# dt = 0.005  # time step of EQ, e.g., 0.005, 0.01, 0.02
# t = np.arange(len(gm)) * dt

# plt.figure(figsize=(10, 4))
# plt.plot(t, gm, linewidth=0.8)
# plt.xlabel("Time (s)")
# plt.ylabel("Acceleration")
# plt.title("Ground Motion Time History")
# plt.grid(True, alpha=0.3)
# plt.tight_layout()
# plt.show()

# print("min =", gm.min())
# print("max =", gm.max())
# # %%
