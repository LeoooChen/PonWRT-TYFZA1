# XGTC 失步后的队列恢复

原生驱动原先在 loss-sync 后重建 SerDes、MAC、PUC/PDC 和激活状态。除了
破坏 O6 宽限期，首次重建失败后的下一次尝试还会把已被清理关闭的 FE 状态
当成用户设置，最终丢失硬件转发。仅增加一个恢复用的 FE 开关不能修复这条
错误的恢复路径；该候选已保存在旧实验目录，没有纳入生产代码。

本次依据 TYFZA1 `ca-ne.ko` 的 `aal_xgpon_flush_tcont_puc@0x116290`，以及
它调用的配置保存/恢复、PUC backup/reset/restore 和 T-CONT flush 实现重写。
原厂默认 `aal_xgpon_mac_reset_at_lods=0`，不把它强行改为全量 PHY 重建。

原生恢复顺序如下：

1. 保存 NI `0x610c` 低16位并暂停 PON 入口，保存 MAC、SDS burst、laser
   和 TO1/TO2 设置；按原厂 LAO 条件暂停 burst，关闭 MAC 并等待500微秒。
2. 在 MAC 停止后读取 PUC 配置、32项 PVT、256项 CIR/PIR/背压、有效队列
   和缓冲限制。快照随设备保存，失败时保留当前阶段。
3. 等待之前的 flush 完成，再清空32个 T-CONT；将 PUC reset位拉低/拉高，
   各等待5毫秒，再调用当前模式的原生 PUC 初始化。
4. 按原厂可写字段恢复队列、整形和背压；调度 credit 从权重和当前 basesel
   重新生成。恢复 MAC/SDS 后，归还先前暂停的 NI 位。

PUC表的保留位以当前硬件值为准；不把任意状态窗口整块写回。原厂 limit
setter使用未初始化栈字的高位，本实现仅改其实际14位阈值并保留其他位。
NI入口恢复也保留原来禁用的端口，没有像原厂结尾那样无条件打开所有位。
这些是明确的原生所有权处理，不加入旧驱动 ABI 或猜测的寄存器兼容路径。

恢复仅由曾经处于 O5/O6 的线路退出宽限期触发，立即调度；启动阶段的失步
不清空队列。恢复过程不关闭 FE、不重新配置 SerDes/MAC 身份，也不覆盖用户在重试期间
修改的 FE 设置。恢复阶段分为等待快照、等待队列清空/初始化和等待写回；
超时报告真实错误，保留原始快照、暂停状态并安排下一次工作。手动停止会
取消恢复并释放其暂停的 NI 位。尚未开始工作而硬件已回到 O5/O6 时，取消
过时的工作。TO2 事件另按原厂条件触发 O1/O8 sub-state1 的时钟序列命令。

原厂实际指令回放确认了 NI8..23 暂停、MAC/SDS 保存/关闭、32个 T-CONT
flush、PUC backup/reset/restore调用顺序及恢复。PUC三个主体的回放边界由
模型代替，不能据此宣称其全部表内容或物理时延已经验证。scalar 和 bucket
可写 mask 来自执行原厂 getter/setter；时钟命令的状态条件来自执行原厂
函数及其真实状态 getter。记录在 `logs/stock-xgtc-queue-recovery.json` 和
`artifacts/pon-full-recovery-20261016/`。

定向验证执行生产恢复工作函数、新 NI gate 函数及 PUC helper，模型提供
MMIO、间接表完成和 PUC 初始化。两种 XG模式都完成32次 flush，模拟恢复
首条表命令超时后保留原快照，下一次完成恢复；保存失败后的手动停止释放
NI。可写表字段、保留位、SDS/MAC/定时器、准入 mask、FE关闭状态和时钟
命令条件均核对；O6、O4分配及合并中断的已有检查通过。

ARM64当前12个 PON对象及 NI对象编译、部分链接通过，新 NI接口在链接产物
中已解析。没有进行全量固件构建或全量测试。此实现需要内核同时包含新的
`rtl9617c_ni_pon_gate`，因此不能只向现有旧 NI内核加载 PON模块。本次没有
部署或重启设备；EPON继续运行已验证的 `rtl9617c-pon-fe-family-live` RAM
模块。真实 XG-PON/XGS-PON失步、FIFO清空时延和业务恢复仍需匹配的 OLT。
