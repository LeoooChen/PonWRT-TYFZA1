# ITU O6 宽限期保留业务配置

此前修复保留了 XGTC 的 OMCC 和密钥，但 GPON、XG-PON、XGS-PON 的 IRQ
仍把 OMCI carrier 关闭直接解释成业务会话结束。O5→O6 时因而调用
`rtl_pon_itu_paths_invalidate()`，撤销 NI 业务图、推进业务表代次。
虽然线路会话代次未变，返回 O5 后仍须等待 OMCI 守护进程重新下发业务，
再次执行 T-CONT/GEM/队列编程。

本机原厂 GPON `__intr_handler@0xfb990` 对 O5→O6、O6→O5 保持
`linkState.5=1` 和已建立的 drain 状态，不发出链路断开事件，也不调用
`aal_gpon_datapath_reset`。回放的 O6→O2 超时分支则清理这些状态并调用 reset。
原厂 XGTC 的 LODS 与 O6 重同步处理在默认 `reset_at_lods=0` 时同样
保留激活上下文，没有重置 MAC 的寄存器写入。

现在分别使用两类硬件状态编号：GPON O5/O6 为4/5，XGTC O5/O6 为3/4。
仅有 carrier 暂停时，完整 OMCC（XGTC 还要求会话密钥就绪）的业务配置
保留，业务 carrier 仍跟随当前可通信状态。返回 O5 后使用原业务图，
不必因 O6 再次下发。OMCC/密钥失效、GPON 激活出错或退出宽限期仍会
撤销业务图；XGTC 的去激活事件清理和超时队列恢复继续执行。

验证范围：

- `trace_stock_gpon_o6.py` 执行原厂 IRQ 状态处理，事件/日志和超时 reset
  函数体由模型提供；比较进入 O6、返回 O5、O6 超时三条路径。
- `trace_stock_xgpon_o6.py` 执行原厂默认 LODS/O6 重同步函数；模式读取
  和日志由模型提供。
- 生产 GPON/XGTC IRQ 的定向检查先复现 O6 错误撤销业务，再验证保留与
  返回 O5；缺失 OMCC/密钥、超时、O4 分配和合并去激活中断仍按其语义处理。
- 两个受影响 ARM64 对象编译通过。没有全量测试、全量固件构建或部署。

记录在 `manifests/pon-itu-o6-business.json`。本轮验证使用边界模型，
没有证明实际光路的重同步时延、FIFO 清空或真实 OLT 下的业务连续性。
