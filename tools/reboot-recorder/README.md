# TYFZA1 离线重启取证工具 1.0.0

面向 PonWRT 0.8.3-beta / RTL9617C / aarch64。静态 musl 可执行文件，
使用固件已有的 `logread`、BusyBox、procd；运行时不联网、不需安装依赖。
这是临时故障取证工具，不是重启修复补丁。

## 收集什么

- `kernel.log`：独立只读打开 `/dev/kmsg`，从尚存的启动环形缓冲读到实时消息。
  保留内核 priority、sequence、单调时间，另加接收时 wall/mono 时间。
  与原 logd 并行，不清空内核环形缓冲。
- `system.log`：`logread -f -l 4096 -Z 0`，现存最多 4096 条及后续的
  系统、procd、网络、PON、应用日志。
  排除重复的 kernel facility；logread 断线后重连，可能重放旧消息。
- `health.log`：每 30 秒记录内存、负载、压力、接口计数、温度、
  看门狗状态/剩余时间、UBI 坏块/擦写次数；每 5 分钟加 CPU/IRQ/softirq、
  vmstat、模块和进程 stat。无主动 debugfs/MMIO 读写、无进程参数/密码采集。
- `events.log`：独立保留 panic/Oops/BUG/OOM/watchdog/reboot/存储错误等关键消息，
  以及采集启停、重连、序列缺失、限流丢弃的说明。关键词命中不等于发生故障。
- `boot.txt`：固件、内核、启动参数、挂载、分区、panic 策略、watchdog bootstatus。
- `shutdown-requested.txt`：只有 OpenWrt orderly shutdown 钩子运行时写入。
  此文件仅证明关机流程开始，不证明完成；缺失也不能单凭这一点判定掉电或看门狗。
- 如果未来固件在 `/sys/fs/pstore` 提供文件，则启动时复制（不删除）原始记录，
  单文件最多 256 KiB。此工具不会启用或配置 pstore。

## 明确的限制

当前 0.8.3-beta 构建配置为 `CONFIG_PSTORE is not set`，
无 ramoops；`CONFIG_SOFTLOCKUP_DETECTOR`、`CONFIG_DETECT_HUNG_TASK` 也未启用。
不能在现有内核上靠安装用户态程序补出崩溃持久化功能。

`/dev/kmsg` 覆盖内核中通常也会输出到串口的 printk 消息，**不是串口录制**。
读取同一台机器的 `/dev/ttyS0` 只能读 RX，不能抓它自己的 TX，
还可能争抢登录控制台；本工具故意不打开它。U-Boot、直接 UART 输出、
硬件断电/看门狗瞬时复位、内核完全卡死后的最后消息，均可能捕获不到。

普通脏日志约每 5 秒 `fdatasync`；关键消息额外触发同步，每秒最多一次。
这只是正常调度、闪存正常时的目标间隔，**不是最大丢失时间保证**；
系统卡死、掉电或闪存损坏时仍可能丢失最后消息。完整解决通常需要
下一版固件预留经核实安全的 RAM 区域并启用 ramoops/pstore，仍不能保证掉电保留。

目前 RTL9617C watchdog 驱动未实现真实复位原因读取，bootstatus=0
不能排除 watchdog。工具不会打开 `/dev/watchdog`、喂狗、停狗、改 timeout、
改 panic/sysctl、触发 SysRq、注入崩溃、修改 U-Boot 或闪存分区。

## 持久化与容量

目录：`/overlay/tyfza1-recorder/boot-<kernel-boot-id>/`。
`current` 指向当前启动批次。不依赖系统时间正确或 NTP。
权限为目录 0700、日志 0600。日志可能自然包含公网地址、MAC 等隐私信息，
请勿将未脱敏导出文件公开发布。

每个流保留当前文件及 `.1`、`.2`（`.2` 最旧）：

| 流 | 单文件上限 | 正常每分钟写入配额 |
|---|---:|---:|
| kernel | 1 MiB | 64 KiB |
| system | 1 MiB | 64 KiB |
| health | 512 KiB | 64 KiB |
| events | 128 KiB | 16 KiB |

一次启动的四个流合计最多约 7.875 MiB，另有少量元数据。
采集器启动第一分钟 kernel/system 各允许 256 KiB，用于读现有缓冲。
配额包含接收时间头；满额后明确记录 dropped bytes，关键流有独立配额。
轮转会覆盖**同一启动批次**最旧的文件；这不是无限全量保存。
**以前启动批次不自动删除**，避免多次重启把第一次故障证据挤掉。
可用空间低于 16 MiB 时暂停写入；首次/再次启动要求至少 17 MiB。
这会牺牲后续取证，不能声称空间满了还会持续记录。请导出后手动清理旧批次。
停止/禁用服务不删除任何证据。

普通重启、断网、拔掉电脑不会清理该目录。**恢复出厂、重建 overlay、
sysupgrade/刷机和 NAND 损坏不保证保留**，执行前务必导出。
为避免把诊断工具隐式带入未来不兼容固件，安装器不修改 sysupgrade 保留列表。

写入配额限制的是逻辑写入量，不是 NAND 物理写放大。约 5 秒同步提高
故障证据存活率，但增加擦写；建议捕获复现后停止，不作为永久高频记录器。

## 编译与测试（Ubuntu 24.04）

```sh
cd /home/leo/PonWRT-TYFZA1/tools/reboot-recorder
gcc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  test_recorder.c -o /tmp/test-recorder
/tmp/test-recorder
shellcheck -s sh build.sh install.sh tyfza1-recorder
sh build.sh /home/leo/tyfza1-pon-mac-openwrt-20261030 \
  /home/leo/PonWRT-TYFZA1/artifacts/reboot-recorder-1.0.0
```

不运行 OpenWrt 全量编译，不修改当前 main 的驱动或网络配置。
`SHA256SUMS` 校验随包程序、脚本和源代码。只接受 TYFZA1 且 `/overlay`
为可写 UBIFS 的实际启动；拒绝在 tmpfs/initramfs 中假装持久化。

## 安装

将离线包通过 SSH 传到 `/tmp`，解压后执行包内 `sh install.sh`。
procd 管理并有界重启采集器，S11 自启、K88 在卸载文件系统前记录关机意图。
不需要刷新固件或重启设备。重复安装拒绝覆盖现有文件。

```sh
tyfza1-recorder status
/etc/init.d/tyfza1-recorder stop      # 停止，不删除证据
/etc/init.d/tyfza1-recorder disable   # 取消下次启动
/etc/init.d/tyfza1-recorder enable
/etc/init.d/tyfza1-recorder start
```

从电脑导出（归档流直接传到电脑，不在光猫 RAM 中生成整包）：

```sh
ssh root@192.168.1.1 'tyfza1-recorder export' > tyfza1-reboot-evidence.tar.gz
```

导出是活跃日志的尽力快照，不是原子快照；GNU tar 可能提示文件正在变化。
需要严格一致时先停止采集，再导出并立即启动。不要为了测试本工具而重启、
触发 panic、停用 watchdog 或拉掉电源，这可能毁掉尚未导出的证据。

## 发生下一次自动重启后

直接重新 SSH 连接，导出完整目录，不先刷机/恢复出厂。
按旧批次的最后 mono/内核序列检查 `events.log*`、`kernel.log*`、
`system.log*` 及 `health.log*`。有 Oops/调用栈时定位具体驱动；
若只有日志突然中断，则仍需区别供电、看门狗、死锁和闪存故障，不能直接定责。
