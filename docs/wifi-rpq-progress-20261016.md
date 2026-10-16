# WP完成回收与NI释放边界修复

## 原厂依据

固定使用本机原厂模块
`evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko`，
SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。
原厂执行工具在加载前核对该哈希。

`tools/check_stock_wp_dispatch.py` 实际执行 `phl_rx_pcie@0x197454` 和
`_phl_rx_handle_wp_report@0x197328`：普通RX通道没有空闲缓冲时，仍处理
另一通道的WP报告，调用payload回收，再回收报告缓冲和补环。资源查询、
单包取得、报告解码及回收/补环原语由显式回调提供。这证明原厂分流，
不声称原厂有本工程同样的ARM/PE交接FIFO。

`tools/check_stock_wp_reset.py` 实际执行 `phl_trx_reset_pcie@0x196698`
的TX重置分支，遍历占用的WP表，先为WP17、1023调用payload回收，再
清空TXBD。回收与HAL原语由回调提供，不模拟DMA停止时延。

## 两处根因及修改

1. `pe_rpq` 入口无条件要求ARM交接环有空位，导致纯PE完成报告也被阻塞。
   这些报告本可在PE内归还WP/BID，等待ARM反而使发送槽耗尽。现在仅在
   报告确实需要ARM空间时保留硬件RPQ：先扫描整个报告包，避免混合包中
   部分WP先完成、重试又完成一次。纯PE完成及旧频道DFS丢弃可继续；ARM
   完成、EOSP及C2H仍完整保留，不覆盖满环，也不丢弃通知。

2. `ni_test_release` 检查EPP与软件待发队列，却漏了已提交的WP。撤销失败
   后的强制卸载会再次调用此函数；空待发队列和一个未回收WP即可令旧函数
   返回成功并撤销NI映射，跳过后面的BID回收。现在WP仍在途或最后的BID
   仍等EQ归还时返回EBUSY，保留常规完成路径或已有强制停机回收路径。

## 验证与边界

实际链接的旧/新MIPS PE均执行相同满ARM环输入。旧PE不消费纯PE报告；
新PE消费并逐个归还两个BID，只完成一次WP。混合、EOSP、C2H在满环时
不修改缓冲或完成状态，释放一个ARM槽后正确交付。原有RPQ定向验证的
1024个MPDU、2048次BID归还、跨环及部分EQ归还也通过。

实际原生NI释放函数的旧/新反例确认：未回收WP不再被正常释放跳过；
部分归还、超时后剩余链和重试归还验证通过。

仅编译外置ARM64无线模块，内嵌PE与受测二进制逐字节一致，模块输入与
当前无线源码一致。MODPOST保留已有缺少MODULE_DESCRIPTION提示。

本次没有设备操作、实机复测、全量测试、全量固件构建或推送。没有调整
速率、160MHz、队列容量或报告阈值。混合报告仍需要等ARM空间，不能把
这次改动解释为消除了所有反压；上述反例也不证明此前每个实机丢包都由
这两处造成。PON改动由用户要求原样保留，不包含在本次Wi-Fi提交中。

证据与产物哈希见 `manifests/wifi-rpq-progress.json`。
