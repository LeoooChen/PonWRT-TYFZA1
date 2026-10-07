# 原生 NI/DMA 移植

2026-10-08，接续已通过实机的 XRAM 管理 LAN。

TX 增加 rtl9617c-dma.c：Linux DMA API分配环和数据缓冲，VP8/TXQ0，64个描述符。原机scfg启用DMA_LSO_ACE_TEST，环和包使用CCI高位2；LSO参数和Header A按本机模块及GPL参考组织。描述符经dma_wmb发布，读指针回收。

实际结果：DMA读指针正常推进；关闭NI管理端口选择（port_to_cpu=15），启用普通L2TM/LAN调度，并改用LSO+Header A后，Mac抓包看到光猫的ARP报文。此记录证明DMA发送到网线，尚不证明完整NI双向业务。

RX 增加 rtl9617c-qm.c：CPU0/EQ4、8个1024-byte EPP64环、256个2048-byte缓冲。原生pool、refill、NAPI与Header A/CPU解析已实现。入口TCAM分类已命中（monitor=0x04001040），继续连接硬件CPU队列。端口映射明确区分：AAL_PPORT_CPU=9，AAL_PPORT_QM=8；后者是deep-Q接口。

增量载荷保存在artifacts/ram-dma-tx-*和artifacts/ram-ni-*，每份含Image/DTB/FIT哈希。实机状态见manifests/ni-tests.json。已有XRAM/LuCI载荷仍可通过本机U-Boot TFTP RAM启动。
