# 原厂收发细节核对与历史改动去留

后续调查顺序：先从复现问题定位错误链条关键点，再核对该点与原厂的细节差异，对齐后验证。不能以全盘寻找非原厂实现代替故障定位。

本轮按功能正确性、资源开销、稳定性与原厂契约判断价值；吞吐是验收项之一，不能用“未提速”直接否定改动。保留native/mac80211/PE架构，核对硬件字段、线格式、时序和数据所有权。依据为板载rtk_wifi6.ko（SHA256 745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e）、ca-ne实际模块、对应Realtek参考源码和现有硬件记录。

## 已保留及重新纳入

| 细节 | 原厂/正确性依据 | 当前处置 |
|---|---|---|
| IRQ开启后NAPI完成、GRO生命周期 | ca_ni_rx_poll先complete再rearm；native teardown必须flush持有VIF的报文 | main21754e8保留 |
| NI读取PE改写包使用非snoop pattern0 | 原厂CPU写缓存与native PE写DDR所有权不同；实机pattern1出现旧MAC/LLC头 | maina318985保留；不机械复制CPU VP的pattern1 |
| QM预取、只读DMA缓冲复用、FE miss保留NAPI | 实际ca-ne prfm与NAPI回调及DMA所有权约束 | maina318985保留 |
| TXBD原始DDR地址、WD页独立于RPQ保留 | 实机CCI重复完成；原厂描述符与payload资源独立 | main57db103/4880ee3保留 |
| 固件RA、软件A-MSDU、EPP完整MPDU准入 | 原厂普通数据固件选速；native整组入队前检查避免半修改 | main4880ee3保留 |
| RX BA会话与静态CAM分离、RX BAR缓存同步 | 原厂软件reorder会话独立、BAR前移/回绕时撤销缓存 | maind939663及现代码保留 |
| 动态SMPS RTS | 原厂WD字节48 bits27/31；STBC不是RTS | main7f51ffa保留 |
| 短ACK聚合 | 原厂TXSC队列和超时；保留全部ACK | main6df8484保留 |
| 大包重试制式下限 | 原厂txsc_apply_sc_cached_entry pktlen>2300；C/XB WD字节36 bits24:16 | main9c0b169保留 |
| wake撤销旧PS授权 | native异步路径必须保持原厂stop/wakeup语义 | main9c0b169保留，ARM/PE交错回归先失败后通过 |
| RX BA256 | 原厂_hal_update_ba_cam window>64编码bmap_size4；C/XB均匹配 | 重新纳入，Mac实测64、手机256；并非提速门槛 |
| TX合法20字节BAR | 原厂encoder header16/packet20合法；native此前拒帧且可能读seq_ctrl越界 | 拆出BAR支持保留；不混入AMPDU_NO_BACK推断 |
| 非AMPDU的BK | 原厂普通数据非AMPDU必设BK；native两encoder遗漏 | ARM/PE均补齐；原厂8组、连续同peer/TID/长度448组通过 |
| PS队列包龄 | 原厂TSF寄存器c438/c43c；native/mac80211期限必须从入队计时 | 批次读完整TSF，复用BID头部；删除两秒旧时钟 |

## 有价值但不等同原厂默认的候选

- cached EPP已重新纳入：原厂实际EPP普通cached/ACE访问；native用DMA_FROM_DEVICE映射及显式invalidate实现对应缓存访问。生产仅+23/-4行；17024个poisoned-cache场景、stop/open、11处失败清理与DMA配对检查通过。历史实机64MiB双向SHA、长跑无损坏记录；缺少提速不构成撤销依据。原厂实际ACE配置免除了sync，不能声称逐项invalidate也是其默认。
- page_pool V3：CPU开销曾下降，2K fragment避免V1的4K truesize代价；本机原厂CA_NI_USE_BUILD_SKB=0，不能称恢复原厂page_pool。保留独立候选，生产化需移除qm_copy开关/旧1MB sentinel并验证stop/open和远核释放。
- 软件SG表17、host额度224：原厂大A-MSDU/共享TXBD检查提供方向，但17、224不是原厂常数。与提高公布fragment上限导致更多线性化/无线重试的负面结果分开评估。
- TX BA128已完整纳入：C/XB全局count128/time160来自实际hal_trx_init调用；CMAC及PE按各peer独立限制64/128。128→64和64→128混合peer回归通过。原厂专用配置API的165不能冒充初始化默认，已纠正。Mac协商64不影响保留128能力的价值。

## 不直接合入的历史实验

RMU no-buffer抑制、强制TXPause、WIFI_CTRL/SSID_MAP、临时EQ/中断阈值、超频、强制速率、WP192，以及全部payload改DDR等，没有本机原厂默认依据或已有明确卡住/性能/稳定性负面。保留记录，默认恢复。RPQ原厂POH为121/255，native现12/255是回收策略；纠正“124BID”旧注释，不能机械改回121。

size-decrease BK未在原厂找到；原厂是非AMPDU和显式批次边界。过去直接去掉有提速记录也有崩跌记录；已补漏掉的非AMPDU BK。原厂tasklet按macid%73选择软件队列，额度边界标记当前skb，TXSC将任一子包标记传播到当前txreq的BK；它不是普遍队尾标记，也不检测前包长度或peer/TID变化。暂保留有下载崩跌记录的size workaround，明确它不是原厂等价实现。依据：core_tx_swq_xmit_tasklet@173d4、txsc_apply_sc_cached_entry@ee8b8/eee34。

## 新发现的控制细节差异

1. 原厂halbb_ra_watchdog先RSSI上报，再调用halbb_ra_update_mask_watchdog；native此前仅在STA能力变化时更新RA。原厂按总体RSSI等级、3点滞回筛低速率，并设置UpdateMask；本轮helper用18928组完整16字节C/XB/HT/VHT/HE/legacy/BW/RSSI/历史等级对照通过。
2. 原厂RSSI上报取两链数据RSSI移动平均，native此前发送最近一次PPDU值。字节1/7链选择正确，时间统计语义不一致；本机producer已确认：各状态零值独立初始化，U8.1样本保存在U16.4累加器；总体加8舍入，两链H2C直接截断。周期样本数决定1/2/3/4因子。资格还必须满足SU且data/beacon，不能把任意每用户报告都当作RA样本。生产接入完成；4096组MA、32组因子边界及11520组完整PHY解析资格与同一原厂ELF一致。现有20ms采样窗保留，因子按窗内合格PPDU计数，未宣称等同原厂全量接收。
3. PS入队时间不能用每2秒更新的旧秒时钟；存在提前过期。已改为每次最多16个EPP的一批取一次完整64位TSF，批内误差限于该次无等待处理时间；睡眠选择同次复用一次TSF，不跨调用缓存。最大listen/beacon期限约101.804天、32/64位回绕和撕裂读取均通过实际PE回归。

验收仍未完成；本文件记录代码价值及原厂一致性，不将任一候选自动视为达到TCP/UDP吞吐目标。

## 合并验证进展

当前合并PE为24012字节，SHA256 18bb92fe08d19573b5a95bf214e80dc16cba389aae03477707e73c6d59fb97fe。实际链接代码的13项相关回归通过，包括六通道发布、共享ARM/NI容量、完整A-MSDU、RPQ回收、BAR、短包等待、PS期限与授权；结果见artifacts/stock-details/test-results.json。ARM64模块编译通过，RSSI整条PHY解析原厂对照通过。合并模块rtl_ax_ap_stockdetail已在RAM加载，MAC关联与64窗口协商通过，c610实读A03F3F7F匹配原厂count128/time160；吞吐与运行回归进行中。

### 本次合并实机结果

RAM模块SHA256 d659b5c8a96ebc477c472ae4f7f1e88d69f7ebf40e2c6cf85afbd316cdfc2050；PE SHA256 18bb92fe08d19573b5a95bf214e80dc16cba389aae03477707e73c6d59fb97fe。默认单连接10秒TCP（只指定接口/JSON/方向）：

| 方向 | Gbps | TCP重传 |
|---|---|---|
| 上行 | 1.648 / 1.575 / 1.690 | 0 / 42 / 0 |
| 下行 | 1.445 / 1.456 / 1.504 | Windows全局计数差329 / 173 / 183 |
| UDP上行，发送1.85G | 1.822 | 丢包0.0403% |
| UDP下行，发送1.85G | 1.767 | 丢包4.3498% |

RSSI平均值有有效样本，两peer周期mask更新持续，update_error=0；Mac协商TX/RX64，手机RX256。未达到完整验收，不能把一次下行1.504当作稳定达标。当前radio修复未显示明确新异常，保留；cached EPP已重编Image，本次未刷写，以历史实机和本次生命周期回归作为当前保留依据。

下一调查点限定为下行排空：已有证据BID池耗尽→NI no_buffer丢包；需判定硬件发送、聚合供给和RPQ回收哪个关键环节限制服务速率，再对该环节核对原厂。暂停新增不在这条故障链上的细节候选。

### 按下行故障链定位的新增证据

临时诊断已能查询当前C U2原厂30个AGG break桶。实际ARM菜单、H2C编码、U2配置和日志编码、原厂ARM解码均经离线执行核对；实机按同MACID在测速前后取差，测速中关闭日志。

TCP1.428Gbps：桶3(0x2000)6508、桶18(0x7000)2821；UDP1.797Gbps：桶3为1572、桶18为6569。原厂长度桶6/7、时间桶8/9均为0，暂不支持调大这两个上限。

临时仅关闭size-decrease BK，桶3从ON的3486降至47，恢复后为4629，证明此WD条件会导致大量聚合中断。OFF期间TCP发生停顿，但sleep/wake事件从对照的10–19次增至210次，Windows控制连接同时中断，不能据此把停顿单独归因为混合包长硬件错误。保留既有规避，后续聚焦该条件与PS/发送连续性的交互。诊断已撤掉，RAM恢复rtl_ax_ap_stockdetail，关联与收发正常。

这轮没有新增生产行为改动。证据位于Mac artifacts/wifi-wired-peer-20261104/AGGREASON-*、AGGCOUNT-*、AGGBK-*；离线命令与解码脚本位于工作树artifacts/agg-break-u2和agg-break-fw-log。

### 长时间去除size BK后的故障定位

按MACID记录PS后，一次ON/OFF/ON默认TCP下行得到1.478/1.542/1.471Gbps，Mac在传输中唤醒，频繁PM来自手机；bucket3为5137/12/3402。但进一步三对TCP与UDP验证复现严重停顿：第三次TCP下行5.76Mbps，UDP下行101Mbps而Windows实际发送1.849Gbps。去除size BK候选已撤回，未提交。

30秒UDP OFF采样复现两个发送冻结区间，pending124且没有RPQ。在多个连续样本中，Mac的PE状态为awake、硬件C2C0 sleep与9EB0 pause位也清零，排除“持续暂停未清除”这一解释。两个peer的RX trigger config均为0，无U-APSD；虽然原厂U-APSD host_getpkt通知确有遗漏，它不解释本次停顿。已有WP持有问题先于/独立于部分PS转换。

下一条证据链是原厂软件批次结束BK的默认开关和额度边界；不能未验证就以任意batch8/64替代。正常RAM模块已恢复，诊断读取模块已移除。

### CMAC实读与WDE队列定位

原厂软件额度BK默认开关为0，没有固定64包必须BK的依据。实际HAL最终将CMAC max_agg_num_sel清零；native一致。执行原厂`__dump_mac_mem`确认CMAC读取窗口后，实机MACID1读回word2=`0x2000`、word3=`0x3e`，MACID2为`0x2000`/`0x17e`，分别对应当前64/128窗口。没有配置漏写证据。

临时按原厂WDE DFI查询group0，OFF停顿期间连续读到`ffffffef`，MACID1四AC的空位图为`e`。在发送/完成计数连续约14秒不变、124 owner持有且Mac sleep/pause清零的同时，至少一个MACID1硬件AC队列仍非空。因此下一步应定位队首及消费条件，不能再把问题只归于RPQ回报丢失。

三份实际已发布WD快照均能将124个held owner匹配到124个MACID1 MPDU；SN连续、各包WD长度等于SG长度之和、末项LS正确。两次独立停顿最早未完成两包均为10524字节A-MSDU紧接1510字节普通MPDU，BK均为0；后续另一个1510字节包已有context BK。期间Mac TX BA未重建。对快照各最早三包执行实际原厂TXSC floor逻辑和encoder，9/9前56字节完全一致；原厂7/1地址对应106/70字节TXBD实际长度，亦一致。这是故障边界证据，尚不是根因。

追加物理报文头ioremap诊断时设备重启，该次实验无有效header快照，诊断撤销。恢复已验证stockdetail模块、所有helper卸除后，默认TCP下行1.433Gbps，运行恢复但吞吐目标仍未达到。后续header读取只沿NI已经分配的CPU DMA映射；原厂队首查询限定GET_FIRST/GET_NEXT，不移动队列。

通过NI原有coherent CPU映射补齐报文头后，QMAC停顿的124包头序号均与WD一致，原厂GET_FIRST/GET_NEXT连续三次返回完全相同的124个Q0 packet ID，Q1–3为空。新边界10524→4524两包同为A-MSDU且retry floor相同，因此故障不要求A-MSDU到普通帧或floor切换。原厂SG encoder、HDR_LLC_LEN、preload配置进一步对照无字段差异。

QHEAD2进一步以shared-buffer `0x18700000 + PID*64`读回硬件队首：前两个PID为`4be/65c`，内容WP=`8133/8134`、SN=`3823/3824`与最早held两个WD相符，分别为10524/3024字节A-MSDU。硬件长度已各加8并插入PN。三次队首及124项链未变；下一步限定于硬件改写的WD状态与队首消费条件。该轮UDP诊断约1.104Gbps并长停顿，不计验收；结束恢复生产模块，未将去掉BK合入。

在已定位链路上，仅将NI首SG拆为原厂布局的独立WLAN头做对照（保持原DDR内容、其余字段和关闭size BK条件）。实际PE发布与单帧/2/3/7/8子帧长度、载荷、LS验证通过；实机UDP两轮0.508/0.119Gbps、默认TCP0.023Gbps及第二轮控制错误，Mac计数仍长时冻结。该布局差异单独不能解释或消除故障，临时改动撤回，未合入生产。原厂对齐以此条故障链上的验证价值判断，不为形式一致增加SG开销。

### 2026-11-05：关闭 BK 分支，恢复默认吞吐主线

上述 BK 关闭故障的“下一步”已全部终止。最后一次仅对齐 TID0 硬件序号及 BA 起点的对照仍冻结在 pending124，iperf 超时；按用户要求不再调查这条实验引出的停顿。该候选未合入。正常 RAM 模块恢复为 stockdetail（SHA256 d659b5c8a96ebc477c472ae4f7f1e88d69f7ebf40e2c6cf85afbd316cdfc2050），保留 BK，硬件调度、时钟及 EDCA 恢复默认，全部诊断模块移除。

恢复后默认10秒单流 TCP 上行1.527Gbps/0重传、下行1.464Gbps；UDP1.85G下行1.788Gbps/3.20%丢包。新一轮默认TCP下行1.439Gbps。大包聚合分别6.95/6.97子帧，接近7帧上限；不再增加大包聚合等待。原始验收仍未达成。

沿正常下行空口耗时核对两处原厂实现，均无差异：普通HE且无动态SMPS/ERP时RTS_EN/HW_RTS_EN均0（实际ELF未编入强制RTS分支）；响应参考速率54M、基本OFDM6/12/24M以及CC08/C090/C08C/CC04配置也一致。未据此改代码。

临时降低功率上限23→12dBm检验近距离重试假设：TCP1.497Gbps，UDP1.752Gbps/5.16%，默认UDP1.751Gbps/5.24%，恢复后TCP1.471Gbps。没有一致改善，恢复自动23dBm并结束该假设。

实际ELF还纠正了旧EDCA脚本的命名：005e4319来自8STA/BE+VI测试分支，不能称普通单BE原厂默认。真正对应HE160、TX占优、吞吐>1300、TID0 RX BA已启用且窗口≤64的动态档是01786615；APPLE不被前置vendor条件排除，TXSC入口数不参与此分支。仅对这一值做同关联A/B/A：

| BE配置 | TCP下行 Gbps | UDP下行 Gbps | UDP丢包 |
|---|---:|---:|---:|
| 原厂高吞吐档01786615 A | 1.368 | 1.718 | 7.04% |
| 当前默认003f642b | 1.441 | 1.761 | 4.71% |
| 原厂高吞吐档01786615 B | 1.372 | 1.803 | 2.44% |

TCP重复退步，UDP改善不一致，故该动态档不合入。结束确认C300恢复003f642b。本轮仅定位进展，无新增产品修复；不把单次UDP达标当作整体验收。实机证据：Mac工作区artifacts/wifi-wired-peer-20261104/RESUME-1105-*、POWER-*和STOCK-EDCA-*。

### 历史 PE 拆分高吞吐记录的因果对照

用户提示后，找到实际记录：Mac工作区`artifacts/pe-final-measure-20261029/tcp-down`为1.55848Gbps/0重传，命令`-P4 -w4M -t30 -R`；`pe-uplink-tcp-20261029/U4-p1`和`U8-p1-pressure`为1.55002/1.55337Gbps、0重传，单流`-w4M -t20`，对应f731966及late-barrier模块SHA256 ee47f858c2bdfc86042e03541c715cf2be88948308c895d2580a8dca08c81757。下行当轮缺独立模块SHA，不能仅由时间认定二进制。后续baae3ab的V1-p1仍1.55302Gbps/1重传。

其端点是路由器Linux iperf与Mac，经过ponfeed0→NI VP11/模拟PON source7/PPPoE→FE→WiFi；不是当前Windows物理2.5G端点。下行feed开启EQ_BACKPRESSURE，空闲EQ少于128+已提交数时NETDEV_TX_BUSY停止上游，100us重醒，并有16MiB bfifo缓冲；原始快照停/醒各10212次、no_buffer0。当前物理LAN不能由该测试模块直接停上游。原厂对应EQ阈值已在native，未发现另一项尚未配置的物理LAN背压开关；不重测已失败RMU/PAUSE组合。

PE方向分工、主要聚合循环及当时上行修复均已保留。当前增加旧测试参数的分离比较：默认下行1.366，单独w4M1.397、单独P4 1.409、P4+w4M1.432Gbps；上行默认1.544/0、w4M1.546/11。单纯参数没有恢复旧下行成绩，但跨时段速率变化明显，不能据此认定代码回归。随后关闭/开启/再关闭新增周期RA mask更新得到1.102/1.521/1.523Gbps，稳定两轮相同；候选PE二进制与production完全相同。没有支持撤销原厂RA mask更新，已恢复生产模块。

当前正常模块复测：默认TCP上行1.590Gbps/0重传，下行1.493/1.517Gbps，Windows全局重传差270/318；UDP下行1.801Gbps/2.58%。UDP上行发端仅1.747Gbps、收1.737Gbps，不能证明1.8Gbps目标。旧/新下行平均MSDU/MPDU为6.998/6.947–6.955，主要聚合能力仍在。完整默认验收尚未完成。

另一个限定实验把NI未完成额度从124临时降至64/96：64显著下降，96与124的UDP约1.798/1.794–1.802Gbps、TCP稳定轮1.478/1.493Gbps，没有扩大124的依据；恢复生产，无资源布局改动。原厂WP tag容量更大这一事实本身不能证明当前额度瓶颈。

实际ELF还确认RA背景噪声输入byte15 bit0未接入native；现有原厂说明仅明确用于92XB带宽切换，不能解释本轮8852C MCS10/11变化。因此记为有待定位关联的细节，不在本轮扩大修复范围。

在正常模块和稳定关联下，完整复用旧下行参数P4/w4M/30s得到1.53923Gbps，距历史1.55848约1.2%；Windows全局重传差1849，仍有no_buffer。当前旧参数下能力基本保留，暂无整体PE回退依据。该结果为定位证据，不替代默认单流验收。

### EPP 摄取批量的限定对照

`ni_ingest` 原行为在全局 queued>512 后，即使当前 peer 唤醒，每轮也只消费一个 EPP。去掉该提前退出仍保留后面的睡眠 peer256/global512 入口限制、超限单包 discard、回收 fence、16项预算和124普通 owner额度；实际链接 PE 的批量、睡眠资源和发布测试均通过。

临时同关联 A/B/A 只开关这条条件：默认TCP下行1.430/1.360/1.425Gbps，UDP1.85G下行1.774/1.761/1.761Gbps、丢包63083/74613/74246。TCP no_buffer240/100/236，UDP61487/65535/65535（后两项饱和），failed MPDU均0。减少重复摄取开销没有改善服务速率，TCP还出现退步；因此没有采用这个候选，也不扩大批量或额度。源码恢复，RAM恢复stockdetail，临时模块移除。

这轮前置UDP pacing和供给速率对照无一致增益；高频SSH采样导致Mac接收CPU开销增大，其1.580Gbps结果仅用于识别观测干扰。无连续采样时丢101680包而Mac socket drops仅2493，仍支持NI缓冲耗尽是主要丢包位置。Windows拒绝MSS1440且未发送流量，该次不作归因、不继续修测试工具。

用户将目标更新为默认TCP双向1.8Gbps、UDP双向1.9Gbps，当前尚未达到。历史feed停上游的差别继续限定在物理入口XOFF链条核对；不重新开启BK分支、不重复RMU和TXPause实验。

### 原厂源端口 XOFF 联动的限定验证

实际`ca-ne.ko`的`__ni_flow_ctrl_init`、`__ge_phy2mac_sync`及`aal_ni_eth_port_mac_set`表明，链路协商后还会联动L2TE和TE源端口计数，旧的单独清MAC bit12实验没有覆盖这条链。NI4实读96c4/96a8/290c/2a80/2124均0、2914=73ff、a814=04059901，确实未配置此联动。

按原厂细节临时设置：2124=88888888、源端口低376/高424、2914 bit31、MAC PFC_disable=1/pause_sel=2/flow_disable=0（a814=04054901）、8个COS quanta=ffff、2a80 bit1、290c bit4、96c4 bit31、96a8=ffffff00。VOQ108与VOQ0的profile实读均0，无需修改。保持RMU80000000和无线配置不变。

单开计数掩码的SRC-COUNT及缺少2a80 bit1的SRCFC-B均不用于完整机制归因。补齐后的SRCFC-C为TCP1.283Gbps/no_buffer7、UDP1.724Gbps/丢106401包（6.65%）/no_buffer65535饱和，failed/retired均0；UDP中间样本NI软件排队499且持有396个已提交BID，源端口0..6计数和XOFF状态全部0。前置正常UDP1.749/1.781Gbps。该原厂联动没有解除本路径EQ6耗尽，未合入；未证明源计数覆盖离开TE后的WiFi BID持有周期。

所有临时寄存器逐项恢复并读回确认，正常stockdetail仍在RAM，无诊断进程。目的LPID核对：native实际19（16+CPU3），已包含在ffffff00掩码中，不能用编号不匹配解释零计数。此分支停止，不增加流控工具或重试RMU/TXPause。

### 默认TCP确认节奏与PE供给成本

复用CAP2-DOWN原始抓包：206569个ACK中206374个宣告窗口大于1MiB，没有零窗口；确认推进量中位数8688字节、90分位11584字节，ACK间隔99分位约834us。不支持持续接收窗口限制。已有短ACK聚合4ms与2/4/8包阈值对照继续保留原结论，不重复。原厂客户端WMM IE生产路径保留用户态AIFSN/CW/TXOP，默认值与当前hostapd相同；未据此修改WMM。

限定共享同一发送turn的首次64位TSF，实际主循环完整时钟读取由12次降为3次，PS期限/回绕/撕裂重试和发布回归通过。临时同关联A/B/A默认TCP下行1.519/1.486/1.534Gbps，UDP2G下行1.832/1.852/1.835Gbps，丢包8.23/7.27/8.09%。TCP退步且UDP收益小，不采用这项扩大时间采样误差的候选，恢复生产模块。

为判定当前版本的PE处理能力边界，默认/减半/默认频率对照：TCP1.500/1.428/1.521Gbps，UDP2G1.835/1.498/1.840Gbps。只降低既有FPLL分频后恢复40404，未超频。UDP明显受处理能力影响，TCP只约5%变化，不能把两者全部差距归于PE指令成本。原厂CMAC建表及普通HE STA更新的force_txop、density、length/time override等字段与当前有效值相同，未发现可据以修改的差异。

原厂`phl_tx_pcie`按`min(hw_res,pending_wd_count)`处理，`phl_handle_pending_wd`每次update_txbd最多32项、可累积更多再触发。限定native发送turn8→15（私有ring16留一格）保留124 owner额度及全部发布屏障；实际15+5门铃、跨环、共享TXBD、暂停排空和PS预算回归通过。同关联8/15/8：TCP1.515/1.472/1.496Gbps，UDP2G1.825/1.811/1.835Gbps，丢包8.66/9.30/8.13%。没有收益，候选未合入，恢复8包发布。连续两项调度开销候选未改善默认TCP，停止扩大PE批处理方向；普通硬件聚合控制的已查原厂默认差异仍未成立。


### 2026-11-05：ACK 队尾替换对照结束

实际原厂模块 tcp_ack_drop_dup 初始化默认1，TXSC enqueue 会执行同流ACK替换；原厂实现还会替换窗口/SACK变化及重复、倒退ACK。限定候选只替换同长度54/66B、无负载、无SACK、窗口及TCP序号相同、ACK严格推进的未提交真实队尾，保留最老包、全部时间与所有权；实际链接PE的分类、整包复制、回收credit、EPP fence和PS回归通过。

同关联默认10秒TCP上行 OFF/ON/OFF 为1.683404/1.590131/1.681771Gbps，重传0/13/0。ON替换58045个ACK，将NI发送帧从约6万减至4946、MPDU从7591减至2376，但空闲空口约10%增至14%，吞吐退步。原厂超时producer在5GHz rx_tp>=680时选10000us；4000us仅适用17..679且低TX，1000us仅为硬件timer剩余时间下限，没有更短ACK专用配套。候选撤回，不继续扫描超时参数。

正常stockdetail已恢复到phy44，MAC 3a:2e:a7:75:9a:f2已授权，提交/完成排空，PEDIV00040404、C300003f642b、force_fw_tx=N、全部诊断模块移除。源码恢复，未新增产品改动。原始吞吐验收仍未完成；BK关闭分支继续终止。


### 2026-11-05：正常 TCP 的额外空口重试与单字段竞争窗口对照

正常stockdetail/phy44/MacID1，使用已有radio_ra TXSTS接口每秒查询，排除第一条陈旧区间且后续report连续。默认TCP下行1.496158Gbps，185314成功MPDU、17341次重试（每百成功9.36次）；UDP2G收到1.773543Gbps，218722/6675（3.05次）；UDP1.5G收到1.492610Gbps，187593/3405（1.82次）。同速UDP也显著低于TCP，额外重试不只由供包速率解释；此为MAC重试而非TCP重传。记录采样为单一持久SSH每秒输出，无固件诊断模块，未据此宣称验收。

原厂高吞吐EDCA档01786615的CWmin为63，当前003f642b为15。为与此前整档替换区分，只将C300改为003f662b，保留AIFS43us、TXOP63及CWmax63。TCP1.464735Gbps、180854成功/13957重试（7.72次），未取得吞吐改善。恢复第一轮0.912249Gbps呈0.264→1.266逐秒爬升，重试仅2.17次，不能以这轮证明候选收益；恢复后的无观测复测1.505434Gbps。没有满足共同改善，候选不合入，C300003f642b读回确认。停止继续扫描CW/AIFS/TXOP。

原厂ACK/BA判定限定复核：actual cmac_init@240050–24009c 对CC00 OR00380000，对CC04清bit19并置1f800000，C/XB patch predicate返回false，native一致。CC00当前60784040，ACKTO/ACKTO_CCK均64us；原厂普通160MHz初始化保留低16位，仅5/10MHz分支写ff/80，原厂复位值尚未证明，不能据此称遗漏或改超时。BA_IGNOREA2 bit16当前0，已核对路径同样保留；未发现虚假retry的已证实字段差异。CC04 response CCA位不能误说为接收BA有效性开关。

历史PE主能力保留、旧feed软件背压与零NI缺缓冲的关系已记在前节；本轮没有新增产品修复。保留现有正确性修复，BK关闭分支仍终止。原始验收尚未完成。证据RETRY-SERVICE-*、CWMIN*及retry-service-summary.json。


### NI 缓冲归还的重复屏障

当前固定NI BID从接收到无线RPQ完成一直被同一PE持有。每owner获取之前已有完整sync；每BID PUSH之后也有完整sync，覆盖下一次链读取。原内层在读取next之前再执行一次sync，没有新增发布/跨PE获取对象。实际原厂aal_l3qm_set_cpu_push_paddr（.nictrx-sec a0，f8直接str）函数内没有DMB/DSB，不存在逐BID双屏障的原厂要求；ARM写法不等同PE内存模型，原生仍保留outer acquire、PUSH后sync及全部credit/readiness检查。

最终最小修改仅删除这条重复sync。实际链接7BID回收由15次屏障降为8次；next读取先于PUSH、PUSH后屏障先于owner发布、缺credit/ready保留后缀、已归还BID立即覆写、completion127→0及reserved discard通过。原有RPQ、PS预算及TX发布回归均通过；最终模块编译通过。诊断同关联OFF/ON/OFF TCP1.517/1.493/1.482Gbps，UDP2G1.809/1.804/1.802Gbps，未证明吞吐改善。保留价值为每个报文减少一条已证明重复的完整屏障，不称原始吞吐修复。最终版已去掉全部诊断开关。

另纠正此前容量口径：2496来自本机PE1配置，不能称当前路径与原厂等价容量。原厂实际WFO关闭，CPU普通池768、CPU2特殊2048；NAPI将旧skb交上层后分配另一块skb补充inactive BID，旧payload仍可持有到无线RPQ。native固定池直到RPQ才归还同一BID，这是真实的缓冲生命周期差异，尚未修改分配架构或扩大pool。后续只沿此处评估对no_buffer的因果影响，不重开BK、源XOFF、RMU或竞争窗口扫描。

最终RAM模块rtl_ax_ap_recyclefinal SHA256=7f9ad0e2fe18d18293baea8fd8d7e8cb748416b074353a6261fa6e0ed2eb3f18，PE24008字节/SHA256=71154f2a035dd3ee97a5a8ab8b4b74a325b5ad73cbcba86162f8d7f975672808，phy48。默认TCP上行1.683627Gbps/0重传、下行1.488827Gbps；UDP2G实际发送1.997347/收到1.844711Gbps、丢131749包（7.6292%）。未达到验收，也未将跨关联单次UDP提升归因于本改动。最终无诊断开关，保留BK，时钟/EDCA默认。

补池依据：主仓evidence/rtl-workspace/private/tyfza1/kernel.config:2540关闭CONFIG_RTK_WFO，2336关闭CONFIG_RTK_FC_WIFI_AMSDU_OFFLOAD_BY_PE；scfg-persisted-20261005/default_scfg.txt:224配CPU pool0=768。evidence/wireless-study-20261010-ne-goal/evidence/stock/modules/ca-ne.ko中aal_l3qm_init_startup_config@5a390读取配置，aal_l3qm_init_empty_buffer@588b8–588cc对相对EQ索引2跳58a94取2048，再5890c写CFG1；CB额度5837c–583ac匹配。其.nictrx-sec ca_ni_rx_napi@ddc先调用上层RX hook，13fc/1414读inactive BID并refill；refill@740分配/714 CPU_PUSH；fill_empty@164 dev_alloc_skb_recy_eth，188写新skb到新buffer+7f8。此处证明补池不等无线RPQ，不表示旧payload提前释放。


### NI 备用缓冲补池的 RAM 对照

沿原厂NAPI补新skb这一已确认差异，限定RAM诊断分配4096个2KiB备用缓冲：EPP RD发布后按credit/readiness补新地址，旧payload仍由原WP/队列持有，RPQ或丢弃完成后只回备用链。原队列、124 owner和聚合策略不变，不复制载荷。生命周期固定到本次重启，避免已入EQ的地址被模块卸载释放。实际PE验证覆盖整组/部分替换、零备用/credit/ready、旧新地址多轮循环、载荷保持、EPP回绕和retire fence、PS超额及年龄丢弃；全部保持free+held=4096。原有EPP/RPQ/PS/publication通过，源码恢复后加载诊断模块。

首次OFF/ON/OFF TCP1.457/0.311/0.685Gbps，UDP2G1.284/1.306/1.800Gbps，跨轮明显状态变化，不把低基线造成的差额视为收益。限定追加ON/OFF：TCP1.159/1.514Gbps，UDP1.373/1.402Gbps。ON期间中间样本free3506/held590、queued284/pending46，NI inactive623/active1700；OFF样本free4096/held0、queued499/pending114，inactive2496/active0。补位确实使NI保留更多可用缓冲，但逐批补池候选没有提高吞吐，UDP整轮no_buffer仍饱和。不能把此负结果概括为原厂生命周期差异无价值，也没有依据直接扩大备用池。

诊断未合入。结束gate0、free4096/held0、WP pending0后直接重启，清除可能仍驻留EQ的临时地址；未rmmod或释放已发布备用内存。新boot a31ad74e-face-4509-bdcf-b2761edf4da7，正在恢复已提交rtl_ax_ap_recyclefinal。证据Mac REPLACEMENT-*、replacement-results.json、replacement-confirm-results.json；worktree artifacts/ni-replacement。诊断module SHA256 ed2c3510971abcfe04ac503f05feab74f7b47fa7845aa184d64837c47f92a35e，PE9ad50c00aaba398f5549bb84d4bb1506b3484ec7a3cae96bf0d7eecca3e6c1ab。本輪没有新增产品改动；BK关闭分支及批量/竞争窗口扫描仍终止。

恢复确认：新boot a31ad74e-face-4509-bdcf-b2761edf4da7、phy2，RAM已加载rtl_ax_ap_recyclefinal（已提交48f0ba4），替换实验模块随重启消失。默认TCP下行1.516426Gbps，上行首轮1.341510Gbps/0重传；仍未达成完整验收。


### 备用补池结论的干扰核对与单客户端复验

逐peer计数发现，上一组最低TCP开启轮手机同时收到91912帧，恢复低速轮47012帧；恢复UDP1.800Gbps轮手机仅5帧（replacement-peer-traffic.json）。因此此前不能把全部低速归于候选处理开销。此项新证据触发一次限定复验，复用完全相同模块，hostapd临时断开手机并ban120秒，全部六次测速前后均验证手机未关联。

同关联OFF/ON/OFF：TCP0.975/1.171/1.503Gbps（前两轮仍爬升，无稳定TCP增益结论），UDP2G1.803/1.441/1.809Gbps，丢包9.68/27.67/9.43%。开启轮实际替换2268978次，结束free4096/held0；仍有NI no_buffer。两侧UDP基线一致且唯一peer为Mac，支持这一逐批补池实现确实降低正常服务能力，候选继续撤回，不扩大备用数量或继续改写此实验。

结束gate0/pending0/free4096后重启，清除临时内存和临时ban。新boot a56cceb0-c77a-4d54-bcbe-7edc14ece606，RAM恢复已提交rtl_ax_ap_recyclefinal/phy2。证据REPLACE-ISOLATED-*及replacement-isolated-results.json。生产源码仍为48f0ba4，没有新增产品修改。

恢复复测：默认TCP上行1.712672Gbps/0重传、下行1.452918Gbps。随后核对boot不变、rtl_ax_ap_recyclefinal为唯一radio模块、ARM与NI pending均0，hostapd临时ban为空，EDCA003f642b。隔离实验完整结束。


### 正常 TCP/UDP 发送形态与历史丢包恢复

复用旧worktree的mac_tx_debug_steady及实际原厂halbb解码器，保持正常recyclefinal模块、同一关联，临时断开手机60秒。SERVICE-AIR-TCP默认下行1.497963Gbps，SERVICE-AIR-UDP1500收到1.489116Gbps；两轮前后手机均不在发送peer中。每轮在开始后约2秒采800项MAC mux样本，按raw[:14]+raw[22:24]+6个0映射原厂BB字段。TCP已解码HE样本568项MCS11/55项MCS10，UDP667/3项；全部HE160/2SS/0.8us GI，TCP出现2288us长数据段，未显示整体降速或只能短聚合的机制。解码器只列最常见40种，顺序读取可能跨PPDU，且寄存器保留旧值；样本数量不是实际PPDU占比，PSDU容量估计不能当准确载荷。旧NORMAL-AIR-UDP已能证明MCS10/11与正确GI，本轮新增的是同关联同速TCP/UDP比较。不能据此再调GI/BW或强制速率。

复用无kernel capture drop的CAP2-DOWN抓包，按ACK推进到已观察SACK右边界计算未闭合恢复区间。5200个SACK ACK集中在0.099367–0.135561s和5.207217–5.245431s，共74.408ms。5.2s所在100ms窗只有0.673Gbps，下一窗已恢复1.501Gbps；0.5–5.1s无SACK长区间平均1.503Gbps，5.8–10s平均1.490Gbps。丢包造成局部低谷，但这份记录的持续1.5Gbps上限不由长期SACK恢复解释，不能据此继续扩大缓冲/背压。该分析不推断完整cwnd，也不把所有低速轮等同这份抓包。证据CAP2-DOWN-recovery-summary.json。

逐包保护条件沿用已有实际ELF结论：普通HE且无动态SMPS/ERP时RTS_EN/HW_RTS_EN均0，响应速率字段一致；未发现新候选，不重复RTS参数实验。本轮无产品代码修改。已有原厂对齐正确性修复及48f0ba4重复屏障删除均保留。mac_tx_debug已退出并删除临时可执行文件，CAE4=0、EDCA003f642b/PEDIV00040404，hostapd临时ban为空，ARM/NI pending均0，boot a56cceb0不变。原始吞吐验收未满足。


### RX BA256 的零位图同步修复

本轮检查反向ACK接收路径时，现场boot a56cceb0的190.541s日志明确出现`RX BA marker can't support max_rx_aggregation_subframes 256 > 64`，调用链为rtl_ax_rx_uplink_sync→ieee80211_rx_shortcut_sync→ieee80211_mark_rx_ba_filtered_frames。原厂支持>64 BA会话能力已纳入，但native回交仍使用64位过滤位图接口；即使filtered=0，函数仍右移diff并按实际buf_size循环BIT_ULL，BA128/256存在越界移位。这是已保留BA256能力的配套同步缺陷，不是将原厂架构复制进mac80211。

新增patch220限定零位图语义：原窗口推进/received>=2048/落后diff>=buf_size的出口均不变；diff有效后filtered=0直接清零并进入原最终重排释放，避开移位及逐槽循环。非零位图仍保留64限制，接口注释同步。测试实际kernel函数，临时逆/正应用patch；BA1..64的159744组原新状态与调用序列相同，BA128/256的312组通过，旧代码4个UBSan移位反例失败，错误前移fastpath的变体被拒绝。底层skb交付和最终超时释放为记录stub，实际window-advance helper参与执行。既有test_rx_uplink_sync和RX BA cleanup通过。

编译保留OpenWrt的-fno-caller-saves，mac80211模块SHA256 a1887df2e494af167ff52e3d1cb30402c4fc225b7f4549881b9ffc11c25c6179。修改前build模块.text与板载flash模块.text逐字节SHA一致，确认基线。RAM仅替换mac80211，radio仍rtl_ax_ap_recyclefinal、PE unchanged；当前phy4。修复前默认TCP上行1.649308G/7重传，下行1.487985G；修复后上行1.698083G/0，下行1.499108G，UDP2G下行收到1.834378G/丢8.0913%，上行发送1.622058G/收到1.602637G且MacCPU98.8%。跨关联不作吞吐增益归因，仍未达标；上行UDP本轮发送端未造足流量。手机重新建立TID0/4/5的RX BA256，Mac BA64，加载后无新增marker告警。结束ARM/NI pending0，无临时设置。此正确性修复保留，flash未写。

附定位边界：SERVICE-AIR-TCP反向PE转发138721帧、重排14次、无RXQ/RPQ RDU，没有大量ACK重排证据。实际原厂opcode11的旧AGGCOUNT日志进一步解码：0x340的四行是1..4SS，每桶u32 report计数；TCP9473份report中9350份为2SS HE MCS10/11，UDP8824份中8803份同档。0xb3是队列时间，不能当发送airtime。22份旧日志均仅38条且止于0x343；真正ZLD均值0x38a在末尾，level2/comp64相同却未收到，不能将缺失视为0。ZLD补读尚未执行，保留这项观测缺口，不建设新通用工具。
