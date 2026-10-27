# Wi-Fi PE 按方向分工

基线：main / 2b45901。分支：codex/wifi-pe-direction-split。
上行指无线客户端进入 NI/FE；下行指 NI/ARM 发往无线客户端。

## 所有权

| 操作 | 执行者 |
| --- | --- |
| RXQ 捕获、RXBD 补环和消费者通知 | PE0 |
| RX 重排、PN/SN、NI 提交及接收缓冲回收 | PE0 |
| EPP 消费、PS 选包、WD/WP/TXBD 构造和发布 | PE1 |
| RPQ 消费、完成报告转交 ARM、BID/EQ 回收 | PE1 |
| RX 事件计数发布 | PE0 |
| RX/TX 事件刷入共享 timer4 | PE1 |

两个无线设备仍使用各自的上下文，同一 PE 轮转服务两个无线设备。
PE0 读取下行上下文中的 AP、peer、授权和 PS 状态；不转移下行队列所有权。
ARM 例外处理及其确认屏障保持原有语义。

原有 16 项 TX 暂存环改为 PE1 内部队列，最多保留 15 个选定 MPDU，
保存已经取得 WP/BID 所有权、但尚未取得 TXBD 信用的包。
保留已有选包和描述符构造接口、队列容量、载荷布局及信用计算。
RX_EVENT_CONTEXT 只供 PE1 通知 ARM，不再执行上行 worker。
删除跨核 RPQ 事件转递计数器。

暂停先关闭 ARM 入队，等待 ARM 请求被接收，再请求 PE1 暂停。
PE1 发布完内部待发队列后才确认；TXBD 无信用时保留队列并等待信用。
随后暂停 PE0。PE0 的确认不再依赖下行 TXBD。
解除暂停仍走现有 ARM 生命周期接口。复位、ATU、IMEM/DMEM 初始化不变。

## 原厂依据与设计边界

双 PE 的方向分工是本项目设计，原厂没有提供等价实现。
本次直接检查以下原始证据，未根据分工设计增加寄存器操作：

- 本机原厂内核恢复的 stock-kernel.elf：
  taurus_pe_reset_assert@0xffffffc01047cc10、
  taurus_pe_reset_probe@0xffffffc01047cdc0、
  taurus_pe_reset_deassert@0xffffffc01047d180。
  deassert 按核选择 GLOBAL_BLOCK_RESET 的 bit18/19 和 bit20/21，
  RAM 地址为 0x9030306c + core*8，清 bit5 后依次释放 engine 和 CPU。
  原生 pe-core.c 和启动汇编保持不变。
- 本机 rtk_wifi6.ko：
  hal_get_avail_txbd_8852ce@0x27cc8c 读取同一通道的读写索引；
  hal_update_txbd_8852ce@0x27cdb4 准备描述符；
  hal_trigger_txdma_8852ce@0x27c99c 从软件 producer 取值并调用寄存器写接口。
  保留信用检查及“描述符写入、sync、16 位 producer 发布”顺序。
  shared_sequence 测试还实际执行本机 C/XB 的 mac_get_wp_offset 函数，
  检查加密偏移和原生 WD/载荷。
- reference77c.bin 是已有参考 PE 镜像，不是本机双 PE Wi-Fi 固件：
  0xa80c 开始的指令依次写 timer4 LD、LOADE bit4、IE 和 CTRL。
  原生 pe_event_flush 保持已有操作顺序，由 PE1 单独调用。

原始输入 SHA256：

- stock-kernel.elf: cab2df66b920560b637b7ee98dac2a837370aa8ca8b23c56b4e9fbe706655d66
- rtk_wifi6.ko: 745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e
- reference77c.bin: fd1e244f340b3cb7904473d6ebc9658cc185add2dd76cc0e8f102b8a2249462b

反汇编摘录保存于 artifacts/pe-direction/factory/，私有原厂输入不纳入提交。

## 验证

生产 PE 构建通过，22368 字节，位于原有 32768 字节指令窗口内。
ARM64/musl rtl_ax_ap.ko 构建通过，保留已有 MODULE_DESCRIPTION 提示。
生成头、测试镜像和模块内嵌镜像逐字节一致。
未定义 PE_UPLINK 的既有调试构建也通过。

以下 14 项通过，日志位于 artifacts/pe-direction/：

- test_pe_tx_publication：六通道、回绕、队列满、共享 ARM/NI 信用；
  PE0 不写 TX，PE1 自行发布，暂停确认晚于最后一次发布。
- test_pe_rxq_backpressure：PE0 独立上行、PE1 独立 RPQ/EQ、
  单 timer 写入者、IRQ 未确认期间的新 RX 事件、514 个缓冲唯一所有权、
  重排、ARM 屏障、RDU 及 NI 保留尾部。
- test_pe_rpq_firmware、test_pe_downlink_gather、test_pe_epp_batch。
- test_pe_ps_budget、test_pe_ps_isolation、test_pe_shared_sequence。
- test_pe_ni_descriptor_credit、test_pe_radio_lifetime、test_radio_pe_waiters。
- test_pe_instruction_memory、test_pe_physical_reset、test_radio_rx_handoff。

旧 test_radio_tx_pause 和 test_radio_pause_queue 的 C 夹具缺少当前驱动字段/
RX PE 函数桩，在 main 与本分支均无法编译；此次不修改这些既有夹具。
暂停中的 PE 发布顺序和背压由更新的链接后 MIPS 测试直接验证。

复现核心检查：

```sh
export PYTHONPATH=/home/leo/PonWRT-TYFZA1/analysis/python-deps
PE_RADIO_CFLAGS=-DPE_UPLINK sh scripts/build_pe_radio_wd.sh artifacts/pe-direction
python3 scripts/test_pe_tx_publication.py artifacts/pe-direction
python3 scripts/test_pe_rxq_backpressure.py artifacts/pe-direction
python3 scripts/test_pe_rpq_firmware.py artifacts/pe-direction/pe-tx.bin
bash scripts/build_live_radio.sh "$PWD/artifacts/pe-direction/module" rtl_ax_ap
```

本 worktree 的内核构建目录是 main 构建树的独立副本；仅复用已有工具链。
输出模块：artifacts/pe-direction/module/rtl_ax_ap.ko。
PE SHA256：0acae06a44ff98676a38a4126cc89ae6a883c5dfa62bfafac81411c06876bf46。
模块 SHA256：af7f75b24d579d895ee6e4ec37ca1ba8b2b850738937f15399f7643c9137f613。

未构建完整固件，未加载设备、刷写或实测吞吐。
指令仿真不能证明真实总线时序、射频性能或双向负载表现。
下一步实机验证包含双频分别及同时双向传输、关联/去关联、PS、重启和卸载，
核对 RPQ/EQ 与 RX/NI 所有权及错误计数。
