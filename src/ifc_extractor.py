import math
import os
import sys
import ifcopenshell.util.element
import ifcopenshell

def unwrap_value(v):
    if v is None:
        return None
    if hasattr(v, "wrappedValue"):
        return v.wrappedValue
    return v

def safe_float(x):
    x = unwrap_value(x)
    try:
        return float(x)
    except Exception:
        return None

def safe_int(x):
    x = safe_float(x)
    if x is None:
        return None
    try:
        return int(round(x))
    except Exception:
        return None

def get_psets_and_qtos(element):
    data = {}
    rels = getattr(element, "IsDefinedBy", []) or []
    for rel in rels:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pdef = rel.RelatingPropertyDefinition
        if pdef and pdef.is_a("IfcElementQuantity"):
            qset_name = getattr(pdef, "Name", None) or "UnnamedQuantitySet"
            qdict = data.setdefault(str(qset_name), {})
            for q in (pdef.Quantities or []):
                qname = getattr(q, "Name", None)
                if not qname:
                    continue
                if hasattr(q, "LengthValue"):
                    qdict[str(qname)] = unwrap_value(q.LengthValue)
                elif hasattr(q, "AreaValue"):
                    qdict[str(qname)] = unwrap_value(q.AreaValue)
                elif hasattr(q, "VolumeValue"):
                    qdict[str(qname)] = unwrap_value(q.VolumeValue)
                elif hasattr(q, "CountValue"):
                    qdict[str(qname)] = unwrap_value(q.CountValue)
                else:
                    qdict[str(qname)] = None
        elif pdef and pdef.is_a("IfcPropertySet"):
            pset_name = getattr(pdef, "Name", None) or "UnnamedPropertySet"
            pdict = data.setdefault(str(pset_name), {})
            for prop in (pdef.HasProperties or []):
                pname = getattr(prop, "Name", None)
                if not pname:
                    continue
                if prop.is_a("IfcPropertySingleValue"):
                    pdict[str(pname)] = unwrap_value(getattr(prop, "NominalValue", None))
                else:
                    pdict[str(pname)] = None
    return data

def pick_dim(psets, keys):
    want = {k.lower() for k in keys}
    for setname, props in psets.items():
        for k, v in props.items():
            if k and k.lower() in want:
                fv = safe_float(v)
                if fv is not None:
                    return fv, setname, k
    return None, None, None

def find_numeric(psets, candidate_keys):
    want = {k.lower() for k in candidate_keys}
    for setname, props in psets.items():
        for k, v in props.items():
            if k and k.lower() in want:
                fv = safe_float(v)
                if fv is not None:
                    return fv, setname, k
    return None, None, None

def find_int(psets, candidate_keys):
    v, s, k = find_numeric(psets, candidate_keys)
    if v is None:
        return None, None, None
    try:
        return int(round(v)), s, k
    except Exception:
        return None, None, None

def fmt_bool(x):
    if x is True:
        return "PASS"
    if x is False:
        return "FAIL"
    if x is None:
        return "N/A"
    return str(x)

def parent_stair_id(flight):
    for rel in getattr(flight, "Decomposes", []) or []:
        obj = getattr(rel, "RelatingObject", None)
        if obj is not None and obj.is_a("IfcStair"):
            return obj.id()
    return None

def get_storey_of_stair(stair, model):
    """Return the IfcBuildingStorey id that contains the given IfcStair, or None."""
    try:
        for rel in getattr(stair, "ContainedInStructure", []):
            if rel.is_a("IfcRelContainedInStructure"):
                relating_structure = rel.RelatingStructure
                if relating_structure and relating_structure.is_a("IfcBuildingStorey"):
                    return relating_structure.id()
    except Exception:
        pass

    for rel in model.by_type("IfcRelContainedInStructure"):
        try:
            if stair in getattr(rel, "RelatedElements", []):
                relating_structure = rel.RelatingStructure
                if relating_structure and relating_structure.is_a("IfcBuildingStorey"):
                    return relating_structure.id()
        except Exception:
            continue
    return None

def get_parent_storey(stair, model):
    for rel in getattr(stair, "Decomposes", []) or []:
        obj = getattr(rel, "RelatingObject", None)
        if obj and obj.is_a("IfcBuildingStorey"):
            return obj.id()
    for rel in getattr(stair, "ContainedInStructure", []) or []:
        if rel.is_a("IfcRelContainedInStructure"):
            relating_structure = rel.RelatingStructure
            if relating_structure and relating_structure.is_a("IfcBuildingStorey"):
                return relating_structure.id()
    return None

def get_containing_storey(element, model):
    from ifcopenshell.util.element import get_container
    container = get_container(element)
    while container and not container.is_a("IfcBuildingStorey"):
        container = get_container(container)
    if container and container.is_a("IfcBuildingStorey"):
        return container.id()
    return None

def classify_railing(name, object_type, height_mm):
    text = f"{name or ''} {object_type or ''}".lower()
    if "1100" in text or "guard" in text:
        return "guardrail"
    if "900" in text or "handrail" in text or "pipe" in text:
        return "handrail"
    if height_mm is None:
        return "unknown"
    return "guardrail" if height_mm >= 1050.0 else "handrail"

def safe_by_type(model, ifc_type):
    try:
        return model.by_type(ifc_type)
    except RuntimeError:
        return []

def main(ifc_path):
    try:
        model = ifcopenshell.open(ifc_path)
    except Exception as e:
        print(f"ERROR: Failed to open IFC file: {e}")
        sys.exit(1)

    stairs = safe_by_type(model, "IfcStair")
    flights = safe_by_type(model, "IfcStairFlight")

    n_stairs = len(stairs)
    n_flights = len(flights)
    staircase_estimate = n_stairs if n_stairs > 0 else n_flights

    print("=======================================")
    print(f"IFC file: {ifc_path}")
    print("---------------------------------------")
    print(f"IfcStair count       : {n_stairs}")
    print(f"IfcStairFlight count : {n_flights}")
    print("---------------------------------------")
    print(f"Staircase estimate   : {staircase_estimate}")
    print("=======================================")

    if n_stairs > 0:
        print("\nIfcStair objects:")
        for s in stairs:
            name = getattr(s, "Name", None)
            print(f"  #{s.id()}  Name={name}")
    elif n_flights > 0:
        print("\nIfcStairFlight objects (no IfcStair found):")
        for f in flights:
            name = getattr(f, "Name", None)
            print(f"  #{f.id()}  Name={name}")

    if not flights:
        print("No IfcStairFlight found.")
        return

    print(f"IFC: {ifc_path}")
    print(f"Found IfcStairFlight: {len(flights)}")
    print("------------------------------------------------------------")

    stair_lookup = {s.id(): (getattr(s, "Name", None), getattr(s, "Tag", None)) for s in stairs}
    flights_by_stair = {}
    flight_parent = {}
    for f in flights:
        sid = parent_stair_id(f)
        flight_parent[f.id()] = sid
        if sid is not None:
            flights_by_stair.setdefault(sid, []).append(f.id())

    printed_storey = set()
    for stair in stairs:
        sid = stair.id()
        storey_id = get_parent_storey(stair, model)
        if storey_id is None:
            flight_ids = flights_by_stair.get(sid, [])
            for fid in flight_ids:
                flight = model.by_id(fid) if fid else None
                if flight:
                    storey_id = get_containing_storey(flight, model)
                    if storey_id is not None:
                        break
        if storey_id is not None and sid not in printed_storey:
            print(f"STOREY_PARENT: stair_id={sid} storey_id={storey_id}")
            printed_storey.add(sid)

    # Also output storey for flights (optional)
    for flight in flights:
        fid = flight.id()
        storey_id = get_containing_storey(flight, model)
        if storey_id is not None:
            print(f"FLIGHT_STOREY: flight_id={fid} storey_id={storey_id}")

    height_keys = ["RiserHeight"]
    depth_keys  = ["TreadLength"]

    for i, f in enumerate(flights, start=1):
        fid = f.id()
        name = getattr(f, "Name", None)
        tag = getattr(f, "Tag", None)
        psets = get_psets_and_qtos(f)
        H, Hset, Hkey = pick_dim(psets, height_keys)
        D, Dset, Dkey = pick_dim(psets, depth_keys)
        print(f"[{i}] IfcStairFlight  #{fid}  Name={name}  Tag={tag}")
        def show(val, src_set, src_key, label):
            if val is not None:
                print(f"    {label:<6}: {val:.6g}  (from {src_set}.{src_key})")
            else:
                print(f"    {label:<6}: None")
        show(H, Hset, Hkey, "Height")
        show(D, Dset, Dkey, "Depth")

    RISER_KEYS = ["RiserHeight"]
    TREAD_KEYS = ["TreadLength"]
    NOSING_KEYS = ["NosingLength", "Overlap", "Nosing"]
    N_RISER_KEYS = ["NumberOfRiser", "NumberOfRisers"]

    for idx, f in enumerate(flights, start=1):
        fid = f.id()
        name = getattr(f, "Name", None)
        tag = getattr(f, "Tag", None)
        psets = get_psets_and_qtos(f)

        h, hs, hk = find_numeric(psets, RISER_KEYS)
        g, gs, gk = find_numeric(psets, TREAD_KEYS)
        r, rs, rk = find_numeric(psets, NOSING_KEYS)
        n_risers, nrs, nrk = find_int(psets, N_RISER_KEYS)

        check1 = None
        check2 = None
        sum_rule = None
        if g is not None and h is not None:
            sum_rule = g + 2.0 * h
            check1 = (600.0 <= sum_rule <= 660.0)
        else:
            check1 = None

        if g is not None:
            check2 = (210.0 <= g <= 310.0)
        else:
            check2 = None

        alpha = None
        check3 = None
        if g is not None and h is not None and g > 0.0:
            alpha = math.degrees(math.atan(h / g))
            check3 = (20.0 < alpha <= 45.0)

        check4 = None
        if r is not None:
            check4 = (r >= 10.0)

        sum_rule_local = None
        check5 = None
        if h is not None and g is not None:
            sum_rule_local = (2.0 * h) + g
            check5 = (480.0 <= sum_rule_local <= 800.0)

        check6 = (n_risers >= 3) if n_risers is not None else None
        check7 = (n_risers <= 16) if n_risers is not None else None
        check8 = (n_risers <= 22) if n_risers is not None else None

        climb_h = None
        if h is not None and n_risers is not None:
            climb_h = h * n_risers
        sid = flight_parent.get(fid)
        flights_in_parent = len(flights_by_stair.get(sid, [])) if sid is not None else None
        check9 = None
        check10 = None
        if climb_h is not None and flights_in_parent is not None:
            if flights_in_parent <= 1:
                check9 = (climb_h <= 4000.0)
            else:
                check10 = (climb_h <= 3000.0)

        print(f"[{idx}] IfcStairFlight #{fid}  Name={name}  Tag={tag}")
        if sid is not None:
            stair_name, stair_tag = stair_lookup.get(sid, (None, None))
            print(f"  Parent stair      = #{sid}  Name={stair_name}  Tag={stair_tag}  (flights in stair: {flights_in_parent})")

        if h is not None:
            src = f"{hs}.{hk}" if hs and hk else "unknown"
            print(f"  h (riser height) = {h:.12g} mm   (from {src})")
        else:
            print("  h (riser height) = N/A")

        if g is not None:
            src = f"{gs}.{gk}" if gs and gk else "unknown"
            print(f"  g (tread/going)  = {g:.12g} mm   (from {src})")
        else:
            print("  g (tread/going)  = N/A")

        if sum_rule is not None:
            print(f"  g + 2h           = {sum_rule:.12g} mm")
        else:
            print("  g + 2h           = N/A")

        if alpha is not None:
            print(f"  alpha            = {alpha:.12g} deg")
        else:
            print("  alpha            = N/A")

        if r is not None:
            src = f"{rs}.{rk}" if rs and rk else "unknown"
            print(f"  r (nosing/overlap)= {r:.12g} mm   (from {src})")
        else:
            print("  r (nosing/overlap)= N/A")

        if n_risers is not None:
            src = f"{nrs}.{nrk}" if nrs and nrk else "unknown"
            print(f"  number of risers = {n_risers}   (from {src})")
        else:
            print("  number of risers = N/A")

        if climb_h is not None:
            print(f"  climb height H   = {climb_h:.12g} mm")
        else:
            print("  climb height H   = N/A")

        if sum_rule_local is not None:
            print(f"  2h + g           = {sum_rule_local:.12g} mm")
        else:
            print("  2h + g           = N/A")

        print("  ----------------------------------------------------------")
        print(f"  Check (1)  [ID 7]  600 <= g+2h <= 660     : {fmt_bool(check1)}")
        print(f"  Check (2)  [ID 8]  210 <= g <= 310        : {fmt_bool(check2)}")
        print(f"  Check (3)  [ID 3]  20 < alpha <= 45 deg   : {fmt_bool(check3)}")
        print(f"  Check (4)  [ID 9]  overlap r >= 10 mm     : {fmt_bool(check4)}")
        print(f"  Check (5)  [ID 56] 480 <= 2h+g <= 800     : {fmt_bool(check5)}")
        print(f"  Check (6)  [ID 55] risers >= 3            : {fmt_bool(check6)}")
        print(f"  Check (7)  [ID 55] risers <= 16 (normal)  : {fmt_bool(check7)}")
        print(f"  Check (8)  [ID 55] risers <= 22 (max cond): {fmt_bool(check8)}")
        print(f"  Check (9)  [ID 19] single-flight H<=4000  : {fmt_bool(check9)}")
        print(f"  Check (10) [ID 20] multi-flight H<=3000   : {fmt_bool(check10)}")
        print("============================================================")

    # ==================== RAILINGS ====================
    railings = safe_by_type(model, "IfcRailing")
    print("\n====================== Railing Checks ======================")
    print(f"Found IfcRailing: {len(railings)}")
    print("------------------------------------------------------------")
    for idx, r in enumerate(railings, start=1):
        rid = r.id()
        rname = getattr(r, "Name", None)
        obj_type = getattr(r, "ObjectType", None)
        psets = get_psets_and_qtos(r)
        h_rail, hs, hk = find_numeric(psets, ["Height"])
        kind = classify_railing(rname, obj_type, h_rail)

        check26 = None
        check36 = None
        check60 = None
        check63 = None
        if h_rail is not None:
            if kind == "guardrail":
                check26 = (h_rail >= 1100.0)
                check60 = (h_rail >= 1100.0)
            elif kind == "handrail":
                check36 = (900.0 <= h_rail <= 1000.0)
                check63 = (760.0 <= h_rail <= 940.0)

        print(f"[R{idx}] IfcRailing #{rid}  Name={rname}  Type={obj_type}  Class={kind}")
        if h_rail is not None:
            src = f"{hs}.{hk}" if hs and hk else "unknown"
            print(f"  height h         = {h_rail:.12g} mm   (from {src})")
        else:
            print("  height h         = N/A")
        print("  ----------------------------------------------------------")
        print(f"  Check (11) [ID 26] guardrail h >= 1100   : {fmt_bool(check26)}")
        print(f"  Check (12) [ID 36] handrail 900..1000    : {fmt_bool(check36)}")
        print(f"  Check (13) [ID 60] landing guardrail 1100: {fmt_bool(check60)}")
        print(f"  Check (14) [ID 63] handrail 760..940      : {fmt_bool(check63)}")
        print("============================================================")

    # ==================== MEP COMPONENTS (SAFE EXTRACTION) ====================
    gas_pipes_all = safe_by_type(model, "IfcFlowSegment")
    elec_panels = safe_by_type(model, "IfcFlowController")
    all_fire_terminals = safe_by_type(model, "IfcFireSuppressionTerminal")
    outlets = safe_by_type(model, "IfcOutlet")

    # Filter gas pipes
    gas_pipes = [gp for gp in gas_pipes_all if (getattr(gp, "ObjectType", "") == "GAS" or
                                                (getattr(gp, "Name", "") and "gas" in getattr(gp, "Name", "").lower()))]

    # Filter fire hoses and cabinets
    fire_hoses = [ft for ft in all_fire_terminals if (getattr(ft, "ObjectType", None) == "HYDRANT" or
                                                      (getattr(ft, "Name", "") and "hose" in getattr(ft, "Name", "").lower()))]
    cabinets = [ft for ft in all_fire_terminals if (getattr(ft, "ObjectType", None) == "CABINET" or
                                                    (getattr(ft, "Name", "") and "cabinet" in getattr(ft, "Name", "").lower()))]

    print("\n====================== MEP Component Extraction ======================")
    print(f"IfcFlowSegment (gas pipes)      : {len(gas_pipes)}")
    print(f"IfcFlowController (elec panels) : {len(elec_panels)}")
    print(f"IfcFireSuppressionTerminal (fire hoses): {len(fire_hoses)}")
    print(f"IfcFireSuppressionTerminal (cabinets) : {len(cabinets)}")
    print(f"IfcOutlet                       : {len(outlets)}")
    print("------------------------------------------------------------")

    # Gas pipes
    for idx, gp in enumerate(gas_pipes, start=1):
        gid = gp.id()
        name = getattr(gp, "Name", None)
        psets = get_psets_and_qtos(gp)
        diam, dset, dkey = find_numeric(psets, ["Diameter", "NominalDiameter"])
        print(f"[G{idx}] IfcFlowSegment #{gid}  Name={name}")
        if diam is not None:
            print(f"    Diameter       = {diam:.12g} mm  (from {dset}.{dkey})")
        else:
            print("    Diameter       = N/A")
        print(f"    IsGasPipe      = True" if "gas" in (name or "").lower() else "    IsGasPipe      = False")

    # Electrical panels
    for idx, ep in enumerate(elec_panels, start=1):
        eid = ep.id()
        name = getattr(ep, "Name", None)
        psets = get_psets_and_qtos(ep)
        width, wset, wkey = find_numeric(psets, ["Width", "PanelWidth"])
        depth, dset, dkey = find_numeric(psets, ["Depth", "PanelDepth"])
        height, hset, hkey = find_numeric(psets, ["Height", "PanelHeight"])
        print(f"[E{idx}] IfcFlowController #{eid}  Name={name}")
        if width is not None:
            print(f"    Width          = {width:.12g} mm  (from {wset}.{wkey})")
        if depth is not None:
            print(f"    Depth          = {depth:.12g} mm  (from {dset}.{dkey})")
        if height is not None:
            print(f"    Height         = {height:.12g} mm  (from {hset}.{hkey})")

    # Fire hose valves
    for idx, fh in enumerate(fire_hoses, start=1):
        fid = fh.id()
        name = getattr(fh, "Name", None)
        psets = get_psets_and_qtos(fh)
        height, _, _ = find_numeric(psets, ["FireHoseHeight", "InstallationHeight", "Height"])
        print(f"[FH{idx}] IfcFireSuppressionTerminal #{fid}  Name={name}")
        if height is not None:
            print(f"    FIRE_HOSE_HEIGHT_{fid} = {height:.12g}")
        else:
            print(f"    FIRE_HOSE_HEIGHT_{fid} = N/A")

    # Fire extinguisher cabinets
    for idx, cab in enumerate(cabinets, start=1):
        cid = cab.id()
        name = getattr(cab, "Name", None)
        psets = get_psets_and_qtos(cab)
        depth, _, _ = find_numeric(psets, ["CabinetDepth", "Depth"])
        print(f"[C{idx}] IfcFireSuppressionTerminal #{cid}  Name={name}")
        if depth is not None:
            print(f"    CABINET_DEPTH_{cid} = {depth:.12g}")
        else:
            print(f"    CABINET_DEPTH_{cid} = N/A")

    # Electrical outlets
    for idx, out in enumerate(outlets, start=1):
        oid = out.id()
        name = getattr(out, "Name", None)
        psets = get_psets_and_qtos(out)
        height, _, _ = find_numeric(psets, ["InstallationHeight", "Height"])
        print(f"[O{idx}] IfcOutlet #{oid}  Name={name}")
        if height is not None:
            print(f"    OUTLET_HEIGHT_{oid} = {height:.12g}")
        else:
            print(f"    OUTLET_HEIGHT_{oid} = N/A")

    # Pull stations (manual pull stations) – kept for backward compatibility
    pull_stations = [ft for ft in all_fire_terminals if not (ft in fire_hoses or ft in cabinets)]
    for idx, ps in enumerate(pull_stations, start=1):
        pid = ps.id()
        name = getattr(ps, "Name", None)
        psets = get_psets_and_qtos(ps)
        height, _, _ = find_numeric(psets, ["InstallationHeight", "Height"])
        print(f"[P{idx}] IfcFireSuppressionTerminal #{pid}  Name={name}")
        if height is not None:
            print(f"    __PULL_HEIGHT__:{pid}:{height:.12g}")
        else:
            print(f"    __PULL_HEIGHT__:{pid}:N/A")

    print("\n================ Implemented Constraint IDs ================")
    print("Already in code before this update: ID 7, ID 8")
    print("Added in this update: ID 3, ID 9, ID 19, ID 20, ID 26, ID 36, ID 55, ID 56, ID 60, ID 63")
    print("============================================================")

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # Use the research-team-owned synthetic fixture in data/fixtures.
    test_file = os.path.join(os.path.dirname(base_dir), "data", "fixtures", "stair_mep_mutation_base.ifc")
    if os.path.exists(test_file):
        default_file_loc = test_file
    else:
        default_file_loc = test_file  # No private-model fallback in public candidate
    file_loc = os.getenv("IFC_FILE_PATH", default_file_loc)
    main(file_loc)