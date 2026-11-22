import os
import re
import sys
import json
import subprocess
import importlib
import webbrowser
import tempfile
from datetime import datetime
from collections import defaultdict
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from rdf_mapper import populate_rdf_graph, save_graph
from rdf_mapper import populate_rdf_graph
from rdflib import Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF, RDFS, XSD, SH

def extract_railing_heights_from_ifc(ifc_path, comp_data):
    """Read railing heights from IFC and update comp_data."""
    try:
        import ifcopenshell
        model = ifcopenshell.open(ifc_path)
    except Exception as e:
        print(f"Failed to open IFC for railing extraction: {e}")
        return

    railings = model.by_type("IfcRailing")
    for railing in railings:
        rid = railing.id()
        key = ("IfcRailing", rid)
        if key not in comp_data:
            # Create minimal entry if missing
            comp_data[key] = {
                "type": "IfcRailing",
                "entity_id": rid,
                "name": getattr(railing, "Name", "?"),
                "metadata": f"Type={getattr(railing, 'ObjectType', '?')}",
            }
        # Read the legacy generic railing height and the dedicated landing
        # datum independently. Do not derive the latter from generic Height.
        from ifc_extractor import (
            get_psets_and_qtos, find_numeric, find_landing_handrail_height
        )
        psets = get_psets_and_qtos(railing)
        height, _, _ = find_numeric(psets, ["Height"])
        landing_height, _, _ = find_landing_handrail_height(psets)

        if height is not None:
            comp_data[key]["height h"] = height
            print(f"Direct IFC added railing height={height} for {key}")
        if landing_height is not None:
            comp_data[key]["landing handrail height"] = landing_height
            print(f"Direct IFC added landing handrail height={landing_height} for {key}")

def extract_stair_parameters_from_ifc(ifc_path):
    import ifcopenshell
    try:
        model = ifcopenshell.open(ifc_path)
    except Exception as e:
        print(f"Failed to open IFC: {e}")
        return {}

    flights = model.by_type("IfcStairFlight")
    if not flights:
        return {}

    flight = flights[0]
    params = {"h": None, "g": None, "r": None, "number of risers": None, "climb height": None}

    for rel in getattr(flight, "IsDefinedBy", []):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pdef = rel.RelatingPropertyDefinition
        if pdef.is_a("IfcElementQuantity"):
            for q in getattr(pdef, "Quantities", []):
                name = getattr(q, "Name", "")
                if name == "RiserHeight" and hasattr(q, "LengthValue"):
                    params["h"] = q.LengthValue
                elif name == "TreadLength" and hasattr(q, "LengthValue"):
                    params["g"] = q.LengthValue
                elif name == "NosingLength" and hasattr(q, "LengthValue"):
                    params["r"] = q.LengthValue
                elif name == "NumberOfRisers" and hasattr(q, "CountValue"):
                    params["number of risers"] = q.CountValue
        elif pdef.is_a("IfcPropertySet"):
            for prop in getattr(pdef, "HasProperties", []):
                if not prop.is_a("IfcPropertySingleValue"):
                    continue
                name = getattr(prop, "Name", "")
                val = getattr(prop, "NominalValue", None)
                if val is None:
                    continue
                if hasattr(val, "wrappedValue"):
                    val = val.wrappedValue
                if name == "RiserHeight":
                    params["h"] = float(val)
                elif name == "TreadLength":
                    params["g"] = float(val)
                elif name == "NosingLength":
                    params["r"] = float(val)
                elif name == "NumberOfRisers":
                    params["number of risers"] = float(val)

    if params["h"] is not None and params["number of risers"] is not None:
        params["climb height"] = params["h"] * params["number of risers"]

    return params

# ------------------------------
# Ontology class (inline)
# ------------------------------
class Ontology:
    """
    A simple ontology to store classes, instances, and binary relations.
    Supports transitive closure computation.
    """
    def __init__(self):
        self.classes = set()
        self.instances = {}      # {(type, id): {'type': class_name, 'attributes': {...}}}
        self.relations = []      # list of (subject, predicate, object)

    def add_class(self, class_name):
        self.classes.add(class_name)

    def add_instance(self, instance_key, class_name, attributes=None):
        self.instances[instance_key] = {
            'type': class_name,
            'attributes': attributes or {}
        }
        self.add_class(class_name)

    def add_relation(self, subject, predicate, obj):
        self.relations.append((subject, predicate, obj))

    def get_instances_of_class(self, class_name):
        return [k for k, v in self.instances.items() if v['type'] == class_name]

    def get_related(self, subject, predicate=None):
        """Return objects related to subject by given predicate (or any predicate if None)"""
        return [o for s, p, o in self.relations if s == subject and (predicate is None or p == predicate)]

    def get_subjects(self, obj, predicate=None):
        """Return subjects that relate to obj by given predicate (or any predicate if None)"""
        return [s for s, p, o in self.relations if o == obj and (predicate is None or p == predicate)]

    def compute_transitive_closure(self, predicate):
        """Add all transitive pairs for the given predicate (e.g., 'isPartOf')"""
        direct = [(s, o) for (s, p, o) in self.relations if p == predicate]
        closure = set(direct)
        changed = True
        while changed:
            changed = False
            new_pairs = set()
            for (a, b) in closure:
                for (c, d) in closure:
                    if b == c and (a, d) not in closure:
                        new_pairs.add((a, d))
                        changed = True
            closure.update(new_pairs)
        for (s, o) in closure:
            if (s, predicate, o) not in self.relations:
                self.relations.append((s, predicate, o))

# ------------------------------
# Constants and regex patterns
# ------------------------------
# Parse numbers printed in ordinary or scientific notation (e.g. 250, -2.5, 1.2e+03).
# The procedural program formats dimensions with .3g, so large values are
# frequently printed in exponent form. Without this pattern, the RDF path
# silently drops those values and returns false INSUFFICIENT_INFORMATION.
NUMERIC_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"

PARAM_RE = re.compile(r"^\s+((?:h|g|r|number of risers|climb height|height h|landing handrail height)(?:\s+\([^)]+\))?)\s*=\s*([\d\.]+)\s*mm")
# PARENT_RE = re.compile(r"^\s+Parent stair\s*=\s*#(\d+)\s+Name=(.*?)\s+Tag=(.*?)\s+\(flights in stair:\s*(\d+)\)")
PARENT_RE = re.compile(r"Parent stair\s*=\s*#(\d+)")
STAIR_HEADER_RE = re.compile(r"^\[(\d+)\]\s+IfcStairFlight\s+#(\d+)\s+Name=(.*?)\s+Tag=(.*)$")
RAILING_HEADER_RE = re.compile(r"^\[R(\d+)\]\s+IfcRailing\s+#(\d+)\s+Name=(.*?)\s+Type=(.*?)\s+Class=(.*)$")
GAS_PIPE_HEADER_RE = re.compile(r"^\[G(\d+)\]\s+IfcFlowSegment\s+#(\d+)\s+Name=(.*)$")
ELEC_PANEL_HEADER_RE = re.compile(r"^\[E(\d+)\]\s+IfcFlowController\s+#(\d+)\s+Name=(.*)$")
PULL_STATION_HEADER_RE = re.compile(r"^\[P(\d+)\]\s+IfcFireSuppressionTerminal\s+#(\d+)\s+Name=(.*)$")
FIRE_HOSE_HEADER_RE = re.compile(r"^\[FH(\d+)\]\s+IfcFireSuppressionTerminal\s+#(\d+)\s+Name=(.*)$")
CABINET_HEADER_RE = re.compile(r"^\[C(\d+)\]\s+IfcFireSuppressionTerminal\s+#(\d+)\s+Name=(.*)$")
OUTLET_HEADER_RE = re.compile(r"^\[O(\d+)\]\s+IfcOutlet\s+#(\d+)\s+Name=(.*)$")
CHECK_RE = re.compile(r"^Check\s+\((\d+)\)\s+\[ID\s+(\d+)\]\s+(.+?)\s*:\s*(PASS|FAIL|N/A)\s*$")

# ------------------------------
# Helper functions for UI, status, dependencies
# ------------------------------
def set_status(status_label, text, level):
    style_map = {
        "ready": "Status.Ready.TLabel",
        "running": "Status.Running.TLabel",
        "done": "Status.Done.TLabel",
        "warn": "Status.Warn.TLabel",
        "error": "Status.Error.TLabel",
    }
    style = style_map.get(level, "Status.Ready.TLabel")
    status_label.config(text=text, style=style)

def set_export_buttons(excel_btn, pdf_btn, enabled):
    state = "!disabled" if enabled else "disabled"
    excel_btn.state([state])
    pdf_btn.state([state])

def dependency_available(module_name):
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False

def install_packages(packages, status_label):
    pkg_text = ", ".join(packages)
    set_status(status_label, f"Installing: {pkg_text} ...", "running")
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir"] + list(packages)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            # try without --no-cache-dir
            cmd2 = [sys.executable, "-m", "pip", "install"] + list(packages)
            proc2 = subprocess.run(cmd2, capture_output=True, text=True)
            if proc2.returncode != 0:
                set_status(status_label, "Dependency install failed.", "error")
                messagebox.showerror("Install Failed", f"Could not install {pkg_text}\n{proc2.stderr}")
                return False
        set_status(status_label, f"Installed: {pkg_text}", "done")
        return True
    except Exception as e:
        set_status(status_label, "Dependency install failed.", "error")
        messagebox.showerror("Install Failed", str(e))
        return False

def ensure_dependency(module_name, package_name, status_label):
    if dependency_available(module_name):
        return True
    if messagebox.askyesno("Missing Dependency", f"Package '{package_name}' is required. Install now?"):
        if install_packages([package_name], status_label):
            return dependency_available(module_name)
    return False

def get_default_ifc_path():
    base = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(os.path.dirname(base), "data", "fixtures", "stair_mep_mutation_base.ifc")
    if os.path.exists(default):
        return default
    # If not found, return an empty string (will trigger file dialog later)
    return ""

def choose_ifc_file(ifc_var):
    initial_dir = os.path.dirname(ifc_var.get()) if ifc_var.get() else os.getcwd()
    selected = filedialog.askopenfilename(
        title="Select IFC File",
        initialdir=initial_dir,
        filetypes=[("IFC files", "*.ifc")]
    )
    if selected:
        ifc_var.set(selected)

def normalize_text(value):
    return "-" if value in (None, "", "None") else str(value)

def truncate_text(value, limit):
    txt = normalize_text(value)
    return txt if len(txt) <= limit else txt[:limit-3] + "..."

def reset_result_state(state):
    state.clear()
    state.update({
        "rows": [],
        "rule_summary": [],
        "totals": {"PASS": 0, "FAIL": 0, "N/A": 0},
        "ifc_file": "",
        "generated_at": datetime.now(),
        "raw_output": "",
        "component_data": {},
        "ontology": None,
    })

def pick_line_tag(line):
    if "FAIL" in line:
        return "fail"
    if "PASS" in line:
        return "pass"
    if "N/A" in line:
        return "na"
    if line.strip().startswith(("=", "-")):
        return "divider"
    if line.strip().startswith("[") or line.strip().startswith("Check ("):
        return "section"
    return None

# ------------------------------
# Ontology serialization (JSON)
# ------------------------------
def serialize_ontology(onto):
    return {
        "classes": list(onto.classes),
        "instances": {f"{k[0]}|{k[1]}": v for k, v in onto.instances.items()},
        "relations": [(f"{s[0]}|{s[1]}", p, f"{o[0]}|{o[1]}") for (s, p, o) in onto.relations],
    }

def deserialize_ontology(data):
    onto = Ontology()
    onto.classes = set(data["classes"])
    for ks, val in data["instances"].items():
        typ, eid = ks.split("|")
        onto.instances[(typ, int(eid))] = val
    for s_str, p, o_str in data["relations"]:
        s_typ, s_id = s_str.split("|")
        o_typ, o_id = o_str.split("|")
        onto.relations.append(((s_typ, int(s_id)), p, (o_typ, int(o_id))))
    return onto

def serialize_component_data(comp_data):
    return {f"{k[0]}|{k[1]}": v for k, v in comp_data.items()}

def deserialize_component_data(data):
    return {(typ, int(eid)): v for (typ, eid), v in ((k.split("|"), v) for k, v in data.items())}

def serialize_result_state(state):
    return {
        "rows": state["rows"],
        "rule_summary": state["rule_summary"],
        "totals": state["totals"],
        "ifc_file": state["ifc_file"],
        "generated_at": state["generated_at"].isoformat(),
        "component_data": serialize_component_data(state["component_data"]),
        "ontology": serialize_ontology(state["ontology"]) if state["ontology"] else None,
    }

def deserialize_result_state(data):
    state = {}
    state["rows"] = data["rows"]
    state["rule_summary"] = data["rule_summary"]
    state["totals"] = data["totals"]
    state["ifc_file"] = data["ifc_file"]
    state["generated_at"] = datetime.fromisoformat(data["generated_at"])
    state["component_data"] = deserialize_component_data(data["component_data"])
    state["ontology"] = deserialize_ontology(data["ontology"]) if data["ontology"] else None
    state["raw_output"] = ""
    return state

# ------------------------------
# TTL (Turtle) export/import (using rdflib)
# ------------------------------
def ontology_to_ttl(ontology, file_path):
    """Save ontology (instances and isPartOf relations) as Turtle file with TransitiveProperty."""
    try:
        from rdflib import Graph, Namespace, Literal
        from rdflib.namespace import RDF, RDFS, OWL
    except ImportError:
        messagebox.showerror("Error", "rdflib not installed. Install with: pip install rdflib")
        return False
    g = Graph()
    ex = Namespace("http://example.com/stairOntology#")
    g.bind("ex", ex)

    # Declare isPartOf as TransitiveProperty
    g.add((ex.isPartOf, RDF.type, OWL.TransitiveProperty))

    # Classes
    for cls in ontology.classes:
        cls_uri = ex[cls.replace(" ", "_")]
        g.add((cls_uri, RDF.type, OWL.Class))
        g.add((cls_uri, RDFS.label, Literal(cls)))

    # Instances
    for (comp_type, eid), inst_data in ontology.instances.items():
        inst_uri = ex[f"{comp_type}_{eid}"]
        g.add((inst_uri, RDF.type, ex[comp_type.replace(" ", "_")]))
        if 'name' in inst_data.get('attributes', {}):
            g.add((inst_uri, ex.hasName, Literal(inst_data['attributes']['name'])))

    # Relations (only isPartOf)
    for s, p, o in ontology.relations:
        if p == "isPartOf":
            s_uri = ex[f"{s[0]}_{s[1]}"]
            o_uri = ex[f"{o[0]}_{o[1]}"]
            g.add((s_uri, ex.isPartOf, o_uri))

    g.serialize(destination=file_path, format="turtle")
    return True

def ttl_to_ontology(file_path):
    try:
        from rdflib import Graph, Namespace
    except ImportError:
        messagebox.showerror("Error", "rdflib not installed.")
        return None
    g = Graph()
    g.parse(file_path, format="turtle")
    ex = Namespace("http://example.com/stairOntology#")
    onto = Ontology()
    import re
    for s, p, o in g.triples((None, ex.isPartOf, None)):
        s_str, o_str = str(s), str(o)
        s_match = re.search(r'#(\w+)_(\d+)$', s_str)
        o_match = re.search(r'#(\w+)_(\d+)$', o_str)
        if s_match and o_match:
            s_type, s_id = s_match.group(1), int(s_match.group(2))
            o_type, o_id = o_match.group(1), int(o_match.group(2))
            onto.add_relation((s_type, s_id), "isPartOf", (o_type, o_id))
            onto.add_instance((s_type, s_id), s_type)
            onto.add_instance((o_type, o_id), o_type)
    return onto

def rdf_graph_to_ontology(g):
    from rdflib import Namespace
    ex = Namespace("http://example.com/stairOntology#")
    onto = Ontology()
    import re
    for s, p, o in g.triples((None, ex.isPartOf, None)):
        s_str, o_str = str(s), str(o)
        s_match = re.search(r'#(\w+)_(\d+)$', s_str)
        o_match = re.search(r'#(\w+)_(\d+)$', o_str)
        if s_match and o_match:
            s_type, s_id = s_match.group(1), int(s_match.group(2))
            o_type, o_id = o_match.group(1), int(o_match.group(2))
            onto.add_relation((s_type, s_id), "isPartOf", (o_type, o_id))
            onto.add_instance((s_type, s_id), s_type)
            onto.add_instance((o_type, o_id), o_type)
    return onto

# ------------------------------
# Change Impact Analyzer (rules 1-14 + storey rule 15)
# ------------------------------
class ChangeImpactAnalyzer:
    def __init__(self):
        self.dep_graph = {
            "h": [(1, "flight"), (3, "flight"), (5, "flight"), (9, "flight"), (10, "flight"), (18, "flight")],
            "g": [(1, "flight"), (2, "flight"), (3, "flight"), (5, "flight"), (17, "flight"), (18, "flight")],
            "r": [(4, "flight")],
            "number of risers": [(6, "flight"), (7, "flight"), (8, "flight"), (9, "flight"), (10, "flight"), (16, "flight")],
            "climb height": [(9, "flight"), (10, "flight"), (17, "flight")],
            "height h": [(11, "railing"), (12, "railing"), (14, "railing"), (16, "railing")],
            "landing handrail height": [(13, "railing")],
            "fire_hose_height": [(16, "hose")],
            "cabinet_depth": [(17, "cabinet")],
            "outlet_height": [(18, "outlet")],
            "guardrail_height": [(16, "railing")]
        }
        self.component_data = {}

    def set_component_data(self, data, ontology=None):
        self.component_data = data
        # Optional structural context is needed to avoid false impact reports
        # for mutually exclusive single-flight and multi-flight rules.
        self.relations = getattr(ontology, "relations", ontology)

    def rule_in_structural_scope(self, component_key, rule_id):
        if rule_id not in (9, 10) or component_key[0] != "IfcStairFlight":
            return True
        if self.relations is None:
            # Backward compatible for synthetic rules-only tests; reviewers
            # should not interpret these results as validated applicability.
            return True
        parents = {parent for child, pred, parent in self.relations
                   if child == component_key and pred == "isPartOf" and parent[0] == "IfcStair"}
        if not parents:
            # Unknown context; omit status change instead of inventing one.
            return False
        counts = [len({child for child, pred, p in self.relations
                       if p == parent and pred == "isPartOf" and child[0] == "IfcStairFlight"})
                  for parent in parents]
        return any(c == 1 for c in counts) if rule_id == 9 else any(c > 1 for c in counts)

    def evaluate_rule(self, rule_id, params):
        h = params.get("h")
        g = params.get("g")
        r = params.get("r")
        n = params.get("number of risers")
        climb = params.get("climb height")
        rail_h = params.get("height h")
        landing_rail_h = params.get("landing handrail height")
        total_climb = params.get("total_climb")

        if rule_id == 1:
            if g is not None and h is not None:
                val = g + 2*h
                return "PASS" if 600 <= val <= 660 else "FAIL"
            return "N/A"
        if rule_id == 2:
            if g is not None:
                return "PASS" if 210 <= g <= 310 else "FAIL"
            return "N/A"
        if rule_id == 3:
            if g is not None and h is not None and g > 0:
                import math
                alpha = math.degrees(math.atan(h / g))
                return "PASS" if 20 < alpha <= 45 else "FAIL"
            return "N/A"
        if rule_id == 4:
            return "PASS" if r is not None and r >= 10 else ("FAIL" if r is not None else "N/A")
        if rule_id == 5:
            if h is not None and g is not None:
                val = 2*h + g
                return "PASS" if 480 <= val <= 800 else "FAIL"
            return "N/A"
        if rule_id == 6:
            return "PASS" if n is not None and n >= 3 else ("FAIL" if n is not None else "N/A")
        if rule_id == 7:
            return "PASS" if n is not None and n <= 16 else ("FAIL" if n is not None else "N/A")
        if rule_id == 8:
            return "PASS" if n is not None and n <= 22 else ("FAIL" if n is not None else "N/A")
        if rule_id == 9:
            return "PASS" if climb is not None and climb <= 4000 else ("FAIL" if climb is not None else "N/A")
        if rule_id == 10:
            return "PASS" if climb is not None and climb <= 3000 else ("FAIL" if climb is not None else "N/A")
        if rule_id == 11:
            return "PASS" if rail_h is not None and rail_h >= 1100 else ("FAIL" if rail_h is not None else "N/A")
        if rule_id == 13:
            return "PASS" if landing_rail_h is not None and landing_rail_h >= 1100 else ("FAIL" if landing_rail_h is not None else "N/A")
        if rule_id == 12:
            return "PASS" if rail_h is not None and 900 <= rail_h <= 1000 else ("FAIL" if rail_h is not None else "N/A")
        if rule_id == 14:
            return "PASS" if rail_h is not None and 760 <= rail_h <= 940 else ("FAIL" if rail_h is not None else "N/A")
        if rule_id == 15:
            if total_climb is not None:
                return "PASS" if total_climb <= 4000 else "FAIL"
            return
        if rule_id == 16:
            n = params.get("number of risers")
            guardrail_h = params.get("guardrail_height")
            hose_h = params.get("fire_hose_height")
            if n is not None and guardrail_h is not None and hose_h is not None:
                if n > 12 and guardrail_h >= 1100 and 900 <= hose_h <= 1500:
                    return "FAIL"
                else:
                    return "PASS"
            return "N/A"
        if rule_id == 17:
            g = params.get("g")
            climb = params.get("climb height")
            depth = params.get("cabinet_depth")
            if depth is not None:
                condition = (g is not None and g < 1200) or (climb is not None and climb > 6000)
                if condition and depth > 100:
                    return "FAIL"
                else:
                    return "PASS"
            return "N/A"
        if rule_id == 18:
            h = params.get("h")
            g = params.get("g")
            outlet_h = params.get("outlet_height")
            if outlet_h is not None:
                condition = (h is not None and h > 190) or (g is not None and g < 240)
                if condition and 600 <= outlet_h <= 1200:
                    return "FAIL"
                else:
                    return "PASS"
            return "N/A"

    def find_impact(self, comp_key, param_name, new_value):
        if comp_key not in self.component_data:
            return []
        comp = self.component_data[comp_key]
        comp_type = comp.get("type")
        comp_name = comp.get("name", "").lower()
        old_params = comp.copy()
        new_params = old_params.copy()
        new_params[param_name] = new_value
        if comp_type == "IfcStairFlight" and param_name in ("h", "number of risers"):
            h_new = new_params.get("h")
            n_new = new_params.get("number of risers")
            new_params["climb height"] = (h_new * n_new if h_new is not None and n_new is not None else None)

        # Local function to collect all parameters from stair, railing, and MEP components
        def get_all_params():
            all_params = {}
            # Stair flight parameters
            for k, v in self.component_data.items():
                if k[0] == "IfcStairFlight":
                    all_params.update({
                        "h": v.get("h"),
                        "g": v.get("g"),
                        "number of risers": v.get("number of risers"),
                        "climb height": v.get("climb height"),
                    })
            # Only explicitly classified guardrails contribute to Rule 16.
            from rdf_mapper import extract_railing_classification
            for k, v in self.component_data.items():
                if k[0] == "IfcRailing" and extract_railing_classification(v) == "guardrail":
                    guardrail_h = v.get("guardrail height", v.get("height h"))
                    if guardrail_h is not None:
                        all_params["guardrail_height"] = guardrail_h
            # MEP components
            for k, v in self.component_data.items():
                if k[0] == "IfcFireSuppressionTerminal":
                    if "hose" in v.get("name", "").lower():
                        all_params["fire_hose_height"] = v.get("fire_hose_height")
                    if "cabinet" in v.get("name", "").lower():
                        all_params["cabinet_depth"] = v.get("cabinet_depth")
                if k[0] == "IfcOutlet":
                    all_params["outlet_height"] = v.get("outlet_height")
            return all_params

        impacts = []
        for rule_id, target_type in self.dep_graph.get(param_name, []):
            if not self.rule_in_structural_scope(comp_key, rule_id):
                continue
            # Case 1: Changing stair flight affects MEP rules
            if rule_id in [16, 17, 18] and comp_type == "IfcStairFlight":
                eval_params_old = {**get_all_params(), **old_params}
                eval_params_new = {**get_all_params(), **new_params}
                old_status = self.evaluate_rule(rule_id, eval_params_old)
                new_status = self.evaluate_rule(rule_id, eval_params_new)
                if old_status != new_status:
                    impacts.append({
                        "component_key": comp_key,
                        "component_name": comp.get("name", "?"),
                        "component_metadata": comp.get("metadata", ""),
                        "rule_id": rule_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "reason": f"Stair parameter {param_name} changed from {old_params.get(param_name)} to {new_value}"
                    })
            # Case 2: Changing MEP component directly (hose, cabinet)
            elif target_type in ["hose", "cabinet", "outlet"] and comp_type == "IfcFireSuppressionTerminal":
                if (rule_id == 16 and "hose" in comp_name) or \
                        (rule_id == 17 and "cabinet" in comp_name):
                    eval_params_old = {**get_all_params(), **old_params}
                    eval_params_new = {**get_all_params(), **new_params}
                    old_status = self.evaluate_rule(rule_id, eval_params_old)
                    new_status = self.evaluate_rule(rule_id, eval_params_new)
                    if old_status != new_status:
                        impacts.append({
                            "component_key": comp_key,
                            "component_name": comp.get("name", "?"),
                            "component_metadata": comp.get("metadata", ""),
                            "rule_id": rule_id,
                            "old_status": old_status,
                            "new_status": new_status,
                            "reason": f"Changed {param_name} from {old_params.get(param_name)} to {new_value}"
                        })
            # Case 3: Outlet direct change
            elif target_type == "outlet" and comp_type == "IfcOutlet" and rule_id == 18:
                eval_params_old = {**get_all_params(), **old_params}
                eval_params_new = {**get_all_params(), **new_params}
                old_status = self.evaluate_rule(rule_id, eval_params_old)
                new_status = self.evaluate_rule(rule_id, eval_params_new)
                if old_status != new_status:
                    impacts.append({
                        "component_key": comp_key,
                        "component_name": comp.get("name", "?"),
                        "component_metadata": comp.get("metadata", ""),
                        "rule_id": rule_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "reason": f"Changed {param_name} from {old_params.get(param_name)} to {new_value}"
                    })
            # Case 4: Traditional flight rules
            elif target_type == "flight" and comp_type == "IfcStairFlight":
                eval_params_old = old_params
                eval_params_new = new_params
                old_status = self.evaluate_rule(rule_id, eval_params_old)
                new_status = self.evaluate_rule(rule_id, eval_params_new)
                if old_status != new_status:
                    impacts.append({
                        "component_key": comp_key,
                        "component_name": comp.get("name", "?"),
                        "component_metadata": comp.get("metadata", ""),
                        "rule_id": rule_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "reason": f"Changed {param_name} from {old_params.get(param_name)} to {new_value}"
                    })
            # Case 5: Railing rules (including rule 16)
            elif target_type == "railing" and comp_type == "IfcRailing":
                # Use the same guardrail/handrail classification as the RDF mapper;
                # generic railings must not be assigned either regulation by guess.
                from rdf_mapper import extract_railing_classification
                railing_kind = extract_railing_classification(comp)
                if rule_id in (11, 16) and railing_kind != "guardrail":
                    continue
                if rule_id == 13 and railing_kind != "landing_handrail":
                    continue
                if rule_id in (12, 14) and railing_kind != "handrail":
                    continue
                # For rule 16, we need to map "height h" to "guardrail_height"
                if rule_id == 16:
                    # Start with all parameters (stair, MEP, etc.)
                    full_old = get_all_params()
                    full_new = get_all_params()
                    # Override guardrail height with the railing's height (old and new)
                    full_old["guardrail_height"] = old_params.get("height h")
                    full_new["guardrail_height"] = new_params.get("height h")
                    old_status = self.evaluate_rule(rule_id, full_old)
                    new_status = self.evaluate_rule(rule_id, full_new)
                else:
                    # Other railing rules (11-14) only need height
                    old_status = self.evaluate_rule(rule_id, old_params)
                    new_status = self.evaluate_rule(rule_id, new_params)
                if old_status != new_status:
                    impacts.append({
                        "component_key": comp_key,
                        "component_name": comp.get("name", "?"),
                        "component_metadata": comp.get("metadata", ""),
                        "rule_id": rule_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "reason": f"Changed {param_name} from {old_params.get(param_name)} to {new_value}"
                    })
        return impacts

    def get_ancestors(self, comp_key, ontology, predicate="isPartOf"):
        ancestors = []
        stack = [comp_key]
        visited = set()
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            parents = ontology.get_related(cur, predicate)
            for p in parents:
                if p not in ancestors:
                    ancestors.append(p)
                    stack.append(p)
        return ancestors

    def propagate_to_related_flights(self, comp_key, param_name, new_value, ontology):
        impacts = []
        if comp_key[0] != "IfcStairFlight":
            return impacts

        # find parent stair
        parent_stair = None
        for s, p, o in ontology.relations:
            if s == comp_key and p == "isPartOf":
                parent_stair = o
                break
        if not parent_stair:
            return self.find_impact(comp_key, param_name, new_value)

        # Re-evaluate the modified flight only. Its siblings are not mutated.
        # Broader cross-element dependencies need a separate, validated selector.
        impacts.extend(self.find_impact(comp_key, param_name, new_value))

        # storey rule (rule 15)
        ancestors = self.get_ancestors(comp_key, ontology, "isPartOf")
        storeys = [a for a in ancestors if a[0] == "IfcBuildingStorey"]
        for storey in storeys:
            all_flights_in_storey = []
            for flight in self.component_data.keys():
                if flight[0] == "IfcStairFlight" and storey in self.get_ancestors(flight, ontology, "isPartOf"):
                    all_flights_in_storey.append(flight)
            total_old = 0
            total_new = 0
            missing_storey_values = False
            for flight in all_flights_in_storey:
                data = self.component_data.get(flight, {})
                h = data.get("h")
                n = data.get("number of risers")
                if h is not None and n is not None:
                    climb = h * n
                else:
                    climb = data.get("climb height")
                if climb is None:
                    missing_storey_values = True
                    break
                total_old += climb
                if flight == comp_key and param_name == "h" and n is not None:
                    climb_new = new_value * n
                else:
                    climb_new = climb
                total_new += climb_new
            if missing_storey_values:
                # Do not assign zero climb to an element with unknown geometry.
                continue
            old_status = self.evaluate_rule(15, {"total_climb": total_old})
            new_status = self.evaluate_rule(15, {"total_climb": total_new})
            if old_status != new_status:
                impacts.append({
                    "component_key": storey,
                    "component_name": f"Storey {storey[1]}",
                    "component_metadata": "",
                    "rule_id": 15,
                    "old_status": old_status,
                    "new_status": new_status,
                    "reason": f"Change in flight {comp_key[1]} alters total climb height from {total_old:.1f} to {total_new:.1f}"
                })
        return impacts

# ------------------------------
# Extraction from script output
# ------------------------------
def extract_results(output_text):
    rows = []
    current_entity = None
    ifc_file = ""
    component_data = {}
    ontology = Ontology()
    temp_parent_map = {}   # {(type, id): parent_id} for flight → stair
    pull_heights = {}      # {entity_id: height}
    fire_hose_heights = {}
    cabinet_depths = {}
    outlet_heights = {}

    lines = output_text.splitlines()
    i = 0
    while i < len(lines):
        # IMPORTANT: strip leading/trailing spaces to match headers correctly
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # ----- Dedicated lines for MEP properties (still work after strip) -----
        if line.startswith("__PULL_HEIGHT__:"):
            parts = line.split(":")
            if len(parts) >= 3:
                try:
                    eid = int(parts[1])
                    val = parts[2]
                    if val != "N/A":
                        pull_heights[eid] = float(val)
                except ValueError:
                    pass
            i += 1
            continue

        if line.startswith("FIRE_HOSE_HEIGHT_"):
            parts = line.split("=")
            if len(parts) == 2:
                id_part = parts[0].replace("FIRE_HOSE_HEIGHT_", "").strip()
                try:
                    eid = int(id_part)
                    val = float(parts[1].strip())
                    fire_hose_heights[eid] = val
                except:
                    pass
            i += 1
            continue

        if line.startswith("CABINET_DEPTH_"):
            parts = line.split("=")
            if len(parts) == 2:
                id_part = parts[0].replace("CABINET_DEPTH_", "").strip()
                try:
                    eid = int(id_part)
                    val = float(parts[1].strip())
                    cabinet_depths[eid] = val
                except:
                    pass
            i += 1
            continue

        if line.startswith("OUTLET_HEIGHT_"):
            parts = line.split("=")
            if len(parts) == 2:
                id_part = parts[0].replace("OUTLET_HEIGHT_", "").strip()
                try:
                    eid = int(id_part)
                    val = float(parts[1].strip())
                    outlet_heights[eid] = val
                except:
                    pass
            i += 1
            continue

        # ----- IFC file line -----
        lower = line.lower()
        if lower.startswith("ifc file:") or lower.startswith("ifc:"):
            ifc_file = line.split(":", 1)[1].strip()
            i += 1
            continue

        # ----- StairFlight header -----
        stair_match = STAIR_HEADER_RE.match(line)
        if stair_match:
            idx, eid, name, tag = stair_match.groups()
            current_entity = {
                "component_type": "IfcStairFlight",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": f"Tag={normalize_text(tag)}",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name, "metadata": tag})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Railing header -----
        railing_match = RAILING_HEADER_RE.match(line)
        if railing_match:
            idx, eid, name, rtype, rclass = railing_match.groups()
            current_entity = {
                "component_type": "IfcRailing",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": f"Type={normalize_text(rtype)} | Class={normalize_text(rclass)}",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name, "metadata": rtype})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Gas pipe header -----
        gas_match = GAS_PIPE_HEADER_RE.match(line)
        if gas_match:
            idx, eid, name = gas_match.groups()
            current_entity = {
                "component_type": "IfcFlowSegment",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Gas pipe",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Electrical panel header -----
        elec_match = ELEC_PANEL_HEADER_RE.match(line)
        if elec_match:
            idx, eid, name = elec_match.groups()
            current_entity = {
                "component_type": "IfcFlowController",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Electrical panel",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Pull station header (manual) -----
        pull_match = PULL_STATION_HEADER_RE.match(line)
        if pull_match:
            idx, eid, name = pull_match.groups()
            current_entity = {
                "component_type": "IfcFireSuppressionTerminal",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Manual pull station",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Fire hose header -----
        fire_hose_match = FIRE_HOSE_HEADER_RE.match(line)
        if fire_hose_match:
            idx, eid, name = fire_hose_match.groups()
            current_entity = {
                "component_type": "IfcFireSuppressionTerminal",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Fire hose valve",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Cabinet header -----
        cabinet_match = CABINET_HEADER_RE.match(line)
        if cabinet_match:
            idx, eid, name = cabinet_match.groups()
            current_entity = {
                "component_type": "IfcFireSuppressionTerminal",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Fire cabinet",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Outlet header -----
        outlet_match = OUTLET_HEADER_RE.match(line)
        if outlet_match:
            idx, eid, name = outlet_match.groups()
            current_entity = {
                "component_type": "IfcOutlet",
                "item_index": int(idx),
                "entity_id": int(eid),
                "name": normalize_text(name),
                "metadata": "Electrical outlet",
            }
            key = (current_entity["component_type"], current_entity["entity_id"])
            ontology.add_instance(key, current_entity["component_type"], {"name": name})
            if key not in component_data:
                component_data[key] = {
                    "type": current_entity["component_type"],
                    "entity_id": current_entity["entity_id"],
                    "name": current_entity["name"],
                    "metadata": current_entity["metadata"]
                }
            i += 1
            continue

        # ----- Parent stair relation (flight -> stair) -----
        if current_entity and current_entity["component_type"] == "IfcStairFlight" and "Parent stair" in line:
            match = re.search(r"#(\d+)", line)
            if match:
                parent_id = int(match.group(1))
                child_key = (current_entity["component_type"], current_entity["entity_id"])
                temp_parent_map[child_key] = parent_id

                # ---- NEW: Add missing stair to component_data if not present ----
                parent_key = ("IfcStair", parent_id)
                if parent_key not in component_data:
                    component_data[parent_key] = {
                        "type": "IfcStair",
                        "entity_id": parent_id,
                        "name": f"Stair_{parent_id}",
                        "metadata": ""
                    }
                # ----------------------------------------------------------------

            i += 1
            continue

        # ----- Storey parent relation (stair -> storey) -----
        storey_match = re.match(r"STOREY_PARENT:\s+stair_id=(\d+)\s+storey_id=(\d+)", line)
        if storey_match:
            stair_id = int(storey_match.group(1))
            storey_id = int(storey_match.group(2))
            stair_key = ("IfcStair", stair_id)
            storey_key = ("IfcBuildingStorey", storey_id)
            ontology.add_class("IfcBuildingStorey")
            if storey_key not in ontology.instances:
                ontology.add_instance(storey_key, "IfcBuildingStorey", {"name": f"Storey_{storey_id}"})
            if stair_key not in ontology.instances:
                ontology.add_instance(stair_key, "IfcStair", {"name": f"Stair_{stair_id}"})
            ontology.add_relation(stair_key, "isPartOf", storey_key)

            # ---- NEW: Add missing individuals to component_data for RDF mapping ----
            if stair_key not in component_data:
                component_data[stair_key] = {
                    "type": "IfcStair",
                    "entity_id": stair_id,
                    "name": f"Stair_{stair_id}",
                    "metadata": ""
                }
            if storey_key not in component_data:
                component_data[storey_key] = {
                    "type": "IfcBuildingStorey",
                    "entity_id": storey_id,
                    "name": f"Storey_{storey_id}",
                    "metadata": ""
                }
            # ----------------------------------------------------------------

            i += 1
            continue

        # Inside extract_results, after setting current_entity and initializing component_data[key]
        if current_entity:
            key = (current_entity["component_type"], current_entity["entity_id"])
            # ... (initialization as before) ...

            # ----- RISER HEIGHT (h) -----
            if line.startswith("h (riser height)") or "riser height" in line.lower():
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)  # <-- added \s* before =
                if match:
                    component_data[key]["h"] = float(match.group(1))
                    # print(f"DEBUG: Captured h = {match.group(1)} for {key}")

            # ----- TREAD / GOING (g) -----
            elif line.startswith("g (tread/going)") or "tread/going" in line.lower():
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)  # <-- added \s* before =
                if match:
                    component_data[key]["g"] = float(match.group(1))
                    # print(f"DEBUG: Captured g = {match.group(1)} for {key}")

            # ----- NOSING / OVERLAP (r) -----
            elif line.startswith("r (nosing/overlap)") or "nosing/overlap" in line.lower():
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)
                if match:
                    component_data[key]["r"] = float(match.group(1))

            # ----- NUMBER OF RISERS -----
            elif "number of risers" in line.lower():
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')(?=\s|$)', line)
                if match:
                    component_data[key]["number of risers"] = float(match.group(1))

            # ----- CLIMB HEIGHT H -----
            elif "climb height H" in line:
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)
                if match:
                    component_data[key]["climb height"] = float(match.group(1))

            # ----- GUARDRAIL / RAILING HEIGHT -----
            elif "height h" in line.lower() and current_entity["component_type"] == "IfcRailing":
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)
                if match:
                    component_data[key]["height h"] = float(match.group(1))

            # ----- LANDING HANDRAIL HEIGHT (explicit landing datum) -----
            elif ("landing handrail height" in line.lower() or
                  "height above landing walking level" in line.lower()) and current_entity["component_type"] == "IfcRailing":
                match = re.search(r'\s*=\s*(' + NUMERIC_TOKEN + r')\s*mm\b', line)
                if match:
                    component_data[key]["landing handrail height"] = float(match.group(1))

            # ----- GAS PIPE DIAMETER -----
            elif "diameter" in line.lower() and current_entity["component_type"] == "IfcFlowSegment":
                match = re.search(r"=\s*(" + NUMERIC_TOKEN + r")\s*mm\b", line)
                if match:
                    component_data[key]["diameter"] = float(match.group(1))

            # ----- ELECTRICAL PANEL DIMENSIONS -----
            elif any(term in line for term in ["Width", "Depth", "Height"]) and "mm" in line and current_entity[
                "component_type"] == "IfcFlowController":
                if "Width" in line:
                    match = re.search(r"=\s*(" + NUMERIC_TOKEN + r")\s*mm\b", line)
                    if match:
                        component_data[key]["width"] = float(match.group(1))
                elif "Depth" in line:
                    match = re.search(r"=\s*(" + NUMERIC_TOKEN + r")\s*mm\b", line)
                    if match:
                        component_data[key]["depth"] = float(match.group(1))
                elif "Height" in line:
                    match = re.search(r"=\s*(" + NUMERIC_TOKEN + r")\s*mm\b", line)
                    if match:
                        component_data[key]["panel_height"] = float(match.group(1))

        # ----- Check results (PASS/FAIL/N/A) -----
        check_match = CHECK_RE.match(line)
        if check_match and current_entity:
            rno, cid, rtext, res = check_match.groups()
            rows.append({
                "component_type": current_entity["component_type"],
                "item_index": current_entity["item_index"],
                "entity_id": current_entity["entity_id"],
                "name": current_entity["name"],
                "metadata": current_entity["metadata"],
                "rule_number": int(rno),
                "constraint_id": int(cid),
                "rule_text": rtext.strip(),
                "result": res,
            })
            i += 1
            continue

        i += 1

    # ----- Assign captured MEP properties to component_data -----
    for key, data in component_data.items():
        if key[0] == "IfcFireSuppressionTerminal":
            eid = key[1]
            if eid in pull_heights:
                data["install_height"] = pull_heights[eid]
            if eid in fire_hose_heights:
                data["fire_hose_height"] = fire_hose_heights[eid]
            if eid in cabinet_depths:
                data["cabinet_depth"] = cabinet_depths[eid]
        elif key[0] == "IfcOutlet":
            eid = key[1]
            if eid in outlet_heights:
                data["outlet_height"] = outlet_heights[eid]

    # ----- Add flight->stair relations to ontology -----
    for child_key, parent_id in temp_parent_map.items():
        parent_key = ("IfcStair", parent_id)
        ontology.add_class("IfcStair")
        if parent_key not in ontology.instances:
            ontology.add_instance(parent_key, "IfcStair", {"name": f"Stair_{parent_id}"})
        ontology.add_relation(child_key, "isPartOf", parent_key)

    # Do NOT compute transitive closure here – let Reasoner do it
    # ontology.compute_transitive_closure("isPartOf")

    return rows, ifc_file, component_data, ontology

def build_rule_summary(rows):
    summary = {}
    totals = {"PASS": 0, "FAIL": 0, "N/A": 0}
    for row in rows:
        res = row["result"]
        totals[res] += 1
        rn = row["rule_number"]
        if rn not in summary:
            summary[rn] = {
                "rule_number": rn,
                "constraint_id": row.get("constraint_id", 0),
                "rule_text": row.get("rule_text", ""),
                "PASS": 0, "FAIL": 0, "N/A": 0, "total": 0
            }
        summary[rn][res] += 1
        summary[rn]["total"] += 1
    return list(summary.values()), totals

# ------------------------------
# Auto-fix suggestions
# ------------------------------
def suggest_fix_for_rule(rule_id, params):
    """Return (param_name, new_value, explanation) for a failed rule, or (None,None,None)."""
    def get_val(key, *aliases):
        for k in [key] + list(aliases):
            v = params.get(k)
            if v is not None:
                return v
        return None

    h = get_val("h", "riser_height")
    g = get_val("g", "tread_length")
    r = get_val("r", "nosing")
    n = get_val("number of risers", "NumberOfRisers", "n_risers")
    climb = get_val("climb height", "climb_height")
    rail_h = get_val("height h", "guardrail_height")
    depth = get_val("cabinet_depth", "depth")
    outlet_h = get_val("outlet_height", "install_height", "InstallationHeight")
    hose_h = get_val("fire_hose_height")

    # Rule 1: 600 <= g+2h <= 660
    if rule_id == 1 and g is not None and h is not None:
        val = g + 2*h
        if val < 600:
            new_h = (600 - g) / 2
            if new_h > 0:
                return "h", new_h, f"Increase riser height to {new_h:.1f} mm (currently {h:.1f}) to reach minimum 600"
        elif val > 660:
            new_h = (660 - g) / 2
            if new_h > 0:
                return "h", new_h, f"Decrease riser height to {new_h:.1f} mm (currently {h:.1f}) to reach maximum 660"
    # Rule 2: 210 <= g <= 310
    elif rule_id == 2 and g is not None:
        if g < 210:
            return "g", 210.0, f"Increase tread length to 210 mm (currently {g:.1f})"
        elif g > 310:
            return "g", 310.0, f"Decrease tread length to 310 mm (currently {g:.1f})"
    # Rule 3: 20 < alpha <= 45 deg
    elif rule_id == 3 and g is not None and h is not None and g > 0:
        import math
        alpha = math.degrees(math.atan(h/g))
        if alpha <= 20:
            new_h = math.tan(math.radians(20.1)) * g
            return "h", new_h, f"Increase riser height to {new_h:.1f} mm to raise angle above 20° (currently {alpha:.1f}°)"
        elif alpha > 45:
            new_h = math.tan(math.radians(45)) * g
            return "h", new_h, f"Decrease riser height to {new_h:.1f} mm to lower angle to ≤45° (currently {alpha:.1f}°)"
    # Rule 4: r >= 10
    elif rule_id == 4 and r is not None and r < 10:
        return "r", 10.0, f"Increase nosing length to 10 mm (currently {r:.1f})"
    # Rule 5: 480 <= 2h+g <= 800
    elif rule_id == 5 and h is not None and g is not None:
        val = 2*h + g
        if val < 480:
            new_h = (480 - g) / 2
            if new_h > 0:
                return "h", new_h, f"Increase riser height to {new_h:.1f} mm to reach minimum 480 (currently {val:.1f})"
        elif val > 800:
            new_h = (800 - g) / 2
            if new_h > 0:
                return "h", new_h, f"Decrease riser height to {new_h:.1f} mm to reach maximum 800 (currently {val:.1f})"
    # Rule 6: n >= 3
    elif rule_id == 6 and n is not None and n < 3:
        return "number of risers", 3.0, f"Increase number of risers to 3 (currently {n})"
    # Rule 7: n <= 16
    elif rule_id == 7 and n is not None and n > 16:
        return "number of risers", 16.0, f"Decrease number of risers to 16 (currently {n})"
    # Rule 8: n <= 22
    elif rule_id == 8 and n is not None and n > 22:
        return "number of risers", 22.0, f"Decrease number of risers to 22 (currently {n})"
    # Rule 9: single-flight climb <= 4000
    elif rule_id == 9 and climb is not None and climb > 4000 and n is not None and n > 0:
        new_h = 4000 / n
        return "h", new_h, f"Decrease riser height to {new_h:.1f} mm to limit climb to 4000 mm (currently {climb:.1f})"
    # Rule 10: multi-flight climb <= 3000
    elif rule_id == 10 and climb is not None and climb > 3000 and n is not None and n > 0:
        new_h = 3000 / n
        return "h", new_h, f"Decrease riser height to {new_h:.1f} mm to limit climb to 3000 mm (currently {climb:.1f})"
    # Rule 11: guardrail height >= 1100
    elif rule_id == 11 and rail_h is not None and rail_h < 1100:
        return "height h", 1100.0, f"Increase guardrail height to 1100 mm (currently {rail_h:.1f})"
    # Rule 13: landing handrail height >= 1100, measured from landing walking surface
    elif rule_id == 13:
        landing_h = get_val("landing handrail height", "landing_handrail_height")
        if landing_h is not None and landing_h < 1100:
            return ("landing handrail height", 1100.0,
                    f"Increase landing handrail height to 1100 mm (currently {landing_h:.1f})")
    # Rule 12: handrail 900-1000
    elif rule_id == 12 and rail_h is not None:
        if rail_h < 900:
            return "height h", 900.0, f"Increase handrail height to 900 mm (currently {rail_h:.1f})"
        elif rail_h > 1000:
            return "height h", 1000.0, f"Decrease handrail height to 1000 mm (currently {rail_h:.1f})"
    # Rule 14: handrail 760-940
    elif rule_id == 14 and rail_h is not None:
        if rail_h < 760:
            return "height h", 760.0, f"Increase handrail height to 760 mm (currently {rail_h:.1f})"
        elif rail_h > 940:
            return "height h", 940.0, f"Decrease handrail height to 940 mm (currently {rail_h:.1f})"
    # Rule 16: fire hose conflict (risers >12)
    elif rule_id == 16 and n is not None and n > 12:
        return "number of risers", 12.0, f"Reduce number of risers to ≤12 (currently {n})"
    # Rule 17: cabinet depth >100 with narrow stair or high climb
    elif rule_id == 17 and depth is not None and depth > 100:
        if g is not None and g < 1200:
            return "g", 1200.0, f"Increase tread length to 1200 mm (currently {g:.1f}) to avoid cabinet conflict"
        if climb is not None and climb > 6000:
            return "climb height", 6000.0, f"Reduce total climb height to ≤6000 mm (currently {climb:.1f})"
    # Rule 18: outlet in danger zone
    elif rule_id == 18 and outlet_h is not None and 600 <= outlet_h <= 1200:
        if h is not None and h > 190:
            return "h", 190.0, f"Decrease riser height to ≤190 mm (currently {h:.1f})"
        if g is not None and g < 240:
            return "g", 240.0, f"Increase tread length to ≥240 mm (currently {g:.1f})"
    return None, None, None

def get_failed_items(result_state):
    items = []
    seen = set()
    for row in result_state["rows"]:
        if row["result"] == "FAIL":
            comp_key = (row["component_type"], row["entity_id"])
            rule_id = row["rule_number"]
            key = (comp_key, rule_id)
            if key not in seen:
                seen.add(key)
                items.append({
                    "comp_key": comp_key,
                    "component_name": row["name"],
                    "rule_id": rule_id,
                    "rule_text": row["rule_text"]
                })
    return items

# ------------------------------
# Graph visualization (networkx, matplotlib)
# ------------------------------
def plot_ontology_graph(result_state, parent_window=None):
    try:
        import networkx as nx
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    except ImportError:
        messagebox.showerror("Error", "Please install networkx and matplotlib: pip install networkx matplotlib")
        return
    onto = result_state.get("ontology")
    if onto is None:
        messagebox.showinfo("Info", "No ontology available.")
        return
    G = nx.DiGraph()
    for key, data in onto.instances.items():
        label = f"{key[0]}\n#{key[1]}"
        if 'name' in data.get('attributes', {}):
            name = data['attributes']['name']
            if name and name != "-":
                label += f"\n{name[:15]}"
        G.add_node(key, label=label)
    for s, p, o in onto.relations:
        G.add_edge(s, o, label=p)
    if G.number_of_nodes() == 0:
        messagebox.showinfo("Graph", "No nodes to display.")
        return
    win = tk.Toplevel(parent_window) if parent_window else tk.Toplevel()
    win.title("Component Relationship Graph")
    win.geometry("800x600")
    pos = nx.spring_layout(G, k=2, seed=42)
    fig, ax = plt.subplots(figsize=(8,6))
    ax.set_title("Relationships (isPartOf, etc.)")
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color="lightblue", node_size=1500)
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color="gray", arrows=True, arrowsize=20)
    labels = nx.get_node_attributes(G, 'label')
    nx.draw_networkx_labels(G, pos, labels, ax=ax, font_size=8)
    edge_labels = {(s, o): p for (s, p, o) in onto.relations}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, ax=ax, font_size=7)
    ax.axis("off")
    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=5)
    def on_close():
        plt.close(fig)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

def plot_mep_conflicts(result_state, parent_window=None, status_overrides=None):
    """2D visualization of the three new MEP rules (fire hose, cabinet, outlet)."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle, Circle, FancyBboxPatch
    except ImportError:
        messagebox.showerror("Error", "Please install matplotlib: pip install matplotlib")
        return

    comp_data = result_state.get("component_data", {})
    rows = result_state.get("rows", [])
    if not comp_data:
        messagebox.showinfo("MEP Viz", "No component data available. Run analysis first.")
        return

    # Collect rule status for rules 16, 17, 18
    status = {16: "N/A", 17: "N/A", 18: "N/A"}
    if status_overrides:
        # Use overridden values if provided
        for k in status:
            if k in status_overrides:
                status[k] = status_overrides[k]
    else:
        # Otherwise read from the result rows
        for row in rows:
            if row["rule_number"] in (16, 17, 18):
                status[row["rule_number"]] = row["result"]

    color_map = {"PASS": "green", "FAIL": "red", "N/A": "gray"}

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_xlim(-1, 5)
    ax.set_ylim(0, 4)
    ax.set_aspect('equal')
    ax.set_title("MEP Conflict Visualization (New Rules – 2D)", fontsize=16, fontweight='bold')
    ax.axis('off')

    # Draw stair flight (light gray rectangle)
    stair_rect = Rectangle((0.5, 0), 3, 2.5, facecolor='lightgray', edgecolor='black',
                           linewidth=2, alpha=0.7, hatch='///')
    ax.add_patch(stair_rect)
    ax.text(2, 1.2, "STAIRCASE\nFLIGHT", ha='center', va='center', fontsize=12, fontweight='bold',
            bbox=dict(facecolor='white', edgecolor='none', alpha=0.7))

    # Draw guardrail (line above stair)
    ax.plot([0.5, 3.5], [2.6, 2.6], color='blue', linewidth=3, linestyle='--', label='Guardrail (top)')

    # 1. Fire hose valve (circle on the right wall)
    hose_x, hose_y = 3.8, 1.4
    hose_color = color_map.get(status[16], 'gray')
    hose_circle = Circle((hose_x, hose_y), 0.15, facecolor=hose_color, edgecolor='black', linewidth=2)
    ax.add_patch(hose_circle)
    ax.text(hose_x, hose_y+0.2, "Fire Hose\nValve", ha='center', fontsize=9, fontweight='bold')
    ax.text(hose_x, hose_y-0.2, f"Rule 16: {status[16]}", ha='center', fontsize=8,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))

    # 2. Fire extinguisher cabinet (rectangle on the left wall)
    cab_x, cab_y = -0.3, 0.8
    cab_width, cab_height = 0.4, 0.6
    cab_color = color_map.get(status[17], 'gray')
    cab_rect = Rectangle((cab_x, cab_y), cab_width, cab_height, facecolor=cab_color, edgecolor='black', linewidth=2)
    ax.add_patch(cab_rect)
    ax.text(cab_x+cab_width/2, cab_y+cab_height+0.1, "Cabinet", ha='center', fontsize=9, fontweight='bold')
    ax.text(cab_x+cab_width/2, cab_y-0.1, f"Rule 17: {status[17]}", ha='center', fontsize=8,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))

    # 3. Electrical outlet (small square on the bottom wall) – more visible
    out_x, out_y = 1.5, -0.2
    out_color = color_map.get(status[18], 'gray')
    out_rect = Rectangle((out_x, out_y), 0.25, 0.25, facecolor=out_color, edgecolor='black', linewidth=2, hatch='//')
    ax.add_patch(out_rect)
    ax.text(out_x + 0.125, out_y - 0.2, "Outlet", ha='center', fontsize=10, fontweight='bold')
    ax.text(out_x + 0.125, out_y - 0.4, f"Rule 18: {status[18]}", ha='center', fontsize=9,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))
    # Add a small arrow to point at it
    ax.annotate("", xy=(out_x + 0.125, out_y + 0.1), xytext=(out_x + 0.125, out_y + 0.3),
                arrowprops=dict(arrowstyle='->', color='black'))

    # Annotations for conflicts
    if status[16] == "FAIL":
        ax.annotate("Conflict: Hose valve in guardrail zone\nwhen risers > 12", xy=(hose_x, hose_y), xytext=(4.5, 2.5),
                    arrowprops=dict(arrowstyle='->', color='red'), fontsize=8, color='red')
    if status[17] == "FAIL":
        ax.annotate("Conflict: Cabinet protrudes >100mm\nwith narrow stair or high climb", xy=(cab_x+cab_width, cab_y+cab_height/2),
                    xytext=(-1.5, 1.5), arrowprops=dict(arrowstyle='->', color='red'), fontsize=8, color='red')
    if status[18] == "FAIL":
        ax.annotate("Conflict: Outlet in danger zone\nwhen stair has steep slope", xy=(out_x+0.1, out_y),
                    xytext=(2.5, -0.8), arrowprops=dict(arrowstyle='->', color='red'), fontsize=8, color='red')

    ax.legend(loc='lower right', fontsize=10)

    # Embed in Tkinter
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    win = tk.Toplevel(parent_window) if parent_window else tk.Toplevel()
    win.title("MEP Conflict Visualization (2D – New Rules)")
    win.geometry("1000x700")
    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=5)

    def on_close():
        plt.close(fig)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

def plot_mep_conflicts_3d(result_state, parent_window=None, status_overrides=None):
    """Static 3D visualization of the three new MEP rules (fire hose, cabinet, outlet)."""
    try:
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        import numpy as np
        from matplotlib import patheffects
    except ImportError as e:
        messagebox.showerror("Error", f"Missing module: {e}")
        return

    comp_data = result_state.get("component_data", {})
    rows = result_state.get("rows", [])
    if not comp_data:
        messagebox.showinfo("MEP 3D Viz", "No component data available. Run analysis first.")
        return

    # Collect rule status for rules 16, 17, 18
    status = {16: "N/A", 17: "N/A", 18: "N/A"}
    if status_overrides:
        # Use overridden values if provided
        for k in status:
            if k in status_overrides:
                status[k] = status_overrides[k]
    else:
        # Otherwise read from the result rows
        for row in rows:
            if row["rule_number"] in (16, 17, 18):
                status[row["rule_number"]] = row["result"]

    color_map = {"PASS": "green", "FAIL": "red", "N/A": "gray"}

    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection='3d')
    ax.set_title("MEP Conflict Visualization (3D – New Rules)", fontsize=16, fontweight='bold')

    # Stair flight (translucent box, background)
    vertices = [
        [(0,0,0), (3,0,0), (3,2,0), (0,2,0)],
        [(0,0,0.2), (3,0,0.2), (3,2,0.2), (0,2,0.2)],
        [(0,0,0), (3,0,0), (3,0,0.2), (0,0,0.2)],
        [(3,0,0), (3,2,0), (3,2,0.2), (3,0,0.2)],
        [(3,2,0), (0,2,0), (0,2,0.2), (3,2,0.2)],
        [(0,2,0), (0,0,0), (0,0,0.2), (0,2,0.2)]
    ]
    stair_box = Poly3DCollection(vertices, alpha=0.3, facecolor='lightgray', edgecolor='black', zorder=0)
    ax.add_collection3d(stair_box)

    # Guardrail (thin blue line on top)
    guardrail_x = [0, 3]
    guardrail_y = [2.1, 2.1]
    guardrail_z = [0.25, 0.25]
    ax.plot(guardrail_x, guardrail_y, guardrail_z, color='blue', linewidth=3, linestyle='--', label='Guardrail', zorder=2)

    # 1. Fire hose valve (small sphere on right wall)
    hose_x, hose_y, hose_z = 3.2, 1.4, 0.6
    u = np.linspace(0, 2*np.pi, 15)
    v = np.linspace(0, np.pi, 15)
    x_sphere = hose_x + 0.1 * np.outer(np.cos(u), np.sin(v))
    y_sphere = hose_y + 0.1 * np.outer(np.sin(u), np.sin(v))
    z_sphere = hose_z + 0.1 * np.outer(np.ones_like(u), np.cos(v))
    ax.plot_surface(x_sphere, y_sphere, z_sphere, color=color_map.get(status[16], 'gray'), alpha=0.9, zorder=3)

    # 2. Fire extinguisher cabinet (box on left wall)
    cab_x0, cab_y0, cab_z0 = -0.3, 0.8, 0
    cab_w, cab_d, cab_h = 0.3, 0.3, 0.6
    cab_vertices = [
        [(cab_x0, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0)],
        [(cab_x0, cab_y0, cab_z0+cab_h), (cab_x0+cab_w, cab_y0, cab_z0+cab_h), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h), (cab_x0, cab_y0+cab_d, cab_z0+cab_h)],
        [(cab_x0, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0+cab_h), (cab_x0, cab_y0, cab_z0+cab_h)],
        [(cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h), (cab_x0+cab_w, cab_y0, cab_z0+cab_h)],
        [(cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0+cab_h), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h)],
        [(cab_x0, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0, cab_z0), (cab_x0, cab_y0, cab_z0+cab_h), (cab_x0, cab_y0+cab_d, cab_z0+cab_h)]
    ]
    cab_box = Poly3DCollection(cab_vertices, alpha=0.9, facecolor=color_map.get(status[17], 'gray'), edgecolor='black', zorder=3)
    ax.add_collection3d(cab_box)

    # 3. Electrical outlet – small cylinder (using a circle extruded)
    out_x, out_y, out_z = 1.2, -0.2, 0.02
    radius = 0.08
    height = 0.02
    theta = np.linspace(0, 2*np.pi, 20)
    x_cyl = out_x + radius * np.cos(theta)
    y_cyl = out_y + radius * np.sin(theta)
    z_bottom = out_z
    z_top = out_z + height
    verts_bottom = [list(zip(x_cyl, y_cyl, [z_bottom]*len(x_cyl)))]
    verts_top = [list(zip(x_cyl, y_cyl, [z_top]*len(x_cyl)))]
    side_faces = []
    for i in range(len(theta)-1):
        side_faces.append([
            (x_cyl[i], y_cyl[i], z_bottom),
            (x_cyl[i+1], y_cyl[i+1], z_bottom),
            (x_cyl[i+1], y_cyl[i+1], z_top),
            (x_cyl[i], y_cyl[i], z_top)
        ])
    cylinder_parts = Poly3DCollection(verts_bottom + verts_top + side_faces,
                                      facecolor=color_map.get(status[18], 'gray'),
                                      edgecolor='black', alpha=0.9, zorder=3)
    ax.add_collection3d(cylinder_parts)

    # Text labels with white stroke
    text1 = ax.text(3.2, 1.4, 0.9, f"Fire Hose\nRule 16: {status[16]}", ha='center', va='center', fontsize=9, color='black', weight='bold', zorder=10)
    text1.set_path_effects([patheffects.withStroke(linewidth=3, foreground='white')])
    text2 = ax.text(-0.45, 0.95, 0.4, f"Cabinet\nRule 17: {status[17]}", ha='center', va='center', fontsize=9, color='black', weight='bold', zorder=10)
    text2.set_path_effects([patheffects.withStroke(linewidth=3, foreground='white')])
    text3 = ax.text(1.3, -0.35, 0.1, f"Outlet\nRule 18: {status[18]}", ha='center', va='center', fontsize=9, color='black', weight='bold', zorder=10)
    text3.set_path_effects([patheffects.withStroke(linewidth=3, foreground='white')])

    # Axes settings
    ax.set_xlim(-1, 5)
    ax.set_ylim(-1, 3)
    ax.set_zlim(0, 3)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.view_init(elev=25, azim=-60)

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color='green', lw=4, label='PASS'),
        Line2D([0], [0], color='red', lw=4, label='FAIL'),
        Line2D([0], [0], color='gray', lw=4, label='N/A')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=10)

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    win = tk.Toplevel(parent_window) if parent_window else tk.Toplevel()
    win.title("3D MEP Conflict Visualization (New Rules)")
    win.geometry("900x700")
    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=5)

    def on_close():
        plt.close(fig)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

def plot_enhanced_mep_viz(result_state, parent_window=None, status_overrides=None, stair_params=None, mep_params=None):
    """
    Enhanced 2D visualization showing stair and MEP parameters that affect rules.
    status_overrides: dict {16: "PASS/FAIL/N/A", ...}
    stair_params: dict with keys h, g, number_of_risers, climb_height
    mep_params: dict with keys fire_hose_height, cabinet_depth, outlet_height
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle, Circle, FancyBboxPatch
        from matplotlib.lines import Line2D
    except ImportError:
        messagebox.showerror("Error", "Please install matplotlib: pip install matplotlib")
        return

    comp_data = result_state.get("component_data", {})
    rows = result_state.get("rows", [])
    if not comp_data and not (stair_params or mep_params):
        messagebox.showinfo("MEP Viz", "No component data available. Run analysis first.")
        return

    # Get rule statuses
    status = {16: "N/A", 17: "N/A", 18: "N/A"}
    if status_overrides:
        for k in status:
            if k in status_overrides:
                status[k] = status_overrides[k]
    else:
        for row in rows:
            if row["rule_number"] in (16, 17, 18):
                status[row["rule_number"]] = row["result"]

    # Extract stair parameters (use passed, or from comp_data)
    if stair_params is None:
        # Try to find from component_data
        for key, data in comp_data.items():
            if key[0] == "IfcStairFlight":
                stair_params = {
                    "h": data.get("h", "?"),
                    "g": data.get("g", "?"),
                    "number_of_risers": data.get("number of risers", "?"),
                    "climb_height": data.get("climb height", "?")
                }
                break
        if stair_params is None:
            stair_params = {"h": "?", "g": "?", "number_of_risers": "?", "climb_height": "?"}

    # Extract MEP parameters
    if mep_params is None:
        mep_params = {
            "fire_hose_height": "?",
            "cabinet_depth": "?",
            "outlet_height": "?"
        }
        for key, data in comp_data.items():
            if key[0] == "IfcFireSuppressionTerminal":
                if "hose" in data.get("name", "").lower():
                    mep_params["fire_hose_height"] = data.get("fire_hose_height", "?")
                elif "cabinet" in data.get("name", "").lower():
                    mep_params["cabinet_depth"] = data.get("cabinet_depth", "?")
            elif key[0] == "IfcOutlet":
                mep_params["outlet_height"] = data.get("outlet_height", "?")

    # Helper to format values
    def fmt(v):
        if v is None or v == "?":
            return "?"
        try:
            return f"{float(v):.1f}"
        except:
            return str(v)

    color_map = {"PASS": "green", "FAIL": "red", "N/A": "gray"}

    fig, ax = plt.subplots(figsize=(14, 8))
    ax.set_xlim(-1.5, 5.5)
    ax.set_ylim(-0.5, 4.5)
    ax.set_aspect('equal')
    ax.set_title("Enhanced MEP Conflict Visualization (with Parameters)", fontsize=16, fontweight='bold')
    ax.axis('off')

    # ---- Stair flight (main rectangle) ----
    stair_rect = Rectangle((0.5, 0), 3, 2.5, facecolor='lightgray', edgecolor='black',
                           linewidth=2, alpha=0.7, hatch='///')
    ax.add_patch(stair_rect)
    ax.text(2, 1.2, "STAIRCASE\nFLIGHT", ha='center', va='center', fontsize=12, fontweight='bold',
            bbox=dict(facecolor='white', edgecolor='none', alpha=0.7))

    # Display stair parameters inside the rectangle (or nearby)
    param_text = f"h = {fmt(stair_params.get('h'))} mm\n"
    param_text += f"g = {fmt(stair_params.get('g'))} mm\n"
    param_text += f"Risers = {fmt(stair_params.get('number_of_risers'))}\n"
    param_text += f"Climb = {fmt(stair_params.get('climb_height'))} mm"
    param_text += f"Guardrail = {fmt(mep_params.get('guardrail_height', '?'))} mm"
    ax.text(2, 0.4, param_text, ha='center', va='bottom', fontsize=9,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.3'))

    # Guardrail (line above)
    ax.plot([0.5, 3.5], [2.6, 2.6], color='blue', linewidth=3, linestyle='--', label='Guardrail (top)')
    ax.text(3.6, 2.55, f"Guardrail ≥1100mm", fontsize=8, color='blue')

    # ---- Fire hose valve ----
    hose_x, hose_y = 3.8, 1.4
    hose_color = color_map.get(status[16], 'gray')
    hose_circle = Circle((hose_x, hose_y), 0.15, facecolor=hose_color, edgecolor='black', linewidth=2)
    ax.add_patch(hose_circle)
    ax.text(hose_x, hose_y+0.25, "Fire Hose Valve", ha='center', fontsize=9, fontweight='bold')
    ax.text(hose_x, hose_y-0.25, f"Height = {fmt(mep_params.get('fire_hose_height'))} mm", ha='center', fontsize=8)
    ax.text(hose_x, hose_y-0.45, f"Rule 16: {status[16]}", ha='center', fontsize=8,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))
    # Show condition
    if status[16] == "FAIL":
        ax.text(hose_x+0.6, hose_y, "✗ Risers >12 & hose in zone", fontsize=7, color='red')

    # ---- Fire extinguisher cabinet ----
    cab_x, cab_y = -0.3, 0.8
    cab_w, cab_h = 0.4, 0.6
    cab_color = color_map.get(status[17], 'gray')
    cab_rect = Rectangle((cab_x, cab_y), cab_w, cab_h, facecolor=cab_color, edgecolor='black', linewidth=2)
    ax.add_patch(cab_rect)
    ax.text(cab_x+cab_w/2, cab_y+cab_h+0.1, "Cabinet", ha='center', fontsize=9, fontweight='bold')
    ax.text(cab_x+cab_w/2, cab_y-0.15, f"Depth = {fmt(mep_params.get('cabinet_depth'))} mm", ha='center', fontsize=8)
    ax.text(cab_x+cab_w/2, cab_y-0.35, f"Rule 17: {status[17]}", ha='center', fontsize=8,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))
    if status[17] == "FAIL":
        ax.text(cab_x-0.5, cab_y+cab_h/2, "✗ Depth>100 & (g<1200 or climb>6000)", fontsize=7, color='red', rotation=90)

    # ---- Electrical outlet ----
    out_x, out_y = 1.5, -0.2
    out_w, out_h = 0.25, 0.25
    out_color = color_map.get(status[18], 'gray')
    out_rect = Rectangle((out_x, out_y), out_w, out_h, facecolor=out_color, edgecolor='black', linewidth=2, hatch='//')
    ax.add_patch(out_rect)
    ax.text(out_x+out_w/2, out_y-0.25, "Outlet", ha='center', fontsize=9, fontweight='bold')
    ax.text(out_x+out_w/2, out_y-0.45, f"Height = {fmt(mep_params.get('outlet_height'))} mm", ha='center', fontsize=8)
    ax.text(out_x+out_w/2, out_y-0.65, f"Rule 18: {status[18]}", ha='center', fontsize=8,
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.2'))
    if status[18] == "FAIL":
        ax.text(out_x+0.4, out_y+0.1, "✗ (h>190 or g<240) & outlet in 600-1200mm", fontsize=7, color='red')

    # ---- Legend and thresholds display ----
    legend_elements = [
        Line2D([0], [0], color='green', lw=4, label='PASS'),
        Line2D([0], [0], color='red', lw=4, label='FAIL'),
        Line2D([0], [0], color='gray', lw=4, label='N/A')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=10)

    # Add a text box with threshold reminders
    thresholds = (
        "Rule 16: Guardrail ≥1100, Hose 900-1500 → conflict if risers>12\n"
        "Rule 17: Cabinet depth >100 → conflict if g<1200 or climb>6000\n"
        "Rule 18: Outlet 600-1200 → conflict if h>190 or g<240"
    )
    ax.text(4.5, 4.0, thresholds, ha='right', va='top', fontsize=7,
            bbox=dict(facecolor='lightyellow', edgecolor='gray', boxstyle='round,pad=0.3'))

    # Embed in Tkinter
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    win = tk.Toplevel(parent_window) if parent_window else tk.Toplevel()
    win.title("Enhanced MEP Conflict Visualization")
    win.geometry("1100x750")
    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=5)

    def on_close():
        plt.close(fig)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

def plot_enhanced_mep_viz_3d(result_state, parent_window=None, status_overrides=None, stair_params=None, mep_params=None):
    """
    Enhanced 3D visualization showing stair and MEP parameters that affect rules.
    status_overrides: dict {16: "PASS/FAIL/N/A", ...}
    stair_params: dict with keys h, g, number_of_risers, climb_height
    mep_params: dict with keys fire_hose_height, cabinet_depth, outlet_height
    """
    try:
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        import numpy as np
        from matplotlib import patheffects
        from matplotlib.lines import Line2D
    except ImportError as e:
        messagebox.showerror("Error", f"Missing module: {e}")
        return

    comp_data = result_state.get("component_data", {})
    rows = result_state.get("rows", [])
    if not comp_data and not (stair_params or mep_params):
        messagebox.showinfo("MEP 3D Viz", "No component data available. Run analysis first.")
        return

    # Get rule statuses
    status = {16: "N/A", 17: "N/A", 18: "N/A"}
    if status_overrides:
        for k in status:
            if k in status_overrides:
                status[k] = status_overrides[k]
    else:
        for row in rows:
            if row["rule_number"] in (16, 17, 18):
                status[row["rule_number"]] = row["result"]

    # Extract stair parameters (use passed, or from comp_data)
    if stair_params is None:
        for key, data in comp_data.items():
            if key[0] == "IfcStairFlight":
                stair_params = {
                    "h": data.get("h", "?"),
                    "g": data.get("g", "?"),
                    "number_of_risers": data.get("number of risers", "?"),
                    "climb_height": data.get("climb height", "?")
                }
                break
        if stair_params is None:
            stair_params = {"h": "?", "g": "?", "number_of_risers": "?", "climb_height": "?"}

    # Extract MEP parameters
    if mep_params is None:
        mep_params = {
            "fire_hose_height": "?",
            "cabinet_depth": "?",
            "outlet_height": "?"
        }
        for key, data in comp_data.items():
            if key[0] == "IfcFireSuppressionTerminal":
                if "hose" in data.get("name", "").lower():
                    mep_params["fire_hose_height"] = data.get("fire_hose_height", "?")
                elif "cabinet" in data.get("name", "").lower():
                    mep_params["cabinet_depth"] = data.get("cabinet_depth", "?")
            elif key[0] == "IfcOutlet":
                mep_params["outlet_height"] = data.get("outlet_height", "?")

    # Helper to format values
    def fmt(v):
        if v is None or v == "?":
            return "?"
        try:
            return f"{float(v):.1f}"
        except:
            return str(v)

    color_map = {"PASS": "green", "FAIL": "red", "N/A": "gray"}

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')
    ax.set_title("Enhanced 3D MEP Conflict Visualization (with Parameters)", fontsize=16, fontweight='bold')

    # Stair flight (translucent box, background)
    vertices = [
        [(0,0,0), (3,0,0), (3,2,0), (0,2,0)],
        [(0,0,0.2), (3,0,0.2), (3,2,0.2), (0,2,0.2)],
        [(0,0,0), (3,0,0), (3,0,0.2), (0,0,0.2)],
        [(3,0,0), (3,2,0), (3,2,0.2), (3,0,0.2)],
        [(3,2,0), (0,2,0), (0,2,0.2), (3,2,0.2)],
        [(0,2,0), (0,0,0), (0,0,0.2), (0,2,0.2)]
    ]
    stair_box = Poly3DCollection(vertices, alpha=0.3, facecolor='lightgray', edgecolor='black', zorder=0)
    ax.add_collection3d(stair_box)

    # Add stair parameter text as a 3D annotation (floating)
    param_text = f"STAIR: h={fmt(stair_params.get('h'))} mm, g={fmt(stair_params.get('g'))} mm, Risers={fmt(stair_params.get('number_of_risers'))}, Climb={fmt(stair_params.get('climb_height'))} mm, Guardrail={fmt(mep_params.get('guardrail_height', '?'))} mm"
    ax.text(1.5, -0.5, 1.2, param_text, fontsize=9, ha='center', va='center',
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.3'), zorder=10)

    # Guardrail (blue line)
    guardrail_x = [0, 3]
    guardrail_y = [2.1, 2.1]
    guardrail_z = [0.25, 0.25]
    ax.plot(guardrail_x, guardrail_y, guardrail_z, color='blue', linewidth=3, linestyle='--', label='Guardrail', zorder=2)
    ax.text(3.2, 2.1, 0.25, "Guardrail ≥1100mm", fontsize=8, color='blue', zorder=10)

    # 1. Fire hose valve (sphere)
    hose_x, hose_y, hose_z = 3.2, 1.4, 0.6
    u = np.linspace(0, 2*np.pi, 15)
    v = np.linspace(0, np.pi, 15)
    x_sphere = hose_x + 0.12 * np.outer(np.cos(u), np.sin(v))
    y_sphere = hose_y + 0.12 * np.outer(np.sin(u), np.sin(v))
    z_sphere = hose_z + 0.12 * np.outer(np.ones_like(u), np.cos(v))
    ax.plot_surface(x_sphere, y_sphere, z_sphere, color=color_map.get(status[16], 'gray'), alpha=0.9, zorder=3)
    # Text label with parameters
    ax.text(hose_x, hose_y+0.3, hose_z+0.2, f"Fire Hose\nHeight={fmt(mep_params.get('fire_hose_height'))} mm\nRule 16: {status[16]}",
            ha='center', fontsize=8, color='black', weight='bold', zorder=10,
            bbox=dict(facecolor='white', alpha=0.7, boxstyle='round,pad=0.2'))

    # 2. Cabinet (box)
    cab_x0, cab_y0, cab_z0 = -0.3, 0.8, 0
    cab_w, cab_d, cab_h = 0.35, 0.35, 0.6
    cab_vertices = [
        [(cab_x0, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0)],
        [(cab_x0, cab_y0, cab_z0+cab_h), (cab_x0+cab_w, cab_y0, cab_z0+cab_h), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h), (cab_x0, cab_y0+cab_d, cab_z0+cab_h)],
        [(cab_x0, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0, cab_z0+cab_h), (cab_x0, cab_y0, cab_z0+cab_h)],
        [(cab_x0+cab_w, cab_y0, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h), (cab_x0+cab_w, cab_y0, cab_z0+cab_h)],
        [(cab_x0+cab_w, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0+cab_d, cab_z0+cab_h), (cab_x0+cab_w, cab_y0+cab_d, cab_z0+cab_h)],
        [(cab_x0, cab_y0+cab_d, cab_z0), (cab_x0, cab_y0, cab_z0), (cab_x0, cab_y0, cab_z0+cab_h), (cab_x0, cab_y0+cab_d, cab_z0+cab_h)]
    ]
    cab_box = Poly3DCollection(cab_vertices, alpha=0.9, facecolor=color_map.get(status[17], 'gray'), edgecolor='black', zorder=3)
    ax.add_collection3d(cab_box)
    ax.text(cab_x0+cab_w/2, cab_y0-0.2, cab_h/2, f"Cabinet\nDepth={fmt(mep_params.get('cabinet_depth'))} mm\nRule 17: {status[17]}",
            ha='center', fontsize=8, color='black', weight='bold', zorder=10,
            bbox=dict(facecolor='white', alpha=0.7, boxstyle='round,pad=0.2'))

    # 3. Outlet (cylinder)
    out_x, out_y, out_z = 1.2, -0.25, 0.02
    radius = 0.1
    height = 0.03
    theta = np.linspace(0, 2*np.pi, 20)
    x_cyl = out_x + radius * np.cos(theta)
    y_cyl = out_y + radius * np.sin(theta)
    z_bottom = out_z
    z_top = out_z + height
    verts_bottom = [list(zip(x_cyl, y_cyl, [z_bottom]*len(x_cyl)))]
    verts_top = [list(zip(x_cyl, y_cyl, [z_top]*len(x_cyl)))]
    side_faces = []
    for i in range(len(theta)-1):
        side_faces.append([
            (x_cyl[i], y_cyl[i], z_bottom),
            (x_cyl[i+1], y_cyl[i+1], z_bottom),
            (x_cyl[i+1], y_cyl[i+1], z_top),
            (x_cyl[i], y_cyl[i], z_top)
        ])
    cylinder_parts = Poly3DCollection(verts_bottom + verts_top + side_faces,
                                      facecolor=color_map.get(status[18], 'gray'),
                                      edgecolor='black', alpha=0.9, zorder=3)
    ax.add_collection3d(cylinder_parts)
    ax.text(out_x, out_y-0.3, out_z+0.05, f"Outlet\nHeight={fmt(mep_params.get('outlet_height'))} mm\nRule 18: {status[18]}",
            ha='center', fontsize=8, color='black', weight='bold', zorder=10,
            bbox=dict(facecolor='white', alpha=0.7, boxstyle='round,pad=0.2'))

    # Add threshold annotations
    thresholds = (
        "Rule 16: Guardrail ≥1100, Hose 900-1500 → conflict if risers>12\n"
        "Rule 17: Cabinet depth >100 → conflict if g<1200 or climb>6000\n"
        "Rule 18: Outlet 600-1200 → conflict if h>190 or g<240"
    )
    ax.text(4.5, 2.5, 1.5, thresholds, fontsize=7, color='black', ha='right', va='top',
            bbox=dict(facecolor='lightyellow', edgecolor='gray', boxstyle='round,pad=0.3'), zorder=10)

    # Axes settings
    ax.set_xlim(-1, 5)
    ax.set_ylim(-1, 3)
    ax.set_zlim(0, 3)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.view_init(elev=25, azim=-60)

    legend_elements = [
        Line2D([0], [0], color='green', lw=4, label='PASS'),
        Line2D([0], [0], color='red', lw=4, label='FAIL'),
        Line2D([0], [0], color='gray', lw=4, label='N/A')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=10)

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    win = tk.Toplevel(parent_window) if parent_window else tk.Toplevel()
    win.title("Enhanced 3D MEP Conflict Visualization")
    win.geometry("1100x800")
    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=5)

    def on_close():
        plt.close(fig)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

# ------------------------------
# Impact results display
# ------------------------------
def show_impact_results(impacts, parent_window, extra_button_callback=None):
    win = tk.Toplevel(parent_window)
    win.title("Impact Analysis Results")
    win.geometry("900x500")
    tree = ttk.Treeview(win, columns=("rule", "old", "new", "reason"), show="tree headings")
    tree.heading("#0", text="Component")
    tree.heading("rule", text="Rule")
    tree.heading("old", text="Old Status")
    tree.heading("new", text="New Status")
    tree.heading("reason", text="Reason")
    tree.column("#0", width=250)
    tree.column("rule", width=80)
    tree.column("old", width=100)
    tree.column("new", width=100)
    tree.column("reason", width=300)

    grouped = defaultdict(list)
    for imp in impacts:
        grouped[imp["component_key"]].append(imp)
    for key, items in grouped.items():
        comp_disp = f"{key[0]} #{key[1]} - {items[0]['component_name']}"
        parent = tree.insert("", "end", text=comp_disp, open=True)
        for it in items:
            tree.insert(parent, "end", text="", values=(
                f"Rule {it['rule_id']}", it['old_status'], it['new_status'], it['reason']
            ))
    tree.pack(fill=tk.BOTH, expand=True)

    btn_frame = ttk.Frame(win)
    btn_frame.pack(pady=5)
    if extra_button_callback:
        ttk.Button(btn_frame, text="Show Updated MEP Visualization", command=extra_button_callback).pack(side=tk.LEFT,
                                                                                                         padx=5)
    ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side=tk.LEFT, padx=5)

# ------------------------------
# Ontology save/load (JSON and TTL)
# ------------------------------
def save_ontology_to_file(result_state, status_label):
    if not result_state.get("component_data") and not result_state.get("ontology"):
        messagebox.showwarning("Save Ontology", "No data to save.")
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("Turtle files", "*.ttl")]
    )
    if not path:
        return
    try:
        if path.endswith(".ttl"):
            if result_state.get("ontology"):
                if ontology_to_ttl(result_state["ontology"], path):
                    set_status(status_label, f"Saved TTL: {os.path.basename(path)}", "done")
                    messagebox.showinfo("Saved", f"TTL ontology saved to {path}")
                else:
                    raise Exception("TTL export failed")
            else:
                messagebox.showerror("Error", "No ontology to export to TTL.")
        else:
            data = serialize_result_state(result_state)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            set_status(status_label, f"Saved JSON: {os.path.basename(path)}", "done")
            messagebox.showinfo("Saved", f"JSON saved to {path}")
    except Exception as e:
        set_status(status_label, f"Save failed: {e}", "error")
        messagebox.showerror("Error", str(e))

def load_ontology_from_file(result_state, status_label, excel_btn, pdf_btn, sim_btn, ifc_var, output_text, run_btn):
    path = filedialog.askopenfilename(
        filetypes=[("JSON files", "*.json"), ("Turtle files", "*.ttl"), ("All files", "*.*")]
    )
    if not path:
        return
    try:
        if path.endswith(".ttl"):
            onto = ttl_to_ontology(path)
            if onto is None:
                raise Exception("Failed to parse TTL")
            # try to load accompanying JSON with numeric data (same basename)
            base = os.path.splitext(path)[0]
            json_path = base + ".json"
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                new_state = deserialize_result_state(data)
                if new_state.get("ontology") is not None:
                    new_state["ontology"].compute_transitive_closure("isPartOf")
                result_state.clear()
                result_state.update(new_state)
                result_state["ontology"] = onto
                if result_state.get("rows"):
                    set_export_buttons(excel_btn, pdf_btn, True)
                    sim_btn.state(["!disabled"])
                output_text.configure(state=tk.NORMAL)
                output_text.delete("1.0", tk.END)
                output_text.insert(tk.END, f"Loaded TTL + JSON from {base}\n")
                output_text.configure(state=tk.DISABLED)
                set_status(status_label, f"Loaded TTL+JSON: {os.path.basename(path)}", "done")
                messagebox.showinfo("Loaded", "TTL ontology and matching JSON numeric data loaded.\nSimulation enabled.")
            else:
                reset_result_state(result_state)
                result_state["ontology"] = onto
                set_export_buttons(excel_btn, pdf_btn, False)
                sim_btn.state(["disabled"])
                output_text.configure(state=tk.NORMAL)
                output_text.delete("1.0", tk.END)
                output_text.insert(tk.END, f"Loaded TTL ontology from {path}\nNo numeric data. Simulation disabled.\n")
                output_text.configure(state=tk.DISABLED)
                set_status(status_label, "TTL loaded (no numeric data)", "warn")
                messagebox.showinfo("Loaded", "TTL loaded without numeric data.\nSimulation not available.")
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            new_state = deserialize_result_state(data)
            if new_state.get("ontology") is not None:
                new_state["ontology"].compute_transitive_closure("isPartOf")
            result_state.clear()
            result_state.update(new_state)
            if result_state.get("rows"):
                set_export_buttons(excel_btn, pdf_btn, True)
                sim_btn.state(["!disabled"])
            else:
                set_export_buttons(excel_btn, pdf_btn, False)
                sim_btn.state(["disabled"])
            if result_state.get("ifc_file"):
                ifc_var.set(result_state["ifc_file"])
            output_text.configure(state=tk.NORMAL)
            output_text.delete("1.0", tk.END)
            output_text.insert(tk.END, f"Loaded JSON ontology from {path}\n")
            output_text.insert(tk.END, f"IFC: {result_state.get('ifc_file', '')}\n")
            output_text.configure(state=tk.DISABLED)
            set_status(status_label, f"Loaded {os.path.basename(path)}", "done")
            messagebox.showinfo("Loaded", "JSON ontology loaded successfully.")
    except Exception as e:
        set_status(status_label, f"Load failed: {e}", "error")
        messagebox.showerror("Error", str(e))

# ------------------------------
# Apply reasoner (OWL RL) on TTL file and merge inferred relations
# ------------------------------
def apply_reasoner(result_state, parent_window=None, excel_btn=None, pdf_btn=None, sim_btn=None):
    """Apply transitive closure on isPartOf relations in the current ontology (or a loaded TTL)."""
    try:
        from rdflib import Graph, Namespace
        from rdflib.namespace import RDF, RDFS, OWL
    except ImportError:
        messagebox.showerror("Missing Library", "Please install rdflib: pip install rdflib")
        return

    # گزینه: اعمال روی آنتولوژی جاری یا بارگذاری فایل TTL
    answer = messagebox.askyesno("Reasoner", "Apply transitive closure on current ontology? (Yes = current, No = load TTL file)")
    if answer:
        # اعمال روی آنتولوژی جاری
        onto = result_state.get("ontology")
        if onto is None:
            messagebox.showwarning("No Ontology", "No ontology loaded. Run analysis or load a JSON file first.")
            return
        # شمارش روابط قبل
        before = len(onto.relations)
        # محاسبه بستار ترایا (اگر قبلاً انجام نشده باشد)
        onto.compute_transitive_closure("isPartOf")
        after = len(onto.relations)
        inferred = after - before
        msg = f"Transitive closure completed on current ontology.\nRelations: {before} → {after} (+{inferred} new)."
        messagebox.showinfo("Reasoner Result", msg)
        if inferred > 0:
            result_state["ontology"] = onto
            if result_state.get("component_data") and sim_btn:
                sim_btn.state(["!disabled"])
        return

    # در غیر این صورت، از فایل TTL بخواند (رفتار قبلی با بهبود)
    path = filedialog.askopenfilename(filetypes=[("Turtle files", "*.ttl")])
    if not path:
        return

    try:
        g = Graph()
        g.parse(path, format="turtle")

        # یافتن predicate‌های حاوی isPartOf
        partof_predicates = set()
        for s, p, o in g:
            if "isPartOf" in str(p):
                partof_predicates.add(p)

        if not partof_predicates:
            messagebox.showinfo("Reasoner", "No isPartOf relations found in the TTL file.")
            return

        original_triples = set()
        for pred in partof_predicates:
            for s, p, o in g.triples((None, pred, None)):
                original_triples.add((s, p, o))

        # محاسبه بستار ترایا به روش دستی
        changed = True
        iteration = 0
        while changed and iteration < 20:
            iteration += 1
            changed = False
            for pred in partof_predicates:
                pairs = list(g.triples((None, pred, None)))
                for s, p, o in pairs:
                    for s2, p2, o2 in g.triples((o, pred, None)):
                        if (s, pred, o2) not in g:
                            g.add((s, pred, o2))
                            changed = True

        new_triples = set()
        for pred in partof_predicates:
            for s, p, o in g.triples((None, pred, None)):
                if (s, p, o) not in original_triples:
                    new_triples.add((s, p, o))

        inferred = len(new_triples)
        if inferred == 0:
            messagebox.showinfo("No new relations", "No new isPartOf relations inferred from this TTL file.")
            return

        # تبدیل URI به کلید‌های آنتولوژی با regex بهبودیافته
        import re
        new_rels = []
        for s, p, o in new_triples:
            s_str = str(s)
            o_str = str(o)
            # الگوی جدید: پشتیبانی از # یا / و هر کاراکتر غیر whitespace قبل از _
            s_match = re.search(r'[#/]([A-Za-z0-9_]+)_(\d+)(?![a-zA-Z0-9_])', s_str)
            o_match = re.search(r'[#/]([A-Za-z0-9_]+)_(\d+)(?![a-zA-Z0-9_])', o_str)
            if s_match and o_match:
                s_type = s_match.group(1)
                s_id = int(s_match.group(2))
                o_type = o_match.group(1)
                o_id = int(o_match.group(2))
                new_rels.append(((s_type, s_id), "isPartOf", (o_type, o_id)))
            else:
                # تلاش با الگوی ساده‌تر: هر چیزی که به _ عدد ختم شود
                s_match2 = re.search(r'_(\d+)$', s_str)
                o_match2 = re.search(r'_(\d+)$', o_str)
                if s_match2 and o_match2:
                    # نوع را از کل URI استخراج کنیم (آخرین قسمت قبل از _)
                    s_type_match = re.search(r'[#/]([A-Za-z0-9_]+)_\d+$', s_str)
                    o_type_match = re.search(r'[#/]([A-Za-z0-9_]+)_\d+$', o_str)
                    if s_type_match and o_type_match:
                        s_type = s_type_match.group(1)
                        o_type = o_type_match.group(1)
                        s_id = int(s_match2.group(1))
                        o_id = int(o_match2.group(1))
                        new_rels.append(((s_type, s_id), "isPartOf", (o_type, o_id)))

        if not new_rels:
            messagebox.showinfo("Warning", f"Inferred {inferred} relations but could not convert to ontology format.\nCheck that URIs follow pattern '#ClassName_123'.")
            return

        # ادغام با آنتولوژی جاری
        if messagebox.askyesno("Merge Relations", f"Add {len(new_rels)} inferred relations to current ontology?"):
            current_onto = result_state.get("ontology")
            if current_onto is None:
                current_onto = Ontology()
                result_state["ontology"] = current_onto
            added = 0
            for s, p, o in new_rels:
                if (s, p, o) not in current_onto.relations:
                    current_onto.add_relation(s, p, o)
                    added += 1
            messagebox.showinfo("Ontology Updated", f"Added {added} new relations.\nSimulation ready.")
            if result_state.get("component_data") and sim_btn:
                sim_btn.state(["!disabled"])

        # ذخیره فایل TTL استنتاج‌شده (اختیاری)
        if messagebox.askyesno("Save Inferred", "Save inferred ontology as a new TTL file?"):
            save_path = filedialog.asksaveasfilename(defaultextension=".ttl", filetypes=[("Turtle files", "*.ttl")])
            if save_path:
                g.serialize(destination=save_path, format="turtle")
                messagebox.showinfo("Saved", f"Inferred ontology saved to {save_path}")

    except Exception as e:
        messagebox.showerror("Reasoner Error", str(e))

# ------------------------------
# Simulation dialog
# ------------------------------
def show_simulation_dialog(parent, result_state, status_label):
    """Display dialog for change impact simulation with MEP integration."""
    comp_data = result_state.get("component_data")
    if not comp_data:
        messagebox.showwarning("Simulate Change", "No component data available. Run analysis or load ontology first.")
        return

    comp_keys = list(comp_data.keys())
    if not comp_keys:
        messagebox.showwarning("Simulate Change", "No components found in data.")
        return

    dialog = tk.Toplevel(parent)
    dialog.title("Simulate Change Impact")
    dialog.geometry("750x600")
    dialog.transient(parent)
    dialog.grab_set()

    main_frame = ttk.Frame(dialog, padding="10")
    main_frame.pack(fill=tk.BOTH, expand=True)

    # Component selection
    ttk.Label(main_frame, text="Select Component:", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0,5))
    comp_var = tk.StringVar()
    comp_display = []
    for key in comp_keys:
        comp_type, eid = key
        name = comp_data[key].get("name", "?")
        display_str = f"{comp_type} #{eid} - {name}"
        comp_display.append(display_str)
    comp_combo = ttk.Combobox(main_frame, textvariable=comp_var, values=comp_display, width=70, state="readonly")
    comp_combo.pack(fill=tk.X, pady=(0,10))
    if comp_display:
        comp_combo.current(0)

    # Parameter selection
    param_frame = ttk.LabelFrame(main_frame, text="Parameter to Change", padding="5")
    param_frame.pack(fill=tk.X, pady=(0,10))
    param_var = tk.StringVar()
    param_combo = ttk.Combobox(param_frame, textvariable=param_var, width=30, state="readonly")
    param_combo.pack(anchor=tk.W, pady=5)

    def update_params(*args):
        idx = comp_combo.current()
        if idx < 0:
            return
        key = comp_keys[idx]
        data = comp_data[key]
        exclude = {"type", "entity_id", "name", "metadata"}
        params = [k for k in data.keys() if k not in exclude]
        param_combo['values'] = params
        if params:
            param_var.set(params[0])
        else:
            param_var.set("")
    comp_combo.bind("<<ComboboxSelected>>", update_params)
    update_params()

    # New value entry
    val_frame = ttk.LabelFrame(main_frame, text="New Value (mm)", padding="5")
    val_frame.pack(fill=tk.X, pady=(0,10))
    new_val_entry = ttk.Entry(val_frame, width=20)
    new_val_entry.pack(anchor=tk.W)
    ttk.Label(val_frame, text="(For 'number of risers', value is count)", font=("Segoe UI", 8, "italic")).pack(anchor=tk.W, pady=(2,0))

    propagate_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(val_frame, text="Apply change to all flights in same stair (propagation)", variable=propagate_var).pack(anchor=tk.W, pady=(8,0))

    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill=tk.X, pady=10)

    # Store last computed new parameters for MEP visualization
    last_new_params = {"stair_params": None, "mep_status_after": None}

    # ----- Helper functions inside the dialog -----
    def get_unique_failed_items(rs):
        items = []
        seen = set()
        for row in rs["rows"]:
            if row["result"] == "FAIL":
                key = ((row["component_type"], row["entity_id"]), row["rule_number"])
                if key not in seen:
                    seen.add(key)
                    items.append({
                        "comp_key": (row["component_type"], row["entity_id"]),
                        "component_name": row["name"],
                        "rule_id": row["rule_number"],
                        "rule_text": row["rule_text"]
                    })
        return items

    def auto_fix():
        failed = get_unique_failed_items(result_state)
        if not failed:
            messagebox.showinfo("Auto-fix", "No failed rules found.")
            return
        fix_win = tk.Toplevel(dialog)
        fix_win.title("Select Failed Rule to Fix")
        fix_win.geometry("700x500")
        fix_win.transient(dialog)
        fix_win.grab_set()
        lb = tk.Listbox(fix_win, font=("Segoe UI", 9), selectmode=tk.SINGLE)
        lb.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        items = []
        for item in failed:
            disp = f"{item['component_name']} (Entity {item['comp_key'][1]}) - Rule {item['rule_id']}: {item['rule_text'][:80]}"
            lb.insert(tk.END, disp)
            items.append(item)

        def apply_fix():
            sel = lb.curselection()
            if not sel:
                return
            item = items[sel[0]]
            rule_id = item["rule_id"]
            # Determine which component to modify
            if rule_id in [16, 17, 18]:
                target_key = None
                for k in comp_data:
                    if k[0] == "IfcStairFlight":
                        target_key = k
                        break
                if target_key is None:
                    messagebox.showerror("Error", "No stair flight found to apply the fix.")
                    return
                comp_key = target_key
                comp_name = comp_data[target_key].get("name", "Stair Flight")
            else:
                comp_key = item["comp_key"]
                comp_name = item["component_name"]

            # Collect parameters for suggestion
            stair_params = {}
            for k, v in comp_data.items():
                if k[0] == "IfcStairFlight":
                    stair_params.update({
                        "h": v.get("h"),
                        "g": v.get("g"),
                        "number of risers": v.get("number of risers"),
                        "climb height": v.get("climb height"),
                    })
                if k[0] == "IfcRailing" and "height h" in v:
                    stair_params["guardrail_height"] = v.get("height h")
            if stair_params.get("guardrail_height") is None:
                stair_params["guardrail_height"] = 1100.0

            mep_data = comp_data.get(item["comp_key"], {})
            full_params = {**stair_params, **mep_data}
            param, new_val, expl = suggest_fix_for_rule(rule_id, full_params)
            if param is None:
                messagebox.showinfo("No suggestion", f"No automated fix suggestion for Rule {rule_id}.")
                return

            # Verify that the new value actually makes the rule PASS
            test_data = comp_data[comp_key].copy()
            test_data[param] = new_val
            old_val = comp_data[comp_key].get(param)
            comp_data[comp_key][param] = new_val
            analyzer_temp = ChangeImpactAnalyzer()
            analyzer_temp.set_component_data(comp_data, result_state.get("ontology"))
            test_params = full_params.copy()
            test_params[param] = new_val
            new_status = analyzer_temp.evaluate_rule(rule_id, test_params)
            comp_data[comp_key][param] = old_val

            if new_status != "PASS":
                messagebox.showwarning("Fix may not resolve", f"After applying the suggested fix, the rule status would be {new_status}, not PASS.\nYou may need additional changes.")
                if not messagebox.askyesno("Apply anyway", f"{expl}\n\nApply this change anyway?"):
                    return
            else:
                if not messagebox.askyesno("Apply Fix", f"{expl}\n\nThis change will make the rule PASS.\nApply it?"):
                    return

            # Apply the change and run impact analysis
            ontology = result_state.get("ontology")
            analyzer = ChangeImpactAnalyzer()
            analyzer.set_component_data(comp_data, result_state.get("ontology"))
            impacts = analyzer.find_impact(comp_key, param, new_val)
            if not impacts:
                impacts = [{
                    "component_key": comp_key,
                    "component_name": comp_name,
                    "component_metadata": comp_data[comp_key].get("metadata", ""),
                    "rule_id": rule_id,
                    "old_status": "FAIL",
                    "new_status": new_status,
                    "reason": f"Change {param} from {old_val} to {new_val} to satisfy rule."
                }]
            show_impact_results(impacts, fix_win)

        ttk.Button(fix_win, text="Apply Fix and Simulate", command=apply_fix).pack(pady=5)
        ttk.Button(fix_win, text="Close", command=fix_win.destroy).pack(pady=5)

    # ----- MEP visualization helper -----
    def show_updated_mep_viz(parent_win):
        if not last_new_params.get("stair_params"):
            messagebox.showinfo("No change", "Please run an impact analysis first.")
            return
        mep_status = last_new_params.get("mep_status_after", {})
        updated_stair = last_new_params.get("stair_params", {})
        updated_mep = last_new_params.get("mep_params", {})
        temp_state = result_state.copy()
        # Offer choice of visualizations
        choice_win = tk.Toplevel(parent_win)
        choice_win.title("Select Visualization")
        choice_win.geometry("350x200")
        choice_win.transient(parent_win)
        choice_win.grab_set()
        ttk.Label(choice_win, text="Choose visualization type:", font=("Segoe UI", 10)).pack(pady=10)
        btn_frame = ttk.Frame(choice_win)
        btn_frame.pack(pady=5)

        def select_enhanced_2d():
            choice_win.destroy()
            plot_enhanced_mep_viz(temp_state, parent_win, status_overrides=mep_status,
                                  stair_params=updated_stair, mep_params=updated_mep)
        def select_enhanced_3d():
            choice_win.destroy()
            plot_enhanced_mep_viz_3d(temp_state, parent_win, status_overrides=mep_status,
                                     stair_params=updated_stair, mep_params=updated_mep)
        def select_basic_2d():
            choice_win.destroy()
            plot_mep_conflicts(temp_state, parent_win, status_overrides=mep_status)
        def select_basic_3d():
            choice_win.destroy()
            plot_mep_conflicts_3d(temp_state, parent_win, status_overrides=mep_status)

        ttk.Button(btn_frame, text="2D (Basic)", command=select_basic_2d).pack(fill=tk.X, padx=10, pady=2)
        ttk.Button(btn_frame, text="3D (Basic)", command=select_basic_3d).pack(fill=tk.X, padx=10, pady=2)
        ttk.Button(btn_frame, text="2D (Enhanced with parameters)", command=select_enhanced_2d).pack(fill=tk.X, padx=10, pady=2)
        ttk.Button(btn_frame, text="3D (Enhanced with parameters)", command=select_enhanced_3d).pack(fill=tk.X, padx=10, pady=2)
        ttk.Button(btn_frame, text="Cancel", command=choice_win.destroy).pack(fill=tk.X, padx=10, pady=5)

    # ----- Analysis function -----
    def analyze():
        idx = comp_combo.current()
        if idx < 0:
            messagebox.showwarning("No selection", "Please select a component.")
            return
        param = param_var.get()
        if not param:
            messagebox.showwarning("No parameter", "Please select a parameter.")
            return
        try:
            new_val = float(new_val_entry.get())
        except ValueError:
            messagebox.showerror("Invalid value", "Please enter a numeric value.")
            return
        key = comp_keys[idx]
        ontology = result_state.get("ontology")
        analyzer = ChangeImpactAnalyzer()
        analyzer.set_component_data(comp_data, result_state.get("ontology"))
        if propagate_var.get() and key[0] == "IfcStairFlight" and ontology:
            impacts = analyzer.propagate_to_related_flights(key, param, new_val, ontology)
        else:
            impacts = analyzer.find_impact(key, param, new_val)

        # ---- Build updated parameters for MEP visualization ----
        # Get current stair parameters from component data
        current_stair = {}
        for k, v in comp_data.items():
            if k[0] == "IfcStairFlight":
                current_stair = {
                    "h": v.get("h"),
                    "g": v.get("g"),
                    "number_of_risers": v.get("number of risers"),
                    "climb_height": v.get("climb height")
                }
                break
        # Get current guardrail height from railing (default 1100 if missing)
        guardrail_h = 1100.0
        for k, v in comp_data.items():
            if k[0] == "IfcRailing" and "height h" in v:
                guardrail_h = v.get("height h")
                break

        # Prepare updated parameters
        updated_stair = current_stair.copy()
        updated_mep = {
            "guardrail_height": guardrail_h,
            "fire_hose_height": None,
            "cabinet_depth": None,
            "outlet_height": None
        }
        # Apply the change based on component type
        if key[0] == "IfcStairFlight":
            updated_stair[param] = new_val
            if param == "h" and updated_stair.get("number_of_risers"):
                updated_stair["climb_height"] = updated_stair["h"] * updated_stair["number_of_risers"]
            elif param == "number_of_risers" and updated_stair.get("h"):
                updated_stair["climb_height"] = updated_stair["h"] * updated_stair["number_of_risers"]
            elif param == "climb_height" and updated_stair.get("number_of_risers"):
                updated_stair["h"] = updated_stair["climb_height"] / updated_stair["number_of_risers"]
        elif key[0] == "IfcRailing" and param == "height h":
            updated_mep["guardrail_height"] = new_val
        elif key[0] == "IfcFireSuppressionTerminal":
            comp_name = comp_data[key].get("name", "").lower()
            if "hose" in comp_name:
                updated_mep["fire_hose_height"] = new_val
            elif "cabinet" in comp_name:
                updated_mep["cabinet_depth"] = new_val
        elif key[0] == "IfcOutlet":
            updated_mep["outlet_height"] = new_val

        # Fill missing MEP values from current component data
        for k, v in comp_data.items():
            if k[0] == "IfcFireSuppressionTerminal":
                if "hose" in v.get("name", "").lower() and updated_mep["fire_hose_height"] is None:
                    updated_mep["fire_hose_height"] = v.get("fire_hose_height")
                elif "cabinet" in v.get("name", "").lower() and updated_mep["cabinet_depth"] is None:
                    updated_mep["cabinet_depth"] = v.get("cabinet_depth")
            elif k[0] == "IfcOutlet" and updated_mep["outlet_height"] is None:
                updated_mep["outlet_height"] = v.get("outlet_height")

        # Evaluate MEP rule statuses with updated parameters
        mep_status = {}
        # Rule 16
        n_risers = updated_stair.get("number_of_risers")
        if n_risers is not None and updated_mep["guardrail_height"] is not None and updated_mep["fire_hose_height"] is not None:
            params16 = {
                "number of risers": n_risers,
                "guardrail_height": updated_mep["guardrail_height"],
                "fire_hose_height": updated_mep["fire_hose_height"]
            }
            mep_status[16] = analyzer.evaluate_rule(16, params16)
        else:
            mep_status[16] = "N/A"
        # Rule 17
        depth = updated_mep["cabinet_depth"]
        if depth is not None:
            params17 = {
                "g": updated_stair.get("g"),
                "climb height": updated_stair.get("climb_height"),
                "cabinet_depth": depth
            }
            mep_status[17] = analyzer.evaluate_rule(17, params17)
        else:
            mep_status[17] = "N/A"
        # Rule 18
        outlet_h = updated_mep["outlet_height"]
        if outlet_h is not None:
            params18 = {
                "h": updated_stair.get("h"),
                "g": updated_stair.get("g"),
                "outlet_height": outlet_h
            }
            mep_status[18] = analyzer.evaluate_rule(18, params18)
        else:
            mep_status[18] = "N/A"

        # Store for enhanced visualization
        last_new_params["stair_params"] = updated_stair
        last_new_params["mep_params"] = updated_mep
        last_new_params["mep_status_after"] = mep_status

        show_impact_results(impacts, dialog, extra_button_callback=lambda: show_updated_mep_viz(dialog))

    # ----- Buttons -----
    ttk.Button(btn_frame, text="Analyze Impact", command=analyze).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Show Updated MEP Viz", command=lambda: show_updated_mep_viz(dialog)).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Auto-fix Suggestions", command=auto_fix).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Show Graph", command=lambda: plot_ontology_graph(result_state, dialog)).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

# ------------------------------
# Run the main analysis (calls ifc_extractor.py)
# ------------------------------
def evaluate_rules_via_sparql(rdf_graph):
    """
    SPARQL evaluation with target class existence check (same as SHACL).
    """
    rule_queries = {
        1: {"id": 7, "text": "600 <= g+2h <= 660", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . BIND (?g + 2*?h AS ?sum) FILTER (?sum < 600 || ?sum > 660) }"},
        2: {"id": 8, "text": "210 <= g <= 310", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasTreadLength ?g . FILTER (?g < 210 || ?g > 310) }"},
        3: {"id": 3, "text": "20 < alpha <= 45 deg", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . FILTER (?g > 0) FILTER (?h / ?g <= 0.36397 || ?h / ?g > 1) }"},
        4: {"id": 9, "text": "overlap r >= 10 mm", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNosingLength ?r . FILTER (?r < 10) }"},
        5: {"id": 56, "text": "480 <= 2h+g <= 800", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . BIND (2*?h + ?g AS ?sum) FILTER (?sum < 480 || ?sum > 800) }"},
        6: {"id": 55, "text": "risers >= 3", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n < 3) }"},
        7: {"id": 55, "text": "risers <= 16 (normal)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n > 16) }"},
        8: {"id": 55, "text": "risers <= 22 (max)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n > 22) }"},
        9: {"id": 19, "text": "single-flight climb H <= 4000 mm", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n . BIND (?h * ?n AS ?climb) FILTER (?climb > 4000) }"},
        10: {"id": 20, "text": "multi-flight climb H <= 3000 mm (per flight in stairs with >1 flight)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n ; stair:isPartOf ?stair . BIND (?h * ?n AS ?climb) ?stair stair:hasFlight ?otherFlight . FILTER (?this != ?otherFlight) FILTER (?climb > 3000) }"},
        11: {"id": 26, "text": "guardrail height >= 1100 mm", "target": "Guardrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Guardrail ; stair:hasGuardrailHeight ?h . FILTER (?h < 1100) }"},
        12: {"id": 36, "text": "handrail height 900..1000 mm", "target": "Handrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Handrail ; stair:hasHandrailHeight ?h . FILTER (?h < 900 || ?h > 1000) }"},
        13: {"id": 60, "text": "landing handrail height >= 1100 mm", "target": "LandingHandrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:LandingHandrail ; stair:hasLandingHandrailHeight ?h . FILTER (?h < 1100) }"},
        14: {"id": 63, "text": "handrail height 760..940 mm", "target": "Handrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Handrail ; stair:hasHandrailHeight ?h . FILTER (?h < 760 || ?h > 940) }"},
        # 15: {"id": 100, "text": "total climb per storey <= 4000 mm", "target": "BuildingStorey", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?storey (SUM(?h * ?n) AS ?totalClimb) WHERE { ?flight stair:isContainedInStorey ?storey ; stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n . } GROUP BY ?storey HAVING (?totalClimb > 4000)"},
        # 16: {"id": 101, "text": "Fire hose conflict (risers>12, hose 900-1500, guardrail present)", "target": "FireHoseValve", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?hose WHERE { ?hose a stair:FireHoseValve ; stair:hasFireHoseHeight ?hoseH . FILTER (?hoseH >= 900 && ?hoseH <= 1500) ?hose stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey ; stair:hasNumberOfRisers ?n . FILTER (?n > 12) ?guardrail stair:isContainedInStorey ?storey ; a stair:Guardrail . }"},
        # 17: {"id": 102, "text": "Cabinet depth >100 with narrow stair (g<1200) or high climb (>6000)", "target": "FireExtinguisherCabinet", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?cab WHERE { ?cab a stair:FireExtinguisherCabinet ; stair:hasCabinetDepth ?depth . FILTER (?depth > 100) ?cab stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey . OPTIONAL { ?flight stair:hasTreadLength ?g . } OPTIONAL { ?flight stair:hasClimbHeight ?climb . } FILTER ( (?g < 1200) || (?climb > 6000) ) }"},
        # 18: {"id": 103, "text": "Outlet height 600-1200 mm with steep stair (h>190 or g<240)", "target": "Outlet", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?out WHERE { ?out a stair:Outlet ; stair:hasOutletHeight ?outH . FILTER (?outH >= 600 && ?outH <= 1200) ?out stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey . OPTIONAL { ?flight stair:hasRiserHeight ?h . } OPTIONAL { ?flight stair:hasTreadLength ?g . } FILTER ( (?h > 190) || (?g < 240) ) }"}
    }

    from semantic_checks import (classify_rule_contexts, build_rule_results,
                                 build_display_rows)
    rows, summary, totals = [], [], {"PASS": 0, "FAIL": 0, "N/A": 0}
    for rn, rule in rule_queries.items():
        checks = classify_rule_contexts(rdf_graph, rn, rule["target"])
        violating_nodes = set()
        if checks["eligible"]:
            try:
                violating_nodes = {record[0] for record in rdf_graph.query(rule["query"])}
            except Exception as exc:
                # Query execution failure is not a compliant result.
                checks["missing"].update(checks["eligible"])
                checks["eligible"].clear()
                print(f"SPARQL rule {rn} error: {exc}")
        summary_row, failed = build_rule_results(rn, rule, checks, violating_nodes)
        summary.append(summary_row)
        rows.extend(build_display_rows(rn, rule, checks, failed))
        label = next(t for t in ("FAIL", "N/A", "PASS") if summary_row[t])
        totals[label] += 1
    return rows, summary, totals

def evaluate_rules_via_shacl(rdf_graph):
    from pyshacl import validate
    from rdflib import Graph, Namespace, RDF
    import re

    # ---- Rule definitions (identical to SPARQL method) ----
    rule_queries = {
        1: {"id": 7, "text": "600 <= g+2h <= 660", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . BIND (?g + 2*?h AS ?sum) FILTER (?sum < 600 || ?sum > 660) }"},
        2: {"id": 8, "text": "210 <= g <= 310", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasTreadLength ?g . FILTER (?g < 210 || ?g > 310) }"},
        3: {"id": 3, "text": "20 < alpha <= 45 deg", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . FILTER (?g > 0) FILTER (?h / ?g <= 0.36397 || ?h / ?g > 1) }"},
        4: {"id": 9, "text": "overlap r >= 10 mm", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNosingLength ?r . FILTER (?r < 10) }"},
        5: {"id": 56, "text": "480 <= 2h+g <= 800", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasTreadLength ?g . BIND (2*?h + ?g AS ?sum) FILTER (?sum < 480 || ?sum > 800) }"},
        6: {"id": 55, "text": "risers >= 3", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n < 3) }"},
        7: {"id": 55, "text": "risers <= 16 (normal)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n > 16) }"},
        8: {"id": 55, "text": "risers <= 22 (max)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasNumberOfRisers ?n . FILTER (?n > 22) }"},
        9: {"id": 19, "text": "single-flight climb H <= 4000 mm", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n . BIND (?h * ?n AS ?climb) FILTER (?climb > 4000) }"},
        10: {"id": 20, "text": "multi-flight climb H <= 3000 mm (per flight in stairs with >1 flight)", "target": "StairFlight", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n ; stair:isPartOf ?stair . BIND (?h * ?n AS ?climb) ?stair stair:hasFlight ?otherFlight . FILTER (?this != ?otherFlight) FILTER (?climb > 3000) }"},
        11: {"id": 26, "text": "guardrail height >= 1100 mm", "target": "Guardrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Guardrail ; stair:hasGuardrailHeight ?h . FILTER (?h < 1100) }"},
        12: {"id": 36, "text": "handrail height 900..1000 mm", "target": "Handrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Handrail ; stair:hasHandrailHeight ?h . FILTER (?h < 900 || ?h > 1000) }"},
        13: {"id": 60, "text": "landing handrail height >= 1100 mm", "target": "LandingHandrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:LandingHandrail ; stair:hasLandingHandrailHeight ?h . FILTER (?h < 1100) }"},
        14: {"id": 63, "text": "handrail height 760..940 mm", "target": "Handrail", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?this WHERE { ?this a stair:Handrail ; stair:hasHandrailHeight ?h . FILTER (?h < 760 || ?h > 940) }"},
        # 15: {"id": 100, "text": "total climb per storey <= 4000 mm", "target": "BuildingStorey", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?storey (SUM(?h * ?n) AS ?totalClimb) WHERE { ?flight stair:isContainedInStorey ?storey ; stair:hasRiserHeight ?h ; stair:hasNumberOfRisers ?n . } GROUP BY ?storey HAVING (?totalClimb > 4000)"},
        # 16: {"id": 101, "text": "Fire hose conflict (risers>12, hose 900-1500, guardrail present)", "target": "FireHoseValve", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?hose WHERE { ?hose a stair:FireHoseValve ; stair:hasFireHoseHeight ?hoseH . FILTER (?hoseH >= 900 && ?hoseH <= 1500) ?hose stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey ; stair:hasNumberOfRisers ?n . FILTER (?n > 12) ?guardrail stair:isContainedInStorey ?storey ; a stair:Guardrail . }"},
        # 17: {"id": 102, "text": "Cabinet depth >100 with narrow stair (g<1200) or high climb (>6000)", "target": "FireExtinguisherCabinet", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?cab WHERE { ?cab a stair:FireExtinguisherCabinet ; stair:hasCabinetDepth ?depth . FILTER (?depth > 100) ?cab stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey . OPTIONAL { ?flight stair:hasTreadLength ?g . } OPTIONAL { ?flight stair:hasClimbHeight ?climb . } FILTER ( (?g < 1200) || (?climb > 6000) ) }"},
        # 18: {"id": 103, "text": "Outlet height 600-1200 mm with steep stair (h>190 or g<240)", "target": "Outlet", "query": "PREFIX stair: <http://example.com/stairOntology#> SELECT ?out WHERE { ?out a stair:Outlet ; stair:hasOutletHeight ?outH . FILTER (?outH >= 600 && ?outH <= 1200) ?out stair:isContainedInStorey ?storey . ?flight stair:isContainedInStorey ?storey . OPTIONAL { ?flight stair:hasRiserHeight ?h . } OPTIONAL { ?flight stair:hasTreadLength ?g . } FILTER ( (?h > 190) || (?g < 240) ) }"}
    }

    # Build SHACL shapes
    prefix_decl = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n@prefix stair: <http://example.com/stairOntology#> .\n"
    shacl_lines = [prefix_decl]
    for rn, rule in rule_queries.items():
        target = rule["target"]
        q = rule["query"].replace('"', '\\"')
        shacl_lines.append(f'[] a sh:NodeShape ; sh:targetClass stair:{target} ; sh:sparql [ sh:message "Rule {rn}" ; sh:select "{q}" ] .')
    shacl_ttl = "\n".join(shacl_lines)

    shacl_graph = Graph()
    try:
        shacl_graph.parse(data=shacl_ttl, format="turtle")
    except Exception as e:
        print(f"SHACL parse error: {e}")
        return [], [], {"PASS":0,"FAIL":0,"N/A":0}

    # Prepare data graph with inferred isContainedInStorey
    g = Graph()
    for triple in rdf_graph:
        g.add(triple)
    STAIR = Namespace("http://example.com/stairOntology#")
    for flight in g.subjects(RDF.type, STAIR.StairFlight):
        for stair in g.objects(flight, STAIR.isPartOf):
            for storey in g.objects(stair, STAIR.isPartOf):
                if (storey, RDF.type, STAIR.BuildingStorey):
                    g.add((flight, STAIR.isContainedInStorey, storey))
            for storey in g.objects(flight, STAIR.isPartOf):
                if (storey, RDF.type, STAIR.BuildingStorey):
                    g.add((flight, STAIR.isContainedInStorey, storey))
    for elem_type in [STAIR.FireHoseValve, STAIR.FireExtinguisherCabinet, STAIR.Outlet]:
        for elem in g.subjects(RDF.type, elem_type):
            for storey in g.objects(elem, STAIR.isPartOf):
                if (storey, RDF.type, STAIR.BuildingStorey):
                    g.add((elem, STAIR.isContainedInStorey, storey))

    # Validate
    try:
        conforms, results_graph, results_text = validate(
            g, shacl_graph=shacl_graph, inference="rdfs",
            abort_on_first=False, allow_infinite=True, advanced=True
        )
    except Exception as e:
        print(f"SHACL validation error: {e}")
        return [], [], {"PASS":0,"FAIL":0,"N/A":0}

    # Record actual focus nodes for each validation result, not just rule IDs.
    # Apply the same data-completeness checks used by the direct SPARQL path.
    from semantic_checks import (classify_rule_contexts, build_rule_results,
                                 build_display_rows)
    SH = Namespace("http://www.w3.org/ns/shacl#")
    violated_nodes_by_rule = defaultdict(set)
    for violation in results_graph.subjects(RDF.type, SH.ValidationResult):
        msg = results_graph.value(violation, SH.resultMessage)
        focus = results_graph.value(violation, SH.focusNode)
        match = re.search(r"Rule\s+(\d+)", str(msg)) if msg else None
        if match and focus is not None:
            violated_nodes_by_rule[int(match.group(1))].add(focus)

    rows, summary, totals = [], [], {"PASS": 0, "FAIL": 0, "N/A": 0}
    for rn, rule in rule_queries.items():
        checks = classify_rule_contexts(g, rn, rule["target"])
        summary_row, failed = build_rule_results(rn, rule, checks, violated_nodes_by_rule[rn])
        summary.append(summary_row)
        rows.extend(build_display_rows(rn, rule, checks, failed))
        label = next(t for t in ("FAIL", "N/A", "PASS") if summary_row[t])
        totals[label] += 1
    return rows, summary, totals

def run_analysis(text_widget, status_label, run_btn, excel_btn, pdf_btn, sim_btn, ifc_var, result_state):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "ifc_extractor.py")
    reset_result_state(result_state)
    set_export_buttons(excel_btn, pdf_btn, False)
    sim_btn.state(["disabled"])
    if not os.path.exists(script_path):
        set_status(status_label, "ERROR: ifc_extractor.py not found.", "error")
        return
    ifc_path = ifc_var.get().strip()
    if not ifc_path or not os.path.exists(ifc_path):
        set_status(status_label, "ERROR: IFC file not found.", "error")
        return

    # Run IFC extraction script (produces raw output)
    env = os.environ.copy()
    env["IFC_FILE_PATH"] = ifc_path
    cmd = [sys.executable, "-u", script_path]
    set_status(status_label, "Running IFC extraction...", "running")
    run_btn.state(["disabled"])
    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", tk.END)
    try:
        proc = subprocess.run(cmd, cwd=script_dir, env=env, capture_output=True, text=True)
    except Exception as e:
        set_status(status_label, f"ERROR: {e}", "error")
        run_btn.state(["!disabled"])
        text_widget.configure(state=tk.DISABLED)
        return
    output = proc.stdout + proc.stderr
    for line in output.splitlines():
        tag = pick_line_tag(line)
        text_widget.insert(tk.END, line + "\n", tag)
    text_widget.see(tk.END)

    # Extract legacy rows (old method) and component data / ontology
    rows_legacy, ifc_file, comp_data, ontology = extract_results(output)
    # Direct IFC read for missing parameters (railing heights, etc.)
    if ifc_file and os.path.exists(ifc_file):
        extract_railing_heights_from_ifc(ifc_file, comp_data)
        # Do not copy the first flight's extracted values to every flight.
        # Missing values must remain unknown until extracted per target.

    # Build legacy summary and totals
    summary_legacy, totals_legacy = build_rule_summary(rows_legacy)

    # Create RDF graph for ontology‑based methods
    rdf_graph = None
    rows_sparql, summary_sparql, totals_sparql = [], [], {"PASS":0,"FAIL":0,"N/A":0}
    rows_shacl, summary_shacl, totals_shacl = [], [], {"PASS":0,"FAIL":0,"N/A":0}
    try:
        from rdf_mapper import populate_rdf_graph, save_graph
        rdf_graph = populate_rdf_graph(
            component_data=comp_data,
            ontology_relations=ontology.relations if ontology else None,
            ontology_ttl_path=os.path.join(script_dir, "stair_ontology.ttl")
        )
        save_graph(rdf_graph, "output_graph.ttl")
        result_state["rdf_graph"] = rdf_graph
    except Exception as e:
        print(f"RDF mapping failed: {e}")
        text_widget.insert(tk.END, f"\nRDF mapping error: {e}\n")
        # Continue without ontology methods

    # SPARQL evaluation (if RDF graph available)
    if rdf_graph is not None:
        try:
            rows_sparql, summary_sparql, totals_sparql = evaluate_rules_via_sparql(rdf_graph)
        except Exception as e:
            print(f"SPARQL evaluation failed: {e}")
            text_widget.insert(tk.END, f"\nSPARQL error: {e}\n")

    # SHACL evaluation (if RDF graph available)
    if rdf_graph is not None:
        try:
            rows_shacl, summary_shacl, totals_shacl = evaluate_rules_via_shacl(rdf_graph)
        except Exception as e:
            print(f"SHACL evaluation failed: {e}")
            text_widget.insert(tk.END, f"\nSHACL error: {e}\n")

    # Store all three result sets in result_state
    result_state.update({
        "rows_legacy": rows_legacy,
        "rows_sparql": rows_sparql,
        "rows_shacl": rows_shacl,
        "summary_legacy": summary_legacy,
        "summary_sparql": summary_sparql,
        "summary_shacl": summary_shacl,
        "totals_legacy": totals_legacy,
        "totals_sparql": totals_sparql,
        "totals_shacl": totals_shacl,
        "ifc_file": ifc_file,
        "generated_at": datetime.now(),
        "raw_output": output,
        "component_data": comp_data,
        "ontology": ontology,
        "rdf_graph": rdf_graph
    })

    # Initially display Legacy results (for backward compatibility)
    result_state["rows"] = rows_legacy
    result_state["rule_summary"] = summary_legacy
    result_state["totals"] = totals_legacy

    # Update UI with summary of all methods (optional)
    text_widget.configure(state=tk.NORMAL)
    text_widget.insert(tk.END, "\n\n=== Validation Results ===\n")
    text_widget.insert(tk.END, f"Legacy   : PASS {totals_legacy['PASS']}  FAIL {totals_legacy['FAIL']}  N/A {totals_legacy['N/A']}\n")
    text_widget.insert(tk.END, f"SPARQL   : PASS {totals_sparql['PASS']}  FAIL {totals_sparql['FAIL']}  N/A {totals_sparql['N/A']}\n")
    text_widget.insert(tk.END, f"SHACL    : PASS {totals_shacl['PASS']}  FAIL {totals_shacl['FAIL']}  N/A {totals_shacl['N/A']}\n")
    text_widget.see(tk.END)
    text_widget.configure(state=tk.DISABLED)

    # Enable buttons if any results exist
    if rows_legacy or rows_sparql or rows_shacl:
        set_export_buttons(excel_btn, pdf_btn, True)
        sim_btn.state(["!disabled"])
    else:
        set_export_buttons(excel_btn, pdf_btn, False)
        sim_btn.state(["disabled"])

    if totals_legacy.get("FAIL", 0) > 0:
        set_status(status_label, f"Completed – Legacy FAIL: {totals_legacy['FAIL']}", "warn")
    else:
        set_status(status_label, f"Completed – Legacy PASS: {totals_legacy['PASS']}", "done")

    run_btn.state(["!disabled"])

# ------------------------------
# Excel and PDF export (using openpyxl and reportlab)
# ------------------------------
def export_excel(result_state, status_label):
    if not result_state.get("rows"):
        messagebox.showwarning("Export Excel", "No data to export. Run analysis first.")
        return
    if not ensure_dependency("openpyxl", "openpyxl", status_label):
        return
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
    if not path:
        return
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        # headers
        ws["A1"] = "Stair Compliance Report"
        ws["A3"] = "Generated"
        ws["B3"] = result_state["generated_at"].strftime("%Y-%m-%d %H:%M:%S")
        ws["A4"] = "IFC File"
        ws["B4"] = result_state.get("ifc_file", "")
        ws["A5"] = "Total Checks"
        ws["B5"] = len(result_state["rows"])
        ws["A6"] = "PASS"
        ws["B6"] = result_state["totals"]["PASS"]
        ws["A7"] = "FAIL"
        ws["B7"] = result_state["totals"]["FAIL"]
        ws["A8"] = "N/A"
        ws["B8"] = result_state["totals"]["N/A"]

        # rule summary
        ws_sum = wb.create_sheet("Rule Summary")
        sum_headers = ["Rule No", "Constraint ID", "Description", "PASS", "FAIL", "N/A", "Total"]
        for col, h in enumerate(sum_headers, 1):
            ws_sum.cell(row=1, column=col, value=h)
        for i, item in enumerate(result_state["rule_summary"], 2):
            ws_sum.cell(row=i, column=1, value=item["rule_number"])
            ws_sum.cell(row=i, column=2, value=item["constraint_id"])
            ws_sum.cell(row=i, column=3, value=item["rule_text"])
            ws_sum.cell(row=i, column=4, value=item["PASS"])
            ws_sum.cell(row=i, column=5, value=item["FAIL"])
            ws_sum.cell(row=i, column=6, value=item["N/A"])
            ws_sum.cell(row=i, column=7, value=item["total"])
        # details
        ws_det = wb.create_sheet("Detailed Results")
        det_headers = ["Component", "Item", "Entity ID", "Name", "Metadata", "Rule No", "Constraint ID", "Rule Description", "Result"]
        for col, h in enumerate(det_headers, 1):
            ws_det.cell(row=1, column=col, value=h)
        for i, row in enumerate(result_state["rows"], 2):
            ws_det.cell(row=i, column=1, value=row["component_type"])
            ws_det.cell(row=i, column=2, value=row["item_index"])
            ws_det.cell(row=i, column=3, value=row["entity_id"])
            ws_det.cell(row=i, column=4, value=row["name"])
            ws_det.cell(row=i, column=5, value=row["metadata"])
            ws_det.cell(row=i, column=6, value=row["rule_number"])
            ws_det.cell(row=i, column=7, value=row["constraint_id"])
            ws_det.cell(row=i, column=8, value=row["rule_text"])
            ws_det.cell(row=i, column=9, value=row["result"])
        wb.save(path)
        set_status(status_label, f"Excel exported: {os.path.basename(path)}", "done")
        messagebox.showinfo("Export Excel", f"Saved to {path}")
    except Exception as e:
        set_status(status_label, f"Excel export failed: {e}", "error")
        messagebox.showerror("Error", str(e))

def export_pdf(result_state, status_label):
    """Export the compliance report as HTML (then user can print to PDF)."""
    if not result_state.get("rows"):
        messagebox.showwarning("Export PDF", "No data to export. Run analysis first.")
        return

    from xml.sax.saxutils import escape
    from datetime import datetime
    import webbrowser
    import tempfile

    # Generate HTML content
    generated = result_state["generated_at"].strftime("%Y-%m-%d %H:%M:%S")
    ifc_file = escape(result_state.get("ifc_file", "Unknown"))
    totals = result_state["totals"]

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Stair Compliance Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #1F4E78; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
        th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
        th {{ background-color: #1F4E78; color: white; }}
        .pass {{ background-color: #E7F6E7; }}
        .fail {{ background-color: #FDEBEC; }}
        .na {{ background-color: #FFF7E0; }}
        @media print {{
            body {{ margin: 0; }}
            .no-print {{ display: none; }}
        }}
    </style>
</head>
<body>
    <h1>Stair Compliance Report</h1>
    <p><strong>Generated:</strong> {generated}<br>
    <strong>IFC file:</strong> {ifc_file}<br>
    <strong>Total checks:</strong> {len(result_state['rows'])} |
    <strong>PASS:</strong> {totals['PASS']} |
    <strong>FAIL:</strong> {totals['FAIL']} |
    <strong>N/A:</strong> {totals['N/A']}</p>

    <h2>Rule Summary</h2>
    <table>
        <thead>
            <tr><th>Rule</th><th>Constraint ID</th><th>Description</th><th>PASS</th><th>FAIL</th><th>N/A</th><th>Total</th></tr>
        </thead>
        <tbody>
"""
    for item in result_state["rule_summary"]:
        html += f"""
            <tr>
                <td>{item['rule_number']}</td>
                <td>{item['constraint_id']}</td>
                <td>{escape(item['rule_text'])}</td>
                <td style="text-align:center">{item['PASS']}</td>
                <td style="text-align:center">{item['FAIL']}</td>
                <td style="text-align:center">{item['N/A']}</td>
                <td style="text-align:center">{item['total']}</td>
            </tr>
        """

    html += """
        </tbody>
    </table>

    <h2>Detailed Results</h2>
    <table>
        <thead>
            <tr><th>Component</th><th>Entity ID</th><th>Name / Metadata</th><th>Rule</th><th>Result</th></tr>
        </thead>
        <tbody>
"""
    for row in result_state["rows"]:
        comp = f"{row['component_type']} {row['item_index']}"
        name_meta = f"{escape(row['name'])} ({escape(row['metadata'])})"
        rule_txt = f"({row['rule_number']}) [ID {row['constraint_id']}] {escape(row['rule_text'])}"
        result_class = row['result'].lower()
        html += f"""
            <tr class="{result_class}">
                <td>{comp}</td>
                <td>{row['entity_id']}</td>
                <td>{name_meta}</td>
                <td>{rule_txt}</td>
                <td style="text-align:center">{row['result']}</td>
            </tr>
        """
    html += """
        </tbody>
    </table>
    <p class="no-print"><em>To save as PDF: Press Ctrl+P and select "Save as PDF".</em></p>
</body>
</html>
"""

    # Save to temporary file and open in browser
    fd, temp_path = tempfile.mkstemp(suffix=".html", prefix="stair_report_", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open(temp_path)
    set_status(status_label, "HTML report generated. Use Ctrl+P to save as PDF.", "done")
    messagebox.showinfo("Export PDF", "Report opened in browser.\nPress Ctrl+P and choose 'Save as PDF'.")

def check_mep_conflicts(result_state, status_label, output_text):
    """Evaluate multi-disciplinary rules (16,17,18) using stair parameters.
       Returns True if any MEP components were processed, False otherwise."""
    comp_data = result_state.get("component_data", {})
    onto = result_state.get("ontology")
    if not comp_data:
        messagebox.showwarning("MEP Check", "No component data available. Please run 'Run Stair Checks' first.")
        set_status(status_label, "MEP check aborted: no data", "warn")
        return False

    # 1. Extract stair flight parameters
    stair_params = {
        "h": None, "g": None, "number of risers": None,
        "climb height": None, "guardrail_height": None
    }
    for key, data in comp_data.items():
        if key[0] == "IfcStairFlight":
            stair_params["h"] = data.get("h")
            stair_params["g"] = data.get("g")
            stair_params["number of risers"] = data.get("number of risers")
            stair_params["climb height"] = data.get("climb height")
        if key[0] == "IfcRailing":
            stair_params["guardrail_height"] = data.get("height h")

    # If guardrail_height still missing, read directly from IFC
    if stair_params["guardrail_height"] is None:
        try:
            import ifcopenshell
            ifc_path = result_state.get("ifc_file", "")
            if ifc_path and os.path.exists(ifc_path):
                model = ifcopenshell.open(ifc_path)
                railings = model.by_type("IfcRailing")
                for railing in railings:
                    for rel in getattr(railing, "IsDefinedBy", []):
                        if not rel.is_a("IfcRelDefinesByProperties"):
                            continue
                        pdef = rel.RelatingPropertyDefinition
                        height_val = None
                        if pdef.is_a("IfcElementQuantity"):
                            for q in getattr(pdef, "Quantities", []):
                                if getattr(q, "Name", "") == "Height" and hasattr(q, "LengthValue"):
                                    height_val = q.LengthValue
                                    break
                        elif pdef.is_a("IfcPropertySet"):
                            for prop in getattr(pdef, "HasProperties", []):
                                if prop.is_a("IfcPropertySingleValue") and getattr(prop, "Name", "") == "Height":
                                    val = getattr(prop, "NominalValue", None)
                                    if val is not None:
                                        if hasattr(val, "wrappedValue"):
                                            height_val = val.wrappedValue
                                        else:
                                            height_val = val
                                    break
                        if height_val is not None:
                            stair_params["guardrail_height"] = float(height_val)
                            print(f"Direct IFC read guardrail height = {height_val} mm")
                            break
                    if stair_params["guardrail_height"] is not None:
                        break
        except Exception as e:
            print(f"Could not read guardrail height from IFC: {e}")

    # Fallback default
    if stair_params["guardrail_height"] is None:
        stair_params["guardrail_height"] = 1100.0
        print("guardrail_height missing, using default 1100 mm")

    print("\n===== FINAL stair_params =====")
    print(stair_params)
    print("===============================\n")

    # 2. Prepare analyzer
    analyzer = ChangeImpactAnalyzer()
    analyzer.set_component_data(comp_data)

    # 3. Collect MEP components and evaluate
    new_rows = []
    new_rule_summary = {}
    totals = {"PASS": 0, "FAIL": 0, "N/A": 0}
    found_mep = False

    mep_rules = {
        16: {"text": "Fire hose valve conflict with guardrail when risers>12 and hose height 900-1500",
             "type": "fire_hose", "param": "fire_hose_height"},
        17: {"text": "Cabinet protrusion >100mm with stair width <1200 or climb>6000",
             "type": "cabinet", "param": "cabinet_depth"},
        18: {"text": "Outlet in danger zone (600-1200mm) when riser>190 or tread<240",
             "type": "outlet", "param": "outlet_height"}
    }

    for key, data in comp_data.items():
        comp_type = data.get("type")
        comp_name = data.get("name", "?")
        comp_meta = data.get("metadata", "")
        entity_id = key[1]

        # Fire hose valve (Rule 16)
        if comp_type == "IfcFireSuppressionTerminal" and "hose" in comp_name.lower():
            found_mep = True
            params = {"fire_hose_height": data.get("fire_hose_height")}
            params.update(stair_params)
            print(f"DEBUG: Fire hose {entity_id} params = {params}")
            status = analyzer.evaluate_rule(16, params)
            print(f"DEBUG: Rule 16 status = {status}")
            totals[status] += 1
            new_rows.append({
                "component_type": comp_type,
                "item_index": 0,
                "entity_id": entity_id,
                "name": comp_name,
                "metadata": comp_meta,
                "rule_number": 16,
                "constraint_id": 1001,
                "rule_text": mep_rules[16]["text"],
                "result": status,
            })
            if 16 not in new_rule_summary:
                new_rule_summary[16] = {"rule_number": 16, "constraint_id": 1001,
                                        "rule_text": mep_rules[16]["text"],
                                        "PASS": 0, "FAIL": 0, "N/A": 0, "total": 0}
            new_rule_summary[16][status] += 1
            new_rule_summary[16]["total"] += 1

        # Fire extinguisher cabinet (Rule 17)
        elif comp_type == "IfcFireSuppressionTerminal" and "cabinet" in comp_name.lower():
            found_mep = True
            params = {"cabinet_depth": data.get("cabinet_depth")}
            params.update(stair_params)
            print(f"DEBUG: Cabinet {entity_id} params = {params}")
            status = analyzer.evaluate_rule(17, params)
            print(f"DEBUG: Rule 17 status = {status}")
            totals[status] += 1
            new_rows.append({
                "component_type": comp_type,
                "item_index": 0,
                "entity_id": entity_id,
                "name": comp_name,
                "metadata": comp_meta,
                "rule_number": 17,
                "constraint_id": 1002,
                "rule_text": mep_rules[17]["text"],
                "result": status,
            })
            if 17 not in new_rule_summary:
                new_rule_summary[17] = {"rule_number": 17, "constraint_id": 1002,
                                        "rule_text": mep_rules[17]["text"],
                                        "PASS": 0, "FAIL": 0, "N/A": 0, "total": 0}
            new_rule_summary[17][status] += 1
            new_rule_summary[17]["total"] += 1

        # Electrical outlet (Rule 18)
        elif comp_type == "IfcOutlet":
            found_mep = True
            params = {"outlet_height": data.get("outlet_height")}
            params.update(stair_params)
            print(f"DEBUG: Outlet {entity_id} params = {params}")
            status = analyzer.evaluate_rule(18, params)
            print(f"DEBUG: Rule 18 status = {status}")
            totals[status] += 1
            new_rows.append({
                "component_type": comp_type,
                "item_index": 0,
                "entity_id": entity_id,
                "name": comp_name,
                "metadata": comp_meta,
                "rule_number": 18,
                "constraint_id": 1003,
                "rule_text": mep_rules[18]["text"],
                "result": status,
            })
            if 18 not in new_rule_summary:
                new_rule_summary[18] = {"rule_number": 18, "constraint_id": 1003,
                                        "rule_text": mep_rules[18]["text"],
                                        "PASS": 0, "FAIL": 0, "N/A": 0, "total": 0}
            new_rule_summary[18][status] += 1
            new_rule_summary[18]["total"] += 1

    # Append results to result_state only if any MEP component was found
    if found_mep:
        result_state["rows"].extend(new_rows)
        existing_summary = {item["rule_number"]: item for item in result_state["rule_summary"]}
        for rn, summary in new_rule_summary.items():
            if rn in existing_summary:
                existing_summary[rn]["PASS"] += summary["PASS"]
                existing_summary[rn]["FAIL"] += summary["FAIL"]
                existing_summary[rn]["N/A"] += summary["N/A"]
                existing_summary[rn]["total"] += summary["total"]
            else:
                result_state["rule_summary"].append(summary)
        for k in totals:
            result_state["totals"][k] += totals[k]

        # Display in output_text
        output_text.configure(state=tk.NORMAL)
        output_text.insert(tk.END, "\n========== MEP Conflict Check Results (New Rules) ==========\n")
        for row in new_rows:
            output_text.insert(tk.END, f"  {row['component_type']} #{row['entity_id']} ({row['name']}) - Rule {row['rule_number']}: {row['result']}\n")
        output_text.insert(tk.END, f"\nMEP Totals: PASS {totals['PASS']}  FAIL {totals['FAIL']}  N/A {totals['N/A']}\n")
        output_text.see(tk.END)
        output_text.configure(state=tk.DISABLED)

        set_status(status_label, f"MEP check done: FAIL {totals['FAIL']}", "warn" if totals["FAIL"] > 0 else "done")
        messagebox.showinfo("MEP Check", f"Completed.\nPASS: {totals['PASS']}\nFAIL: {totals['FAIL']}\nN/A: {totals['N/A']}")
    else:
        set_status(status_label, "No MEP components found for rules 16-18", "warn")

    return found_mep

def mep_analysis(result_state, status_label, output_text, parent_window):
    """Run MEP conflict check and offer 2D/3D visualization only if components were found."""
    # Run the conflict check and get whether any MEP components were processed
    found = check_mep_conflicts(result_state, status_label, output_text)
    if not found:
        # No MEP components, do not open visualization dialog
        messagebox.showinfo("MEP Analysis", "No MEP components (fire hose, cabinet, outlet) found in the IFC file.\nVisualization not available.")
        return
    # If components were found, offer visualization
    dialog = tk.Toplevel(parent_window)
    dialog.title("MEP Visualization")
    dialog.geometry("400x200")
    dialog.transient(parent_window)
    dialog.grab_set()
    ttk.Label(dialog, text="MEP Conflict Analysis Completed", font=("Segoe UI", 12, "bold")).pack(pady=10)
    ttk.Label(dialog, text="Choose a visualization method:").pack(pady=5)
    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=20)
    def show_2d():
        dialog.destroy()
        plot_mep_conflicts(result_state, parent_window)
    def show_3d():
        dialog.destroy()
        plot_mep_conflicts_3d(result_state, parent_window)
    ttk.Button(btn_frame, text="2D Visualization", command=show_2d).pack(side=tk.LEFT, padx=10)
    ttk.Button(btn_frame, text="3D Visualization", command=show_3d).pack(side=tk.LEFT, padx=10)
    ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=10)

    def refresh_displayed_results(*args):
        method = method_var.get()
        if method == "Legacy":
            rows = result_state.get("rows_legacy", [])
            summary = result_state.get("summary_legacy", [])
            totals = result_state.get("totals_legacy", {"PASS":0,"FAIL":0,"N/A":0})
        elif method == "SPARQL":
            rows = result_state.get("rows_sparql", [])
            summary = result_state.get("summary_sparql", [])
            totals = result_state.get("totals_sparql", {"PASS":0,"FAIL":0,"N/A":0})
        elif method == "SHACL":
            rows = result_state.get("rows_shacl", [])
            summary = result_state.get("summary_shacl", [])
            totals = result_state.get("totals_shacl", {"PASS":0,"FAIL":0,"N/A":0})
        else:
            return

        # --- Print rule‑wise status to console for debugging ---
        print(f"\n=== {method} Results ===")
        rule_status = {}
        for row in rows:
            rn = row["rule_number"]
            # If multiple rows for same rule, keep the first (should be only one per rule)
            if rn not in rule_status:
                rule_status[rn] = row["result"]
        for rn in range(1, 19):
            status = rule_status.get(rn, "PASS")  # assume PASS if no row
            print(f"Rule {rn}: {status}")
        print(f"Totals: PASS {totals['PASS']} FAIL {totals['FAIL']} N/A {totals['N/A']}")
        # -----------------------------------------------------

        # Update the main result state fields
        result_state["rows"] = rows
        result_state["rule_summary"] = summary
        result_state["totals"] = totals
        # Update output text
        output_text.configure(state=tk.NORMAL)
        output_text.insert(tk.END, f"\n--- Switched to {method} method ---\n")
        output_text.insert(tk.END, f"PASS: {totals['PASS']}  FAIL: {totals['FAIL']}  N/A: {totals['N/A']}\n")
        output_text.see(tk.END)
        output_text.configure(state=tk.DISABLED)
        set_export_buttons(excel_btn, pdf_btn, len(rows) > 0)

        # Inside refresh_displayed_results, after obtaining rows for the method:
        failed_rules = [row["rule_number"] for row in rows if row["result"] == "FAIL"]
        print(f"{method} failed rules: {failed_rules}")
        na_rules = [rn for rn in range(1, 19) if rn not in [row["rule_number"] for row in rows] and all(
            row["result"] != "N/A" for row in rows if row["rule_number"] == rn)]  # approximate
        print(f"{method} N/A rules: {na_rules}")

# ------------------------------
# Main GUI
# ------------------------------
def main():
    root = tk.Tk()
    root.title("Stair Compliance Checker")
    root.geometry("1200x780")
    root.minsize(1000, 700)

    # styles (unchanged)
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg="#f0f4f8")
    style.configure("App.TFrame", background="#f0f4f8")
    style.configure("Card.TFrame", background="#ffffff")
    style.configure("Title.TLabel", background="#ffffff", foreground="#1e3a8a", font=("Segoe UI", 16, "bold"))
    style.configure("Body.TLabel", background="#ffffff", foreground="#334155", font=("Segoe UI", 10))
    style.configure("Section.TLabel", background="#ffffff", foreground="#1e293b", font=("Segoe UI", 11, "bold"))
    style.configure("Rule.TLabel", background="#ffffff", foreground="#334155", font=("Segoe UI", 9))
    style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12,6))
    style.configure("Secondary.TButton", font=("Segoe UI", 10), padding=(10,5))
    style.configure("Status.Ready.TLabel", background="#e2e8f0", foreground="#334155", font=("Segoe UI", 9, "bold"), padding=(8,4))
    style.configure("Status.Running.TLabel", background="#fef3c7", foreground="#92400e", font=("Segoe UI", 9, "bold"), padding=(8,4))
    style.configure("Status.Done.TLabel", background="#dcfce7", foreground="#166534", font=("Segoe UI", 9, "bold"), padding=(8,4))
    style.configure("Status.Warn.TLabel", background="#fef3c7", foreground="#92400e", font=("Segoe UI", 9, "bold"), padding=(8,4))
    style.configure("Status.Error.TLabel", background="#fecaca", foreground="#7f1d1d", font=("Segoe UI", 9, "bold"), padding=(8,4))

    app = ttk.Frame(root, style="App.TFrame", padding=18)
    app.pack(fill=tk.BOTH, expand=True)
    app.columnconfigure(0, weight=1)
    app.rowconfigure(3, weight=1)

    # header (unchanged)
    header = ttk.Frame(app, style="Card.TFrame", padding=(18,14))
    header.grid(row=0, column=0, sticky="ew", pady=(0,10))
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text="Stair Compliance Checker", style="Title.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(header, text="BIM-based rule checking, change impact analysis, and ontology reasoning", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(3,0))
    ifc_row = ttk.Frame(header, style="Card.TFrame")
    ifc_row.grid(row=2, column=0, sticky="ew", pady=(10,0))
    ifc_row.columnconfigure(1, weight=1)
    ttk.Label(ifc_row, text="IFC File", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0,10))
    ifc_var = tk.StringVar(value=get_default_ifc_path())
    if not os.path.exists(ifc_var.get()):
        ifc_var.set("")
    ifc_entry = ttk.Entry(ifc_row, textvariable=ifc_var)
    ifc_entry.grid(row=0, column=1, sticky="ew")
    ttk.Button(ifc_row, text="Browse...", style="Secondary.TButton", command=lambda: choose_ifc_file(ifc_var)).grid(row=0, column=2, padx=(8,0))

    # rules card (unchanged)
    rules_card = ttk.Frame(app, style="Card.TFrame", padding=(18,14))
    rules_card.grid(row=1, column=0, sticky="ew", pady=(0,10))
    rules_card.columnconfigure(0, weight=1)
    rules_card.columnconfigure(1, weight=1)
    ttk.Label(rules_card, text="Implemented Rules", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
    stair_rules = [
        "Rule 1  [ID 7] : 600 ≤ g+2h ≤ 660 mm",
        "Rule 2  [ID 8] : 210 ≤ g ≤ 310 mm",
        "Rule 3  [ID 3] : 20 < alpha ≤ 45 deg",
        "Rule 4  [ID 9] : overlap r ≥ 10 mm",
        "Rule 5  [ID 56]: 480 ≤ 2h+g ≤ 800 mm",
        "Rule 6  [ID 55]: number of risers ≥ 3",
        "Rule 7  [ID 55]: number of risers ≤ 16 (normal)",
        "Rule 8  [ID 55]: number of risers ≤ 22 (max)",
        "Rule 9  [ID 19]: single-flight climb height ≤ 4000 mm",
        "Rule 10 [ID 20]: multi-flight climb height ≤ 3000 mm",
        # "Rule 15 (storey): total climb height per storey ≤ 4000 mm"
    ]
    railing_rules = [
        "Rule 11 [ID 26]: guardrail height ≥ 1100 mm",
        "Rule 12 [ID 36]: handrail height 900..1000 mm",
        "Rule 13 [ID 60]: landing handrail height ≥ 1100 mm",
        "Rule 14 [ID 63]: handrail height 760..940 mm"
    ]
    stair_frame = ttk.Frame(rules_card, style="Card.TFrame")
    stair_frame.grid(row=1, column=0, sticky="nw", pady=(8,0), padx=(0,16))
    railing_frame = ttk.Frame(rules_card, style="Card.TFrame")
    railing_frame.grid(row=1, column=1, sticky="nw", pady=(8,0))
    ttk.Label(stair_frame, text="Stair Flight Rules", style="Section.TLabel").pack(anchor="w")
    for r in stair_rules:
        ttk.Label(stair_frame, text=r, style="Rule.TLabel").pack(anchor="w", pady=(2,0))
    ttk.Label(railing_frame, text="Railing Rules", style="Section.TLabel").pack(anchor="w")
    for r in railing_rules:
        ttk.Label(railing_frame, text=r, style="Rule.TLabel").pack(anchor="w", pady=(2,0))

    # ------------------------------------------------------------
    # Action bar (buttons)
    # ------------------------------------------------------------
    action_bar = ttk.Frame(app, style="Card.TFrame", padding=(18,12))
    action_bar.grid(row=2, column=0, sticky="ew", pady=(0,5))
    action_bar.columnconfigure(0, weight=1)
    button_row = ttk.Frame(action_bar, style="Card.TFrame")
    button_row.grid(row=0, column=0, sticky="w")
    run_btn = ttk.Button(button_row, text="Run Stair Checks", style="Primary.TButton")
    run_btn.pack(side=tk.LEFT, padx=(0,8))
    excel_btn = ttk.Button(button_row, text="Export Excel", style="Secondary.TButton")
    excel_btn.pack(side=tk.LEFT, padx=(0,8))
    pdf_btn = ttk.Button(button_row, text="Export PDF", style="Secondary.TButton")
    pdf_btn.pack(side=tk.LEFT, padx=(0,8))
    sim_btn = ttk.Button(button_row, text="Simulate Change Impact", style="Secondary.TButton")
    sim_btn.pack(side=tk.LEFT, padx=(0,8))
    mep_analysis_btn = ttk.Button(button_row, text="MEP Analysis", style="Primary.TButton")
    mep_analysis_btn.pack(side=tk.LEFT, padx=(0,8))
    save_btn = ttk.Button(button_row, text="Save Ontology", style="Secondary.TButton")
    save_btn.pack(side=tk.LEFT, padx=(0,8))
    load_btn = ttk.Button(button_row, text="Load Ontology", style="Secondary.TButton")
    load_btn.pack(side=tk.LEFT, padx=(0,8))
    reasoner_btn = ttk.Button(button_row, text="Apply Reasoner", style="Secondary.TButton")
    reasoner_btn.pack(side=tk.LEFT, padx=(0,8))

    # ------------------------------------------------------------
    # Method selection row (below action bar, above console)
    # ------------------------------------------------------------
    method_row = ttk.Frame(action_bar, style="Card.TFrame")
    method_row.grid(row=1, column=0, sticky="ew", pady=(5,0))
    method_row.columnconfigure(1, weight=1)
    ttk.Label(method_row, text="Display Results Method:", style="Body.TLabel").grid(row=0, column=0, sticky="w", padx=(0,10))
    method_var = tk.StringVar(value="Legacy")
    method_combo = ttk.Combobox(method_row, textvariable=method_var, values=["Legacy", "SPARQL", "SHACL"], state="readonly", width=12)
    method_combo.grid(row=0, column=1, sticky="w")
    ttk.Label(method_row, text="Switch between validation methods after analysis", font=("Segoe UI", 8, "italic")).grid(row=0, column=2, sticky="w", padx=(10,0))

    # ------------------------------------------------------------
    # Status row (below method row)
    # ------------------------------------------------------------
    status_row = ttk.Frame(action_bar, style="Card.TFrame")
    status_row.grid(row=2, column=0, sticky="ew", pady=(8,0))
    status_row.columnconfigure(0, weight=1)
    status_label = ttk.Label(status_row, text="Ready", style="Status.Ready.TLabel")
    status_label.grid(row=0, column=0, sticky="ew")

    # ------------------------------------------------------------
    # Output console
    # ------------------------------------------------------------
    output_card = ttk.Frame(app, style="Card.TFrame", padding=(18,14))
    output_card.grid(row=3, column=0, sticky="nsew")
    output_card.columnconfigure(0, weight=1)
    output_card.rowconfigure(1, weight=1)
    ttk.Label(output_card, text="Execution Log", style="Section.TLabel").grid(row=0, column=0, sticky="w")
    console = ttk.Frame(output_card, style="Card.TFrame")
    console.grid(row=1, column=0, sticky="nsew", pady=(8,0))
    console.columnconfigure(0, weight=1)
    console.rowconfigure(0, weight=1)
    output_text = tk.Text(console, wrap="none", font=("Consolas", 9), bg="#0f172a", fg="#e2e8f0", insertbackground="#e2e8f0", padx=12, pady=10)
    output_text.grid(row=0, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(console, orient=tk.VERTICAL, command=output_text.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(console, orient=tk.HORIZONTAL, command=output_text.xview)
    x_scroll.grid(row=1, column=0, sticky="ew")
    output_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    output_text.tag_configure("pass", foreground="#22c55e")
    output_text.tag_configure("fail", foreground="#f87171")
    output_text.tag_configure("na", foreground="#fbbf24")
    output_text.tag_configure("divider", foreground="#93c5fd")
    output_text.tag_configure("section", foreground="#f8fafc", font=("Consolas", 9, "bold"))
    output_text.insert(tk.END, "Press 'Run Stair Checks' to start.\n")
    output_text.configure(state=tk.DISABLED)

    # result state
    result_state = {}
    reset_result_state(result_state)

    # define refresh_displayed_results (must be defined before binding)
    def refresh_displayed_results(*args):
        method = method_var.get()
        if method == "Legacy":
            rows = result_state.get("rows_legacy", [])
            summary = result_state.get("summary_legacy", [])
            totals = result_state.get("totals_legacy", {"PASS":0,"FAIL":0,"N/A":0})
        elif method == "SPARQL":
            rows = result_state.get("rows_sparql", [])
            summary = result_state.get("summary_sparql", [])
            totals = result_state.get("totals_sparql", {"PASS":0,"FAIL":0,"N/A":0})
        elif method == "SHACL":
            rows = result_state.get("rows_shacl", [])
            summary = result_state.get("summary_shacl", [])
            totals = result_state.get("totals_shacl", {"PASS":0,"FAIL":0,"N/A":0})
        else:
            return
        result_state["rows"] = rows
        result_state["rule_summary"] = summary
        result_state["totals"] = totals
        # Update output text with new summary (append)
        output_text.configure(state=tk.NORMAL)
        output_text.insert(tk.END, f"\n--- Switched to {method} method ---\n")
        output_text.insert(tk.END, f"PASS: {totals['PASS']}  FAIL: {totals['FAIL']}  N/A: {totals['N/A']}\n")
        output_text.see(tk.END)
        output_text.configure(state=tk.DISABLED)
        set_export_buttons(excel_btn, pdf_btn, len(rows) > 0)

    method_combo.bind("<<ComboboxSelected>>", refresh_displayed_results)

    # command bindings (buttons)
    run_btn.configure(command=lambda: run_analysis(output_text, status_label, run_btn, excel_btn, pdf_btn, sim_btn, ifc_var, result_state))
    excel_btn.configure(command=lambda: export_excel(result_state, status_label))
    pdf_btn.configure(command=lambda: export_pdf(result_state, status_label))
    sim_btn.configure(command=lambda: show_simulation_dialog(root, result_state, status_label))
    save_btn.configure(command=lambda: save_ontology_to_file(result_state, status_label))
    load_btn.configure(command=lambda: load_ontology_from_file(result_state, status_label, excel_btn, pdf_btn, sim_btn, ifc_var, output_text, run_btn))
    reasoner_btn.configure(command=lambda: apply_reasoner(result_state, root, excel_btn, pdf_btn, sim_btn))
    mep_analysis_btn.configure(command=lambda: mep_analysis(result_state, status_label, output_text, root))

    # disable simulation and export initially
    sim_btn.state(["disabled"])
    set_export_buttons(excel_btn, pdf_btn, False)

    root.mainloop()

if __name__ == "__main__":
    main()