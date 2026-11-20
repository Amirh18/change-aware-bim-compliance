"""Run the procedural, direct SPARQL, and SHACL paths on user-supplied IFC files.

Requires a local Python environment with ifcopenshell, pyshacl, rdflib.
Keeps original IFC models unchanged. Output is a *diagnostic*, not independently
validated code compliance. Recommended use: provide your original controlled
base IFC and violation variant(s) as input files.

Windows example:
  python src/run_ifc_baseline.py --models "data/fixtures/stair_mep_mutation_base.ifc" --out baseline_local.json
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))


def version(module_name):
    try:
        m=__import__(module_name)
        return str(getattr(m,'__version__',getattr(m,'version','unknown')))
    except ImportError:
        return None


def get_gui():
    spec=importlib.util.spec_from_file_location('jcem_ui_fixed',ROOT/'compliance_gui.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute_one(ui, path):
    import ifcopenshell
    import ifcopenshell.util.unit
    from rdf_mapper import populate_rdf_graph
    model=ifcopenshell.open(str(path))
    counts={name:len(model.by_type(name)) for name in ('IfcStair','IfcStairFlight','IfcRailing','IfcBuildingStorey')}
    # Report project-unit metadata; do not silently assume metres for an
    # IFC file that does not declare UnitsInContext.
    projects = model.by_type('IfcProject')
    project_unit_assignment_present = bool(projects and getattr(projects[0], 'UnitsInContext', None))
    try:
        mm_per_model_length_unit = (1000 * ifcopenshell.util.unit.calculate_unit_scale(model)
                                    if project_unit_assignment_present else None)
    except Exception:
        mm_per_model_length_unit = None

    env=os.environ.copy(); env['IFC_FILE_PATH']=str(path)
    proc=subprocess.run([sys.executable,'-u',str(ROOT/'ifc_extractor.py')],
                       cwd=str(ROOT),env=env,capture_output=True,text=True,timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f'Procedural checker failed, exit code {proc.returncode}: {proc.stderr[-1200:]}')
    raw=proc.stdout
    legacy,_,component_data,ontology=ui.extract_results(raw)
    ui.extract_railing_heights_from_ifc(str(path),component_data)
    graph=populate_rdf_graph(component_data,ontology.relations if ontology else (),
                             ontology_ttl_path=str(ROOT/'stair_ontology.ttl'))
    sp_rows,sp_summary,sp_totals=ui.evaluate_rules_via_sparql(graph)
    sh_rows,sh_summary,sh_totals=ui.evaluate_rules_via_shacl(graph)
    legacy_summary,legacy_totals=ui.build_rule_summary(legacy)
    def by_rule(summary):
        return {int(row['rule_number']): next((status for status in ('PASS','FAIL','N/A')
                                               if int(row.get(status,0))>0), 'N/A')
                for row in summary}
    proc_status=by_rule(legacy_summary)
    sparql_status=by_rule(sp_summary)
    shacl_status=by_rule(sh_summary)
    disagreements=[]
    for rid in sorted(set(proc_status)|set(sparql_status)|set(shacl_status)):
        vals={'procedural':proc_status.get(rid),'sparql':sparql_status.get(rid),'shacl':shacl_status.get(rid)}
        if len(set(vals.values()))>1:
            disagreements.append({'rule_number':rid,**vals})
    return {
      'file_name':path.name,
      'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'counts':counts,
      'ifc_units_mm_per_project_length_unit':mm_per_model_length_unit,
      'ifc_project_unit_assignment_present':project_unit_assignment_present,
      'unit_caveat':('Project UnitsInContext is missing: current procedural rule calculations '
                     'assume the stored dimensions are in millimetres. Physical-unit '
                     'compliance cannot be established from this fixture.'
                     if not project_unit_assignment_present else
                     'IFC project units are declared, but numeric properties are not automatically '
                     'normalized to millimetres by the current extractor; check per-property unit overrides.'),
      'procedural':{'totals':legacy_totals,'summary':legacy_summary},
      'sparql':{'totals':sp_totals,'summary':sp_summary,'rows':sp_rows},
      'shacl':{'totals':sh_totals,'summary':sh_summary,'rows':sh_rows},
      'rdf_triples':len(graph),
      'method_disagreements':disagreements,
      'warnings':['Requires independently reviewed rule applicability','Aggregates are per-rule model results rather than independently checked regulatory ground truth'],
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--models',nargs='+',required=True,help='IFC files')
    ap.add_argument('--out',default='local_ifc_baseline_results.json')
    a=ap.parse_args()
    missing=[lib for lib in ('ifcopenshell','pyshacl','rdflib') if version(lib) is None]
    if missing:
        raise SystemExit('Please install dependencies first: python -m pip install -r requirements.txt (missing: '+', '.join(missing)+')')
    gui=get_gui()
    report={
      'status':'LOCAL_DIAGNOSTIC_NOT_EXPERT_VALIDATED',
      'timestamp_utc':datetime.now(timezone.utc).isoformat(),
      'python':sys.version,
      'platform':platform.platform(),
      'library_versions':{lib:version(lib) for lib in ('ifcopenshell','pyshacl','rdflib')},
      'models':[],
    }
    for src in a.models:
        p=Path(src).resolve()
        if not p.is_file():
            report['models'].append({'file':str(p),'error':'File not found'})
            continue
        print('Checking:',p)
        try:
            report['models'].append(execute_one(gui,p))
        except Exception as exc:
            report['models'].append({'file':str(p),'error':str(exc),
                                     'traceback':traceback.format_exc(limit=8)})
    Path(a.out).write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    print('Saved:',Path(a.out).resolve())
    print('Models without errors:',sum('error' not in x for x in report['models']))

if __name__=='__main__':
    main()
