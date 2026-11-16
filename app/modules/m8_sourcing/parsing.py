"""M8 来源 DSL 解析：复用 M1/M3 的同一条解码与诊断路径。

多来源接入不另造解析器，避免同一行 DSL 在批次链路与多源链路里结论不一致。
来源内同名仍按 M4 的「保留行号最小」规则收敛，解析失败的行整体不进该来源注册表。
"""
from __future__ import annotations

from ... import manifest, reasons
from ..m1_ingest import decode as decode_mod
from ..m1_ingest import reader
from ..m3_diagnostics import service as diag_service


def parse_source_dsl(text: str) -> dict:
    """解析一个来源提交的 DSL 文本，返回指标、报表无关的结构与诊断。

    返回的 metric 结构与 m4_graph.builder 消费的 candidate 完全同形。
    """
    decoded = decode_mod.decode_bytes(text.encode("utf-8"))
    raw_rows, _ = reader.split_lines(decoded.text)
    inspect = diag_service.inspect_lines(raw_rows)
    error_lines = {d.get("line_no") for d in inspect["diagnostics"]
                   if d["level"] == reasons.ERROR and d.get("line_no")}
    return {
        "metrics": inspect["metrics"],
        "diagnostics": inspect["diagnostics"],
        "lines": inspect["lines"],
        "error_lines": sorted(error_lines),
        "encoding": decoded.encoding,
        "digest": manifest.digest_text(decoded.text),
    }


def parse_reports_csv(text: str) -> list[dict]:
    """来源内报表台账：与 M4 报表同形（report_id/report_name/metric/weight）。"""
    import csv
    import io
    from decimal import Decimal, InvalidOperation

    rows: list[dict] = []
    for raw in csv.DictReader(io.StringIO(text)):
        report_id = (raw.get("report_id") or "").strip()
        metric = (raw.get("metric") or "").strip()
        if not report_id or not metric:
            continue
        weight_text = (raw.get("weight") or "0").strip()
        try:
            weight = Decimal(weight_text)
        except InvalidOperation:
            weight = Decimal("0")
        rows.append({
            "report_id": report_id,
            "report_name": (raw.get("report_name") or report_id).strip(),
            "metric": metric,
            "weight": weight,
        })
    return rows
