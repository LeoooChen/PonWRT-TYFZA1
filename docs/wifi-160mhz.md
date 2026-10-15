# TYFZA1 5 GHz 160 MHz：源码接入与离线验证

2026-10-15，基于本机原厂 `rtk_wifi6.ko`，SHA256 为
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。
本次源码增加 RTL8852C/RFE54/CV1/非 DBCC 的连续 160 MHz；0.3.1-alpha 已发布镜像不含这次修改。

频道配置从 nl80211 的 160 MHz 映射到原厂带宽值 3，贯穿 MAC、BB、RF、IQK、TSSI、功率表、固件通知、站点速率及 TX WD。原厂 `rtw_5ghz_a_channels` 的 28 个信道取代旧驱动只注册 36/40/44/48 的限制，由 cfg80211 施加监管域规则。80+80 MHz 没有启用。

原厂证据与实现对应：

- `halbb_get_txsc`、`cfg_mac_bw`、`halbb_ch_bw_notif`：八个主 20 MHz 位置、40/80 MHz 子信道选择，MAC 和 BBinfo 使用各自字段布局。
- `halbb_ctrl_bw_8852c`、`halbb_adc_cfg_8852c`：带宽值 3 及两条路径的 160 MHz ADC 时序；原厂 5 MHz 边缘掩码在此宽度关闭。
- `halrf_ctl_band_ch_bw_8852c`、`halrf_set_rxbb_bw_8852c`：RF18 的独立 160 MHz 位组合和 RXBB LUT 值 3。
- `_iqk_rxk_setting_8852c`：RX 时钟 selector 3 和控制字段 2。TX/RX IQK 命令保留原厂按带宽生成的格式。
- `halrf_get_power_limit_to_struct_160m_8852c` 及 RU 构造器：八个 HE20、四个 HE40、两个 HE80、HE160、两个特殊 HE40 最小值，以及三类 RU 的八列功率。
- `halrf_tssi_set_efuse_to_de_8852c`：RFE54 在 160 MHz 读取 BB1a8 的三个有符号 ADC 修正，加入 20/40/80 MHz 的 DE；160 MHz DE 不加该修正。其余 TSSI 固定初始化程序与原厂 20 MHz 提取结果一致。
- `halbb_set_pd_lower_bound_8852c`：频道切换时更新带宽相关检测门限；超出原厂可表示范围时按原厂关闭该门限。
- VHT/HE 能力、VHT160 短 GI、客户端独立 HE160 MCS 表以及普通 TX 的 160 MHz 标志已接通。RX、RA 反馈和 PE 的现有带宽字段本来就能表示值 3。

离线验证已经通过：

- `python3 scripts/test_radio_160.py`：八个主信道和固件通知与原厂一致；TSSI 正负 ADC 修正、IQK RX 时钟、带宽检测门限、独立 HE160/HE80 MCS、VHT160 GI、原厂信道表及最高速率字段。
- `python3 scripts/test_radio_wideband.py`：20/40/80/160 MHz 的功率、RF18、ADC 对照；240 个 VHT 和 72 个 HE160 WD 对照。WD 提取明确使用原生裸 WLAN 的零 WP_OFFSET 输入，不再沿用旧 PHL 预留值。
- `python3 scripts/test_pe_downlink_gather.py artifacts/radio-160 --160`：执行本次模块内嵌的实际 MIPS PE 程序，VHT160/HE160 的 EPP、WD、载荷及 RPQ/EQ 所有权通过。
- `bash scripts/build_live_radio.sh "$PWD/artifacts/radio-160" rtl_ax_ap_160`：ARM64/musl 模块编译通过。产物是开发模块，不是新发布固件。

硬件 IO、校准查询和完成状态在离线执行中由模型提供；以上不证明空口协商、实际吞吐或长期稳定性。当前没有实机。

**仍有明确的使用限制：现有 DFS 只有初始化参数，雷达检测、报告和 CAC 运行链尚未接入。需要 DFS 的 160 MHz 信道块仍不可用于 AP；只有实际监管域允许的非 DFS 完整信道块才可能启用。不得绕过 cfg80211 或伪造 CAC 成功。** 因此本次完成的是 160 MHz 射频与数据通道接入，不等于所有地区、信道的 160 MHz AP 已完整可用。

机器可读结果见 `manifests/radio-160-channel-tssi-contract.json`、`manifests/radio-wideband-contract-test.json`；原厂调用的完整输出保存在同目录的 `radio-stock-*160*` 及扩展后的带宽、RF、校准清单。
