# TYFZA1 WiFi 吞吐排障交接

截至 2026 年 10 月 21 日。用户要求停止当前排障并转交其他 AI。**吞吐根因尚未闭环，目标未完成；设备已经恢复到已验证模块，当前没有正在运行的实验。**

先读本文件，再读同目录 CHECKPOINT.md 的后续记录及 JSON 原始分析。CHECKPOINT.md 是按时间追加的历史，前面的“正在加载”“服务仍在”等中间状态已失效，以本文件和 handoff-final-device-state.txt 为准。

## 任务边界和设备连接

目标是在这台全千兆口 TYFZA1 上真正修复 OpenWrt WiFi 上行及下行 TCP 吞吐，并核对 FE 硬件加速、各核 CPU 和各层丢包。用户期望在约 2.4 Gbps PHY 下接近至少 1.7 Gbps。旧机、仅到网卡的模拟量或只提交 DMA 的数字不能作为当前设备验收。

约束如下：

- 只使用 Mac 与光猫；手机和小新已撤出，不再要求用户提供第三个端点。
- Mac 外网使用 en5，默认网关 192.168.71.1。Antify 提供必要代理，禁止关闭或修改。
- Mac 测试 WiFi 使用 en0，IP 192.168.1.212，MAC 06:7a:fb:78:b2:87，SSID PonWRT-Test-5G。
- 光猫没有接光纤。现有模拟可覆盖 NI 入口、FE、PPPoE 封装或解封装、加密 WiFi 和真实应用，但不包含光学 PON、拨号协商或自动 WAN 建流。
- 保持合法 DFS 和正常 CAC。已修好 CAC，当前使用 44 信道、中心 50、HE160。不要重新关闭 DFS。
- 原厂源码和这块板的实际 ELF 指令是主要依据。不得去掉序号、PN、重排、所有权保护来隐藏问题；不要盲调预算、扩大缓冲、关掉丢包检查或全量构建测试。
- 未授权新子代理、推送或给其他会话发消息。参考会话 01a15957-8c5e-7ba2-adcf-746204ced6ed 仅只读查看过。

路径与控制入口：

| 用途 | 位置 |
|---|---|
| Mac 工作区 | /Users/leo/Documents/Codex项目/TYFZA1 |
| VM 权威 Git 仓库 | /home/leo/PonWRT-TYFZA1 |
| Mac 的 VM 挂载 | /Users/leo/OrbStack/ubuntu2404/home/leo/PonWRT-TYFZA1 |
| 原始现场记录 | 工作区/现场记录/20261020-local-wifi |
| VM 构建及诊断产物 | 仓库/artifacts/own-wifi-20261020 |
| VM 命令 | orb -m ubuntu2404 bash -lc 'cd ~/PonWRT-TYFZA1; …' |
| 光猫 SSH | root@192.168.1.1；现场记录目录的 router.py 已配置密钥和严格 known_hosts，命令从标准输入传入 |
| 串口 | /dev/cu.usbserial-120，115200；写入按字节约 3 ms 间隔 |
| 已授权电源继电器 | /dev/cu.usbserial-140，9600；断电 a0 01 01 a2，通电 a0 01 00 a1 |

OrbStack 数据依赖 Leon Disk 外置盘，请保持连接。重新链接 ko 后，Mac 挂载偶有 Stale NFS handle；用 orb cat 从 VM 权威路径取二进制，不要因此重复构建或重复加载。SSH 可用时优先使用 SSH。串口标记可能被 dmesg 插入，解析失败不等于命令未执行。

## 收尾时核验的真实状态

当前 boot_id 为 d871034d-5a08-4234-830f-bec74ff2ac18，内核 6.18.55 第 4 次构建，发行标记 0.8.0-beta。设备无外网校时，设备墙钟与 Mac 日期不同，跨记录应使用 boot_id 与 uptime 判断，不能按 DUT 日期直接拼时间线。

| 项目 | 已核验值 |
|---|---|
| 磁盘 radio SHA256 | 5053c0364cae48b766e6b3e15fa024b3cf0c87351d42dc1ee9575577f1c38919 |
| 已加载 radio 构建 note SHA256 | e5e16d4988b70d29b26c7b6a75c6e290d990018f42fdd5d10f586f0abb787841 |
| 磁盘 mac80211 SHA256 | 9780d6046b9e2021439a785c7b6c68ff569fabf84afc271312bf013badf4343b |
| 已加载 mac80211 note SHA256 | b5bf6b4b4beeacd9283b63e9519c76e7918d19a4f7bcf25e1f90eccf3bf72b87 |
| 无线 | 44 / center50 / HE160，CAC0，DFS enabled/hardware1，events0 |
| 测试站点 | MACID1，06:7a:fb:78:b2:87，关联 ap5 |
| 当前 PE_RAM | 0x04f80000；这是本次启动值，加载观察器前必须重新读取 radio_tx |
| NI IRQ23 | smp_affinity=f |
| ap5 RPS | 2 |
| printk | 7 4 1 7，正常值 |
| 临时资源 | 无观察器、sink、PON fixture、硬件回环模块及 iperf 服务；无 policy220 或临时 nft 规则 |
| LAN1 | 回到 br-lan，正常无外接链路 |
| Mac | 外网默认路由 en5，Antify 两个进程在运行，mbuf 当前健康 |

收尾已删除 Mac 上仅用于测速的 198.18.0.1 临时主机路由；198.18.0.2 临时路由也不存在。复现模拟流量前需重新添加仅指向测试主机、经 192.168.1.1/en0 的 MTU1500 路由，不能改变 en5 默认路由。硬件环脚本的 route change 同样依赖该前置路由。

收尾未刷固件或写入新生产模块。唯一最近的冷启动是恢复失败阶段标记候选，磁盘始终保留上述 5053。完整核验见 handoff-final-device-state.txt 和 handoff-final-mac-state.txt。

## 已提交的修复和验收边界

最近生产代码提交为 0eb92b1。后续若出现交接文档提交，不能把文档提交当成新固件已经部署。

| 提交 | 内容与边界 |
|---|---|
| f89081e | FE 身份键不匹配、把 PCIe DMA cookie 当作 PE 可直接读的包头地址、快慢收包排序与同步交付、退出时撤销路由等修复。都有代码或指令反例；不代表全部吞吐根因已解决。初期 current-firmware-identity.json 已证明新设备当时装有相关 FE 内核、header staging 和 RX_INLINE 修复。 |
| 42fe458 | 修正 PPPoE 测量，使用真实应用收包、序号与完整载荷核验，保留发送、FE、NI、应用各层计数。 |
| ba54e44 | 未变化的 RX 发布计数不再反复全站点扫描；不跳过待恢复重排状态的处理。 |
| 7ea9fe0 | CAC 作用子带修复及原厂 DFS 状态处理。 |
| 71ae24a | 记录已验证 5053 无线模块的持久化与回滚。 |
| 0eb92b1 | 补回原厂 L2QM private reservation。只提交源码并编译单个驱动对象，尚未部署新内核或验证新内核启动。 |

CAC 修复按真实信道 RADAR 标志确定需检测的子带，在分类前过滤不属于 DFS 子带的报告，保留原厂脉冲判别。原厂实际指令回放、正常完整 60 秒 CAC 和冷启动均通过。不要重新绕过这一修复。

L2QM 的原厂 ca-ne.ko SHA256 为 16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7。实际 aal_l2_qm_init 在 EQ1 配置 4096 个 64 字节页、private29，profile0 为29；原生初始化遗漏后两项。硬件 TCP 回环中 L2TM_BM_NOBUF_DPCNT 在偏移 0x216c 的增量与重传一致，而 QM no_buffer 与 NI tail 均为0。临时原厂值 A/B/A 为 540/526/540 Mbps，L2TM 丢包为67/9/96，支持补齐缺失配置，但没有吞吐提升证明。临时值已经恢复，当前内核仍是原初始化。

## 最新有效证据是 PE 待处理段积压

完整有效记录为 rx-pipeline-stages-samples.txt，SHA256：

    201aaa854767480ea8b4733da8c644c010abb0885846ad1b1a5ac8f37bc1a7be

该轮仍为已验证 5053，仅使用只读观察器。通过当前 ELF 的 DWARF 获取驱动原有 coherent CPU 映射，不对活跃 payload 做 cache maintenance，不写硬件寄存器。在 CPU2 每250微秒采样，记录前后游标，排除游标在读取过程中变化的样本。39907 个样本中35302个游标稳定；其中35289个还符合缓冲数量512至514的守恒范围。单次采样主体中位5.48微秒，主体耗时约占四核总量0.566%，不包含全部定时器外围成本。

关键字段：

- HW 高位是硬件生产游标，低位是已发布归还游标；scan 是 PE 扫描游标。
- w、p、r 分别为工作环写入、处理、回收游标，均按256取模。
- queued=(w-r)&255，unprocessed=(w-p)&255，recycle_wait=(p-r)&255。
- idle 是剩余可替换 RX payload；ARMw/ARMr 是慢路径投递和完成游标。
- barrier 为0不必然意味着完全没有 ARM 工作。因此严格筛选还要求 ARMw=ARMr。
- tag 是下一个预期 DMA tag 的前驱；info 中的 tag 必须按8191绕回1。desc 与 addr 比较实际描述符指向和当前槽映射的 payload DMA 地址。

在全部35302个游标稳定样本里，硬件已报告有包时，没有发现 DMA tag 未就绪，也没有发现描述符地址不匹配。接近满队列的3062个稳定样本里，总积压中位255、未处理中位254、处理后待回收中位1、idle中位2、硬件待取中位255、归还滞后中位0、NI待提交中位0。

只看 barrier=0 有605个近满样本；进一步要求 ARMw=ARMr 后是455个，再排除缓冲守恒不合理的中间快照后是450个。不要混用这三个数字。这批严格样本队首均为数据包，归还滞后与 NI 提交占用中位均为0。

首次积压的先后顺序如下，时间相对观察器启动，不是 TCP 启动：

| 时刻 | 观察 |
|---|---|
| 829.747 ms | 软件工作环积压37，其中未处理36；ARM屏障1，硬件待取9，归还滞后0 |
| 848.497 ms | 软件工作环积压137，未处理136；硬件待取0，归还滞后0 |
| 873.997 ms | 软件工作环积压242，未处理241；硬件待取0 |
| 899.747 ms | 软件工作环255，硬件待取246 |
| 900.247 ms | 两个环均255，首次观察到 RDU 增长 |

**这轮最先出现的是软件待处理积压，随后才是物理 RX 环耗尽；它最初发生在 ARM 屏障期间。不能把“后续无 ARM 等待仍有积压”改写成“积压从来与 ARM 无关”。**

另有505个相邻区间，共126.248 ms，满足两个端点游标稳定、已有积压、无屏障、ARM生产和消费游标均不变。统计处理速度约26915 MPDU/s，扫描约23335 MPDU/s。它说明已有积压的恢复能力有限，但不足以单独证明某条 PE 指令就是根因，也不能把 MPDU/s 直接换算成最终 Mbps。

该轮6秒真实上行应用结果392.594 Mbps、0 TCP重传、FE=sink206685、NI tail0、QM no_buffer0、RDU事件50458。ARM数据5141，恰为 unseeded4962加SN失配179；PN_old和NI_full均0。扣除测速服务后的剩余 CPU 为四核总量14.137%，仍含 NI、内核、ACK 和观察器。

复算入口为 analyze-rx-pipeline.py；详细结果在 rx-pipeline-stages-timing-analysis.json。原始样本完整保留。RDU 是事件数，不能当丢包数。

## RPS 和收包调度已经查到哪里

已部署 RX_INLINE，实际 hw flag57 为Y，mac80211 交付直接走 netif_receive_skb_core；不要再漏查这一既有修复，也不要重复提出在初始 RPS 前新增入口。

另一个入口是 NI FE miss 返回：rtl9617c-wifi.c 的 wifi_receive 在 miss 时重新调用 netif_receive_skb，仍可能进入 RPS。模拟下行最初没有反向 ACK 精确流，曾有119665个 miss。补上精确反向 FE 后 miss 降到123，但吞吐仍约1.15 Gbps。

该轮全局 NI tail899，恰等于下行 FE到NI 缺口54加ACK接收端缺口845。随后只将临时 sink RPS 指向CPU1，ACK FE=sink74534，ACK缺口0，但下行数据缺口270、吞吐1.099 Gbps。RPS 确实改变了端点 CPU 分布；这不是生产转发已修复的证明。

上行 sink RPS0/2/0 短对照为374/590/538 Mbps，回验不回原值，不能声称稳定提升58%。

原厂真实 phl_register_trx_hdlr_pcie、phl_register_handler、平台初始化与调度指令回放确认 RX 使用高优先级 tasklet。之后使用同一诊断 ko、只在加载时固定上下文、同一 spin_lock_bh 与计时实现的有效对照为：

| 固定上下文 | 上行 Mbps | 下行 Mbps | 上行 RDU事件 | 上行 NI tail |
|---|---:|---:|---:|---:|
| 普通 system_wq | 548.119 | 1211.543 | 66006 | 0 |
| 高优先级软中断 system_bh_highpri_wq | 543.468 | 1215.031 | 73618 | 406 |

四轮均 reentries0，软中断模式 BH_calls/calls=1，普通模式为0。软中断缩短部分服务时间，但未改善吞吐，因此没有采纳生产调度修改。该对照仍有跨模块加载与 RF 时间差，也包含 Linux 端点；它不等价于穷尽验证所有原厂 tasklet 机制。

较早的在线跨工作队列切换试验无效：它违反 Linux workqueue 的不重入条件，可能让同一 work 并发执行。已经明确记录并停用。旧 BH_calls 累加了 in_serving_softirq 的256位掩码，需要除以256；后续固定版本已改用布尔计数。不要再运行在线迁移版本。

## 其他直接证据及尚未解决的问题

- 原厂实际重排和释放指令对首个 SN 失配记录的10次状态转移，与 mac80211 全部一致。该事件是正常乱序加 Retry 补回，不能去掉空重排缓冲保护。此前 SN失配加unseeded解释约99.98%的 ARM 数据，表示调查方向，不等于状态同步代码已被证明有错。
- 无第三个端点的真实硬件 TCP 环已打通：Mac两端真实socket，数据和ACK各经过 WiFi、FE PPPoE PUSH/SNAT、LAN1内部 PHY 回环、FE POP/DNAT、WiFi。DUT没有bulk测试进程，CPU约3%。但它包含双程空口和1Gb PHY，约580 Mbps不能当单向无线极限。
- 已捕获真实 MAC 完成停顿：TX DMA 已消费、RPQ与ARM报告队列已空，仍有124个 WP未完成，随后NI缓冲积压与尾丢。它定位到无线MAC完成生成之前，未能区分RF重试、BA调度或报告计时。不能仅据此强关PS。
- L2TM no-buffer、QM no_buffer、L3TE tail 是不同层的计数。只盯后两者会漏掉已找到的 L2QM 问题。全局 tail 也必须区分上下行数据和ACK。
- ATU shadow 是已知未闭环风险。此前一次启动核查1060个分配范围无冲突，但不覆盖后续每次重载或未来分配。当前新boot的所有范围未重新完整盘点，不能沿用旧结论。
- Mac 曾发生 mbuf 压力，已重启并恢复；收尾时健康。不能通过关闭 Antify 或反复重启 Mac 替代定位。
- 原厂 HostSpeedUP.ko 的符号包含 ACK、窗口与TCP调节项；未运行或采用，不能当作正确吞吐修复。PE时钟未发现确凿配置反例，不应盲改 PEDIV。

## 失败候选和诊断故障必须保留

以下均未采纳或持久化，源码与失败记录仍在原路径：

- cached RX 在一次上行改善，但下行退化且 RF/乱序事件不同，未采纳。
- PE 队列 metadata 未证明改善，仍是未提交工作区实验；生成头保持旧代码，切勿盲构建整个工作树。
- 固定16批补环、eager补环退化，已撤回；不要重复加载。
- header snapshot 候选曾触发 SER/H2C故障。没有证据说明是漏链接 .early；二进制包含该段，不能用这个猜测解释故障。
- own_cb_watch 曾因线程达到上限自行退出、卸载仍 kthread_stop 旧指针而引发UAF/panic。这是诊断代码错误。旧源保留为 own_cb_watch-before-lifetime-fix.c；修正版达到上限后保持存活直到卸载，已验证。
- 动态跨WQ诊断曾导致管理失联；无有效软中断吞吐结果，已被固定上下文实验替代。
- 最新 PE 阶段标记候选只增加 mailbox250 的四处阶段写入，未改协议、PN/SN、预算或缓冲逻辑；源码为 rx-pipeline-audit/phase-source。候选 ko SHA256 为 ee842f589d18932012cd7085cca770228193e3eb3f363decd5d099da119a5c7a。所有权回放通过，但实机出现 SER01000000、DMAC00000100、CMAC00000002、RX halt1000；MIPS Cause/EPC为0。**没有有效完整吞吐结果，不得再作为通过版本使用。故障具体原因未证实，不能直接说原路径也有同一故障。**
- 阶段候选曾把7177614字节完整样本复制到光猫 /tmp，串口保留了尾部样本和phase计数0/1/2/3分别36582/1095/1330/993。全量取回失败，之后冷启动丢失了这份 /tmp 文件。**本地不存在完整 rx-pipeline-phase-samples.txt，不能假称留存完整。** 这些汇总包含空闲和故障后样本，不能当有效忙时占比。
- 用户随后确认串口曾物理断开并重新接好。此前“无串口输出”不足以证明内核或卸载死锁；所谓 blocked backtrace 文件只有回显字符，并非内核栈。
- 冷启动后已恢复5053，当前boot_id与状态见上文。所有临时模块、进程、规则已核验清理。

原版 PE 用同一构建脚本重新构建，与接受版逐字节相同，bin SHA256均为39534acfbe76aa04c53433cb133a547369cea8edc10039f16f35298a7b196ccb；阶段版bin为53c32c732dcf3c1eaecf4655fcc5ed6efda291179b473005c25cd81dd6bcc26c，16300字节。最初在主循环加标记的版本触发链接器“code overlaps mailbox”，从未部署，失败源和日志也保留。

只读观察器原版保存在 artifacts/own-wifi-20261020/rx-pipeline-audit/own_rx_stages-stage-only.c 和对应 ko。该目录当前 own_rx_stages.c/ko 已扩展为阶段读取版本，不要混淆两者。观察器使用接受版 DWARF：rtl_ax_ap.rx13984，rtl_ax_rx.rpq_memory24/queues64，queue.payloads32，payload记录16字节且data0/dma8；换驱动前必须重新核对，不可对别的布局直接加载。

## 测速与复现入口

| 入口 | 用途与前提 |
|---|---|
| router.py | 标准输入传入DUT命令，使用现有SSH身份，不需要重配认证 |
| restore-tcp-bench.py | 恢复单向PPPoE模拟源和5206端口服务；默认上传plain sink，GRO对照需明确替换 |
| tcp-run.py | label seconds window streams direction，固定源端口55000；输出原始JSON、前后计数与CPU分析 |
| rx-pipeline-capture.py | 接受版coherent映射采样控制器；首次有效结果对应stage-only观察器，重用时须确认上传产物 |
| analyze-rx-pipeline.py | 复算原始游标、tag、守恒与首次积压；不模拟RF |
| hardware-tcp-loop-run.py | 双空口1Gb内部PHY环；会临时摘LAN1桥、加精确流和Mac测试路由，须完整清理 |
| swap-radio-candidate.py | 已改为确认ap5实际关联MAC，避免仅SSH通却误以为在5GHz；依赖已加载源fixture，重载后必须等正常CAC |

旧BPF或只提交到网卡的约1.7Gbps数字不能作为端到端应用验收。有效数据必须同时看实际应用接收、FE命中、NI完成、各层丢包与错误。

当前不应继续试优先级、队列大小或重复失败候选。若接手继续，先读完整有效 RX 样本和原厂 RX资源查询、tag检查、处理与补环指令。下一步需要把“PE处理段消化不及”落实到具体实现成本或错误；现在没有足够证据指定某条指令、时钟或参数为根因，故未提交新的生产吞吐修改。

## Git 工作区与留存材料

原本未接受或无关改动仍保留：

- manifests/radio-wideband-contract-test.json
- port/RTL8221B.md
- rtl9617c-xram.c 的 SGMII/GMII adjust_link 改动
- port/pe/rtl9617c-radio-downlink.h 与 scripts/test_pe_downlink_gather.py 的 metadata 实验
- sources/openwrt 子模块脏状态
- 未跟踪的 test_lan_link_mode.py 与 live-pon-diag 诊断源

不要 reset、clean 或将这些内容一起提交。交接保存了主仓库和子模块的状态、diff、未跟踪清单。只为交接提交文档与本任务证据 manifest，不推送。

证据入口：

- CHECKPOINT.md：完整历史及被后续记录纠正的中间状态。
- manifests/wifi-rx-pipeline-20261021.json：最新有效 RX 证据、阶段失败与收尾状态。
- manifests/wifi-rx-scheduling-20261021.json：固定执行上下文、失败动态切换及原厂调度指令依据。
- manifests/wifi-rps-endpoint-isolation-20261021.json：ACK和临时端点RPS证据。
- manifests/wifi-rx-oem-path-audit-20261021.json：原厂重排、RX预算与RX_INLINE纠正。
- manifests/wifi-hardware-pppoe-loop-20261021.json：真实硬件环、协议与丢包记录。
- manifests/wifi-l2qm-private-20261021.json：已提交的原厂L2QM遗漏。
- HANDOFF-EVIDENCE-20261021.json：本目录完整文件校验清单，排除清单自身。
- HANDOFF-VM-EVIDENCE-20261021.json：归档所含VM源码、诊断产物与原厂ELF校验清单。
- 工作区/交接材料/TYFZA1-WiFi-handoff-20261021.tar.gz：现场记录及选定VM证据的本地归档；校验值见同目录sha256文件。
