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


### 原厂高吞吐积累后排空策略的限定验证

实际txsc_init@eb45c–eb470默认enable/amsdu_enable/mode均1。普通mode1字节门槛会在第8个1514B包到达后触发最多7包出队，但高TX有优先分支：enqueue@eb1d8–eb214在BW160且tx_tp_kbits>>10>1300时等128项或10ms。rtw_core_tx@e8e20–e8f6c在满128后直接调用txsc_amsdu_timeout_tx；f0ae4/f0afc/f0b18循环组装、提交和deque_check，剩余>=7继续，所以128→18组7包→余2。collect_sta_traffic_statistics@d0510–d0550用名义2秒字节增量直接统计；另算的60/40平滑值不参与此门槛。这与旧“不足7包等200/500us”的实验不同。

RAM候选仅人工启用这套开始排空条件，保留原BK、124 owner、每turn发布预算、PS、回收和短ACK策略；每TID 1byte状态位于0x1d100..1d4ff。实际PE测试127等待/128触发/18x7按序/尾部超时/empty reset/独立ACK peer/TSF回绕/PS/退役通过，publication与PSbudget通过。gate可跨暂时睡眠保留，但不越过原PS资格。候选PE SHA d824efcc4990a736564c1821f37d27f07a4fe57bcaff1655a53fb22eed65c192；模块d006ca68fd1ec1bebb30542284486905c207caca61e00f5ca1e14aa758e06c29。源码构建后即恢复HEAD。

OFF/ON/OFF默认TCP1.305/0.804/1.481Gbps；UDP2G1.848/1.830/1.840Gbps，丢7.47/8.39/7.86%。ON实际触发1689次TCP/75次UDP。TCP开启从SYN生效，与原厂达到门槛后启用不同，因此追加同一连接30s中途ON(5s)/OFF(20s)对照：明确排除切换附近区间后ON平均1.509Gbps，后OFF平均1.523Gbps，无稳定收益。候选撤回，不做128/10ms参数扫描，不将直接对齐原厂策略当作适配当前架构的充分条件。

另一个本轮已终止观测：根据原厂WCPU_DATA窗口18e00000及mask3ffff、现有锁定CMAC读工具，读8个已知U2地址校验映射，全部返回deadbeef。没有有效WCPU统计；辅助模块已经卸载，不能据此判断ZLD为0。无新日志/内存工具分支。

恢复收尾：RAM回到rtl_ax_ap_recyclefinal/phy8，mac80211_rxbasync保持；boot a56cceb0未变。首次重关联默认上行1.474477Gbps/3重传（仅通信恢复检查，不作新的稳定性能结论），ARM/NI pending0、failed0/retired0、PEDIV40404/EDCA003f642b，临时ban为空，wcpu/stockdrain辅助模块和测速进程均已结束。生产源码无新增变更，保留88b37cd。


### CPU EPP 准入检查按原厂非空位图缩小读取范围

历史PE拆分版f731966没有当前CPU EPP准入检查；4880ee3为修复CPU接收拥塞尾丢包加入每MPDU扫描8个COS读写指针（16次NI MMIO）。原保护有实际RPS0压力下471→0尾丢包的价值，不能直接撤销。沿此路径核对实际ca-ne.ko：.nictrx-sec ca_ni_rx_get_voqs_state@1850/1868/1870读取STATUS0并取CPU0低8位，ca_ni_rx_poll@18fc/1918/1934只对非空COS进入NAPI；aal_l3qm_get_rx_status0@2c/34读取659c。当前检查可以使用同一硬件非空快照。

最终产品改动仅4行：每个完整MPDU读取一次659c低8位，仅对置位COS读取原WR/RD。仍计算原peak+VP在途+完整子帧数<=96，保留32项余量、payload/PN/SN修改前检查及所有NI资源语义。没有跨MPDU缓存、流量例外或新阈值。位图/指针与其他入口并非原子快照，沿用原有限余量；本次不宣称证明所有并发入口的无损性。空队列时16次读取变1次，单活跃COS变3次；8个全忙时17次。原厂位图只用来选择队列，仍由原指针检查计算占用。

临时MB221同关联OFF/ON/OFF（手机全部12轮前后均未关联）：默认TCP上行1.678756/1.689547/1.683817Gbps，均0重传；下行1.494817/1.489931/1.488594Gbps；UDP2G下行1.848640/1.841049/1.850007Gbps，丢7.426/7.807/7.374%。上行UDP发端1.642/1.834/1.628Gbps，收1.633/1.829/1.619Gbps，各轮MacCPU98–99%。限定追加ON/OFF/ON收到1.745159/1.560460/1.695080Gbps，丢0.0432/0.0803/0.0211%，支持减少读取对上行排空有价值，但仍有绝对速率波动，未达到目标，不把CPU近满载简单当作与固件无关。

使用原CPU端iperf/RPS0做准入压力检查（非验收端点）：OFF/ON/OFF为0.998/0.845/0.819Gbps，均0TCP重传、0L3TE尾丢包。三轮后半段都明显下降，不能归因于开启候选；该检查只支持没有复现原保护要解决的尾丢包。临时RPS已恢复8，服务已退出。脚本kill后iperf自行删除pidfile导致多余rm报错，数据完整且状态恢复实读确认；只去掉重复rm，没有重做实验。

实际链接测试保留原完整RX ownership/PN/reorder/RPQ覆盖，160组COS占用/在途/回绕/恢复，全部256种位图、其他CPU高24位隔离及下一MPDU新拥塞重读通过，实际MMIO次数验证通过。最终无开关版本重跑同套测试、IMEM/入口/栈（6736<8176）及TX publication通过。最终模块SHA ab3fed7cf3b46943f1ad8edcc981795fe971053e51334223ef6b51e3a764faeb，PE SHA 2c4681ef7835e881517c6c3488efb6caf8ecb89e093b02256875403280fc0bb5。保留价值是明确减少每包MMIO且有上行UDP改善证据，不能称默认TCP吞吐问题已解决。

此前正常重试/T2R核对也结束：live C62C=20200000，与原厂参考MIB默认31经实际rtw_hal_mac_set_tx_retry_limit_all_sta@1a9ed0加一后的32一致；C IC init@2e14c4只写a10/2a10，live10624=0101030a/10608=1e1e1e1e与原厂PHY表一致，不能复制XB专用15/9时间配置。EDCCA原有3072向量核对仍有效，没有新差异。不改重试/时序、不重开BK分支。

最终无开关模块rtl_ax_ap_eppfinal/phy12：默认TCP上行1.675232Gbps/0重传；首次下行1.138554Gbps，最后3秒0.793/0.155/0.074Gbps，未忽略此异常；UDP完成后默认下行复测1.490533Gbps。UDP2G上行发1.873873/收1.863058Gbps、丢650（0.04014%），下行发1.997969/收1.830260Gbps、丢144596（8.364%）。这组最终测试手机未隔离；此前同关联隔离A/B支持本改动无稳定TCP退步，最终单轮低谷不作新归因。原始吞吐验收仍未满足。结束ARM/NI pending0、failed0、EDCA003f642b、RPS8、临时ban为空、无诊断开关或本轮测速进程，boot a56cceb0不变；mac80211_rxbasync继续保留，flash未写。


### 正常发送填充观测与 PS 扫描候选

保持0b4863b/eppfinal，复用已有mac_tx_debug采样程序，仅追加原厂CA20用户0选择和CA24/CA28/CA20读取。idle800项ZLD全0；同关联隔离手机的AIR-FILL-TCP1.471315Gbps、UDP2G收到1.835484Gbps。TCP/UDP各800项CA24低10位中位848/864、饱和1023各16/7项。单位未确认，不换算空口占比；顺序mux读取不等于独立完整PPDU，不能用样本数量当PPDU数量。MPDU与DMA计数相等且HE字段前后相同的常见组合：TCP63MPDU/168符号，UDP24/65、32/86、40/106，与当前约7MSDU/MPDU的数据量基本相符；不支持据此修改填充阈值。程序已退出并删除，CA20恢复01010100、CAE4恢复0，未更改生产行为。

旧TXSTS原始32B的offset20..31出现a5a5和固定地址，不能强行按较新SDK解码RTS/PPDU数字；已有OK/retry/total口径保留，没有新报告工具。缺失opcode11的0x38a仍不能由这些观测补成可靠的pre/mid/post平均值。

沿正常PE1固定处理成本，ni_ps_scan每轮对一个MACID先ni_ps_grant（两次sync及内容读取），之后才比较command与PE已处理ps[2]。该扫描与原厂事件式stop/wakeup语义的适配有关，但重复读取不是原厂要求。限定候选只提前比较version==ps[2]，相等本次返回；不读新授权内容，不改变127站点轮转、新授权完整ni_ps_grant及原8TID检查/事件发布。测试实际PE120组旧新状态、0xfffffffe回绕、并发新授权下一扫描处理、未变化不读generation通过；启用条件下原PS budget、wake撤销及TX publication/IMEM通过。临时MB221开关/module046f0581636feff2522b8c72041ce11dc0911e0e9a85a5a5e61c8b3071a3770f、PE3115d8165cb78a0c06899ec087bf1e9021ff256ae7173556cee088549a47046d，源码构建后恢复。该阶段仅为RAM候选；后续结果如下。

PS-SCAN同关联OFF/ON/OFF，手机六轮前后均未关联：默认TCP下行1.476129/1.459377/1.245579Gbps，恢复轮后半段明显下降，不能称稳定提速；UDP2G收到1.839691/1.850317/1.841480Gbps，丢7.881/7.361/7.690%。没有明显UDP退步或新的数据所有权错误。按减少固定处理成本保留提前version==ack判断，不将它称为已找到吞吐根因。最终版本仅新增版本/ACK读取和相等返回（连注释5行），新授权仍完整获取。新旧实际二进制120组状态/事件比较一致，已处理偶版本sync2→0；最终原有PS wake/queue budget/publication、IMEM和新增无grant读取/竞态/版本及MACID回绕回归通过。诊断开关去掉，产品模块de4dbfecb7d270e384d8c287e32a22df3e35210d2a879489a8ed3d554757c794，PE8a48f08278629905bdd525d5fed46031422539908b6a1d97b3e13d5ec05d6cdb。

最终RAM rtl_ax_ap_psfinal/phy16：默认TCP上行1.682167Gbps/0重传；首次下行0.905050Gbps（1秒区间1.301/1.405/1.482/1.410/1.127/0.320/0.120/0.140/0.783/0.959），其前后仅Mac1且sleep/wake分别+25/+26，RXQ/RPQ RDU均0；随后默认下行恢复1.482141Gbps。不能把正常场景低谷归于本次扫描优化：上一已提交eppfinal首次下行1.138554低谷也仅Mac，sleep/wake各+25，先前“该组手机未隔离”的描述不足以说明这两个具体TCP轮。此证据只将正常低谷与Mac PM切换建立关联，不能倒推因果，不重开BK关闭分支。UDP2G上行发1.887185/收1.878853Gbps、丢34（0.00209%）；下行收1.842961Gbps、丢132822（7.684%）。上行host_total字段0.0129与user7.9569/system89.6044自相矛盾，记录原值但不据其判断CPU空闲。原始验收未完成。结束ARM/NI pending0、failed0、RPS8、EDCA003f642b、临时ban为空，无本轮诊断/iperf进程；mac80211_rxbasync保留，flash未写。


### 正常 PM 时序、端点角色和 PPDU 采样边界

0954411/psfinal/phy16保持不变，复用sta_notify_watch，仅给Mac/手机省电通知加现实时戳。NORMAL-PM-TCP-DOWN默认1.505206Gbps，Mac在iperf时间戳前5.7ms唤醒，结束约0.42s后睡眠，传输中无切换；限定追加“默认上传后立即下载”序列，上行1.673525/0重传、下行1.494730Gbps，Mac在下行开始0.137s唤醒，此后直至10.428s无切换。两轮没有复现低谷，证明持续约1.5Gbps并不需要Mac反复睡眠。偶发低谷PM因果仍未证实，停止加深此分支。原观察模块已卸载，未改生产收发或PS配置。原样本见NORMAL-PM-*及normal-pm-summary.json。

交换iperf客户端/服务端角色，保持Windows有线与Mac无线之间的方向、默认单流/socket选项不变：Windows客户端→Mac服务端下行1.332704Gbps；反向上行1.706077Gbps/0重传；回原Mac客户端-R后下行1.494512Gbps。未解除上限，不修改端点设置。Mac临时5210服务已按原工具会话停止，Windows原5201服务保留。手机192.168.1.165的原5201服务连接超时（errno35），ADB无设备，没有手机有效测速且未维修工具。

沿之前填充样本的观测缺口，为现有mac_tx采样追加CAE0的HE计数前后读取，保留所有发送配置；800项idle冒烟通过。隔离手机，AIR-COUNT-TCP1.481495Gbps、UDP2G1.838599Gbps。约0.939s采样窗口HE计数分别增长618/754，去重后同计数HE样本419/599；但相同HE计数内CA20的MPDU计数会继续增长，UDP还出现符号数变化，所以该计数不是完整PPDU快照边界。不能从样本均值/比例推算准确空口时间或填充开销。此前“常见符号数与长度相符”只是一致性检查，不能严密排除填充损耗。无参数改动，采样程序已删除，CAE0/CAE4恢复0、CA20恢复01010100。

进一步只读实际U2：2011dc18/24把报告byte24累计到stats+256，dc2c/30把byte25累计到+260，dc3a/3e把byte31累计到+264；2013c69c..c704分别除stats+12输出0x38a，与参考tx_statistic.h的pre/mid/post_zld_len吻合。仍无报告字段单位/完整实机平均值，CA24低10位不能替代这三个字段。未恢复已终止的WCPU或固件日志工具。TXSTS尾部固定/a5a5数据继续不用于RTS/PPDU归因。

本轮没有新产品修改。稳定默认下行仍约1.49–1.51Gbps，TCP1.8/UDP1.9和低丢包目标未满足。保留全部已接受修复；不重复BK关闭、队列阈值、时序/EDCA/时钟、补池或ACK替换实验。


### 正常 RTS 对照及 BSSID CAM 颜色假设的边界

保持7c0ea7c/psfinal/phy16、同一关联、BK开启及正常EDCA/PEDIV。正常TCP比同速UDP有更多MAC重试，因此仅使用已有动态SMPS的RTS编码作因果干预，不把普通HE缺少RTS称为原厂遗漏。第一组OFF/ON/OFF下行TCP为1.421804/1.535364/1.486493Gbps；去掉首个旧TXSTS报告后成功MPDU分别176094/190513/184319，重试12275/9908/16031（每百成功6.97/5.20/8.70）。只用单个持久SSH每秒读取既有TXSTS，描述符确认ON的word12=88000000、OFF为0。

限定追加不带持续采样的ON/OFF/ON：默认下行TCP1.546364/1.485690/1.547737Gbps；UDP2G收到1.831331/1.844769/1.833072Gbps，丢包8.2011/7.5864/8.1555%。收益可重复但有限，UDP有小幅代价。参考check_rts的普通分支沿用psta->rtsen，VCS_update只有动态SMPS/ERP/显式配置；cca_rts_mode唯一赋值来自调试命令，未发现按TCP重试率自动开启的原厂策略。保持普通默认RTS关闭，不新增按协议判别、阈值扫描或自创动态保护策略。smps_probe已卸载，peer rate00230180、WD12=0，未更改生产源码。证据Mac NORMAL-RTS-*、RTS-CONFIRM-*、normal-rts-results.json、rts-confirm-results.json；worktree artifacts/normal-rts。

随后沿双向空口竞争核对同BSS/NAV识别：端口C6A0=5，native自角色及peer的BSSID CAM命令byte53固定0。实际原厂_hal_stainfo_to_macrinfo@1a86d4从sta+2513取低6位写role+55，role_info_init@22e8ec再转入BSSID字段，fill_bssid_cam_info@1e7bac编码到H2C byte53。执行这三层64种颜色全部通过，证明给定输入的编码，不能证明AP客户端的默认输入非零。

复用既有H2C入口，在无流量时只重发完全相同的MACID0自角色CAM、byte53改5；done ACK与连通冒烟通过，恢复byte53=0同样done ACK成功。手机90s隔离、同关联0/5/0：下行TCP1.509666/1.484779/1.485192Gbps；上行TCP1.648289/1.650232/1.687897Gbps，重传19/816/0；UDP2G下行1.841937/1.840079/1.837527Gbps，丢7.7372/7.8426/7.9608%。ON上行816次集中于第二秒，未归因，但不能称无相关负面；没有吞吐收益，候选未纳入，不重复颜色实验。

继续核对实际update_sta_he_info_apmode@af95c（内联MAC/PHY处理，MCS/PPE另调）：零/5/63初值及4种HE能力模式共12组保留sta+2513，内存写监测没有触及该字节；参考AP producer也没有复制AP广播颜色。这里更正实验前“原厂会把同一广播颜色写入AP客户端CAM”的过强表述：原厂底层会透传输入，但尚未证明本机AP producer给非零输入，不能将CAM0定性为已确认缺陷。此分支停止，无产品补丁。证据worktree artifacts/bss-color/{verify_stock.py,stock-fields.json,verify_ap_producer.py,ap-producer.json,color_probe.c}；Mac COLOR-*、color-results.json。

本轮仅增加定位证据，没有产品或工具修复；主仓全部已接受修复保留。最后OFF-B正常上行1.687897Gbps/0重传、下行1.485192Gbps、UDP下行1.837527Gbps/7.9608%，未达TCP1.8/UDP1.9及低丢包目标。清除本轮辅助模块，恢复CAM0/RTS关闭，flash未写，不重开BK、队列阈值、EDCA、超时和填充观测分支。


### NI 回收批次复用剩余额度

先复用本轮已有前后快照排除ARM小包插入解释：六轮正常TCP下行约17.7–19.3万PE MPDU，ARM submitted增量仅0–21，不能解释数千聚合break；未加新诊断。沿NI排空固定开销核对实际ca-ne：ca_ni_rx_napi .nictrx-sec 13fc读inactive BID计数，1414把整份数量传给refill；ca_ni_refill_eq_buf_pool_with_rule_zero@6d0在714循环PUSH，不逐个重读额度。native同MPDU已经使用局部credit，跨完成MPDU仍重复MMIO读。本机运行时ARM仅补EQ4/5，PE1独占当前无线EQ6；PE代码保持每BID ready检查和PUSH后完整sync。

限定改动把credit移到ni_recycle调用局部，只有credit==0才重新读取，接受一次PUSH就减1；跨调用不缓存。保留所有ownership、PS/TID、回收统计及busy行为。硬件新释放只增加可用额度，局部剩余是可用量下界。若原快照在一个MPDU中途用完，沿用原部分回收出口，下个主循环再获取，最多延后剩余链到下次调用，不强行无界排空。实际链接16个7BID MPDU的计数由16次额度读降1，112次ready读不变；初始7额度且硬件追加时读2次。测试覆盖零额度、MPDU中途耗尽、MPDU边界/中途busy、跨completion126→14、已归还BID立即覆写、续调无重复，全部通过。原RPQ1024MPDU/2048BID、PS预算、七BID顺序、TX发布、IMEM通过，保守栈6744<8176。

临时MB221诊断同关联OFF/ON/OFF：默认TCP下行1.413335/1.478999/1.432756Gbps，UDP2G1.841505/1.840619/1.834400Gbps，丢7.7866/7.7973/8.1168%。TCP的no_buffer93/42/159，UDP均计数饱和；failed/retired/RXQ_RDU增量均0。反向顺序确认ON/OFF/ON的TCP1.470502/1.490835/1.471722Gbps，UDP1.840425/1.830517/1.837282Gbps，丢7.8447/8.3631/7.9920%。第二组不支持稳定TCP提升，因此不称吞吐根因修复；按明确减少重复MMIO及原厂批量额度契约保留最小改动，未改变资源数量或无线策略。

无开关最终PE SHA256=65869939084cd2c83e12ddd1592c001bf6b0e9097aeec0683138d036ef180e9f；rtl_ax_ap_creditfinal模块SHA256=ebf0dedb8d615ea74bc8e2943b1d553a81629130f7ca731771e8d63100931f01。最终无开关实际代码重跑上述六套验证通过。产物worktree artifacts/recycle-credit，实机Mac CREDIT-*及credit-results.json、credit-confirm-results.json。最终RAM验收随后记录，目标仍TCP1.8/UDP1.9且低丢包。


无开关最终版首次phy20验收：默认TCP上行1.232523Gbps/0重传，前5秒1.62–1.72、7–9秒0.455/0.342/0.209并有+24sleep/+25wake；下行1.488273Gbps。UDP2G上行发1.719081/收1.709070Gbps、丢341（0.02297%）、Mac CPU98.37%；下行收1.844196Gbps、丢131914（7.63808%）。上行单次复测1.640576Gbps/45重传，未将这些异常忽略为“必然无关”。

为补足上行直接回归证据，恢复同一诊断版phy22再做OFF/ON/OFF：TCP1.368145/1.682120/1.691893Gbps，全部0重传。首个OFF同样在后3秒1.227/0.449/0.197发生低谷、sleep/wake各+23，说明低谷不以本改动开启为必要条件；不重开PM或BK分支。UDP发1.897636/1.902630/1.904928Gbps，收1.892780/1.844143/1.894631Gbps，丢648/650/247（0.03952/0.03954/0.01501%）；ON接收统计时长10.320975秒，OFF10.022742/10.023290秒，收到总字节2.371/2.379/2.374GB基本相近，仍按原始received速率报告，不用统一时长替代验收。不据单个不同计时轮证明稳定吞吐回退或提升。来源CREDIT-FINAL-*、CREDIT-UP-*、credit-up-results.json。

保留依据为明确减少MMIO且最小所有权契约不变，实机无新回收/失败状态。目标吞吐和低丢包仍未达到。最终恢复无诊断开关rtl_ax_ap_creditfinal；mac80211_rxbasync与全部前序修复保留，flash不变。


### 正常 ACK、供包与 PHY 发射时间的定向核对

b51d106/creditfinal/phy24保持，未重载radio。当前接收公布11K MPDU、HE exponent扩展2、RX BA256及AMSDU，hostapd默认SMPS_DISABLED；没有建立ACK缺少聚合能力的候选。ACK-AIR-DOWN1.469206Gbps，单持久SSH每秒取iw/env：反向PE报文162186，RXQ46373、ARM例外2445、重排13，说明已有聚合；多数最新RX速率HE160/2SS/MCS11、少数20MHz，最新样本不等于完整速率分布。没有据此修改接收能力、聚合间距或窗口。

补齐Windows默认单流物理发送方向验证：路由器iperf3客户端-R向原Windows5201服务，WIRED-SEND-DEFAULT收到2.337645Gbps，路由器CPU61.36%，排除Windows发端固定1.5Gbps上限。反向路由器发送WIRED-RECEIVE-DEFAULT1.548817Gbps/0重传、路由器CPU98.51%，属于CPU造流检查，不给无线转发能力设上限；临时客户端自行结束，无新服务。复用CAP2无SACK稳定区间0.5–5.1秒，数据间隙>500us共2608项/累计2.2097秒，其中2535项间隙内有ACK，数据gap p99约279us/p99.9约953us；这些是接收栈时间戳，不能当空口空闲。解析只复用已有parser，缺__file__做一次最小修正后完成，没有新抓包。

NORMAL-BACKLOG-DOWN用原radio_tx读取31个中间样本：pending min/median/max0/84/124，queued0/106/499，TXBD0/0/8；29项TXBD0，只有1项pending和queued同时0。采样吞吐1.364818Gbps低于无采样约1.49，不作新基线，且粗采样不能排除短暂空队列；结果不支持长期等发端供包，继续聚焦取走WD后的无线服务效率。未加载队列探针。

沿原厂HE关联元数据发现当前JOIN固定id|8b040000，HE/trigger/bandwidth字段全0。实际_hal_stainfo_to_macrinfo@1a8390在HE且role linked2时置trigger/HE，带宽从sta+44生成；mac_h2c_join_info@22dc7c编码trigger bit12、HE bit13、DL_BW bits15:14。16组HE/HT、BW0..3、linked0/2实际执行通过。原厂填充取PPE/trigger能力，不能直接假定。hostapd原始HE能力查询临时20行Unix socket工具缺time头修正后仍超时，停止并删除设备可执行文件，不再维修工具。限定诊断仅补已知HE160的f000位，未知填充值不动，关闭轮也重复相同JOIN排除单纯重发影响。OFF/ON/OFF：TCP下行1.492943/1.494536/1.477208G，上行1.685467/1.663571/1.691309G（重传0/4/0），UDP2G下行1.844627/1.845311/1.838828G，丢7.6599/7.6100/7.8989%。未解除上限，部分JOIN候选未合入；完整元数据差异保留记录，不把它当已验证完整正确性修复。helper卸载恢复8b040001 doneACK=0，radio正常；artifacts/he-join及Mac JOIN-*。

新的低观测开销依据：实际set_hw_ch_busy_cnt@239410（参考status.c）仅更换C654统计参考源，保留低23位，将原C8033005暂改00833005只计PHY_TXON，不改ATM调度位/收发配置；C658/C65C原始32位idle/busy差无reset。idle约1.13s冒烟总增长1.150695百万，支持微秒尺度但分析用单位比值，不强定单位。每组开始/结束校验cfg保持00833005，无中途重置或回绕，结束恢复C8033005实读。

无持续采样的PHY-TIME-TCP1.493017G，busy8406984，有效bits/busy1777.23；UDP2G1.841335G，busy9155883，比值2011.93。限定同速UDP1.5G收到1.494421G，busy7794394，比值1918.73；默认TCP复测1.475147G，busy8425376，比值1751.20。计数窗口包含测速前后状态读，故busy_fraction不是精确测试期占空比，但总发射时间/有效数据仍显示TCP效率较低。

最后仅将既有TXSTS每秒查询与该时间计数放在同组：PHY-RETRY-TCP收到1.490793G，OK184796/retry27539，每百成功14.9024，bits/busy1774.49；同速UDP收到1.491714G，OK187501/retry12587，每百成功6.7130，bits/busy1891.63。去掉首陈旧report，后续report均不同；统计窗口近似同测速但并非逐MPDU严格匹配。按等MPDU/等PHY成本近似乘(1+retry/OK)，结果2038.93/2018.61，差约1%。这不是实际逐重试airtime，不能严格证明重试解释全部损失，但足以取消当前“另加TCP填充损耗”候选；接下来优先正常TCP额外MAC重试原因，不重开BK、EDCA扫描、RTS扫描或ZLD观察工具。此轮无产品改动，完整目标仍未达成。


### 正常双向小包竞争、响应分类与广播 RTS 门槛

本轮b51d106/creditfinal/phy24保持。RX-CRC-TCP下行1.484589G，已有MAC RX计数连续12次读取（16位回绕按差）：OFDM成功10377/CRC1，HESU成功48257/CRC5；UDP1.5G收到1.492493G，OFDM11090/CRC0、HESU35/CRC0。未见与数千额外MAC重试相称的AP接收CRC错误；不能排除未检测到的响应或部分BA确认，未改RX配置。

复用已有正常BK开启的AGGREASON/AGGCOUNT/AGGPS-ON日志，实际U2@2011da06报告byte12/13低9位=total，byte14/15低9位=OK，s9=total-OK；SU分支2011e330..e346累加OK到stats+28、差到+32，2011e428..e442仅report byte0 bits7:6==1时把差累加+40。日志0xac@2013be36输出这三项；所以norsp单位为MPDU尝试，不是BA次数。TCP正常四轮failed13872/14061/13696/11506，norsp7107/7679/5385/6388；UDP两轮failed3410/5972，norsp1209/1338。0x376零OK报告TCP206/197/180/204、UDP67/65。这里只证明状态1和部分失败都存在，不把状态1MPDU数说成丢失BA数量。0xb3末三项是队列时间极值/均值，不求差。原始解析historical-response-results.json保留before/after。

新因果干预保持下行UDP1.5G，额外上行UDP24B payload/3M约15625pps（Ethernet约66B，与TCP ACK大小接近），Mac无线→路由器本地5212；本地服务器仅接收约10MB/s线速开销，CPU实测9.85%。其使用相同无线反向路径，但终点为路由器，不把它当完整Windows ACK路径模拟。冒烟31274发/31273收、0丢；正式12秒187523发/187518收、0丢，发送3.000M足量。OFF/ON/OFF下行收到1.489923/1.476414/1.495535G，MAC每百成功重试2.2497/8.2304/1.7831，UDP丢0.5478/1.4292/0.2562%。仅增加反向小包即可显著提高下行重试，支持收发竞争链，不要求TCP特有协议行为。TXSTS首旧报告去除、后续序号独立。源ACKLOAD-*、ackload-results.json；临时5212服务已kill并确认退出，Windows原5201保留。WindowsSSH master h过期后仅重新认证恢复，未改端点网络设置。

响应取消计数CC14/18/1C初值0/00ff0000/0；参考CC18bit24清零后仍00ff0000，TCP/UDP期间全部不变。该字段未确认是否饱和或此版本布局不同，不能用于排除/支持调度取消响应。清零bit已恢复0，观察停止；RESP-ABORT-*不作归因，不改响应CCA/NAV或重试超时。

沿客户端保护策略发现HE Operation具体差异：实际rtw_build_he_operation_ie@b0944先清16B，只写defaultPEdur4，RTS TXOP bits4..13保持0；4种颜色、污染初值的原厂执行均输出ff0724040000...。当前hostapd实际Beacon为ff0724f43f00...（门槛1023）。现有ubus get_sta_ies可读完整客户端IE，后续若需要应直接用它，不修之前Unix socket查询工具。为确认广播用既有ieee80211_beacon_get_template读取HE Operation，DWARF hw14064/ap_vif16256，辅助模块仅读模板并卸载。

仅修改/var/run/hostapd-phy24.conf。第一次BSS reload没有读入新值，Beacon仍1023且触发重新关联，这不是候选有效轮，finally恢复。一次最小重试使用现成全局ubus hostapd reload phy24/radio-1，重新建立BSS并CAC，读回ff07240400002cfcff确认0已生效。此方法跨关联且BSS颜色随机变化，不把单轮差归因。原正常1023轮TCP下行1.465235G/每百成功retry13.3319、上行1.687323G/0重传、UDP2G收到1.836140G（raw loss7.8515%）；原厂门槛0轮下行1.486843G/retry11.8452、上行1.690179G/0、UDP1.846538G/loss7.5700%。没有解除上限，未进行门槛扫描，未改AP WD RTS。恢复1023后Beacon读回ff0724f43f0024fcff，恢复轮随后记录。门槛候选不直接合入，完整目标未满足。


HE门槛恢复轮：TCP下行1.420070G/retry9.8271每百成功、上行1.681945G/0重传、UDP2G1.850648G/7.2653%。两侧1023的retry13.33/9.83夹住0的11.85，没有稳定原厂门槛收益，且三轮跨关联/颜色变化；不合入、不重复门槛试验。结束运行配置与备份cmp一致，删备份和beacon_read设备文件；ARM/NI pending0、failed0、RPS8、PEDIV40404、EDCA003f642b、C654c8033005、WD12=0，无辅助模块/测试进程，临时ban为空。RAM creditfinal/mac80211_rxbasync保留，flash未写。本轮无产品变更，唯一新主线证据是双向小包流量可诱发额外下行重试，未证明最终根因。


### 2026-11-05：旧 PE 结果复核及反向小包的接收路径对照

再次核对旧记录而不重复测速：tcp-down为P4/w4M/30s、1.558480Gbps、TCP重传0；此前当前版本同参数已测1.53923Gbps。旧单流上传w4M为1.55002/1.55337Gbps；当前默认上传已有约1.68Gbps/0重传。因此没有旧PE整体吞吐能力退化的依据。旧Linux ponfeed使用EQ背压和16MiB bfifo，stop/wake各10212且NI no_buffer0；这说明其低TCP丢包有不同的供包条件。旧快照的iw tx retries0没有覆盖PE空口重试，不能与当前TXSTS重试直接比较。原厂对齐修复全部保留。

按反向小包诱发重试这一链条，只读复核实际rtk_wifi6.ko cmac_init@23f100–23f430：C33C低7位C=3e/XB=40、C340清BTCCA、ASIC保留其余CCA位、9D00 bit11为0才设C338低5位16、C的C340高字节6a，均与native及旧PE版本一致。当前9D00=80001f3f（bit11=1），C338=103c0209，所以当前prebackoff9不构成漏写16的证据。C390=712100ff，CC00/CC04、slot9和已查T2R原厂分支一致；正确BAR地址a0410608读1e1e1e1e、a0410624读0101030a。先前误读a04010608的0无效。没有改寄存器，没有CCA/退避/时序候选。

共享QoS序号也沿已有实现核对：ARM raw BIT22与NI BIT18都由同一PE的sequences[macid*8+tid]分配；ACKLOAD开启轮ARM submitted仅+2，关闭两侧+1/+0，不支持ARM发送插入解释大批重试。不新增序号观测工具。

限定ACKPATH对照将额外Mac无线UDP24B/3M的接收端由路由器本机改为Windows物理有线5212，主下行仍UDP1.5G，radio模块/关联/策略不变。OFF-A/WIN/OFF-B收到1.495179/1.485875/1.495759Gbps，MAC retry/100成功MPDU=2.2213/8.5490/2.0352；UDP丢2713/11121/2965（0.20936/0.85844/0.22868%）。辅流12秒收到187514包、2.998901Mbps、0丢包。每秒既有TXSTS去掉首陈旧报告，剩余序号独立；WIN和OFF-B前后仅Mac，OFF-A前快照还有手机、结束已无，因此因果判断主要依WIN/OFF-B及前一组ACKLOAD的重复效应。这支持小包反向空口本身的影响不依赖路由器本机UDP接收；不证明全部TCP差距由碰撞或某字段解释。

临时Windows后台启动方式未保留监听，唯一修复改为持久SSH前台server后冒烟2秒0丢通过；第二次del_client超时，直接用现有iw/PE peer确认无手机后取消重复调用，不维修ubus。无效冒烟不作归因。结束停止5212监听进程、删除两条临时防火墙规则及日志，原5201保留；没有修改产品源码、模块或flash。原始TCP1.8/UDP1.9低丢包目标仍未完成。来源Mac ACKPATH-*及ackpath-results.json。到此停止无实际差异的CCA参数方向，不重复已关闭的BK、EDCA/RTS/超时、源XOFF或补池试验。

收尾读回：creditfinal/phy24、boot a56cceb0保持，ARM submitted/completed4420、NI sent/recycled6772495，pending/failed/unmatched均0；C300003f642b、C654c8033005，无辅助模块或路由器iperf。Windows5212监听数0、临时防火墙规则数0，原5201 PID18992保留。list_bans限定3秒仍超时，未宣称查询返回空；本轮只请求20秒临时ban、距请求已超过数分钟，未设置永久ban，也未为此重启hostapd。


### 2026-11-05：完整 HE peer JOIN 与 trigger CAM 对齐

反向小包重试链继续核对关联元数据。此前只补JOIN f000的临时试验未覆盖TF padding、DL_T_PE及地址CAM trigger。实际_hal_stainfo_to_macrinfo@1a8390读取sta+2504 bits55:54作为TF padding、根据sta+2624+(nss-1)*4的PPE80生成DL_T_PE（即使160MHz也取80字段），HE且role linked2同时设trigger/is_hesta；mac_h2c_join_info@22dc7c编码bits12/13、BW15:14、TF17:16、PE20:18。role_info_init@22e7cc还将trigger拷至a_info.tf_trs，fill_addr_cam_info@1e773c输出CAM byte32 bit14。之前代码始终legacy JOIN8b040000且CAM trigger0，能力变化只更新RA/CCTL、不更新JOIN。

现有get_sta_ies仍3秒超时，未修查询工具；只读小模块通过当前creditfinal DWARF确认hw14064/ap_vif16256后读取Linux已有STA。Mac HE160/nss2，MAC010808800088、PHY4c3002807d019f080ccc00、PPE791cc7711cc771：TF padding2=16us、PPE每SS每RU值56对应DL_T_PE2。因此该peer JOIN应8b0af001、CAM32应4001。修正原注释：旧8b040000中的1是DL_T_PE，不是TF padding。

产品修改仅role.h/monitor.h：从实际HE能力/PPE和受限NSS/BW生成JOIN，在正常station_add的两次地址CAM写入加HE trigger；带宽/NSS变化的ra_update_work同步JOIN。无PPE时保留原厂零初始化PPE80的8us语义，不把nominal padding误用于该JOIN字段；自角色创建/删除及非HE处理保留。PE代码/二进制不变。测试scripts/test_radio_he_join.py编译实际native helper，与原厂PPE parser→HAL producer→JOIN及CAM encoders比较800组，包含HE/legacy、BW0..3、NSS1/2、不同RU mask/缺PPE、随机PPE和TF时长；通过。另96组底层字段验证通过。完整ARM64模块编译通过。旧test_wifi_station_release夹具缺少现有HE/BA/ra字段而编译失败（含新helper类型），不将其记为通过，未扩大修复旧夹具。

临时同关联JOIN+全量CAM重写的第一组不代表正常产品路径：三轮上行PE_uplink_frames增量均0，ARM_exceptions约13万/轮，上行只0.592/0.570/0.592G，开启轮630次重传；下行1.194/1.378/1.206G。全量CAM在已安装密钥的关联中重发后出现全ARM接收，尚未单独定位具体CAM安全字段，不能把此组当正常候选收益/回归证据。辅助工具退出/删除，不继续维修；改用正式station_add在密钥安装前写正确字段。

正式无开关RAM rtl_ax_ap_hejoin SHA2245af9edc1fd1cf3b55a9d2b7f3d4f13c36ee7a5f21072f8e85609d5da71dab，PE SHA65869939084cd2c83e12ddd1592c001bf6b0e9097aeec0683138d036ef180e9f与creditfinal相同。加载时原hostapd忙循环阻塞wifi up，服务日志明确SIGTERM不能处理；仅一次/etc/init.d/wpad restart后恢复，未修改hostapd，phy26/CAC60s完成，Mac重新关联/密钥/BA均成功，ubus恢复。完整产品上行首轮1.209494G/44重传且逐秒1.608→0.782→1.452，未忽略；后两次1.669741/1.687334G均0重传。下行1.484723/1.493733G。上行各轮PE转发1018274/1414596/1431156帧，默认下行ACK PE176417/163662帧，确认正常快速路径。UDP2G上行发1.865131/收1.858769G、丢449(0.02786%)，MacCPU98.64%；下行发1.998585/收1.849946G、丢126961(7.35223%)。

保留价值是正确上报已经协商的HE关联能力，正式路径无持续吞吐退步证据；没有稳定提速结论，更没有完成TCP1.8/UDP1.9低丢包验收。最后boot a56cceb0、phy26、ARM pending0、NI pending0/failed0（退休3个）、unmatched0，RPS8、EDCA003f642b、C654c8033005、临时ban为空，无诊断模块/测速进程，原Windows5201保持。flash未写。来源worktree artifacts/he-join-full、Mac JOIN-FULL-*（临时路径无效组）、HEJOIN-PRODUCT-*及hejoin-product-results.json。


### 2026-11-05：反向小包下的处理节奏与 NAV 初始化

正常hejoin/phy26，重试制式下限、C62C=20200000重试32、描述符限制和普通HE RTS策略沿现有原厂证据复核，没有新重试参数候选。限定因果实验主下行UDP1.2G、反向Mac→router5212 UDP24B/3M约15625pps，仅PEDIV f_divsel4→8→4（其余C2F/PEAXI字段不变，原GLOBAL_PEDIV位定义已核）。无反向流两侧retry/100=1.8676/1.7296；反向流全频/半频/全频=7.4804/4.0730/7.7835，接收1.198765/1.198839/1.197753G。各轮反向PE约16.1万帧/RXQ约4.8万基本相同，无传输中PM；半频MSDU/MPDU6.91、全频6.82，反向最终少量丢包669（恢复全频196）原样保留，不能说半频零代价。

仅一次反序确认补读已有CAE0 selector7 HE PPDU计数（每次恢复selector0）：半频/全频/半频retry/100=4.1439/7.1678/3.7380，HE PPDU13042/11930/13482，主下行1.198789/1.196246/1.198612G；反向PE160953/161018/161038、RXQ47894/48032/48053，辅流约3M、丢0/38/0。半频HE PPDU更多，平均提交MPDU/PPDU约11.49/12.79/11.09，不能以“更大空口聚合”解释低重试；该粗比值包含重试及起止窗口差异，不是逐PPDU实测尺寸。支持处理/供包节奏会改变双向重试，不证明具体竞争或驱动根因。半频此前已降低饱和吞吐，不作为修复；不继续频率/队列/聚合大小扫描。PEDIV40404、CAE0选择器0恢复，router5212 PID23621已停止。数据ACKCLOCK-*、ACKPPDU-*、各results/shape.json。

沿普通发送保护初始化发现实际缺项：hal_trx_init_8852ce@27cd6c和XB调用rtw_hal_mac_set_nav_padding(hal,0,0,1,0)。实际set_nav_padding@21c0fc的disabled分支只W16 C674=0、C678=0；不改NAV_OVER_TXOP_EN（参考较新offload分支行为不同，未照搬）。当前native遗漏，live C674=00400040、C678=00040000。只对齐低16位，保护高16位SPEC_MBA_HE_PTCL和原其余控制。原厂C/XB caller→wrapper→writer8组验证及扩充既有tools/trace_stock_ampdu_cfg.py通过，manifest新增实际NAV参数与写序列，原AMPDU6组/2初始化输出保留。

同关联64/0/64：TCPdown1.395088/1.465529/1.447426G，TCPup1.667194/1.635385/1.683982G（重传0/31/0），UDPdown1.829828/1.853019/1.831849G。反序0/64/0：down1.474190/1.479857/1.433903G，up1.689725/1.680989/1.685256G均0重传，UDPdown1.845620/1.850509/1.832332G；每轮寄存器读回匹配。首组OFF-A-DOWN前快照还有手机、结束已无，余下所有前后只Mac。无稳定吞吐收益，不把第一组的小提升作为提速结论；31次上行重传没有在后两次ON重复。明确原厂正常初始化依据且无持续退步，保留两次16位清零作为初始化正确性修复。

产品mac-runtime.h仅增加两条write16及说明；无运行开关。模块rtl_ax_ap_navinit SHA256 da446fb3461616953f055394fea2317552dd0753446338445bad4db9ec710ccd，PE仍65869939084cd2c83e12ddd1592c001bf6b0e9097aeec0683138d036ef180e9f。RAM加载phy28，启动读回C67400400000/C67800040000，flash未写。误读a041c674的0不是第二radio寄存器，未用其论证XB现场验证。最终测速随后记录；TCP1.8/UDP1.9低丢包目标仍未完成。

最终navinit/phy28默认首轮上行1.216450G/0重传，逐秒1.62/1.689/1.721/1.711/1.692/1.12/0.307/0.170/0.596/1.573，sleep/wake+27/+28；下行1.417059G。该低谷在前基线已有，未归因、未忽略。唯一复测上行1.698482G/0、下行1.508516G。UDP2G上行发1.826849/收1.816805G，MacCPU97.92%、丢228(0.01445%)；下行发1.999885/收1.851149G、丢125872(7.28017%)。目标未满足，不以一次下行跨1.5G宣称完成。启动及结束C67400400000/C67800040000，PEDIV40404/C300003f642b/C654c8033005/RPS8、临时ban为空，普通BK开启。无新工具服务残留，flash未写。保留NAV初始化对齐，不能称稳定提速。来源NAVFINAL-*。
