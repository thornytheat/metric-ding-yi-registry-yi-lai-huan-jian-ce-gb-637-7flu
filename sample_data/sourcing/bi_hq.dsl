# 总部 BI 指标（优先级最高，合并链路代表元来源）
销售额 = SUM(order_amt) - SUM(refund_amt) group by store_id,order_date
毛利额 = SUM(order_amt) - SUM(cost_amt) group by store_id
客单价 = AVG(order_amt) group by store_id
订单数 = COUNT(order_id) group by store_id,order_date
门店销售额 = SUM(order_amt) group by store_id
