from langgraph.graph import END, START, StateGraph

from .nodes import AuditedIngestImageNodes, IngestImageNodes
from .state import IngestImageState


def build_ingest_image_graph(scanner):
    nodes = IngestImageNodes(scanner)

    graph = StateGraph(IngestImageState)

    graph.add_node(
        "scan",
        nodes.scan,
        metadata={
            "stage": "perception",
            "component": "workflow",
        },
    )

    graph.add_edge(START, "scan")
    graph.add_edge("scan", END)

    return graph.compile()


def _route_after_reconcile(state: IngestImageState) -> str:
    """Conditional-edge router: recover missing regions, else finalize."""
    return "targeted_recovery" if state.get("missing_regions") else "merge"


def build_audited_ingest_image_graph(scanner, auditor, recovery_scanner=None):
    nodes = AuditedIngestImageNodes(scanner, auditor, recovery_scanner)

    graph = StateGraph(IngestImageState)
    graph.add_node(
        "parallel_scan_audit",
        nodes.parallel_scan_audit,
        metadata={
            "stage": "perception",
            "component": "workflow",
        },
    )
    graph.add_node(
        "reconcile",
        nodes.reconcile,
        metadata={
            "stage": "reconciliation",
            "component": "workflow",
        },
    )
    graph.add_node(
        "targeted_recovery",
        nodes.targeted_recovery,
        metadata={
            "stage": "recovery",
            "component": "workflow",
        },
    )
    graph.add_node(
        "merge",
        nodes.merge,
        metadata={
            "stage": "finalization",
            "component": "workflow",
        },
    )

    graph.add_edge(START, "parallel_scan_audit")
    graph.add_edge("parallel_scan_audit", "reconcile")
    graph.add_conditional_edges(
        "reconcile",
        _route_after_reconcile,
        {"targeted_recovery": "targeted_recovery", "merge": "merge"},
    )
    graph.add_edge("targeted_recovery", "merge")
    graph.add_edge("merge", END)

    return graph.compile()
