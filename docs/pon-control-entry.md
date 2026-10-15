# PON 控制入口的协议与恢复归属

两个遗漏的入口可以绕过此前已分开的控制生命周期：

- `pon-oamN` 使用独立的 EPON 控制发送函数，但它原先只检查帧格式。接口
  carrier 关闭后、或旧 BUSY 包重试时制式已变成 ITU，仍可能提交 EPON
  OAM容器。关闭再打开这个接口也没有核对制式。
- XGTC 恢复尚未开始或间接表超时暂停时，重新写 activation=1 原先会清除
  恢复错误、启用 MAC 和 burst，并改写 OMCC 就绪状态。恢复工作随后仍会
  写回其保存的状态，两个控制事务因此冲突。

OAM控制接口现在只在 EPON 下打开；OAM发包同时要求 EPON 和当前 carrier。
不满足条件的 skb 计入 drop 并归还；NI BUSY 时仍由调用者持有 skb，若制式
在其重试前已切换，则重试不会再提交。业务接口仍可用于 ITU 的数据路径。
NI 的控制端口配置由 EPON OAM 和 ITU OMCI共用，未因名字含 OAM 而拆开。

XG-PON/XGS-PON在恢复标志有效时重新激活返回 `-EBUSY`，不改错误值、
硬件准入、MAC使能或快照。恢复完成后的激活和显式停止继续使用各自现有流程。
该检查在 control/state 锁内，与 GPON 已有恢复事务规则一致。

本机原厂 `dal_rt_rtl8277c_epon_oam_tx@0x22230` 的实际指令回放确认控制
Header A、COS7、LDPID15/LSPID17及16字节容器的发送约定。这个底层 helper
本身在六种 CA_PON_MODE 下都提交 NI，没有逐次的制式/carrier 检查。因此
新增入口限制是原生多制式 netdev 的生命周期修复，不声称原厂已有相同条件，
也不把原厂底层入口未校验的行为作为跨制式发送的依据。

定向检查执行生产 OAM open/TX与 XGTC恢复/激活函数。覆盖六种协议、carrier
关闭、BUSY后的协议切换、排队和部分恢复时重新激活、恢复完成后激活；
校验 skb归属、现有32个T-CONT恢复及NI/FE状态保留，ASan/UBSan通过。
两个受影响 ARM64对象编译通过。原厂回放中的分配器、memcpy、MPCP offset、
NI提交由模型提供；原生检查的NI/MMIO/延时与锁也是边界模型。

本轮没有部署、全量测试或全量固件构建。检查没有证明同时切换制式时已提交
DMA描述符的物理排空，也没有真实 ITU OLT 的恢复或流量验证。
