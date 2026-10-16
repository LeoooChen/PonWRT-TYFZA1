# PON 驱动功能覆盖审计

2026-10-16。范围为 TYFZA1 的 XGPON/XGSPON 驱动、光前端和运行恢复；OMCI Class、MIB 与 Class 的协议处理由另一 worktree 负责，本清单不安排这些工作。

依据当前工作树及本机原厂 ca-ne.ko（SHA256 `16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7`）。以下“缺失”表示已对照原厂实际函数和当前调用入口；不是按函数名称或代码行数推算。离线对照不等于光口验收。

## 已确认的缺口与实现顺序

| 顺序 | 功能 | 原厂实际路径 | 修改前缺口 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 完整 PON MAC 热复位及配置恢复 | `aal_xgpon_reset(1/3)`，`aal_xgpon_backup_ponmac/restore_ponmac` | 已有的是 T-CONT/PUC 排空恢复；缺少 MAC 复位、重新初始化、GEM/队列恢复路径 | 已实现；定向寄存器模型检查通过，待 XG 实机验收 |
| 2 | O1/O23/O5 周期健康检查 | `aal_xgpn_activation_fsm_init` 注册参数1000的周期回调；`__xgpon_onu_state_check_timer_callback` | 没有 O5 带宽授权停止、O23 SN/Grant 异常、O1 FEC/同步异常的检查 | 已实现；七组场景与原厂 ARM64 指令执行的复位决定一致 |
| 3 | PLOAM 发送器卡住处理 | 首次 Key-Control 后注册参数100的回调，MAC热复位后保留运行；`__xgpon_ploam_reset_timer_callback` 检查 `0x4afc` 后调用 MAC reset1 | 发送函数有忙位超时，没有周期检查与 MAC 级恢复路径 | 已实现；持续忙位走 MAC-only 复位，原始故障和恢复失败单独保留 |
| 4 | 热复位后的业务 VOQ 恢复门控 | `restore_ponmac`，`__onu_link_evt_handler`，`__xgpon_onu_delay_puc_voq_valid_callback` | 原厂 `delay_puc_voq_valid=1`、间隔3000；缺少 OMCC0..7先恢复、O5恢复T-CONT、再延迟业务队列的状态 | 已实现；包括等待期间业务配置更新，O6宽限保留原策略 |
| 5 | Dying Gasp 断电上报 | `aal_gpon_dying_gasp_set`、发送 handler；本机原厂 DT 的 `dying_gasp@0` | 没有预备缓冲、MIC、外设配置及断电发送处理 | 已实现；已接 per_ictl 第24路，离线生命周期检查通过，未做物理断电验收 |
| 6 | O7/O23 激光 TX-disable 状态控制 | `__xgpn_act_intr_proc` 将 O7软件态11映射为禁用、O23软件态5映射为放开 | GPIO只在启动/停止时控制，没有跟随这两个激活状态 | 已接入硬件态5/1；并纳入定向 IRQ 检查 |
| 7 | OMCC 上行 GEM AES 模式切换 | `aal_xgpon_intr_handler` 分发 DSFP；`__pon_misc_isr(7, bit5)` 改写管理GEM0..7 | 缺少DSFP中断位、全局中断使能及上行AES同步 | 已实现；四组加密组合与原厂ISR/表getter/setter执行结果一致；错误保留并撤下管理carrier |
| 8 | 光口 rogue 检测阈值、恢复及禁用 | `aal_pon_init` 设置阈值2/65535；`aal_opt_wq_proc` 比较1000ms间隔并调用禁用激光/强制O7 | optical IRQ只确认并计数，没有消费rogue事件 | 已实现于XG模式；十二组原厂worker指令回放一致，强制O7控制路径通过定向检查 |

1、2、3、4有依赖，不能先接一个周期复位动作，再把配置丢失或重新注册失败当作“恢复完成”。复位前应保留真实故障寄存器和原因，失败应明确上报，不能靠反复复位掩盖。

实现位置：`rtl9617c-pon-xgpon.c`、`rtl9617c-xgpon-reset.h`、`rtl9617c-pon-omci.c` 和公共 PON DTS。这里只改内核 OMCI 传输及 MIC 使用，没有新增或修改 OMCI Class。

复位共用的 PUC 保存/恢复也按原厂实际调用范围修正：CIR/PIR各256项，背压映射192项。原厂底层 getter/setter允许256项，不能据此声称192之后是非法硬件地址；本次修正的是复位过程保存、恢复的范围。

新增诊断保留 `mac_resets`、`reset_reason`、`restore_pending` 和 `dying_gasp_ready/tx/error`。复位原因1至5分别为O1 FEC/失步、O23无授权、O23有授权无SN、O5授权停滞、PLOAM发送忙位卡住。恢复步骤失败时日志包含具体阶段和错误，不改写为成功。

第二批中断路径核对：原厂 `__xgpon_intr_tbl` 的DSFP状态/使能为 `0x4118/0x411c`、默认使能bit5，全局汇聚bit0；原厂ISR在此事件下改变USBG `0x4618/0x461c` 表的bit17。原生实现以管理表0的当前模式作为切换依据，避免另建软件toggle在MAC复位后失配；只修改0..7的AES位，保留ID、使能、多播和其他字段。关闭加密时先检查是否仍有业务GEM使用AES，再决定是否清除USBG全局AES位。

光口rogue处理使用原有 `0x88/0x8c` 光口中断，阈值写入 `GLOBAL+0x84`；DTS光控制映射扩展为8字节以包含该寄存器。间隔不足1000ms时立即禁用GPIO，已有健康工作取得control/state锁后执行SerDes重启、MAC保持活动、冻结ACTP/强制状态5/解冻，保持原厂O7恢复语义；不以普通关机或无限复位代替。停止当前XG会话时清除未执行动作，非XG模式不进入此新处理。

收尾修正：已复现两次MAC复位发生于重新进入O5之前时，第二次读取空硬件T-CONT表并覆盖待恢复Alloc-ID的问题。原厂 `aal_xgpon_bwmp_tcont_set_isr` 在配置写入时维护 `tmp_alloc_id_cfg`，`aal_xgpon_restore_tcont` 从该软件配置恢复。现按此归属实现：成功的业务配置始终更新待恢复分配，删除业务同步清除，冷初始化清空；MAC快照不再反向覆盖该配置，也不再读取这32项硬件表。定向用例在修正前失败，修正后连续复位及O5恢复通过。

## 已接入的主链，不重复判为整块缺失

| 功能 | 当前入口与证据边界 |
| --- | --- |
| 本机 BOSA 校准、SerDes/gearbox、速率模式 | `rtl9617c-bosa.c`、`rtl9617c-pon-serdes.c`；有原厂序列对照。DDMI当前只有原始值，不能宣称已提供完整工程单位读数 |
| SN、36字节 Registration-ID、ACTP、PLOAM 硬件处理配置 | `rtl_xgpon_mac_parameters/identity`、prepare/activation；已对照原厂寄存器序列，但尚无本轮真实 XG OLT 验收 |
| OMCC、密钥交换、原始 OMCI 传输 | XG IRQ、内核 OMCI transport；已有定向原厂指令回放。这里仅指通道/加密传输，不代表所有 OMCI Class 完成 |
| T-CONT、GEM、PDC、PUC、业务 netdev | `rtl9617c-pon-path.c` 等；已有原厂表格式与部分生命周期对照，仍不能凭这些判定整个业务闭环已验收 |
| O6 短暂失步保留会话、退出宽限后的队列排空 | 已有原生路径与原厂默认策略对照；这与上表缺少的完整 MAC 热复位是不同操作 |

## 排除误判

- 原厂 `Request-Registration`、`Change-Power`、`Sleep-Allow` 的已检查软件处理函数主要是日志和参数展示，不能仅因原生没有同名 parser 就认定少移植完整功能。硬件处理配置和其他调用链仍需分别确认。
- 已检查的 ranging 软件回调保存的是 TWDM 信道 EQD，不直接写测距硬件；不能把 NGPON2 的软件信道管理搬到当前模式4/5。
- 随机退避种子刷新出现在 `aal_xgpon_reset` 内，应随热复位一起实现；目前没有证据表明原厂普通冷启动也必须调用这个 setter，不额外猜加。
- 模式4/5的普通状态机 handler、DSYNC handler以维护软件状态为主；LODS的反复PHY复位 timer受 `aal_xgpon_mac_reset_at_lods` 控制，原厂默认值为0，不能据其函数存在就启用另一套复位策略。
- 原厂 Disable-SN 软件 parser只比对SN并输出日志；激光门控实际在ACTP事件分发，已作为第6项处理。
- `ploam_pkt_ngpon2_proc` 对模式4/5过滤消息19..31，因此不能把其中的 Protection-Control/TWDM信道处理当作当前模式的必需遗漏。`ASSIGN_ALLOCID` 的额外软件清表分支受调试位20控制；原厂默认 `g_aal_xgpon_actfsm_debug=0x105`，该位未开启。Burst-Profile的强制软件改写分支也默认关闭，且REA交叉引用没有发现本模块写入其开关。这些结论限定于已检查模块，不能推广为所有外部调用均不存在。
- 已核对原厂misc工作入口：`__misc_intr_handler`为空函数，不能把其余misc FIFO/工作队列本身当作缺失功能。DSFP AES切换在进入此空handler前就已执行，已单独补入第7项。
- 光前端启动入口也已补查：本机 `/etc/scripts/rtkbosa.sh` 调用 `rtkbosa -c <校准文件>`；该ELF的 `main` 在 `-c` 分支识别EEPROM、执行对应型号初始化和模块标识写入，然后返回，没有由这个启动分支建立常驻温控/轮询任务。`-y` 等独立工厂校准命令不是该启动分支，不能凭其函数存在就添加周期重校准。此处仅排除已检查启动链中的常驻任务遗漏，不宣称其他进程或完整DDMI显示已经核完。
- 约50秒 ACTP 重连和业务 `-110` 仍未定位到现场的确切触发点。功能缺口与现场根因之间必须继续取证。

## 原始分析记录

REA 批次位于本机 `rea-analysis-20261016/rea/`，本轮证据归档索引由对应 manifest 引用：`ghidra-xgpon-functional-gap-sweep`、`ghidra-xgpon-driver-gap-inventory`、`ghidra-xgpon-gap-exact-entries`、`ghidra-xgpon-dying-gasp-path`、`ghidra-xgpon-gap-link-and-power-events`，以及已有的 reset/control 原厂函数分析。

本清单是第一批已确认的驱动缺口，不是“已穷尽所有原厂功能”的声明。新增项目必须先补原厂实际使用路径和当前消费者的证据，再进入实现队列。

## 定向验证与边界

核验记录在 `artifacts/xgpon-driver-gaps-rea-20261016/`：相关 PON 内核对象交叉编译、公共 PON DTB编译、MAC快照/完整恢复、Dying Gasp生命周期，以及受影响的GEM方向、O6和PUC恢复检查。健康检查直接执行本机原厂模块中的回调作对照。

第二批回放记录为 `logs/stock-xgpon-omcc-aes.json`、`logs/stock-xgpon-rogue.json`。前者执行原厂DSFP分发后的misc ISR及USBG表getter/setter，比较最终表和全局控制；后者执行原厂光口worker及检测重臂指令，FIFO、时间和MAC-enable调用出口被明确建模。真实native延后强制O7由warm-recovery定向检查覆盖。边界包括恰好1000ms、AT0/AT1、两个AT位同时出现和LOS位优先。

寄存器模型模拟表访问完成和ACTP状态变化；Dying Gasp检查中的MIC和NI出口是受控模型。它们不证明OLT接受断电报文、真实电源保持时间足够，也不证明已解决现场业务-110或约50秒重连。
