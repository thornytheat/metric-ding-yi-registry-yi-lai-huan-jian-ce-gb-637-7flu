# 样例数据

| 文件 | 行数 | 真实问题 |
|---|---|---|
| metrics_dirty.dsl | 见文件 | UTF-8 BOM、CRLF 与 LF 混用、全角逗号与全角括号、别名列名、空值哨兵、括号不配对、多余记号、聚合白名单外写法、未注册字段、自引用、2 环与 3 环、同名不同定义、U+2028 分隔符 |
| metrics_clean.dsl | 11 | 无问题，用于对照同一代码路径 |
| metric_versions.csv | 10 | 版本有效期重叠（销售额 v1/v2）、起止倒挂（毛利额 v2）、未注册指标 |
| report_usage.csv | 10 | 报表引用未注册指标（R-10） |

脏样例与干净样例走同一条解析链路，没有任何特判分支。

## sourcing/ 多来源同名指标（M8）

| 文件 | 内容 |
|---|---|
| sources.csv | 四个来源与各自 DSL/报表文件的清单，来源优先级在 `config.SOURCE_PRIORITY` 登记 |
| bi_hq.dsl / bi_store.dsl / bi_channel.dsl / bi_third.dsl | 四个 BI 各自的同名指标，口径（过滤/分组）不同，身份键是 `(来源, 指标名)`，来源内独立 |
| *_reports.csv | 各来源自己的报表台账，影响面只计本来源已解析报表 |
| equivalences.csv | 跨来源等价关系：4 条已确认（连成链路）、1 条 `pending`（总部↔三方销售额，确认前三方先隔离） |

代表元永远取来源优先级最高的总部 BI；`POST /api/sourcing/sample-bundle` 一次性幂等载入，
确认 pending 关系并重新发布后，三方销售额才进入现网图与影响面。

