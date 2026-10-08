# 2026-10-08 接收、OAM与控制并发复核

本轮处理连续报告的七项问题，基线为3f3fda1及当时未提交修改。

| 问题 | 判定与修改 |
| --- | --- |
| QM 64项栈数组与外部budget | c6c5079旧函数可用65个描述符触发ASan栈越界。3f3fda1已删除该数组，改用持久归还队列。本轮直接运行当前rtl_qm_rx_poll，覆盖65/129/256/512包、budget64/65/256/512/1024、满环及归还阻塞；无越界，所有缓冲最终归还一次。 |
| XRAM或DMA-TX暴露qm_pool | 确认。sysfs属性组按qm_rx决定可见性；这些模式不暴露qm_pool、qm_trace、qm_wait、qm_es、qm_buffer。QM模式保持正常访问。旧读取路径在寄存器模型中重现空指针访问。 |
| CTC Set丢对象实例 | 确认。0x36/0x37对象选择器分别完整回抄5/8字节，保留1/4字节实例及后续变量归属；未实现变量仍返回0x87。测试涵盖两个对象、两种宽度、错误宽度和截断。 |
| EPON模式判断在锁外 | 确认。MAC初始化与traffic开关都在状态锁内判断模式，ONU mode从当前锁内模式计算。另一线程在加锁前改为任一GPON族模式时，不执行EPON寄存器访问。 |
| 模式切换停止/切换之间可重启MPCP | 确认。新增control_lock覆盖整个控制事务；公开SerDes、MAC、MPCP、PUC/PDC函数统一获取它，内部_locked步骤只要求调用方持有它。BOSA、ONU MAC、LLID数量/表及remove也遵循该规则。MPCP在GPON族明确返回EOPNOTSUPP。 |
| 无效/忽略OAM报文延后超时 | 确认。解析返回负errno表示格式错误、0表示忽略、1表示已处理无应答、60..1514表示已验证的应答长度。只有已处理报文刷新last_received；截断组织头返回EMSGSIZE，未知code/OUI/opcode及无变量Get/Set不续命。 |
| ONU MAC通过RCU写netdev地址树 | 确认。更新使用control_lock、RTNL和每个netdev的地址锁；按原厂规则派生LLID地址并发出NETDEV_CHANGEADDR。OAM接口移除独立ndo_set_mac_address，地址由ONU配置统一管理。 |

控制锁始终先于状态锁。涉及网络设备时，RTNL先于状态锁/设备锁；LLID扩缩不持有状态锁等待RTNL。IRQ、MPCP work、SerDes work只使用状态锁，永不等待control_lock。同步取消与IRQ同步在控制锁持有、状态锁释放时执行；测试用实际状态工作线程完成取消，覆盖该锁顺序。

scripts/test_native_rx_modes.py从生产文件原样提取函数，以ASan/UBSan、寄存器及资源所有权模型运行接收与诊断路径。scripts/test_pon_transactions.py运行实际SerDes/MPCP控制函数和ONU MAC入口，在停止与切换的确切间隙安排另一线程调用MPCP启动，验证三个GPON族最终均无MPCP工作或中断使能。两份测试支持--revision以复现旧版本；日志位于logs/native-*、logs/pon-*-baseline.txt和对应JSON。

scripts/test_oam_control.c验证报文分类与对象回抄；scripts/test_oam_daemon.py在隔离veth中运行实际OpenWrt ARM64/musl进程。新增测试在认证成功后连续7秒发送截断、未知code、未知OUI和未知opcode报文，认证仍在5秒期限后撤销，历史成功次数保留。该套集成测试共13项。

本轮设备离线；寄存器模型、受控线程和隔离网络均不访问光猫。完整主线镜像由独立systemd构建服务生成，构建及载荷记录见manifests/pon-audit-build.json和pon-control-review-tests.json。后续最终FIT绑定、BOSA写入口及NAPI关闭路径修复见pon-artifact-tool-review.md。
