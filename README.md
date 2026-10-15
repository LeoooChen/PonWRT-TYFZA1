# PonWRT-TYFZA1

感谢 [PonWrt](https://github.com/pbs05/ponwrt) 在 PON 与 OpenWrt 整合、原厂身份和校准保留、硬件转发及排障思路上提供的参考。本项目针对天翼 TYFZA1 / Realtek RTL9617CVW 独立适配，OMCI 协议引擎使用其 GPL 源码；硬件配置以本机原厂实现为依据。

**版本：0.3.1-alpha** · OpenWrt · Linux 6.18.55 · 四核 Cortex-A55

本次更新见 [更新日志](RELEASE_NOTES.md)。

## 功能与进度

| 功能 | 状态 |
| --- | --- |
| 系统启动、NAND/UBI、watchdog | 已实测 |
| 原厂 telnet 安装、sysupgrade、断电启动 | 已实测 |
| 四 LAN 网桥 | 已接入；四口逐口待测试 |
| 2.5G 网口 | 驱动已接入，链路与硬件转发待测试 |
| 双频 WiFi、WPA2、HT/VHT、Wi-Fi 6 | HT/VHT80 已实测；源码已接入 160 MHz，尚未上机，DFS 信道仍受限，见[适配记录](docs/wifi-160mhz.md) |
| IPv4 路由/NAT、WiFi 双向硬件加速 | 双频上下行硬件路径已实测；持续高负载丢包待优化 |
| EPON（1G/1G） | 独立 MAC/PCS/FEC、MPCP 和 CTC OAM 已接入；业务与硬件转发待测试 |
| 10G-EPON 非对称（10G/1G） | 注册、CTC OAM、LOID、FEC、DHCP 和双向硬件路径已实测；PPPoE、桥接待测试 |
| 10G-EPON 对称（10G/10G） | 独立 MAC/PCS/FEC、MPCP 和 CTC OAM 已接入；业务与硬件转发待测试 |
| GPON | GTC 激活、PLOAM、OMCI 和 GEM/T-CONT 业务映射已接入；OLT 互通与硬件转发待测试 |
| XG-PON | 非对称 XGTC 激活、PLOAM、OMCI 和业务映射已接入；OLT 互通与硬件转发待测试 |
| XGS-PON | 对称 XGTC 激活、PLOAM、OMCI 和业务映射已接入；OLT 互通与硬件转发待测试 |
| PPPoE | 已接入 OpenWrt 原生配置；拨号、业务与硬件转发待测试 |
| 桥接 | 已接入 Linux 网桥自动学习、VLAN/QinQ 和二层硬件转发；实机待测试 |
| IPv6 | 已加入 DHCPv6、RA 和原生页面；公网、前缀下发与硬件转发待测试 |
| VLAN/QinQ、多 LLID、多业务 | 待测试 |
| 冷启动、WiFi 停启、防火墙重载 | 待测试 |

支持手动选择 PON 模式。认证参数均可留空；MAC、SN 和厂商标识留空时使用本机原厂身份。镜像的身份和光学/射频校准从当前设备的原厂分区读取。

无需手动提取或导入校准数据。首次安装将 OpenWrt 写入原厂非活动系统槽，保留另一槽的原厂系统和独立的 `ubi_Config` 配置卷。每次启动只读访问该配置卷，将本机光学、射频校准数据加载到 `/tmp` 后交给驱动使用。

## 下载与刷入

在 [Releases](https://github.com/LeoooChen/PonWRT-TYFZA1/releases) 下载 `tyfza1-telnet-install.tar.gz` 或 `sysupgrade.bin`。

### 原厂系统安装

启用原厂 telnet，接好 LAN 网线并登录光猫。在电脑的安装包目录运行：

```sh
python3 -m http.server 8000
```

在光猫 telnet 中执行，将 `LAN_SERVER` 替换为电脑的 LAN 地址：

```sh
mkdir -p /tmp/tyfza1-install
wget -O /tmp/tyfza1-install.tar.gz http://LAN_SERVER:8000/tyfza1-telnet-install.tar.gz
tar -xzf /tmp/tyfza1-install.tar.gz -C /tmp/tyfza1-install
cd /tmp/tyfza1-install
sh ./install.sh
```

安装脚本更新原厂已有的 UBI 固件槽，保留原厂身份和校准，完成后自动重启。

### OpenWrt 升级

通过 **系统 → 备份与更新** 上传 `sysupgrade.bin`，或执行：

```sh
sysupgrade /tmp/sysupgrade.bin
```

默认保留配置；`sysupgrade -n` 恢复默认配置。

## 配置

通过 LAN 打开 `http://192.168.1.1/`，设置管理密码。

- **网络 → PON 管理**：选择线路协议，填写运营商要求的 LOID、密码、SN、ONU MAC 等可选参数；OMCI 增强认证 PSK 仅在线路需要时填写。
- **网络 → 接口 → WAN**：使用 OpenWrt 原生 DHCP/PPPoE、VLAN 和 IPv6 设置。默认业务设备为 `pon-data0`，VLAN 使用 `pon-data0.VID`。
- **网络 → 无线**：设置名称、密码和国家/地区，然后启用无线。

## Fork 云端编译

1. 点击仓库右上角 **Fork**，然后进入自己的仓库。
2. 打开 **Actions**，启用工作流。
3. 选择 **Build TYFZA1 firmware → Run workflow**，选择分支并填写 `release_tag`；自定义软件包时，将配置提交到仓库，在 `config_file` 填写配置文件路径。
4. 编译成功后，在自己仓库的 **Releases** 下载固件，或在本次 Actions 运行的 **Artifacts** 中下载 `firmware-and-build-log`。

密钥和编译所需附件会自动获取，无需配置 Secrets。

本地运行 `sh port/scripts/sync-overlay.sh sources/openwrt` 后，在 OpenWrt 目录执行 `make menuconfig` 选择软件包，再运行 `sh port/scripts/build.sh sources/openwrt 4`。后续构建保留 `.config`；也可通过 `TYFZA1_CONFIG=/配置文件路径` 指定配置。

镜像按所选软件包压缩生成，沿用原厂 UBI 卷大小：

| 内容 | 大小上限 |
| --- | --- |
| `kernel.itb` | 6,291,456 字节（6 MiB，原厂 U-Boot 读取长度） |
| `rootfs.squashfs` | 33,648,640 字节（约 32.09 MiB，原厂 rootfs 卷容量） |
| 可写 `rootfs_data` | 16 MiB，UBIFS 可用空间扣除文件系统开销 |

`sysupgrade.bin` 和安装包中的内核、rootfs 分别写入对应卷。编译时检查镜像上限；安装和升级时读取设备实际卷容量，超出容量会报错。

## 协议

源代码采用 [GNU GPL v2](COPYING)。第三方组件和原厂设备微码保留各自授权。
