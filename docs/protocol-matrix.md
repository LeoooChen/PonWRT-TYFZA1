# TYFZA1 手动协议适配矩阵

用户要求所有GPON族/EPON族协议完整适配，当前只实现手动协议选择。以下为当前源码阶段和验证证据，完整适配目标保持不变。

| UCI模式 | 协议 | SerDes模式值/极性页 | 当前实现 | 光口验收 |
| --- | --- | --- | --- | --- |
| epon | 1G EPON | 040c / 62 | SerDes、MAC/PCS/FEC、PUC/PDC、MPCP、OAM守护进程、业务netdev/DQ | 待注册/认证/业务测试 |
| epon-d10g | 非对称10G EPON | 044c / 60 | 同上；当前用户线路选择 | 单LLID注册、LOID、FEC、DHCP及公网/软件NAT通过；见xepon-live-network.md |
| epon-bi10g | 对称10G EPON | 046c / 60 | 同上 | 待注册/认证/业务测试 |
| gpon | GPON | 0408 / 61 | 原厂参数、家族选择、SerDes/LOS复位序列接入；MAC/PLOAM/OMCI继续移植 | 未验证 |
| xgpon | XG-PON | 0448 / 45 | 同上 | 未验证 |
| xgspon | XGS-PON | 0428 / 45 | 同上，GPON global上行10G/新模式位 | 未验证 |

scripts/test_pon_modes.py依据固定GPL aal_psds.c检查全部六项模式值、极性页及参数文件存在。BOSA共用原机通用校准；如果后续发现某协议有独立校准文件，使用该协议文件并保留通用文件来源记录。

GPON族需要独立MAC/ANI、PLOAM激活、ONU-ID/测距/定时器、密钥、OMCI通道、GEM/Alloc/T-CONT及业务规则。当前GPON族的MAC启动返回EOPNOTSUPP，状态显示prepared=0/error=-95；SerDes配置不能作为GPON完整注册证据。对应后续任务与参考教训在pon-reference-lessons.md。

EPON族已接入原生OAM发现、CTC扩展协商、LOID认证、版本查询和每LLID守护进程/状态，实际ARM64/musl进程的隔离网络测试通过。每LLID业务netdev、上行DQ缓冲/credit、PUC背压、PDC分流及DHCP默认WAN已进入代码和主线构建；生产函数的寄存器/网络栈模型验证通过。OLT业务规则、CTC Set后台及其VLAN配置继续移植。epon-d10g在正确上联光口已通过单LLID注册、LOID认证、OLT FEC配置、无标签DHCP与实际公网收发；其他线路模式及多LLID硬件继续验证，见xepon-live-network.md。
