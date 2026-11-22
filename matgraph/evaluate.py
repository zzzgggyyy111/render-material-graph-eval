"""Deterministic evaluation of offline render-material node graphs.

Graph schema
------------
``graph`` is a mapping with:

* ``"nodes"``: ``dict[str, node]``.  Iteration order is declaration order.
* ``"edges"``: iterable of ``(src_id, dst_id, port)``.  An edge means the
  source must be evaluated before the destination; ``port`` names the input
  socket on the destination node.
* ``"outputs"``: optional iterable of output node IDs.  Functions that need
  outputs use this key when no explicit outputs argument exists.

A node is a mapping with a ``"type"`` and optional ``"params"`` mapping.  The
supported types are ``"constant"``, ``"texture"``, ``"mix"`` and ``"math"``.

Value model
-----------
A value is either a finite scalar number or a color represented by a
three-element sequence of finite numbers.  Color operations are componentwise
and color results are returned as three-element tuples.  Scalar operations
return floats.

Color spaces
------------
Only ``"srgb"`` (gamma-encoded IEC 61966-2-1 sRGB) and ``"linear"`` (linear
light) are supported.  Conversion must use the exact piecewise sRGB transfer
function, never the gamma-2.2 approximation, and must be reversible.

Tolerance
---------
Every numeric contract in this module uses an absolute tolerance of ``1e-9``
unless a function says otherwise.  In particular, an sRGB -> linear -> sRGB
round trip must differ from the input by strictly less than ``1e-9``.

All functions are offline and deterministic: they must not use randomness,
I/O, global mutable caches, or external packages.
"""


def toposort(nodes, edges) -> list[str]:
    """Return node IDs in stable dependency order.

    Parameters
    ----------
    nodes:
        Either a mapping ``id -> node`` (mapping iteration order is the
        declaration order) or an iterable of unique string node IDs.
    edges:
        Iterable of ``(src, dst)`` or ``(src, dst, port)`` tuples.  Only the
        first two entries affect ordering; ``port`` may be omitted.  Every
        edge requires ``src`` to appear before ``dst`` in the result.

    Returns
    -------
    list[str]
        A topological order.  Ties between nodes that are simultaneously
        ready are broken by declaration order, so the result is deterministic.

    Raises
    ------
    ValueError
        If an edge names an unknown node, an ID iterable contains a duplicate,
        or the graph contains a self-loop or a cycle.  A cycle error must
        contain the complete cycle path, for example
        ``"cycle detected: a -> b -> c -> a"``; reporting only one node is not
        sufficient.
    """
    raise NotImplementedError


def eval_node(node, inputs, params) -> object:
    """Evaluate one material node from already evaluated inputs.

    ``node`` is a mapping containing ``"type"`` and optional ``"params"``.
    ``inputs`` maps input port names to values.  ``params`` is an optional
    mapping of per-call parameter overrides: effective params are
    ``node["params"]`` updated by ``params`` (the override wins).  Inputs and
    params supplied by the caller must not be mutated.

    For ``mix`` and ``math``, a required value is read from ``inputs`` first
    and then, if absent, from the effective params.  Unknown input ports are
    ignored.

    Supported node types
    --------------------
    constant
        Returns effective ``params["value"]``.  No inputs.
    texture
        Returns effective ``params["texel"]``, the deterministic offline
        sample that stands in for a texture fetch.  No inputs.
    mix
        Requires ``a`` and ``b`` and a scalar ``factor`` (input or param).
        Returns componentwise ``a + (b - a) * factor``.  It must never be
        implemented as ``a + b``.  ``a`` and ``b`` must both be scalars or both
        be colors; mixing a scalar with a color is a type error.  ``factor``
        is not clamped, so values outside ``[0, 1]`` extrapolate.
    math
        Requires effective ``params["op"]``.  Unary ops: ``"sqrt"``,
        ``"abs"``, ``"neg"``.  Binary ops: ``"add"``, ``"sub"``, ``"mul"``,
        ``"div"``.  ``a`` is required; binary ops also require ``b``.
        Operations are componentwise for colors.  ``sqrt`` of a negative
        component raises ``ValueError`` instead of returning/propagating NaN;
        division by zero raises ``ValueError``.  Results must be finite.

    Returns
    -------
    float or tuple[float, float, float]
        A scalar or a three-element color tuple.

    Raises
    ------
    ValueError
        For an unknown node type or op, a missing required value, a negative
        square root, division by zero, or another non-finite result.
    TypeError
        For a value that is neither a scalar nor a three-element color, or for
        incompatible operand kinds in a componentwise operation.
    """
    raise NotImplementedError


def evaluate(graph, outputs) -> dict:
    """Evaluate selected graph outputs with one evaluation per reachable node.

    Only the requested outputs and their transitive dependencies are
    evaluated.  Each reachable node is passed to :func:`eval_node` exactly
    once per call, even when several outputs share it; results are reused from
    a call-local cache.  The cache is not shared with any later call.

    ``graph`` follows the module schema, with ``nodes`` and three-tuple
    ``edges``.  ``outputs`` is an iterable of output node IDs.  Evaluation must
    respect the dependency edges: every source is evaluated before its
    destination.  Repeated output IDs are collapsed while preserving the first
    occurrence order.

    Returns
    -------
    dict[str, object]
        ``output_id -> value`` for each requested output, in output order.

    Raises
    ------
    ValueError
        If an output is unknown, an edge is malformed or references an unknown
        node, two edges feed the same destination port, an input is missing, or
        the reachable graph contains a cycle.  A cycle message must include the
        complete cycle path (``"a -> b -> a"``), not merely one node.
    """
    raise NotImplementedError


def color_space_convert(value, src, dst) -> object:
    """Convert a scalar or color between sRGB and linear light.

    ``src`` and ``dst`` must be exactly ``"srgb"`` or ``"linear"`` (lowercase).
    A scalar is converted componentwise and returned as a float; a
    three-element color sequence is converted componentwise and returned as a
    three-element tuple.  If the spaces are equal, the value is returned in
    that canonical form without changing it.

    The conversion must use the exact IEC 61966-2-1 piecewise transfer
    functions::

        srgb -> linear:
            c / 12.92                         if c <= 0.04045
            ((c + 0.055) / 1.055) ** 2.4      otherwise

        linear -> srgb:
            12.92 * c                         if c <= 0.0031308
            1.055 * c ** (1 / 2.4) - 0.055    otherwise

    The common gamma-2.2 approximation is incorrect.  The two directions must
    be genuine inverses: for finite values in the usual sRGB range, converting
    sRGB to linear and back must agree with the original within absolute
    tolerance ``1e-9`` (strictly less than ``1e-9`` error).  Values are not
    clamped; extended-range values are converted by the same piecewise rules.

    Raises
    ------
    ValueError
        For an unsupported color-space name, a non-finite input, or a value
        that is neither a scalar nor a three-element color.
    """
    raise NotImplementedError


def dead_code(graph) -> list[str]:
    """Return nodes that do not contribute to any declared graph output.

    A node is live when it is one of ``graph["outputs"]`` or can reach one of
    those outputs by following edges.  Every other node is dead, including
    unused source nodes, their downstream nodes, and unused sink nodes.  The
    check must be transitive; inspecting only the direct output nodes is
    incorrect.

    Returns
    -------
    list[str]
        Dead node IDs in declaration order.  A fully live graph returns an
        empty list.  The graph is not mutated.

    Raises
    ------
    ValueError
        If ``graph["outputs"]`` is missing or contains an unknown node ID.
    """
    raise NotImplementedError


def memoize_key(node, inputs) -> str:
    """Return a deterministic cache key for one node evaluation.

    The key must include the node's identity/type, its complete effective
    params, and the complete ``inputs`` mapping.  It must distinguish equal
    node/input structures from structures that differ only in a parameter or
    in an input value.  Nested mappings must be key-order independent, while
    sequences are order-sensitive.  Equal structures must produce equal keys
    across calls and processes.

    The key is exact: values are not rounded to the ``1e-9`` tolerance.  It
    must not depend on ``id()``, object hashes, memory addresses, or iteration
    order of a Python hash table.

    Returns
    -------
    str
        A stable, human-readable key.
    """
    raise NotImplementedError


def validate_graph(graph) -> list[str]:
    """Return human-readable problems with a graph, or ``[]`` when valid.

    Validation is structural and must not evaluate node math or mutate the
    graph.  It checks at least:

    * that ``graph["outputs"]`` exists and names known nodes;
    * that every edge has known endpoints and a string destination port, and
      that no two edges feed the same destination port;
    * that each node's required input ports are supplied by edges or node
      params (``mix`` needs ``a`` and ``b`` plus a scalar ``factor`` from an
      edge or param; ``math`` needs ``a`` and a ``b`` for binary ops);
    * that node types/ops and constant values are recognized;
    * that there are no cycles, reporting the complete cycle path; and
    * obvious type mismatches, such as a scalar and a color feeding the two
      operands of a componentwise operation.

    Issues are returned in declaration/edge order and are descriptive strings.
    An invalid graph is reported through this list rather than by raising.
    """
    raise NotImplementedError


def graph_batch(graphs, config) -> dict:
    """Evaluate a batch of independent graphs.

    ``graphs`` is a mapping ``batch_name -> graph``.  ``config`` is a mapping
    of batch options: an optional ``"outputs"`` iterable applies the same
    output IDs to every graph; when it is absent, each graph's own
    ``"outputs"`` is used.

    Every graph must be evaluated exactly as if :func:`evaluate` were called
    separately for it.  No node value, memo key, or cache entry may be shared
    between graphs, even when the graphs reuse the same node IDs.  In
    particular, the second graph must not hit the first graph's cached result.
    Batch input order and each output order are preserved.

    Returns
    -------
    dict[str, dict]
        ``batch_name -> {output_id: value}``.

    Raises
    ------
    ValueError
        If a batch name is duplicated, a graph has no outputs and config does
        not provide them, or one of the underlying evaluations fails.
    """
    raise NotImplementedError
