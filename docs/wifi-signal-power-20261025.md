# TYFZA1 弱信号与 30 dBm 功率设置

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
尚不能以离线结果证明所有用户的弱信号都已恢复。

## 0～30 dBm 的真实设置路径

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

没有操作另一位 AI 正在使用的实机，也没有刷写、推送或发布固件。
模块保存在本 worktree 的 `artifacts/wifi-signal-module/rtl_ax_ap_signal.ko`。
下一步需在设备空闲后进行同机、同位置、同信道的前后 RSSI/TSSI 和距离对比；
绝对 30 dBm 射频输出还需校准功率测量，不能从配置值或寄存器读回直接宣称。
