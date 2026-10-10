# TYFZA1 原生 AP 实机推进（2026-10-10）

原生驱动已接入 mac80211 AP、自身角色、客户站点 MACID/CAM、port0 和固件 Beacon 模板。2.4 GHz 实机完成 WPA2 四次握手、桥接及公网测速，用户初次报告约 5 Mbps。当前只发布传统速率、20 MHz、WMM 关闭；尚未发布 HT/VHT/HE 发送及聚合能力。

## 实际验证

- Beacon04：两颗芯片各三次统计报告、30 次成功；关闭后计数停止，恢复后各增加30。原机 WCPU 报文实际只有15字节（7字节统计 payload），按原机 `mac_parse_bcn_stats_c2h` 的52个有效位解析。日志 `logs/radio-beacon04-cycles.txt`。
- AP04：原机默认 CMAC 天线表接入后，用户扫到2G、完成连接及公网测速。站点显示 RX/TX 48/54 Mbps、WMM=no，TX约20.96 MB；native DATA0完成18370，管理8完成205，HIQ9有3项未完成，full计数114。日志 `logs/radio-ap04-connected.txt`。上述 HIQ及流控仍需按原厂补齐。
- AP04后续重启：用户扫到5G，但认证/关联应答出现未获 ACK，未完成 WPA2握手。这不是已证明的密码错误。
- AP07：密码改为用户指定的88888888，AP自身保留原机创建阶段的默认 CMAC，只在客户关联时更新连接参数。2G先收到旧PSK的 invalid MIC，随后另一轮真正完成四次握手、授权并持续收发数据。Mac定向查询返回信道6、RSSI约-49 dBm的目标网络；API隐藏SSID/BSSID，不能把字段不可读误判为没有网络。
- 5G可见性不稳定：AP04曾由用户观察到，AP07信道36/44的Mac定向查询均未返回目标；当前仍未验收5G稳定关联与业务。

## 原机实现依据

所有主机函数均是原生Linux实现，未加载原厂 `rtk_wifi6.ko`。运行时使用原机WCPU固件及板级校准/参数数据。离线运行原机ARM64 ELF只用于提取格式和寄存器/参数链路，不作为空口成功证明。

原机模块SHA256：745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e。

- `trace_stock_ap_port.py`：执行原机角色、地址/BSSID CAM、port0、HIQ drop及客户创建/连接/删除链路。客户 MACID独立、共享 AP BSSID CAM保留。
- `trace_stock_beacon.py`：执行原机 `mac_send_bcn_h2c`。OUTSRC以外的MAC category1/class5/func16，LONG_DATA，16字节控制头后为不带FCS的802.11模板，TIM相对24字节MAC头。显式天线覆盖仅作为原机接口支持的诊断参数，尚未证明它解决5G问题。
- `trace_stock_station_tables.py`：执行原机 `rtw_hal_add_sta_entry`、C/XB天线选择及真正MAC H2C编码；2T默认天线码0x43。默认的零掩码 DMAC no-op被两颗实际WCPU以status14拒绝，移植省略这一空操作。关联阶段再执行原机连接CMAC字段更新。
- `trace_stock_rfk_notify.py`：执行原机 `halrf_mcc_get_ch_info`和C专有`halrf_fw_ntfy_8852c`。完整RFK后通知24字节：两个缓存频道、两个频段、当前中心频道、当前频段。category2/class10/func2，DATA，无ACK；通知提交不等于得到固件完成应答。
- C TSSI成功收尾遗漏了原机`023c[31:16]=2001`及`5670/2fc`的DAC复位脉冲，现按原机3342d8..334358和pathB尾部补齐。

## 未通过的试验与撤回结论

AP01同时使用模拟频道上下文及自定义分配回调，Linux6.18拒绝注册；AP02保留模拟频道上下文，由config驱动原生射频配置。

AP05试验写入20MHz功率表后，用户报告两个网络不可见。恢复AP04后用户看到5G、未看到2G。这没有建立单一功率根因。功率写入草稿保存在 `artifacts/radio-power-20-draft`，不接入默认驱动；实际原机映射保留在 `manifests/radio-stock-power-registers.json`。

AP06将连接后的CMAC更新同时应用到AP自身后，用户报告不可见。AP07将连接阶段更新限定到客户站点，2G成功连接。不能仅凭这次前后现象推导所有字段的单独因果关系。

CMAC诊断时一度把硬件DWORD5误当为端口所在的DWORD4，错误写入已恢复；真正DWORD4读回0，port0/MBSSID0本来正确。该假设撤回，未加入所谓端口修复。

此前RX WARN所在 `ieee80211_rx_list+728` 实际是`skb->len <= FCS_LEN + rtap_space`；硬件过渡记录可以只有4字节FCS。现在在送mac80211前排除没有MAC头的记录。不能继续把它称为已证明的停机后迟到回调问题。

## 当前工程与接续验证

完整构建入口已加入wpad-mbedtls；构建与live模块编译均用Ubuntu用户systemd服务并用OpenWrt构建锁串行。`build_live_radio.sh`可指定独立模块/驱动名，只替换C时保留XB及客户连接。

先完成5G空口与关联应答，再接原机速率、HT/HE、WMM/EDCA、BA/聚合与真正队列流控；补齐DTIM/TIM与HIQ生命周期，然后验证加密/重连及双频数据。保留实际RX、TX完成、失败和用户端扫描/连接证据，最终纳入内置驱动FIT重新RAM启动。Wi-Fi仍未完成，当前镜像不作为原生UBI正式刷入产物。
