"""IFC-level regressions for the five landing-hand­rail Rule 13 mutation cases.

Run from the repository root:
    python tests/test_rule13_ifc_mutations.py

These are synthetic fixture tests, not regulatory ground truth.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import ifcopenshell
import run_mutation_study as study
from rdflib import Namespace
from rdflib.namespace import RDF
from rdf_mapper import STAIR


def main():
    protocol = json.loads(
        (ROOT / "data" / "protocols" / "mutation_cases_72.json").read_text(encoding="utf-8")
    )["cases"]
    cases = [case for case in protocol if case["targets"] == [13]]
    assert [case["case_id"] for case in cases] == [
        "M061", "M062", "M063", "M064", "M065"
    ]

    ui = study.get_gui()
    rules = study.get_rule_queries()

    with tempfile.TemporaryDirectory(prefix="jcem_rule13_") as temp:
        temp = Path(temp)
        for case in cases:
            before_path = temp / (case["case_id"] + "_before.ifc")
            after_path = temp / (case["case_id"] + "_after.ifc")

            spec = study.make_state(case, False, before_path)
            study.make_state(case, True, after_path)

            # Confirm the dedicated IFC property is present/missing as the protocol says.
            before_model = ifcopenshell.open(str(before_path))
            after_model = ifcopenshell.open(str(after_path))
            before_element = study.get_first(before_model, "IfcRailing")
            after_element = study.get_first(after_model, "IfcRailing")
            assert before_element.Name == "Test Landing Handrail"
            assert before_element.ObjectType == "LANDING_HANDRAIL"

            def landing_values(element):
                values = []
                for rel in element.IsDefinedBy or ():
                    if not rel.is_a("IfcRelDefinesByProperties"):
                        continue
                    pset = rel.RelatingPropertyDefinition
                    if not pset or not pset.is_a("IfcPropertySet"):
                        continue
                    if pset.Name != "Pset_ComplianceLandingHandrail":
                        continue
                    for prop in pset.HasProperties or ():
                        if prop.Name == "HeightAboveLandingWalkingLevel":
                            values.append(float(prop.NominalValue.wrappedValue))
                return values

            assert landing_values(before_element) == [
                float(case["baseline"]["landing handrail height"])
            ], (case["case_id"], landing_values(before_element))
            expected_after = case["new_value"]
            assert landing_values(after_element) == (
                [] if expected_after is None else [float(expected_after)]
            ), (case["case_id"], landing_values(after_element))

            graph0, components0, _ = study.extract_graph(ui, before_path)
            graph1, components1, _ = study.extract_graph(ui, after_path)

            key = ("IfcRailing", before_element.id())
            component = components0[key]
            assert component["metadata"].lower().find("landing_handrail") >= 0, component
            assert component.get("landing handrail height") == float(
                case["baseline"]["landing handrail height"]
            )
            uri = STAIR[f"LandingHandrail_{before_element.id()}"]
            assert (uri, RDF.type, STAIR.LandingHandrail) in graph0

            old_status = study.test_rule(graph0, 13, rules)["detail"]
            new_status = study.test_rule(graph1, 13, rules)["detail"]
            expected_pair = study.expected_transition_label(case)
            assert (old_status, new_status) == expected_pair, (
                case["case_id"], old_status, new_status, expected_pair
            )

            changed = {
                rid for rid in range(1, 15)
                if study.test_rule(graph0, rid, rules)["detail"]
                != study.test_rule(graph1, rid, rules)["detail"]
            }
            assert changed == ([13] if old_status != new_status else []), (
                case["case_id"], changed
            )
            print(
                f"PASS: {case['case_id']} landing datum "
                f"{old_status} -> {new_status}; changed rules={sorted(changed)}"
            )

    print("PASS: all five Rule 13 IFC mutation cases use the landing-specific datum")


if __name__ == "__main__":
    main()
