# TYFZA1 RF 周期温度与 LCK

冻结遗漏清单中的 F03/F06 开始落实：驱动现在每两秒读取两条 RF 路径的实时温度，首次执行或任一路径相对上次成功 LCK 的温差达到 8 时重新校准。F03 的其他 RF 周期算法和其余冻结缺项仍未完成；本提交不增加审计项目。

## 原厂行为与重写

依据本机原厂模块 SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`：

- `halrf_get_thermal_C@30e790` / `XB@33d7a4`：RF42 bit19 依次写 1/0/1，等待 200us，再取 RF42[6:1]。
- `halrf_lck_tracking@2f5a1c` 及 C/XB 下级：默认 RF 能力 bit2 启用；参考温度均为零或两路径最大温差至少为 8 时校准。软件能力与芯片能力取交集，原厂 `rtw_rfk_ability` / `rtw_halrf_ability` 编译初值均为 `0xffffffff`。
- SCC LCK 保存 RF0 两路径的模式和路径 A 的 RF18；设两路径模式为 3、RF5 为 0，再通过路径 A 的 RFd3 bit8 和 RF18 触发 LCK，最多观察 RFb7 bit8 共 1000 次，每次 busy 等待 1us。
- 结束后恢复两路径 RF0。C 按原厂写两路径 RF5=1；XB 恢复之前保存的两个 RF5 值。不能把两颗的恢复步骤统一成同一种行为。

新 `rf-runtime.h` 使用现有原生 RF 访问函数，未嵌入原厂结构体或调用私有 SDK。当前两颗无线均采用 SCC；本提交不扩展到尚未提供的 DBCC 模式。

`dm_work` 使用 wiphy delayed work，每两秒执行；`.start` 入队，`.stop` 同步取消。wiphy 互斥负责无线生命周期，`command_mutex` 排除换信道、sysfs RFK 与周期校准交叠。没有在自旋锁中等待校准。Linux 6.18.55 的 `wiphy_delayed_work_cancel` 在 wiphy 锁下删除 timer 并移除待运行 work，取消后再停止 RX/TX 和硬件。

## 原厂问题的调整

原厂 `halrf_set_ch_lck_*` 在 1000 次 busy 后返回失败，但 SCC 的上级没有消费这个返回值，仍然更新“成功校准时温度”。这会把未完成的校准当成成功基线。

新实现保留原厂观察次数和 RF 恢复顺序，超时返回 `-ETIMEDOUT`，记录超时并保留上次成功基线。RF 写入失败时也执行运行模式/控制恢复，不以返回成功或更新温度来掩盖错误。后续正常周期仍依据上次成功基线决定是否需要 LCK，没有增加特殊重试循环或更改温差门限。

PCI 设备属性 `radio_rf_runtime` 提供周期数、实时温度、LCK 基线、完成次数、超时次数和最后错误；原有 `radio_rf_start` 继续表示启动阶段状态。

## 定点验证

[check_radio_rf_runtime.py](../tools/check_radio_rf_runtime.py) 同时执行原厂 ARM64 函数与编译后的生产 `rf-runtime.h`。两颗各检查首次、温差 7、路径 A 升温 8、路径 B 降温 8、1000 次 busy 超时。正常分支与原厂的 RF 读写操作、延时、寄存器最终状态及成功基线一致；超时分支 RF 操作仍一致，但原厂错误地推进基线，新实现返回失败并保留旧值。另验证一次 RF18 写入失败后的 RFd3、RF0、RF5 恢复。

这里 RF 访问原语和时间由模型提供；读操作以寄存器地址归一化比较，原厂返回掩码字段，原生访问读取寄存器后在调用者提取同一字段。没有宣称执行了真实 RF 总线、校准或两秒调度时序。完整结果见 [rf-lck-check.json](../artifacts/wifi-bb-runtime-20261016/rf-lck-check.json)，原厂 REA 记录见 [rea-lck-index.json](../artifacts/wifi-bb-runtime-20261016/rea-lck-index.json)。

仅编译当前无线外置模块，通过；产物 `rtl_ax_ap_rf_runtime.ko` 的散列与源码一致性见 [wifi-rf-runtime.json](../manifests/wifi-rf-runtime.json)。保留此前构建已存在的复合模块 `MODULE_DESCRIPTION` 提示。本次没有实机、全量测试、全量固件构建或推送，PON/以太网未提交改动原样保留。
