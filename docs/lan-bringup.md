# TYFZA1 原生 LAN 验证

2026-10-07：新增 rtl9617c-xram.c，把 Taurus CPU XRAM 管理收发路径移植为 Linux netdev/NAPI；MDIO/OCP 与 phylib 提供真实 PHY 状态。当前已通的是 MAC port 0 / PHY address 1，PHY ID=0x001ccac4，1Gbps full duplex。

载荷 rtl9617c-tyfza1-xram-lan.dtb 启用上述 MAC 与 MDIO，OpenWrt 建立 eth0 → br-lan，设备地址 192.168.1.1。首次数据测试使用 Mac en5 临时地址 192.168.1.170。

关键移植修正：公开 U-Boot 2022 参考的 Header-XT bytes_valid 位域为 bit13:10，本机实际为 bit14:12。16 个编码的以太网抓包证明 bit11:10 不影响长度、从 bit12 开始改变尾部长度。修正后不再额外给满 MTU 帧添加对齐字节。

已通过：

- 双向 ARP/IPv4 ping，11 档 ICMP 载荷，从 1 到 1472 bytes；所有测试无丢包。
- Mac→光猫 1000 个满 MTU 包，1000 个回复，0%丢包，平均约 1.07ms。
- 1MiB 随机内容经 TCP 双向传输，Mac/光猫 SHA256 相同，光猫 cmp 返回0。

此路径用于管理访问和基础联网验证。完整 NI/DMA/多端口交换按后续实现；它们也是 PON 业务通路的基础。当前 PHY patch/模拟参数由原厂 U-Boot 完成，后续迁入原生 cold-boot 初始化。

完整主线 + dnsmasq + LuCI 载荷：artifacts/full-lan-luci-6.18.55/ram/image.itb。后续 DHCP/Web 的实际结果记录在 manifests/lan-tests.json。

完整 main 实机结果：r0-79bc56c / Linux 6.18.55 / OpenWrt GCC 14.4.0。dnsmasq 已向 Mac en5 分配 192.168.1.172；设备 lease 和 Mac DHCP ACK 对应。uhttpd/rpcd/LuCI 正常，root 空密码登录返回302，认证后概览页返回200。该完整载荷额外50个满MTU ping全部收到回复。

IPv6补测发现网桥设置了 promiscuous/allmulticast，但第一版驱动没有 ndo_set_rx_mode，硬件仍按 mode0 过滤。已增加对应回调，按 Taurus GPL 参考的 mode1 接收。实机复测单独记录。

重新构建 LAN/LuCI 载荷：

```sh
sh scripts/prepare_luci_feeds.sh
bash scripts/build_lan_ui.sh
```

feeds 使用 configs/feeds.conf 的固定提交，额外安装 cgi-io 与 luasrcdiet。完整固件包含 xram-lan.dtb；MAC 0的测试载荷使用这份 DTB。

最终组播版本复测通过：XRAM_CFG=0x00004000，IPv6邻居条目正确建立；Mac→光猫IPv6 link-local ping 3/3，无丢包。DHCP仍为192.168.1.172，LuCI登录302、认证后概览200。最终载荷位于 artifacts/full-lan-luci-multicast-6.18.55/ram/image.itb，SHA256=`fd0c18ba791cf56a6b4dbb1d22f0534e47559dbe0939fe7089514e9120866ac6`。

独立后台构建通过 `sh scripts/start_lan_build.sh` 启动；它保存本次脚本副本，运行过程中修改源码不会再改变 shell 正在读取的脚本。

收尾状态：临时静态地址192.168.1.170与直连路由已移除，en5只保留DHCP分配的192.168.1.172；LuCI认证概览仍返回200，IPv4 ping3/3。清理后手工重启DHCP客户端曾先重试，随后设备DHCPACK和Mac getpacket一致，最终网络配置已恢复。串口/TFTP进程已退出，光猫继续运行最终RAM载荷。

## 四个 LAN 口共享原生 NI 数据面

2026-10-09 晚间重新连接设备，U-Boot 检测网线在 physical port3；旧 RAM 镜像只登记 port0/PHY1，故有线链路在 Mac 端在线，但 eth0 carrier=0。这是原生驱动只接入一个 LAN 的实际缺口。

现实现四个独立 netdev（eth0..eth3），对应 Taurus 内置 PHY 地址1..4，共用一个 NI、TX DMA/VP8、CPU0 QM pool、NAPI及 FE 实例。每个LAN使用自己的PHY状态和netdev队列；各接口open/stop按RTNL引用计数共享engine，只控制自己的MAC RX/TX。PON继续使用同一共享engine及其独立endpoint，不被关闭一个LAN口重置。

RX依据原机Header A的LSPID字段，将source0..3递交对应LAN接口；PON source7/32以上保留原来LLID分流。四条LAN ILPB/PONCLE CPU0规则分别使用自己的TCAM/action槽，避免后一个端口覆盖前一个。TX将目的physical port写入每包Header A，继续使用共享DMA ring，不通过切换整个VP的默认目的端口发送。完成统计归属原始netdev；环恢复空间时分别唤醒各LAN及PON队列。每个LAN都登记到native FE接口表，供相应硬件流的入口/出口归属。

NI/DMA目标DTS提供realtek,lan-phys四个PHY句柄；XRAM历史调试目标仍使用其单口管理路径。89-tyfza1-lan-ports默认脚本把存在的eth0..eth3加入统一br-lan，地址、DHCP和LuCI通过同一个LAN网络提供。各口不要求重配IP或换用特定插口。

ARM64的NI/QM对象编译已通过。全量候选由后台服务ponwrt-tyfza1-lan-20261009-210804构建到artifacts/full-pon-four-lan01-6.18.55，实机验证正在继续；不能将四口代码接入等同于四个插口都已完成现场数据测试。
