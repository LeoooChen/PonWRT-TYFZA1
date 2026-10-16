# TYFZA1 Wi-Fi 首批 BB/RF 与数据面证据补充

日期：2026-10-16。代码基线：`1cc4824d3316851ef18b1eb8fe0079154192589c`。

本文保留首批16个原厂函数的技术推导；完整范围与后续复核以[Wi-Fi全范围遗漏清单](wifi-porting-gaps-20261016.md)为准。

## 结论

存在整条功能链尚未移植的情况。此前的数据通路修复有必要，但不能据此认定无线移植已完成。最明确的缺口是 BB 运行期动态管理：项目有 DIG、环境监测、CFO 等启动初始化，缺少原厂持续更新状态、采集报告、计算参数和写回硬件的调用链。另有发送保护策略接口未接入，以及尚未提供的高级无线能力。

这些是代码覆盖缺口，不是已经证实的现场丢包根因。本轮没有实机，没有运行吞吐测试，也没有修改驱动行为。此前 WP/BID、BA/PN、暂停和完成通知的修复还需要新固件实测。

## 原厂证据与审计边界

唯一原厂二进制依据：

`evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko`

SHA256：`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。

使用 REA 6.5.0 / Ghidra 12.1.2 分析 16 个函数。完整工具返回保存在 [rea-full-records.tar.gz](../artifacts/wifi-coverage-audit-20261016/rea-full-records.tar.gz)，每条 Evidence ID、原始记录散列和函数地址见 [rea-index.json](../artifacts/wifi-coverage-audit-20261016/rea-index.json)。便于阅读的伪代码、汇编及调用关系见 [rea-functions.json](../artifacts/wifi-coverage-audit-20261016/rea-functions.json)。伪代码是反编译结果，不是原始源码。

本文函数地址使用 ELF `.text` 偏移；对应 REA 地址在这些函数中需要加 `0x10003c`。没有把另一份总散列不同的 live-files 模块混入证据。

本轮覆盖启动、周期管理、关联/能力更新、发包保护、mac80211 接口和 PE 准入。没有穷尽原厂全部符号、固件内部代码或所有运行配置，也不计算“移植完成百分比”。源码核对范围为 `port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/`、`port/pe/rtl9617c-radio-*`，以及只读检查的 NI 无线入口。表内 `D/` 表示上述驱动目录。

## 运行期缺口为何成立

原厂主链：

```text
phl_watchdog_cmd_hdl
  rtw_hal_watchdog@1c5b80
    rtw_hal_bb_watchdog
      halbb_watchdog_normal@285a74
        common state update -> HW setting -> environment -> DIG
        -> PMAC statistics -> PHY status accounting
        -> [advanced DM] EDCCA / DFS / CFO / RA / power / ...
        -> reset period statistics
    rtw_hal_rf_watchdog
      halrf_watchdog@2f7194
        thermal A/B -> TSSI / DPK / IQK / LCK / RXDCK / OP5K tracking
```

项目 `D/pci.c:1067` 起仅执行初始化；`D/monitor.h:1079` 的周期任务主要执行 PPDU 的 20 ms RSSI 采样和约两秒一次的 RSSI H2C。`D/pci.c:379` 的 PPDU 路径调用 `rtl_ax_ppdu_signal`，消费 RPL/RSSI。它没有接入上述 DIG/环境测量算法，也没有建立完整 BB 周期状态。固件 RA 已注册且有反馈，不能说“没有速率控制”；固件 RA 的存在也不能代替这些原厂 ARM 主机算法。

关键启用条件已用原厂 ARM64 机器码复核，而非仅依函数名判断：

| 条件 | 原厂实际行为 | 对移植判断的约束 |
| --- | --- | --- |
| normal self-init，C/XB | `halbb_cmn_info_self_init@2b01d0` 将 BB+`0xcfd` 设为 1 | advanced DM 默认开启；不等于每个下级算法都开启 |
| C，RFE54，无天线分集，共享晶振 | supportability=`0x38ba` | DIG bit 11、环境监测 bit 5 保留，CFO bit 6 关闭 |
| XB，同上 | supportability=`0x8bb` | 同上；两颗都不能误判为 DIG 默认关闭 |
| C / XB，独立晶振 | 掩码分别为 `0x38fa` / `0x8fb` | CFO tracking 的能力门打开，仍受运行状态和暂停条件约束 |
| `halbb_dig_abort@2d6818` | 检查 bit 11、暂停及暂停后的跳过周期 | 不能用能力位 bit 0 推断 DIG 是否开启 |
| CFO acceleration 未开启 | `halbb_cfo_watchdog@28fb44` 调用普通 `halbb_cfo_dm` | `dynamic_acceleration=false` 不等于整个 CFO 算法停用 |

工具 [check_stock_wifi_watchdog.py](../tools/check_stock_wifi_watchdog.py) 执行原厂初始化、能力掩码、abort 和 dispatch 函数，结果见 [stock-dispatch.json](../artifacts/wifi-coverage-audit-20261016/stock-dispatch.json)。未选择的被调函数采用显式回调；它证明分支和调用关系，不证明温漂、频偏或射频效果。原机最终 `dev_cap.is_indiv_xtal` 值仍未知；项目默认 0 不能当成原厂实测值。

## 功能覆盖清单

| 功能链 | 原厂证据 | 项目实际覆盖 | 判定与影响 |
| --- | --- | --- | --- |
| MAC/PCIe/HFC、原厂表与 RFE54 参数 | 已有启动反汇编及参数提取证据 | `D/pci.c`、`mac-runtime.h`、校准/表文件 | 已有实现；本轮未重新认证全部寄存器，不标为缺失 |
| 5 GHz 20/40/80/160 MHz | 已有本机 channel/IQK/TSSI 证据 | `rtl_ax_chandef_locked`、channel/calibration 路径，HE/VHT 160 声明 | 已实现；不能把当前问题再归为只声明 80 MHz |
| BB 共同状态、周期统计与 PHY 报告消费 | `halbb_cmn_info_self_update@285434`、watchdog | 保留 station/RSSI/RA 状态，但 PPDU 主要只取 RSSI，缺完整周期账本 | **部分移植，P1**。是 DIG、EDCCA 等运行算法的前置依赖 |
| DIG 运行期增益/检测门限 | `halbb_dig@2d6eb4`：连接变化、FAHM latch/trigger、阻尼、增益计算、BB 写回 | `D/dig.h` 仅 initialize、C 带宽门限重编码；运行期没有调用算法 | **确认缺失，P1**。两颗芯片均存在默认能力；噪声/RSSI 变化下行为可能偏离原厂 |
| 环境监测运行循环 | `halbb_env_mntr@2acd10`：能力 bit 5、人工占用检查、结果读取、重新触发 | `D/environment.h` 仅初始化 | **确认缺失，P1**。不是填好 NHM/FAHM 初值就完成环境测量 |
| EDCCA 动态门限与碰撞门限 | `halbb_edcca@2a4430`：从公共配置取 mode、abort、threshold/collision 更新、读取结果 | `D/bb-dm.h` 初始化 mode/caps/high/low；无对应周期链 | **确认缺少运行实现**。各 mode 的实际写回差异需继续追原厂下级分支，不能直接认定默认 mode 会引发丢包 |
| CFO 跟踪 | watchdog、`halbb_cfo_dm@28f3d0`、`halbb_cfo_trk_abort@28f128` | `D/cfo.h` 初始化；无频偏样本累计、普通/加速运行算法 | **有条件缺失**。先确定本机晶振配置；禁止为补功能强开共享晶振的 tracking |
| RF 周期温度/重校准 | `halrf_watchdog@2f7194` | 启动/换信道校准与硬件 TSSI tracking 已有，未找到等价主机周期任务 | **部分移植**。不能说 TSSI 全缺；后续确认 C 的 IQK tracking 下级为空，LCK/RXDCK/OP5K 才有各自条件化动作，详见全范围清单 |
| RA 注册、能力和反馈 | `rtw_hal_update_sta_entry@1c9478` 的 RA/BB 媒体状态调用 | `D/monitor.h` 的 `rtl_ax_ra_peer`、`link_sta_rc_update`、RSSI H2C；`D/pci.c` 接 RA/TX 统计 C2H | **已有核心实现**。尚未完整移植原厂共同状态、RSSI 平滑与主机 watchdog 策略 |
| RTS/CTS、ERP/HT 保护状态的传递 | `VCS_update@a85f4`、`update_attrib_vcs_info@dcfc4` 存在条件化站点/逐包保护策略 | `D/monitor.h:287` 处理 ERP slot、BSS color、beacon；无 ERP CTS/HT protection 更新、无 `set_rts_threshold`，TX 未消费 `USE_RTS_CTS`/`USE_CTS_PROTECT` | **确认缺接入，P1**。但启动硬件 RTS 门限、fallback rate、动态 SMPS 的 RTS 位已存在；不是完全没有 RTS。需同时覆盖 ARM TX 和 PE 模板 |
| Beamforming / sounding / CSI | 原厂关联更新按双方能力调用 `_hal_bfee_init`、CSI、RF BF 配置 | HE/VHT 能力未声明 BF，未找到等价关联/sounding 控制链 | **未提供的能力**。原机实际启用组合仍待确认；不能拿它直接解释普通 SU 丢包 |
| OFDMA / TWT / MU 调度 | 原厂有条件的相关实现；本轮未恢复全部配置来源 | `txbf_mu`/`ofdma` 参数只参与 firmware scheduling 选择；不构成能力协商与完整调度实现，未声明相应能力 | **未提供/未完成审计**。不能因参数名存在就宣称 OFDMA 或 Wi-Fi 6 全功能已完成 |
| 2.4 GHz 40 MHz | 本轮未完成原机配置和整条 40 MHz 路径核对 | `D/monitor.h:29` 明确拒绝 XB 非 20 MHz，能力也未声明 40 MHz | **明确未提供**。属于覆盖限制；不解释 5 GHz 160 MHz 的现场现象 |
| 多 AP / VAP | 本轮未恢复原机启用配置 | 每颗只有一个 `ap_vif`，第二个 AP 返回 `-EBUSY` | **明确未提供**。不把它列为单 AP 压测的根因 |
| CCMP 和其他加密 | 已有原厂 security/CAM/PN 证据 | `set_key` 硬件仅 CCMP-128；其他返回 `-EOPNOTSUPP`，当前 Linux mac80211 对所列 cipher 可软件处理 | **硬件覆盖有限**。不能等同于 WPA3/PMF 完全没实现；混合软硬件密钥的端到端行为仍待实测 |
| BA、密钥、暂停、PS、完成回收 | 原厂重排/密钥/完成证据及此前定点复现 | Linux/ARM/PE 共同维护，已有本地修复 | **已有实现，未全部实机回归**。跨执行端状态一致性仍需审计 |
| DFS/CAC | 既有原厂雷达报告与分类证据 | 项目有雷达报告、CAC/TX 门控、分类与 cfg80211 通知路径 | **已有实现**。BB watchdog 中存在 DFS 调用不能据此认定项目完全没有 DFS |
| PE 下行准入与 A-MSDU | 项目自有 PE 架构，见下文原机 Kconfig | 需 authorized、PTK、TID0 TX BA 与 Ethernet 模板；支持多 peer/TID 排队和聚合 | **已实现但准入有限**。非 TID0 活动能否及时建立准入是后续覆盖问题；不能把多 TID 队列误说成已经实现每 TID 独立模板准入 |
| PE 上行快路 | `port/pe/rtl9617c-radio-uplink.h` 实际代码 | 受保护 ToDS QoS、已授权 CCMP peer、顺序 SN/PN、符合格式的 TCP/UDP IPv4/IPv6；PS 转换、碎片等交 ARM | **已实现但非全流量**。异常帧交完整路径不等于丢弃；缺包须定位实际边界 |
| NI/Linux 无线入口 | `rtl9617c-wifi.c` 实际代码 | GSO、CHECKSUM_PARTIAL、多播及部分协议保留 Linux 路径，普通匹配流量进入 NI | **分工与覆盖限制**。慢路负载可能影响性能；需要计数证实，不能从分支存在直接推出掉包 |

Linux 软件加密判断依据是本项目构建树 `linux-6.18.55/net/mac80211/key.c:201` 起的 `out_unsupported` 分支，且本驱动未设置 `SW_CRYPTO_CONTROL`。这是 Linux 接口分工证据，不是拿其他驱动替代原厂硬件实现。

## 原厂硬件加速的比较基线

原机提取的 [stock-kernel.config](../evidence/wireless-study-20261009-104746/evidence/derived/stock-kernel.config) 明确关闭：

```text
# CONFIG_RTK_NIC_HWLOOKUP_DEAMSDU_OFFLOAD is not set
# CONFIG_RTK_FC_WIFI_AMSDU_OFFLOAD_BY_PE is not set
# CONFIG_RTK_WFO is not set
# CONFIG_WFO_VIRT is not set
```

因此不能把别的板型的 WFO/TXAMSDU PE 镜像当作本机必须照搬的原厂路径。本项目 PE 快路是新的执行分工：比较重点应是原厂语义、报文所有权、序列号/PN、BA、PS、队列和完成回收，而不是要求每个原厂主机函数都在 PE 内出现。原厂 FC/NE 通路仍是硬件行为依据，不能由上述禁用项推断原机“没有硬件加速”。

RFE54 DPK 也是反例：[radio-stock-RFE54-DPK-bypass.json](../manifests/radio-stock-RFE54-DPK-bypass.json) 已记录两颗芯片原厂 bypass 分支。不能因 watchdog 包含 `halrf_dpk_tracking` 就强开 DPK，或把原厂 bypass 算作漏移植。

## 实现依赖（不按高速丢包相关性筛选）

1. **先补 BB 运行链的前置数据与 DIG/环境监测闭环。** 原厂证明这是主机任务，当前两颗芯片都缺。先恢复关联变化、RSSI/PHY 统计、报告完成和周期重置语义，再移植结果计算/写回。DIG 与环境监测共用 FAHM 资源，必须复核原厂 application/占用仲裁，不能各自添一个定时器同时操作寄存器。
2. **补发送保护状态贯通。** 从原厂 VCS/发包元数据到实际 C/XB TXWD 位继续追，统一接到 mac80211 状态、ARM 请求和 PE 模板更新。不得靠全局强制 RTS、降带宽或固定低速率代替条件化策略。需要区分传统 ERP/HT 保护、动态 SMPS 和硬件门限触发。
3. **按本机条件补 RF 周期任务，核实 CFO 配置。** 保留已存在的硬件 TSSI 自动跟踪、RFE54 DPK bypass；查明温度变化时哪些原厂下级函数真的执行。CFO 先追原厂 `is_indiv_xtal` 最终值，避免两颗共享晶振时错误调节。
4. **继续覆盖加速准入、高级能力、管理与诊断。** BF/OFDMA/TWT/多 AP、扫描选频、节能恢复和板级管理均属于审计范围，不能因与高速丢包关系较弱就排除。新增能力必须同时检查 ARM/PE 模板与状态传播。

所有周期实现都要接入现有 start/stop、换信道和校准互斥生命周期；不能在 radio stop 后继续访问硬件，不能持自旋锁做睡眠校准，也不能跳过固件命令的完成语义。这些属于原厂行为在当前 Linux/PE 架构中的必要状态约束。

原始现场计数仅是历史验证材料，本次目标是完整找出遗漏移植的 Wi-Fi 部分，不再以吞吐/丢包作为功能清单的筛选条件。
## 本轮交付

提交范围仅为本 Wi-Fi 审计文档、manifest、原厂函数定点执行工具和证据归档。没有变更驱动/PE 运行代码，没有改速率、带宽、队列容量或超时；没有全量测试/全量固件编译。PON 与以太网工作区改动原样保留。
