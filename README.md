# PonWRT-TYFZA1

TYFZA1 / RTL9617CVW 的原生 OpenWrt 主线移植项目。

2026-10-07：实机已启动原生 Linux 6.18.52 / OpenWrt initramfs，UART RX/TX、四核 PSCI、GICv3、25MHz timer、procd/ubus 和 watchdog 接管通过。已修复 QSPI 对 SPI-NAND dirmap 零长度模板的判断，下一次载荷验证 NAND/UBI 读取。

开发顺序：系统启动 → PON（包括 NI/DMA 数据通路）→ Wi-Fi → 硬件加速。最终交付目标是原生 UBI 固件与完整安装/升级/恢复步骤。

当前主线基线：OpenWrt `79bc56cb7483bc716da07c369530b792631904b4`，Linux `6.18.55`。本机原厂 DTB 与资料库原生代码采用的 TYFZA1 DTB SHA256 相同。

- `port/overlay/target/linux/rtl9617c/`：新增 target、板级 DTS 和原生驱动。
- `scripts/`：RAM FIT 构建、启动内核构建、TTL 控制和 TFTP。
- `manifests/`：固定源码、板级参数和实机结果。
- `docs/bringup.md`：启动记录和可复用操作。
- `sources/openwrt/`：固定提交的主线源码；`sources/ponwrt-reference/`：Airoha 协议层参考。
- `evidence/`：本机原厂备份和完整 SDK 研究资料；`artifacts/`：载荷与构建目录；`logs/`：原始串口/构建/传输日志。后三者留在 Ubuntu，不进入 Git。

工作目录：Ubuntu `/home/leo/PonWRT-TYFZA1`。Mac 可查看 `~/OrbStack/ubuntu2404/home/leo/PonWRT-TYFZA1`。
