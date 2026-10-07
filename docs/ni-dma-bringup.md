# 原生 NI/DMA 网口移植

2026-10-08，完整 OpenWrt main r0-79bc56c / Linux6.18.55 / GCC14.4.0 已在TYFZA1实机运行原生NI/DMA网口。最新RAM FIT位于artifacts/full-ni-pon-base-6.18.55/ram-pon/image.itb，SHA256为bdcf7be27718d6279fff6c9dcf41fbcc0da05d59791b81fcd8dfd1c685d6af21。

## 实机结果

- 100000个1400-byte UDP包，10000pps：全部收到，零缺号、重复、错包。
- 16MiB随机数据：Mac→光猫0.322s（约417Mbps），光猫→Mac0.457s（约294Mbps），双向SHA256一致，cmp=0。
- TCP校验和错误0，驱动rx_errors/rx_dropped均0，实际GIC116中断计数52291。
- 增量验证内核另通过1000个MTU1500 ping（零丢失、均值0.557ms），重开网口后协商完成仍可双向ping。
- 完整主线的DHCP向Mac en5分配192.168.1.170；LuCI登录HTTP302、已认证Overview HTTP200。

原始证据在logs/main-native-*、logs/ni29-*，结构化结果在manifests/ni-tests.json。

## 原生驱动

rtl9617c-dma.c使用Linux DMA API分配VP8/TXQ0的64-entry发送环和缓冲，LSO参数8bytes、HeaderA8bytes，以dma_wmb发布描述符；保留原厂两项预取距离。NI管理口mux=15，普通LAN/L2TM调度使能。

rtl9617c-qm.c提供CPU0的8个1024-byte EPP64环，独立EQ4/EQ5各256个2048-byte缓冲。驱动通过CPU16入口接收，以NAPI解析HeaderA/CPU头并送入Linux网络栈，先退役EPP，再按硬件已释放BID数量归还缓冲。接收中断为GIC SPI84 / INTID116；发送回收使用1ms定时器。

rtl9617c-xram.c负责netdev、phylib、打开/关闭与诊断接口；XRAM管理通路也保留了已通过实机的实现。PON阶段继续扩展独立队列和接口。

## 本轮解决的根因

早期16MiB接收需47..76s，并出现TCP校验和错误。AF_PACKET抓包发现报文后半部分被下一段TCP数据覆盖；带序号UDP在10000pps出现约一半重复，XRAM对照则无重复。

EPP跟踪进一步证明不同命令指向同一个RAM地址；BID表中BID0被反复覆盖，池计数超过配置容量。补齐原厂Linux的TQM复位、清空未使用EQ并重载全部EQ后，缓冲地址和包序号恢复正常。

突发流量又触发满环：EPP_STATUS0置位但读写指针相等。驱动现在把该状态作为满环处理，避免错误判为空环导致缓冲耗尽。打开网口时也先退役旧EPP位置，消除了preinit切换后的无效接收。

AXI reorder、缓存映射、EPP等待、双池、CRC/低延迟等对照保留在manifests/ni-tests.json；最终硬件初始化和满环处理带来的结果以上述实机数据为准。
