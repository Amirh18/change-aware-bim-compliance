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

STAIR = Namespace("http://example.com/stairOntology#")
ROOT = SOURCE_ROOT
s = (ROOT / 'ifc_extractor.py').read_text(encoding='utf-8')
assert 'climb_h:.12g' in s, 'Precision fix missing from procedural extractor'
assert float(format(200.05, '.12g')) * 20 > 4000, 'Rule 9 threshold precision broken'

spec = importlib.util.spec_from_file_location('phase5_gui', ROOT / 'compliance_gui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

for kind, expected in [('guardrail', [11,13]),('handrail', [12,14])]:
    key = ('IfcRailing', 23)
    component = {'type':'IfcRailing','name':'Test '+kind.capitalize(),
                 'metadata':'Class='+kind,'height h':1200.0}
    assert extract_railing_classification(component) == kind
    analyzer = ui.ChangeImpactAnalyzer()
    analyzer.set_component_data({key: component})
    observed = sorted(x['rule_id'] for x in analyzer.find_impact(key,'height h',None))
    assert observed == expected, f'{kind}: expected {expected}, observed {observed}'
    print(f'PASS: {kind} change only affects its own rule class')
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
assert (landing_uri, RDF.type, STAIR.Handrail) in landing_graph
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
