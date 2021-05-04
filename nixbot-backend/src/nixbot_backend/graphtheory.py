# https://github.com/networkx/networkx/blob/777e57a5f08736a9e2b5aa6c87ff38cb8729c926/networkx/algorithms/dag.py
from typing import Dict, List, Tuple

import networkx as nx


def dag_longest_paths(
    G: nx.DiGraph,
    weight: str = "weight",
    default_weight: float = 1,
    topo_order: List[str] = None,
):
    """Returns the all longest paths in a directed acyclic graph (DAG).

    If `G` has edges with `weight` attribute the edge data are used as
    weight values.

    Parameters
    ----------
    G : NetworkX DiGraph
        A directed acyclic graph (DAG)

    weight : str, optional
        Edge data key to use for weight

    default_weight : int, optional
        The weight of edges that do not have a weight attribute

    topo_order: list or tuple, optional
        A topological order for G (if None, the function will compute one)

    Returns
    -------
    list of lists
        List of longest paths

    Raises
    ------
    NetworkXNotImplemented
        If `G` is not directed
    """
    if not G:
        return []

    if topo_order is None:
        topo_order = nx.topological_sort(G)

    dist: Dict[str, Tuple[int, str]] = {}  # stores {v : (length, u)}
    for v in topo_order:
        us = [
            (dist[u][0] + data.get(weight, default_weight), u)
            for u, data in G.pred[v].items()
        ]

        # Use the best predecessor if there is one and its distance is
        # non-negative, otherwise terminate.
        maxu = max(us, key=lambda x: x[0]) if us else (0, v)
        dist[v] = maxu if maxu[0] >= 0 else (0, v)

    paths = []
    for v in _all_maxes(dist, key=lambda x: dist[x][0]):
        u = None
        path = []
        while u != v:
            path.append(v)
            u = v
            v = dist[v][1]

        path.reverse()
        paths.append(path)

    return paths


def _all_maxes(iterable, key):
    results = []
    max_value = None

    for item in iterable:
        value = key(item)

        if max_value is None or value > max_value:
            max_value = value
            results = [item]
        elif value == max_value:
            results.append(item)

    return results
