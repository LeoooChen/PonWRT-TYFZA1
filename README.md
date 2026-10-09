# PonWRT-TYFZA1

TYFZA1 / RTL9617CVW 的原生 OpenWrt 主线移植项目。

2026-10-07：实机已启动原生 Linux 6.18.55 / OpenWrt initramfs。四核、UART RX/TX、PSCI、GICv3、timer、procd/ubus 与 watchdog 正常；原生 SPI NAND/MTD 和 UBI 读取通过。11 个原厂 UBI 卷全部识别，boot、static_conf 和 ubi_k0 的完整读回 SHA256 与本机原厂备份一致。

LAN 首轮：原生 XRAM 管理通路已提供 eth0/br-lan，1Gbps 协商、1000 个满 MTU 包与 1MiB 双向 TCP 哈希校验通过。完整 main（r0-79bc56c）已在实机通过 DHCP、LuCI登录和IPv6邻居发现/ping，最终 RAM 载荷保存于 artifacts/full-lan-luci-multicast-6.18.55/。

NI/DMA 已在实机实现 Linux DMA API 发送、L3QM/EPP64 中断接收与软件转发。TQM 复位和 EPP 满环处理已修正；100000 个带序号 UDP 包零丢失/重复/错包，16MiB 双向 TCP cmp0，完整主线接收约417Mbps、发送约294Mbps，DHCP和LuCI正常。最新验证镜像为 artifacts/full-ni-pon-base-6.18.55/ram-pon/image.itb。当前验证日志及各单项对照见 docs/ni-dma-bringup.md 和 manifests/ni-tests.json。

当前开发顺序：EPON有线硬件转发 → Wi-Fi → 原生OpenWrt UBI安装/升级/恢复。GPON族按用户要求暂缓。无线资料完整文档阅读与实施顺序见docs/wifi-port-order.md；每个新阶段先核对用户资料目录的增量。

当前主线基线：OpenWrt `79bc56cb7483bc716da07c369530b792631904b4`，Linux `6.18.55`。本机原厂 DTB 与资料库原生代码采用的 TYFZA1 DTB SHA256 相同。

- `port/overlay/target/linux/rtl9617c/`：新增 target、板级 DTS 和原生驱动。
- `scripts/`：RAM FIT 构建、启动内核构建、TTL 控制和 TFTP。
- `manifests/`：固定源码、板级参数和实机结果。
- `docs/bringup.md`：启动记录和可复用操作。
- `sources/openwrt/`：固定提交的主线源码；`sources/ponwrt-reference/`：Airoha 协议层参考。
- `evidence/`：本机原厂备份和完整 SDK 研究资料；`artifacts/`：载荷与构建目录；`logs/`：原始串口/构建/传输日志。后三者留在 Ubuntu，不进入 Git。

工作目录：Ubuntu `/home/leo/PonWRT-TYFZA1`。Mac 可查看 `~/OrbStack/ubuntu2404/home/leo/PonWRT-TYFZA1`。

PON原生SerDes、gearbox、MAC/PCS/FEC已完成实机初始化，三个时钟就绪、关键参数读回匹配、统计RAM初始化完成。最新完整主线镜像通过UCI pon与S25pon自动执行；MPCP/LLID控制、原机ONU身份导入及PDC映射已进入原生驱动，后续接通PUC上行队列、OAM和PON业务接口。详细记录见 docs/pon-bringup.md。

2026-10-09：XEPON的MPCP注册、CTC OAM认证、FEC、WAN DHCP和LAN↔PON业务已实机通过；IPv4 TCP/UDP动态硬件NAT、cache自动分配/删除及原厂64K Hash/48字节槽诊断通过。新源码补齐VLAN/QinQ及共享出口接口，完整镜像artifacts/full-pon-wired-vlan01-6.18.55构建/载荷校验通过，新功能待设备重新连接后板测。当前Wi-Fi从板级PCIe与芯片身份开始，尚未声明无线可用。
