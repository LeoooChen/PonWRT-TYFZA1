# pon-fw-r1 经验对照

参考项目：/home/leo/pon-fw-r1，控制仓库提交19b698f0f8a994cd8839f07d9e733b33b8ecefc4。按用户要求读取，未修改该项目。其XG-040G-MD/TF采用Airoha平台；本项目保留RTL9617C原生驱动实现，借鉴协议处理、状态诊断和OpenWrt集成经验。

| 参考资料或代码 | 对本项目的具体要求 |
| --- | --- |
| docs/SHANGHAI-XGPON-DIAGNOSTICS-R1.5.3-20260923.md | XG-PON广播ONU-ID为03ff；03fe按当前协议处理，不能把发给其他ONU的控制命令用于本机。重新激活必须清除当前认证、密钥和业务状态。记录命令目标、ONU-ID、原因和状态迁移。 |
| docs/R144-NETIZEN-XGPON-OLT-DEACTIVATE-20260910.md | 区分LOS、PCS同步、测距/注册、管理交换、订户认证和业务配置。进入O5或收到LOID Set成功应答均不能直接代表认证通过。 |
| docs/FIX-R1.5.4-LINUX-VLAN-MULTI-VID.md | VLAN分类逐业务VID绑定；多VID不能只读取第一项。Linux VLAN和OLT标签规则只在相同业务绑定处去掉重复动作，保留真实标签改写。 |
| docs/FIX-20260922-NEUTRAL-FIRST-BOOT.md | 模式切换顺序为停止发送及管理处理→重置本次会话→加载目标协议→恢复管理和业务。用户当前要求只做手动切换，自动识别暂不实现。 |
| xg140g-pon/src/userspace/epon_oamd/epon_oamd.c | 802.3ah Local/Remote Information互抄和稳定判定；CTC重新认证撤销旧认证结果；24字节LOID及12字节口令定长编码；原生NI的OAM Ethernet封装由RTL9617C驱动处理。 |
| openwrt-main/package/luci-app-xg140g-pon | LuCI配置写入UCI，状态来自真实内核和管理进程；当前值与历史诊断分开。身份、版本字段需要由对应OAM/OMCI编码器消费。 |

本轮已接入原生OAM控制接口、共享NI生命周期、独立MPCP状态、BOSA状态和LuCI实时读取；手动模式覆盖六个原厂SerDes参数入口。GPON/XG-PON/XGS-PON的MAC、PLOAM、OMCI、GEM/Alloc及业务规则继续移植。

新LuCI提供LOID、LOID口令、SN、Registration ID、PLOAM口令、厂商ID、设备型号、硬件版本、软件版本和WAN协议/VLAN配置。这些值保存到/etc/config/pon；ONU MAC和LLID数量已有内核消费者。新增订户/版本/WAN字段的完整协议消费者仍在开发，字段保存不代表OLT已接受它们。

2026-10-08业务netdev/DQ接入后的下一项是CTC Set后台。notes/verify-snap/src/userspace/epon_oamd/epon_oamd.c:1030..1165给出branch37对象与各setter的归属：C7/0021为对象相关VLAN模式，C7/0041、0042为对象相关组播处理，C7/0017、0045等还需要实际端口消费者。参考项目在HGU路径对部分字段依原厂语义ACK，不调用SFU VLAN API；本项目应先确认TYFZA1的同项处理，再把必要动作落到Linux VLAN、桥接或端口设置。特别是单个对象后的多个变量、多个对象、多VID以及恢复会话时撤销规则，不能仅靠返回ACK表示已经配置成功。
