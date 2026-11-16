"""M8 合并纯规则：等价链路、来源优先级代表元、隔离判定。

设计要点（对应需求的四条硬约束）：
1. 同名指标在各自来源内独立：身份键是 (source, name)，永不跨来源覆盖；
2. 只有「已确认」的等价关系参与并查集，未确认关系两端先隔离，不进现网链路；
3. 合并组代表元取来源优先级最高者（config.SOURCE_RANK 数字最小），同优先级再按名称稳定排序；
4. 整个模块是纯函数：同样的指标集合 + 关系集合，无论输入顺序，结论完全一致。
"""
from __future__ import annotations

from ... import config


class UnionFind:
    """按规范键做并查集；键排序后接入，合并结果与输入顺序无关。"""

    def __init__(self, keys: set[str]):
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:  # 路径压缩
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 固定取字典序较小者为根，保证结构确定
        if ra > rb:
            ra, rb = rb, ra
        self.parent[rb] = ra

    def groups(self) -> list[list[str]]:
        buckets: dict[str, list[str]] = {}
        for key in sorted(self.parent):
            buckets.setdefault(self.find(key), []).append(key)
        return [sorted(members) for members in buckets.values()]


def member_key(source: str, name: str) -> str:
    return f"{source}\x00{name}"


def split_key(key: str) -> tuple[str, str]:
    source, name = key.split("\x00", 1)
    return source, name


def confirmed_pairs(links: list[dict]) -> list[tuple[str, str]]:
    """只取已确认关系；同一对去重并排序，避免重复建链。"""
    pairs: set[tuple[str, str]] = set()
    for link in links:
        if link["state"] != config.LINK_CONFIRMED:
            continue
        left = member_key(link["left_source"], link["left_name"])
        right = member_key(link["right_source"], link["right_name"])
        pairs.add((left, right) if left <= right else (right, left))
    return sorted(pairs)


def pending_member_keys(links: list[dict]) -> set[str]:
    """参与未确认关系的成员键；仅用于隔离判定，不直接等于隔离集合。"""
    keys: set[str] = set()
    for link in links:
        if link["state"] != config.LINK_PENDING:
            continue
        keys.add(member_key(link["left_source"], link["left_name"]))
        keys.add(member_key(link["right_source"], link["right_name"]))
    return keys


def confirmed_member_keys(links: list[dict]) -> set[str]:
    """至少参与一条已确认等价关系的成员键（其跨来源等价已成立）。"""
    keys: set[str] = set()
    for left, right in confirmed_pairs(links):
        keys.add(left)
        keys.add(right)
    return keys


def isolated_member_keys(links: list[dict], live_keys: set[str]) -> set[str]:
    """隔离集合 = 参与了待确认关系、且没有任何已确认等价关系的成员。

    关键边界：已在确认链路中的成员（如代表元）即使对新来源还有一条 pending 关系，
    也保持在现网；只隔离「仅凭未确认关系挂钩」的新成员，避免已生效下游被误断。
    纯 pending 的一对（双方都无确认关系）则两端都隔离。
    """
    pending = pending_member_keys(links) & live_keys
    confirmed = confirmed_member_keys(links) & live_keys
    return pending - confirmed


def canonical_of(members: list[tuple[str, str]]) -> tuple[str, str]:
    """代表元：来源优先级最高（rank 最小），同级按指标名、再按来源编码稳定排序。"""
    return sorted(members, key=lambda item: (config.SOURCE_RANK.get(item[0], 9999),
                                             item[1], item[0]))[0]


def build_groups(metrics: list[dict], links: list[dict]) -> dict:
    """根据指标注册表与等价关系，算出合并组、代表元与每个成员的状态。

    metrics 元素至少含 source、name；links 元素含两端 source/name 与 state。
    返回 groups（每个合并组一行）与 member_index（成员键 → 组信息）。
    """
    all_keys = {member_key(m["source"], m["name"]) for m in metrics}
    uf = UnionFind(all_keys)
    for left, right in confirmed_pairs(links):
        # 关系两端若已不在注册表（来源被整体替换等），跳过，不污染并查集
        if left in all_keys and right in all_keys:
            uf.union(left, right)

    isolated = isolated_member_keys(links, all_keys)
    raw_groups = uf.groups()

    groups: list[dict] = []
    member_index: dict[str, dict] = {}
    for index, member_keys in enumerate(raw_groups):
        pairs = [split_key(key) for key in member_keys]
        canonical_source, canonical_name = canonical_of(pairs)
        group_id = f"G{index + 1:03d}"
        # 多来源组才是真正的「合并链路」；单来源单指标是独立组
        is_merged = len({source for source, _ in pairs}) > 1
        has_isolated = any(key in isolated for key in member_keys)
        members_view = []
        for key in member_keys:
            source, name = split_key(key)
            if key in isolated:
                state = config.MEMBER_ISOLATED
            elif source == canonical_source and name == canonical_name:
                state = config.MEMBER_CANONICAL
            else:
                state = config.MEMBER_LINKED
            members_view.append({
                "key": key, "source": source,
                "source_label": config.SOURCE_LABELS.get(source, source),
                "name": name, "state": state,
                "state_label": config.MEMBER_STATE_LABELS[state],
                "is_canonical": state == config.MEMBER_CANONICAL,
                "is_isolated": state == config.MEMBER_ISOLATED,
            })
        members_view.sort(key=lambda item: (
            config.SOURCE_RANK.get(item["source"], 9999), item["name"]))
        group = {
            "group_id": group_id,
            "metric_name": canonical_name,
            "is_merged": is_merged,
            "has_pending": has_isolated,
            "canonical_source": canonical_source,
            "canonical_source_label": config.SOURCE_LABELS.get(
                canonical_source, canonical_source),
            "canonical_name": canonical_name,
            "members": members_view,
            "members_by_key": {m["key"]: m for m in members_view},
            "member_count": len(members_view),
            "isolated_count": sum(1 for m in members_view if m["is_isolated"]),
        }
        groups.append(group)
        for key in member_keys:
            member_index[key] = group
    groups.sort(key=lambda g: (g["metric_name"], g["group_id"]))
    return {"groups": groups, "member_index": member_index,
            "pending_keys": sorted(isolated),
            "isolated_keys": sorted(isolated)}
