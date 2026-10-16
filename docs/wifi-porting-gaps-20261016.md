# TYFZA1 Wi-Fi 全范围移植遗漏清单

2026-10-16，基线 `1cc4824d3316851ef18b1eb8fe0079154192589c`。

审计范围覆盖全部 Wi-Fi 功能域，不按高速丢包相关性筛选。本报告区分确定遗漏、部分覆盖、条件功能、Linux分工以及证据不足，不能把待判定项记成完成。

## 原厂目录与证据

冻结原厂模块 SHA256：`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。从实际ELF提取全部9740个函数符号、70组含函数指针的对象、121个模块参数及170个旧MIB字段；包含完整MAC/PCIe ops、39个cfg80211注册入口、action/MLME表、固件C2H/event、SER、厂测与诊断目录。重复函数名/别名保留原始section/offset，不以文本名相同推断行为相同。

[原厂完整目录](../artifacts/wifi-coverage-audit-20261016/stock-inventory.json.gz) 由 [inventory_stock_wifi.py](../tools/inventory_stock_wifi.py) 直接读取本机原厂模块生成；[REA索引](../artifacts/wifi-coverage-audit-20261016/rea-index.json) 和完整归档保留反编译Evidence ID/散列。能力与watchdog关键门控另用原厂ARM64实际指令定点执行。目录扫描不是全量测试，也不是逐函数已审完的宣称。

原厂函数名、配置字段和SDK能力只能作为入口；每行结论还需结合注册、调用、门控和项目实际消费者。未提供功能不一定在原机默认开启。原厂模块参数编译初值也不等于flash/运行期最终值。

[机器可读清单](../manifests/wifi-factory-coverage.json) 保留每项状态、源代码锚点和可复查的原厂符号检索式。`D/` 表示 `port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/`。源码只读检视也涉及NI/FE无线集成和Linux/wpad；未修改PON或共享以太网文件。

## 如何读状态

- **已有**：存在所列原生实现，不代表全部配置或硬件验收完成。
- **缺失**：已核对的有效原厂/必要Linux集成链在项目中缺少实现。
- **部分**：有基础实现，但列出的分支、输入或控制闭环未完整覆盖。
- **条件缺口**：原厂存在有条件实现，项目无等价链；本机门控必须保留。
- **未提供**：项目明确不提供该能力；原厂条件/公开接口见该行，不自动视为当前默认故障。
- **Linux接管**：由当前Linux/wpad提供软件职责，不应重复搬原厂协议状态机。
- **不列为缺口**：原厂空实现、本机bypass/关闭或缺本板需求证据，不能虚构移植任务。
- **待判定**：证据不足，未当作已完成，也未武断当成必须移植的活动功能。

下列数量只是清单分类，不是完成率；一项不对应一个函数，也不能把不同硬件条件加总为“必移植功能数”。

已有 15 项、部分 30 项、未提供 15 项、缺失 11 项、不列为缺口 5 项、条件缺口 17 项、待判定 11 项、Linux接管 8 项。

## 启动与固件

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|A01|芯片/CV/ADIE、MAC与flash/OTP输入|已有|已有板级识别、校准输入和独立无线MAC；不把原厂全部私有输入接口视作必须兼容的ABI。|
|A02|MAC/BB/RF/FEM参数、功率表导入|已有|已有本机表及RFE54校准数据；仍需逐配置核对，不等于全部运行期策略完成。|
|A03|WCPU固件装载及CPU/内存启动|已有|已有AP固件、DLE/HFC与命令启动；NIC/WoW用途不由AP镜像完成度代替。|
|A04|PCIe DMA、HFC、TX/RX/RPQ环|已有|已有原厂格式和当前PE分工；已有定点修复不代表所有硬件情形均实测。|
|A05|FW能力/公共命令及C2H全分派|部分|只处理命令ACK、beacon统计、RA反馈/TX统计及既有RX报告；原厂TWT、MCC、扫描、CSA、CSI、WPS等事件未建立完整分派。|
|A06|固件日志、状态转储与遥测|未提供|有命令和拒绝报告摘要，未提供原厂FW日志配置、任务/堆/ISR等状态处理链；这是诊断能力差异。|
|A07|SER事件、L0/L1/L2恢复与状态重建|缺失|原厂注册ser_ops并处理事件；项目没有对应恢复状态机、固件/环及站点密钥恢复闭环。|
|A08|旧sreset与PCIe统计watchdog|不列为缺口|原厂sreset_reset直接return，phl_tx_watchdog_pcie主要打印/转储统计；不能把这两者算成漏移植的自动恢复。真实SER另列A07。|
|A09|系统suspend/resume|缺失|原厂PCI驱动注册挂起/恢复并保存/重建状态；项目pci_driver无PM入口。|
|A10|ASPM/LTR与动态PCIe电源策略|条件缺口|未提供原厂可选动态策略；原厂模块ASPM初值为0，不能说正常默认路径必然少了一次动态调节。|
|A11|IPS/LPS/低功耗无线角色|未提供|没有完整设备低功耗生命周期；AP端客户端PS是另一项，不能因有PS队列就宣称IPS/LPS完成。|
|A12|WoW、GTK/ARP/ND/NLO代理唤醒|待判定|项目未提供；原厂有下级实现，但公开wowlan_stub只含ANY标志，扩展能力的本机最终配置尚未确认。|

## 模式与信道

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|B01|单AP、monitor与基本接口生命周期|已有|单AP+monitor已有；不能把一个可运行AP当成所有接口类型已移植。|
|B02|多AP/VAP与独立BSSID/port资源|缺失|每颗只有一个ap_vif，第二AP返回EBUSY；原厂有虚拟接口和多接口组合，模块virtual_iface_num初值8。|
|B03|STA客户端、连接、重连与STA漫游|未提供|原厂wiphy声明STA且注册连接/断开；项目只声明AP/monitor，没有STA固件/角色完整链。|
|B04|IBSS/ad-hoc|未提供|原厂公开声明并注册，项目未声明或实现。|
|B05|P2P、Mesh、NAN前端模式|待判定|项目未提供。原厂有相关底层/历史符号，但公开wiphy模式掩码0x0e未声明这些模式；不能据此声称原机具备完整功能而漏移植。|
|B06|A4/WDS、回程与四地址快路|部分|原厂公开设置A4能力且有地址学习；项目加速模板主要为三地址，未建立完整A4回程/学习生命周期。Linux四地址能力及慢路需独立核实。|
|B07|MCC、同芯片多信道并发|条件缺口|只有单信道模拟chanctx；原厂底层存在MCC。本机公开角色组合和实际启用条件不能由底层表单独确定。|
|B08|DBCC与双PHY资源切换|待判定|项目每芯片按SCC/PHY0实现；原厂有DBCC测试/控制符号，本板是否需要启用尚无依据。双独立无线芯片不等于DBCC。|
|B09|软件扫描和扫描驻留/回家策略|部分|依赖mac80211软件扫描；没有对应原厂细分scan_backop/离家预算策略。本机AP离信道扫描的完整行为未实测。|
|B10|固件扫描/快速换信道offload|条件缺口|未接原厂FW扫描与换信道完成事件；软件扫描不是功能完全缺失，但硬件offload尚未提供。|
|B11|ACS/DACS、survey忙时/噪声报告|缺失|原厂真实读取ACS信道表、CLM和noise；项目无get_survey及运行环境结果链，不能宣称自动选频已完整可用。|
|B12|CSA/eCSA公告、beacon倒数及完成|部分|有普通换信道和beacon替换；没有LinuxCSA倒数更新/完成通知及原厂对应事件闭环，不能等同于无缝CSA。|
|B13|2.4GHz 40MHz|未提供|明确拒绝XB非20MHz，未声明40MHz；原厂保留该带宽配置及能力构造路径。|
|B14|5GHz 20/40/80/160MHz|已有|已接带宽、中心信道、校准、功率与HE/VHT声明；不是本次新发现的漏项。|
|B15|80+80、6GHz、4SS、EHT/320MHz|不列为缺口|项目未提供；尚无本板应提供这些能力的原厂证据，通用SDK的4SS参数不能当作本机要求。|
|B16|国家域、DFS/CAC、非占用期与功率|部分|已有regdb、雷达/CAC/TX门控和功率路径；原厂DFS动态参数、自动选频/CSA联动及各国家配置的完整覆盖仍不足。|

## MAC与协议控制

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|C01|beacon、DTIM/TIM、组播缓存|已有|已有Linux模板及固件beacon、TIM/DTIM基本路径；多port、CSA等另列。|
|C02|ERP/HT保护、短前导码、RTS/CTS|部分|缺ERP_CTS/HT保护/短前导码更新与逐包RTS/CTS标志消费、RTS阈值接口；启动硬件RTS、fallback与动态SMPS位已存在。|
|C03|软件发送控制帧|部分|TX入口只接management/data；软件请求的BAR等control帧没有完整提交路径。硬件自动ACK/BA不等于软件控制帧已接入；原厂与Linux分工不同。|
|C04|管理帧TX/RX、认证/关联与探测|Linux接管|基础管理帧传输已有，协议状态由Linux/wpad处理；不重复移植原厂MLME状态机。|
|C05|RX过滤、monitor过滤需求|部分|接受部分FIF请求，但写回主要按AP/monitor两种mode，未逐请求落实全部过滤语义。|
|C06|每帧RSSI/链信号、TSF、AMPDU及HE/RU元数据|部分|RX仅填基础rate/BW/GI且设置NO_SIGNAL_VAL；PPDU只用于采样RSSI，未完整关联每帧元数据、聚合引用或HE/RU报告。|
|C07|客户端PS、PS-Poll、U-APSD/EOSP|已有|已有睡眠通知、缓存释放、TIM/EOSP路径；未声称全部交错已实机回归。|
|C08|客户端强制唤醒/省电策略控制|条件缺口|没有原厂force_sta_wake等专门控制；现有正常PS机制不能代替这个可选策略，也不应未经条件确认强开。|
|C09|AP隔离、ACL、隐藏SSID、广播转单播|Linux接管|基本策略由hostapd/Linux提供；NI/FE绕行时是否保留隔离/VLAN/ACL须作为加速规则单独审计。|
|C10|TSF读写/同步与多port定时API|部分|port0定时初始化已有；无通用get/set/reset_tsf或多port同步接口，不能宣称原厂全部时钟控制已移植。|

## 无线能力

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|D01|HT/VHT/HE基础MCS、NSS、GI与PPE|部分|基础2SS及HE160/PPE已有；原厂条件化能力集合更大，不能笼统写为Wi-Fi6所有功能已完成。|
|D02|HT/VHT LDPC|未提供|当前HT/VHT声明未含对应RX LDPC能力；HE LDPC有实现，不能将HE覆盖扩展到所有模式。|
|D03|STBC与DCM/ER/其他HE条件能力|条件缺口|未提供完整对应能力和参数贯通。原厂HE构造明确不宣告RX STBC，不能把全部STBC都算成原厂开启而项目关闭；其他位取决于本机最终proto cap。|
|D04|HE OMI/A-Control变化|未提供|原厂解析OM并暂停/释放/更新/恢复站点；项目未声明OMI，也无该控制链。HTC_HE声明不等于OMI。|
|D05|VHT Operating Mode、SMPS动态能力更新|Linux接管|Linux处理VHT OM/HT SMPS并回调BW/NSS/SMPS更新；原生已接link_sta_rc_update，不能误报为完全没有动态能力处理。|
|D06|SU beamformer/beamformee、CSI、sounding|未提供|原厂关联按双方能力配置BF与CSI；项目无对应声明、角色/sounding/报告链。原机实际默认开关仍需区分。|
|D07|MU-MIMO分组、CSI与调度|未提供|txbf_mu只参与固件调度选择，缺完整能力、分组和sounding控制，不能据参数名宣称已支持。|
|D08|DL/UL OFDMA、RU分配、触发/BSR|未提供|ofdma参数和部分BSR格式存在，缺能力协商、RU/STA组控制和完整触发调度/状态处理。|
|D09|MU-EDCA与HE调度参数一致性|部分|Linux可生成beacon IE；项目未建立对应硬件MU调度链。不能仅因无HE_MUEDCA handler就断言AP的IE完全不存在。|
|D10|TWT responder/requester及会话资源|未提供|原厂AP setup有能力校验、配置分配、响应、UL参数、STA绑定；项目无TWT声明、会话/命令/C2H与PS联动。|
|D11|Spatial reuse/OBSS-PD、two-NAV动态更新|条件缺口|初始化有静态two-NAV/SR设置；无完整动态配置链。原厂启动清SR_EN，不把默认关闭直接算故障。|

## BB运行管理

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|E01|公共连接/流量/RSSI状态与周期账本|缺失|原厂按连接变化、站点流量、RSSI等更新；项目没有等价完整公共状态循环。|
|E02|完整PHY status消费及周期统计|部分|主要取RSSI；未完整消费原厂PHY IE、统计、CFO/噪声/分集等算法样本。|
|E03|PMAC计数/CRC/CCA统计采样与重置|部分|有统计选择器初始化及软件计数，缺原厂周期读取/计算/重置；这些数据也是ACS/DM依赖。|
|E04|DIG增益、FAHM、动态PD门限|缺失|仅初始化与C带宽重编码；两颗原厂能力bit11均开，缺连接变化、FA样本/阻尼、增益计算和写回。|
|E05|NHM/CLM/IFS/FAHM环境测量闭环|缺失|只有初始化；缺占用仲裁、读取结果、重新触发及周期状态。不能与DIG各加独立定时器争用FAHM。|
|E06|EDCCA/碰撞门限运行策略|条件缺口|原厂bit4/mode控制且normal mode有专门分支；项目仅初值，缺完整运行处理。不能把所有mode视作同一算法。|
|E07|CFO普通/加速跟踪、数字补偿|条件缺口|只初始化；共享晶振关闭tracking bit6，独立晶振条件下缺样本、控制和写回。原机is_indiv_xtal最终值未知。|
|E08|按站点动态功率DTP|条件缺口|有静态功率/初值，无原厂每STA周期控制；原厂能力bit7及平台0x109例外必须保留条件。|
|E09|Path diversity、每STA发射路径选择|条件缺口|仅固定TRX初始化；原厂C能力bit12及路径模式、站点样本等门控，XB默认掩码不含bit12。|
|E10|Antenna diversity、EVM/RSSI训练|条件缺口|有开关影响supportability，未实现训练/样本/切换运行链；RFE54还要求原厂配置启用，不能默认强开。|
|E11|C芯片周期硬件设置/BTG策略|条件缺口|原厂会按band/链接/RSSI改4aa4；项目只启动写表，没有等价周期更新。本机运行条件须核实。|
|E12|DCR/动态CSI响应与环境检查|条件缺口|有初始化，无对应环境检测；原厂env_det仅平台0x409等条件执行。dyn_csi_rsp_main直接调用根未确立，不能把全部相关符号算活动链。|
|E13|RA主机周期mask/噪声/GI-LTF策略|缺失|原厂按RSSI、NHM/noisy、NSS等重算RAMASK并发H2C；项目周期主要发RSSI，能力变化才重注册，不是完整主机RA watchdog。|
|E14|固件RA注册、反馈和基础RSSI上报|已有|基础固件速率控制已存在；不能因缺主机watchdog而写成完全没有RA。|

## RF与校准

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|F01|RCK/DACK、IQK/GAPK、RXDCK启动/换信道|已有|本机路径和已有原厂证据均有；不把周期任务缺失反推启动校准也没移植。|
|F02|硬件TSSI跟踪、热表、DE与功率参考|已有|已有硬件自动跟踪和flash/table输入；本版原厂主机tssi_tracking仅做watchdog状态切换，没有可照搬的调节主体。|
|F03|RF温度采样与周期状态|缺失|无原厂等价周期thermal A/B及下级dispatch账本；不能据此把每个下级都列为有实际算法。|
|F04|C温漂触发RXDCK|条件缺口|原厂支持位、busy、初始化/模式门控后比较温度差约12并触发；项目有换信道RXDCK但无温漂闭环。|
|F05|C周期IQK tracking|不列为缺口|原厂下级函数为直接return；不能仅凭上层dispatch把周期IQK算法算成漏移植。|
|F06|LCK温漂跟踪|条件缺口|原厂两颗LCK下级确有温漂比较：首次参考温度为空或最大差值达到8时触发LCK；项目缺对应周期链。最终enable/busy门控仍须沿原厂保留。|
|F07|XB OP5K温漂跟踪|条件缺口|缺周期采样和触发/恢复；原厂CV非CAV、set-channel检查、能力bit20等条件后处理，不能无条件启用。|
|F08|RFE54 DPK|不列为缺口|本板原厂按RFE54走bypass；不为补齐符号强开DPK。|
|F09|RFK开始/结束、TX暂停及恢复协同|部分|启动/换信道已有协同；新增运行期RFK还没有完整状态机，需与固件/PE/站点生命周期一起设计。|
|F10|工厂写OTP/efuse、晶振/PHY配置工具|未提供|已有只读输入和正常校准；未提供工厂烧录/MP私有控制。属于维护能力，不应在正常启动写OTP补齐。|

## 安全与数据通路

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|G01|CCMP-128硬件key/PN/CAM及密钥生命周期|已有|已有实现及本地修复；未宣称所有重键交错已实机验收。|
|G02|其余cipher硬件卸载|部分|硬件只接CCMP；原厂公开11种cipher不等于每种均硬件执行，原厂实际硬件集合尚需逐cipher核对。Linux软件处理另列。|
|G03|WPA2/WPA3/SAE、OWE、802.1X、PMF|Linux接管|full wpad及Linux提供协议/所列软件cipher；不把非CCMP硬件未接当成安全协议整体缺失，也不替代端到端验收。|
|G04|802.11r/k/v、PMKSA、WNM/MBO/RRM|Linux接管|AP侧通用软件原语由Linux/wpad提供；STA角色未提供，survey/每帧信号限制与完整steering策略尚未解决。|
|G05|数据TX/RX、A-MPDU/A-MSDU与BA|部分|已有基础聚合、BA、PN和所有权机制；RX硬件BA资源、原厂动态聚合/低速聚合策略未完整覆盖。|
|G06|软件重排、BAR、重组/碎片处理|Linux接管|Linux处理完整异常路径，PE维持快路状态；快路例外不等于这些协议功能全部没实现，软件控制帧TX缺口另列。|
|G07|WMM/EDCA基本四队列配置|已有|已有conf_tx及slot更新；动态资源再分配与固定EDCA是不同功能。|
|G08|动态EDCA与按站点A-MSDU策略|缺失|原厂iface周期调用站点/流量动态控制，按流量方向、速率等选择A-MSDU参数并改EDCA；项目只有conf_tx与现有聚合，没有等价闭环。数值策略须由实际指令核对，不能复制Ghidra误识别为字符串的参数。|
|G09|手动速率mask、GI/NSS/路径与legacy限制|部分|关联能力/SMPS会更新；未提供完整用户掩码到固件RA和PE模板的控制链。固定TRX/硬件RA不能代表原厂所有手动策略。|
|G10|重试/lifetime/队列limit/优先级控制|部分|有固定CCTL/队列参数；原厂可调策略未逐项接入。默认配置是否等效需核对，不能从MIB初值直接推断CCTL最终值。|
|G11|Airtime公平、时分模式和统计反馈|部分|有Linux队列调度，但无完整PPDU airtime反馈和原厂tx_mode控制链；不能将软件公平排队等同全部硬件时分策略。|
|G12|无线带宽限额、DSCP与租户QoS策略|Linux接管|通用策略可由Linux提供；没有移植运营商专用MIB/DSCP和整套Mesh租户控制，实际配置及加速规则等效性待核对。|
|G13|PFD timer/TX report与延迟发送策略|待判定|原厂setter确实写公共控制字段；项目无同名完整功能，实际PFD消费者/固件行为与defer策略仍未核实，不能仅据缩写命名算法。|
|G14|TCP ACK抑制、kick ICV与私有优化|待判定|原厂MIB有tcp_ack_drop_dup/kick_icverr等，项目未提供原厂策略接口；未证实本机启用及语义，不移植可能改变报文语义的优化来冒充功能补齐。|

## 硬件加速

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|H01|PE下行桥接/转发、聚合与完成回收|部分|已有多peer/TID队列、BID/WP回收；准入依赖TID0 BA和一个基准模板，未完成每TID/每角色独立准入配置。|
|H02|PE上行快路及异常交接|部分|仅已授权CCMP、顺序SN/PN及特定三地址TCP/UDP格式快路；异常交Linux，未覆盖全部合法报文的硬件转发。|
|H03|802.11/Ethernet头转换与HW sequence|已有|TX已用硬件封装，RX使用PE/软件处理；实现分工不同，不因缺同名原厂函数就判功能缺失。|
|H04|MAC硬件A-MSDU搜索/切分与聚合offload|条件缺口|当前由PE聚合、完整路径拆分，未接全部MAC硬件HWAMSDU控制。本机原厂PE WFO关闭，不代表MAC HWAMSDU一概启用或关闭。|
|H05|TSO/GSO、checksum partial与分段快路|部分|已有部分checksum/SG；NI入口将GSO、CHECKSUM_PARTIAL保留Linux，不是全量硬件分段/校验卸载。|
|H06|组播/广播、复制与mc2u硬件快路|部分|目前主要走Linux，未提供完整原厂组播策略的硬件路径；不能据此认定组播完全不可用。|
|H07|VLAN、PPPoE、IPv6及协议例外覆盖|部分|部分普通TCP/UDP、VLAN/PPPoE路径已有；上行PE只接直接TCP/UDP及IPv4无options等格式，扩展头/其他类型仍需Linux。|
|H08|AP隔离、ACL、VLAN/flow规则一致性|待判定|必须沿规则安装与命中检查，不能用慢路策略存在证明快路已保留全部语义。本轮不改动共享NI/PON工作区。|
|H09|PS、密钥、BA、速率变化跨ARM/PE同步|部分|部分同步及既有修复已有；新增OMI/BF/TWT/VAP等没有对应模板/资源状态传播，不能只补一端。|
|H10|WFO/VIRT/其他板型PE镜像|不列为缺口|本机原厂明确关闭RTK_WFO、WFO_VIRT和FC_WIFI_AMSDU_OFFLOAD_BY_PE；不能将别板镜像算作本板必移植功能。|

## 板级管理与诊断

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|I01|UCI/netifd/wpad/LuCI基本无线管理|已有|已选full wpad、wifi-scripts、iw、regdb与LuCI；无需兼容原厂私有ioctl才算有管理功能。|
|I02|WPS协议/软件PBC|Linux接管|WPS软件组件已编译；硬件按钮/LED事件尚未接入，不能把二者混为一项。此seed是模块参数，不以函数匹配作启用证明。|
|I03|WPS按钮、无线开关、LED/rfkill板级接入|缺失|未找到gpio-keys/gpio-leds/WPS/rfkill绑定和事件映射；原厂有WPS/LED事件入口，具体板级引脚仍需按原厂DTB/硬件核对。|
|I04|Band steering、EasyMesh控制与回程策略|未提供|原厂DBus BandSteering对象确实注册且有RSSI/信道占用/STA负载阈值接口；项目有通用软件原语，但未提供整套策略管理程序。不能把对象注册等同于算法在本机实际启用。|
|I05|最低RSSI准入、未关联站点测量|部分|原厂MIB有assoc RSSI/未关联测量策略；当前每帧NO_SIGNAL_VAL，不能宣称hostapd准入/测量已获得等价信号输入。|
|I06|OMCI/TR-069/DBus无线管理桥接|待判定|原厂DBus无线对象确有注册。Pptp80211Uni模块也注册数据表，但本轮驱动配置handler仅处理ARC定时，并未发现直接配置无线；不能仅按库名声称OLT原厂能完整控制Wi-Fi。项目缺该管理桥/模型，需继续区分Linux功能与运营商接口。|
|I07|MP/PMAC/PLCP/环回与认证测试模式|未提供|正常AP和现有诊断计数不能代替原厂厂测模式；若后续需要，须按本机实际测试实现重新设计。|
|I08|LA/PSD、CSI sensing和采样导出|未提供|有部分LA/PSD初始化；没有完整触发/报告/导出与sensing控制接口。初始化标记不代表工具功能已提供。|
|I09|私有WEXT/proc/MIB/ethtool控制面|部分|保留了部分新诊断接口与Linux通用控制；原厂私有接口不要求原样兼容，但其背后的有效功能须逐项映射，完整原始目录随本报告保留。|
|I10|BT共存、USB专用选项及其他芯片通用项|待判定|C的BTG运行寄存器策略另列；本板没有据此确立完整BT功能需求，USB切换/其他芯片选项不能直接记为PCIe本板漏移植。|

## 已排除的误报

- 原厂DIG检查bit11，两颗默认均开启；不是依据bit0判断。共享晶振只清CFO bit6。
- 原厂C的`halrf_iqk_track_8852c`直接返回；主机`halrf_tssi_tracking`也没有实质调节主体。它们不能列成两套漏移植算法。
- RFE54的DPK bypass、本机关闭的WFO/VIRT/PE A-MSDU不能用别板实现补齐。
- P2P/Mesh/NAN在低层出现，不等于原厂公开提供完整工作模式。实际wiphy模式为0x0e，即IBSS/STA/AP。
- 完整wpad的有效功能以OpenWrt Makefile覆盖参数和构建配置为准；不能因hostapd模板`.config`中AX/ACS被注释就判未编译。当前DRIVER_11AX/11AC、WPA_MBO与full-mbedtls配置均存在。
- Linux/wpad提供安全、MLME、软件重排等职责；缺原厂同名实现不等于缺功能。反之，声明能力/填初始化值/存在开关也不等于完整实现。

## 逐项追踪的约束

后续实现以本清单和原厂具体分支为依据，同时更新能力声明、上层控制、固件命令/完成、硬件状态、ARM/PE同步和start/stop/换信道生命周期。不得只增加功能开关或标记“applied”来替代真实控制闭环。

缺口的处理顺序取决于依赖关系，不再按高速丢包排序：先恢复公共状态/采样和硬件管理底座，再贯通协议能力、角色/资源与固件事件，之后完善加速对这些新状态的支持；管理/诊断和板级接入也保留在完整范围内。未确认的原厂门控须继续从制品追溯，不能猜值强开。

本报告没有声称已识别任意未来配置下的所有问题，也没有把待判定项隐藏为“已完成”。冻结原厂模块之外的运行期flash最终配置、外部无线管理二进制完整语义及未解析的间接调用仍是明确边界。已有材料允许继续研究的部分应继续核实；确需硬件观察的条件保持待确认。

本轮只提交审计目录、证据、定点工具和文档；未改动运行行为，未运行全量测试或固件编译。技术细节见[BB/RF与加速初步对照](wifi-factory-coverage-20261016.md)。

## 原厂39个cfg80211入口逐项映射

这些是功能职责映射，不是比较两个不同内核版本回调数量来计算完成率。通用Linux入口存在，也必须检查驱动所需的底层能力。

|原厂入口|清单项|
|---|---|
|`cfg80211_rtw_add_virtual_intf`|B01、B02、B03、B04|
|`cfg80211_rtw_del_virtual_intf`|B01、B02|
|`cfg80211_rtw_change_iface`|B01、B03、B04|
|`cfg80211_rtw_add_key`|G01、G02、G03|
|`cfg80211_rtw_get_key`|G01、G03|
|`cfg80211_rtw_del_key`|G01、G03|
|`cfg80211_rtw_set_default_key`|G01、G03|
|`cfg80211_rtw_set_default_mgmt_key`|G03|
|`cfg80211_rtw_start_ap`|B01、C01|
|`cfg80211_rtw_change_beacon`|C01、B12|
|`cfg80211_rtw_stop_ap`|B01、C01|
|`cfg80211_rtw_add_station`|C04、G05|
|`cfg80211_rtw_del_station`|C04、G01、G05|
|`cfg80211_rtw_change_station`|C04、G01、G05、D05|
|`cfg80211_rtw_get_station`|C06、E14|
|`cfg80211_rtw_dump_station`|C06、E14|
|`cfg80211_rtw_change_bss`|C09|
|`cfg80211_rtw_set_txq_params`|G07|
|`cfg80211_rtw_set_monitor_channel`|B01、B13、B14|
|`cfg80211_rtw_scan`|B09、B10、B11|
|`cfg80211_rtw_connect`|B03|
|`cfg80211_rtw_disconnect`|B03|
|`cfg80211_rtw_join_ibss`|B04|
|`cfg80211_rtw_leave_ibss`|B04|
|`cfg80211_rtw_set_wiphy_params`|C02、G06、G10|
|`cfg80211_rtw_set_txpower`|B16、F02|
|`cfg80211_rtw_get_txpower`|B16、F02|
|`cfg80211_rtw_dump_survey`|B11|
|`cfg80211_rtw_set_pmksa`|G03、G04|
|`cfg80211_rtw_del_pmksa`|G03、G04|
|`cfg80211_rtw_flush_pmksa`|G03、G04|
|`cfg80211_rtw_mgmt_tx`|C04、C03|
|`cfg80211_rtw_set_power_mgmt`|A11、B03|
|`cfg80211_rtw_update_mgmt_frame_registrations`|C04、C05|
|`cfg80211_rtw_probe_client`|C04|
|`cfg80211_rtw_get_channel`|B13、B14|
|`cfg80211_rtw_set_mac_acl`|C09、H08|
|`cfg80211_rtw_update_ft_ies`|G04、B03|
|`cfg80211_rtw_external_auth`|G03、B03|

170个旧MIB字段全部保留在[控制项映射](../artifacts/wifi-coverage-audit-20261016/factory-control-map.json)，每项都有功能域归属。这个映射不冒充每个消费者均已核实；`consumer_fully_verified=false`明确保留该边界。121个模块参数、70个回调对象及全部符号见原始目录。

[当前源码接口与散列](../artifacts/wifi-coverage-audit-20261016/native-surface.json)固定了本轮检查的驱动版本。共享NI/PON工作区未纳入提交。

## 扩展核对的管理与控制项目

|编号|功能|状态|实际覆盖/遗漏及条件|
|---|---|---|---|
|C11|RRSR/basic response rate动态控制|待判定|原厂有可调/动态RRSR控制；项目beacon基本速率已有，但缺BSS基本速率到完整响应速率策略的可核实映射，不能凭固定初始化掩码认定全覆盖。|
|G15|动态HFC/WMM资源再分配|待判定|项目主要静态HFC资源；原厂wmm_dm控制与动态EDCA不应混为一项。资源再分配的实际消费者/本机门控尚未确认，不以名字推断一定存在遗漏算法。|
|I11|按周期/星期/SSID掩码的无线定时开关|待判定|原厂有两套时间/星期/SSIDMask配置接口；项目未提供相应管理。已反编译的wifitimer_time只解析HH:MM，定时执行消费者尚未确立，不能宣称原机活动调度算法已完整验证。|
|I12|信道质量采集、评分、背景噪声与遥测|部分|原厂管理接口含ChannelCollection/评分/扫描完成时刻/背景噪声与质量采集；项目有部分统计，缺运行survey、评分采集和相应管理流程。|
|I13|同SSID协调、设备踢除及带宽策略管理|部分|Linux可配置同SSID、踢站和通用QoS；未移植原厂SetSameSSID/CancelSameSSID及整套运营商协调流程，不能把协议原语缺失与管理流程缺失混为一项。|

原厂无线管理样本来自完整Linux解包归档（归档内驱动散列与冻结模块相同）。Mac早期`rootfs-partial-casefold`中的同名零字节占位文件未用作行为依据；本轮重新按精确成员名读取完整tar成员，并保存[管理输入与接口](../artifacts/wifi-coverage-audit-20261016/management-inputs.json)。

本轮REA保存75条成功函数记录：驱动68条，管理库/程序7条。另保留一次函数名同时命中PLT与函数体的失败查询；已用明确函数体地址修正，错误记录没有计入成功分析。
