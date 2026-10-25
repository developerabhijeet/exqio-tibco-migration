import pytest

from engine.memory.diff import VersionRef, classify
from engine.memory.model import (
    EMBEDDED_LABELS,
    KNOWLEDGE_GROUP,
    NEW_LABELS,
    NODE_LABELS,
    OLD_LABELS,
    MemNode,
    MemRel,
    mask_node,
    mask_rel,
    node_hash,
    rel_hash,
    side_of,
)


def test_node_hash_ignores_prop_order_and_embedding():
    a = MemNode(label="Process", uid="process:p1", name="Get", props={"x": 1, "y": 2})
    b = MemNode(label="Process", uid="process:p1", name="Get", props={"y": 2, "x": 1},
                embedding=[0.1] * 384)
    assert node_hash(a) == node_hash(b)


def test_node_hash_sees_text_and_props():
    a = MemNode(label="Process", uid="process:p1", name="Get", text="t", props={"x": 1})
    assert node_hash(a) != node_hash(a.model_copy(update={"text": "u"}))
    assert node_hash(a) != node_hash(a.model_copy(update={"props": {"x": 2}}))


def test_rel_hash_covers_endpoints_and_uid_names_them():
    r = MemRel(type="CALLS", src="process:a", dst="process:b")
    assert r.uid == "CALLS:process:a->process:b"
    assert rel_hash(r) != rel_hash(r.model_copy(update={"dst": "process:c"}))


def test_mask_node_masks_text_and_props_but_not_identity():
    n, refused = mask_node(MemNode(label="Chunk", uid="chunk:p1:summary", name="s",
                                   text="customer SSN 123-45-6789",
                                   props={"kind": "summary", "note": "123-45-6789"}))
    assert "123-45-6789" not in n.text and "123-45-6789" not in n.props["note"]
    assert n.uid == "chunk:p1:summary" and n.props["kind"] == "summary" and refused is False


def test_mask_node_refuses_raw_tibco_xml():
    n, refused = mask_node(MemNode(label="Chunk", uid="c", name="c", text="<pd:activity name='x'/>"))
    assert refused is True and n.text == ""


def test_mask_rel_masks_props_and_keeps_endpoints():
    r = mask_rel(MemRel(type="ABOUT", src="chunk:x", dst="process:y",
                        props={"note": "ssn 123-45-6789", "kind": "k"}))
    assert "123-45-6789" not in r.props["note"] and r.props["kind"] == "k"
    assert (r.src, r.dst) == ("chunk:x", "process:y")


def test_label_groups_cover_every_label_and_side_of():
    assert set(OLD_LABELS) | set(NEW_LABELS) | set(KNOWLEDGE_GROUP) == set(NODE_LABELS)
    assert set(EMBEDDED_LABELS) <= set(NODE_LABELS)
    assert side_of("Process", {}) == "old" and side_of("Method", {}) == "new"
    assert side_of("Chunk", {}) == "knowledge"
    assert side_of("Endpoint", {"side": "new"}) == "new"
    with pytest.raises(ValueError):
        side_of("Endpoint", {})


def test_classify_drops_reattachments_and_reports_changed_keys():
    o = [VersionRef(uid="r1", kind="CALLS", name="", content_hash="h1", props={}),
         VersionRef(uid="n1", kind="Process", name="A", content_hash="h3", props={"band": "high"})]
    c = [VersionRef(uid="r1", kind="CALLS", name="", content_hash="h1", props={}),
         VersionRef(uid="n1", kind="Process", name="A", content_hash="h2", props={"band": "low"}),
         VersionRef(uid="n2", kind="Process", name="B", content_hash="h9", props={})]
    d = classify(o, c)
    assert [x.uid for x in d.added] == [] and [x.uid for x in d.removed] == ["n2"]
    assert [(x.uid, x.changed_keys) for x in d.changed] == [("n1", ["band"])]


def test_classify_reports_opened_only_as_added():
    d = classify([VersionRef(uid="n3", kind="Process", name="C", content_hash="h", props={})], [])
    assert [x.uid for x in d.added] == ["n3"] and d.changed == [] and d.removed == []
