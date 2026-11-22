"""Contract tests for the offline material graph evaluator.

The suite is intentionally strict about deterministic ordering, call counts,
cache isolation, and the exact sRGB transfer function.
"""

import math

import pytest

import matgraph.evaluate as evaluator
from matgraph.evaluate import (
    color_space_convert,
    dead_code,
    evaluate,
    eval_node,
    graph_batch,
    memoize_key,
    toposort,
    validate_graph,
)


def _single_constant_graph(node_id, value):
    return {
        "nodes": {
            node_id: {
                "id": node_id,
                "type": "constant",
                "params": {"value": value},
            }
        },
        "edges": [],
        "outputs": [node_id],
    }


def _diamond_graph():
    return {
        "nodes": {
            "out": {"id": "out", "type": "math", "params": {"op": "add"}},
            "right": {"id": "right", "type": "math", "params": {"op": "add"}},
            "left": {"id": "left", "type": "math", "params": {"op": "mul"}},
            "offset": {
                "id": "offset",
                "type": "constant",
                "params": {"value": 0.125},
            },
            "scale": {
                "id": "scale",
                "type": "constant",
                "params": {"value": 2.0},
            },
            "base": {
                "id": "base",
                "type": "constant",
                "params": {"value": 0.25},
            },
        },
        "edges": [
            ("base", "left", "a"),
            ("scale", "left", "b"),
            ("base", "right", "a"),
            ("offset", "right", "b"),
            ("left", "out", "a"),
            ("right", "out", "b"),
        ],
        "outputs": ["out"],
    }


def _install_eval_spy(monkeypatch, calls):
    real_eval_node = evaluator.eval_node

    def recording_eval_node(node, inputs, params):
        calls.append(node["id"])
        return real_eval_node(node, inputs, params)

    monkeypatch.setattr(evaluator, "eval_node", recording_eval_node)


def test_toposort_respects_dependencies_not_declaration_order():
    nodes = {
        "result": {"type": "math", "params": {"op": "sqrt"}},
        "mid": {"type": "math", "params": {"op": "sqrt"}},
        "source": {"type": "constant", "params": {"value": 1.0}},
    }
    edges = [("source", "mid"), ("mid", "result")]

    assert toposort(nodes, edges) == ["source", "mid", "result"]


def test_toposort_cycle_error_contains_cycle_path():
    nodes = {
        "alpha_node": {"type": "math", "params": {"op": "sqrt"}},
        "beta_node": {"type": "math", "params": {"op": "sqrt"}},
        "gamma_node": {"type": "math", "params": {"op": "sqrt"}},
    }
    edges = [
        ("alpha_node", "beta_node"),
        ("beta_node", "gamma_node"),
        ("gamma_node", "alpha_node"),
    ]

    with pytest.raises(ValueError) as excinfo:
        toposort(nodes, edges)

    message = str(excinfo.value)
    for node_id in nodes:
        assert node_id in message
    assert "->" in message


def test_mix_node_uses_lerp_for_color():
    node = {"type": "mix", "params": {}}
    inputs = {
        "a": (0.0, 0.2, 0.8),
        "b": (1.0, 0.6, 0.2),
        "factor": 0.25,
    }

    result = eval_node(node, inputs, {})

    assert result == pytest.approx((0.25, 0.30, 0.65), abs=1e-9)


def test_math_sqrt_rejects_negative_instead_of_nan():
    node = {"type": "math", "params": {"op": "sqrt"}}

    with pytest.raises(ValueError):
        eval_node(node, {"a": -0.25}, {})


def test_evaluate_caches_shared_intermediate(monkeypatch):
    graph = _diamond_graph()
    calls = []
    _install_eval_spy(monkeypatch, calls)

    result = evaluate(graph, ["out"])

    assert result["out"] == pytest.approx(0.875, abs=1e-9)
    assert len(calls) == len(graph["nodes"])
    assert calls.count("base") == 1
    assert sorted(calls) == sorted(graph["nodes"])

    positions = {node_id: index for index, node_id in enumerate(calls)}
    assert positions["base"] < positions["left"]
    assert positions["base"] < positions["right"]
    assert positions["scale"] < positions["left"]
    assert positions["offset"] < positions["right"]
    assert positions["left"] < positions["out"]
    assert positions["right"] < positions["out"]


def test_srgb_to_linear_uses_iec_curve_not_gamma_22():
    actual = color_space_convert(0.5, "srgb", "linear")
    expected = ((0.5 + 0.055) / 1.055) ** 2.4

    assert actual == pytest.approx(expected, abs=1e-9)
    assert abs(actual - (0.5**2.2)) > 1e-4


def test_srgb_linear_conversion_is_inverse():
    linear = ((0.18 + 0.055) / 1.055) ** 2.4

    back = color_space_convert(linear, "linear", "srgb")
    same_formula = color_space_convert(linear, "srgb", "linear")

    assert back == pytest.approx(0.18, abs=1e-9)
    assert abs(back - same_formula) > 1e-6


def test_dead_code_includes_indirectly_unused_nodes():
    graph = {
        "nodes": {
            "dead_end": {"id": "dead_end", "type": "math", "params": {"op": "sqrt"}},
            "dead_mid": {"id": "dead_mid", "type": "math", "params": {"op": "sqrt"}},
            "dead_source": {
                "id": "dead_source",
                "type": "constant",
                "params": {"value": 0.25},
            },
            "out": {"id": "out", "type": "math", "params": {"op": "sqrt"}},
            "used": {
                "id": "used",
                "type": "constant",
                "params": {"value": 0.25},
            },
        },
        "edges": [
            ("used", "out", "a"),
            ("dead_source", "dead_mid", "a"),
            ("dead_mid", "dead_end", "a"),
        ],
        "outputs": ["out"],
    }

    assert dead_code(graph) == ["dead_end", "dead_mid", "dead_source"]


def test_memoize_key_includes_node_params_and_inputs():
    node_a = {
        "id": "tex",
        "type": "texture",
        "params": {"texel": (0.2, 0.4, 0.6), "space": "linear"},
    }
    node_same = {
        "id": "tex",
        "type": "texture",
        "params": {"texel": (0.2, 0.4, 0.6), "space": "linear"},
    }
    node_changed = {
        "id": "tex",
        "type": "texture",
        "params": {"texel": (0.3, 0.4, 0.6), "space": "linear"},
    }
    inputs = {"uv": (0.25, 0.50, 0.0)}
    inputs_changed = {"uv": (0.25, 0.75, 0.0)}

    key = memoize_key(node_a, inputs)

    assert isinstance(key, str)
    assert key == memoize_key(node_same, inputs)
    assert key != memoize_key(node_changed, inputs)
    assert key != memoize_key(node_a, inputs_changed)


def test_graph_batch_isolates_caches_between_graphs():
    graph_a = _single_constant_graph("result", 0.2)
    graph_b = _single_constant_graph("result", 0.8)
    expected_a = evaluate(graph_a, ["result"])
    expected_b = evaluate(graph_b, ["result"])

    batch = graph_batch(
        {"first": graph_a, "second": graph_b},
        {"outputs": ["result"]},
    )

    assert list(batch) == ["first", "second"]
    assert batch["first"]["result"] == pytest.approx(0.2, abs=1e-9)
    assert batch["second"]["result"] == pytest.approx(0.8, abs=1e-9)
    assert batch["first"] == expected_a
    assert batch["second"] == expected_b


def test_constant_node_returns_its_value():
    value = 0.123456789
    node = {"type": "constant", "params": {"value": value}}

    assert eval_node(node, {}, {}) == pytest.approx(value, abs=1e-9)


def test_srgb_roundtrip_within_tolerance():
    for value in (0.0, 0.02, 0.0404, 0.0405, 0.18, 0.5, 1.0):
        linear = color_space_convert(value, "srgb", "linear")
        assert math.isfinite(linear)
        back = color_space_convert(linear, "linear", "srgb")
        assert math.isfinite(back)
        assert abs(back - value) < 1e-9


def test_linear_graph_evaluates_each_node_once(monkeypatch):
    graph = {
        "nodes": {
            "result": {"id": "result", "type": "math", "params": {"op": "sqrt"}},
            "step2": {"id": "step2", "type": "math", "params": {"op": "sqrt"}},
            "step1": {"id": "step1", "type": "math", "params": {"op": "sqrt"}},
            "base": {
                "id": "base",
                "type": "constant",
                "params": {"value": 0.0625},
            },
        },
        "edges": [
            ("base", "step1", "a"),
            ("step1", "step2", "a"),
            ("step2", "result", "a"),
        ],
        "outputs": ["result"],
    }
    calls = []
    _install_eval_spy(monkeypatch, calls)

    result = evaluate(graph, ["result"])

    assert result["result"] == pytest.approx(
        math.sqrt(math.sqrt(math.sqrt(0.0625))), abs=1e-9
    )
    assert calls == ["base", "step1", "step2", "result"]
    assert len(calls) == len(graph["nodes"])


def test_cycle_graph_raises_with_cycle_node_in_message():
    graph = {
        "nodes": {
            "ring_c": {"id": "ring_c", "type": "math", "params": {"op": "sqrt"}},
            "ring_b": {"id": "ring_b", "type": "math", "params": {"op": "sqrt"}},
            "ring_a": {"id": "ring_a", "type": "math", "params": {"op": "sqrt"}},
        },
        "edges": [
            ("ring_a", "ring_b", "a"),
            ("ring_b", "ring_c", "a"),
            ("ring_c", "ring_a", "a"),
        ],
        "outputs": ["ring_c"],
    }
    cycle_nodes = ("ring_a", "ring_b", "ring_c")

    issues = validate_graph(graph)
    matching = [
        issue
        for issue in issues
        if "cycle" in issue.lower()
        and all(node_id in issue for node_id in cycle_nodes)
        and "->" in issue
    ]
    assert matching

    with pytest.raises(ValueError) as excinfo:
        evaluate(graph, ["ring_c"])

    message = str(excinfo.value)
    for node_id in cycle_nodes:
        assert node_id in message
    assert "->" in message


def test_two_graphs_match_independent_evaluations():
    graph_one = _single_constant_graph("left_out", 0.125)
    graph_two = _single_constant_graph("right_out", 0.875)
    expected_one = evaluate(graph_one, ["left_out"])
    expected_two = evaluate(graph_two, ["right_out"])

    batch = graph_batch({"one": graph_one, "two": graph_two}, {})

    assert list(batch) == ["one", "two"]
    assert batch["one"] == expected_one
    assert batch["two"] == expected_two
    assert batch["one"]["left_out"] == pytest.approx(0.125, abs=1e-9)
    assert batch["two"]["right_out"] == pytest.approx(0.875, abs=1e-9)
