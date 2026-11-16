"""M8 合并纯规则：并查集链路、来源优先级代表元、隔离边界、顺序无关性。

覆盖需求里最容易写错的两条边界：
- 已在确认链路中的成员，不因对新来源的一条 pending 关系被误隔离（已生效下游不能断）；
- 代表元只由来源优先级决定，与关系/指标的输入顺序无关。
"""
from __future__ import annotations

from app import config
from app.modules.m8_sourcing import livegraph, merge


def _metric(source, name):
    return {"source": source, "name": name, "line_no": 1,
            "metrics": [], "fields": [], "group_by": [],
            "depends_declared": [], "aggregators": [], "expr_text": "",
            "where": [], "expr": [], "signature": f"{source}:{name}"}


def _link(ls, ln, rs, rn, state=config.LINK_CONFIRMED):
    return {"left_source": ls, "left_name": ln,
            "right_source": rs, "right_name": rn, "state": state}


def test_union_find_groups_are_order_independent():
    """同一些键与同一些边，换输入顺序，分组成员完全一致。"""
    keys = {"a\x00x", "b\x00x", "c\x00x", "d\x00y"}
    edges = [("a\x00x", "b\x00x"), ("b\x00x", "c\x00x")]
    first = merge.UnionFind(set(keys))
    for a, b in edges:
        first.union(a, b)
    second = merge.UnionFind(set(keys))
    for a, b in reversed(edges):
        second.union(a, b)
    assert first.groups() == second.groups()


def test_canonical_follows_source_priority():
    """代表元取来源优先级最高者；门店/渠道再同名也不能盖过总部。"""
    members = [("bi_channel", "销售额"), ("bi_store", "销售额"),
               ("bi_hq", "销售额")]
    assert merge.canonical_of(members) == ("bi_hq", "销售额")


def test_same_name_metrics_stay_independent_per_source():
    """没有任何等价关系时，同名指标在各来源各自成组，绝不并成一个节点。"""
    metrics = [_metric("bi_hq", "销售额"), _metric("bi_store", "销售额"),
               _metric("bi_channel", "销售额")]
    result = merge.build_groups(metrics, [])
    assert len(result["groups"]) == 3
    assert all(not g["is_merged"] for g in result["groups"])
    assert result["pending_keys"] == []


def test_confirmed_equivalence_merges_into_chain():
    metrics = [_metric("bi_hq", "销售额"), _metric("bi_store", "销售额"),
               _metric("bi_channel", "销售额")]
    links = [_link("bi_hq", "销售额", "bi_store", "销售额"),
             _link("bi_hq", "销售额", "bi_channel", "销售额")]
    result = merge.build_groups(metrics, links)
    merged = [g for g in result["groups"] if g["is_merged"]]
    assert len(merged) == 1
    group = merged[0]
    assert group["canonical_source"] == "bi_hq"
    states = {m["source"]: m["state"] for m in group["members"]}
    assert states["bi_hq"] == config.MEMBER_CANONICAL
    assert states["bi_store"] == config.MEMBER_LINKED
    assert states["bi_channel"] == config.MEMBER_LINKED


def test_pending_link_isolates_only_unconfirmed_member():
    """总部已与门店确认，对三方只是 pending：总部/门店留现网，仅三方隔离。"""
    metrics = [_metric("bi_hq", "销售额"), _metric("bi_store", "销售额"),
               _metric("bi_third", "销售额")]
    links = [_link("bi_hq", "销售额", "bi_store", "销售额"),
             _link("bi_hq", "销售额", "bi_third", "销售额",
                   state=config.LINK_PENDING)]
    result = merge.build_groups(metrics, links)
    isolated = result["pending_keys"]
    assert isolated == [merge.member_key("bi_third", "销售额")]
    sales_group = next(g for g in result["groups"] if g["metric_name"] == "销售额"
                       and g["is_merged"])
    states = {m["source"]: m["state"] for m in sales_group["members"]}
    assert states["bi_hq"] == config.MEMBER_CANONICAL
    assert states["bi_store"] == config.MEMBER_LINKED


def test_pure_pending_pair_isolates_both_sides():
    """两个来源都没有任何确认关系，仅一条 pending：两端都先隔离。"""
    metrics = [_metric("bi_store", "退款率"), _metric("bi_channel", "退款率")]
    links = [_link("bi_store", "退款率", "bi_channel", "退款率",
                   state=config.LINK_PENDING)]
    result = merge.build_groups(metrics, links)
    assert set(result["pending_keys"]) == {
        merge.member_key("bi_store", "退款率"),
        merge.member_key("bi_channel", "退款率")}


def test_equivalence_edges_are_undirected_in_snapshot():
    """等价是无向关系，快照里每个关系一对双向边，反向追溯不漏来源。"""
    metrics = [_metric("bi_hq", "销售额"), _metric("bi_store", "销售额"),
               _metric("bi_channel", "销售额")]
    links = [_link("bi_hq", "销售额", "bi_store", "销售额"),
             _link("bi_hq", "销售额", "bi_channel", "销售额")]
    reports = [
        {"source": "bi_hq", "report_id": "H1", "report_name": "h",
         "metric": "销售额", "weight": 4},
        {"source": "bi_store", "report_id": "S1", "report_name": "s",
         "metric": "销售额", "weight": 3},
        {"source": "bi_channel", "report_id": "C1", "report_name": "c",
         "metric": "销售额", "weight": 2},
    ]
    snapshot = livegraph.build_snapshot(metrics=metrics, reports=reports, links=links)
    assert snapshot["counts"]["equiv_pairs"] == 2
    assert snapshot["counts"]["equiv_edges"] == 4
    impact = livegraph.impact(snapshot, "bi_hq", "销售额")
    downstream_sources = {d.split(":", 1)[0] for d in impact["downstream"]}
    assert downstream_sources == {"bi_store", "bi_channel"}
    assert impact["weight_sum"] == "9.00"


def test_isolated_member_excluded_from_live_snapshot():
    metrics = [_metric("bi_hq", "销售额"), _metric("bi_third", "销售额")]
    links = [_link("bi_hq", "销售额", "bi_third", "销售额",
                   state=config.LINK_PENDING)]
    reports = [
        {"source": "bi_hq", "report_id": "H1", "report_name": "h",
         "metric": "销售额", "weight": 4},
        {"source": "bi_third", "report_id": "T1", "report_name": "t",
         "metric": "销售额", "weight": 1},
    ]
    snapshot = livegraph.build_snapshot(metrics=metrics, reports=reports, links=links)
    # 纯 pending 关系两端都无确认等价，双方都不进现网
    node_sources = {n["source"] for n in snapshot["nodes"]}
    assert node_sources == set()
    assert snapshot["counts"]["isolated_members"] == 2
    assert all(r["resolved"] is False for r in snapshot["reports"])
    assert snapshot["counts"]["reports_resolved"] == 0


def test_intra_source_dependency_does_not_leak_across_sources():
    """来源内依赖边只连同一来源；A 来源的血缘不会窜到 B 来源的同名节点。"""
    hq_a = _metric("bi_hq", "毛利率")
    hq_a["metrics"] = ["销售额"]
    hq_b = _metric("bi_hq", "销售额")
    st_b = _metric("bi_store", "销售额")
    snapshot = livegraph.build_snapshot(
        metrics=[hq_a, hq_b, st_b], reports=[], links=[])
    dep_edges = [e for e in snapshot["edges"] if e["kind"] == livegraph.EDGE_DEP]
    assert dep_edges == [
        {"src": "bi_hq:毛利率", "dst": "bi_hq:销售额",
         "kind": livegraph.EDGE_DEP}]
