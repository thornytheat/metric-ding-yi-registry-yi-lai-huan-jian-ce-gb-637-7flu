"""M8 存储访问：来源注册表、来源指标/报表、等价关系、现网版本与现网指针。

所有写操作走 Store 的同一连接上下文（单事务），读操作只按来源/状态过滤。
现网指针 src_live 永远只有一行（singleton=1），读路径只认它指向的 published 版本。
"""
from __future__ import annotations

import json
from datetime import datetime

from ... import config, manifest


def now_utc() -> str:
    return datetime.now(config.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- 来源 ----------------
def ensure_sources(store) -> None:
    with store.connect() as conn:
        existing = {r["source_code"] for r in
                    conn.execute("SELECT source_code FROM src_sources").fetchall()}
        for code, label, priority in config.SOURCE_PRIORITY:
            if code in existing:
                continue
            conn.execute(
                "INSERT INTO src_sources (source_code,label,priority,created_at,note) "
                "VALUES (?,?,?,?,?)",
                (code, label, priority, now_utc(), "来源优先级登记表初始化"))


def list_sources(store) -> list[dict]:
    ensure_sources(store)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM src_sources ORDER BY priority, source_code").fetchall()
    return [dict(r) for r in rows]


def known_source_codes(store) -> set[str]:
    return {r["source_code"] for r in list_sources(store)}


# ---------------- 来源指标接入（幂等） ----------------
def find_run(store, source: str, digest: str) -> dict | None:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM src_metric_runs WHERE source_code=? AND content_digest=?",
            (source, digest)).fetchone()
    return dict(row) if row else None


def replace_source_metrics(store, *, source: str, digest: str, run_id: str,
                           metrics: list[dict], report_count: int,
                           note: str) -> None:
    """同一来源整组替换：先删该来源旧指标/运行摘要，再插新内容，单事务。

    报表在另一接口单独 upsert；这里只记 report_count 统计。
    """
    ts = now_utc()
    with store.connect() as conn:
        conn.execute("DELETE FROM src_metrics WHERE source_code=?", (source,))
        conn.execute(
            "DELETE FROM src_metric_runs WHERE source_code=? AND content_digest=?",
            (source, digest))
        conn.executemany(
            """INSERT INTO src_metrics (source_code,name,line_no,signature,expr_json,
                 where_json,group_by_json,fields_json,metrics_json,deps_json,aggs_json,
                 expr_text,content_digest,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(source, m["name"], m["line_no"], m["signature"], _j(m["expr"]),
              _j(m["where"]), _j(m["group_by"]), _j(m["fields"]), _j(m["metrics"]),
              _j(m["depends_declared"]), _j(m["aggregators"]),
              m.get("expr_text", ""), digest, ts) for m in metrics])
        conn.execute(
            """INSERT INTO src_metric_runs (source_code,content_digest,run_id,status,
                 metric_count,report_count,created_at,note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (source, digest, run_id, "done", len(metrics), report_count, ts, note))


def list_metrics(store, source: str | None = None) -> list[dict]:
    sql = ("SELECT * FROM src_metrics "
           "WHERE (? IS NULL OR source_code=?) ORDER BY source_code, line_no")
    arg = source
    with store.connect() as conn:
        rows = conn.execute(sql, (arg, arg)).fetchall()
    mapping = {"expr": "expr_json", "where": "where_json",
               "group_by": "group_by_json", "fields": "fields_json",
               "metrics": "metrics_json", "depends_declared": "deps_json",
               "aggregators": "aggs_json"}
    out: list[dict] = []
    for r in rows:
        item = dict(r)
        item["source"] = item.pop("source_code")
        for key, column in mapping.items():
            item[key] = json.loads(item.pop(column) or "null")
        out.append(item)
    return out


def list_runs(store) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM src_metric_runs ORDER BY created_at DESC, source_code"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 来源报表（幂等 upsert） ----------------
def upsert_reports(store, *, source: str, reports: list[dict]) -> None:
    ts = now_utc()
    with store.connect() as conn:
        for r in reports:
            conn.execute(
                """INSERT INTO src_reports (source_code,report_id,report_name,metric,
                     weight,updated_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(source_code,report_id) DO UPDATE SET
                     report_name=excluded.report_name, metric=excluded.metric,
                     weight=excluded.weight, updated_at=excluded.updated_at""",
                (source, r["report_id"], r["report_name"], r["metric"],
                 str(r["weight"]), ts))


def list_reports(store, source: str | None = None) -> list[dict]:
    from decimal import Decimal

    sql = ("SELECT * FROM src_reports "
           "WHERE (? IS NULL OR source_code=?) ORDER BY source_code, report_id")
    arg = source
    with store.connect() as conn:
        rows = conn.execute(sql, (arg, arg)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["source"] = item.pop("source_code")
        item["weight"] = Decimal(item["weight"])
        out.append(item)
    return out


# ---------------- 等价关系 ----------------
def find_link(store, left_source, left_name, right_source, right_name) -> dict | None:
    a, b = _ordered_pair(left_source, left_name, right_source, right_name)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM src_links WHERE left_source=? AND left_name=? "
            "AND right_source=? AND right_name=?",
            (a[0], a[1], b[0], b[1])).fetchone()
    return dict(row) if row else None


def insert_link(store, *, left_source, left_name, right_source, right_name,
                state: str, note: str = "") -> dict:
    a, b = _ordered_pair(left_source, left_name, right_source, right_name)
    link_id = "L-" + manifest.digest_parts(
        [a[0], a[1], b[0], b[1]])[:12]
    ts = now_utc()
    confirmed_at = ts if state == config.LINK_CONFIRMED else None
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO src_links (link_id,left_source,left_name,right_source,
                 right_name,state,created_at,confirmed_at,note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (link_id, a[0], a[1], b[0], b[1], state, ts, confirmed_at, note))
    return {"link_id": link_id, "left_source": a[0], "left_name": a[1],
            "right_source": b[0], "right_name": b[1], "state": state}


def set_link_state(store, link_id: str, state: str) -> dict | None:
    ts = now_utc()
    confirmed_at = ts if state == config.LINK_CONFIRMED else None
    with store.connect() as conn:
        cur = conn.execute(
            "UPDATE src_links SET state=?, confirmed_at=? WHERE link_id=?",
            (state, confirmed_at, link_id))
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM src_links WHERE link_id=?", (link_id,)).fetchone()
    return dict(row) if row else None


def list_links(store, state: str | None = None) -> list[dict]:
    sql = "SELECT * FROM src_links WHERE (? IS NULL OR state=?) ORDER BY link_id"
    with store.connect() as conn:
        rows = conn.execute(sql, (state, state)).fetchall()
    return [dict(r) for r in rows]


def _ordered_pair(left_source, left_name, right_source, right_name):
    a = (left_source, left_name)
    b = (right_source, right_name)
    return (a, b) if a <= b else (b, a)


# ---------------- 现网版本与发布 ----------------
def next_revision(store) -> int:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) AS n FROM src_revisions").fetchone()
    return row["n"] + 1


def insert_revision(store, *, revision: int, status: str, digest: str,
                    snapshot: dict, note: str) -> None:
    ts = now_utc()
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO src_revisions (revision,status,digest,snapshot_json,
                 created_at,published_at,note) VALUES (?,?,?,?,?,?,?)""",
            (revision, status, digest, json.dumps(snapshot, ensure_ascii=False),
             ts, ts if status == config.REV_PUBLISHED else None, note))


def publish_revision(store, revision: int) -> dict | None:
    """原子发布：旧 published 全部置 superseded，目标版置 published，现网指针切到该版。"""
    ts = now_utc()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT revision,status FROM src_revisions WHERE revision=?",
            (revision,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE src_revisions SET status=?, published_at=? WHERE status=?",
            (config.REV_SUPERSEDED, None, config.REV_PUBLISHED))
        conn.execute(
            "UPDATE src_revisions SET status=?, published_at=? WHERE revision=?",
            (config.REV_PUBLISHED, ts, revision))
        conn.execute(
            "INSERT INTO src_live (singleton,live_revision) VALUES (1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET live_revision=excluded.live_revision",
            (revision,))
        fresh = conn.execute(
            "SELECT * FROM src_revisions WHERE revision=?", (revision,)).fetchone()
    return dict(fresh) if fresh else None


def live_revision_id(store) -> int | None:
    with store.connect() as conn:
        row = conn.execute("SELECT live_revision FROM src_live").fetchone()
    return row["live_revision"] if row and row["live_revision"] is not None else None


def get_revision(store, revision: int) -> dict | None:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM src_revisions WHERE revision=?", (revision,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["snapshot"] = json.loads(item.pop("snapshot_json"))
    return item


def list_revisions(store) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT revision,status,digest,created_at,published_at,note "
            "FROM src_revisions ORDER BY revision DESC").fetchall()
    return [dict(r) for r in rows]


def get_live_snapshot(store) -> dict | None:
    revision = live_revision_id(store)
    if revision is None:
        return None
    record = get_revision(store, revision)
    if record is None or record["status"] != config.REV_PUBLISHED:
        return None
    return {"revision": revision, "snapshot": record["snapshot"],
            "published_at": record["published_at"]}


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
