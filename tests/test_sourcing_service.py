"""M8 服务与接口：幂等接入、等价关系、草稿/原子发布、现网图与多源影响面。

四条硬约束都有一条对应断言：
- 同名指标各来源独立、未确认先隔离（test_pending_source_isolated_until_confirmed）；
- 确认后全量切换现网图与影响面（test_confirm_then_publish_switches_live）；
- 发布未完成继续展示旧版本（test_staging_revision_keeps_old_live_result）；
- 同一来源重跑不新增节点或报表（test_replay_same_source_adds_no_node_or_report）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.errors import BadRequest, NotFound
from app.main import create_app
from app.modules.m8_sourcing import repository, service
from app.store import Store

HQ_DSL = (
    "销售额 = SUM(order_amt) - SUM(refund_amt) group by store_id,order_date\n"
    "客单价 = AVG(order_amt) group by store_id\n"
)
STORE_DSL = (
    "销售额 = SUM(order_amt) where channel = 线下 group by store_id\n"
    "退款率 = SUM(refund_amt) / SUM(order_amt) group by store_id\n"
)
THIRD_DSL = "销售额 = SUM(order_amt) group by city\n"
HQ_REPORTS = (
    "report_id,report_name,metric,weight\n"
    "HQ-01,总部日报,销售额,4\nHQ-02,客单看板,客单价,1.5\n"
)
STORE_REPORTS = (
    "report_id,report_name,metric,weight\n"
    "ST-01,门店日报,销售额,3.5\n"
)
THIRD_REPORTS = (
    "report_id,report_name,metric,weight\n"
    "TP-01,三方看板,销售额,1\n"
)


@pytest.fixture()
def m8_store(tmp_path) -> Store:
    settings = config.Settings(db_path=str(tmp_path / "m8.sqlite"))
    store = Store(settings.db_path)
    return store


@pytest.fixture()
def m8_settings(tmp_path):
    return config.Settings(db_path=str(tmp_path / "m8.sqlite"),
                           sample_dir="/workspace/sample_data")


def _ingest_all(settings, store):
    service.ingest_source(settings, store, source="bi_hq", text=HQ_DSL,
                          reports_text=HQ_REPORTS)
    service.ingest_source(settings, store, source="bi_store", text=STORE_DSL,
                          reports_text=STORE_REPORTS)
    service.ingest_source(settings, store, source="bi_third", text=THIRD_DSL,
                          reports_text=THIRD_REPORTS)


def test_unknown_source_rejected(m8_settings, m8_store):
    with pytest.raises(BadRequest) as exc:
        service.ingest_source(m8_settings, m8_store, source="bi_x",
                              text="销售额 = SUM(order_amt)")
    assert exc.value.code == "S_SOURCE_UNKNOWN"


def test_parse_failure_rejected_and_not_registered(m8_settings, m8_store):
    with pytest.raises(BadRequest) as exc:
        service.ingest_source(m8_settings, m8_store, source="bi_hq",
                              text="坏行 SUM(order_amt)")
    assert exc.value.code == "S_SOURCE_PARSE_FAILED"
    assert repository.list_metrics(m8_store, "bi_hq") == []


def test_replay_same_source_adds_no_node_or_report(m8_settings, m8_store):
    first = service.ingest_source(m8_settings, m8_store, source="bi_hq",
                                  text=HQ_DSL, reports_text=HQ_REPORTS)
    second = service.ingest_source(m8_settings, m8_store, source="bi_hq",
                                   text=HQ_DSL, reports_text=HQ_REPORTS)
    assert first["reused"] is False and second["reused"] is True
    assert first["run_id"] == second["run_id"]
    assert len(repository.list_metrics(m8_store)) == 2
    assert len(repository.list_reports(m8_store)) == 2
    assert len(repository.list_runs(m8_store)) == 1


def test_link_requires_registered_members(m8_settings, m8_store):
    _ingest_all(m8_settings, m8_store)
    with pytest.raises(NotFound) as exc:
        service.register_link(m8_store, {
            "left_source": "bi_hq", "left_name": "销售额",
            "right_source": "bi_store", "right_name": "幽灵",
            "state": "confirmed"})
    assert exc.value.code == "S_LINK_UNKNOWN_MEMBER"


def test_duplicate_link_is_upserted_not_doubled(m8_settings, m8_store):
    _ingest_all(m8_settings, m8_store)
    payload = {"left_source": "bi_hq", "left_name": "销售额",
               "right_source": "bi_store", "right_name": "销售额",
               "state": "pending"}
    first = service.register_link(m8_store, payload)
    again = service.register_link(m8_store, payload)
    assert first["duplicate"] is False and again["duplicate"] is True
    assert len(repository.list_links(m8_store)) == 1


def test_staging_revision_keeps_old_live_result(m8_settings, m8_store):
    _ingest_all(m8_settings, m8_store)
    service.register_link(m8_store, {
        "left_source": "bi_hq", "left_name": "销售额",
        "right_source": "bi_store", "right_name": "销售额",
        "state": "confirmed"})
    first = service.prepare_and_publish(m8_store)
    live_before = service.live_view(m8_store)
    # 再出一版草稿但不发布：现网必须仍是上一版
    staged = service.prepare_revision(m8_store, note="未完成发布")
    assert staged["status"] == config.REV_STAGING
    live_after = service.live_view(m8_store)
    assert live_after["revision"] == first["revision"]
    assert live_after["revision"] == live_before["revision"]


def test_pending_source_isolated_until_confirmed(m8_settings, m8_store):
    _ingest_all(m8_settings, m8_store)
    service.register_link(m8_store, {
        "left_source": "bi_hq", "left_name": "销售额",
        "right_source": "bi_store", "right_name": "销售额",
        "state": "confirmed"})
    service.register_link(m8_store, {
        "left_source": "bi_hq", "left_name": "销售额",
        "right_source": "bi_third", "right_name": "销售额",
        "state": "pending"})
    service.prepare_and_publish(m8_store)
    # 三方未确认：不在现网，查影响面直接 404
    with pytest.raises(NotFound) as exc:
        service.impact_view(m8_store, "bi_third", "销售额")
    assert exc.value.code == "S_LINK_UNKNOWN_MEMBER"
    # 总部仍在现网且代表元身份不被 pending 关系动摇
    hq = service.impact_view(m8_store, "bi_hq", "销售额")
    assert hq["known"] is True
    sources = {r["source"] for r in hq["affected_reports"]}
    assert sources == {"bi_hq", "bi_store"}


def test_confirm_then_publish_switches_live(m8_settings, m8_store):
    _ingest_all(m8_settings, m8_store)
    service.register_link(m8_store, {
        "left_source": "bi_hq", "left_name": "销售额",
        "right_source": "bi_store", "right_name": "销售额",
        "state": "confirmed"})
    pending = service.register_link(m8_store, {
        "left_source": "bi_hq", "left_name": "销售额",
        "right_source": "bi_third", "right_name": "销售额",
        "state": "pending"})
    first = service.prepare_and_publish(m8_store)
    service.confirm_link(m8_store, pending["link"]["link_id"])
    second = service.prepare_and_publish(m8_store)
    assert second["revision"] > first["revision"]
    live = service.live_view(m8_store)
    assert live["revision"] == second["revision"]
    assert live["snapshot"]["counts"]["isolated_members"] == 0
    third = service.impact_view(m8_store, "bi_third", "销售额")
    assert {r["source"] for r in third["affected_reports"]} == {
        "bi_hq", "bi_store", "bi_third"}
    # 原子切换：旧版本被置 superseded，现网指针唯一
    statuses = {r["revision"]: r["status"]
                for r in repository.list_revisions(m8_store)}
    assert statuses[first["revision"]] == config.REV_SUPERSEDED
    assert statuses[second["revision"]] == config.REV_PUBLISHED
    assert repository.live_revision_id(m8_store) == second["revision"]


def test_impact_does_not_mix_unrelated_source_reports(m8_settings, m8_store):
    """影响面只沿来源内依赖 + 已确认等价走，未确认同名来源的报表不算进来。"""
    _ingest_all(m8_settings, m8_store)
    service.prepare_and_publish(m8_store)
    hq = service.impact_view(m8_store, "bi_hq", "客单价")
    assert [r["report_id"] for r in hq["affected_reports"]] == ["HQ-02"]


def test_sample_bundle_is_idempotent(m8_settings, m8_store):
    service.load_sample_bundle(m8_settings, m8_store)
    metrics_after_first = len(repository.list_metrics(m8_store))
    again = service.load_sample_bundle(m8_settings, m8_store)
    assert all(item["reused"] for item in again["sources"])
    assert len(repository.list_metrics(m8_store)) == metrics_after_first


# ---------------- HTTP 层 ----------------
@pytest.fixture()
def client(m8_settings):
    return TestClient(create_app(m8_settings))


def test_http_full_flow_and_page(client):
    assert client.get("/api/sourcing/live").json()["has_live"] is False
    bundle = client.post("/api/sourcing/sample-bundle").json()
    assert len(bundle["sources"]) == 4
    # 发布前影响面 404
    assert client.get("/api/sourcing/impact?source=bi_hq&metric=销售额").status_code == 404
    staged = client.post("/api/sourcing/revisions/prepare").json()
    # 草稿不切现网
    assert client.get("/api/sourcing/live").json()["has_live"] is False
    published = client.post(
        f"/api/sourcing/revisions/{staged['revision']}/publish").json()
    assert published["status"] == "published"
    impact = client.get(
        "/api/sourcing/impact?source=bi_hq&metric=销售额").json()
    assert impact["score"] == "19.00"
    # 隔离来源 404
    assert client.get(
        "/api/sourcing/impact?source=bi_third&metric=销售额").status_code == 404
    # 页面可渲染且导航含多源
    page = client.get("/sourcing")
    assert page.status_code == 200
    assert 'data-nav="sourcing"' in page.text
    assert page.text.count("data-nav=") == 8


def test_http_ingest_validation(client):
    bad_source = client.post("/api/sourcing/ingest",
                             json={"source": "nope", "text": "a = SUM(order_amt)"})
    assert bad_source.status_code == 400
    assert bad_source.json()["error"] == "S_SOURCE_UNKNOWN"
    missing_text = client.post("/api/sourcing/ingest",
                               json={"source": "bi_hq"})
    assert missing_text.status_code == 400
    assert missing_text.json()["error"] == "S_SOURCE_PARSE_FAILED"
