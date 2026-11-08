# 原生双频AP的OpenWrt管理接入

2026-10-10。此前板上通过临时hostapd配置验证AP，RAM系统未安装wifi-scripts，netifd启动时也未注册network.wireless。生产构建配置增加`CONFIG_PACKAGE_wifi-scripts=y`，使用本项目固定OpenWrt提交中的正式ucode实现，不复制另一套无线管理逻辑。

在当前RAM系统安装本工程生成的hostapd-common、wpad-mbedtls、wifi-scripts及ucode的nl80211/rtnl模块。该系统根为tmpfs，操作未刷NAND。首次安装后须让ubusd加载新增的wpad ACL，并重新初始化之前没有无线脚本的netifd。否则hostapd以network用户运行时无法注册全局ubus对象，wifi-scripts等待hostapd，两个radio保持pending。已对照本地ubusd源码，以SIGHUP重新加载ACL，并重启wpad；正常启动的新完整镜像会在启动时读取这些文件，不需要加入每次重启的修补脚本。

当前UCI对象为tyfza1_2g、tyfza1_5g和ap2、ap5，分别通过完整PCI路径绑定XB和C。2G为频道6/HT20；5G为频道44/VHT80、独立MAC02:00:00:00:00:05。SSID和测试密码沿用用户已验收设置，配置快照保存在artifacts/radio-native-uci，不放入默认公开固件配置。

实际验收：`network.wireless status`两个radio均up=true/pending=false；procd管理hostapd/wpa_supplicant；全局hostapd及hostapd.ap2/ap5均注册；两个AP进入br-lan。5G客户端自动关联、完成WPA2四次握手，双向VHT MCS9/NSS2/80MHz/短GI仍为866.7Mbps。LuCI调用的实际对象是`luci-rpc getWirelessDevices`，不是旧的`luci getWirelessDevices`；实际返回包含两个已启用无线对象。此轮没有要求用户再次测速，不扩大为新的吞吐验收。

证据：logs/wifi-native-package-install.txt、wifi-native-uci-after-acl.txt、wifi-native-uci-luci-rpc.txt。安装只改变当前RAM环境，正式镜像通过正常包选择包含同一管理组件。

HE接续的原机依据已保存：tools/trace_stock_ra.py执行完整RA注册及本机helper，78组C/XB、HT/VHT/HE、20/40/80MHz、1/2SS与关联RSSI输入取得16字节H2C；另经完整BB C2H分派验证2304组速率反馈字段解析。主机日志和H2C发布被模拟，不是无线固件执行或HE业务验收。生产驱动仍使用已验收的HT/VHT路径。资料见manifests/radio-stock-RA-register.json。

用户最新顺序：Wi-Fi功能收尾后，先验证及修正有线LAN↔PON硬件转发，再做Wi-Fi硬件加速；保持GPON族暂缓。

AP22完整构建的编译阶段通过，但最终无线管理核验最初把ACL路径误写为wpad.json，导致构建服务退出1。已按实际包文件更正为wpad_acl.json，重新核验AP22最终FIT通过；不能把原服务的退出码写成0。后续四LAN修复完整构建使用正确核验路径，服务退出0，包含同一双频UCI/netifd/wpad管理组件。设备未为此重新启动无线或整机。

## 2026-10-17：0.6.0 用户反馈修正（尚未实机验收）

- WPA3：驱动缺少 `MFP_CAPABLE`，导致 mac80211 从公布的 cipher 列表删除全部 BIP 管理帧算法。补齐该声明；CCMP 数据仍走原有硬件路径，管理帧使用已有 `SW_MGMT_TX` 和 mac80211 BIP，接收保留 IV/MIC 与重放检查。没有降级成 WPA2 或关闭 PMF。原厂 `rtw_calculate_bip_mic` / `_bip_ccmp_protect` 同样存在主机管理帧保护路径。
- 2.4G：非 20MHz 请求此前直接返回不支持；HE PHY 首字节为 0 又使本版本 iwinfo 不识别 AX。现按原厂 XB 实现接通 HT/HE40 的 MAC TXSC、基带、RF18、IQK 带宽命令和能力声明。原厂 `ctrl_bw_ch_XB@2f3820` 给 5MHz mask 传的是主信道号，不是带宽子信道索引，保留此实际调用语义。40MHz 仍服从国家规则及 20/40 共存，不设置 `noscan` 强行绕过。
- MAC：原机 `setup_mac_addr` 启动记录确认 LAN 使用 `ELAN_MAC_ADDR`、wlan1/2.4G 使用基址 +1、wlan0/5G 使用基址 +2。旧实现误读与实际地址不一致的 `WLAN_MAC_ADDR`，并另改 5G 的本地管理位和尾字节。导入器现在直接生成两路正确身份，完整地址加法保留进位；LAN 在 netifd 之前从出厂区恢复，网桥固定使用出厂 LAN 地址。升级迁移保留用户在接口或网桥设备上显式设置的 MAC，不修改 PON 的 LLID 身份。

验证证据在本地 `artifacts/user-reports-20261017/`：实际原厂机器码与原生基带函数的频道 6/20MHz、6/40MHz 上下边带序列匹配；原厂 IQK 的 20/40MHz RXK 时钟一致，TX/RX one-shot 带宽编码已核对。真实 ucode 导入器通过不同 WLAN 占位值和跨字节进位用例；启动服务及真实 UCI 升级迁移通过定向检查。ARM64 无线模块编译通过，保留原有 MODPOST 的 MODULE_DESCRIPTION 提示。没有全量测试、全量固件构建、刷机或发布；WPA3 握手、PMF 收发、2.4G HE40 空口工作和重启后身份仍需设备验证，不能把这些离线结果写成实机已修复。


## 2026-11-08：时区默认值修正与 WPA3 反馈核验

当前 main 8ee9791 仍继承 OpenWrt config_generate 的 GMT0/UTC。新增一次性
95-tyfza1-timezone，将默认 UTC 或未完整配置的上海时区迁移为 CST-8/Asia/Shanghai；
保留其他显式时区、主机名及所有 NTP 设置。真实 UCI 八组初始配置与重复执行检查通过，
实机 system reload 后本地/UTC 相差八小时。本次隔离光猫没有默认路由，NTP 无法访问外网，
其绝对日期另从 Mac 校正一次；时区迁移不保证离线设备能自动获得正确日期。

在未修改无线驱动的 8ee9791 固件上，5 GHz HE160 纯 SAE、PMF required 成功关联，
客户端 MFP=yes；再次重载后能关联，临时十秒组密钥周期观测到十次握手完成，
16 MiB 双向传输逐字节一致。WPA2/WPA3 混合模式也成功关联且 MFP=yes，但未单独证明
混合模式客户端所选 AKM。2.4 GHz HE20 纯 SAE/PMF required 完成关联及 8 MiB 双向传输；
初始站点计数有 tx_failed=2/rx_drop_misc=5，不表述为无损压力验收。
全程 boot_id 保持 ed5bebf0-155e-473f-8b00-2646ff6f24f0，没有复现所报崩溃。
SA_QUERY 命令被接受，但未独立捕获响应，不以此宣称完整受保护管理帧验证。

此次没有 WPA3 产品补丁，不能宣布网友的问题已修复。仍需其准确加密选项、客户端型号、
复现步骤及崩溃/hostapd 日志区分配置启动失败、连接失败和内核崩溃。
原始证据保存在 artifacts/time-wpa3-20261108；测试结束恢复原 WPA2 配置，关闭临时
2.4 GHz AP 并撤销十秒重密钥配置。此批未构建或刷写新的完整固件。
