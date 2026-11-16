"""M8 现网图快照与多源影响面。

现网图（live graph）的不变量：
- 节点身份是 (来源, 指标名)，同名指标在各来源内独立成节点，互不覆盖；
- 指标→指标依赖边只在同一来源内部连（来源内血缘），避免把 A 来源的影响算进 B 来源报表；
- 已确认的跨来源等价关系连成 equivalence 边，下游可沿这条链路跨来源追溯；
- 隔离成员（来源未确认 / 等价关系待确认）整体不进现网图，其报表也不计影响面；
- 快照在发布那一刻定型并落库，发布未完成时读路径仍取上一版快照（旧版本结果）。
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal

from ... import config
from . import merge

METRIC_NODE = "metric"
EDGE_DEP = "metric_metric"      # 来源内指标依赖
EDGE_EQUIV = "equivalence"      # 跨来源已确认等价


def node_id(source: str, name: str) -> str:
    return f"{source}:{name}"


def _live_metric_keys(metrics: list[dict], merge_result: dict) -> set[str]:
    """现网收录的成员：注册表内、且不在隔离集合。"""
    keys = {merge.member_key(m["source"], m["name"]) for m in metrics}
    return keys - set(merge_result["pending_keys"])


def build_snapshot(*, metrics: list[dict], reports: list[dict],
                   links: list[dict]) -> dict:
    """根据当前注册表、报表与等价关系，生成一份可落库的现网快照。"""
    merge_result = merge.build_groups(metrics, links)
    live_keys = _live_metric_keys(metrics, merge_result)
    metric_by_key = {merge.member_key(m["source"], m["name"]): m for m in metrics}

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    def add_edge(src: str, dst: str, kind: str) -> None:
        sig = (kind, src, dst)
        if sig in seen_edges:
            return
        seen_edges.add(sig)
        edges.append({"src": src, "dst": dst, "kind": kind})

    # ---- 指标节点（仅现网成员）+ 来源内依赖边 ----
    for key in sorted(live_keys):
        source, name = merge.split_key(key)
        nid = node_id(source, name)
        group = merge_result["member_index"].get(key)
        nodes.append({
            "node_id": nid, "kind": METRIC_NODE, "source": source,
            "source_label": config.SOURCE_LABELS.get(source, source),
            "label": name, "metric": name,
            "group_id": group["group_id"] if group else None,
            "is_canonical": (group["members_by_key"][key]["is_canonical"]
                             if group else False),
        })
        metric = metric_by_key[key]
        for target in sorted(metric.get("metrics") or []):
            target_key = merge.member_key(source, target)
            if target_key not in live_keys:
                continue  # 指向隔离/未登记成员，不连来源内边
            add_edge(nid, node_id(source, target), EDGE_DEP)

    # ---- 跨来源等价边（仅已确认、两端都在现网）----
    # 等价是无向关系：存成一对方向相反的有向边，反向追溯才能走到链路另一侧，
    # 下游依赖不会因边的存储朝向而漏掉某个来源。
    equiv_pairs = 0
    for left, right in merge.confirmed_pairs(links):
        if left in live_keys and right in live_keys:
            equiv_pairs += 1
            ls, ln = merge.split_key(left)
            rs, rn = merge.split_key(right)
            a, b = node_id(ls, ln), node_id(rs, rn)
            add_edge(a, b, EDGE_EQUIV)
            add_edge(b, a, EDGE_EQUIV)

    # ---- 报表：只挂现网成员；隔离成员的报表标记为未解析、不计影响面 ----
    live_node_ids = {n["node_id"] for n in nodes}
    report_rows: list[dict] = []
    for row in reports:
        target = node_id(row["source"], row["metric"])
        resolved = target in live_node_ids
        report_rows.append({
            "source": row["source"],
            "source_label": config.SOURCE_LABELS.get(row["source"], row["source"]),
            "report_id": row["report_id"], "report_name": row["report_name"],
            "metric": row["metric"], "weight": str(row["weight"]),
            "node_id": target, "resolved": resolved,
        })
        if resolved:
            add_edge(f"report:{row['report_id']}", target, "report_metric")

    edges.sort(key=lambda e: (e["kind"], e["src"], e["dst"]))
    nodes.sort(key=lambda n: (n["source"], n["metric"]))
    groups = [g for g in merge_result["groups"]]
    return {
        "nodes": nodes,
        "edges": edges,
        "reports": report_rows,
        "groups": groups,
        "member_index": merge_result["member_index"],
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "dep_edges": sum(1 for e in edges if e["kind"] == EDGE_DEP),
            "equiv_pairs": equiv_pairs,
            "equiv_edges": sum(1 for e in edges if e["kind"] == EDGE_EQUIV),
            "report_edges": sum(1 for e in edges if e["kind"] == "report_metric"),
            "groups": len(groups),
            "merged_groups": sum(1 for g in groups if g["is_merged"]),
            "isolated_members": len(merge_result["pending_keys"]),
            "reports_total": len(report_rows),
            "reports_resolved": sum(1 for r in report_rows if r["resolved"]),
        },
        "rules": {
            "priority": config.SOURCE_PRIORITY_RULE,
            "isolation": config.SOURCE_ISOLATION_RULE,
            "cutover": config.SOURCE_CUTOVER_RULE,
            "idempotent": config.SOURCE_IDEMPOTENT_RULE,
        },
    }


def reverse_reachable(edges: list[dict], start: str,
                      kinds: tuple[str, ...]) -> set[str]:
    """反向可达（谁依赖我）；visited 防环，自环不把自己算进下游。"""
    reverse: dict[str, list[str]] = {}
    for edge in edges:
        if edge["kind"] not in kinds:
            continue
        reverse.setdefault(edge["dst"], []).append(edge["src"])
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for parent in reverse.get(node, []):
            if parent in visited or parent == start:
                continue
            visited.add(parent)
            queue.append(parent)
    return visited


def impact(snapshot: dict, source: str, name: str) -> dict:
    """多源影响面：沿来源内依赖边 + 跨来源等价边反向追溯，只计已解析报表。

    等价边让下游依赖不断链；来源内依赖边保证 A 来源指标的影响不会窜到 B 来源
    仅同名而未确认等价的报表。
    """
    target = node_id(source, name)
    node_ids = {n["node_id"] for n in snapshot["nodes"]}
    if target not in node_ids:
        return {"known": False, "source": source, "metric": name,
                "downstream": [], "affected_reports": [],
                "downstream_count": 0, "report_count": 0,
                "weight_sum": "0.00", "score": "0.00"}
    reachable = reverse_reachable(
        snapshot["edges"], target, (EDGE_DEP, EDGE_EQUIV))
    affected_set = reachable | {target}
    hit_reports = [r for r in snapshot["reports"]
                   if r["resolved"] and r["node_id"] in affected_set]
    hit_reports.sort(key=lambda r: (r["source"], r["report_id"]))
    weight_sum = sum((Decimal(r["weight"]) for r in hit_reports),
                     Decimal("0")).quantize(config.DECIMAL_QUANT)
    down_count = len(affected_set) - 1
    score = (Decimal(down_count) * weight_sum).quantize(config.DECIMAL_QUANT)
    return {
        "known": True, "source": source,
        "source_label": config.SOURCE_LABELS.get(source, source),
        "metric": name, "node_id": target,
        "downstream": sorted(affected_set - {target}),
        "downstream_count": down_count,
        "affected_reports": [
            {"source": r["source"], "report_id": r["report_id"],
             "report_name": r["report_name"], "metric": r["metric"],
             "weight": str(Decimal(r["weight"]).quantize(config.DECIMAL_QUANT))}
            for r in hit_reports],
        "report_count": len(hit_reports),
        "weight_sum": str(weight_sum),
        "score": str(score),
    }
