# TYFZA1 原生 OpenWrt 启动工程

此目录接续已有适配，不另建 target、不重写 NAND/ECC/UBI 协议。按用户 2026-10-01 的决定，固定上一轮 main，不跟随远端分支移动。

| 固定项 | 版本 |
| --- | --- |
| OpenWrt main 历史提交 | `c759267c92b0697a6fd6f369164a5ed4c1a6a03c` |
| Linux | `6.18.52` |
| 内核源码 SHA-256 | `2b69564f7d4fea0c859b1959ba33709ee6e9139bd100e30a853b57159a8221b8` |
| 架构与工具链 | AArch64 / Cortex-A55，OpenWrt GCC 14.4.0，musl |
| 参考备份 | TYFZA1 V1.0.5，Taurus/A55；与用户实机版本尚未对应 |

**本工程已进行实际原厂逻辑迁移，仍未完成实机启动、NAND 读写、LAN 或 PON 验证。** 原厂看门狗、外设 IRQ、MDIO/OCP，以及 FIT 签名和 UBI/dm-verity 启动链的依据与改动见 [原厂迁移记录](../docs/tyfza1-vendor-port.md)。不可因编译或验签通过就直接刷机。

2026-10-05 补充了[后续完整移植的研究档案](../docs/tyfza1-porting-dossier.md)，包括新取得的 Taurus NI/DMA、PCIe/ATU、SCFG 和 PON 依赖。该轮没有修改 target 或驱动，也没有扩大本文的硬件验证结论。

## 构建与复核

在 Debian WSL 的 Linux 文件系统中构建，沿用 `$HOME/tyfza1-openwrt`；不要在 Windows 挂载盘内重新构建工具链。以下命令在 Debian 终端执行，工作目录为仓库根目录：

```bash
cd /mnt/d/Document/Codex项目/9617C
```

```bash
sh openwrt/scripts/prepare.sh "$HOME/tyfza1-openwrt"
```

```bash
sh openwrt/scripts/build.sh "$HOME/tyfza1-openwrt" 8
```

`prepare.sh` 只接受固定提交或新目录，不重置不匹配的源码树。`build.sh` 应用已有 overlay 和版本化配置，重新生成 `.config`，因此自定义配置应先另存。默认代理为 `http://127.0.0.1:7897`，可通过 `TYFZA1_PROXY` 指定。

本机 WSL 下，OpenWrt 自带 fakeroot 的 TCP 模式在首次文件访问时报 `libfakeroot: connect: Connection refused`。相同系统的 SysV 模式通过。构建脚本仅在 WSL 上应用 [host patch](host-patches/100-fakeroot-sysv.patch)，清理并重建该 host 工具。不会修改系统网络、关闭安全设置、以 root 构建或替换整个工具链。

已有 target 缺少标准 `generic/target.mk`，导致构建系统匹配不到设备选择，曾只生成通用 rootfs 而跳过全部设备镜像。本次按上游现有 target 的方式补齐 generic 子目标并修正 profile，不改上游镜像生成器。rootfs 设备镜像仅选择 SquashFS，避免把 tar.gz 内容命名成 SquashFS。

该 target 不依赖额外 feeds。构建新 Debian 环境时需要 OpenWrt 官方构建依赖，另需 Python 3、`device-tree-compiler` 和可用的本机 C 编译器以运行离线检查。

```bash
python3 openwrt/tools/test_qspi.py -v
```

```bash
python3 openwrt/tools/test_ram_plan.py -v
```

```bash
python3 openwrt/tools/test_artifacts.py -v
```

```bash
sh openwrt/scripts/check-dtb.sh "$HOME/tyfza1-openwrt/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.52" "$HOME/tyfza1-dtb-recheck" private/tyfza1/from-kernel-fit.dtb
```

实际镜像生成后，进行版本、ARM64 header、DTB、rootfs 可执行文件架构、包清单和升级拒写检查：

```bash
python3 openwrt/tools/verify_artifacts.py "$HOME/tyfza1-openwrt" private/tyfza1/from-kernel-fit.dtb
```

SPI 测试直接编译已有驱动的事务函数，替换 MMIO 和内核辅助调用，检查无数据命令方向、只读限制、尾部字节和超时清理。它不是芯片仿真或实机测试，未使用 QEMU。正常内核编译另行检查驱动 API 与链接。

## 启动边界与产物用途

主设备树沿用社区 TYFZA1 DTB 的 RAM、全部 reserved-memory、CPU MPIDR、PSCI、GICv3、timer 和 UART 信息；不导入其他型号的板级值。CPU 时钟和 DDR 初始化留给已有启动固件，不猜测 PLL 设置。首次启动限制为单核，待完整串口日志确认后再启用其他核。

| 产物类型 | 用途和限制 |
| --- | --- |
| `*-initramfs-Image` | 原生 ARM64 内核和 OpenWrt RAM 根文件系统，首测使用 |
| `*-Image` | 不含 RAM 根文件系统的内核；不能单独作为首测系统 |
| `*-board.dtb` | 仅启动必需设备；QSPI、LAN、PON、FTTR、Wi-Fi 未启用 |
| `*-nand-readonly.dtb` | 额外启用单线 QSPI/SPI-NAND，无分区只读探测 |
| `*-stock-ubi-readonly.dtb` | CRC/BBL 证据对应的只读原厂分区布局，只适用于匹配板型 |
| `*-mdio-probe.dtb` | 独立 MDIO/OCP 探测，不含 LAN 数据通路 |
| `*-rootfs.squashfs` | 根文件系统载荷，不是整机升级包，不是 UBI 镜像 |
| `*.manifest`、`config.buildinfo`、`sha256sums` | 包清单、配置和产物校验 |

NAND 使用 Linux 的 `spi-mem`、SPI-NAND、MTD、UBI/ubiblock 和厂商 on-die ECC 实现。已有 QSPI 驱动只做 Taurus PIO 适配，本次修复无数据命令错误设置写方向的问题，遵循固定 GPL 参考中的 Taurus 分支及完成后的等待。默认禁止 NAND 阵列写入，另有 MTD 只读标志；不通过开启 `allow_writes` 跳过板级验证。

Linux 6.18.52 已支持 W25N02KV。进一步读取厂商 core.c 后发现，其 F50L2G41XA UID 表使用 `2c:24`，对应上游已支持的 Micron MT29F2G01ABAGD ID；不能仅凭商品名认定驱动缺失，也不能据此断言用户机器就是该芯片。原厂启用了工厂坏块隐藏，本份备份的 BBL CRC 有效且条目为零，才支持这里的物理分区解释。

### 将来实机先采集

先断开所有光纤，测量 UART 电平，不连接 USB-TTL 的 VCC。以下为采集指令，不是当前已经取得的数据。原厂 Linux shell 执行：

```sh
sh collect_tyfza1_port.sh --ack-private-output
```

脚本见 [collect_tyfza1_port.sh](../tools/collect_tyfza1_port.sh)。它写入临时私有目录，采集运行 DTB、cmdline、iomem、MTD/ECC、已有 UBI、MDIO 和模块信息。不执行 flash dump、`/dev/mem`、厂商管理命令、UBI attach 或持久写入。保存原版私有输出，不公开身份、账号或校准。

在 **U-Boot 提示符**逐条执行并保存完整串口文本，未知命令报错也应保留：

```text
version
bdinfo
printenv bootcmd bootargs bootpart active_slot boot_slot mtdids mtdparts
printenv loadaddr kernel_addr_r fdt_addr_r ramdisk_addr_r unzip_src unzip_dst
printenv fdt_high initrd_high bootm_low bootm_size verify
help booti
help bootm
help loady
help tftpboot
help ubi
```

不要为采集执行 `saveenv`、flash 写入、UBI 重新格式化或未知地址内存读取。需要补充的环境变量名字应从实际 `bootcmd` 的引用链继续查询，而不是假定上述候选变量必然存在。

### RAM 启动

**实际备份走 `source ${unzip_src}:script`，再由 script 调用 `bootm`。厂商对应配置关闭了 CMD_BOOTI，因此不能把下面的 booti 工具当作默认路线。** 按真实链路制作的 FIT 由 `make_boot_fit.py` 生成，操作与验签说明见原厂迁移记录。

只有在另行确认原机允许 ARM64 `booti`、板型与 DTB 匹配、启动固件及安全策略允许、加载区域已经核实之后，使用 [plan_ram_boot.py](tools/plan_ram_boot.py)。它要求显式提供：

- 本次 `initramfs-Image` 与对应 DTB。
- **用户实机**运行 DTB，而不是将社区 DTB冒充实机采集。
- 从 `bdinfo` 等实测资料得到的 kernel/DTB 加载地址。
- 全部 U-Boot、stack、malloc 及其他装载保留区，逐个作为 `--uboot-range`。
- 已确认的 TFTP 或 YMODEM 传输方式及 `--booti-confirmed`。

```bash
python3 openwrt/tools/plan_ram_boot.py --help
```

工具只做静态地址范围与 ARM64 对齐检查，不测量启动固件，不证明其重定位行为或签名策略。**这里不填入示例刷写地址或假定 RAM 装载地址。** 输出只有 RAM 下载、临时 bootargs 和 `booti`，不包含持久写入。主 DTB 中的 NAND 关闭，先验证 earlycon、正常 ttyS0 输入、timer/IRQ、RAM 和 OpenWrt procd shell，再考虑只读 NAND DTB。

### 原生 UBI 持久安装尚未放行

从备份可确认的只是观察值：PEB 131072、VID offset 2048、data offset 4096、LEB 126976，以及 `ubi_DTB0/1`、`ubi_k0/1`、`ubi_r0/1` 等双槽卷。它们不是用户机器的刷写参数。现有卷表也不等于可删除或覆盖的卷清单。

更关键的是：备份里的 kernel 卷是带签名的 FIT，含压缩内核、DTB 与启动 script。U-Boot 公钥 DTB 有 `required = "conf"`，script 设置 dm-verity。后续核对了公开 SDK 开发证书与备份公钥完全匹配，并补充启用 TOOLS_LIBCRYPTO 的专用 host 验签工具和损坏签名负测试；普通 OpenWrt host 工具的空校验不能作为证据。原生构建工具可在显式提供签名目录和验证 DTB 时签名并验签，且使用上游 veritysetup 保留根文件系统完整性检查；仍未测量用户实机 fuse/boot policy。

当前采用可更新原厂 UBI 内核/根卷的 FIT 与 verity-rootfs 成对载荷，而不是覆盖全部原厂卷的整颗 `.ubi`。本机升级钩子仍拒绝刷写；签名验证通过不等于安装放行。缺少以下证据时，不执行卷更新、不变更活动槽、不格式化整颗 NAND：

1. 用户实机板型、NAND ID、ECC 模式、坏块策略和可信可恢复备份。
2. 实际启动分区、UBI 卷 ID/大小、活动槽和完整 bootcmd 链。
3. 自建镜像被启动固件接受的机制，包括签名、FIT/script、dm-verity 与冷启动策略。
4. 原生只读驱动与原厂读回一致的证据，再进行隔离测试区的写入、读回和掉电验证。

后续安装应使用核实后的原生 MTD/UBI 卷接口，不套用其他型号地址或编程器文件的页外布局。这里没有隐藏的“强制刷入”开关或伪装成原厂签名的产物。

## LAN 与原生 PON 复用

Linux 已有 RTL8221B 所属 Realtek PHY 支持。现已迁移标准 mii_bus 的 MDIO/OCP 访问，仍缺经过实机验证的 NI/DMA/端口映射；没有加载厂商二进制模块。现有 Elnath 社区 NI 驱动明确针对 RTL9607F，其 buffer manager、DMA 窗口与初始化路径不能直接改 compatible 后用于 Taurus。TYFZA1 DTB 中有高位 MMIO 地址和非缓存 DDR 别名，必须逐项确认。

第二阶段复用 [pbs05/ponwrt](https://github.com/pbs05/ponwrt) 的协议层。该项目硬件驱动面向 Airoha，不是 RTL9617C 驱动。详细接口、验证结果与缺口见 [原生 PON 对接记录](../docs/tyfza1-native-pon.md)。本工程不导入 Airoha 光前端固件、他机身份或校准，不装载原厂内核、原厂模块或闭源 OMCI daemon。
