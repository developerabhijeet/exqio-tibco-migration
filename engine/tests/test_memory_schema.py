import ast
from pathlib import Path

import pytest

from engine.memory import cypher
from engine.memory.graph import MemoryGraph, MemoryUnavailable
from engine.memory.model import EMBEDDED_LABELS, NODE_LABELS, REL_TYPES


async def _names(graph, statement: str) -> set[str]:
    async with graph._driver.session(database=graph.database) as session:
        result = await session.run(statement)
        return {r["name"] async for r in result}


async def index_names(graph) -> set[str]:
    return await _names(graph, cypher.SHOW_INDEX_NAMES)


async def constraint_names(graph) -> set[str]:
    return await _names(graph, cypher.SHOW_CONSTRAINT_NAMES)


async def test_ensure_schema_is_idempotent(memory_graph):
    await memory_graph.ensure_schema()
    await memory_graph.ensure_schema()
    names = await index_names(memory_graph)
    assert {"mem_uid", "mem_scope", "mem_from", "mem_to", "mem_target", "rev_n", "mem_text",
            "vec_chunk", "vec_memory", "vec_process", "vec_activity", "vec_class",
            "vec_method"} <= names
    assert {f"rel_{t.lower()}_{k}" for t in REL_TYPES for k in ("from", "to", "uid")} <= names
    assert {"mem_vid", "rev_rid", "rev_ikey", "meta_key"} <= await constraint_names(memory_graph)


async def test_reset_refuses_a_non_test_uri(memory_graph, monkeypatch):
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://elsewhere:7687")
    with pytest.raises(ValueError, match="not NEO4J_TEST_URI"):
        await memory_graph.reset()


async def test_ping_on_closed_port_raises_memory_unavailable():
    g = MemoryGraph.connect("bolt://127.0.0.1:1", "neo4j", "x")
    with pytest.raises(MemoryUnavailable, match="ServiceUnavailable"):
        await g.ping()
    await g.close()


def test_closed_sets_have_the_specified_sizes():
    assert len(NODE_LABELS) == 24 and len(set(NODE_LABELS)) == 24
    assert len(REL_TYPES) == 12 and len(set(REL_TYPES)) == 12
    assert set(EMBEDDED_LABELS) <= set(NODE_LABELS)
    assert len(cypher.SCHEMA) == 4 + 6 + 3 * len(REL_TYPES) + len(EMBEDDED_LABELS) + 1


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _violations(tree: ast.Module) -> list[str]:
    """f-strings, str.format and % formatting of strings outside the private _render_* functions."""
    found: list[str] = []

    def visit(node: ast.AST, in_render: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            in_render = in_render or node.name.startswith("_render_")
        if not in_render:
            if isinstance(node, ast.JoinedStr):
                found.append(f"f-string at line {node.lineno}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "format"):
                found.append(f".format at line {node.lineno}")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and (
                    _is_str(node.left) or isinstance(node.left, ast.JoinedStr)):
                found.append(f"% formatting at line {node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, in_render)

    visit(tree, False)
    return found


def test_cypher_module_builds_strings_only_in_render_functions():
    tree = ast.parse(Path(cypher.__file__).read_text())
    assert _violations(tree) == []


def test_the_violation_scan_catches_each_formatting_form():
    bad = ast.parse('def f(x):\n    a = f"MATCH {x}"\n    b = "MATCH {}".format(x)\n'
                    '    c = "MATCH %s" % x\n')
    assert len(_violations(bad)) == 3
    ok = ast.parse('def _render_a(x):\n    return f"MATCH {x}"\n')
    assert _violations(ok) == []
