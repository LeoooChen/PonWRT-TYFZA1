# GPON 超时清理的本机原厂证据

本文保留 `046836e` 阶段的缺口与原厂回放记录；后续实现见
[原生 GPON 超时恢复](pon-gpon-native-recovery.md)。

当前原生 GPON IRQ 在离开 O5/O6 时推进线路代次、撤销业务图，但没有对应
原厂 `aal_gpon_datapath_reset@0xf9ab0` 的软件队列清理。本记录确认这项
实现缺口，并固定其真实调用链；尚未把它标记为已修复。

`tools/trace_stock_gpon_queue_recovery.py` 执行本机 `ca-ne.ko` 的 datapath
reset、逐 T-CONT flush 及其 PUC/GTC setter/getter 实际指令。它不使用
其他 SDK 的恢复函数代替本机实现。

原厂实际顺序为：

1. 临时开启 PUC invalid-queue drop，设置强制 drop 选择为VOQ6；保存256个
   VOQ valid，关闭有效队列。本机编译初值是32个 T-CONT、每个8个队列。
2. 每个 T-CONT 暂停对应 Alloc-ID 的 OMCI enable，读取8个队列的字节与
   完整包计数。空队列和完整包队列均不注入探测帧。
3. 若有队列的字节数非0、完整包数为0，选取其中一个，临时启用该 VOQ，
   经 NI 向相同 T-CONT/COS 注入64字节探测帧，再读一次计数。
4. 逐队列保存 PVT 映射和两位缓冲限制选择，临时关闭映射有效位、选择限制A；
   每个 T-CONT 首次处理时触发 GPON 专用 T-CONT flush。对有完整包的队列
   请求 PUC flush，随后还原映射、限制选择及 Alloc-ID 的 OMCI enable。
5. 通过 GTC `0x6114` bit4 脉冲复位数据通路，归还先前保存的 VOQ valid
   和 invalid-drop 设置。没有采用 XGTC `0x4004` 的 T-CONT flush 或 PUC
   全部表的 reset/restore 流程。

| 操作 | 本机原厂地址/请求 |
| --- | --- |
| PVT | `0x8000`，数据从`0x8014`向下 |
| 队列计数 | `0x802c`，数据`0x8034/0x8030` |
| 临时 invalid-drop | `0x808c` bit30 |
| VOQ valid | `0x81bc`至`0x81a0` |
| 强制 drop 选择 | `0x80e0`低8位及bit31 |
| 缓冲限制选择 | `0x8090 + tcont/2*4`，每队列两位 |
| GPON T-CONT flush | `0x61c4` bit31及bits28:24，250微秒后清bit31 |
| PUC 队列 flush | `0x80dc`，VOQ低8位、T-CONT bits12:8、bit16、bit31 |

回放的 T-CONT1/COS3 探测帧 Header A 为 `0x2020230b00202000`，长度64。
前16字节为 `00010203040500112233445581000140`。原厂只初始化这16字节，
原生实现应初始化全部64字节，避免携带分配器留下的内容。

空队列、完整包积压、未完成包积压三条回放均核对了 PVT、Alloc-ID、VOQ
valid、限制选择和配置归还。另有一次 flush 请求保持 busy 的回放：原厂
打印超时后仍返回成功、归还 valid，模型中的字节和完整包仍在队列中。
原生恢复必须报告此错误并维持清理状态，不能照搬这个成功返回路径。

边界模型提供间接表内容/完成、队列计数变化、NI 探测帧提交、延时、锁和
日志；PVT 临时修改触发的全局缓冲策略重算也由模型代替。它不是硅片 FIFO
时延验证，未证明真实 GPON OLT 下的重连。本轮没有部署或更改实机配置。
