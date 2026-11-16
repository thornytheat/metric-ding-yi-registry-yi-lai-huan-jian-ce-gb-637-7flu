"""metricreg 全局配置：字段登记表、聚合白名单、诊断优先级、版本区间边界。

规则唯一来源，其他模块只读：
- 指标定义一行一条，换行只按 "\\n" 切分，CRLF 归一但不合并逻辑行；
- 字段名一律小写，别名表把历史列名映射到登记名；
- 版本有效期是半开区间 [valid_from, valid_to)，valid_to 留空表示未关闭；
- 数值配置一律 Decimal 字符串，不落 float。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "metricreg/1.4.0"
DECIMAL_QUANT = Decimal("0.01")

STAGES = ("ingest", "syntax", "diagnose", "graph", "version", "impact", "retire",
          "sourcing")
STAGE_LABELS = {
    "ingest": "接入",
    "syntax": "语法",
    "diagnose": "诊断",
    "graph": "依赖图",
    "version": "版本",
    "impact": "影响面",
    "retire": "下线",
    "sourcing": "多源合并",
}
STAGE_FLOW = "接入 → 语法 → 诊断 → 依赖图 → 版本 → 影响面 → 下线 → 多源合并"

# 聚合函数白名单：名称 → (返回类型, 说明用中文短名)
AGGREGATORS = {
    "SUM": ("decimal", "求和"),
    "AVG": ("decimal", "平均"),
    "MIN": ("decimal", "最小"),
    "MAX": ("decimal", "最大"),
    "COUNT": ("int", "计数"),
    "COUNT_DISTINCT": ("int", "去重计数"),
}
# 中文聚合写法归一为登记名（录入口语化的定义很常见）
AGG_ALIASES = {
    "求和": "SUM",
    "合计": "SUM",
    "平均": "AVG",
    "均值": "AVG",
    "最大": "MAX",
    "最小": "MIN",
    "计数": "COUNT",
    "去重计数": "COUNT_DISTINCT",
}

# 字段登记表：登记名 → 类型 / 单位 / 历史别名
FIELD_REGISTRY = {
    "order_amt": {"type": "decimal", "unit": "元", "aliases": ("order_amount", "订单金额")},
    "refund_amt": {"type": "decimal", "unit": "元", "aliases": ("refund_amount", "退款金额")},
    "cost_amt": {"type": "decimal", "unit": "元", "aliases": ("cost_amount", "成本金额")},
    "qty": {"type": "int", "unit": "件", "aliases": ("quantity", "件数")},
    "visitor_cnt": {"type": "int", "unit": "人", "aliases": ("visitor_count", "访客数")},
    "order_id": {"type": "string", "unit": "", "aliases": ("order_no", "订单号")},
    "store_id": {"type": "string", "unit": "", "aliases": ("shop_id", "门店编码")},
    "channel": {"type": "string", "unit": "", "aliases": ("channel_name", "渠道")},
    "category": {"type": "string", "unit": "", "aliases": ("category_name", "品类")},
    "city": {"type": "string", "unit": "", "aliases": ("city_name", "城市")},
    "order_date": {"type": "date", "unit": "", "aliases": ("biz_date", "业务日期")},
}
# 可 group by 的维度：字段登记表的子集
DIMENSIONS = ("store_id", "channel", "category", "city", "order_date")

# 编码回退链：gb18030 是 gbk/gb2312 的超集
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")

# 空值哨兵：集中定义，三态分开（哨兵 / 0 / 空串），哨兵不能参与计算
SENTINELS = frozenset(
    {"", "-", "--", "—", "/", "n/a", "na", "null", "none", "nil", "nan",
     "未知", "无", "暂无", "待定", "不适用", "未取数"}
)

# 诊断优先级：同一行命中多条时按此顺序排，规则书写顺序不影响结果
DIAG_ORDER = (
    "E_DECODE_FAILED", "E_EMPTY_INPUT", "E_EMPTY_BLANKS", "E_EMPTY_COMMENTS",
    "Q_NO_ASSIGN", "Q_NAME_INVALID", "Q_EXPR_EMPTY", "Q_PAREN_UNBALANCED",
    "Q_WHERE_INVALID", "Q_GROUP_INVALID", "Q_TOKEN_EXTRA", "Q_AGG_ARITY",
    "Q_AGG_UNKNOWN", "Q_SENTINEL_VALUE", "Q_FIELD_UNKNOWN", "Q_DUP_LINE",
    "C_DUP_NAME_BLOCK", "C_DUP_NAME_REVIEW", "C_DUP_NAME_INFO",
    "G_SELF_REF", "G_CYCLE", "G_REF_UNKNOWN", "G_DEP_UNDECLARED",
    "G_DEP_DECLARED_UNUSED",
    "V_INTERVAL_OVERLAP", "V_INTERVAL_INVERTED", "V_STALE_SUBMIT",
    "X_EMPTY_SELECTION",
    "R_NO_TARGETS", "R_TARGET_UNKNOWN", "R_CYCLE_BLOCKED", "R_NO_REPLACEMENT",
    "R_VERSION_AMBIGUOUS", "R_REPLACEMENT_PARTIAL", "R_VERSION_NONE",
    "R_REPORT_DANGLING", "R_REQUEST_DUPLICATE", "R_RING_MERGED",
    "R_ISOLATED_SKIPPED",
    "S_SOURCE_UNKNOWN", "S_SOURCE_PARSE_FAILED", "S_LINK_UNKNOWN_MEMBER",
    "S_LINK_DUPLICATE", "S_LINK_PENDING_ISOLATED", "S_LINK_CONFIRMED",
    "S_GROUP_CANONICAL", "S_MEMBER_ISOLATED", "S_REVISION_STAGING",
    "S_REVISION_PUBLISHED", "S_CUTOVER_ATOMIC", "S_NO_PUBLISHED",
    "S_REPLAY_NO_NEW_NODE",
    "B_IDEMPOTENT_REPLAY", "E_BOM_STRIPPED",
    "E_CRLF_NORMALIZED", "E_CHARSET_FALLBACK",
)

# 下线计划：迁移顺序与环计数规则，页面、导出、测试共用同一份说明
RETIRE_ORDER_RULE = "依赖方先迁，被依赖方后下线"
RETIRE_RING_RULE = "一个强连通分量只计一个环，环键取成员集合的规范轮转"
RETIRE_BLOCK_LEVELS = (
    ("R_CYCLE_BLOCKED", "环内", "指标在依赖环内，先后次序无法确定"),
    ("R_NO_REPLACEMENT", "无替代", "没有等价替代，且仍有报表引用"),
    ("R_VERSION_AMBIGUOUS", "版本重", "同名指标多个版本同时生效"),
)

# 同名冲突等级：block 阻断 / review 复核 / info 记录
CONFLICT_LEVELS = (
    ("BLOCK", "阻断", "聚合方式不同，同名指标无法共存"),
    ("REVIEW", "复核", "过滤条件不同，需业务确认取哪一个"),
    ("INFO", "记录", "仅分组维度不同，下游拼装需知会"),
)
CONFLICT_LEVEL_LABELS = {key: label for key, label, _ in CONFLICT_LEVELS}

# 表达式运算符优先级：数值只是在语法层使用，本项目不计算指标值
OP_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}

# 版本区间：valid_to 留空视为未关闭，用哨兵日期参与比较
OPEN_END = "9999-12-31"

# ---- M8 多来源同名指标合并 ----
# 来源优先级：数字越小优先级越高；合并链路的代表元永远取优先级最高（数字最小）的来源。
# 顺序只在这里定义，模块其余部分只读，换来源顺序结论随之确定且可复算。
SOURCE_PRIORITY = (
    ("bi_hq", "总部 BI", 10),
    ("bi_store", "门店 BI", 20),
    ("bi_channel", "渠道 BI", 30),
    ("bi_third", "三方 BI", 40),
)
SOURCE_LABELS = {code: label for code, label, _ in SOURCE_PRIORITY}
SOURCE_RANK = {code: rank for code, _, rank in SOURCE_PRIORITY}

# 等价关系确认状态：未确认先隔离，确认后才进合并链路。
LINK_PENDING = "pending"
LINK_CONFIRMED = "confirmed"
LINK_STATES = (LINK_PENDING, LINK_CONFIRMED)
LINK_STATE_LABELS = {LINK_PENDING: "待确认", LINK_CONFIRMED: "已确认"}

# 合并组成员状态：隔离（来源或关系未确认）/ 已进链路 / 代表元。
MEMBER_ISOLATED = "isolated"
MEMBER_LINKED = "linked"
MEMBER_CANONICAL = "canonical"
MEMBER_STATE_LABELS = {
    MEMBER_ISOLATED: "隔离",
    MEMBER_LINKED: "链路内",
    MEMBER_CANONICAL: "代表元",
}

# 现网版本发布状态：staging 草稿（未完成发布，继续展示旧版）、published 已全量切换、
# superseded 已被更新的发布版取代。读路径只认 published 的最新一版。
REV_STAGING = "staging"
REV_PUBLISHED = "published"
REV_SUPERSEDED = "superseded"
REV_STATE_LABELS = {
    REV_STAGING: "待发布",
    REV_PUBLISHED: "现网",
    REV_SUPERSEDED: "旧版",
}

# M8 固定规则文案，页面、导出、测试共用同一份说明。
SOURCE_PRIORITY_RULE = "代表元取来源优先级最高者：" + " > ".join(
    label for _, label, _ in SOURCE_PRIORITY)
SOURCE_ISOLATION_RULE = "同名指标在各自来源内独立成节点；未确认来源与未确认等价关系先隔离"
SOURCE_CUTOVER_RULE = "确认后整版原子发布并全量切换现网图与影响面；发布未完成继续展示旧版本结果"
SOURCE_IDEMPOTENT_RULE = "同一来源同内容重跑按内容摘要复用，不新增节点或报表"


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，避免读环境变量的分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "metricreg.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    eval_date: dt.date = dt.date(2026, 11, 15)
    rule_version: str = RULE_VERSION
    export_dir: str = str(PROJECT_ROOT / "var" / "export")

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("METRICREG_DB") or base.db_path
        sample_dir = os.getenv("METRICREG_SAMPLE_DIR") or base.sample_dir
        eval_raw = os.getenv("METRICREG_EVAL_DATE") or ""
        eval_date = base.eval_date
        if eval_raw:
            eval_date = dt.date.fromisoformat(eval_raw)
        return replace(cls(), db_path=db_path, sample_dir=sample_dir, eval_date=eval_date)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent

    def sample_path(self, name: str) -> Path:
        return Path(self.sample_dir) / name
