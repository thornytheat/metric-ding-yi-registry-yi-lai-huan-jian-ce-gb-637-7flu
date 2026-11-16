# 三方 BI 指标：外部补数，等价关系尚未确认，同名指标先隔离不进现网
销售额 = SUM(order_amt) group by city,order_date
外部热度 = COUNT_DISTINCT(visitor_cnt) group by city
