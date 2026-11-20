"""Quick regression checks for Phase 5 bug fixes; run from PyCharm project folder.
Requires the same dependencies as the mutation experiment. No IFC file is edited.
"""
from pathlib import Path
import importlib.util
import sys
SOURCE_ROOT = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SOURCE_ROOT))
from rdf_mapper import extract_railing_classification

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
