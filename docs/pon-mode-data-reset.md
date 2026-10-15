# 模式交接前复位 PUC/PDC

原生 SerDes模式切换停止旧制式、撤销转发并清除软件 configured 标志后，
原先直接改变制式并重配 SerDes。PON control 的PUC/PDC位仍可能为1。
EPON与GPON后续 MAC准备也可能只再次写入`0x30f/0x30e`，没有产生数据模块
的复位边沿，旧表和队列硬件状态因而没有明确的跨制式清除步骤。XGTC MAC
准备已有单独的数据模块复位，但不能据此认为其他制式也执行了它。

本机原厂所有六种 `aal_pon_deinit` 路径均通过实际制式 deinit把
GLOBAL_PON_CNTL写0，并清零当前制式的MAC控制寄存器，末尾等待100毫秒。
原生模式交接现在在转发清理成功、周期工作取消之后，持有control/state锁
清除PON control bits8/9并等待同样的100毫秒，再改变模式。SerDes与MAC
其他复位继续由原生既有流程负责；PUC/PDC一直保持复位，直到新制式的
MAC准备阶段释放它们。FE或NI清理失败时尚未执行这一步，保留原模式和错误。

`trace_stock_pon_data_reset.py` 执行本机原厂六种模式的分派、制式deinit
以及PSDS deinit实际指令，确认PON及所选MAC的复位写入、未选MAC保留及
100毫秒延时。IRQ/FSM/MPCP/加密退出主体和OS调用由模型提供。

生产控制事务检查先复现旧PUC/PDC状态保留，修复后覆盖EPON→三种ITU、
GPON→EPON，以及FE/NI清理失败。模型按reset边沿清除预置的旧状态，
并继续检查并发MPCP启动不能穿过模式交接。延迟同步的CSR交接回归通过，
ASan/UBSan和受影响ARM64对象编译通过。没有全量测试或全量固件构建。

这次检查还确认NI DMA为LAN/PON等端口共享，`rtl_dma_tx_forget()`只撤销
netdev引用，不能据此认为描述符已经排空。因此本次没有重置共享DMA或
声称模式切换时所有已提交帧均已物理清空；这部分仍需进一步证据和实测。
本轮没有部署或改动实机配置，也没有实际ITU OLT验证。
