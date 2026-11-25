"""Regression checks for illustrative MEP outcomes, not regulatory validation."""
from pathlib import Path
import importlib.util
import sys

SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
spec = importlib.util.spec_from_file_location('mep_gui', SRC / 'compliance_gui.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
a = gui.ChangeImpactAnalyzer()
checks = [
    (16, {'number of risers': 13, 'guardrail_height': 1100, 'fire_hose_height': 1100}, 'FAIL'),
    (16, {'number of risers': 12, 'guardrail_height': 1100, 'fire_hose_height': 1100}, 'PASS'),
    (16, {'number of risers': 13, 'fire_hose_height': 1100}, 'N/A'),
    (17, {'g': 280, 'climb height': 3653, 'cabinet_depth': 150}, 'FAIL'),
    (17, {'g': 1200, 'climb height': 6000, 'cabinet_depth': 150}, 'PASS'),
    (17, {'g': 280, 'cabinet_depth': 150}, 'N/A'),
    (18, {'h': 281, 'g': 280, 'outlet_height': 900}, 'FAIL'),
    (18, {'h': 175, 'g': 280, 'outlet_height': 900}, 'PASS'),
    (18, {'g': 280, 'outlet_height': 900}, 'N/A'),
]
for rid, params, expected in checks:
    observed = a.evaluate_rule(rid, params)
    assert observed == expected, (rid, params, observed, expected)
    print(f'PASS: Rule {rid} -> {expected}')
assert gui.mep_guardrail_height({}) is None
assert gui.mep_guardrail_height({('IfcRailing', 1): {'name': 'Handrail', 'height h': 1000}}) is None
assert gui.mep_guardrail_height({('IfcRailing', 1): {'name': 'Guardrail', 'height h': 1100}}) == 1100
assert gui.mep_guardrail_height({('IfcRailing', 1): {'name': 'Guardrail', 'height h': 1100}, ('IfcRailing', 2): {'name': 'Guardrail', 'height h': 1200}}) is None
print('PASS: no default guardrail height and no substitution from handrail')
for rid in (16, 17, 18):
    assert gui.suggest_fix_for_rule(rid, {}) == (None, None, None)
print('PASS: illustrative MEP rules do not generate automated design fixes')
k = ('IfcStairFlight', 9)
data = {
    k: {'type': 'IfcStairFlight', 'name': 'Flight 1', 'h': 175, 'g': 280, 'number of risers': 13, 'climb height': 2275, 'r': 15},
    ('IfcRailing', 18): {'type': 'IfcRailing', 'name': 'Guardrail', 'height h': 1100},
    ('IfcFireSuppressionTerminal', 20): {'type': 'IfcFireSuppressionTerminal', 'name': 'Fire hose valve', 'fire_hose_height': 1100},
    ('IfcFireSuppressionTerminal', 21): {'type': 'IfcFireSuppressionTerminal', 'name': 'Cabinet', 'cabinet_depth': 150},
    ('IfcOutlet', 22): {'type': 'IfcOutlet', 'name': 'Outlet', 'outlet_height': 900},
}
a.set_component_data(data, [(k, 'isPartOf', ('IfcStair', 4))])
observed = sorted((i['rule_id'], i['old_status'], i['new_status']) for i in a.find_impact(k, 'h', 281))
assert observed == [(rid, 'PASS', 'FAIL') for rid in (1, 3, 5, 18)], observed
print('PASS: synthetic M015 parameters -> changed rules [1, 3, 5, 18]')
# Rule 17 must respond when a riser change crosses its derived climb threshold.
data[k].update({'h': 200, 'g': 1200, 'number of risers': 30, 'climb height': 6000})
assert any(i['rule_id'] == 17 and i['new_status'] == 'FAIL' for i in a.find_impact(k, 'h', 201))
print('PASS: derived climb dependency for Rule 17')
