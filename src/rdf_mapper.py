"""
RDF Mapper: Converts extracted component data and custom ontology relations
into an rdflib Graph that conforms to stair_ontology.ttl.
"""

import os
from pathlib import Path
from rdflib import Graph, URIRef, Literal, BNode
from rdflib.namespace import RDF, RDFS, XSD, OWL
from rdflib import Namespace

# Define the ontology namespace
STAIR = Namespace("http://example.com/stairOntology#")

# Mapping from UI component_type strings to ontology class URIs
# Special case: "IfcRailing" -> decided later based on classification
CLASS_MAP = {
    "IfcBuildingStorey": STAIR.BuildingStorey,
    "IfcStair": STAIR.Stair,
    "IfcStairFlight": STAIR.StairFlight,
    "IfcRailing": STAIR.Railing,          # base class, may be refined
    "IfcFireSuppressionTerminal": STAIR.FireSuppressionTerminal,
    "IfcFlowSegment": STAIR.FlowSegment,
    "IfcFlowController": STAIR.FlowController,
    "IfcOutlet": STAIR.Outlet,
}

# Map data property keys (from component_data) to ontology properties
DATA_PROP_MAP = {
    # Stair flight
    "h": STAIR.hasRiserHeight,
    "g": STAIR.hasTreadLength,
    "r": STAIR.hasNosingLength,
    "number of risers": STAIR.hasNumberOfRisers,
    "climb height": STAIR.hasClimbHeight,
    # Railing generic and role-specific height datums
    "height h": STAIR.hasRailingHeight,
    "guardrail height": STAIR.hasGuardrailHeight,
    "stair handrail height": STAIR.hasHandrailHeight,
    "landing handrail height": STAIR.hasLandingHandrailHeight,
    # MEP
    "diameter": STAIR.hasDiameter,
    "width": STAIR.hasWidth,
    "depth": STAIR.hasDepth,
    "panel_height": STAIR.hasHeight,
    "install_height": STAIR.hasInstallationHeight,
    "fire_hose_height": STAIR.hasFireHoseHeight,
    "cabinet_depth": STAIR.hasCabinetDepth,
    "outlet_height": STAIR.hasOutletHeight,
}

def get_individual_uri(comp_type, entity_id, railing_classification=None):
    """
    Generate a URI for an individual.
    For railings, if classification is 'guardrail' or 'handrail', use that class.
    Otherwise fallback to generic Railing.
    """
    if comp_type == "IfcRailing" and railing_classification:
        if railing_classification == "guardrail":
            class_name = "Guardrail"
        elif railing_classification == "handrail":
            class_name = "Handrail"
        elif railing_classification == "landing_handrail":
            class_name = "LandingHandrail"
        else:
            class_name = "Railing"
    else:
        # Map component_type to ontology class name (remove "Ifc" prefix)
        class_name = comp_type.replace("Ifc", "")
    # Sanitize: remove spaces, keep alphanumeric
    class_name = ''.join(c for c in class_name if c.isalnum())
    return STAIR[f"{class_name}_{entity_id}"]

def extract_railing_classification(comp_data):
    """
    Determine railing classification from component metadata.
    Returns 'guardrail', 'handrail', or None.
    """
    metadata = str(comp_data.get("metadata", "") or "").lower()
    name = str(comp_data.get("name", "") or "").lower()

    # A dedicated landing datum takes precedence over a legacy generic railing
    # height or a coarse class label from the IFC export.
    if comp_data.get("landing handrail height") is not None:
        return "landing_handrail"
    if "class=landing_handrail" in metadata or "class=landing handrail" in metadata:
        return "landing_handrail"
    if "landing handrail" in name or "landing handrail" in metadata:
        return "landing_handrail"
    if "class=guardrail" in metadata or "guard" in name:
        return "guardrail"
    if "class=handrail" in metadata or "hand" in name:
        return "handrail"
    return None

def populate_rdf_graph(component_data, ontology_relations=None, ontology_ttl_path=None):
    """
    Populates an rdflib Graph with individuals from component_data and relations.

    Args:
        component_data: dict keyed by (component_type, entity_id) with attributes.
        ontology_relations: list of (subject_key, predicate, object_key)
                            from the custom Ontology class (predicate is "isPartOf").
        ontology_ttl_path: path to the OWL ontology TTL file.

    Returns:
        rdflib.Graph containing both T-Box (from file) and A-Box (generated).
    """
    # Create graph and load the ontology T-Box
    if ontology_ttl_path is None:
        ontology_ttl_path = str(Path(__file__).resolve().with_name('stair_ontology.ttl'))
    g = Graph()
    if os.path.exists(ontology_ttl_path):
        g.parse(ontology_ttl_path, format="turtle")
    else:
        print(f"Warning: ontology file '{ontology_ttl_path}' not found. Creating empty graph.")
        # Still need to bind namespaces
        g.bind("stair", STAIR)
        g.bind("rdf", RDF)
        g.bind("rdfs", RDFS)
        g.bind("xsd", XSD)

    # Track created individuals to avoid duplicates (though each key appears once)
    individuals = {}

    # First pass: create all individuals with their types
    for (comp_type, entity_id), data in component_data.items():
        # Determine classification for railings
        railing_class = None
        if comp_type == "IfcRailing":
            railing_class = extract_railing_classification(data)
        uri = get_individual_uri(comp_type, entity_id, railing_class)
        individuals[(comp_type, entity_id)] = uri

        # Add rdf:type triple
        if comp_type == "IfcRailing" and railing_class:
            if railing_class == "guardrail":
                class_uri = STAIR.Guardrail
            elif railing_class == "handrail":
                class_uri = STAIR.Handrail
            elif railing_class == "landing_handrail":
                class_uri = STAIR.LandingHandrail
            else:
                class_uri = STAIR.Railing
        else:
            class_uri = CLASS_MAP.get(comp_type, STAIR.Thing)
        g.add((uri, RDF.type, class_uri))
        # A landing handrail is also a handrail. Assert both types explicitly
        # because this graph is queried without requiring OWL subclass inference.
        if comp_type == "IfcRailing" and railing_class == "landing_handrail":
            g.add((uri, RDF.type, STAIR.Handrail))

        # Add name and metadata as annotations (optional)
        name = data.get("name")
        if name and name != "-":
            g.add((uri, RDFS.label, Literal(name, datatype=XSD.string)))
        # Store metadata as comment
        metadata = data.get("metadata")
        if metadata:
            g.add((uri, RDFS.comment, Literal(metadata, datatype=XSD.string)))

        # Add data properties
        for key, prop_uri in DATA_PROP_MAP.items():
            value = data.get(key)
            if value is not None:
                # Convert to appropriate XSD type
                if key == "number of risers":
                    # ensure integer
                    lit = Literal(int(round(float(value))), datatype=XSD.integer)
                else:
                    lit = Literal(float(value), datatype=XSD.float)
                g.add((uri, prop_uri, lit))

        # Backward-compatible mapping for legacy IFC exports that expose only
        # one generic "Height" value. Never reinterpret that generic value as
        # a landing handrail height: Rule 13 requires its explicit datum.
        if comp_type == "IfcRailing" and data.get("height h") is not None:
            height_val = data["height h"]
            if railing_class == "guardrail" and data.get("guardrail height") is None:
                g.add((uri, STAIR.hasGuardrailHeight, Literal(float(height_val), datatype=XSD.float)))
            elif railing_class == "handrail" and data.get("stair handrail height") is None:
                g.add((uri, STAIR.hasHandrailHeight, Literal(float(height_val), datatype=XSD.float)))

    # Second pass: add object properties (isPartOf) from ontology_relations
    if ontology_relations:
        for (subj_type, subj_id), pred, (obj_type, obj_id) in ontology_relations:
            if pred != "isPartOf":
                continue
            subj_uri = individuals.get((subj_type, subj_id))
            obj_uri = individuals.get((obj_type, obj_id))
            if subj_uri and obj_uri:
                g.add((subj_uri, STAIR.isPartOf, obj_uri))
            else:
                print(f"Warning: Missing individual for relation ({subj_type},{subj_id}) -> ({obj_type},{obj_id})")

    # Materialise stair->flight reverse links required by Rule 10.
    # Do NOT infer this as the inverse of every isPartOf assertion:
    # stair->storey containment must not create a hasFlight statement.
    for (child, _, parent) in list(g.triples((None, STAIR.isPartOf, None))):
        if ((child, RDF.type, STAIR.StairFlight) in g and
                (parent, RDF.type, STAIR.Stair) in g):
            g.add((parent, STAIR.hasFlight, child))

    # Make storey context available without requiring an OWL reasoner.
    # Follow explicit child -> parent paths, with cycle protection.
    for child in individuals.values():
        stack = list(g.objects(child, STAIR.isPartOf))
        visited = set()
        while stack:
            parent = stack.pop()
            if parent in visited:
                continue
            visited.add(parent)
            if (parent, RDF.type, STAIR.BuildingStorey) in g:
                g.add((child, STAIR.isContainedInStorey, parent))
            stack.extend(g.objects(parent, STAIR.isPartOf))

    return g

def save_graph(graph, path):
    """Save RDF graph to a Turtle file."""
    graph.serialize(destination=path, format="turtle")
    print(f"Graph saved to {path}")

# Example usage (to be integrated into UI later):
if __name__ == "__main__":
    # This is just a test stub; actual usage will come from the main UI.
    pass