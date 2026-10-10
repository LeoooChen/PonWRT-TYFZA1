# TYFZA1 原生 AP 实机推进（2026-10-10）

原生驱动已实现双频 mac80211 AP、WPA2、桥接、20 MHz 两空间流 HT、WMM/EDCA、发送聚合和接收 BA。用户实测两个频段均约50 Mbps下载、20 Mbps上传，上传跑满宽带。原厂主机ko未加载；WCPU固件及校准/参数来自本机。40/80MHz、VHT/HE发送、完整节能/TIM及无线NE卸载继续移植。

## 实际验证

- Beacon04：两颗芯片各三次统计报告、30 次成功；关闭后计数停止，恢复后各增加30。原机 WCPU 报文实际只有15字节（7字节统计 payload），按原机 `mac_parse_bcn_stats_c2h` 的52个有效位解析。日志 `logs/radio-beacon04-cycles.txt`。
- AP04：原机默认 CMAC 天线表接入后，用户扫到2G、完成连接及公网测速。站点显示 RX/TX 48/54 Mbps、WMM=no，TX约20.96 MB；native DATA0完成18370，管理8完成205，HIQ9有3项未完成，full计数114。日志 `logs/radio-ap04-connected.txt`。上述 HIQ及流控仍需按原厂补齐。
- AP04后续重启：用户扫到5G，但认证/关联应答出现未获 ACK，未完成 WPA2握手。这不是已证明的密码错误。
- AP07：密码改为用户指定的88888888，AP自身保留原机创建阶段的默认 CMAC，只在客户关联时更新连接参数。2G先收到旧PSK的 invalid MIC，随后另一轮真正完成四次握手、授权并持续收发数据。Mac定向查询返回信道6、RSSI约-49 dBm的目标网络；API隐藏SSID/BSSID，不能把字段不可读误判为没有网络。
- 历史5G可见性不稳定：AP04曾由用户观察到，AP07信道36/44的Mac定向查询均未返回目标。AP13补齐原厂正常2T2R启动尾部后完成扫描、连接和业务，见下文。

## 原机实现依据

所有主机函数均是原生Linux实现，未加载原厂 `rtk_wifi6.ko`。运行时使用原机WCPU固件及板级校准/参数数据。离线运行原机ARM64 ELF只用于提取格式和寄存器/参数链路，不作为空口成功证明。

原机模块SHA256：745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e。

- `trace_stock_ap_port.py`：执行原机角色、地址/BSSID CAM、port0、HIQ drop及客户创建/连接/删除链路。客户 MACID独立、共享 AP BSSID CAM保留。
- `trace_stock_beacon.py`：执行原机 `mac_send_bcn_h2c`。OUTSRC以外的MAC category1/class5/func16，LONG_DATA，16字节控制头后为不带FCS的802.11模板，TIM相对24字节MAC头。显式天线覆盖仅作为原机接口支持的诊断参数，尚未证明它解决5G问题。
- `trace_stock_station_tables.py`：执行原机 `rtw_hal_add_sta_entry`、C/XB天线选择，并读取真实ops+0x110/+0x118绑定芯片专有DCTL/CCTL V1编码器。HAL主机字段中的0x43不被实际CCTL编码器输出，已撤回旧的wire天线表结论。早期DMAC试验误选generic36字节/function1接口，收到status14；本机实际为68字节/function9，改用正确格式后得到成功应答。
- `trace_stock_rfk_notify.py`：执行原机 `halrf_mcc_get_ch_info`和C专有`halrf_fw_ntfy_8852c`。完整RFK后通知24字节：两个缓存频道、两个频段、当前中心频道、当前频段。category2/class10/func2，DATA，无ACK；通知提交不等于得到固件完成应答。
- C TSSI成功收尾遗漏了原机`023c[31:16]=2001`及`5670/2fc`的DAC复位脉冲，现按原机3342d8..334358和pathB尾部补齐。

## 未通过的试验与撤回结论

AP01同时使用模拟频道上下文及自定义分配回调，Linux6.18拒绝注册；AP02保留模拟频道上下文，由config驱动原生射频配置。

AP05早期功率草稿试验后，用户报告两个网络不可见。恢复AP04后用户看到5G、未看到2G。这没有建立单一功率根因。草稿保存在 `artifacts/radio-power-20-draft`；当前默认驱动已使用重新对照完整原机构造器、普通/RU布局和shape0的实现，实机43个当前DWORD读回吻合。两者不是同一实现。

AP06将连接后的CMAC更新同时应用到AP自身后，用户报告不可见。AP07将连接阶段更新限定到客户站点，2G成功连接。不能仅凭这次前后现象推导所有字段的单独因果关系。

CMAC诊断时一度把硬件DWORD5误当为端口所在的DWORD4，错误写入已恢复；真正DWORD4读回0，port0/MBSSID0本来正确。该假设撤回，未加入所谓端口修复。

此前RX WARN所在 `ieee80211_rx_list+728` 实际是`skb->len <= FCS_LEN + rtap_space`；硬件过渡记录可以只有4字节FCS。现在在送mac80211前排除没有MAC头的记录。不能继续把它称为已证明的停机后迟到回调问题。

## 当前工程与接续验证

完整构建入口已加入wpad-mbedtls；构建与live模块编译均用Ubuntu用户systemd服务并用OpenWrt构建锁串行。`build_live_radio.sh`可指定独立模块/驱动名，只替换C时保留XB及客户连接。

接续工作：完整DTIM/TIM与节能、原机更高带宽和VHT/HE发送、密钥/重连生命周期及无线NE卸载。每阶段保留实际RX、TX完成和用户端业务证据，纳入内置驱动FIT再RAM启动。Wi-Fi完整移植与原生UBI正式刷入验收仍未完成。

## 2026-10-10 午间继续

无线资料增至700个文件，新增第15..18篇已全文阅读，冻结为`evidence/wireless-study-20261010-121438`；阅读收据`manifests/wifi-reading-20261010-control-update.json`。U1固件调度证据只用于相应分支，实际两颗cv1/U2身份继续以板上读回为准。

`ada768d`完整后台构建114729已退出0，并通过实际FIT核对。后续AP10c新增43个20MHz TPU DWORD、普通/RU限幅、shape0和CN参数；实机全部当前读回等于写入值，C仍不可见，XB07保持连接。C OFDM MAC_TXEN/PHY_TXON观察从1902/1902增长到2192/2192，说明计数范围的发射请求进入PHY；不由此宣称空口正确或天线工作。

普通MAC管理DMA另提交3000个广播Beacon、真正RPQ完成3000、pending0，Mac定向扫描仍无C目标网络。这一试验排除了仅H2C模板路径是唯一疑点，仍未证明每个RF寄存器和校准结果正确。

原机`rtw_hal_notify_switch_band`包含by-rate更新和TSSI scan；RFE54的EDCCA固件命令分支跳过。每次频道更改另更新普通与RU限值。原生20MHz功率实现依实际原机TPU构造器、bitmask和MAC writer映射，包含RU布局；超出20MHz的完整构造仍待对应带宽接入。

## 双频业务、HT与聚合实测

AP13c补齐原机`rtw_hal_start@1c55ec..1c5618`共同尾部：2T2R normal-mode TRX、PHY user table和20字节BBinfo通知。对应`trace_stock_trx_paths.py`及`radio-stock-TRX-paths.json`。用户随后能扫描、连接5G并测速约10Mbps；真正WPA2四次握手、授权、双向业务记录在`logs/radio-ap13c-dualband-connected.txt`。

AP14c接入`rtw_hal_mac_set_edca -> set_hw_edca_param -> get_edca_addr`的原厂队列写入格式；四个寄存器为C300/C304/C308/C30c。Linux AC顺序转换为原厂BE/BK/VI/VO，AIFSN按实际SIFS和slot换为微秒。原机函数执行收据`radio-stock-EDCA.json`，实机逐项readback吻合。

HT速率采用原机MCS0=0x80、WD7 rate[24:16]、GI[27:25]，执行原厂C/XB DATA构造器后接入20MHz MCS0..15和SGI。AP14板上真实MCS15/144.4Mbps、WMM=yes；用户约16Mbps下载。WP接近满时停止mac80211 dequeue，RPQ释放后恢复，测速记录full=0、stop/wake各2次；不再把ENOSPC直接当正常业务丢包。

AP16/17接入原机终端CMAC聚合mask、WD3 AGG_EN、WD9最大聚合数、WD10密度，以及`rtw_hal_cfg_tx_ampdu -> set_hw_ampdu_cfg`的全局数量/时间字段。发送BA TID0/TID4实际进入operational并有RPQ完成。用户5G下载约50Mbps，上传20Mbps。RPQ是逐MPDU完成，当前统计不伪造完整PPDU子帧总数或硬件重试次数。

接收BA以原机真实MAC ops+496/+504和`mac_bacam_info`格式为依据。原机HAL包装输出将旧entry_idx放在word0[3:2]，实际C/XB MAC回调选择扩展word1[31:28]；两层输出分开归档，原生分配使用回调真正支持的8..15和standard-enable。`radio-stock-BA-sessions.json`同时保存HAL链和直接MAC编码结果。板上C创建/删除entry8都收到成功done应答；XB TID0/1的entry8/9都成功。

HIQ原机AP初始化值50可从`rtw_phl_wifi_role_alloc@13af24..13af28`及模式变化`13b964..13b968`直接确认，`rtw_hal_mac_port_init@1aa14c`读role+1268。之前trace输入误设0，移植随之遗漏。更新原机trace及port初始化后，C积压的两个HIQ WP都完成，pending归零。

AP17双频采用同一源码、独立原生模块。用户复测2G也达到50Mbps下载/20Mbps上传；XB实机12468个聚合MPDU提交/完成，14804个HT数据帧软件FCS全对，full0/RDU0、队列无积压，TX_failed54/12479保留原样。完整证据`logs/radio-ap17-dualband-50Mbps.txt`及`manifests/radio-native-AP-20261010.json`。50Mbps是实际宽带测速结果，不是144.4Mbps物理速率，也不是完整带宽能力已完成。
