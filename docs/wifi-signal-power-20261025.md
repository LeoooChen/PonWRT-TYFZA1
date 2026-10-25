# TYFZA1 弱信号修复与历史功率试验

当前 main 已撤销 30 dBm 设备上限、CN 功率上限覆盖和超出原厂 ByRate
表值的额外抬升。功率目标恢复为原厂表值与 Linux 国家域、用户上限取小值。
恢复原厂可选限幅表开关的弱信号修复继续保留；调制回退、模式偏移、校准、
TSSI、DIG/EDCCA 和收发队列修复均未撤销。以下 30 dBm 设置与实机数据是
回退前的历史记录，不代表当前 main 仍提供功率解锁。

## 已确认的功率损失

main 的 `rtl_ax_power_apply()` 强制开启 D208[21:20] 和 D21C[18]，
使两颗芯片在 ByRate 目标之外，再受普通/RU 厂商限幅表约束。
本机原厂 `rtk_wifi6.ko`（SHA256
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`）
的默认策略关闭这组可选表。

执行原厂 `phl_sw_cap_init`、`rtw_core_update_default_setting`、
`rtw_hal_final_cap_decision` 和 `halrf_get_efuse_power_table_switch`
后，2G/5G 的 `pwr_lmt_en` 都为 0。另执行
`set_rtw_mib_default_tbl`，得到 `power_limit`（adapter+0x9824）默认 0；
原厂 `core_mib_power_limit` 同样将它作为限幅开关。
这一结论不依赖把 `HW_BOARD_OPTIONS` 猜作最终策略输入。

生产解析器读取构建所用的实际 RFE54/FEM 表，CN、请求 23 dBm 时：

| 无线 | OFDM6 ByRate | 原强制启用的 1T/2T OFDM 限幅 |
| --- | --- | --- |
| RTL8852C / RTK66587_BPF，信道 36 | 23 dBm | 12.5 / 11.5 dBm |
| RTL8192XB / RTK66287，信道 6 | 23 dBm | 15 / 12 dBm |

修复恢复原厂可选表开关状态。表仍按原顺序加载、构造；Linux 国家域和用户
功率上限继续作用在实际 ByRate 寄存器上。这消除一处可重现的软件功率损失，
后面的 RAM 对比进一步确认了该机双频的实际信号改善。

## 历史：0～30 dBm 的设置路径（已撤销）

按用户要求保留 1 dBm 步长，注册信道的设备功率上限设为 30 dBm。
cfg80211 再与当前监管域取交集。现有 iwinfo/LuCI 从内核的信道功率上限
生成档位，本次没有修改前端显示。

原厂 FEM ByRate 最高分别只有 24 / 25 dBm，单纯把上限数字设为 30
无法提高发送目标。驱动现在对超过原厂峰值的请求增加相应 ByRate 偏移，
保留原厂调制速率间的回退，再编码为 TPU 的半 dBm 值。
30 dBm 对应 OFDM6 字段 60；两颗芯片的 HE MCS11 仍保留 7 dB 回退。
低于原厂峰值的请求沿用原有逐速率限幅逻辑。

构建使用的 `wireless-regdb-2026.09.03` 中，CN 的 5725～5850 MHz
规则为 33 dBm，因此信道 149 等高频信道经过设备上限后可设置到 30 dBm；
CN 的 2.4 GHz 为 20 dBm，5150～5250 MHz 为 23 dBm，
5250～5350 MHz 为 20 dBm。驱动不覆盖这些内核限制。

## 验证与待测

- `scripts/test_radio_power_policy.py` 执行上述原厂 ARM64 函数，并编译生产
  `limits.c`、`power-tables.c`、mac80211 config/get_txpower 回调。
  通过实际表文件，检查双频全部支持带宽、CN/world/US 和每个 0～30 dBm
  整数档，共 558 例；还检查切换后硬件表、读回、不相关寄存器位和旧限幅位。
- 原 main 在相同测试的限幅开关检查中失败。仅恢复原厂开关、未增加 ByRate
  提升的中间版本，在 5G 的 25 dBm 档失败，证明测试能发现“只改显示上限”。
- 既有 20/40/80/160 MHz TPU/RF/ADC、240 个 VHT 和 72 个 HE160 WD
  对照通过。测试使用替代 MMIO，不是射频测量。
- Linux 6.18.55 ARM64 无线模块编译通过；保留原有
  `MODULE_DESCRIPTION` 的 MODPOST 提示。

## 2026-10-25 RAM 实机对比

用户释放实机后，先将 main 的新增提交合入修复分支。测试基线为
`f8f05cc`，两模块都包含该 main 的 PS-Poll 和共享 TXBD 容量修复。
实际编译源码只有 `monitor.h`、`power-tables.c`、`power-tables.h`
三个文件不同，PE 固件字节完全一致。

同一台光猫、Mac en0 和固定位置，CN，2G 信道 1/HE20、5G 信道
149/HE80，按旧→新→旧→新换 RAM 模块。全过程 boot_id 为
`a316c6d1-55d5-4bf2-94f7-dbae8672f53a`，没有重启或刷 Flash。

| 设置均为 20 dBm | 第一轮旧→新 RSSI | 第二轮旧→新 RSSI | 改善 |
| --- | --- | --- | --- |
| 2.4 GHz | −28→−18 dBm | −28→−19 dBm | 9～10 dB |
| 5 GHz | −35→−29 dBm | −37→−33 dBm | 4～6 dB |

2G 数据帧的硬件 TXINFO 从 12 dBm 变为 19～20 dBm；5G 随调制速率
从约 15.5～16 dBm 变为 17～20 dBm。旧模块回装后同时恢复较低功率和
较弱信号。两次修复版 5G/30 dBm 的信标 RSSI 都为 −22 dBm。

原厂 `halbb_get_txinfo_txpwr_dbm_C/XB` 读取 BB1804[26:18] 有符号
9-bit 值，原厂 MP 调用者除以 4。测试通过 PCI `resource2` 只读映射
采样该真实寄存器，不新增强制发射、模拟读数或替换芯片校准。

25、26、27、28、29、30 dBm 六档均完成硬件表与当前值核验，OFDM6
半 dBm 编码逐档为 50、52、54、56、58、60。TXINFO 的 HE MCS11
报告也逐档上升，30 dBm 档通常为 23 dBm，符合保留的 7 dB 速率回退；
该档还实际采到 TXINFO=30 dBm 的发送。各档 10 Mbps UDP 下行零丢包，
双频各轮 12 秒下行也全部零丢包。两路 TSSI tracking 持续开启且未暂停，
周期 RF 状态 result=0、LCK timeout=0。

快速扫描会缓存前一档的 RSSI，不能据此声称每 1 dBm 都已完成空口标定。
另在同一关联连接、每档先等待 10 秒，再读取 200 个无零值的关联 RSSI：
30→20→30 dBm 的中位数为 −25→−33→−24 dBm。这个可逆变化证明
功率档位实际影响射频信号。收流时 CoreWLAN 返回的无效零 RSSI，以及
快速扫档的缓存 RSSI，均排除在改善结论之外。

UCI 的 `wireless.radio0.txpower=30` 经正常 `wifi reload radio0`
路径实际应用，`iw`、驱动读回及寄存器一致；实时 iwinfo RPC 的 5G 档位
为 0～30，2G 为 0～20。2G 请求 30 后仍实际为 20 dBm，符合 CN 规则。
没有修改监管域。30 dBm 的硬件目标、发送报告和空口响应已验证；绝对天线
输出是否恰为 30 dBm 仍需外接功率计标定。本次也未移动 Mac 做 10 米距离测试。

完整结构化记录及原始文件散列见
`manifests/wifi-signal-power-live-20261025.json`；Mac 原始记录为
`现场记录/20261025-wifi-signal/`。模块位于本 worktree 的
`artifacts/signal-ab-{baseline,fixed}/rtl_ax_ap_signal.ko`，修复版 SHA256 为
`e8ddd28894dc60fff274d49a608875f9eae14570a1358237db817ff8dce01b17`。

收尾保留修复版 RAM 模块及双频测试 SSID：5G 信道149/HE80/30 dBm，
2G 信道1/HE20/20 dBm。测试 iperf 服务已停止。UCI 测试设置仅暂存在
`/tmp/.uci`，`/etc/config/wireless` 与开始时的文件逐字相同；没有刷写、
推送或发布固件。
