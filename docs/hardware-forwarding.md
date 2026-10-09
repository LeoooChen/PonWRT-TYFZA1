# RTL9617C 有线硬件转发移植

当前优先LAN↔EPON有线硬件转发。XEPON的IPv4 TCP/UDP动态硬件NAT与持久配置已实机验证；IPv6路由格式和原厂表容量继续对齐。Wi-Fi通路随后接入。

2026-10-09离线更新：VLAN/QinQ和共享L3出口接口进入生产源码，镜像full-pon-wired-vlan01-6.18.55已构建并通过最终FIT载荷核验。实机离线，新增功能仍待板测。Wi-Fi实施顺序见wifi-port-order.md。

## 已确认的硬件实现

本机原厂ca-ne.ko包含aal_hash_add、aal_hash_profile_init、hash_value_calculate、convert_act_flow_nomal_mode和_hash_hw_entry_set；Cortina GPL参考的aal-77c提供同一主Hash/分类/包编辑器实现。aal_hashlite.c及其部分头文件仅6字节占位，当前依据实际主Hash实现推进。

流键为736位（92字节），包括IPv4地址、协议、TCP/UDP端口、入口、POL_ID/LLID和profile。掩码的软件格式224位（28字节）；硬件分为两个128位bank，访问地址bit6选择上半部，DATA0..3对应NI窗口3920、391c、3918、3914。原生事务使用完整8个word，未使用的最高word置0。

普通流动作使用group18+20，合计224位；按本机原厂默认参数使用48字节槽。包含目的LDPID/深队列、入口/出口L3接口、源MAC改写、目的MAC索引、TTL、单向NAT地址和端口、VLAN、COS等67个字段。正常模式选择字段ip_type区分源/目的IPv4改写；每个方向独立建流。

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

## 2026-10-09：Linux动态flowtable后端与实机验收

新增rtl9617c-fe.c，连接Linux TC_SETUP_FT/FLOW_CLS_REPLACE、DESTROY和STATS。内核的IPv4 TCP/UDP五元组、以太网改写与单向NAT动作转换为原生Hash动作；主Hash使用4096个槽、每bucket32槽，当前逐流统计占用64个TX IFID资源。源MAC CAM与目的FDB按引用共享，出口接口和Hash槽在最后引用释放后恢复。槽不足或当前尚未实现的IPv6、VLAN、隧道、双侧NAT动作继续由Linux软件路径处理，尚未作为全部硬件功能完成。

eth0和每个pon-dataN接入flowtable回调；PON控制事务负责启停FE和PDC业务入口。停止MPCP/traffic后先移除硬件流与入口，再进行PON重置；OAM保持原有CPU路径。动态回调只获取FE锁，不获取PON状态锁或RTNL，避免与控制事务形成反向锁序。接口关闭清除相关流，接口注销遇到尚未完成的释放时保留其记录，待清理结束再回收。

主Hash配置增加TCP FIN/SYN/RST、IPv4错误/分片和untagged匹配约束。GPL aal_common.h中mask.vlan_cnt位于bit100；最初误写为bit114，引入错误字段匹配，导致内核虽标HW_OFFLOAD但实际仍走软件。运行中的原生诊断模块修正了mask63 word3，之后实际UDP和TCP均命中；full-pon-flowtable04镜像内置正确位，启动无需诊断修补。

Linux当前nf_flow_table_offload.c的实际NAT构造函数生成1024个TCP/UDP双向向量，对照生产解析函数，通过ASan/UBSan验证端口、IPv4及MAC字节序。生产释放函数验证共享MAC的最后引用、CAM恢复失败重试、注销记录保留和Hash/计数器资源释放。统计测试验证355个线速字节/5包转换为285个IP字节，空闲lastused保持不变，以及32位包计数回绕。

首次动态UDP验收：由Linux创建双向规则，之后5请求/5答复未出现在设备eth0/pon-data0的CPU抓包中，conntrack两个方向各增加5包。初始统计包含以太网头，现已减去untagged的14字节以匹配conntrack；最新镜像上行5包/285字节与真实IPv4长度一致。抓包前等待HW_OFFLOAD，避免把异步安装期间的首批软件转发算作硬件阶段。UDP GRO会合并多个帧，少量CPU抓包条数不能证明绕过CPU，只有目标流零CPU记录与真实收发、计数同时成立才验收。

TCP使用tools/mac_fe_tcp.c在Mac en5注入原始Ethernet帧，完成10.23.42.1:53123→1.1.1.1:80的握手及30次HTTP请求，收到11580字节响应；持续阶段6秒CPU捕获中该TCP流在两个方向均0帧，conntrack标HW_OFFLOAD并增加计数。普通主机socket请求没有出现在光猫conntrack中，其结果不作为光猫TCP验收证据。

最新镜像为artifacts/full-pon-flowtable04-6.18.55/ram-pon/image.itb，SHA256为02351a56b688ea757f00ea26527fc94d05e174df5c39197b39263dd618ab519f，6087828字节。最终FIT内核、DTB、内置initramfs994项与外置对应内容一致，校准、身份、六个SerDes协议配置齐全，实机四核、XEPON注册、认证和DHCP已恢复。MPCP停止实测把FE从enabled=1/flows=2变成enabled=0/prepared=0/flows=0，PDC恢复CPU16；重新注册后OAM generation=4、认证历史成功数2、WAN网关ping3/3。

当前启动默认仍为软件转发。测试启用方式为PON设备fe属性写1，再开启firewall defaults的flow_offloading与flow_offloading_hw并reload fw3；这只是一条当前开发控制入口，UCI/LuCI持久配置尚待接入。现有实机证明为XEPON的IPv4 untagged TCP/UDP动态卸载；其他两种EPON光链路、VLAN/IPv6卸载、完整吞吐及长期稳定性仍需对应验证。按用户顺序完成这些有线工作后推进Wi-Fi，GPON族暂缓，最后完成原生UBI安装与升级。

## 2026-10-09：持久配置、IPv6格式和原厂通路核对

硬件入口现在由OpenWrt firewall defaults中的flow_offloading和flow_offloading_hw共同控制。首次配置默认启用；已经明确配置为0的值不覆盖。PON服务启动执行offload动作，firewall配置事件只更新FE，不重启OAM。LuCI PON页使用同一项原生防火墙配置并显示实际FE状态、流数、安装和删除数量。板上关闭/启用事件前后OAM PID3153、process_start137102保持不变，认证持续accepted；服务触发器同时包含pon reload和firewall offload。

整包artifacts/full-pon-offload-ui01-6.18.55/ram-pon/image.itb已实机默认启动：SHA256696e7da4a1823b653abf0a51adfd68d8bc497dadfd221cd795ff3e9933ae565d，6088340字节，UCI硬件开关1，FE enabled1且动态流已安装，XEPON认证与DHCP恢复。最终FIT根文件系统995项校验通过，证据为logs/offload-ui01-default-boot-device.txt和offload-ui01-payload-verification.json。

IPv6 key和Header-I包含完整128位地址；网络序word0为高32位，硬件ip_sa/da_0为低32位。SWO除了ip_ver，还需要IPv6 packet type6；补齐后128组IPv6 TCP/UDP tuple和CRC与ASIC全部一致，同一full128 mask的128组IPv4也通过。实包NI注入经Hash命中后从LAN输出，IPv6地址2001:db8:0:1::2→2001:db8:0:2::2不变、Hop Limit64→63、源/目的MAC改写、UDP校验和和载荷正确，CPU RX前后均2623。该轮为静态NI注入，不扩大成外部PON IPv6动态验收。原生动态解析器已加入IPv6路由，1028组IPv4 NAT/IPv6解析向量通过；动态IPv6整包及实机尚待完成。第一轮抓包地址误写成2001:db8:1::2，空pcap不是出口故障证据，按实际地址/MAC重新抓取通过。

用户要求按原厂核对UNB/BND。这两个名称在Airoha PPE中是FOE的UNBIND/BIND状态，不能直接当作RTL9617C寄存器枚举。当前已验证的硬件业务属于“软件建流→有效Hash查表命中→硬件NAT/包编辑→实际出口”语义，等价于用户关注的BND型查表转发。本机ca-ne.ko的aal_hash_add、aal_hash_profile_init、convert_act_flow_nomal_mode、_hash_hw_entry_set已查到真实代码；RTL8277C原厂参考的path34/5使用五元组profile并调用aal_hash_add。当前对照的是同一主Hash/packet editor，不套用Airoha FOE格式。

原厂type0动作设置cache_ctrl1=1，原生初期动作默认0，本轮已修正。实机原生模块以该字段1完成双向3包，上行213字节、下行1275字节，均0丢弃。原厂参考将非软件整形流age置1；当前原生动态后端age7由Linux按逐流IFID统计管理生命周期，这一差别尚待对齐。本机default_scfg.txt声明主Hash65536项、cache2048项、动作槽48字节；当前原生Hash4096项、cache256项、224位动作32字节、独立IFID统计最多64个方向流。该文件是原厂默认配置，尚未替代原厂运行时寄存器快照。不能宣称参数容量已经完整复现。

实验性设置CACHE_MISC=80000000/c0000000后双向转发与统计仍正常，但自动cache扫描未见有效项、main age仍1。自动cache与原厂活跃/老化路径仍需继续查证；原生动态流的实测硬件计数和CPU绕行结论保持有效。证据记录为logs/fe-stock-cache-alignment-device.txt。


## 2026-10-09：原厂容量与缓存检查掩码

原生主Hash调整为65536项、每桶32槽，内部动作cache配置2048项，主动作槽48字节。分配3411968字节coherent DMA，HashINI读回0000009f、CacheINI读回00010304。CRC16桶映射使用原厂crc16_mask对应的crc16 & 0xffe0；全部65536个CRC桶映射和65536项age更新通过ASan/UBSan模型验证，并保留相邻31项。

实机诊断模块完成主Hash/DMA/cache手动取数自检和物理LAN↔XEPON双向转发。上行CRC4c6f命中19552槽，下行CRCff4b命中65344槽；两个方向各3包，上行213字节、下行1275字节、0丢弃。该测试验证高地址槽及48字节动作跨度，证据为logs/fe-stock-capacity02-device.txt。首次容量诊断仍将CRC0123测试项写在旧几何的槽0，cache命令返回err_hash；诊断已改为从相同CRC桶函数确定实际槽256后通过。

原厂aal_hash_cache_init实际机器码设置CACHE_MISC=a0000000、两个cache age单位寄存器ffffffff。按这组参数，容量诊断暂未观察自动cache项。继续对照aal_hash_add和FC动作生成，发现原厂会把chk_msk_ptr1设为该流mask索引；原生动作先前为0，而实际流使用63。生产动作和实机诊断已加入检查mask参数，修正后的缓存板测待执行。主Hash非整形age1/原生动态age7、64个独立IFID统计资源限制仍是后续对齐工作。

原厂FC初始化还显式调用aal_hash_aging_timer_set(0)，关闭硬件老化倒计时；rtk_8277c_asic_flow_traffic_get调用aal_hash_traffic_status_get，以32项为组读age>1活跃位，并把有效项恢复为age1。当前由Linux flowtable通过IFID计数更新lastused，与原厂同属软件管理生命周期；原生尚未使用这组活跃位，所以仍保留64方向流的计数资源限制。此处依据原厂FC/NE参考实现，待进一步对照本机实际模块的相关函数。

## 2026-10-09：离线容量、VLAN与生命周期更新

原生L3出口接口改为独立引用对象；前64个方向流有独立MIB，后续流可共享相同源MAC接口并关闭动作中的interface counter位，避免污染其他流的计数。所有流读取主Hash age活跃位，只有age>1或独立MIB有变化才更新Linux lastused；读取活跃位后恢复age1。之前实机已确认自动cache命中时age能6→1→6，本轮新增共享接口实现尚待板测。

4096流/15源MAC离线调用实际生产资源函数通过ASan/UBSan，覆盖共享引用、原计数流删除、计数重新归属、最后引用恢复和寄存器失败重试。精确计数最多64个方向流；其他流有独立活跃状态，不报告伪造的逐流字节/包数量。sysfs和LuCI分开显示总流数、counted_flows及activity_flows。安装过程中发生释放失败时保留硬件资源对象，由下一次控制回调重试；共享FDB引用只复用实际仍持有目的对象的流。

VLAN实现匹配外/内VID、TPID和标签数，保持PCP/DEI不参与key。TPID原厂解析表8100/88a8/9100/9200、使能77；当前路由动作支持802.1Q/802.1ad字段和LinuxVLAN POP/PUSH，最终动作写入0..2个标签。Linux当前flowtable实际构造802.1Q匹配及动作；802.1ad字段接受不扩大为该上游自动生成QinQ卸载。保留未知PCP/DEI的部分栈改写仍由Linux处理，完整pop/push和untouched栈可以表达。

1028组NAT/IPv6及288组使用实际Linux匹配与POP/PUSH代码的VLAN向量通过；VID0/4095、TCP/UDP、输入/输出0..2层、标签顺序、无效掩码和动作位域均检查。计数按实际输出Ethernet头长度14/18/22扣除，QinQ五包395线包字节对应285IP字节通过。镜像SHA256=95f2ea01e98e400556ccf9e5b1a7b4c2b445d6675351920702add93a5ca350c7，6089552字节；FIT实际内核/DTB/initramfs995项一致。新增功能没有在离线期间标作硬件验收。

证据：logs/fe-interface-capacity.json、fe-flow-parser.json、fe-flow-release.json、wired-vlan01-payload-verification.json；构建服务ponwrt-tyfza1-lan-20261009-092739正常退出0。

chk_msk_ptr1=63修正后的实机模块在同样3次双向请求中自动生成cache608/2016，主Hash两方向age均从1升到6；线上6帧的IPv4和UDP校验和、DNS ID正确。接着按原厂补齐cache_age0_hit、tuple priority0和main aging countdown0，双向转发与自动cache继续通过。删除两个流后cache从2变0，再发送3次请求，原IFID计数保持3/3、cache仍0，CPU捕获到1条GRO记录，载荷正好为3份完整DNS查询。证据为logs/fe-stock-cache05-wire-check.json、fe-stock-cache05-device.txt、fe-stock-cache06-device.txt和fe-stock-cache06-cpu-check.json。

生产后端已接入这些原生配置：64K Hash（65536项）、2K内部cache、48字节主动作槽、check mask63、自动cache替换、有效/空闲age1与Linux连接生命周期。artifacts/full-pon-stock-fe02-6.18.55由脱离Codex的systemd后台服务构建，最终镜像启动测试尚待完成。64方向流的独立IFID统计限制仍待解除。

新增160组VLAN/QinQ流键直接对照本机原厂hash_value_calculate实际机器码，总计320组masked key及CRC16/CRC32全部一致；保留原160组IPv4 TCP/UDP向量。
