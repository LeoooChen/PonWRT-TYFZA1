# RTL9617C 有线硬件转发移植

当前优先LAN↔PON的IPv4 TCP/UDP硬件转发。Wi-Fi通路随后接入。Linux软件NAT已经板测通过，FE硬件加速尚未启用。

## 已确认的硬件实现

本机原厂ca-ne.ko包含aal_hash_add、aal_hash_profile_init、hash_value_calculate、convert_act_flow_nomal_mode和_hash_hw_entry_set；Cortina GPL参考的aal-77c提供同一主Hash/分类/包编辑器实现。aal_hashlite.c及其部分头文件仅6字节占位，当前依据实际主Hash实现推进。

流键为736位（92字节），包括IPv4地址、协议、TCP/UDP端口、入口、POL_ID/LLID和profile。掩码的软件格式224位（28字节）；硬件分为两个128位bank，访问地址bit6选择上半部，DATA0..3对应NI窗口3920、391c、3918、3914。原生事务使用完整8个word，未使用的最高word置0。

普通流动作使用group18+20，合计224位，占32字节槽。包含目的LDPID/深队列、入口/出口L3接口、源MAC改写、目的MAC索引、TTL、单向NAT地址和端口、VLAN、COS等67个字段。正常模式选择字段ip_type区分源/目的IPv4改写；每个方向独立建流。

主Hash大小支持512..65536条、bucket4..32槽；Hash值和动作存放于设备DMA内存。硬件cache最高2048条，可使用内部SRAM容纳224位普通动作。初始化还涉及默认miss动作、overflow、aging、cache失效、L3入口profile和AXI重排序。GPL原厂路径把默认FIB设为外部存储，并配置L3FE AXI原ID=0xf、新ID8/9，这些初始化关系会纳入原生实现。

## 原生代码与验证

rtl9617c-fe-format.h提供位字段写入、IPv4动作/流键、掩码及CRC；rtl9617c-fe-table.h提供两个bank的读写事务。正常流动作逐字节对照本机原厂ARM64转换函数：67字段、330个单字段边界/随机向量全部一致。流键、实际原厂mask处理结果、CRC16与CRC32对照原厂hash_value_calculate：160组IPv4 TCP/UDP、32个LLID和4个profile全部一致；该轮tuple旋转配置为neutral。

测试工具只在Unicorn离线执行指定原厂机器码；设备运行原生实现。scripts/test_fe_action_oracle.py、test_fe_key_oracle.py保存本机模块及源码哈希，test_fe_table.c的ASan/UBSan模型验证64个掩码槽的bank顺序、恢复和事务失败传播，以及动作/流键缓冲范围。

2026-10-08实机执行原生live_fe_mask模块：保存空闲mask63，写入IPv4掩码，读取两个bank逐字比较，再恢复并核对原内容。结果readback=0、original_restored=0；HashINI和CacheINI均0。PON注册、认证和WAN保持正常。该结果证明原生掩码表访问，不是硬件转发流量验收。

证据在logs/fe-action-oracle.json、fe-key-oracle.json、live-fe-mask01-device.txt，汇总为manifests/fe-native-tests.json。

## 后续执行路径

配置由Linux拥有的Hash/动作/默认miss DMA表与cache，补齐MAC和L3接口索引表、双向NAT动作及CPU miss通路。接入Linux flow block/flowtable回调，让连接规则安装、删除、计数和老化由内核状态驱动。有线入口命中后进入硬件包编辑器，LAN下行到NI，PON上行进入原生深队列/PUC；OAM及未命中报文继续CPU路径。

验收同时核对硬件命中/字节计数、LAN↔PON实际报文、NAT端口/IP/MAC/TTL/校验和、连接删除/老化以及CPU接收计数。硬件吞吐和CPU减少需从这些实机数据得出，不能由编码或表项读回代替。


## 2026-10-09：ASIC CRC与DMA动作读取

原生Header-I构造为1024位，SWO端口依次提交32个word及mask指针，通过bit0等待计算完成，再读实际CRC和736位输出tuple。128组IPv4 TCP/UDP、LAN LSPID0/PON LSPID7向量与软件流键及CRC16/CRC32完全一致。诊断SWO输入的orig_lspid、POL_ID和profile未进入输出tuple；逐位扫描输入头已经记录，实际报文入口的LLID/stream metadata仍需从后续包轨迹验证，不能将软件布局一致扩大为这些字段的硬件闭环。

原生FE DMA准备层分配151552字节Linux coherent内存，包含4096个CRC表项、4096个32字节动作、64个overflow动作和32个16字节默认miss动作。4096条采用32槽bucket；cache为256条内部224位SRAM。base寄存器使用已验证的CCI高地址别名2。硬件MEM_INI请求完成，全部base与size读回符合配置，退出时恢复原配置并释放内存。

实际硬件cache分配从上述Linux内存读取到CRC32=12345678。stage2 loc=0时读回动作为零，stage3 loc=1时224位NAT动作逐word与CPU写入内容一致。确认主Hash和动作的DMA可达性、age发布及cache invalidate入口。此轮保持有线入口在已验证软件路径，尚未启用真实连接的硬件转发。

接下来通过L3FE入口profile、分类默认结果和明确的静态双向流，把真实报文送入Hash/包编辑器；验证NAT改写、TTL/校验和与CPU miss后接Linux flow block/flowtable。未启用的硬件转发不在LuCI中标为成功。

用户新增无线研究已冻结到evidence/wireless-study-20261008，共433个文件、约66MB，逐文件收据为evidence/wireless-study-import-files.json。含RTL8192XB/RTL8832C固件与原厂FC/NE元数据链，按用户顺序在EPON有线硬件转发完成后推进Wi-Fi。GPON/XG-PON/XGS-PON暂缓，最后实施OpenWrt UBI刷入。


## 2026-10-09：真实包的硬件NAT、MAC与LAN出口

新增原生fe-pipeline.h，实现分类默认FIB、入口LPB、源MAC CAM、L3出口接口和目的FDB命令。以NI DMA注入一条UDP五元组，经LDPID25/PDPID13进入L3FE。实际包经过Hash profile0匹配index1280（CRC ceb0f986/5141），源IP从10.23.42.1改为198.51.100.1，源端口53000改为62000，TTL64改为63；源MAC和目的MAC分别改为02:66:55:44:33:22、02:77:66:55:44:33。payload完整，IPv4和非零UDP校验和均正确。

把同一匹配流的出口改为LDPID0后，Mac en5在线上抓到相同的正确改写帧；本次测试CPU RX计数前后均7626。这证明Hash、包编辑器和LAN物理出口已在硬件通路执行。它仍是明确的一条NI注入静态流，尚未作为Linux动态连接卸载。

由实包暴露并修复的格式问题：TTL mask的0是忽略TTL，3为TTL范围编码；初始全1掩码导致实包CRC不同，离线测试新增变化TTL覆盖。FDB命令必须暂设cpu_opt_mode bit31；append成功应答不提供目的MAC索引，重新lookup才得到3056。原厂模块table ID20/23及机器码证明MAC CAM selector为3，旧参考注释4不适用本机。源MAC改写使用smac_trans=0并指定有效L3出口接口。

该轮物理入口实验尚未打通；其UDP发送工具后来被证实没有始终在en5线上发送指定五元组，因此不能单凭这些实验判断分类级故障。NI的a5f8为RX控制帧旁路配置，原来的“强制全部CPU旁路”名称不准确。下面的直接以太网注入实验解决了这两个证据问题。

## 2026-10-09：物理LAN与XEPON双向硬件转发

tools/mac_fe_inject.c通过Mac BPF直接注入固定Ethernet/IPv4/UDP帧，避免普通UDP套接字的实际源地址、端口与静态Hash键不一致。芯片L2/L3监视锁存和指定五元组的采样确认实际解析内容；采样保留匹配时的Header-I，后续串口输出不再被其他流量替换。

物理LAN入口只需把NI RXctrl目的从CPU16改为LAN L3连接器25。逐项隔离验证：保持该目标16并修改ICLS/PONCLE没有硬件返回；目标25且保持ICLS/PONCLE原内容时10帧全部硬件返回。10帧的NAT、MAC、TTL、IPv4/UDP校验和、载荷完整性均通过。证据为logs/fe-physical-wire02.pcap及其-check.json。

XEPON上行使用LDPID32和deepq，经过本工程原生QM/PUC进入LLID0。运营商DNS收到ASIC改写为WAN地址及端口62000的请求并发回对应答复。下行仅调整PDC正常业务的0/128两项到WAN L3连接器24，并清除FE bypass；PDC控制/OAM项保持CPU路径。WAN入口LPB使用分类profile1，默认Hash选择仍为profile0。反向Hash动作执行目的IP/端口DNAT，并改写LAN源/目的MAC。

实际下行Header-I验证了LSPID7。该测试掩码忽略orig_lspid，因此构造CRC时必须清除此字段：实际masked tuple word22为00000700；错误的0000071c导致Hash miss。修正后的双向规则收到3/3 DNS答复，目的还原为测试客户端10.23.42.1:53000，IPv4/UDP校验和均正确。最终pcap和逐包验收在logs/fe-bidir-dns04.pcap、logs/fe-bidir-dns04-check.json。

同时在光猫eth0和pon-data0采集CPU收到的帧：规则生效阶段指定DNS流没有进入CPU；删除主Hash age、失效对应cache后，硬件返回停止，原请求与软件转发回包进入CPU。此轮没有出现删除后的旧缓存硬件返回。证据为logs/fe-bidir-lifecycle02-check.json，私有CPU抓包位于analysis/private/live-oam/。

normal action的L3_IF_COUNTER_EN bit42启用出口接口MIB，每个接口拥有64位字节数、32位单播包数等7个word。原生fe-pipeline.h增加64个TX IFID槽的读取。实机上行3包/213字节、下行3包/1275字节，两个方向丢弃数均0；ASan/UBSan测试包含该实测布局。主Hash age初始1在当前配置下未随命中改变，尚不能将其直接当作活跃计数；Linux流统计与lastused将依据实测IFID计数推进。

本轮验证为两条原生静态IPv4/UDP规则，尚未完成Linux动态flowtable安装、删除、老化以及TCP/VLAN参数覆盖。固件常规启动仍使用已验证软件转发，hardware_forwarding_enabled保持false。下一步完成动态有线卸载与协议切换生命周期后，再适配Wi-Fi，最后实施原生OpenWrt UBI安装与升级；GPON族按用户要求暂缓。
