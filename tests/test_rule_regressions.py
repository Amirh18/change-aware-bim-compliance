"""Quick regression checks for Phase 5 bug fixes; run from PyCharm project folder.
Requires the same dependencies as the mutation experiment. No IFC file is edited.
"""
from pathlib import Path
import importlib.util
import sys
SOURCE_ROOT = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SOURCE_ROOT))
from rdflib import Literal, Namespace
from rdflib.namespace import RDF, XSD
from rdf_mapper import extract_railing_classification, populate_rdf_graph
from ifc_extractor import find_landing_handrail_height
from semantic_checks import REQUIRED

STAIR = Namespace("http://example.com/stairOntology#")
ROOT = SOURCE_ROOT
s = (ROOT / 'ifc_extractor.py').read_text(encoding='utf-8')
assert 'climb_h:.12g' in s, 'Precision fix missing from procedural extractor'
assert float(format(200.05, '.12g')) * 20 > 4000, 'Rule 9 threshold precision broken'

spec = importlib.util.spec_from_file_location('phase5_gui', ROOT / 'compliance_gui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

for kind, old_value, new_value, expected in [
    ("guardrail", 1099.0, 1100.0, [11]),
    ("handrail", 1200.0, 900.0, [12, 14]),
]:
    key = ("IfcRailing", 23)
    component = {"type": "IfcRailing", "name": "Test " + kind.capitalize(),
                 "metadata": "Class=" + kind, "height h": old_value}
    assert extract_railing_classification(component) == kind
    analyzer = ui.ChangeImpactAnalyzer()
    analyzer.set_component_data({key: component})
    observed = sorted(x["rule_id"] for x in analyzer.find_impact(key, "height h", new_value))
    assert observed == expected, f"{kind}: expected {expected}, observed {observed}"
    print(f"PASS: {kind} height edit affects only rules {expected}")

landing_key = ("IfcRailing", 24)
landing_component = {
    "type": "IfcRailing", "name": "Test Landing Handrail",
    "metadata": "Class=landing_handrail",
    "height h": 900.0, "landing handrail height": 1099.0,
}
assert extract_railing_classification(landing_component) == "landing_handrail"
landing_analyzer = ui.ChangeImpactAnalyzer()
landing_analyzer.set_component_data({landing_key: landing_component})
landing_impacts = landing_analyzer.find_impact(
    landing_key, "landing handrail height", 1100.0
)
assert [x["rule_id"] for x in landing_impacts] == [13], landing_impacts
assert landing_analyzer.evaluate_rule(13, {"landing handrail height": 1099.0}) == "FAIL"
assert landing_analyzer.evaluate_rule(13, {"landing handrail height": 1100.0}) == "PASS"
assert landing_analyzer.evaluate_rule(13, {"height h": 1200.0}) == "N/A"
assert REQUIRED[13] == ("hasLandingHandrailHeight",)
assert ui.suggest_fix_for_rule(13, {"landing handrail height": 1099.0})[:2] == (
    "landing handrail height", 1100.0
)
print("PASS: Rule 13 uses landing-specific data without cross-triggering")

print('PASS: high precision threshold handling')
print('PASS: Phase 5 regression checks completed')

# Rule 13 data-model regression: the landing datum must remain distinct from
# the generic IFC railing Height and from the stair handrail height.
landing_component = {
    "type": "IfcRailing",
    "entity_id": 24,
    "name": "Test Landing Handrail",
    "metadata": "Class=landing_handrail",
    "height h": 900.0,
    "landing handrail height": 1099.0,
}
assert extract_railing_classification(landing_component) == "landing_handrail"
landing_graph = populate_rdf_graph({("IfcRailing", 24): landing_component})
landing_uri = STAIR.LandingHandrail_24
assert (landing_uri, RDF.type, STAIR.LandingHandrail) in landing_graph
assert (landing_uri, RDF.type, STAIR.Handrail) not in landing_graph
assert (landing_uri, STAIR.hasLandingHandrailHeight,
        Literal(1099.0, datatype=XSD.float)) in landing_graph
assert (landing_uri, STAIR.hasRailingHeight,
        Literal(900.0, datatype=XSD.float)) in landing_graph
assert not list(landing_graph.objects(landing_uri, STAIR.hasGuardrailHeight))
assert not list(landing_graph.objects(landing_uri, STAIR.hasHandrailHeight))

landing_psets = {
    "Pset_ComplianceLandingHandrail": {
        "HeightAboveLandingWalkingLevel": 1099.0
    }
}
assert find_landing_handrail_height(landing_psets)[0] == 1099.0
assert find_landing_handrail_height({"Pset_RailingCommon": {"Height": 1100.0}})[0] is None
print("PASS: landing handrail class and height datum remain distinct")

# SPARQL and SHACL must both use the landing-specific measurement and boundary.
def get_rule13_summary(result):
    _, summary, _ = result
    return next(row for row in summary if row["rule_number"] == 13)

for height, expected in [(1099.0, "FAIL"), (1100.0, "PASS")]:
    component = {
        "type": "IfcRailing", "entity_id": 31,
        "name": "Test Landing Handrail",
        "metadata": "Class=landing_handrail",
        "landing handrail height": height,
    }
    graph = populate_rdf_graph({("IfcRailing", 31): component})
    sparql_row = get_rule13_summary(ui.evaluate_rules_via_sparql(graph))
    shacl_row = get_rule13_summary(ui.evaluate_rules_via_shacl(graph))
    assert sparql_row["status_detail"] == expected, sparql_row
    assert shacl_row["status_detail"] == expected, shacl_row
    print(f"PASS: SPARQL and SHACL Rule 13 at {height} mm -> {expected}")

print("PASS: Rule 13 SPARQL/SHACL parity")
