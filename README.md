<h1 align="center">PonWRT · TYFZA1</h1>

<p align="center">
  天翼 TYFZA1 的 OpenWrt 固件<br>
  PON 光口 · 双频 Wi-Fi 6 · 硬件加速
</p>

<p align="center">
  <a href="https://github.com/LeoooChen/PonWRT-TYFZA1/releases"><img src="https://img.shields.io/github/v/release/LeoooChen/PonWRT-TYFZA1?include_prereleases&amp;label=release&amp;color=0f766e" alt="最新版本"></a>
  <a href="https://github.com/LeoooChen/PonWRT-TYFZA1/actions/workflows/build-release.yml"><img src="https://img.shields.io/github/actions/workflow/status/LeoooChen/PonWRT-TYFZA1/build-release.yml?label=build" alt="固件构建"></a>
  <a href="COPYING"><img src="https://img.shields.io/badge/license-GPL--2.0-blue" alt="GPL-2.0"></a>
</p>

<p align="center">
  <a href="#下载固件">下载固件</a> ·
  <a href="#刷入">刷入指南</a> ·
  <a href="RELEASE_NOTES.md">更新日志</a> ·
  <a href="https://github.com/LeoooChen/PonWRT-TYFZA1/issues">问题反馈</a>
</p>

## 关于项目

PonWRT-TYFZA1 是面向天翼 TYFZA1 光猫的 OpenWrt 适配项目。设备采用 Realtek RTL9617CVW 四核 Cortex-A55，固件基于 Linux 6.18.55。

- **PON 适配**：支持 10G-EPON、XG-PON（EPON、GPON 待测试），提供线路认证和状态管理。
- **双频无线**：2.4 GHz / 5 GHz、WPA2、Wi-Fi 6 和 160 MHz。
- **硬件加速**：有线与 Wi-Fi 双向硬件转发，可在 LuCI 中启用流量卸载。
- **原生体验**：使用 LuCI 管理上网、无线和防火墙，通过 sysupgrade 升级。
- **原厂数据保留**：启动时自动读取本机身份、光学校准和无线校准，安装时保留另一槽原厂系统及配置卷。

目前已实测非对称 10G-EPON 注册、认证与 DHCP 上网，以及双频 Wi-Fi 和双向硬件路径。当前重点是 XG-PON 运营商互通、PPPoE/桥接及持续高负载优化。

## 下载固件

前往 **[Releases](https://github.com/LeoooChen/PonWRT-TYFZA1/releases)** 下载，版本变化见 [更新日志](RELEASE_NOTES.md)。

| 文件 | 用途 |
| --- | --- |
| `tyfza1-telnet-install.tar.gz` | 从原厂系统首次安装，U 盘和网络方式通用 |
| `sysupgrade.bin` | 已安装 OpenWrt 的设备升级 |
| `SHA256SUMS` | 核对下载文件的 SHA-256 |

## 刷入

### 1. 开启原厂 Telnet

用网线连接电脑与光猫，打开 [Telnet 开启页面](http://192.168.1.1:8080/CCOM/OpenTelnet.asp)，然后在电脑终端连接：

```sh
telnet 192.168.1.1
```

按帖子中的原厂默认配置，用户名、密码均为 `telnetadmin`。登录后执行 `su`，再次输入密码 `telnetadmin`，取得 root 权限。若已修改原厂地址或密码，使用实际配置。

刷入前备份原厂数据，刷写期间保持供电。接下来任选一种方式传入安装包。

### 2. 传入安装包

**方式一：U 盘**

将 `tyfza1-telnet-install.tar.gz` 原样复制到 U 盘根目录，插入光猫 USB 接口。在光猫 Telnet 中查看挂载位置和安装包：

```sh
cat /proc/mounts | grep '/mnt/usb'
ls -l /mnt/usb*/tyfza1-telnet-install.tar.gz
```

以下以 `/mnt/usb1_2` 为例，替换成上一步显示的实际目录，解压到内存：

```sh
mkdir -p /tmp/tyfza1-install
tar -xzf /mnt/usb1_2/tyfza1-telnet-install.tar.gz -C /tmp/tyfza1-install
```

<details>
<summary><strong>方式二：通过电脑传包（Python HTTP 服务）</strong></summary>

在电脑上进入安装包所在目录，运行并保持终端开启：

```sh
python3 -m http.server 8888
```

在光猫 Telnet 中执行，将 `PC_IP` 替换为电脑的局域网 IP：

```sh
mkdir -p /tmp/tyfza1-install
wget -O /tmp/tyfza1-install.tar.gz http://PC_IP:8888/tyfza1-telnet-install.tar.gz &&
tar -xzf /tmp/tyfza1-install.tar.gz -C /tmp/tyfza1-install
```

</details>

### 3. 执行安装

确认解压成功，在光猫 Telnet 中执行：

```sh
cd /tmp/tyfza1-install && sh ./install.sh
```

脚本将固件写入原厂非活动系统槽，完成后自动重启。通过 LAN 打开 **[192.168.1.1](http://192.168.1.1/)**，进入 OpenWrt 管理页面。

安装图文与讨论见 [恩山发布帖](https://www.right.com.cn/forum/forum.php?mod=viewthread&tid=8493081&page=1)。

### 后续升级

在 **系统 → 备份与更新** 中上传 `sysupgrade.bin`；也可以将文件传到光猫后执行：

```sh
sysupgrade /tmp/sysupgrade.bin
```

默认保留配置，`sysupgrade -n` 可恢复默认配置。

<details>
<summary><strong>切回保留的原厂系统</strong></summary>

SSH 登录 OpenWrt 后执行以下命令。它会根据当前系统槽选择另一槽的原厂系统：

```sh
(
    set -e
    slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
    case "$slot" in
        0) stock=1 ;;
        1) stock=0 ;;
        *) echo "无法识别当前系统槽"; exit 1 ;;
    esac
    printf 'sw_commit %s\nsw_tryactive 2\n' "$stock" > /tmp/tyfza1-stock.env
    fw_setenv -c /etc/fw_env.config -s /tmp/tyfza1-stock.env
    fw_printenv -c /etc/fw_env.config sw_commit sw_tryactive
    sync
)
```

确认没有报错后，重启光猫。

</details>

## 开始使用

首次进入管理页面后，先设置管理员密码。

| 入口 | 配置内容 |
| --- | --- |
| **网络 → PON 管理** | 按线路选择 PON 模式，填写运营商要求的认证参数；MAC、SN 等身份项留空时使用本机原厂值 |
| **网络 → 接口 → WAN** | 设置 DHCP 或 PPPoE、上网 VLAN 和 IPv6；默认业务设备为 `pon-data0`，VLAN 子设备为 `pon-data0.VID` |
| **网络 → 无线** | 设置 SSID、密码并启用无线；使用 160 MHz 时等待 DFS/CAC 完成 |
| **网络 → 防火墙** | 配置软件流量卸载和硬件流量卸载 |

## 自行编译

### GitHub Actions

1. **Fork** 本仓库，在 **Actions** 中启用工作流。
2. 打开 **Build TYFZA1 firmware → Run workflow**，填写新的 `release_tag`，例如 `v0.4.1-custom`。
3. 如需自定义软件包，将配置文件提交到仓库，并在 `config_file` 中填写路径。
4. 构建完成后，从 **Releases** 或本次运行的 **Artifacts → firmware-and-build-log** 下载固件。

工作流自动获取编译依赖、公共运行时文件和 FIT 签名配置，无需额外设置 Secrets。

<details>
<summary><strong>本地开发与构建</strong></summary>

使用 Linux 构建环境，安装 LLVM/Clang 18、Rust 1.96.1 及 `aarch64-unknown-linux-musl` 目标。完整依赖和公共构建输入的获取方式见 [构建工作流](.github/workflows/build-release.yml)。

准备好运行时文件与签名目录后，在仓库根目录执行：

```sh
sh port/scripts/prepare.sh sources/openwrt
TYFZA1_RUNTIME_INPUTS=/path/to/runtime-inputs \
TYFZA1_SIGNING_KEYS=/path/to/signing \
sh port/scripts/build.sh sources/openwrt 4
sh scripts/package_release.sh sources/openwrt artifacts/release
```

自定义软件包可先运行 `sh port/scripts/sync-overlay.sh sources/openwrt` 和 `sh scripts/prepare_luci_feeds.sh sources/openwrt`，再进入 `sources/openwrt` 执行 `make menuconfig`。后续构建保留 `.config`，也可通过 `TYFZA1_CONFIG` 指定配置文件。

固件沿用原厂 UBI 布局：内核上限 6 MiB，rootfs 上限约 32.09 MiB，可写数据卷 16 MiB。构建脚本自动检查镜像大小。

</details>

## 参与项目

欢迎通过 [Issues](https://github.com/LeoooChen/PonWRT-TYFZA1/issues) 反馈问题、通过 [Pull Requests](https://github.com/LeoooChen/PonWRT-TYFZA1/pulls) 提交改进，也欢迎在 [恩山交流帖](https://www.right.com.cn/forum/forum.php?mod=viewthread&tid=8493081&page=1) 分享线路测试结果。

反馈时请附固件版本、PON 模式、问题现象和相关日志，分享前隐去宽带账号、LOID、SN 等个人信息。

## 致谢与许可

感谢 [OpenWrt](https://github.com/openwrt/openwrt) 提供系统基础，感谢 [PonWrt](https://github.com/pbs05/ponwrt) 提供 PON 与 OpenWrt 整合、原厂数据保留和硬件转发方面的参考。OMCI 协议引擎基于其 [开源实现](https://github.com/pbs05/openwrt-pon-userspace)，RTL9617C 底层按本机原厂实现独立适配。

项目采用 [GNU GPL v2](COPYING) 许可；第三方组件及原厂设备微码遵循各自授权。
