# 渠道 BI 指标：同名指标按渠道口径登记，与总部/门店在各自来源内独立
销售额 = SUM(order_amt) - SUM(refund_amt) group by channel,order_date
渠道销售额 = SUM(order_amt) group by channel
退款率 = SUM(refund_amt) / SUM(order_amt) group by channel
访客数 = COUNT_DISTINCT(visitor_cnt) group by channel,order_date
