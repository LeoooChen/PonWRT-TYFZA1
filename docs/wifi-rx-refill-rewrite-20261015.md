# RX 补环与缓冲生命周期重写

2026-10-15。本次只修改无线 RX/PE 及相关定向验证；没有操作光猫，没有修改另一会话的 XG-PON 文件。

## 行为变化

原先一个 ARM 例外包未确认，就会同时阻止后续 RXQ 消费和物理 RXBD 归还。现在按本机原厂 query_idle_rx_buf → rtw_hal_update_rxbd → enqueue_busy_rx_buf → rtw_hal_notify_rxdone 的所有权模型，将物理补环和载荷处理拆开：

- PCIe 收到完整 tag 后，将原缓冲交给工作环，从空闲池取另一缓冲写入 RXBD，再发布硬件索引。ARM 例外未确认时，这条补环路径仍继续运行。
- 工作环保持接收顺序；PN/SN、重排序和 PS 状态仍等 ARM 确认后才继续处理，不能被后续快路径超越。
- ARM 使用工作项中保存的缓冲 ID，不再通过已补充的物理 RXBD 找旧载荷。每个 RXQ 载荷独立分配 12288 字节 DMA 缓冲，并保存自己的 CPU/DMA 地址；PE mailbox[80] 指向 514 项 DMA 地址表，读取、补环和回收均查表。停启时物理 slot-ID 映射由 PE 交回 ARM。RPQ 仍为连续的 256×1024 字节。
- NI 尚未消费的载荷不归还；末两项预取引用继续独立保留，经过两个后续消费位置后才回空闲池。
- 停止时有序交付已捕获但尚未处理的载荷，再完成工作环回收；未消费的 NI 描述符返回 EBUSY 并保留所有权。恢复时保留空闲池和 NI 尾部，并沿用现有的 NI 序号 epoch 转换。
- 换频道时给已经捕获的工作项保存旧频道标志，避免软件队列中的旧 DFS 报告被当作新频道报告。

物理 RXBD 仍为 256 项。工作环独立使用 256 项，保留一项区分满/空；工作环满时停止取新包，已捕获载荷保持原样。载荷池为 514 项，即物理环、工作环和两项 NI 尾部的容量合计。每项仍为 12288 字节；相较旧 258 项池，每颗无线芯片增加 3 MiB 的有效载荷容量。独立分配在当前 4 KiB 页内核上每项需 order2（16 KiB）页块，514 项的载荷页块占用约 8.03 MiB；实际分配开销不能仅按有效载荷容量估计。空闲、物理描述符、工作环和 NI 尾部中的缓冲 ID 必须互斥并覆盖整个池。

## 连续分配问题及修正

首个候选模块 b543f8ff9071f6390d47ef6eb70127b70328da826a84d2d29a26990d5b6b7b07 被另一实机会话加载时，514×12288 字节的整池 dma_alloc_coherent 申请触发 order11，而当前内核 CONFIG_ARCH_FORCE_MAX_ORDER/MAX_PAGE_ORDER 为10，两颗无线 probe 均返回 ENOMEM。该候选已作废。现场恢复旧模块后的 MemFree 为343520kB，因此这不是总可用内存耗尽。

修正版保持514项池和256项物理环，按每个载荷独立分配；RX 队列最大的单次 DMA 申请为 RPQ 的262144字节。分配过程中失败则释放已分配项并返回原错误，没有缩池、重试大分配或旧路径 fallback。

原厂 _phl_alloc_dynamic_rxbuf_pcie@0x193420 的实际执行记录确认，每次分别申请12288字节并映射DMA，各 rxbuf 对象独立保存CPU地址、DMA地址和长度，两个非连续DMA地址均正确保留。原始记录：现场记录/20261015-160mhz-live/stock-rx-allocation.json；失败串口记录：同目录 console-rx-pool.log。

## RDU 计数

PE 拥有 RX 时直接采集 ISR bit19（RXQ RDU）和 bit20（RPQ RDU），分别计数，再仅以 W1C 确认观察到的位。RPQ 完成处理只确认 bit2，不再先清除尚未计数的 bit20。

ARM 按无符号 32 位增量累计到 64 位计数，处理计数回绕，并在 PE 停启交接前收齐最后一次计数。原有 rdu 汇总加入 PE 计数；诊断输出同时提供 PE_RXQ_RDU、PE_RPQ_RDU、空闲数和工作环的写入/处理/回收位置。RDU 计数表示观察到的缺描述符原因，不是丢包数。

## 离线验证

在 Ubuntu 仓库根目录运行：

```sh
PE_RADIO_CFLAGS=-DPE_UPLINK sh scripts/build_pe_radio_wd.sh "$PWD/artifacts/rx-refill-rewrite-20261015/pe-individual"
python3 scripts/test_pe_rxq_backpressure.py artifacts/rx-refill-rewrite-20261015/pe-individual
python3 scripts/test_rx_pool_handoff.py
python3 scripts/test_rx_dma_alloc.py
python3 scripts/test_pe_rpq_firmware.py artifacts/rx-refill-rewrite-20261015/pe-individual/pe-tx.bin
```

已通过的针对性检查：

- 实际分配/释放函数：在4MiB单次申请上限模型下，514项非连续DMA地址与初始RXBD及地址表逐项匹配；7个针对性部分失败位置均无泄漏和重复释放，ASan/UBSan通过。
- 实际链接的 MIPS PE：ARM 确认阻塞时补充 255 个 RXBD；工作环满时保留载荷；确认后有序交付 256 个载荷，继续跨越 513 包环回。每次所有权检查均覆盖 514 个缓冲，未出现重复或丢失 ID；全部514项DMA地址均按打乱的非连续布局执行，物理RXBD地址与地址表逐项核对。
- 同一 PE：保护帧不能越过 ARM 例外更新 PN/SN；NI 最后两个载荷保持不变，NI end 序号 bit30 和 31 位回绕正确；RXQ/RPQ RDU 在 W1C 模型下分别计数，未清除其他状态位。
- 实际 ARM 函数：未消费 NI 的停止保留所有权；已捕获的旧 DFS 报告丢弃、新报告交付；频道 epoch 标记覆盖工作环回绕；NI 尾部跨停启及序号 epoch 保留；RDU 跨交接和 32 位回绕累计一次。
- RPQ：1024 个 PE MPDU、2048 次 BID 归还、ARM/EOSP 报告、516 字节原厂 DFS 满批报告及 RXQ/RPQ 旧/新频道边界通过。
- ARM64/OpenWrt musl 无线外置模块编译通过；模块内嵌 PE 与上述被验证二进制逐字节一致。

产物和机器可读结果位于 artifacts/rx-refill-rewrite-20261015。manifest.json 保存源码和产物 SHA256。旧 owner64/65/66 回放工具针对 258 缓冲布局，本次 514 缓冲布局使用上述验证入口。

原厂依据及改动前的实机证据见 artifacts/loss-root-cause-20261015/REPORT.md；原厂模块 SHA256 为 745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e。

验证中的 PCIe、ARM 确认、NI 消费和 W1C 时序由模型提供，不包含实际 DDR/PCIe 延迟、空口或操作系统调度。本会话没有执行全量测试、全量固件构建或设备操作。首候选的上机分配失败由另一会话反馈并已修正；当前独立DMA分配候选尚未由本会话上机验证，不能据此宣称probe或高速丢包已经修复完成。
