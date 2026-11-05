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
