"""Pre-evaluation information and applicability screening for JCEM prototype.

These checks classify data completeness and structural scope, NOT legal applicability.
A verified project-specific ISO/INSO/OSHA regulatory profile must still be supplied.
The existing GUI continues to display three states; `status_detail` retains a
fourth, distinct state instead of interpreting missing data as compliance.
"""
import math
from rdflib import Namespace, Literal
from rdflib.namespace import RDF, XSD

STAIR = Namespace('http://example.com/stairOntology#')

REQUIRED = {
    1: ('hasRiserHeight', 'hasTreadLength'),
    2: ('hasTreadLength',),
    3: ('hasRiserHeight', 'hasTreadLength'),
    4: ('hasNosingLength',),
    5: ('hasRiserHeight', 'hasTreadLength'),
    6: ('hasNumberOfRisers',),
    7: ('hasNumberOfRisers',),
    8: ('hasNumberOfRisers',),
    9: ('hasRiserHeight', 'hasNumberOfRisers'),
    10: ('hasRiserHeight', 'hasNumberOfRisers'),
    11: ('hasGuardrailHeight',),
    12: ('hasHandrailHeight',),
    13: ('hasGuardrailHeight',),
    14: ('hasHandrailHeight',),
}


NUMERIC_DATATYPES = {XSD.integer, XSD.int, XSD.long, XSD.decimal, XSD.float,
                     XSD.double, XSD.nonNegativeInteger, XSD.positiveInteger}


def has_numeric_value(graph, node, property_name):
    """Treat numeric zero as data, but reject missing, nonnumeric, and NaN literals."""
    for value in graph.objects(node, STAIR[property_name]):
        if not isinstance(value, Literal) or value.datatype not in NUMERIC_DATATYPES:
            continue
        try:
            if math.isfinite(float(value)):
                return True
        except (ValueError, TypeError, OverflowError):
            pass
    return False


def classify_rule_contexts(graph, number, target_class):
    """Return eligible, incomplete, and not-applicable nodes for one rule.

    Rules 9 and 10 use explicit relationships. Others use formula-level targeting
    only; their conditional regulatory applicability remains unverified.
    """
    nodes = set(graph.subjects(RDF.type, STAIR[target_class]))
    eligible, missing, not_applicable, unknown_context = set(), set(), set(), set()
    for node in nodes:
        if number in (9, 10):
            parents = {p for p in graph.objects(node, STAIR.isPartOf)
                       if (p, RDF.type, STAIR.Stair) in graph}
            if not parents:
                missing.add(node)  # Cannot establish single-vs-multi-flight context
                unknown_context.add(node)
                continue
            sibling_counts = [len(set(graph.subjects(STAIR.isPartOf, p))
                                  & set(graph.subjects(RDF.type, STAIR.StairFlight)))
                              for p in parents]
            if (number == 9 and not any(c == 1 for c in sibling_counts)) or (number == 10 and not any(c > 1 for c in sibling_counts)):
                not_applicable.add(node)
                continue
        if not all(has_numeric_value(graph, node, prop) for prop in REQUIRED[number]):
            missing.add(node)
        elif number == 3 and any(float(v) <= 0 for v in graph.objects(node, STAIR.hasTreadLength)):
            # atan(h/g) cannot be evaluated for zero or negative going.
            missing.add(node)
        else:
            eligible.add(node)
    return {
        'eligible': eligible, 'missing': missing,
        'not_applicable': not_applicable,
        'unknown_context': unknown_context,
        'all_targets': nodes,
    }


def classify_outcome(precheck, violating_nodes):
    """Four-state detailed result, plus backward-compatible three-state label."""
    failures = precheck['eligible'].intersection(set(violating_nodes))
    if failures:
        detailed = 'FAIL'
    elif precheck['missing']:
        detailed = 'INSUFFICIENT_INFORMATION'
    elif precheck['eligible']:
        detailed = 'PASS'
    else:
        detailed = 'NOT_APPLICABLE'
    status = detailed if detailed in ('PASS', 'FAIL') else 'N/A'
    return status, detailed, failures


def build_rule_results(number, rule, precheck, violated):
    status, detail, failed = classify_outcome(precheck, violated)
    reason = {
        'PASS': 'All evaluated targets satisfy the formalised formula',
        'FAIL': f'{len(failed)} target(s) violate the formalised formula',
        'INSUFFICIENT_INFORMATION': f'{len(precheck["missing"])} target(s) have missing inputs or unresolved single/multi-flight context',
        'NOT_APPLICABLE': 'No matching target in the evaluated structural context',
    }[detail]
    row = {
        'rule_number': number,
        'constraint_id': rule['id'],
        'rule_text': rule['text'],
        'PASS': int(status == 'PASS'),
        'FAIL': int(status == 'FAIL'),
        'N/A': int(status == 'N/A'),
        'total': 1,
        'status_detail': detail,
        'reason': reason,
        'evaluated_target_count': len(precheck['eligible']),
        'missing_target_count': len(precheck['missing']),
        'failed_target_count': len(failed),
        'regulatory_applicability_verified': False,
    }
    return row, failed


def build_display_rows(number, rule, precheck, failed):
    """Represent failures and incomplete nodes without mislabeling either PASS."""
    results = []
    for status, nodes in (('FAIL', failed), ('N/A', precheck['missing'])):
        for node in sorted(nodes, key=str):
            token = str(node).rsplit('#', 1)[-1]
            results.append({
                'component_type': rule['target'],
                'item_index': 0,
                'entity_id': token.rsplit('_', 1)[-1],
                'name': token,
                'metadata': '',
                'rule_number': number,
                'constraint_id': rule['id'],
                'rule_text': rule['text'],
                'result': status,
                'status_detail': 'FAIL' if status == 'FAIL' else 'INSUFFICIENT_INFORMATION',
            })
    return results
