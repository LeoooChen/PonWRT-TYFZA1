# TYFZA1 5 GHz 160 MHz：源码接入与验证

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
- `cfg_dfs`、`en_bbrpt`、`halbb_radar_detect_enable/disable`：MAC band0 报告阈值61、80ms 超时，以及 CLIENT BB0 bit31 雷达开关；停止报告的硬件超时直接返回错误。
- `mac_parse_dfs`、`halbb_radar_info_processing/seq_inspctn/ptrn_cmprn/chrp_mntr/radar_detect`：8字节头、8字节脉冲、FCC/ETSI/KCC、MKK W53/W56、160MHz 双段、序号和长脉冲累积，均使用本机原厂参数和判定行为。采用 `dfs_init` 初始阈值；原厂环境 watchdog 的自适应噪声抑制尚未接入。
- 保留 mac80211 模拟单频道上下文和软件扫描。补丁208确保同频点仅变更雷达开关时也调用驱动配置。监管域自动映射 FCC/ETSI/JP；不明监管域不能开启检测，`dfs_region=1..4` 保留明确覆盖入口。
- CAC 的时长、结束、雷达事件及 NOP 由当前 Linux6.18/mac80211/cfg80211 管理。CAC 期间关闭18个 MAC 调度队列，AP 启动才解除；雷达命中立即重新关闭调度和 beacon，并通知 mac80211。RFK 恢复、BSS beacon 更新和调试入口均不能解除该状态。
- 原厂61脉冲报告加 RXBD/WD 为516字节，原512字节 RPQ 无法容纳。ARM、PE、转发环统一改为1KiB槽。换频道前暂停 PE、等待 DMA 空闲，给物理 RXQ/RPQ 和两个 ARM 转发环记录旧数据边界；只丢弃旧频道雷达报告，边界随所有权交接保留。

离线验证已经通过：

- `python3 scripts/test_radio_160.py`：八个主信道和固件通知与原厂一致；TSSI 正负 ADC 修正、IQK RX 时钟、带宽检测门限、独立 HE160/HE80 MCS、VHT160 GI、原厂信道表及最高速率字段。
- `python3 scripts/test_radio_wideband.py`：20/40/80/160 MHz 的功率、RF18、ADC 对照；240 个 VHT 和 72 个 HE160 WD 对照。WD 提取明确使用原生裸 WLAN 的零 WP_OFFSET 输入，不再沿用旧 PHL 预留值。
- `python3 scripts/test_pe_downlink_gather.py artifacts/radio-160 --160`：执行本次模块内嵌的实际 MIPS PE 程序，VHT160/HE160 的 EPP、WD、载荷及 RPQ/EQ 所有权通过。
- `bash scripts/build_live_radio.sh "$PWD/artifacts/radio-160" rtl_ax_ap_160`：ARM64/musl 模块编译通过。产物是开发模块，不是新发布固件。
- `python3 scripts/test_radio_dfs.py`：执行本机原厂 ARM64 判定函数，459组脉冲/序号/双段/长脉冲窗口对照通过；MAC/BB 开关写序列相同；真实原生函数的 CAC/AP 发射控制、RFK 交叠和启动期间雷达命中边界通过。
- `python3 scripts/test_pe_rpq_firmware.py artifacts/radio-160-dfs/pe-tx.bin`：执行当前生产 MIPS PE 程序，516字节报告完整转发，RXQ/RPQ 旧报告丢弃和新报告保留通过。
- `python3 scripts/test_pe_downlink_gather.py artifacts/radio-160-dfs --160`：更新 RPQ 槽位后，VHT160/HE160 下行载荷和 RPQ/EQ 所有权通过。
- `bash scripts/build_live_radio.sh "$PWD/artifacts/radio-160-dfs" rtl_ax_ap_160_dfs`：包含本次 DFS/CAC 接线和生产 PE 程序的 ARM64/musl 开发模块编译通过。

硬件 IO、校准查询和完成状态在上述离线执行中由模型提供；它们不证明空口协商、实际吞吐或长期稳定性。

2026-10-15 实机接入：

- 首次上机抓到原生 chanctx 方案在 `.start` 读取未提供的 chandef，导致内核空指针；修正启动顺序后，又证实该接口路径不提供 mac80211 软件扫描，hostapd 邻居扫描因此失败。最终恢复模拟接口，并用补丁208修正雷达状态通知，保留真实扫描流程。
- 中国监管域下 HE160、主信道44、中心信道50（5250MHz）成功配置，RF/BB、IQK读回一致。hostapd 真实执行60秒 CAC，再进入 AP-ENABLED；CAC期间调度及beacon关闭，检测报告持续输入。
- Mac 客户端实际协商 HE160/2SS，下行 PHY 反馈达到2401.9Mbit/s，上行反馈也包含160MHz。PHY速率不等同于吞吐。
- 600Mbit/s目标线速、5秒单向下行，243788个已成功发送包全部收到，UDP净载荷约574.17Mbit/s；发送端另有35次EAGAIN，单独记为测试未完全通过。双向每方向300Mbit/s仍可复现丢包，不能宣称高速丢包已修复。
- 配套开发模块为 `artifacts/radio-160-dfs-live2/rtl_ax_ap_160_dfs.ko` 和同目录 `mac80211.ko`，两者共同用于本次实机验证；补丁208须随固件构建应用。固件闪存未改写。

DFS/CAC 按真实监管规则运行，没有放宽国家规则或伪造 CAC 成功。未使用雷达发生器验证空口雷达响应；上述结果不等同于 DFS 认证或长期稳定性验证。原始现场记录保存在工作区 `现场记录/20261015-160mhz-live`，模块和结果摘要见 `manifests/radio-160-live.json`。

机器可读结果见 `manifests/radio-160-channel-tssi-contract.json`、`manifests/radio-wideband-contract-test.json`、`manifests/radio-dfs-contract.json`；原厂调用的完整输出保存在同目录的 `radio-stock-*160*` 及扩展后的带宽、RF、校准清单。
