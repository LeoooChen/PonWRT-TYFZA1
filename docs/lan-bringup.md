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
