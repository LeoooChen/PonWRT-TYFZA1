# 原生双频AP的OpenWrt管理接入

2026-10-10。此前板上通过临时hostapd配置验证AP，RAM系统未安装wifi-scripts，netifd启动时也未注册network.wireless。生产构建配置增加`CONFIG_PACKAGE_wifi-scripts=y`，使用本项目固定OpenWrt提交中的正式ucode实现，不复制另一套无线管理逻辑。

在当前RAM系统安装本工程生成的hostapd-common、wpad-mbedtls、wifi-scripts及ucode的nl80211/rtnl模块。该系统根为tmpfs，操作未刷NAND。首次安装后须让ubusd加载新增的wpad ACL，并重新初始化之前没有无线脚本的netifd。否则hostapd以network用户运行时无法注册全局ubus对象，wifi-scripts等待hostapd，两个radio保持pending。已对照本地ubusd源码，以SIGHUP重新加载ACL，并重启wpad；正常启动的新完整镜像会在启动时读取这些文件，不需要加入每次重启的修补脚本。

当前UCI对象为tyfza1_2g、tyfza1_5g和ap2、ap5，分别通过完整PCI路径绑定XB和C。2G为频道6/HT20；5G为频道44/VHT80、独立MAC02:e0:4c:07:68:05。SSID和测试密码沿用用户已验收设置，配置快照保存在artifacts/radio-native-uci，不放入默认公开固件配置。

实际验收：`network.wireless status`两个radio均up=true/pending=false；procd管理hostapd/wpa_supplicant；全局hostapd及hostapd.ap2/ap5均注册；两个AP进入br-lan。5G客户端自动关联、完成WPA2四次握手，双向VHT MCS9/NSS2/80MHz/短GI仍为866.7Mbps。LuCI调用的实际对象是`luci-rpc getWirelessDevices`，不是旧的`luci getWirelessDevices`；实际返回包含两个已启用无线对象。此轮没有要求用户再次测速，不扩大为新的吞吐验收。

证据：logs/wifi-native-package-install.txt、wifi-native-uci-after-acl.txt、wifi-native-uci-luci-rpc.txt。安装只改变当前RAM环境，正式镜像通过正常包选择包含同一管理组件。

HE接续的原机依据已保存：tools/trace_stock_ra.py执行完整RA注册及本机helper，78组C/XB、HT/VHT/HE、20/40/80MHz、1/2SS与关联RSSI输入取得16字节H2C；另经完整BB C2H分派验证2304组速率反馈字段解析。主机日志和H2C发布被模拟，不是无线固件执行或HE业务验收。生产驱动仍使用已验收的HT/VHT路径。资料见manifests/radio-stock-RA-register.json。

用户最新顺序：Wi-Fi功能收尾后，先验证及修正有线LAN↔PON硬件转发，再做Wi-Fi硬件加速；保持GPON族暂缓。
