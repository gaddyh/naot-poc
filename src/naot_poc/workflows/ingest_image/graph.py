from langgraph.graph import START, END, StateGraph

from .state import IngestImageState
from .nodes import IngestImageNodes


def build_ingest_image_graph(scanner):
    nodes = IngestImageNodes(scanner)

    graph = StateGraph(IngestImageState)

    graph.add_node("scan", nodes.scan)

    graph.add_edge(START, "scan")
    graph.add_edge("scan", END)

    return graph.compile()