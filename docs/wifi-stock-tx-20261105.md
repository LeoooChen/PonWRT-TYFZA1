# 原厂 WP 生命周期适配与下行测试（第一批）

基线 main 9331c6e1db0c4473a06d28da14795b20484cffc1；worktree `/home/leo/PonWRT-TYFZA1-pe-stock-tx-20261105`，分支 `fix/pe-stock-tx-20261105`。

原厂 ELF SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。实际 `alloc_txring@ddf6c` 请求池为 5G 2048 / 2G 1024；`get_txreq_buffer@e49fc` 只检查空池，没有新版 SDK 的保留八项分支。124 是 native 自有准入上限，不是原厂 WP 上限。

`phl_prepare_tx_pcie@196ae4`、`phl_trx_init_pcie@193928`、`phl_check_wp_tag_resource@196958`、`phl_recycle_payload@196314` 证明硬件 WP 由 DMA 通道和 tag 标识；DMA0 使用 free_wp FIFO，初始化包含 tag0，模4095（FFF保留），next-read==write 表示不可分配。其他通道独立扫描，回绕到1。原厂异常全占用强制回收不作为正常准入行为移植。

## 实现

软件请求 ID 与硬件 tag 分离；NI 请求池取原厂容量，ARM 保留当前独立256槽池。因此这是 PE 架构适配，不宣称与原厂共享 core 请求池完全同构。NI 回收全部 BID 后再返还 tag 和请求；ARM RPQ 转译回原软件 ID。独立 discard 槽不占硬件 tag。每通道 WD264页保留和 TXBD255信用维持原有正确实现；完成环扩大为4096项。仅 TX radio DDR context 扩至512KiB，执行器/IMEM/DMEM不扩大。

移除按前包长度和 peer/TID 变化设置 BK 的规则；保留非 A-MPDU BK。原厂软件额度 BK 默认禁用；没有加入任意 batch8/64 BK。

## 验证

实际链接 PE：5G6144次、2G5120次提交，分别1024/2048在途，乱序 RPQ、tag4094/0回绕、completion4096回绕、EQ无信用/部分回收通过；RPQ 硬件 tag 与软件 ID 不同的 ARM/NI/PS转译通过。六通道发布/信用、双PE/双radio隔离、大HE聚合、WD保留、PS预算、退站/key/BA等待相关回归通过。直接构建和 PE_UPLINK 构建通过，ARM64模块编译通过。高编号生命周期验证选128/1023/2047/discard边界；全枚举 sanitizer 开销过高的运行主动终止，不作成功证据。

最终 PE24644字节 SHA256 `ad271bb64b8f89f2480ca3890f1fb90b41ad613da513f84e549e8179976e7406`；模块 `b15145cb74e33a39960c65634fc3683c4c7e783e4ef0a7e18e3be8bdf9031668`。临时BK/124开关仅存artifacts诊断制品，最终源码无开关。

## 上机结果

Mac en6 2.5GbE→路由器→Mac en0 Wi-Fi160MHz，地址按接口绑定，经测试DNAT避免本机直达。默认单流TCP；UDP发送2G；接收端吞吐如下。无线跨轮波动及同机双接口拓扑不支持将细小差值视为稳定收益。

|条件|TCP 30秒 Gbps|UDP 60秒 Gbps|UDP丢包|
|---|---:|---:|---:|
|新生命周期，BK关，临时准入124|1.433|1.645|17.71%|
|同关联，恢复2048请求池|1.458|1.739|13.03%|
|清理后的最终模块|1.429|1.627|18.65%|
|恢复基线，有效NI快路|1.283|1.722|13.89%|

新生命周期各组未出现非空pending时RPQ长停，最终离场pending0、fault0。124准入实验最大pending124仍持续完成，说明本轮不冻结并非必须扩大同时在途数量；请求/标签复用周期仍不同，不能单独认定硬件tag快速复用就是根因。未达到TCP1.8/UDP1.9目标，不能声称完整修复。

BK同关联ON/OFF/ON 10秒：TCP1.324/1.171/1.416、UDP1.120/1.735/1.651；波动明显，无稳定TCP收益。此前一组有线lan1掉线12秒导致控制中断，缺失段排除。恢复基线的第一次60秒UDP走ARM而非NI，0.306Gbps排除，确认进入快路后重测为表中1.722。

原始数据、命令、1Hz队列样本、模块和诊断源码均在worktree `artifacts/`，汇总 `measurement-summary.json`。第一批结束时已恢复 RAM基线 `rtl_ax_ap_wdinit`，flash与main源码未改。用户后续扩大到全部Wi-Fi原厂细节对齐，继续在同分支分批推进；本提交是可回退的第一批，不是最终验收。

## 第二批：RPQ POH 默认回报

用户扩大原厂细节对齐范围后，将回报聚合从12恢复实际ELF的121，超时代码仍255。`tools/check_radio_report_config.py`执行本机原厂默认选择分支（agg_def/tmr_def及四种默认状态），再比较native写回；默认9414=`a5ff5a79`，重复配置不写寄存器，检查通过。

RAM模块rtl_ax_ap_stockrpr SHA256 `d1c680b345c1dca3dbaca05ddf6a926a468c86c4da95cdcdda5234106daebb6c`；实测单流TCP30秒1.250Gbps、UDP60秒1.520Gbps/23.97%丢包，完成持续前进。单独本项没有提速，保留为完整原厂策略组合的中间版本，后续须验证组合和NI池持有时间。
