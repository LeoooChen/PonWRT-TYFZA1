# TYFZA1 原厂运行时 DIG / EDCCA 对齐

## 原厂依据与确认的差异

唯一二进制依据仍为本机 `rtk_wifi6.ko`，SHA256
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。
本轮直接执行其中的 ARM64 函数，并以生产 helper 对照寄存器和状态。

1. **初始化的 PD 计算用了相反的比较。**
   `halbb_dyn_pd_th_ofdm@2d5ebc` 和 `halbb_dyn_pd_th_cck@2d5d60`
   实际计算 `min(IGI, RSSI+5)`，再减去至多16的偏移。原移植用了 `max`。
   初始 RSSI/IGI 均为28，原厂请求为98；20 MHz 的 OFDM 下限应关闭，CCK
   下限编码应为 −103。旧实现请求93，开启 OFDM 下限，CCK编码为 −98。
   此处更正此前初值推导；先前的带宽测试只校验给定请求的编码，没有执行完整初始化。
2. **CCK 换带宽时没有补偿。** 原厂两芯片均加 `5+3*bandwidth_index`，
   本轮与 OFDM 一起更新。OFDM 仍按原厂向上取偶数、超过102关闭下限。
3. **连接后的 DIG 循环缺失。** `halbb_dig@2d6eb4` 在关联变化时重设门限与
   阻尼历史，后续读取 FAHM 的误检占比，按 −2/0/+2/+4/+6 的步长调整 IGI。
   关联/未关联阈值分别为4/8/12/16和196/352/440/528。旧驱动一直保留启动值。
4. **默认 EDCCA / collision 周期写回缺失。**
   `halbb_edcca_thre_calc@2a3a60` 的 mode0 使用关联终端最小 RSSI，
   编码高门限为 `max(66,RSSI/2+15)`；未关联为249，迟滞8。
   `halbb_set_collision_th@2a38c8` 使用 `clamp(60-RSSI/2,0,31)`。
   原厂 common-state 在失联时将最小 RSSI 清零。旧驱动只初始化了这些功能。

这里的 RSSI 是原厂 U8.1 正值编码；IGI 为算法状态，不能直接解释为天线增益。
EDCCA 数值也是寄存器编码，不能直接当作实测 dBm。

## 实现边界

在原有两秒 `dm_work` 内接入两颗芯片的 origin DIG 和默认 mode0 EDCCA；
没有新增定时器。PPDU 的已关联终端 RSSI 按
`halbb_rx_pkt_su_store_in_sta_info@296950` 的累加器平滑，由两秒周期的包数选择
下一周期的平滑因子；控制输入取所有已关联终端的最小值。
沿用现有 RA 每两秒开启20 ms的 PHY 报告窗口；未改成持续上报，也未移植
其他 common-state 的 MU、beacon 等统计。

FAHM 使用原厂 DIG 的 priority2/application2、1885 ms实际测量窗口、32µs单位、
numerator1/denominator4。原厂 background FAHM 为priority1；当前原生驱动没有
其他 FAHM 消费者，DIG 在现有互斥任务内独占此资源。原来的 C NHM/DFS 仍消费
独立的 NHM 控制/报告寄存器。换信道和重新启动 AP 后丢弃旧 FAHM 报告。

保留原厂的三种振荡识别模式、IGI 下限保持、RSSI 变化超过3 dB或60秒后的解除。
默认 EDCCA 的高门限、迟滞与全部14个 collision 字段都写到真实硬件；未改发射功率。
EDCCA mode1/2/3 所需的适应性/监管策略不在本次实现内，仍保留原有初始化行为。

`command_mutex` 排除换信道、RF 校准和 sysfs RF 操作；现有 wiphy work 负责与
start/stop/STA 的串行化。RSSI 累加器与 PPDU 回调共用 `command_lock`，只在
短暂快照时持锁，寄存器操作在解锁后执行。

## 原厂执行对照与构建

- `test_radio_dig_runtime.py`：4,200个原厂 watchdog 周期，双芯片初始化、
  FAHM完整直方图/未完成报告、连接/失联、带宽20/40/80/160、阻尼状态和实际PD写回；
  另有3,632个原厂站点RSSI样本与包数平滑因子切换，覆盖阻尼实际生效路径。
- `test_radio_edcca_runtime.py`：3,072个双芯片原厂向量，所有RSSI字节值、
  关联/失联及随机寄存器初值，确认不覆盖无关位。
- 带宽原厂1,332个向量、160 MHz检查、558个功率策略用例及AP停止/NI撤销测试通过。
- ARM64模块编译通过；仅有内核既有的 MODULE_DESCRIPTION 提示。

## RAM 验证

原始记录位于 Mac `现场记录/20261025-dig-runtime/`。结构化结果、模块散列和
日志散列见 `manifests/wifi-dig-runtime-20261025.json`。

比较基线为 `main@41b3bae` 加已验证的 `6706cf6` 带宽修复；完整修复在同一基线
上增加 DIG / EDCCA。本轮同时使用相同的 RAM mac80211 模块，以提供 main 的
补丁217新增接口；原实机 mac80211 没有该符号。没有刷写 Flash 或修改无线配置文件。

同一 boot_id `a316c6d1-55d5-4bf2-94f7-dbae8672f53a` 完成基线、DIG版、回切基线、
完整DIG/EDCCA版的RAM对比。有效主对比均先确认hostapd BSS已启用。

| 频段/模块 | 上行10Mbps实收 | 上行丢包 | 下行10Mbps实收 | 下行丢包 | ping |
| --- | --- | --- | --- | --- | --- |
| 2G基线 | 9.98Mbps | 0.152% | 10.00Mbps | 0 | 100/100 |
| 2G完整修复复测 | 10.00Mbps | 0 | 10.00Mbps | 0.040% | 100/100 |
| 5G基线 | 9.52Mbps | 4.782% | 9.99Mbps | 0.136% | 100/100 |
| 5G完整修复 | 9.63Mbps | 3.696% | 10.00Mbps | 0 | 100/100 |

每方向12秒，包长1200；位置、信道、带宽和发射功率固定。这些短测验证正常收发，
不据其轮次波动宣称吞吐提升。2G有效RSSI中位数为−22→−21，5G为−26→−25 dBm；
不把1 dB差异作为增益，接收控制修复本身不提高AP发射功率。

旧模块始终为IGI28、PD请求93、periodic_DIG=pending。新模块实测FAHM报告持续增加，
2G linked FA阈值为4/8/12/16，IGI随误检占比改变，OFDM/CCK下限和PAGC跟随写入硬件。
5G同样按RSSI/FAHM更新。独立只读PCI resource2映射核对了PD、FAHM、EDCCA及所有
collision字段；例如2G最终EDCCA寄存器为 `6f386f6f`，collision各字段为0，
未关联5G为 `f938f9f9`、collision31，其他寄存器位保留。

2G AP停止后，两次间隔3秒读取的DIG更新计数均为22；重启后增加至32，最终86。
两颗芯片RF watchdog result=0、LCK_timeouts=0。最终仍加载生产源码构建的
`rtl_ax_ap_dig_complete`，SHA256
`22e0ce8b0578a05c3fcad6101ca768546b23adf60b63e8aeafdb869c567ebe26`。

热插拔期间部分ap2因hostapd重复注册未启用，未作为射频失败或改善统计。另一次2G完整
修复测试已完成上下行，但测试后长SSH快照重置；串口仍能读取站点和硬件状态。后续2G
复测完整通过收发、ping及测试后SSH读取。保留全部原始材料，没有删除失败记录。

未使用可控衰减器或干扰源，未进行远距离覆盖测量，本轮不量化覆盖距离或额外dB增益。

