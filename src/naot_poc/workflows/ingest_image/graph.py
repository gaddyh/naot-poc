from langgraph.graph import END, START, StateGraph

from .nodes import IngestImageNodes
from .state import IngestImageState


def build_ingest_image_graph(scanner):
    nodes = IngestImageNodes(scanner)

    graph = StateGraph(IngestImageState)

    graph.add_node("scan", nodes.scan)

    graph.add_edge(START, "scan")
    graph.add_edge("scan", END)

    return graph.compile()
