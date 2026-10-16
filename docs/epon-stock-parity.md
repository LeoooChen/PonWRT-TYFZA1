# TYFZA1 EPON 原厂功能对照与移植记录

2026-10-16。本次工作位于独立分支 `codex/epon-parity-20261016`、
`/home/leo/PonWRT-TYFZA1-epon-parity`，基线为 `1cc4824`。
未把修改写回其他 AI 正在使用的主工作区；内核使用独立 reflink 副本完成链接。

## 结论

**EPON 族尚未完整实现原厂全部功能，本次提交也不宣称完成全部遗漏。**
原有 EPON-D10G 注册、发现/LOID、FEC、DHCP/转发证据不能证明所有 CTC
管理功能、密钥交换、恢复与告警均已移植。下列清单保留每个未完成项，
不以未知 Set 返回成功、固定数值或空实现补齐计数。

## 原厂基准及 REA

权威基准为实际闪存备份 `ubi0_4-ubi_r0` 解出的 `bin/eponoamd`：
SHA256 `a9b075cdee5a2b176eca35e731c7b0d48881d46091d7b7469344e99c7f2e4b28`。
Mac 快照位于 `rea-analysis-20261016/inputs/userspace/eponoamd`，
VM 原始解包位于 `/home/leo/rea-analysis-20261016-work/rootfs/bin/eponoamd`。
工程旧 selected 目录的同名文件 SHA256 为
`02c2c04c846c04ccc933ded0689dd0eaaf9fef3920339eef1f4277cf8b292878`；
它与本轮完整备份在代码及数据上有差异，本轮最终对照使用完整备份。

REA 6.5.0 / Ghidra 12.1.2 通过既有本地 stdio 批处理运行。
102 条回调表项对应 88 个不同函数，已经全部保存 REA 反编译结果。
表项布局及入口由 `0x416e90` 的实际表查询代码核对。
两张扩展属性表由运行标志 `0x20000000` 选择，**不能仅根据 CTC
协商版本推断**。回调表项是入口清单，不等于功能已在实际设备启用。
表中函数指针仍需追到底层 wrapper，已确认多个原厂 setter 只是成功占位。
原始 REA 响应、请求和快照保留在 Mac 私有分析目录的
`rea/ghidra-epon-parity-*`；`manifests/epon-parity-evidence.json` 记录响应哈希，
不把原厂二进制或反编译全文放入公开仓库。

## 本次实际实现

- CTC 容器编解码：宽度0代表128字节，长值重复 branch/leaf 分段。
  加入长 Set 重组、AlarmThreshold Get 的2字节参数，以及无宽度/数据的
  RestartAutoConfig、Reset ONU 动作格式。后者只有格式已实现，Reset ONU
  动作本身仍未实现，不返回虚假成功。
- FirmwareVer 按原厂为3字节，软件身份字符串继续位于 ONU SN；版本来自
  编译时的实际包版本，未硬编码原厂的 `1.2.3` 占位值。
- 端口管理状态、链路状态、自动协商状态/能力/重启/开关、协商速率、Pause
  广告位。通过 Linux netdev/phylib/ethtool 消费，CTC端口1..4按原机
  `PORT_REMAPPING=03,02,01,00` 映射到 eth3..eth0。Pause 同时修改对称和
  非对称广告，保持速率能力；未照搬原厂特定标志下的空 ACK。
- ONU能力1/2/3从当前 PHY 能力和 LLID配置产生；未实现的光电源控制不声明
  支持。光模块诊断调用原生 BOSA I2C 驱动，输出温度/电压/偏置/TX/RX
  原始16位字；读取失败不返回虚构值。
- CTC opcode9 的密钥协商：1G为3字节、两种10G为9字节；同一索引请求
  重发相同密钥，索引切换生成新密钥。使用系统随机源，硬件写入成功后才回复。
  1G common-key寄存器、XG SA读改写、TX→RX顺序和slot0/1 packing均按
  实际 DAL/AAL 执行结果重写；XG同时恢复禁用TX MACsec后的PUC载荷/REPORT计费，
  调整GATE阈值为100并关闭GATE自动注销。
- 加密初始化、注册丢失/停止时的LLID失效；1G禁用表项真实清除valid，
  修复原厂channel4..7的禁用值仍含bit19。超时保留失败并传播，不伪装成功。
  表项未清理成功时后续刷新继续完成该实际清理。
- 每LLID注册会话编号在内核锁内验证。停止/重注册后，旧用户态密钥提交返回
  `ESTALE`；守护进程发现会话变化即撤销认证和缓存密钥。新内核/用户态
  需要配套部署；没有为旧 ABI 添加猜测式兼容路径。
- 接入1G/XG PCS链路叶中断、XG链路下降的CORESEC RX复位、加密启用时的
  GATE超时复位，以及原厂3秒采样、连续5次指定同步异常的PCS RX复位。
  这些恢复只复现确认的原厂条件，不以循环重启掩盖未知故障。

上述加密接口按本机原厂 DAL 的限制支持 LLID0..7；普通注册/数据接口仍有
32个LLID槽，不能据此声称32路加密均支持。实际光路、跨速率OLT及加密互通
尚未上机验证。本次未刷机、未替换运行驱动、未运行原厂程序。

## 原厂漏洞与不能照搬的实现

- `0x418378` 对未知Set打印“fake SETOK”并返回80；本实现保留失败返回。
- `0x424ef8` AlarmThreshold Get 把threshold写了两遍，未正确返回
  clearThreshold；告警后端尚待实现，此问题已登记，不能照抄。
- `aal_epon_crypto_1g_llid_set.part.0` 禁用channel4..7时的索引位覆盖valid。
  实际指令执行复现了这个问题；本实现禁用写0。
- 原厂部分间接访问超时仍返回成功；新加密写表等待失败直接返回错误。
- HGU `DEVICE_TYPE=1` 时的VLAN Set在 `0x4224cc` 直接ACK，
  VLAN Get在 `0x421e34` 不产生有效配置。SFU/hybrid另有真实VLAN后台；
  本项目没有把该HGU空动作当成已完成VLAN配置。
- SNMP Set、Classification Set、PortMode Config、RMS Set、Sleep Control等
  处理项存在无设备动作或仅验证长度的占位；没有移植成“已支持”。

## 验证范围

- 实际原厂 ARM64 `0x4162ec` 与生产编解码器逐字节对照：127/128/256/296
  字节、长Set、带参数Get、无数据动作。原厂侧只模拟memcpy/htons。
- 实际原厂 DAL+AAL 与生产加密helper对照：3种模式配置、2个slot、实际
  key packing和寄存器写值；channel4..7 valid位问题和间接访问超时传播。
  寄存器/table完成由模型提供，不能代替光口加解密测试。
- 生产用户态的密钥重发/轮换、失败不回复、会话清零、LLID及epoch字节绑定。
- 隔离netns里的真实Linux端口开关、原机逆序映射、拒绝PHY不支持操作，
  光诊断有符号温度及字序；未修改主机网络或光猫。
- 实际ARM64/musl守护进程14项网络集成检查；已有FEC检查和家族控制检查。
- Linux6.18.55的独立完整 `vmlinux` 链接，包含全部本次PON、NI和BOSA
  修改。最终源哈希/产物哈希及构建日志见本分支manifest和logs。

## 尚未完成

以下均须继续基于实际原厂后台重写，而不是用内存保存或固定ACK声称完成：

- CTC Service SLA/DBA REPORT参数与实际PUC调度消费，Holdover的实际定时行为。
- SFU/hybrid VLAN透明/标签/翻译/聚合/Trunk及Linux/FE业务规则消费。
- 组播VLAN、tag操作、受控组表、组数限制、fast leave与真实桥/硬件通路。
- 上下行限速/突发参数、环路检测/阻断及端口相关配置。
- 告警enable/threshold/hysteresis、事件状态Get/Set与真实检测/上报；必须修正
  原厂clearThreshold返回错误，不能只有参数存储。
- 296字节性能计数及周期/历史/复位，需要硬件统计。容器长度已经支持，但
  计数后端仍缺失；不能拿CPU软件计数冒充含FE转发的端口统计。
- 远程ONU重启、发光控制、软件接收/校验/安装/激活/提交，需要真正OpenWrt
  生命周期和镜像校验消费者。不能调用原厂shell、MIB或fwu脚本强行兼容。
- 芯片/光收发器静态信息、扩展设备管理项、厂商特定OUI与本机实际启用范围。
- Dying Gasp的电源事件到发送路径、原厂自动/节能/时间同步等EPON可选链路
  的实际启用条件；尚未建立完整覆盖证明。
- 1G/10G对称与不同OLT、加密线路、多LLID及真实恢复光路验收。

## 表项清单

`implemented_offline`只表示有原生消费者并完成本文列明的离线验证；
不代表所有硬件分支均已实测。`not_ported`保留为未完成。

| 表 | Branch/Leaf | 原厂名称 | 本次状态 |
| --- | --- | --- | --- |
| standard_attributes | 07/0025 | aPhyAdminState | implemented_offline |
| standard_attributes | 07/004f | aAutoNegAdminState | implemented_offline |
| standard_attributes | 07/0052 | aAutoNegLocalTechnologyAbility | implemented_offline |
| standard_attributes | 07/0053 | aAutoNegAdvertisedTechnologyAbility | implemented_offline |
| standard_attributes | 07/0139 | aFECAbility | implemented_offline |
| standard_attributes | 07/013a | aFECmode | implemented_offline |
| standard_actions | 09/0005 | acPhyAdminControl | implemented_offline |
| standard_actions | 09/000b | acAutoNegRestartAutoConfig | implemented_offline |
| standard_actions | 09/000c | acAutoNegAdminControl | implemented_offline |
| extended_default | c7/0001 | ONU SN | implemented_offline |
| extended_default | c7/0002 | FirmwareVer | implemented_offline |
| extended_default | c7/0003 | Chipset ID | not_ported |
| extended_default | c7/0004 | ONU Capabilities-1 | implemented_offline |
| extended_default | c7/0005 | OpticalTransceiverDiagnosis | implemented_offline |
| extended_default | c7/0006 | Sevice SLA | not_ported |
| extended_default | c7/0007 | ONU Capabilities-2 | implemented_offline |
| extended_default | c7/0008 | HoldoverConfig | not_ported |
| extended_default | c7/0009 | MxUMngGlobalParameter | not_ported |
| extended_default | c7/000a | MxUMngSNMPParameter | not_ported |
| extended_default | c7/000b | Active PON_IFAdminstate | not_ported |
| extended_default | c7/000c | ONU Capabilities-3 | implemented_offline |
| extended_default | c7/000d | ONU power saving capabilities | not_ported |
| extended_default | c7/000e | ONU power saving config | not_ported |
| extended_default | c7/000f | ONU Protection Parameters | not_ported |
| extended_default | c7/0011 | ethPort State | implemented_offline |
| extended_default | c7/0012 | ethPort Pause | implemented_offline |
| extended_default | c7/0013 | ethPortUs Policing | not_ported |
| extended_default | c7/0014 | voip port | not_ported |
| extended_default | c7/0016 | EthPortDs RateLimiting | not_ported |
| extended_default | c7/0017 | PortLoopDetect | not_ported |
| extended_default | c7/0018 | PortDisableLooped | not_ported |
| extended_default | c7/0019 | PortLoopParameterConfig | not_ported |
| extended_default | c7/00a4 | MAC Aging Time | not_ported |
| extended_default | c7/0021 | VLAN | not_ported |
| extended_default | c7/0031 | Classification | not_ported |
| extended_default | c7/0041 | Add/Del Multicast VLAN | not_ported |
| extended_default | c7/0042 | MulticastTagOper | not_ported |
| extended_default | c7/0043 | MulticastSwitch | not_ported |
| extended_default | c7/0044 | MulticastControl | not_ported |
| extended_default | c7/0045 | Group Num Max | not_ported |
| extended_default | c7/0046 | aFastLeaveAbility | not_ported |
| extended_default | c7/0047 | aFastLeaveAdminState | not_ported |
| extended_default | c7/006b | POTSStatus | not_ported |
| extended_default | c7/0081 | AlarmAdminState | not_ported |
| extended_default | c7/0082 | AlarmThreshold | not_ported |
| extended_default | c7/00a1 | ONUTxPowerSupplyControl | not_ported |
| extended_default | c7/00b1 | performanceMonitoringStatus | not_ported |
| extended_default | c7/00b2 | performanceMonitoringDataCurrent | not_ported |
| extended_default | c7/00b3 | performanceMonitoringDataHistory | not_ported |
| extended_default | c7/801f | configVlanOfTr069Wan | not_ported |
| extended_default | c7/c001 | oltLocationConfig | not_ported |
| extended_flag_0x20000000 | c7/0001 | ONU SN | implemented_offline |
| extended_flag_0x20000000 | c7/0002 | FirmwareVer | implemented_offline |
| extended_flag_0x20000000 | c7/0003 | Chipset ID | not_ported |
| extended_flag_0x20000000 | c7/0004 | ONU Capabilities-1 | implemented_offline |
| extended_flag_0x20000000 | c7/0005 | OpticalTransceiverDiagnosis | implemented_offline |
| extended_flag_0x20000000 | c7/0006 | Sevice SLA | not_ported |
| extended_flag_0x20000000 | c7/0007 | ONU Capabilities-2 | implemented_offline |
| extended_flag_0x20000000 | c7/0008 | HoldoverConfig | not_ported |
| extended_flag_0x20000000 | c7/0009 | MxUMngGlobalParameter | not_ported |
| extended_flag_0x20000000 | c7/000a | MxUMngSNMPParameter | not_ported |
| extended_flag_0x20000000 | c7/000b | Active PON_IFAdminstate | not_ported |
| extended_flag_0x20000000 | c7/000c | ONU Capabilities-3 | implemented_offline |
| extended_flag_0x20000000 | c7/000d | ONU power saving capabilities | not_ported |
| extended_flag_0x20000000 | c7/00a5 | ONU power saving config | not_ported |
| extended_flag_0x20000000 | c7/00a6 | ONU Protection Parameters | not_ported |
| extended_flag_0x20000000 | c7/000f | Configuration Counter | not_ported |
| extended_flag_0x20000000 | c7/0011 | ethPort State | implemented_offline |
| extended_flag_0x20000000 | c7/0012 | ethPort Pause | implemented_offline |
| extended_flag_0x20000000 | c7/0013 | ethPortUs Policing | not_ported |
| extended_flag_0x20000000 | c7/0014 | voip port | not_ported |
| extended_flag_0x20000000 | c7/0016 | EthPortDs RateLimiting | not_ported |
| extended_flag_0x20000000 | c7/0017 | PortLoopDetect | not_ported |
| extended_flag_0x20000000 | c7/0018 | PortDisableLooped | not_ported |
| extended_flag_0x20000000 | c7/0019 | PortLoopParameterConfig | not_ported |
| extended_flag_0x20000000 | c7/001d | ONUPortMode Config | not_ported |
| extended_flag_0x20000000 | c7/001e | ONUPortMode Status | implemented_offline |
| extended_flag_0x20000000 | c7/000e | MAC Aging Time | not_ported |
| extended_flag_0x20000000 | c7/0021 | VLAN | not_ported |
| extended_flag_0x20000000 | c7/0031 | Classification | not_ported |
| extended_flag_0x20000000 | c7/0041 | Add/Del Multicast VLAN | not_ported |
| extended_flag_0x20000000 | c7/0042 | MulticastTagOper | not_ported |
| extended_flag_0x20000000 | c7/0043 | MulticastSwitch | not_ported |
| extended_flag_0x20000000 | c7/0044 | MulticastControl | not_ported |
| extended_flag_0x20000000 | c7/0045 | Group Num Max | not_ported |
| extended_flag_0x20000000 | c7/0046 | aFastLeaveAbility | not_ported |
| extended_flag_0x20000000 | c7/0047 | aFastLeaveAdminState | not_ported |
| extended_flag_0x20000000 | c7/006b | POTSStatus | not_ported |
| extended_flag_0x20000000 | c7/0081 | AlarmAdminState | not_ported |
| extended_flag_0x20000000 | c7/0082 | AlarmThreshold | not_ported |
| extended_flag_0x20000000 | c7/00a1 | ONUTxPowerSupplyControl | not_ported |
| extended_flag_0x20000000 | c7/00a3 | RMS Global Parameter Config1 | not_ported |
| extended_flag_0x20000000 | c7/00a4 | RMS Global Parameter Config2 | not_ported |
| extended_flag_0x20000000 | c7/00b1 | performanceMonitoringStatus | not_ported |
| extended_flag_0x20000000 | c7/00b2 | performanceMonitoringDataCurrent | not_ported |
| extended_flag_0x20000000 | c7/00b3 | performanceMonitoringDataHistory | not_ported |
| extended_flag_0x20000000 | c7/7003 | Optical Transceiver Information | not_ported |
| extended_flag_0x20000000 | c7/801f | configVlanOfTr069Wan | not_ported |
| extended_flag_0x20000000 | c7/c001 | oltLocationConfig | not_ported |
| extended_actions | c9/0001 | Reset ONU | not_ported |
| extended_actions | c9/0002 | Sleep Control | not_ported |
| extended_actions | c9/0048 | acFastLeaveAdminControl | not_ported |


## 后续：光学与 UNI LOS 告警

新增 `pon-alarm.c` 原生后端：CTC C7/0081设置、C7/0082带参数查询/阈值设置、
opcodeFF状态/阈值Get/Set、code1事件通知及序号。实际消费者包括20个
0x0101..0x0114光学告警/预警与四个UNI的0x0302 LOS。
BOSA采样与端口carrier来自现有原生接口；counter类告警仍等待硬件PM，
不能据此把全部告警标为完成。

原厂 ARM64 函数的20个滞回条件及事件TLV已逐项执行对照。新实现用精确
原始值比较替代单调的光功率log10、偏置/电压单位换算，保留严格不等式和
滞回边界；温度按有符号16位原始值比较。复现了原厂0x424ef8把触发阈值
写两遍的问题，新后端正确返回实际clearThreshold。

发送失败不会把事件标为已经上报；采样失败不发送旧采样的待定事件，
`alarm_error`显示真实错误。守护进程19项ARM64/musl集成检查通过，包含
实际OAM阈值设置/查询、code1触发/清除上报、FF状态查询、滞回与读取失败。
证据：`manifests/epon-optical-alarms.json`。未进行物理传感器/OLT验证。
