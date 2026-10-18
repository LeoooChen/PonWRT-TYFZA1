# TYFZA1 温度、CPU 频率与无线功率

## 使用入口

- LuCI 状态概览的目标平台显示 `Realtek/RTL9617C（xx.x°C）`，温度跟随概览刷新。读取不到传感器时明确显示“温度未提供”，RPC 失败显示“温度读取失败”。这里只改变展示文本，固件目标标识仍为 `rtl9617c/generic`。
- 系统 → CPU 频率：读取硬件当前频率、SoC 温度，选择四核共享运行频率。保存应用和重启后均通过 `system.cpu.frequency` 生效，单位 kHz，默认 `1000000`。
- 网络 → 无线 → 编辑 → 设备配置 → 常规设置 → 最大发射功率（旁边标为“当前功率上限”）：沿用标准 `wireless.<radio>.txpower`，单位 dBm；留空使用自动上限，两个频段分别设置。

## 本机原厂依据

原厂 FIT：`evidence/wireless-study-20261009-104746/evidence/stock/kernel-k0.fit`，SHA256 `4af8d36db16492b4c764bc856b04b68f4403b893d9fabc0bfe00625c509d9743`。
解包后内核 SHA256 `4a43feb73503f3a25d7791a31e618abfdd5114ecbaa2ac0e263b908097980e95`，Linux 5.10.70，2024-12-02 构建。以下地址是该内核恢复符号后的虚拟地址。

- `cortina_get_temp@ffffffc0106b0db0` 从传感器偏移 `0x0c` 读取数据，即物理地址 `0xf4320270`。正温度为 `[17:10] × 1000 + [9:0]`；bit18 为负时，整数先减 256，再乘 1000 并减去小数字段。原生实现保留本机实际算法，不采用外部 6.6 驱动的不同负温度公式，也不臆测小数字段需要除以 1024。
- `cortina_thermal_probe@ffffffc0106b0e10` 在 `0xf4320264` 起的 16 字节窗口写 A=`0x07f80000`、B=`0x00374000`。**本机芯片 ID 为 `0x6706`，必须选择 C=`0x2102011e` → `0x2102011f` 的专用分支**，等待 300 ms 后读取 C、DATA、DATA。`0x0101010c` → `0x0101011d` 是其他芯片的分支，先前错误使用它导致实机显示约 -50°C。新驱动保留本机分支，用可睡眠等待和 Linux thermal 接口，并核对 A/B 校准及 C 的 ADC 时钟、过采样、供电、reset 等可读字段；失配返回错误。
- `rtl8277c_cpu_init@ffffffc010471930` 向 `0xf4320204` 写 `0xff000000`，配置切换保护。
- `rtl8277c_cpu_set_rate@ffffffc010472500` 的硬件次序为：CPU 临时选择 FPLL/F2C → CPLL reset 清零 → 重配 CPLL → mode override → reset 置 1 → 等待 200 µs → 设置 Cortex 分频和 override → CPU 切回 CPLL。
- `rtl8277c_cpu_strap_speed_table@ffffffc010a32b00` 与 `rtl8277c_pll_param_table@ffffffc010a329d8` 给出下表；已逐项核对新驱动和四核共享 OPP 表。

| MHz | PLL DIVN | PLL PREDIV | Cortex 分频 |
| --- | --- | --- | --- |
| 400 | 240 | 5 | 3 |
| 625 | 250 | 5 | 2 |
| 700 | 168 | 3 | 2 |
| 800 | 192 | 3 | 2 |
| 900 | 216 | 3 | 2 |
| 1000 | 200 | 5 | 1 |
| 1100 | 220 | 5 | 1 |
| 1200 | 240 | 5 | 1 |

新 CPU 驱动只提供本机 CPU 时钟，不移植原厂整套时钟框架。PE 使用 CPLL 时在任何切频写入之前拒绝请求；正常 FPLL、PE 和 AXI 的控制字段均不写入。CPU 临时源切换必须读回成功才允许复位 CPLL；系数、reset、override 和分频每阶段检查读回，失败时不切回尚未验证的 CPLL。`transition_error` 通过 sysfs 和 RPC 向前端传递，因此即使失败后 FPLL 临时频率恰好等于请求值，也不能显示“已生效”。原厂 OPP 电压字段全部相同，本实现只调频，不添加没有板级依据的调压控制。

原厂频率表遍历缺少边界，`set_rate` 成功返回频率值，部分下级操作结果未检查。原生实现使用有限表查找和明确错误。当前 Linux 6.18.55 的 `drivers/clk/clk.c:clk_change_rate` 不传递 provider 的 `set_rate` 返回值，因此另注册本平台 OPP 时钟回调，以硬件回读核实频率；不依赖返回成功或缓存目标值判定切换完成。

## 无线功率路径

使用原厂无线模块 `rtk_wifi6.ko`（SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`）的底层功率表语义和本机 RFE54/FEM 参数。其 `cfg80211_rtw_set_txpower@1f580` 是空实现，`cfg80211_rtw_get_txpower@1f520` 固定返回 12；不沿用这两个接口的行为。

PHY 和接口级功率请求统一由 mac80211 重新计算完整的全局/接口/国家域上限，再交原生 `.config`。不再直接用旧 `hw->conf.power_level` 与新接口上限取最小值，避免从低功率调高时被旧值卡住。`213-mac80211-txpower-errors.patch` 让接口级请求也执行这一重算，把驱动错误传回 netlink，并恢复失败前的软件功率值以允许同目标重试；不改变其他 AI 的数据通路。频道通知与功率通知同时出现但物理频道没有变化，以及显式重复设置同一上限，也会应用功率。无线关闭时，配置仍可接受为待启动请求，不冒充正在发射。

`rtl_ax_power_apply` 用 Linux 请求上限裁剪本机 ByRate、信道 limit 和 RU 表，写入后逐寄存器回读，失败时清除已生效标记并返回错误。`.get_txpower` 每次重新核对全部 43 个表寄存器、模式偏移及 limit/RU 使能位；不一致立即清除已生效标记并返回错误。诊断 sysfs 同样核对当前寄存器，不把旧快照的 applied 标记当作实时结果。它报告的是 Linux 配置且已编程的功率上限；该数字不是每个速率、每条天线或每个报文的实测射频输出。国家域和原厂表可能进一步降低功率。

Linux `nl80211_send_iface` 在 getter 失败时省略功率属性，仍会成功返回其他接口信息。原 libiwinfo 在属性缺失时把预置 0 当成有效功率。`130-iwinfo-txpower-unknown.patch` 改用 `INT_MIN` 标记未获得属性，缺失即失败，真实 0 dBm 仍有效；rpcd 不再输出伪造的零功率，LuCI 展示未知。补丁在 `sync-overlay.sh` 安装到 iwinfo 包补丁目录，未直接修改共享构建产物。

本机原厂 `mac_write_pwr_limit_en@21e954` 在 SCC MAC0 操作 `D208[21:20]` 与 `D21C[18]`；已执行实际模块机器码核对使能/禁用两分支及保留其他位。原厂启动 `rtw_hal_start` 先将该开关设为 0；本实现为了让已写入的信道和 RU 限值真正参与 OpenWrt 功率限制，在完整写表后显式使能并检查读回。这是明确的策略调整，不声称原厂默认已打开。

本机原厂时钟树中，CPLL 除 CPU/trace 还可经 C2F 向 PE 供时钟；PE mux 选择 FPLL 时，CPU 调频不重配 PE/PE AXI。Core、CCI、LSAXI 与外设时钟来自 EPLL，PCIe gate 和 PON/SerDes 的控制寄存器不在本驱动写集合中。这支持时钟域独立的源码结论，不代表改变 CPU 频率不会影响 Linux 慢路、协议处理或软件吞吐。

## 检查边界

本次按要求不编译、不提交、不刷写，不创建 worktree，不改动其他 AI 的 PE 数据通路。检查包括本机原厂频率参数比对、Linux 接口和调用链核对、补丁可应用性、JS/Shell/JSON 检查，以及生产 RPC 和视图的定点解释执行。另复核 CPU 驱动全部 MMIO 写地址仅含 CPLLDIV/CPLL0/CPLLMUX，以及全部八档对非 CPU 分频字段的保留。还执行本机原厂温度 getter 与功率 limit enable 的 ARM64 机器码，I/O 由显式模型提供；核对零值、负温度及控制位保留。RPC 拒绝空白和非法数值，CPU 页面同时检查 userspace 策略、硬件频率与 transition_error。没有将这些检查写成硬件温度精度、射频输出、PON 不掉线或 1200 MHz 稳定性的实测结论。


## 2026-10-18 实机问题与修正

`0.8.0-beta` 实机的 `luci.tyfza1-system.status` 与 `/sys/class/thermal/thermal_zone0/temp` 均读到 `-50023`，因此并非 LuCI 加负号或单位换算错误。历史原机启动日志 `logs/network-restore-uart.txt` 明确记录 `RTK_SOC_CHIP_ID = 0x6706`；原机串口历史温度为 42～51°C。执行上述原厂 FIT 中的实际 `cortina_thermal_probe` 机器码，分别给 `RTK_SOC_CHIP_ID` 注入 `0x6706`、`0x6900` 和另一型号，确认前两者选择 `0x2102011e/1f`，另一型号才选择 `0x0101010c/1d`。仅硬件 I/O、内存分配、延时和 thermal 注册由模型提供，没有用模型值宣称修正后的实测温度。

同时实机 `/www/luci-static/resources/view/status/include/10_system.js` 和本地 LuCI feed 均仍为上游脚本，未包含平台/温度改动。原补丁只在 `port/scripts/build.sh` 的完整构建入口应用，直接同步 overlay 后使用 make 的本地构建会遗漏。现在统一由 `port/scripts/apply-luci-patches.sh` 应用，在 feed 更新后及已有 feed 的 overlay 同步阶段调用，并已修正当前本地 feed。重复调用会先检查补丁是否已应用，保留其他改动；失败直接退出。相关测试验证两次准备后平台显示、温度 RPC 和功率上限标签均已进入 feed 且没有重复添加。

本轮未编译或刷写内核，当前运行固件的温度仍需在更换修正内核后验证；不把负读数取绝对值、不添加经验偏移，也不以屏蔽读数代替修正初始化。源码位于本地 main 的后续修复提交；无需更改 PON、PE、Wi-Fi 数据通路。
