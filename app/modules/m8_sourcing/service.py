"""M8 编排：来源接入、等价关系、现网版本发布、现网图与多源影响面。

四条硬约束在这一层收口：
- 来源优先级决定合并组代表元（merge.canonical_of）；
- 未确认来源/未确认等价关系先隔离，不进现网快照（livegraph.build_snapshot）；
- 确认后整版原子发布，全量切换现网图与影响面（repository.publish_revision）；
- 发布未完成（草稿）继续展示上一版现网结果（get_live_snapshot 只读 published）；
- 同一来源同内容重跑命中内容摘要，复用运行摘要，不新增节点或报表。
"""
from __future__ import annotations

from ... import config, errors, manifest, reasons
from . import livegraph, merge, parsing, repository


# ---------------- 来源接入 ----------------
def ingest_source(settings, store, *, source: str, text: str,
                  reports_text: str | None = None, note: str = "") -> dict:
    repository.ensure_sources(store)
    if source not in repository.known_source_codes(store):
        raise errors.BadRequest(
            f"来源未登记：{source}", code="S_SOURCE_UNKNOWN",
            detail={"source": source, "known": sorted(config.SOURCE_RANK)})

    parsed = parsing.parse_source_dsl(text)
    if parsed["error_lines"]:
        raise errors.BadRequest(
            f"来源 {source} 有 {len(parsed['error_lines'])} 行无法登记",
            code="S_SOURCE_PARSE_FAILED",
            detail={"error_lines": parsed["error_lines"],
                    "codes": sorted({d["code"] for d in parsed["diagnostics"]
                                     if d["level"] == reasons.ERROR})})

    digest = parsed["digest"]
    existing = repository.find_run(store, source, digest)
    reports = (parsing.parse_reports_csv(reports_text)
               if reports_text is not None else [])
    if existing is not None:
        # 幂等：同来源同内容不新增节点；报表文本若同时提交则按 report_id upsert
        if reports:
            repository.upsert_reports(store, source=source, reports=reports)
        return {"reused": True, "source": source, "digest": digest,
                "run_id": existing["run_id"],
                "metric_count": existing["metric_count"],
                "note": reasons.get("S_REPLAY_NO_NEW_NODE").title}

    run_id = manifest.new_run_id(prefix="MS")
    repository.replace_source_metrics(
        store, source=source, digest=digest, run_id=run_id,
        metrics=parsed["metrics"], report_count=len(reports),
        note=note or "来源接入")
    if reports:
        repository.upsert_reports(store, source=source, reports=reports)
    return {"reused": False, "source": source, "digest": digest, "run_id": run_id,
            "metric_count": len(parsed["metrics"]),
            "report_count": len(reports), "note": "已接入"}


def load_sample_bundle(settings, store) -> dict:
    """从 sample_data/sourcing/ 一次性载入全部来源、报表与等价关系，供演示与测试。

    每个来源各自幂等接入；已存在的同内容来源直接复用，不新增节点。
    """
    base = settings.sample_path("sourcing")
    sources_file = base / "sources.csv"
    import csv
    import io

    results: list[dict] = []
    with sources_file.open(encoding="utf-8-sig") as fh:
        source_rows = list(csv.DictReader(io.StringIO(fh.read())))
    for row in source_rows:
        code = row["source"].strip()
        dsl = (base / row["dsl"].strip()).read_text(encoding="utf-8-sig")
        report_path = base / row["reports"].strip()
        reports_text = report_path.read_text(encoding="utf-8-sig")
        results.append(ingest_source(
            settings, store, source=code, text=dsl,
            reports_text=reports_text, note="样例来源接入"))

    links_file = base / "equivalences.csv"
    link_results: list[dict] = []
    if links_file.exists():
        with links_file.open(encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(io.StringIO(fh.read())):
                link_results.append(register_link(store, {
                    "left_source": raw["left_source"].strip(),
                    "left_name": raw["left_name"].strip(),
                    "right_source": raw["right_source"].strip(),
                    "right_name": raw["right_name"].strip(),
                    "state": raw["state"].strip(),
                }))
    return {"sources": results, "links": link_results}


# ---------------- 等价关系 ----------------
def _require_metric(store, source: str, name: str) -> None:
    known = {(m["source"], m["name"]) for m in repository.list_metrics(store, source)}
    if (source, name) not in known:
        raise errors.NotFound(
            f"来源指标未登记：{source}/{name}", code="S_LINK_UNKNOWN_MEMBER",
            detail={"source": source, "name": name})


def register_link(store, payload: dict) -> dict:
    repository.ensure_sources(store)
    left_source = (payload.get("left_source") or "").strip()
    left_name = (payload.get("left_name") or "").strip()
    right_source = (payload.get("right_source") or "").strip()
    right_name = (payload.get("right_name") or "").strip()
    state = (payload.get("state") or config.LINK_PENDING).strip()
    if state not in config.LINK_STATES:
        raise errors.BadRequest(
            f"等价关系状态非法：{state}", code="S_SOURCE_UNKNOWN",
            detail={"allowed": list(config.LINK_STATES)})
    if not all([left_source, left_name, right_source, right_name]):
        raise errors.BadRequest("等价关系两端来源与指标名均必填",
                                code="S_LINK_UNKNOWN_MEMBER")
    for code in (left_source, right_source):
        if code not in repository.known_source_codes(store):
            raise errors.BadRequest(f"来源未登记：{code}",
                                    code="S_SOURCE_UNKNOWN")
    _require_metric(store, left_source, left_name)
    _require_metric(store, right_source, right_name)

    existing = repository.find_link(
        store, left_source, left_name, right_source, right_name)
    if existing is not None:
        updated = repository.set_link_state(store, existing["link_id"], state)
        return {"duplicate": True, "link": updated,
                "note": reasons.get("S_LINK_DUPLICATE").title}
    link = repository.insert_link(
        store, left_source=left_source, left_name=left_name,
        right_source=right_source, right_name=right_name, state=state,
        note=payload.get("note", ""))
    return {"duplicate": False, "link": link,
            "note": reasons.get(
                "S_LINK_CONFIRMED" if state == config.LINK_CONFIRMED
                else "S_LINK_PENDING_ISOLATED").title}


def confirm_link(store, link_id: str) -> dict:
    updated = repository.set_link_state(store, link_id, config.LINK_CONFIRMED)
    if updated is None:
        raise errors.NotFound(f"等价关系不存在：{link_id}",
                              code="S_LINK_UNKNOWN_MEMBER")
    return {"link": updated, "note": reasons.get("S_LINK_CONFIRMED").title}


# ---------------- 现网版本：草稿 → 原子发布 ----------------
def _current_snapshot(store) -> dict:
    metrics = repository.list_metrics(store)
    reports = repository.list_reports(store)
    links = repository.list_links(store)
    return livegraph.build_snapshot(metrics=metrics, reports=reports, links=links)


def prepare_revision(store, *, note: str = "") -> dict:
    """生成一版待发布草稿；不改变现网指针，现网仍展示上一版。"""
    import json

    snapshot = _current_snapshot(store)
    digest = manifest.digest_text(json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, default=str))
    revision = repository.next_revision(store)
    repository.insert_revision(
        store, revision=revision, status=config.REV_STAGING,
        digest=digest, snapshot=snapshot, note=note or "现网版本草稿")
    return {"revision": revision, "status": config.REV_STAGING,
            "digest": digest, "counts": snapshot["counts"],
            "note": reasons.get("S_REVISION_STAGING").title}


def publish(store, revision: int) -> dict:
    record = repository.get_revision(store, revision)
    if record is None:
        raise errors.NotFound(f"现网版本不存在：v{revision}",
                              code="S_NO_PUBLISHED")
    before = repository.live_revision_id(store)
    published = repository.publish_revision(store, revision)
    if published is None:  # 理论上不会发生，前置已查
        raise errors.NotFound(f"现网版本发布失败：v{revision}",
                              code="S_NO_PUBLISHED")
    return {"revision": revision, "status": config.REV_PUBLISHED,
            "previous_revision": before,
            "counts": record["snapshot"]["counts"],
            "note": reasons.get("S_REVISION_PUBLISHED").title,
            "atomic": reasons.get("S_CUTOVER_ATOMIC").title}


def prepare_and_publish(store, *, note: str = "") -> dict:
    """确认后常用的一步到位：先出草稿再原子发布，全量切换现网。"""
    staged = prepare_revision(store, note=note)
    return publish(store, staged["revision"])


# ---------------- 读视图 ----------------
def groups_view(store) -> dict:
    metrics = repository.list_metrics(store)
    links = repository.list_links(store)
    result = merge.build_groups(metrics, links)
    merged = [g for g in result["groups"] if g["is_merged"]]
    return {
        "groups": result["groups"],
        "merged_groups": merged,
        "counts": {
            "groups": len(result["groups"]),
            "merged": len(merged),
            "pending_members": len(result["pending_keys"]),
            "canonical": sum(1 for g in result["groups"]),
        },
        "rules": {"priority": config.SOURCE_PRIORITY_RULE,
                  "isolation": config.SOURCE_ISOLATION_RULE},
    }


def live_view(store) -> dict:
    live = repository.get_live_snapshot(store)
    if live is None:
        return {"has_live": False, "revision": None, "snapshot": None,
                "notice": reasons.get("S_NO_PUBLISHED").title}
    return {"has_live": True, "revision": live["revision"],
            "published_at": live["published_at"],
            "snapshot": live["snapshot"]}


def impact_view(store, source: str, name: str) -> dict:
    live = repository.get_live_snapshot(store)
    if live is None:
        raise errors.NotFound("还没有已发布的现网版本", code="S_NO_PUBLISHED")
    result = livegraph.impact(live["snapshot"], source, name)
    result["revision"] = live["revision"]
    if not result["known"]:
        raise errors.NotFound(
            f"现网图中没有该来源指标：{source}/{name}",
            code="S_LINK_UNKNOWN_MEMBER")
    return result


def overview(store) -> dict:
    repository.ensure_sources(store)
    sources = repository.list_sources(store)
    metrics = repository.list_metrics(store)
    reports = repository.list_reports(store)
    links = repository.list_links(store)
    runs = repository.list_runs(store)
    revisions = repository.list_revisions(store)
    live_id = repository.live_revision_id(store)
    per_source: dict[str, dict] = {}
    for code in config.SOURCE_RANK:
        per_source[code] = {"source": code,
                            "label": config.SOURCE_LABELS[code],
                            "priority": config.SOURCE_RANK[code],
                            "metrics": 0, "reports": 0, "runs": 0}
    for m in metrics:
        per_source[m["source"]]["metrics"] += 1
    for r in reports:
        per_source[r["source"]]["reports"] += 1
    for run in runs:
        per_source[run["source_code"]]["runs"] += 1
    return {
        "sources": sources,
        "per_source": [per_source[c] for c in config.SOURCE_RANK],
        "links": links,
        "link_counts": {
            "total": len(links),
            "confirmed": sum(1 for x in links if x["state"] == config.LINK_CONFIRMED),
            "pending": sum(1 for x in links if x["state"] == config.LINK_PENDING),
        },
        "revisions": revisions,
        "live_revision": live_id,
        "groups": groups_view(store),
    }
