# 原厂 WP 生命周期适配与下行测试（第一批）

基线 main 9331c6e1db0c4473a06d28da14795b20484cffc1；worktree `/home/leo/PonWRT-TYFZA1-pe-stock-tx-20261105`，分支 `fix/pe-stock-tx-20261105`。

原厂 ELF SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。实际 `alloc_txring@ddf6c` 请求池为 5G 2048 / 2G 1024；`get_txreq_buffer@e49fc` 只检查空池，没有新版 SDK 的保留八项分支。124 是 native 自有准入上限，不是原厂 WP 上限。

`phl_prepare_tx_pcie@196ae4`、`phl_trx_init_pcie@193928`、`phl_check_wp_tag_resource@196958`、`phl_recycle_payload@196314` 证明硬件 WP 由 DMA 通道和 tag 标识；DMA0 使用 free_wp FIFO，初始化包含 tag0，模4095（FFF保留），next-read==write 表示不可分配。其他通道独立扫描，回绕到1。原厂异常全占用强制回收不作为正常准入行为移植。

## 实现

软件请求 ID 与硬件 tag 分离；NI 请求池取原厂容量，ARM 保留当前独立256槽池。因此这是 PE 架构适配，不宣称与原厂共享 core 请求池完全同构。NI 回收全部 BID 后再返还 tag 和请求；ARM RPQ 转译回原软件 ID。独立 discard 槽不占硬件 tag。每通道 WD264页保留和 TXBD255信用维持原有正确实现；完成环扩大为4096项。仅 TX radio DDR context 扩至512KiB，执行器/IMEM/DMEM不扩大。

移除按前包长度和 peer/TID 变化设置 BK 的规则；保留非 A-MPDU BK。原厂软件额度 BK 默认禁用；没有加入任意 batch8/64 BK。

## 验证

实际链接 PE：5G6144次、2G5120次提交，分别1024/2048在途，乱序 RPQ、tag4094/0回绕、completion4096回绕、EQ无信用/部分回收通过；RPQ 硬件 tag 与软件 ID 不同的 ARM/NI/PS转译通过。六通道发布/信用、双PE/双radio隔离、大HE聚合、WD保留、PS预算、退站/key/BA等待相关回归通过。直接构建和 PE_UPLINK 构建通过，ARM64模块编译通过。高编号生命周期验证选128/1023/2047/discard边界；全枚举 sanitizer 开销过高的运行主动终止，不作成功证据。

最终 PE24644字节 SHA256 `ad271bb64b8f89f2480ca3890f1fb90b41ad613da513f84e549e8179976e7406`；模块 `b15145cb74e33a39960c65634fc3683c4c7e783e4ef0a7e18e3be8bdf9031668`。临时BK/124开关仅存artifacts诊断制品，最终源码无开关。

## 上机结果

Mac en6 2.5GbE→路由器→Mac en0 Wi-Fi160MHz，地址按接口绑定，经测试DNAT避免本机直达。默认单流TCP；UDP发送2G；接收端吞吐如下。无线跨轮波动及同机双接口拓扑不支持将细小差值视为稳定收益。

|条件|TCP 30秒 Gbps|UDP 60秒 Gbps|UDP丢包|
|---|---:|---:|---:|
|新生命周期，BK关，临时准入124|1.433|1.645|17.71%|
|同关联，恢复2048请求池|1.458|1.739|13.03%|
|清理后的最终模块|1.429|1.627|18.65%|
|恢复基线，有效NI快路|1.283|1.722|13.89%|

新生命周期各组未出现非空pending时RPQ长停，最终离场pending0、fault0。124准入实验最大pending124仍持续完成，说明本轮不冻结并非必须扩大同时在途数量；请求/标签复用周期仍不同，不能单独认定硬件tag快速复用就是根因。未达到TCP1.8/UDP1.9目标，不能声称完整修复。

BK同关联ON/OFF/ON 10秒：TCP1.324/1.171/1.416、UDP1.120/1.735/1.651；波动明显，无稳定TCP收益。此前一组有线lan1掉线12秒导致控制中断，缺失段排除。恢复基线的第一次60秒UDP走ARM而非NI，0.306Gbps排除，确认进入快路后重测为表中1.722。

原始数据、命令、1Hz队列样本、模块和诊断源码均在worktree `artifacts/`，汇总 `measurement-summary.json`。第一批结束时已恢复 RAM基线 `rtl_ax_ap_wdinit`，flash与main源码未改。用户后续扩大到全部Wi-Fi原厂细节对齐，继续在同分支分批推进；本提交是可回退的第一批，不是最终验收。

## 第二批：RPQ POH 默认回报

用户扩大原厂细节对齐范围后，将回报聚合从12恢复实际ELF的121，超时代码仍255。`tools/check_radio_report_config.py`执行本机原厂默认选择分支（agg_def/tmr_def及四种默认状态），再比较native写回；默认9414=`a5ff5a79`，重复配置不写寄存器，检查通过。

RAM模块rtl_ax_ap_stockrpr SHA256 `d1c680b345c1dca3dbaca05ddf6a926a468c86c4da95cdcdda5234106daebb6c`；实测单流TCP30秒1.250Gbps、UDP60秒1.520Gbps/23.97%丢包，完成持续前进。单独本项没有提速，保留为完整原厂策略组合的中间版本，后续须验证组合和NI池持有时间。

## 动态发送策略批次（2026-11-05）

按本机原厂 ELF 普通 AP 分支实现每两秒流量策略、A-MSDU 3793/7433/10896 字节及 2/4/7 大包数量策略、最多9个子帧出队、关联厂商识别、WMM 状态和动态 EDCA，并接入 RA 注册/更新/看门狗的 NHM 噪声位。PE 架构保留，ARM/NI 独立累计流量、以源 MAC+EtherType 合并活动流。NI 每批次合并相同流统计以控制执行开销。

原厂对照通过：1920 A-MSDU、82944 EDCA、220厂商分类、156 WMM状态调用；18928 RSSI及36 NHM命令。链接PE验证动态尺寸、9小包、逐字节DMA内容、所有权和完成回收；生命周期/退役检查通过。

最新已测模块 rtl_ax_ap_stockgather SHA256=650f0a9d47fd7c2fe227d0d3f7db471654b8be6d785f398ec2b4aacfdc183fec，PE SHA256=9976fba2e59597177cf7ac06dd88689529c0ca312a73649cf4904f2ee03fc0ae。TCP30秒1.466Gbps，UDP60秒1.546Gbps、丢包22.65%，未见持续pending冻结；未达到1.8/1.9Gbps目标。中间动态版本1.243/1.269Gbps不用于宣称收益。

实际Mac关联被原厂IE规则识别为Broadcom(3)，160MHz下EDCA为0138642b。仍未对齐：WMM每流请求配额、完整TXSC缓存替换生命周期、聚合等待细节、XB环境采样、RF周期跟踪和完整PHY包统计。此提交是可追溯中间批次，不代表全面对齐或吞吐修复完成。

## 8852C RXDCK 对齐批次

修正换信道以及该次RFK中DPK后的重复RXDCK：原厂is_chl_rfk时跳过偏移修正。普通offset读取去掉原先多读一次second+1的差异。增加独立温度模式，偏移差>10最多追加3次shot后才修正；SCC两路径校准完成且5G温差>=12触发。复用PE暂停/恢复适配原厂TMAC暂停，保留原厂20ms静默等待。

证据：实际halrf_rfability_init C=0088e17f/XB=0038e17f，rtw_rfk_ability数据默认ffffffff；实际final_cap_decision 1d0f7c..1d0f90在默认值选择hal硬件cap，halrf_set_final_rfability再取交集。RXDCK普通/换信道/温度的120组RF序列及324组触发条件与本机原厂ELF一致；测试RF读写使用模型，尚未在真实温差>=12下观测周期触发。换信道重试及1332组DIG带宽验证通过。

单独换信道模块dd5cf754bb5bedcf8fcc2d13d369d25ad75bf6f12103416e0cc465e1e4f16791，UDP1.521Gbps/23.93%，有效NI TCP补测1.477Gbps；初次TCP0.731Gbps中NI只发70包、ARM新增270795个请求，排除该数据。加入温度模式后模块rtl_ax_ap_stockthermal SHA256=adf3e7fcd6f9a5c6ccf0d5d2cd26c65c75b704a33bc5bfe26e4fddb13f426a82，TCP1.506Gbps、UDP1.532Gbps/23.37%，NI有效。运行温差不足12，RXDCK_runs=0；无pending冻结，未证明吞吐收益。

实际BTC RFK回调无H2C发送，仅首次START100ms延时；当前native启动尚未对齐该首次通知延时，温度周期代码不额外发送不存在的通知。原厂周期RF仍有XB OP5K未适配。

## 持续PHY报告批次

实际C/XB hal_start分别277ca4/27e788附近传入enable=1、append=0e、filter=20；实际rtw_hal_mac_ppdu_stat_cfg→cfg_ppdu_status输出CE40=2f、9c18低2位1。当前12+20ms/2s采样改为原厂2f持续采集；扫描沿用CRC正确的外部beacon报告，不再关闭采样开关。18928RSSI/36噪声H2C对照和构建通过。

RAM模块rtl_ax_ap_stockphy SHA256=92e8bffd287a8726684fcb1f69585b2a8987542d7e8ed3e9ae403ea09c5109c3。有效NI TCP1.467Gbps、UDP1.546Gbps/22.66%，未达标。初次TCP0.728中NI只144包、ARM新增314257请求，排除。RSSI样本14473，PHY总报告281302，持续配置读回2f。CPU抽样最大核没有饱和；没有证明吞吐增益。

## NHM 统计修正

原先C最后raw bin11错误合并bin10；实际halbb_nhm_get_utility 2a61b0..2a61bc合并到sw_result[0]。另外原先utility使用未减external loss的阈值，实际nhm_set保存并使用减loss后的阈值。修正两处，并将XB独立12桶NHM周期接入DM（同一CLIENT CR，XB损耗项0）。

504组C/XB带宽/损耗/计数桶输入：旧实现112处不同，修正后0不同。原厂CR初始化→get_result→utility→noise exclusion→ratio串联执行，与编译native一致。真实模块rtl_ax_ap_stocknhm SHA256=4f7fccaab8818a193e908057ff4e1e95a46bd8885ae63c8431a206b5614781a9，TCP1.492Gbps、UDP1.574Gbps/21.30%。无pending冻结，未达标。

最近GATHER/THERMAL/PHY三组UDP，NI发包8020319/7946148/8020657，与客户端实际收到8009635/7934704/8009428相差约万包，RPQ失败均0。约220万以上丢包发生在NI发送前或其背压上游；这一观测不能独立区分无线背压和PE供给极限。

## XB OP5K

按实际原厂33e978/33f3b0/33f7bc补齐16点ADC扫描、连续零区间/中点/±5设置限制、最多两次尝试、两次温度平均、低于基准26且相对上次温差>5时校准、回温后恢复带宽默认值。保留PE架构，非CV0在带宽设置后记录默认值，周期调用。20组原厂序列与状态对照通过；换信道重试/DIG1332继续通过。

RAM模块rtl_ax_ap_stockop5k SHA256=be7350f8e62353566dad305997638f58d0d71c23d3b773feffcc59c2b826d334。原有2G接口配置禁用；创建临时monitor运行5秒再删除，2G温度30/29、OP5K_ready=1默认4/4、周期2次、NHM有效1次。温度未低于26，没有硬件触发扫描；扫描写序列已离线原厂对照。没有为纯XB变化重复5G长测。

## 默认 SWQ 聚合调度对齐

实际 `txsc_init@eb444` 默认 txsc_amsdu_mode=1；`rtw_core_tx@e8c10` 在tx_mode=0调用 `txsc_amsdu_enqueue@eaf00`，128帧分支进入 `txsc_amsdu_timeout_tx` 连续出队。现在PE按同样的字节/帧数触发与128批量/num尾部条件发送，替换了“长度<=128且少于8帧固定4ms”的自定义规则；聚合禁用不再强加短包等待。普通超时10ms，5G且16<接收Kibit/s>>10<680、发送Kibit/s>>10<16时4ms。

原厂默认SWQ路径实际内联于 `txsc_apply_sc_cached_entry@ee5bc`，选取上限按以太帧len累计。此前采用另一条 `txsc_amsdu_dequeue_txq` 的len+12规则；本批改为默认SWQ规则，保留协商MPDU长度、9帧和大包数量边界。PE继续使用BID链和WP异步完成，未搬原厂skb和线程架构。TCP ACK合并分支尚未对齐。

验证：`tools/check_stock_amsdu_queue.py` 执行实际ELF，864组enqueue大小/帧数/128边界与387组连续出队条件一致。linked PE覆盖9短帧触发、10/4ms、TSF回绕、禁用聚合无等待、128批量排空、PS及退站；聚合报文/DMA/padding、6144次WP随机完成和250休眠包隔离通过。IMEM32768/DMEM8192、栈保守上界6880字节。

RAM模块 `stockswq` SHA256=e53abe8af3cdd7d929f941f48d0e3895b664b90f8ee9cb44d645332aa0c70368，PE=afd1f8f718ae2aba6be65cff532474393789ffea1fd907de7f1eb04fde7ea128。60s UDP=1.530Gbps、丢包23.49%；30s TCP首次0.730Gbps但NI仅125帧、ARM+304129请求，不能归因到PE；附加流表观测复测TCP=1.433Gbps，双向HW_OFFLOAD且数据流age6/NI持续增长。UDP→TCP短复测均进入PE，没有稳定复现准入故障，保留未解状态，不再重复盲测。本批未解决1.8/1.9目标，无pending停滞、RPQ失败0。

CFO配置补充：实际supportability函数可执行确认共享/独立晶振掩码；实际final_cap中未见SDK为第三颗radio设置独立晶振的分支。原机最终is_indiv_xtal仍缺实测，不将native默认0当成已证实原厂值，不强开运行跟踪。

## WMM 每站/每 AC 请求配额

`rtw_switch_wmm_mode@59e0c` 的资源配额是max_tx_ring_cnt/活动AC峰值，`_txreq_lmt_check@e68d8` 在每站AC未完成请求数>=配额时拒绝。ARM与NI现在分别发布各自MPDU请求计数，检查合计；NI直到完成的全部BID归还后减一，不按聚合子帧数计请求。拒绝的NI聚合通过既有discard所有权回收，PS服务失败仍完成对应generation。两侧沿用原厂check后计数的语义，非跨CPU原子reserve。

原厂320组pending/TID边界、156 WMM转换、既有1920聚合/82944 EDCA/220 vendor对照通过。linked PE覆盖ARM+NI合计、AC/peer隔离、拒绝时EQ无credit不提前归还、6144次请求回收后AC计数归零、PS及聚合/IMEM通过。

RAM测试初始模块bf174255...：TCP30s1.429Gbps，UDP60s1.591Gbps/20.45%丢包，单AC配额0、无pending冻结。设备本地两条各15Mbps的DSCP0/46流量实际进入DMA0/3（Linux把EF映射VO），quota0→1024→0，两流接收零丢包。此前从LAN转发的DSCP双流全部TID0，不能算多AC验证；已定位FE默认COS0使NI未保留IP优先级，下一批对齐真实原厂分类。

最终代码将quota计算收敛到已测traffic helper；重编模块9a4e19c138a1e7a864755686ff81d9d4920695e505e9d2dc9dc73efbc51d335e，PE与RAM版本完全相同82b7b778c0c2669e6c0645394b495cfd7285eec0e291548cc5a661999ccc11bf（26396字节）。最终helper调用位置变化编译通过，RAM仍初始WMM模块，未把未装载版本称为实测。

## NI 默认 QoS 与新 TID 的 BA 启动

实际tos_to_up@de93c默认CTC0/custom mapping0/force_qos0（MIB字段初值已读ELF）按IPv4/IPv6/PPPoE报文优先级再映射到0/1/4/6，原厂不会把FE COS0当作最终无线TID。native NI现按该入口规则分类，1024组实际ELF报文对照通过。ARM/mac80211仍使用Linux分类（例如EF→VO），尚未宣称所有自定义QoS映射已统一。

NI新TID有活动时触发已有wiphy offload工作，按正常授权/能力/retry门槛启动BA。原厂issue_action_ba@906dc直接读硬件last_seq+1；native tx_peer_ssn从PE的next_seq取快照，去掉整个TID排空等待，避免持续NI流使BA启动等不到。linked分类、WMM/PS、6144所有权随机完成、聚合通过；普通BA触发测试通过。

RAM stockqos SHAec0dbe5b1671d15df6ac5524c3ad91e3b8e6184fec5103f9cf1f622ef787ee6f：两条LAN下行DSCP0/32实际TID bitmap0011，TID4 BA size64在14878.494建成。第一次采样脚本误把WMM_request_quota替换成QOS_request_quota，缺配额行不能证明启用；修正后的短复测实际读到1024→0，无quota drop。两条50Mbps流第一次各7.90% loss，第二次各13.31%，同期retired_EPP增9204且sleep/wake计数增长，BA早已建成。低速丢包并非一次BA启动窗口，也非WMM拒绝；继续核对原厂PS缓存。单流TCP30s1.357Gbps/UDP60s1.458Gbps loss27.06%，无pending冻结，未提速。

## 原厂休眠缓存总量

实际 `os_tx_enqueue_for_sleeping_sta@1acec` 检查dvobj txq_total_len>=mib.txq_limit；实际mib_table偏移2740的默认limit=2048，未见native每peer256/总512限制。PE已移除这组自定义限制，休眠入队按总2048控制，不在后续选择时因awake队列使总量升高而驱逐既有睡眠缓存。当前PS超时仍使用Linux的listen_interval/beacon预算，未宣称原厂全部aging语义完成。

144组实际ELF入队条件通过；linked PS预算/expiry/TIM、grant竞争、阻塞EQ、2048休眠帧+另一醒站实际发送、6144请求回收通过。初次PSQ双流虽零丢包，但只有一半走NI且无传输中休眠，不能用于归因。第二次同关联PSQ-RETRY：两条50Mbps流均走NI（约69000帧）、客户端sleep/wake多次增加，两条均0 loss、retired_EPP保持0，支持该修改解决已复现的QoS低速休眠丢包。WMM quota1024，drop0；长WMM timeout仍可能在采样结束时保持，未说本次20秒采样已见退出。

RAM stockpsq SHA4b5a085757ccc537d615ef7217ba12673fc644049d21b92416fdef0152d075b1：TCP30s1.361Gbps、UDP60s1.482Gbps/loss25.89%，目标仍未达到，pending正常排空、RPQ failed0。所有本批额外服务器/客户端和stock_psq_probe NAT表已清理，继续保留测试基线的downlink_probe和RAM候选以便下一批。


2026-11-08 integration: retained the confirmed four-peer preload lifecycle from 8ee15dc. Preload uses word5 bit15 (RTL_AX_TX_AGG_PRELOAD), matching main 2ad19d9; word4 bit22 remains raw sequencing and bit23 remains STA direction. Historical measurements do not qualify this combined tree.


## 2026-11-08 全千兆设备：本机造流与 ARM 请求额度

当前目标全千兆设备 MAC98:1e:89:61:68:d0、flash0.8.3-beta；Mac en5维持外部运营商供网，en0连接临时WPA2网络TYFZA1-Stock08，192.168.1.171。它不同于之前d0:5b的2.5G设备；不要把两者启动失败或吞吐混为一组。TTL1120必须每次打开明确115200，9600时空输出不能作为设备死机证据。

同一构建cfg80211/mac80211/候选radio热加载成功；更换cfg80211后需重启wpad清除旧nl80211 family状态，未修改hostap脚本。初始候选保留原厂RPQ121，但ARM请求仍沿用旧PE96上限。单关联12→121对照：TCP1.282492→0.411254Gbps均0重传，UDP0.369013→0.124303Gbps均0报告丢包，原配置121恢复。该不匹配是明确回收等待问题，不能将原厂121独立套进旧请求额度。

最小修正仅tx.c三个门槛：ARM已有256请求池用224停止、128恢复；保留32余量，NI2048请求/硬件tag独立、PE共享TXBD检查不变。没有更改PE二进制、WD/密钥/序号/回报超时。ARM模块编译、PE共享环full/pause/双radio publication、6144随机所有权回收和preload生命周期通过。modpost仅原有缺MODULE_DESCRIPTION警告。

最终host-credit224.ko(去debug)SHA91e88c962a7734fdfaa1b5a70614ad1991e75f3e808990e71d4316696369114d，PE2124b3ff5f4b0a7b0fbc12f3cbdd7c4289c2c49b069b3f04e122543255030667不变。RAM加载rtl_ax_ap_stock08/phy2-ap0，bootcff17fbb-76bc-4dce-8616-2cbbe4fdb544，原厂RPQ00ff0079。30秒本机单流TCP下行1.356628G/0retrans，上行1.055346G/249retrans；UDP下行使用GSO+4MiB socket，1.376314G、lost0、sender1.376349G，CPU接近1核。三个方向提交/回收增量一致，unmatched/polluted/full/RXQ_RDU增量0。持续低负载背景ARM pending2，不谎称全局pending0。

UDP原默认socket下只有0.125G；GSO本身0.175G，GSO+4MiB达1.409G，说明待回收socket缓冲也影响原厂批量策略。4流并行未解决目标：TCP下1.095G/up0.851G9052retx，UDP下1.569G4.23%loss/up0.951G17.20%loss，不能用于验收。全部数据文件位于artifacts/gigabit-*、rpq-credit-ab.json、credit224-*。

现有VP11NI注入工具尝试一次50M反向TCP超时并伴随后续确认的设备重启；无连续崩溃日志/pstore，不归因具体驱动。重启清掉所有注入模块，Mac198.18.255.1临时路由已删除。该工具停止使用，不能把这轮算成吞吐。之后普通本机候选测试boot保持cff17fbb；未写flash。

总目标仍未达：原厂策略候选+224额度只构成进展；本机峰值受CPU/旧内核路径限制仍需定位，完整Wi-Fi原厂对齐与TCP1.8/UDP2.0双向低丢验收都未完成。下一步核对目标0.8.3内核的NI/miss/GRO实现与现main差异，避免用新驱动覆盖旧内核而假设全栈已一致。


### 完整固件与硬件路径（2026-11-08）

独立OpenWrt构建目录/home/leo/PonWRT-TYFZA1-wifi-stock-build-20261108，从现有构建复制并修正1103个绝对软链接及48份生成pkg-config/libtool路径；仅副本LuCI旧补丁恢复后重应用当前补丁。宿主APK首轮因两份endian.h包含路径产生未展开字节序宏，清理该host包后成功。构建绑定b8cc8b8，镜像SHA6705cbfef3ef48f24b83c73c5dc5e2944b7b95205ffbb80256bf0879b022bc59，设备sysupgrade-T通过后保留配置刷入。

当前运行boot2a847704-b124-454e-aabf-ad4f9d359168、flash0.8.5-beta。校验闪存ubi0_6的FIT与新镜像逐字相等，FIT解压kernel等于KDIR/vmlinux二进制467b7186；运行wifi_receive、rtl9617c_ni_wifi_lookup、napi_complete_done及gic_handle_irq地址匹配KDIR/vmlinux.debug。/sys/kernel/notes为84个零字节（objcopy剥离），原先用其hash核对是不适用的工具检查，已纠正，不称启动旧内核。linux-6.18.55/vmlinux最终为initramfs构建，也不能代替KDIR/vmlinux.debug比对。rootfs模块rtl_ax_apa801914e、mac8021187f8ca17、cfg802115d61836a与构建代码数据段一致，打包去build-id；sysupgrade root尾部10字节0填充已确认。

全栈普通本机10秒默认TCPdown1.337001G/up1.350001G，均0retx。UDP默认socket仍不足，GSO+4MiB下行1.385415G/0loss，上行GRO+DF+4MiB1.342158G/.5903%loss，两端单核约100%。sendfile下行.337G/0无收益，不保留此测试选择。

重新按完整运行内核编译既有NI工具后，50Mbps低速冒烟13250包全部PE发出/回收，0retrans，boot不变。这套工具与此前旧内核试验区分。它用本机真实iperf报文经VP11PON-source模拟进入FE/PE，再到真实Wi-Fi；不测试光口。

15秒硬件路径TCPdown1.180584G/32retx，up1.392157G/0retx；UDPdown实际send1.887205G、recv1.375163G/loss27.0207%，upsend1.466223G、recv1.462758G/loss0（发送端受限）。低高负载边界：1.3G发送1124236包、PE/recycled同数、loss0；1.9G实际1.888G、发送1633501包，PE1192954包全回收、recv1.377690G/loss26.8932%，QMno_buffer读清计数饱和65535，failed/retired/fe_drop/l3te_tail_drop0。不能把65535当确切全部丢包数，但入口缓冲耗尽与PE发送前缺失有直接证据。

候选补回main dc6c808的64入口/每发送turn一次批量，仍保留本分支原厂QoS/动态聚合/WMM/PS2048。两PE源文件变更，gather/sharedTXBD/PS/6144ownership/IMEM均通过，尚未完成实测，不宣称提高吞吐。当前RAMrtl_ax_ap_batch08 SHA09c77bccc00fc28f609228a946dfca6b05021e024187409f222d6aa0bc824f5c，phy2-ap0等待CAC；flash仍b8cc8b8。NI工具已停止以便reload，Mac198.18.255.1hostroute仍在，需后续清理。生成overlayPE头未替换候选前不得构建正式固件。


批处理A/B/A结束：baseTCP1.180584G32retx/UDP1.375163G27.02%loss；batchTCP1.233945G218retx/UDP1.456170G22.016%loss；restoredTCP1.174191G131retx/UDP1.379901G25.8505%loss。UDP sender分别1.887/1.870/1.863G；不能当作低丢验收，但同拓扑恢复后吞吐回落，支持约5–6%容量改善。保留有界批处理修正，不丢弃此前原厂规则。生成PE头已同步候选。

完整linked主循环gather、publication、6144ownership、PS、RXQbackpressure和RPQ通过，IMEM26976/32768、stack6936/8176。额外旧test_pe_epp_batch直接函数测试读未映射内存，扩大DDR/更新分类/return哨兵仍未通过；停止该工具修复，原脚本恢复HEAD，失败适配在artifacts/ni-batch/epp-unqualified.py，不列入通过项。未把工具失败解释为硬件正确或错误。

最终RAM恢复已刷b8cc8b8原模块rtl_ax_ap/phy4-ap0，boot2a847704保持，AP160授权、ARMpending0、unmatched/polluted0。NI feed/flow/sink全部停止卸载，主机198.18.255.1hostroute删除，普通5218iperf核对PID停止。Mac有线供网保持。批处理已实测但尚未重打固件，因此磁盘源码与已刷image6705cbfe存在这两处批处理差异，必须在后续交付中明确。当前完整目标仍未达，不以小幅进步缩减验收。


### 2026-11-08 batch固件落盘与NI载荷构造

b8245ec完整固件SHA b5a40771409b24cb3b00eb715c91f1fd56be474638fa85477c36191fb9b22adf已保留配置刷入，全千兆目标boot c0eb71c3-9fc9-4b50-9102-def584529974。Flash FIT逐字与构建一致，模块rtl_ax_ap27c4b092、mac8021187f8ca17、cfg802115d61836a全部匹配；内核本体467b7186与前一版本相同，修改仅radio/PE。

从main2ad19d9移入独立ni_build_payload，只改NI报头和地址表准备分支，保留本分支原厂QoS/流统计/聚合/PS/WMM/请求生命周期。实际linked AP/STA/mesh头、gather字节/DMA/padding、sharedpublication、PS、6144ownership、RPQ/IMEM通过。候选PE1d0bcbb5，27504字节/stack6936。

同拓扑A/B/A：已刷batch TCP1.234905G162retx、UDP1.454894G23.19%loss；独立NI候选TCP1.249419G0retx、UDP1.482633G20.89%loss；恢复batch UDP1.452667G21.69%loss。UDP各自发送1.897/1.876/约1.87G，接收能力约2%提高，支持保留小幅改善；仍无达标证据，不能称低丢高速完成。卸载/恢复正常、boot不变。候选仅实验副本加载，当前flash及RAM恢复batch，后续源码继续承接该构造；artifact direct-ni保存完整证据。
