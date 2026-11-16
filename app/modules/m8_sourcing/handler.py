"""M8 入口：多来源接入、等价关系、现网版本发布、现网图、多源影响面、页面。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from ... import errors
from ...web import Ctx, get_ctx, render
from . import repository, service

router = APIRouter(tags=["M8 多来源同名指标合并"])


def _field(payload, name, default=None):
    if isinstance(payload, dict):
        return payload.get(name, default)
    return default


@router.get("/api/sourcing/overview")
def api_overview(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.overview(ctx.store)


@router.post("/api/sourcing/ingest")
def api_ingest(payload: dict | None = Body(default=None),
               ctx: Ctx = Depends(get_ctx)) -> dict:
    data = payload if isinstance(payload, dict) else {}
    source = (_field(data, "source") or "").strip()
    text = _field(data, "text")
    if not source:
        raise errors.BadRequest("缺少 source", code="S_SOURCE_UNKNOWN")
    if not isinstance(text, str) or not text.strip():
        raise errors.BadRequest("缺少 DSL 文本 text", code="S_SOURCE_PARSE_FAILED")
    reports_text = _field(data, "reports_text")
    if reports_text is not None and not isinstance(reports_text, str):
        raise errors.BadRequest("reports_text 必须是字符串",
                                code="S_SOURCE_PARSE_FAILED")
    return service.ingest_source(
        ctx.settings, ctx.store, source=source, text=text,
        reports_text=reports_text, note=_field(data, "note", ""))


@router.post("/api/sourcing/sample-bundle")
def api_sample_bundle(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.load_sample_bundle(ctx.settings, ctx.store)


@router.get("/api/sourcing/metrics")
def api_metrics(source: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    metrics = repository.list_metrics(ctx.store, source)
    return {"source": source, "count": len(metrics), "metrics": metrics}


@router.get("/api/sourcing/links")
def api_links(state: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    rows = repository.list_links(ctx.store, state)
    return {"state": state, "count": len(rows), "links": rows}


@router.post("/api/sourcing/links")
def api_register_link(payload: dict | None = Body(default=None),
                      ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.register_link(ctx.store, payload if isinstance(payload, dict) else {})


@router.post("/api/sourcing/links/{link_id}/confirm")
def api_confirm_link(link_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.confirm_link(ctx.store, link_id)


@router.get("/api/sourcing/groups")
def api_groups(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.groups_view(ctx.store)


@router.post("/api/sourcing/revisions/prepare")
def api_prepare(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.prepare_revision(ctx.store)


@router.post("/api/sourcing/revisions/{revision}/publish")
def api_publish(revision: int, ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.publish(ctx.store, revision)


@router.post("/api/sourcing/revisions/publish-latest")
def api_publish_latest(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.prepare_and_publish(ctx.store)


@router.get("/api/sourcing/revisions")
def api_revisions(ctx: Ctx = Depends(get_ctx)) -> dict:
    rows = repository.list_revisions(ctx.store)
    return {"count": len(rows), "revisions": rows,
            "live_revision": repository.live_revision_id(ctx.store)}


@router.get("/api/sourcing/live")
def api_live(ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.live_view(ctx.store)


@router.get("/api/sourcing/impact")
def api_impact(source: str, metric: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.impact_view(ctx.store, source, metric)


@router.get("/sourcing")
def page_sourcing(request: Request, ctx: Ctx = Depends(get_ctx)):
    payload = {"overview": None, "live": None}
    try:
        payload["overview"] = service.overview(ctx.store)
        payload["live"] = service.live_view(ctx.store)
    except errors.MetricregError as exc:
        payload["notice"] = f"{exc.code} {exc.message}"
    return render(request, "sourcing.html", batch=None, page="sourcing",
                  payload=payload, page_title="多来源同名指标合并")
