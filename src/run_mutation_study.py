"""Run 72 PRELIMINARY controlled parameter mutations on a small IFC4 fixture.

This test reopens real IFC files via IFCOpenShell, injects mathematical baseline
values according to the prospectively written test protocol, writes temporary
IFC states, extracts via the original procedural checker, then compares full
SPARQL rechecking against limited class-scoped and property-scoped decisions.
SHACL is also executed unless --no-shacl is supplied.

Research limitation: the supplied fixture originally lacked project units;
experiment-generated copies explicitly assign millimetres. They are controlled
fixture tests, NOT independently established code-compliance ground truth.
Neither structural relevance of all rules nor ISO/INSO/OSHA legal applicability
is fully adjudicated. Do NOT report these as real-project regulatory accuracy.
"""
from __future__ import annotations
import argparse
import ast
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def get_gui():
    spec = importlib.util.spec_from_file_location('jcem_for_mutations', HERE / 'compliance_gui.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_rule_queries():
    # Read the exact 14 query declarations used by the GUI. No second hand-coded
    # version of the formulas is maintained by the test runner.
    tree = ast.parse((HERE / 'compliance_gui.py').read_text(encoding='utf-8'))
    method = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'evaluate_rules_via_sparql')
    defs = [node for node in ast.walk(method) if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'rule_queries' for t in node.targets)]
    if len(defs) != 1:
        raise RuntimeError('Expected exactly one rule_queries dict inside evaluate_rules_via_sparql')
    queries = ast.literal_eval(defs[0].value)
    if sorted(queries) != list(range(1, 15)):
        raise RuntimeError('Expected rules 1..14 to be active')
    return queries


def set_mm_project_units(model):
    project = model.by_type('IfcProject')[0]
    if getattr(project, 'UnitsInContext', None) is not None:
        raise RuntimeError('Model already has units: use a different fixture and first verify conversion')
    mm = model.create_entity('IfcSIUnit', UnitType='LENGTHUNIT', Prefix='MILLI', Name='METRE')
    project.UnitsInContext = model.create_entity('IfcUnitAssignment', Units=[mm])


def get_first(model, name):
    items = model.by_type(name)
    if not items:
        raise RuntimeError(f'Fixture does not contain {name}')
    return items[0]


def set_element_measure(model, element, quantity_name, value):
    """Edit/remove all matching IFC quantity and property-set values."""
    found = 0
    for rel in getattr(element, 'IsDefinedBy', ()) or ():
        if not rel.is_a('IfcRelDefinesByProperties'):
            continue
        pset = rel.RelatingPropertyDefinition
        if pset is None:
            continue
        if pset.is_a('IfcElementQuantity'):
            quantities = tuple(pset.Quantities or ())
            matches = [q for q in quantities if q.Name == quantity_name]
            if not matches:
                continue
            found += len(matches)
            if value is None:
                remaining = tuple(q for q in quantities if q.Name != quantity_name)
                if remaining:
                    pset.Quantities = remaining
                else:
                    # An empty IfcElementQuantity is not valid; detach it.
                    model.remove(rel)
                    model.remove(pset)
            else:
                for q in matches:
                    attr = 'CountValue' if q.is_a('IfcQuantityCount') else 'LengthValue'
                    setattr(q, attr, float(value))
        elif pset.is_a('IfcPropertySet'):
            properties = tuple(pset.HasProperties or ())
            matches = [p for p in properties if p.Name == quantity_name and p.is_a('IfcPropertySingleValue')]
            if not matches:
                continue
            found += len(matches)
            if value is None:
                remaining = tuple(p for p in properties if p not in matches)
                if remaining:
                    pset.HasProperties = remaining
                else:
                    model.remove(rel)
                    model.remove(pset)
            else:
                for p in matches:
                    p.NominalValue = model.create_entity('IfcReal', float(value))
    if not found and value is not None:
        typ = 'IfcQuantityCount' if quantity_name == 'NumberOfRisers' else 'IfcQuantityLength'
        quantity = model.create_entity(typ, Name=quantity_name, **{
            'CountValue' if typ == 'IfcQuantityCount' else 'LengthValue': float(value)})
        qto = model.create_entity('IfcElementQuantity', GlobalId=new_guid(), Name='MutationQuantities', Quantities=[quantity])
        model.create_entity('IfcRelDefinesByProperties', GlobalId=new_guid(), RelatedObjects=[element], RelatingPropertyDefinition=qto)


def new_guid():
    import ifcopenshell
    return ifcopenshell.guid.new()


def ensure_second_flight(model):
    """Add one fully specified sibling for multi-flight rule 10 tests."""
    stair = get_first(model, 'IfcStair')
    flight = model.create_entity('IfcStairFlight', GlobalId=new_guid(), Name='Sibling flight for Rule 10')
    aggregations = [r for r in model.by_type('IfcRelAggregates') if r.RelatingObject == stair]
    if len(aggregations) != 1:
        raise RuntimeError('Expected one stair aggregation in fixture')
    rel = aggregations[0]
    rel.RelatedObjects = tuple(rel.RelatedObjects) + (flight,)
    # Complete sibling information so that global rule outcomes are not masked
    # by an incomplete second target.
    for name, value in [('RiserHeight',150),('TreadLength',280),('NosingLength',10),('NumberOfRisers',10)]:
        set_element_measure(model, flight, name, value)
    return flight


def prepare_protocol_values(case):
    target = case['targets'][0]
    base = dict(case['baseline'])
    prop = case['property']
    new_value = case['new_value']
    notes = []
    if prop == 'climb height':
        # Climb = rise * riser count, so mutating climb independently would be
        # physically inconsistent with the rules' own derived geometry.
        n = 20
        h = base['climb height'] / n if base['climb height'] is not None else None
        base = {'h':h,'number of risers':n}
        prop = 'h'
        new_value = new_value / n if new_value is not None else None
        notes.append('Derived climb-height edit converted to h mutation with n=20')
    return target, base, prop, new_value, notes


MEASURE_NAMES = {'h':'RiserHeight','g':'TreadLength','r':'NosingLength',
                 'number of risers':'NumberOfRisers','height h':'Height'}


def make_state(case, is_after, filepath):
    import ifcopenshell
    model = ifcopenshell.open(str(HERE.parent / 'data' / 'fixtures' / 'stair_mep_mutation_base.ifc'))
    set_mm_project_units(model)
    target, baseline, prop, new_value, notes = prepare_protocol_values(case)
    if target == 10:
        ensure_second_flight(model)
    element = get_first(model, 'IfcRailing' if target in (11, 12, 13, 14) else 'IfcStairFlight')
    if target in (12, 14):
        element.Name = 'Test Handrail'
        element.ObjectType = 'HANDRAIL'
    elif target in (11, 13):
        element.Name = 'Test Guardrail'
        element.ObjectType = 'GUARD'
    for key, value in baseline.items():
        if key not in MEASURE_NAMES:
            raise RuntimeError(f'Unsupported baseline property {key!r}')
        set_element_measure(model, element, MEASURE_NAMES[key], value)
    if is_after:
        set_element_measure(model, element, MEASURE_NAMES[prop], new_value)
    model.write(str(filepath))
    return {'target_rule':target,'property':prop,'baseline':baseline,'new_value':new_value,'notes':notes}


def extract_graph(ui, path):
    from rdf_mapper import populate_rdf_graph
    env = __import__('os').environ.copy()
    env['IFC_FILE_PATH'] = str(path)
    proc = subprocess.run([sys.executable, str(HERE / 'ifc_extractor.py')],
                          cwd=str(HERE), env=env, capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError('Procedural extraction failed: ' + proc.stderr[-1600:])
    rows,_,component_data,ontology = ui.extract_results(proc.stdout)
    ui.extract_railing_heights_from_ifc(str(path),component_data)
    graph = populate_rdf_graph(component_data, ontology.relations if ontology else (),
                               ontology_ttl_path=str(HERE / 'stair_ontology.ttl'))
    return graph, component_data, ontology


def test_rule(graph, rule_id, rules):
    from semantic_checks import classify_rule_contexts, classify_outcome
    r = rules[rule_id]
    precheck = classify_rule_contexts(graph, rule_id, r['target'])
    violating = set()
    if precheck['eligible']:
        # Never convert an unexpected query error into a PASS or N/A.
        violating = {row[0] for row in graph.query(r['query'])}
    outward, detail, _ = classify_outcome(precheck, violating)
    return {'outward':outward,'detail':detail,
            'targets':len(precheck['all_targets']), 'evaluated':len(precheck['eligible'])}


def full_check(graph, rules, rule_ids):
    return {rid:test_rule(graph,rid,rules) for rid in rule_ids}


def timed_rules(graph, rules, rids, repetitions):
    samples=[]
    for _ in range(repetitions):
        t=perf_counter()
        full_check(graph,rules,rids)
        samples.append(perf_counter()-t)
    return {'mean_seconds':statistics.mean(samples),
            'std_seconds':statistics.stdev(samples) if len(samples)>1 else None,
            'samples_seconds':samples}


def class_scoped_ids(rule_target):
    if rule_target in range(1,11): return list(range(1,11))
    if rule_target in (11,13): return [11,13]
    if rule_target in (12,14): return [12,14]
    raise RuntimeError('Unsupported target rule')


def expected_transition_label(case):
    raw = case['expected']
    parts = raw.split('→')
    if len(parts) != 2:
        return None
    before = parts[0].split(':')[-1].strip()
    after = parts[1].split(':')[-1].strip()
    convert = {'INSUFF. INFO':'INSUFFICIENT_INFORMATION',
               'N/A':'INSUFFICIENT_INFORMATION', 'PASS':'PASS','FAIL':'FAIL'}
    return convert.get(before), convert.get(after)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',default='mutation_results_72.json')
    ap.add_argument('--timing-repetitions',type=int,default=5)
    ap.add_argument('--limit',type=int,default=72,help='Use 2 for a quick smoke test')
    ap.add_argument('--no-shacl',action='store_true',help='Skip SHACL until initial SPARQL run succeeds')
    args=ap.parse_args()
    if args.timing_repetitions < 1:
        ap.error('--timing-repetitions must be >= 1')
    import ifcopenshell, rdflib
    if not args.no_shacl:
        import pyshacl
    ui=get_gui()
    rules=get_rule_queries()
    cases=json.loads((HERE.parent / 'data' / 'protocols' / 'mutation_cases_72.json').read_text(encoding='utf-8'))['cases'][:args.limit]
    root_sha=hashlib.sha256((HERE.parent / 'data' / 'fixtures' / 'stair_mep_mutation_base.ifc').read_bytes()).hexdigest()
    report={'status':'PRELIMINARY_IFC_FIXTURE_MUTATIONS_NOT_EXPERT_VALIDATED',
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'python':sys.version,'platform':platform.platform(),
            'original_fixture_sha256':root_sha,'model_state':'IFCOpenShell-generated temporary copies of uploaded IFC4 fixture',
            'unit_assumption':'Original fixture has no UnitsInContext. Generated copies explicitly declare millimetres; original files unchanged.',
            'regulatory_applicability_expert_verified':False,
            'timing_scope':'SPARQL rule checks on already built RDF graphs; excludes IFC parsing/graph construction/display',
            'scope_warning':'Controlled rule-formula tests only; regulatory profiles and independent ground truth remain unverified',
            'cases':[]}
    for idx,case in enumerate(cases,1):
        row={'case_id':case['case_id'],'target_rules':case['targets'],'protocol_expectation':case['expected']}
        print(f'[{idx}/{len(cases)}] {case["case_id"]}',flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix='jcem_mut_') as td:
                before_path=Path(td)/'before.ifc';after_path=Path(td)/'after.ifc'
                spec=make_state(case,False,before_path)
                make_state(case,True,after_path)
                graph0,components,ontology=extract_graph(ui,before_path)
                graph1,_,_=extract_graph(ui,after_path)
                full0=full_check(graph0,rules,range(1,15))
                full1=full_check(graph1,rules,range(1,15))
                expected_pair=expected_transition_label(case)
                expected_matched=(expected_pair is not None and all(
                    (full0[rid]['detail'],full1[rid]['detail'])==expected_pair for rid in case['targets']))
                changed={rid for rid in rules if full0[rid]['detail']!=full1[rid]['detail']}
                outward_changed={rid for rid in rules if full0[rid]['outward']!=full1[rid]['outward']}
                cls_ids=class_scoped_ids(spec['target_rule'])
                cls0=full_check(graph0,rules,cls_ids)
                cls1=full_check(graph1,rules,cls_ids)
                cls_changed={rid for rid in cls_ids if cls0[rid]['detail']!=cls1[rid]['detail']}
                cls_outward={rid for rid in cls_ids if cls0[rid]['outward']!=cls1[rid]['outward']}
                # Separate comparison with the ACTUAL GUI change analyzer.
                target_type='IfcRailing' if spec['target_rule'] in (11,12,13,14) else 'IfcStairFlight'
                # In Rule 10 tests a sibling stair flight is intentionally added.
                # The mutation is applied to the FIRST flight in the IFC, not the
                # sibling. Select that instance by STEP id rather than asserting
                # that the model has only one flight.
                before_ifc=ifcopenshell.open(str(before_path))
                modified_entity=get_first(before_ifc,target_type)
                modified_id=modified_entity.id()
                comp_key=(target_type,modified_id)
                if comp_key not in components:
                    raise RuntimeError(f'Modified IFC target {comp_key} is missing from extraction; candidates: '+str([k for k in components if k[0]==target_type]))
                analyzer=ui.ChangeImpactAnalyzer()
                analyzer.set_component_data(components,ontology)
                reported=analyzer.find_impact(comp_key,spec['property'],spec['new_value'])
                analyzed_changed={r['rule_id'] for r in reported if r['rule_id']<=14}
                if not args.no_shacl:
                    _,shacl0,_=ui.evaluate_rules_via_shacl(graph0)
                    _,shacl1,_=ui.evaluate_rules_via_shacl(graph1)
                    sh0={r['rule_number']:r['status_detail'] for r in shacl0}
                    sh1={r['rule_number']:r['status_detail'] for r in shacl1}
                    shacl_mismatch=sorted(rid for rid in rules if sh0[rid]!=full0[rid]['detail'] or sh1[rid]!=full1[rid]['detail'])
                else:shacl_mismatch=None
                # Alternate order of paired timings avoids consistent warm-cache bias.
                if idx%2:
                    t_full=timed_rules(graph1,rules,range(1,15),args.timing_repetitions)
                    t_partial=timed_rules(graph1,rules,cls_ids,args.timing_repetitions)
                else:
                    t_partial=timed_rules(graph1,rules,cls_ids,args.timing_repetitions)
                    t_full=timed_rules(graph1,rules,range(1,15),args.timing_repetitions)
                row.update({'case_parameters':spec,
                            'protocol_target_expectations_matched':expected_matched,
                            'old_full_status':{str(k):v['detail'] for k,v in full0.items()},
                            'new_full_status':{str(k):v['detail'] for k,v in full1.items()},
                            'full_changed_rule_ids':sorted(changed),
                            'full_outward_changed_rule_ids':sorted(outward_changed),
                            'class_scoped_rule_ids':cls_ids,
                            'class_scoped_changed_rule_ids':sorted(cls_changed),
                            'class_scoped_missed_detailed':sorted(changed-cls_changed),
                            'class_scoped_extra_detailed':sorted(cls_changed-changed),
                            'class_scoped_missed_outward':sorted(outward_changed-cls_outward),
                            'gui_analyzer_changed_rule_ids':sorted(analyzed_changed),
                            'gui_analyzer_missed_outward':sorted(outward_changed-analyzed_changed),
                            'gui_analyzer_extra_outward':sorted(analyzed_changed-outward_changed),
                            'shacl_disagreement_rule_ids':shacl_mismatch,
                            'mean_full_seconds':t_full['mean_seconds'],
                            'std_full_seconds':t_full['std_seconds'],
                            'mean_partial_seconds':t_partial['mean_seconds'],
                            'std_partial_seconds':t_partial['std_seconds'],
                            'full_timing_samples':t_full['samples_seconds'],
                            'partial_timing_samples':t_partial['samples_seconds'],
                            'ratio_full_over_partial':t_full['mean_seconds']/t_partial['mean_seconds'] if t_partial['mean_seconds'] else None,
                            'status':'COMPLETED_DIAGNOSTIC'})
        except Exception as e:
            row.update({'status':'ERROR','error':str(e),'traceback':traceback.format_exc(limit=6)})
            print('  ERROR:',e,flush=True)
        report['cases'].append(row)
    ok=[x for x in report['cases'] if x['status']=='COMPLETED_DIAGNOSTIC']
    report['summary']={'n_requested':len(cases),'n_completed':len(ok),'n_errors':len(cases)-len(ok),
                       'total_class_scoped_missed_outward':sum(len(x['class_scoped_missed_outward']) for x in ok),
                       'total_gui_analyzer_missed_outward':sum(len(x['gui_analyzer_missed_outward']) for x in ok),
                       'total_gui_analyzer_extra_outward':sum(len(x['gui_analyzer_extra_outward']) for x in ok),
                       'n_with_shacl_discrepancy':sum(bool(x['shacl_disagreement_rule_ids']) for x in ok),
                       'target_expectation_matches':sum(bool(x['protocol_target_expectations_matched']) for x in ok),
                       'n_protocol_expectation_mismatches':sum(not x['protocol_target_expectations_matched'] for x in ok),
                       'median_full_over_class_scoped_ratio':statistics.median(x['ratio_full_over_partial'] for x in ok) if ok else None}
    out=Path(args.out)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    csv_out=out.with_suffix('.csv')
    with csv_out.open('w',newline='',encoding='utf-8-sig') as f:
        fields=['case_id','status','target_rules','protocol_target_expectations_matched','full_changed_rule_ids','class_scoped_changed_rule_ids',
                'class_scoped_missed_outward','gui_analyzer_missed_outward','gui_analyzer_extra_outward',
                'shacl_disagreement_rule_ids','mean_full_seconds','mean_partial_seconds',
                'ratio_full_over_partial','error']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for rec in report['cases']:
            writer.writerow({k:json.dumps(rec[k]) if isinstance(rec.get(k),list) else rec.get(k,'') for k in fields})
    print('\nSAVED:',out.resolve(),csv_out.resolve())
    print('SUMMARY:',json.dumps(report['summary'],indent=2))
    print('Please send both outputs to ChatGPT for review. Do not use directly as regulatory ground truth.')


if __name__=='__main__':
    main()
