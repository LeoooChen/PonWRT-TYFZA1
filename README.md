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

PonWRT-TYFZA1 是面向天翼 TYFZA1 光猫的 OpenWrt 适配项目。设备采用 Realtek RTL9617C 四核 Cortex-A55，固件基于 Linux 6.18.55。

- **PON 适配**：10G-EPON、XG-PON 线路认证、业务配置和状态管理。
- **双频无线**：2.4 GHz / 5 GHz、WPA2、Wi-Fi 6 和 160 MHz。
- **硬件加速**：有线与 Wi-Fi 双向硬件转发，可在 LuCI 中启用流量卸载。
- **原生体验**：使用 LuCI 管理上网、无线和防火墙，通过 sysupgrade 升级。
- **原厂数据保留**：启动时自动读取本机身份、光学校准和无线校准，安装时保留另一槽原厂系统及配置卷。

## 下载固件

前往 **[Releases](https://github.com/LeoooChen/PonWRT-TYFZA1/releases)** 下载，版本变化见 [更新日志](RELEASE_NOTES.md)。

| 文件 | 用途 |
| --- | --- |
| `tyfza1-telnet-install.tar.gz` | 从原厂系统首次安装，U 盘和网络方式通用 |
| `sysupgrade.bin` | 已安装 OpenWrt 的设备升级 |
| `SHA256SUMS` | 核对下载文件的 SHA-256 |

## 刷入

### 从原生 U-Boot 安装

进入 [TYFZA1 U-Boot](https://github.com/LeoooChen/U-Boot-TYFZA1) 的 Web recovery，通过 LAN 打开 **[192.168.0.1](http://192.168.0.1/)**，在“刷写系统”中上传 `sysupgrade.bin`。安装器校验并写入镜像、读回确认，再按镜像大小分配系统卷和可写数据卷。安装完成后选择启动系统，通过 **[192.168.1.1](http://192.168.1.1/)** 管理 OpenWrt。

恢复页面安装会初始化 OpenWrt 的可写数据；日常保留配置升级使用下文的 sysupgrade。

### 1. 开启原厂 Telnet

用网线连接电脑与光猫，打开 [Telnet 开启页面](http://192.168.1.1:8080/CCOM/OpenTelnet.asp)，然后在电脑终端连接：

```sh
telnet 192.168.1.1
```

原厂 Telnet 默认用户名和密码均为 `telnetadmin`。登录后执行 `su`，再次输入密码 `telnetadmin`，取得 root 权限。若已修改原厂地址或密码，使用实际配置。

### 2. 备份原厂分区

准备一个至少有 **1 GiB 可用空间**的 U 盘，插入光猫。保持设备空闲，在已取得 root 权限的 Telnet 中整段复制运行以下命令，自动将全部物理 NAND 分区、UBI 逻辑卷和配置导出到 U 盘：

<details>
<summary><strong>展开一键备份命令</strong></summary>

```sh
(
    set -eu
    umask 077
    [ "$(id -u)" -eq 0 ] || { echo "请先执行 su 取得 root 权限。"; exit 1; }
    for tool in nanddump saveconfig md5sum; do
        which "$tool" >/dev/null || { echo "缺少备份工具：$tool"; exit 1; }
    done
    usb=
    while read -r source target rest; do
        case "$target" in
            /mnt/usb*)
                [ -z "$usb" ] || { echo "检测到多个 U 盘分区，请只保留一个后重试。"; exit 1; }
                usb=$target ;;
        esac
    done < /proc/mounts
    [ -n "$usb" ] || { echo "未找到已挂载的 U 盘，请插好后重试。"; exit 1; }
    free_kb=$(df -Pk "$usb" | awk 'END {print $4}')
    [ "$free_kb" -ge 1048576 ] || { echo "U 盘可用空间不足 1 GiB。"; exit 1; }
    backup="$usb/TYFZA1-backup-$(date +%Y%m%d-%H%M%S)-$$"
    mkdir "$backup"
    cd "$backup"
    check_size() {
        [ "$(wc -c < "$1")" -eq "$2" ] || { echo "备份文件长度异常：$1，备份未完成。"; exit 1; }
    }
    cat /proc/mtd > partitions.txt
    cat /proc/mounts > mounts.txt
    cat /proc/cmdline > cmdline.txt
    uname -a > system.txt
    printf 'device\tsize\twritesize\toobsize\toffset\tname\n' > nand-layout.txt
    count=0
    for mtd in /sys/class/mtd/mtd[0-9]*; do
        [ -f "$mtd/type" ] || continue
        case "$(cat "$mtd/type")" in nand|mlc-nand) ;; *) continue ;; esac
        device=${mtd##*/}
        size=$(cat "$mtd/size")
        page=$(cat "$mtd/writesize")
        oob=$(cat "$mtd/oobsize")
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$device" "$size" "$page" "$oob" "$(cat "$mtd/offset")" "$(cat "$mtd/name")" >> nand-layout.txt
        echo "正在备份 $device（$(cat "$mtd/name")）……"
        failed=$(cat "$mtd/ecc_failures")
        nanddump --omitoob --bb=dumpbad -f "$device.bin" "/dev/$device" 2> "$device-ecc.log"
        check_size "$device.bin" "$size"
        [ "$(cat "$mtd/ecc_failures")" -eq "$failed" ] || { echo "$device 出现不可校正的 ECC 错误，备份未完成。"; exit 1; }
        nanddump -n -o --bb=dumpbad -f "$device.raw" "/dev/$device" 2> "$device-raw.log"
        check_size "$device.raw" "$((size / page * (page + oob)))"
        count=$((count + 1))
    done
    [ "$count" -gt 0 ] || { echo "未找到物理 NAND 分区，备份未完成。"; exit 1; }
    printf 'device\tsize\tname\n' > ubi-layout.txt
    count=0
    for volume in /sys/class/ubi/ubi[0-9]*_[0-9]*; do
        [ -f "$volume/name" ] || continue
        device=${volume##*/}
        size=$(cat "$volume/data_bytes")
        printf '%s\t%s\t%s\n' "$device" "$size" "$(cat "$volume/name")" >> ubi-layout.txt
        echo "正在备份 $device（$(cat "$volume/name")）……"
        dd if="/dev/$device" of="$device.bin" bs=2048 2> "$device.log"
        check_size "$device.bin" "$size"
        count=$((count + 1))
    done
    [ "$count" -gt 0 ] || { echo "未找到 UBI 卷，备份未完成。"; exit 1; }
    saveconfig -f config-cs.xml cs
    saveconfig -f config-hs.xml hs
    [ -s config-cs.xml ] && [ -s config-hs.xml ]
    sync
    md5sum ./*.bin ./*.raw ./*.xml ./*.txt ./*.log > MD5SUMS
    md5sum -c MD5SUMS
    touch BACKUP_COMPLETE
    sync
    echo "备份完成：$backup，请将整个目录复制到电脑保存。"
)
```

</details>

完成后，备份目录中会生成 `BACKUP_COMPLETE` 和 `MD5SUMS`。`.bin` 保存 ECC 校正后的分区数据及 UBI 卷，`.raw` 保存 NAND 主数据和 OOB，XML 文件保存原厂配置。

### 3. 安装固件

**方式一：U 盘**

将 `tyfza1-telnet-install.tar.gz` 原样复制到 U 盘根目录，插入光猫 USB 接口。在光猫 Telnet 中**整段复制并运行**以下命令，即可自动查找安装包并安装：

```sh
(
    set -eu
    set -- /mnt/usb*/tyfza1-telnet-install.tar.gz
    [ -f "$1" ] || { echo "未找到安装包，请确认文件已放入 U 盘根目录，且 U 盘已插好并挂载。"; exit 1; }
    [ "$#" -eq 1 ] || { echo "找到多个安装包，请只保留一个存有安装包的 U 盘后重试。"; exit 1; }
    echo "找到安装包：$1"
    install_dir=/tmp/tyfza1-install.$$
    mkdir "$install_dir"
    trap 'rm -rf "$install_dir"' EXIT
    tar -xzf "$1" -C "$install_dir"
    cd "$install_dir"
    sh ./install.sh
)
```

<details>
<summary><strong>方式二：通过电脑传包（Python HTTP 服务）</strong></summary>

在电脑上进入安装包所在目录，运行并保持终端开启：

```sh
python3 -m http.server 8888
```

在光猫 Telnet 中执行，将 `PC_IP` 替换为电脑的局域网 IP：

```sh
(
    set -eu
    install_dir=/tmp/tyfza1-install.$$
    mkdir "$install_dir"
    trap 'rm -rf "$install_dir"' EXIT
    wget -O "$install_dir/package.tar.gz" http://PC_IP:8888/tyfza1-telnet-install.tar.gz
    tar -xzf "$install_dir/package.tar.gz" -C "$install_dir"
    cd "$install_dir"
    sh ./install.sh
)
```

</details>

脚本将固件写入原厂非活动系统槽，完成后自动重启。通过 LAN 打开 **[192.168.1.1](http://192.168.1.1/)**，进入 OpenWrt 管理页面。

安装图文与讨论见 [恩山发布帖](https://www.right.com.cn/forum/forum.php?mod=viewthread&tid=8493081&page=1)。

### 后续升级

在 **系统 → 备份与更新** 中上传 `sysupgrade.bin`；也可以将文件传到光猫后执行：

```sh
sysupgrade /tmp/sysupgrade.bin
```

默认保留配置。清空配置升级使用 `sysupgrade -n /tmp/sysupgrade.bin`。

首次安装及后续升级按系统镜像实际大小分配 rootfs，其余空闲 UBI 空间用于可写数据卷。全千兆版实机的数据卷约 41 MiB，初装空闲约 34.4 MiB；其余容量用于 UBIFS 元数据、日志和保留空间。原厂应用、framework、身份和校准卷均保留。

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

命令执行成功后，重启光猫。

</details>

## 开始使用

首次进入管理页面后，设置管理员密码。首次安装和清空配置升级时，线路协议从原厂配置初始化；保留配置升级沿用已有设置。

| 入口 | 配置内容 |
| --- | --- |
| **网络 → PON 管理** | 按线路选择 PON 模式，填写运营商要求的认证参数；MAC、SN 等身份项留空时使用本机原厂值 |
| **网络 → 接口 → WAN** | 设置 DHCP 或 PPPoE、上网 VLAN 和 IPv6；默认业务设备为 `pon0`，VLAN 子设备为 `pon0.VID` |
| **网络 → 无线** | 设置 SSID、密码、信道和带宽，启用无线 |
| **网络 → 防火墙** | 配置软件流量卸载和硬件流量卸载 |

使用 DFS 信道时，无线会先完成 CAC 检测，再开始广播 SSID。系统日志中的 `DFS-CAC-START`、`DFS-CAC-COMPLETED` 和 `AP-ENABLED` 分别对应检测开始、检测完成和 AP 启用。

有线接口使用 `lan1`、`lan2` 等命名，PON 业务接口为 `pon0`。
业务 MAC 在启动时从原厂 `ELAN_MAC_ADDR` 读取，自定义值在网络接口中设置。PON 管理中的 ONU MAC 用于 EPON 注册与 OAM。
保留配置升级会自动迁移原有网络、防火墙、LED 和 SQM 配置中的设备引用，包括 VLAN 子设备。

## 软件源

固件配置了 OpenWrt 官方 snapshot 软件源、公钥和 HTTPS CA 证书。可在 LuCI 软件包页面安装软件，或在终端执行：

```sh
apk update
apk add htop nano iperf3
```

常用插件所需的内核模块和运行库随固件预装，涵盖 VPN、SQM、代理、多 WAN 和流量统计。具体项目见[预装依赖清单](port/configs/tyfza1-compat.config)，软件包架构和安装验证见[兼容说明](port/APK_COMPATIBILITY.md)。

## 自行编译

### GitHub Actions

1. **Fork** 本仓库，在 **Actions** 中启用工作流。
2. 打开 **Build TYFZA1 firmware → Run workflow**，填写 `release_tag`，例如 `v0.8.0-beta`。
3. 如需自定义软件包，将配置文件提交到仓库，并在 `config_file` 中填写路径。
4. 构建完成后，从 **Releases** 或本次运行的 **Artifacts → firmware-and-build-log** 下载固件。

工作流自动下载编译依赖和运行时文件，并准备 FIT 签名材料。

<details>
<summary><strong>本地开发与构建</strong></summary>

使用 Linux 构建环境，安装 LLVM/Clang 18、Rust 1.96.1 及 `aarch64-unknown-linux-musl` 目标。按[构建工作流](.github/workflows/build-release.yml)中的步骤准备运行时文件和 FIT 签名目录，然后在仓库根目录执行：

```sh
sh port/scripts/prepare.sh sources/openwrt
TYFZA1_RUNTIME_INPUTS=/path/to/runtime-inputs \
TYFZA1_SIGNING_KEYS=/path/to/signing \
sh port/scripts/build.sh sources/openwrt 4
sh scripts/package_release.sh sources/openwrt artifacts/release
```

自定义软件包可先运行 `sh port/scripts/sync-overlay.sh sources/openwrt` 和 `sh scripts/prepare_luci_feeds.sh sources/openwrt`，再进入 `sources/openwrt` 执行 `make menuconfig`。后续构建保留 `.config`，也可通过 `TYFZA1_CONFIG` 指定配置文件。

常用插件的预装依赖配置位于 `port/configs/tyfza1-compat.config`，同步脚本会将其合并入构建配置。

固件保留原厂 UBI 卷编号：内核上限 6 MiB，rootfs 上限约 32.09 MiB。rootfs 按镜像大小取整到 UBI 逻辑擦除块，可写数据卷使用剩余空间；升级时自动调整两者大小。构建脚本自动检查镜像大小。

</details>

## 参与项目

欢迎通过 [Issues](https://github.com/LeoooChen/PonWRT-TYFZA1/issues) 反馈问题、通过 [Pull Requests](https://github.com/LeoooChen/PonWRT-TYFZA1/pulls) 提交改进，也欢迎在 [恩山交流帖](https://www.right.com.cn/forum/forum.php?mod=viewthread&tid=8493081&page=1) 分享线路测试结果。

提交问题时，请提供固件版本、PON 模式、复现步骤和脱敏后的日志。

## 致谢与许可

感谢 [OpenWrt](https://github.com/openwrt/openwrt) 提供系统基础，感谢 [PonWrt](https://github.com/pbs05/ponwrt) 提供 PON 与 OpenWrt 整合、原厂数据保留和硬件转发方面的参考。OMCI 协议引擎基于其 [开源实现](https://github.com/pbs05/openwrt-pon-userspace)，RTL9617C 底层按本机原厂实现独立适配。

项目采用 [GNU GPL v2](COPYING) 许可；第三方组件及原厂设备微码遵循各自授权。
