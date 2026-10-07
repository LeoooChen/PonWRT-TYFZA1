# PonWRT-TYFZA1

TYFZA1 / RTL9617CVW 的原生 OpenWrt 主线移植项目。

2026-10-07：实机已启动原生 Linux 6.18.55 / OpenWrt initramfs。四核、UART RX/TX、PSCI、GICv3、timer、procd/ubus 与 watchdog 正常；原生 SPI NAND/MTD 和 UBI 读取通过。11 个原厂 UBI 卷全部识别，boot、static_conf 和 ubi_k0 的完整读回 SHA256 与本机原厂备份一致。

LAN 首轮：原生 XRAM 管理通路已提供 eth0/br-lan，1Gbps 协商、1000 个满 MTU 包与 1MiB 双向 TCP 哈希校验通过。完整 main（r0-79bc56c）已在实机通过 DHCP、LuCI登录和IPv6邻居发现/ping，最终 RAM 载荷保存于 artifacts/full-lan-luci-multicast-6.18.55/。

开发顺序：系统启动 → PON（包括 NI/DMA 数据通路）→ Wi-Fi → 硬件加速。最终交付目标是原生 UBI 固件与完整安装/升级/恢复步骤。

当前主线基线：OpenWrt `79bc56cb7483bc716da07c369530b792631904b4`，Linux `6.18.55`。本机原厂 DTB 与资料库原生代码采用的 TYFZA1 DTB SHA256 相同。

- `port/overlay/target/linux/rtl9617c/`：新增 target、板级 DTS 和原生驱动。
- `scripts/`：RAM FIT 构建、启动内核构建、TTL 控制和 TFTP。
- `manifests/`：固定源码、板级参数和实机结果。
- `docs/bringup.md`：启动记录和可复用操作。
- `sources/openwrt/`：固定提交的主线源码；`sources/ponwrt-reference/`：Airoha 协议层参考。
- `evidence/`：本机原厂备份和完整 SDK 研究资料；`artifacts/`：载荷与构建目录；`logs/`：原始串口/构建/传输日志。后三者留在 Ubuntu，不进入 Git。

工作目录：Ubuntu `/home/leo/PonWRT-TYFZA1`。Mac 可查看 `~/OrbStack/ubuntu2404/home/leo/PonWRT-TYFZA1`。
