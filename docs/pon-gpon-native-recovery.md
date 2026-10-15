# 原生 GPON 超时恢复

本实现补上 `046836e` 记录的原生 GPON 软件 drain 缺口。依据本机原厂
`aal_gpon_datapath_reset` 和 `aal_puc_voq_flush_by_idx`，GPON 有独立的
恢复工作和队列状态，不复用 XGTC 的 SerDes/MAC/PUC 重建方式。

退出 O5/O6 后，IRQ 推进线路代次、撤销业务图，并暂时屏蔽 GPON 顶层
中断。已知会话中的分配事件若改变 ONU-ID，或同时带有状态事件，即使采样
仍为 O5，也先结束旧会话；初次分配、单独重复分配及正常 O6 返回保持其语义。
本机原厂 `__intr_handler@0xfb990` 对 O5 中合并分配/状态事件同样清理旧链路。
新绑定等到队列清理后才建立；清理期间的后续 GPON 事件保留在硬件。

恢复先保存并冻结 VOQ valid、invalid-drop、强制 drop 选择及 GTC flush
选择，再等待 NI 业务清理成功。NI 清理失败也保留原快照和暂停的准入。
逐 T-CONT 保存 PVT 与 Alloc-ID 的 OMCI enable，读取8个队列的字节/
完整包计数。仅对有字节而无完整包的队列注入原厂 Header A 和16字节头的
64字节探测帧，尾部明确清零。NI 忙时归还 skb、关闭临时 valid 后重试；
已提交的探测帧等待计数变化，不重复提交。

逐队列临时关闭 PVT 映射有效位、选择缓冲限制A；每个 T-CONT 用 GTC
`0x61c4` 的250微秒脉冲清理，完整包使用 PUC `0x80dc` flush。每个队列
必须确认 flush 请求完成且字节/包计数均为0，才归还其 PVT 和限制选择。
末尾按原厂脉冲复位 GTC 数据通路，归还原配置、Alloc-ID OMCI enable 和
VOQ valid。恢复不改 BOSA、SerDes、MAC 身份或用户 FE 开关。
临时映射操作保留既有全局缓冲策略；原厂回放中的策略重算函数属于模型
边界，这部分与真实 FIFO 完成时序仍需实测。

原厂在 flush 超时后可能打印信息但仍返回成功。本实现返回真实超时或
`-EIO`，保存原配置和当前阶段，保持业务关闭、GPON 中断暂停，并安排重试。
重试等待已发出的请求，不覆盖 busy 命令、不重新采集已经暂停的状态，也不
重复提交同一 flush。队列确认清空前不能重新开放全部 VOQ valid。

队列清理后重新读取实际 ONU-ID、OMCI port 和 O5 状态，恢复 T-CONT0 与
8个 OMCC upstream GEM 绑定，再决定 carrier。这不依赖再来一次分配中断；
若完成时尚未到 O5，之后的 O5/OMCC 事件会补齐绑定。OMCC 写入超时保留
独立的最后阶段和 busy 窗口，重试绑定时不会再次执行 drain 或复位队列。

PUC/PDC 用户入口及 OMCI 业务提交不能覆盖尚在恢复的硬件表；这条所有权
规则同时覆盖已有 XGTC 恢复。XGTC 工作自身仍可调用其内部 PUC 初始化。
GPON 恢复期间重新激活返回 `-EBUSY`；显式停止取消工作（含排队未开始的
恢复），并按原厂 deinit 的复位原则结束未完成的 PUC/GTC 事务，使
MAC/PUC/PDC 配置失效，要求重新
初始化。`activation` 的 `recovering` 和实际错误值反映恢复状态。

定向检查执行生产工作函数、drain helper、停止/激活函数和 PUC/PDC 用户
入口，边界模型提供 MMIO、队列完成、NI 提交、锁和延时。覆盖空队列、
完整包、未完成包、NI 忙/清理失败、flush 超时、flush 完成但仍有积压、
当前 O5 身份恢复、OMCC 超时及显式停止，检查配置归还和 FE 开关保留；
ASan/UBSan 通过。GPON O6、合并分配/状态事件、
业务分片提交与协议隔离检查通过，已有 XGTC 恢复检查通过。

12个 PON 对象及 NI 对象已做 ARM64 编译和部分链接，没有构建全量固件
或运行全量测试。本轮没有部署或改动实机配置；EPON 实机继续使用之前的
`rtl9617c-pon-fe-family-live` 模块，注册和外网连通正常。真实 GPON 光路的
FIFO 计数/完成时序、未完成包注入效果、中断重放及业务恢复仍需 GPON OLT
验证。指令回放和边界模型不能替代这些物理结果。
