# 门店 BI 指标：与总部同名，但口径带门店过滤；确认等价前必须独立、互不影响
销售额 = SUM(order_amt) - SUM(refund_amt) where channel = 线下 group by store_id,order_date
客单价 = AVG(order_amt) where channel = 线下 group by store_id
门店销售额 = SUM(order_amt) where channel = 线下 group by store_id
退款率 = SUM(refund_amt) / SUM(order_amt) group by store_id
