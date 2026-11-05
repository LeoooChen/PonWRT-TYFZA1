# 外部 2.5G 对端验证与短 ACK 聚合

2026-11-04，Mac Wi-Fi 192.168.1.186（en0）与 Windows RTL8156BG 2.5GbE 192.168.1.159 互测；AP 只转发，不运行流量生成。管理流量走 Mac en5 192.168.1.172。TCP 使用 iperf3 默认单流、默认窗口和块长，仅指定地址、接口、JSON和反向选项。

NI 原先看到短 ACK 就立即发送，约六万个 ACK 几乎各占一个 MPDU。原厂 `rtw_xmit_shortcut.c` 的 TXSC 软件队列会成组并通过定时器释放。改为最多等待 4 ms 或同 peer/TID 满 8 个短帧；包长超过 128 字节、休眠授权、退役清理均不等待。原始 ACK 全部保留，不合并、不丢弃。

外部对端实测，原先上传约 1.37 Gbps，短 ACK 聚合后多次达到 1.67–1.70 Gbps，包含零 TCP 重传轮次。`ONEPEER-up` 接收 1.700 Gbps、零重传。Windows USB 网卡接收缓冲已在对照测试后从 6/37 调整至 64/256；这是对端配置，不能算固件修改收益。下载仍约 1.48–1.49 Gbps，UDP 一轮上传 1.846 Gbps（0.135%丢包）、下载 1.810 Gbps（2.079%丢包）。整体验收尚未完成，尤其默认 TCP 下载稳定超过 1.5 Gbps。

验证：实际链接 PE 指令测试短帧期限、8帧立即聚合、TSF回绕、其他peer继续发送、退役和PS授权绕过等待；EPP批量/几何/PS隔离、RPQ回收、双PE提交和payload保留测试通过。生成头与实际验证字节码对应。用 en0/en5 收发字节核对流量路径，不能只用可能在NI重建时清零的累计计数判断。

后续排查发现 EQ6 可用2492个BID，124上限是WP/MPDU；`qm_state` 的no_buffer/rxcount是读清窗口计数。外部下载突发可能耗尽EQ6。RPQ批次、大包等待、缺缓冲抑制等实验未纳入本次短 ACK 修改；实验记录保存在本机 artifacts/wifi-wired-peer-20261104/INVESTIGATION.md。


## 大包重试与省电授权正确性

原厂 `txsc_apply_sc_cached_entry`（板载模块反汇编0xef624及参考源码3710行）对超过2300字节的大包设置 `DATA_RTY_LOWEST_RATE`。当前WD此前没有该字段。ARM与PE现在按HT/VHT/HE制式设置0x80/0x100/0x180，写入WD字节36的bits24:16；小包保持0，固定速率仍禁用fallback。测试覆盖2300/2301边界、各制式及实际NI七子帧路径。有效A/B的一对默认下载为ON1.483/OFF1.454 Gbps，实际Mac固件重试占比约6.07%/7.68%；其他轮次波动较大，不认定已解决吞吐上限。

另修复未消费的省电授权跨wake/sleep周期保留：wake与grant发布使用同一锁，按既有版本协议撤销flags；PE在分配owner前再次确认版本。修复前ARM状态与真实PE选择两类回归均复现旧授权发送，修复后旧授权和旧快照均被拒绝，新授权只释放一次；版本回绕、旧完成与新周期、并发发布、PS隔离和RPQ所有权检查通过。它纠正授权语义，不把全局retired计数等同于Mac误睡眠。

旧 `test_pe_wd_firmware.py` 在本次运行中因测试夹具缺少 `rtl_ax_io` 定义而无法编译；临时补齐后又遇到现有PE内存映射不匹配。未改动该旧测试；实际链接PE的 `test_pe_host_headers.py`、`test_pe_tx_publication.py`、`test_pe_rpq_firmware.py` 以及原生编码测试完成本次验证。
