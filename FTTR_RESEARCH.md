# TYFZA1 FTTR 研究分支交接

本分支 `fttr-rewrite` 用于协作开发 TYFZA1 的原生 OpenWrt FTTR 支持。
代码最初从 `dcf470f`（`0.7.0-alpha` 时期）分出，保留了逐步实现和修正的提交记录。
已通过 `61d24e6` 合入最新 `main` 的 `9331c6e`，继续在 `fttr-rewrite` 开发。
这不是新的发布版本，也不是已经完成验收的固件。

本次目标是完整补齐原厂 FTTR 功能，并接入项目自创的 NI/QM/DMA/FE/PE
硬件加速、硬件转发框架。原厂作为行为和硬件契约依据，不能把局部寄存器
复刻、若干离线对比通过或软件回退当作整体完成。继续在 `fttr-rewrite`
开发，不合并到 `main`。按用户最新要求，当前只做离线实现、原厂对比和
构建检查，暂不访问实机；实机验收状态单独保留。

**当前状态：已有原生驱动、管理和转发基础；真实光路、从光猫注册及业务通流、FTTR 硬件加速均未通过实机验收。**
“实现完成”“编译通过”“原厂指令对比通过”在这里指各自明确的范围，不能相互替代。

## 从哪里开始

- [完整研究记录和未完成项](port/FTTR.md)：原厂函数地址、寄存器、已实现行为、验证范围与缺口。
- [内核驱动目录](port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/)：`rtl9617c-fttr*` 为 MiniOLT、光学、P6、OMCI 和队列控制；共享 NI/QM/DMA/FE 的接入也在此目录。
- [控制器和协议实现](port/packages/tyfza1-fttr/src/)：`fttrd.c` 为常驻控制器，`fttrctl.c` 为开发命令，`omci-*` 为原生协议和配置事务，`fttr-net.c` 管理按 GEM 分流的 VLAN 端口。
- [原厂执行工具](tools/)和[定向检查](scripts/)：相关文件名含 `fttr`。每个检查有独立用途及输入要求，不必运行全量测试。
- [业务策略示例文件](port/packages/tyfza1-fttr/files/tyfza1-fttr.services)：默认只有注释，没有替未知从光猫选择网络策略。

## 已经实现的部分

1. MiniOLT MDIO/regmap 和光学 I2C；确认的 ASIC9690/GN25L99 路径、SoC P6 与对端 SerDes、核心初始化及 DBA。另有 FPGA Manager、原厂镜像打包及加载后绑定 MDIO 的开发设备树依赖；FPGA 光学/控制器启动与 FPGA2 协议分派的后续进展见第34项；实机启动仍未验收。
2. 独立 `fttr0`，P6 源端口 6 与运营商上联 PON 源端口 7 分开；原厂 OMCI 封装、CRC、异步 MIB 读取和实体模型。
3. 显式策略选择实际 UNI/T-CONT/队列，创建并读回 GEM、桥接和 VLAN 配置；原始错误及清理失败均保留。
4. 多 ONU 异步调度、运行时记录、序列号更换及 MIB 同步丢失后的重新读取；按 GEM Port-ID 添加外层 VLAN，接到明确指定的 Linux 网桥。
5. 原厂格式的 P6 CF/FE 接入、删除端口前同步撤销 Hash/cache 流；P6 ES 队列清空函数和 DMA/AFT 配对复位修正。补齐独立 FTTR 启用共享 FE，PON/FTTR 分别持有启用状态；关闭 PON 撤销其双向流并保留 FTTR，最后一个使用者退出后恢复共享表。已依据原厂补上 P6 的独立 EQ0 缓冲池、端口14信用/队列配置及 DEEPQ 转发，避免误用普通 LAN 的直接 L2TM 路径。补齐不同 ONU 业务 VLAN 共用 P6 的桥接学习，以及当前 QM 布局下的长 FTTR 帧分段接收。实机命中及完整报文退场仍待验收。
6. 原厂 `0xff69/0xff6a` 远端 Wi-Fi 更新事务，支持预读取、核验与恢复。`fttrctl wifi` 和 `fttrd --wifi` 共用配置解析；常驻控制器按序列号与实际实例选择策略，串行完成每 ONU 的 Wi-Fi、业务和同步核验，支持多实例、超时、替换及属性漂移后的重新配置。原厂报文及 AF_PACKET/veth 对比已通过；尚未实现 OpenWrt 配置到原厂枚举的映射。用法见 [Wi-Fi 命令说明](port/FTTR.md#explicit-remote-wi-fi-command)和[常驻策略说明](port/FTTR.md#resident-remote-wi-fi-policy)。
7. 已执行保留原厂 `sync_controllerd` 的 JSON 构造、完整发送路由、心跳发现、MAC/槽号查找、老化及分片接收代码。原生 `fttr-sync` 支持 `discover` 和按目标 MAC 自动发现后查询；发送到原厂组播地址，接收按来源 MAC 解析本地槽号，并支持有长度/期限限制的 JSON 分片重组。新增 `wifi-settings`，按原厂 `SetAllSetting/Flag1/content.act=get` 请求读取 Wi-Fi，区分 ACK 和 `STATUS:0` 完成上报，按管理程序消费路径解析 `content.content.List`。完成原厂报文/状态/拼接对比和39项 AF_PACKET/veth 检查。发现不是身份认证；显式字段写入进展见下一项，实机验收仍未完成。详见 [syncserv 证据与入口](port/FTTR.md#original-syncserv-ethernet-path)及[回读与上报](port/FTTR.md#wi-fi-settings-read-and-completion-report)。
8. 已执行原厂 `fttrManage` 字段生产、远端写入构造及 libmib 枚举转换，新增 `wifi-apply <私有JSON文件>`：校验完整配置，预读取实际条目，发送一次 Flag1 写入，再独立读取并比较可读字段。30项 AF_PACKET/veth 检查通过。原厂读取不返回密码，因此明确保留 `credentials_verified:false` 和 `configuration_verified:false`，尚无完整密码回读/恢复或实机接受证据。详见[显式写入及边界](port/FTTR.md#explicit-flag1-wi-fi-write-and-readable-field-comparison)。
9. 已执行原厂 JSON Register 基线握手与协议状态切换，新增 `register <私有策略文件>`：显式提供主设备 OUI/SN/Ready，核对目标自报 SN/可选 GPONSN 后回复原厂基线报文。20项网络检查通过，既有读写回归通过。只报告自报身份匹配及回复已发送，不声称认证或对端接受；真实就绪状态联动仍待实现。详见[注册入口](port/FTTR.md#explicit-syncserv-registration-reply)。

10. 新增 `fttr-sync serve`：在同一个接口租约内维持发现、身份策略、原厂注册回复及异步 Wi-Fi 预读/写入/回读。支持16条从设备策略与每设备16个 SSID，重新注册、掉线或身份替换取消旧事务，已发送过的密码在进程内不自动重发。37项常驻网络场景、原有89项网络回归和 GCC14.4/musl 包编译安装通过；新增默认关闭的独立 procd 服务。身份核对仍来自自报字段，真实就绪与实机密码核验未完成；持久恢复进展见第12项。详见[常驻 syncserv 策略](port/FTTR.md#resident-syncserv-registration-and-wi-fi-policies)。

11. 新增 `fttr-sync-policy`，用真实 libuci 将指定命名 AP 的 SSID/密码、启用/隐藏及加密模式转成常驻策略；明确绑定从设备频段、SSID 序号和 Standard，输出私有快照。49次转换检查、24次原厂字段比较及打包常驻程序的双频 veth 下发/回读通过。另执行原厂 Ready 的29个场景，确认依赖 WAN IPv4 与 CWMP USERINFO 状态，不能用光口就绪代替；动态 Ready、UCI 热更新和完整安全/无线参数映射仍未完成。详见[UCI 快照入口](port/FTTR.md#openwrt-wi-fi-policy-snapshot)与[原厂 Ready](port/FTTR.md#original-master-readiness-predicate)。

12. 新增 `fttr-sync serve --state`：按从设备 MAC 与显式 Revision 保存完整私有配置和尝试位图，文件/目录 fsync 成功后才发配置包。进程重启先重新发现及核对身份，对已尝试的行只做可读字段核验；不匹配即停止，未尝试的后续行可继续。38项强制终止/同步失败/版本冲突/权限及恢复检查、37项常驻与89项单次回归通过；打包 UCI 到网络链路启用持久记录后也通过。并未验证物理闪存掉电或从设备密码安装。详见[持久恢复](port/FTTR.md#durable-syncserv-write-intent-and-restart-recovery)。

> 下列按完成顺序保留研究记录。第13–33项中关于 FPGA2 使用 FPGA1 relay、授权绑定未知或拒绝业务的旧结论，已由第34项执行原厂 supervisor 的证据纠正；当前支持范围以第34项及详细文档为准。

13. 新增可选 FPGA 镜像包：核对保留 top.bin 的长度和 SHA-256 后原样安装，开发 DT 在 SPI probe 中加载镜像，MiniOLT 通过设备依赖等到加载成功且 DONE 就绪后才访问 MDIO，避免把未加载的零寄存器识别成 FPGA1。加载器绑定期间禁止手动重写，设备链约束卸载顺序。新增 FPGA1/FPGA2 原厂 post-DONE 寄存器控制与错误停止检查，真实内核对象/模块链接、DT 和固件包暂存验证通过。进一步按 `libponrelay1p25g` 补上 FPGA 专属 OMCI 初始化、固定目的 MAC/基线封装、只读 ONU 状态和保留其他位的 TX 控制；12组原厂对比、18个报文、ASIC OMCI/FPGA 启动回归和6.18.55对象编译通过。原厂 FPGA 带宽配置接口为空实现，不能据此套用 ASIC 的管理 grant/DBA。完整光学、核心、ONU 业务启动或实机验证仍未完成。详见[FPGA 启动依赖](port/FTTR.md#fpga-boot-image-and-miniolt-dependency)及[管理通道](port/FTTR.md#fpga-management-transport)。

14. 补上 FPGA1 独立 DBA/资源编号及控制器接入：原厂 `libasicdrv` 的 FPGA1 授权位于 `40400+ONU*2`，fixed 与 ONU 状态共用下一字，写入只替换低16位；原厂 FPGA relay 的 Alloc-ID 为256..319，每 ONU 一个业务槽。CLI/常驻控制器按真实 backend 选择资源编号，将业务槽1映射到 FPGA1 物理槽0；管理会话不写 ASIC 管理授权，P6 就绪检查不再读取 FPGA 上不存在的 WTG 属性。63组原厂 DBA、64个分配/占用及 Set 报文、CLI 事务、FPGA1/ASIC 常驻生命周期与启动回归、真实软件包/内核模块构建通过。FPGA2 的底层 DBA 访问已对比，但其物理授权表与 relay 编号的对应尚未查实，业务入口明确拒绝且仍列为缺口。完整启动、真实授权/流量及旧报文退场仍未完成。详见[FPGA DBA 接入](port/FTTR.md#fpga-dba-and-controller-integration)。

15. 按原厂 `rtkoltbosa` 的四张可达操作表补齐 UX3326、UX3326S、GN25L99、GN25L81 初始校准及可用身份检查；FPGA 从本机工厂文件读取型号和地址，动态绑定 I²C 校准客户端。常驻启动分开 ASIC/FPGA 流程，FPGA 使用 P6 mode5、原厂 post-DONE 设置和650ms等待，再校准、配置管理通道并最后启用 TX。四种 ARM64 原厂写序列、逐笔错误停止、生产驱动配置/状态、动态绑定、启动脚本和三种后端启动检查通过，真实软件包及 MiniOLT/BOSA 模块编译链接通过。UX3326 无原厂硅片身份判据，不能把地址应答写成芯片识别；持续监控/DDMI、SFP 分支、较新 FPGA1 的认证和实际光学器件/流量仍未验收。详见[光学与 FPGA 启动](port/FTTR.md#factory-selected-optics-and-fpga-bootstrap)。

16. 接入新版 FPGA1 的本机工厂认证：根据原厂 MIB2715 导入8个32位字，校验格式并以私有运行时文件提供给驱动；仅 backend1 且版本高于 `230406ff` 才请求输入，按原厂顺序写57..50、等待1.2秒并读5e。原厂认证失败仍继续，重写则返回错误，在启用 TX 前停止并清理。36组实际原厂启动分支/寄存器/结果及 libmib 转换、逐笔 I/O 失败、输入失效、控制器认证失败清理、FPGA 管理和工厂导入回归通过，软件包及内核模块编译链接通过；没有实机认证验收。详见[FPGA1 工厂认证](port/FTTR.md#fpga1-factory-authentication)。

17. 补齐 CPU 经 `fttr0` 发包时的 DEEPQ：原厂虚拟 CPU 端口分配设置 DEEPQ，发送构造将它写到 Header A bit62；此前重写只给 FE 输出设置，CPU 发送遗漏。现补上该位，使管理和业务 CPU 报文同样使用 P6 的端口14队列与背压。执行原厂分配/位赋值及生产发送/DMA 打包，确认旧代码失败、新代码在64/1518/1996字节帧通过；4096次 DMA 混合发送、环回绕/BUSY 和 QM 分流回归通过。仍未证明物理调度和完整报文退场。详见[CPU P6 DEEPQ](port/FTTR.md#cpu-originated-p6-deepq)。

18. 补齐 UX3326 原厂常驻温度调节与复位恢复：按工厂温度开关选择200ms/1000ms周期，读取温度后查84项表并更新调制参数；每1.6s/2s检查复位特征与原厂光功率阈值，按需重载校准、等5s、关光1s及开光1s复查。恢复由 `fttrd` 事件循环持有，退出后不再有延迟开光；持续低功率或 I²C/工厂输入错误进入原有 TX/FE 清理。补齐 MiniOLT SMBus 双字节访问及字节序。514组温度边界、63组功率输入、四段完整原厂循环、生产驱动/生命周期、真实命名空间恢复/失败/退出及原有控制器回归通过，包和内核模块编译链接通过。仍未做实际温漂、功率或恢复验收；完整 DDMI/SFP 仍待补齐。详见[UX3326 常驻控制](port/FTTR.md#ux3326-temperature-adjustment-and-recovery)。

19. 修复 P6 重配期间的软件提交竞态：原有停队列后，共享 DMA 定时器可能依据旧 `configured` 状态再次唤醒；新增与 DMA 提交共用锁的 P6 门控，同时约束定时器唤醒与实际入队。停止/重配失败保持关闭，仅正常打开或重配成功恢复，其他 DMA 使用者继续工作。生产回调交错检查先复现旧错误再验证修复，包含 FE/SerDes 错误、关闭、重新打开及其他端口；共享 FE、4096次 DMA 回绕/BUSY 与内核 Image/模块构建通过。这只解决软件生产者窗口，DMA 预取及下游完整退场仍未完成。

20. 接入原厂永久停用/显式恢复 ONU：FPGA 停用整字改写下行状态、清除共用固定授权，等待10ms再清上行状态；ASIC 仅修改屏蔽位，两者 PLOAM 写入长度也不同。新 `fttrctl ... onu-admin` 通过常驻控制器按序列号校验，先暂停 FE、撤销该 ONU 的业务 VLAN/FDB 与 DBA，再调用驱动；驱动在控制锁内重查身份，成功后才恢复其他 ONU 的 FE。超时或 I/O 失败触发 TX/FE 退出清理。18组实际原厂状态入口、逐笔故障、序列号/读回/解析检查，以及 ASIC/FPGA1 两套真实守护进程与 CLI 双 ONU 生命周期测试通过；既有控制器、远端 Wi-Fi、光学启动回归及包/内核模块编译链接通过。定时停用、跨核心复位的持久屏蔽策略、完整报文退场和物理 PLOAM 接受仍未完成。详见[ONU 停用与恢复](port/FTTR.md#resident-onu-deactivation-and-explicit-resume)。

21. 补齐原厂临时停用 state4：FPGA 在约100ms扫描中等待注册位清除后仅清屏蔽位，ASIC 按8秒离线扫描及15秒回调允许释放；显式恢复仍保留原厂不同的整字/掩码行为。控制器按序列号持有待恢复状态，期间不重建业务；后续永久停用、恢复、身份变化及退出取消旧任务，驱动重新核对身份/状态且保留同字授权位。24组原厂释放条件与生产寄存器操作、事件循环边界/故障，以及 ASIC/FPGA1 双 ONU 的临时恢复、覆盖、替换和退出检查通过；控制器/CLI、FPGA 光学启动回归和包/模块构建通过。持久管理策略、物理报文退场与 PLOAM 实机接受仍未完成。

22. 补齐原厂 P6 先于 FPGA 加载的启动顺序：rc 脚本按本机工厂 NWAY 配置 mode5，加载器在任何 FPGA GPIO 操作前取得 NI/P6 保护，随后才进行引脚设置和镜像加载。期间禁止打开 `fttr0`、重配 P6 或启用 FE，其他 NI/DMA 使用者继续工作；失败停止 P6，设备链约束卸载顺序。冷启动控制器保留加载前时钟，重启时才按需重新配置。补上成功 probe 属性检查，避免把提前出现的 driver 链接误认为加载成功。工厂双 NWAY、加载/依赖/错误路径、生产 P6/DMA 交错、三种后端启动、四种光学校准、共享 FE 和 LAN 并发回归通过；内核 Image/模块、FPGA DT 及软件包构建和源码一致性核对通过。仍未加载实机，完整报文退场、FPGA2 授权绑定和其他功能缺口继续保留。详见[P6 加载前依赖](port/FTTR.md#p6-prerequisite-for-fpga-loading)。

23. 补齐 P6 DEEPQ 的 L3 ES 端口14清队列步骤，随后处理物理 P6 的 L2 ES；两层分别保留完成位图、失败队列、占用、原始错误与恢复错误，先核查任一层已有清空操作，末尾复查跨层回填。原厂 L3 也存在超时假成功，重写明确报错。6组原厂指令轨迹、22个双层故障/回填/相邻端口场景、P6 配置与 TX/加载门控回归及内核 Image/模块构建通过。原厂 EMPTY 中断和同步发送指针检查仍不证明 AFT/PE/MiniOLT 退场，因此尚未把该函数接作 GEM 复用保证；完整退场仍是主线缺口。

24. 接入原厂上行状态错误恢复：ASIC/FPGA 分别按 bit20–24/bit20–23 判定，均要求最高两位为零；按原厂方式清错后停用，再复用临时释放流程。常驻控制器按后端节奏扫描，先撤销 FE/业务 VLAN/DBA，驱动锁内重查序列号和条件；永久屏蔽不被转换为临时状态，错误消失或编号替换不误写。扫描从本轮完成后计时，避免慢 MDIO 引起连续追赶。384组原厂判断/操作对比、故障与生命周期边界、ASIC/FPGA1 双 ONU 自动恢复和包/模块构建通过。实际故障恢复、完整报文退场仍未做实机验收。详见[上行状态恢复](port/FTTR.md#original-upstream-state-error-recovery)。

25. 补齐四种 BOSA 的原厂状态读取：UX3326/GN25L99/GN25L81 按本机出厂系数换算，UX3326S 读取已校准数值及接收功率。光模块驱动在校准/维护共用锁内采样，`fttrctl ... optical` 输出温度、电压、偏置电流与光功率；dBm 在用户态换算，零功率、无 RX 能力和读取错误明确区分。400组原厂 ARM64 输入对比、17个逐笔 I²C 故障、校准文件/锁及真实 CLI 检查通过；校准、温控、Wi-Fi/MIB CLI 回归及包/模块构建通过。SFP 分派、告警策略、传感精度与光口就绪仍未验收。详见[BOSA 状态读取](port/FTTR.md#original-bosa-diagnostics)。

26. 接入原厂全局 LOID 认证：工厂导入独立 CTC 字段，常驻控制器在 MIB/Wi-Fi/业务前异步读取 CTC 凭据，按原厂条件回退 ONU-G，写入并读回1/2/3结果。修复原厂读取失败仍假成功的行为；失败保留管理通道、不创建业务授权/端口，身份替换、MIB 丢失或显式恢复重新认证，退出撤销认证状态。两种后端在认证与 Wi-Fi 同时启用下的真实守护进程/veth/网桥/VLAN检查、原厂编解码与从端结果 GET、错误/超时/私有输入和既有管理/Wi-Fi回归通过；FTTR/工厂包编译安装及源码一致性通过。完整五种逐 ONU 认证、SN绑定/分配、策略热更新仍未实现；没有实机认证或完整旧报文退场证明。详见[常驻全局 LOID](port/FTTR.md#resident-global-loid-authentication)。

27. 补齐 P6 与共享 FE 的长帧预算：原生 FE 的 LPB1 原先限制1700字节，低于原厂 P6 MAC 的2000字节；在 FTTR 存在时将该共享 LAN/P6 入口对齐到2000。执行原厂 STG0 确认 LDPID25→LPB1 和48组 MRU 位赋值/读回，生产配置检查先复现差异后通过；无 FTTR、上联 PON、退出恢复、FE错误回滚及768组 QM 收包回归通过，内核 Image/模块编译通过。原厂默认 MRU 为零，其含义未据此猜测；实机长帧接受和完整报文退场仍未验收。详见[共享 FE 长帧入口](port/FTTR.md#shared-fe-ingress-limit-for-long-fttr-frames)。

28. 修复全局认证重启时继承旧 DBA 的缺口：控制器取得独占管理权、撤销 FE/旧业务 VLAN 后，清除并读回全部业务授权，覆盖当前离线和无策略的 ONU；ASIC 保留管理槽，FPGA1 清唯一数据槽。完整启动在核心初始化后、开光前完成此步骤，清除失败禁止继续；启用认证的 FPGA2 因授权绑定未查实而在接管前拒绝。实际进程测试先复现旧授权残留，再验证两后端认证/Wi-Fi/启动顺序、假成功与缺失读回，软件包编译通过。该修复不等于下游缓存排空或每 GEM 硬件入口隔离，相关缺口继续保留。

29. 接入原厂 ONU 编号迁移执行链：新 `fttrctl ... onu-move` 经常驻控制器核对两端 SN，先撤销本地 FE/VLAN/DBA、异步释放本进程拥有的远端 VLAN/GEM/T-CONT，再由驱动锁内二次检查身份及零授权，按原厂顺序写目标 SN、停用源 ONU、清源 SN。解决迁移后残留旧 Alloc-ID 导致无可用 T-CONT 的问题；重新注册/认证才恢复业务。两后端原厂写序列、逐笔故障、实际守护进程/CLI/网桥迁移及漂移/超时/退出检查通过，包和模块构建通过。重启后无原始归属记录的资源明确拒绝释放，自动五方法策略触发、持久绑定、FPGA2 和完整报文退场仍未完成。详见[编号迁移](port/FTTR.md#resident-onu-serial-reservation-migration)。

30. 接入原厂逐 ONU 认证选择器：五种比较规则、厂商前缀过滤、启用状态、SN 持久绑定、双开关自动学习和编号迁移已接入常驻服务。SN/LOID/LOID+密码按实际凭据认证；PLOAM 获取仍缺证据，方法2/3明确报缺失而非使用全零值。绑定/学习先持久化再重试，迁移先只读盘点并拒绝归属不明的远端资源；目标被其他注册绑定、旧响应/替换身份、持久化失败均不能开放业务。240个原厂比较、96个分配用例、ASIC/FPGA1实际守护进程/veth/Wi-Fi/业务流程及全局认证/显式迁移回归通过，FTTR包交叉编译通过。热更新、原厂逐 ONU 配置导入、PLOAM 获取和完整报文退场仍未完成；无实机验收。详见[逐 ONU 认证与分配](port/FTTR.md#resident-per-onu-authentication-and-assignment)。

31. 修复逐 ONU 分配的同轮扫描竞态：已复现旧高编号槽掉线、新低编号槽先处理时，旧认证标记导致跳过原 SN 行并错误自动学习。注册快照现先完整撤销失效身份及旧业务，再进行新身份选择，保持原编号迁移语义。ASIC/FPGA1真实守护进程/Wi-Fi/业务全流程及竞态回归、软件包构建通过；此修复不扩大硬件退场证明范围。

32. 补齐撤销时的 CPU 业务入口：已复现仅写 `fttr_accel=0` 时 GEM/VLAN 帧仍进入 DMA。新增 `fttr_forward`，先在共享发送锁下关闭业务、等待接收读者并清理父接口 qdisc，再执行既有 FE 撤销；非管理 CPU 收发丢弃，原厂无外层 VLAN 的 OMCI 信封继续可用。启用失败撤销待生效 FE 请求，接口重开不继承旧业务许可；控制器启动/撤销/迁移/管理/退出全部接入，缺少接口明确失败。生产函数交错/ASan/UBSan、实际两后端控制器场景、P6/FE/768组收包回归及最终 Image/模块/软件包编译通过。DMA/AFT预取、在途 FE/PE、已交付 RX/GRO 和 MiniOLT 仍未证明排空，不作为完整 GEM 复用屏障。详见[CPU业务撤销入口](port/FTTR.md#cpu-business-admission-during-forwarding-withdrawal)。

33. 排除 FPGA2 授权绑定的一条错误依据：执行两套原厂 relay 的带宽设置/清除完整调用链及直接驱动入口，144次均返回成功但无硬件调用或栈外写入。实际 `drv_gpon_alloc_id_config/clear` 为空实现，因此高层 T-CONT 添加成功不证明物理 DBA 已绑定，亦不能作为退场证据。原厂63组直接 DBA 读写及64个分配器回归通过；后续应追踪独立寄存器生产者，FPGA2仍保持明确未支持。

34. 纠正 FPGA2 的原厂程序分派并补齐控制器接入：执行 `onuinfo_check` 证明仅 FPGA1 使用 `onuinfo_fpga/libponrelay1p25g`，FPGA2 与 ASIC 使用 `onuinfo/libponrelay`。据此修正 FPGA2 OMCI 目的地址/长度封装、七业务槽 Alloc-ID/DBA、独立管理授权、停用/恢复/迁移及认证流程；执行实际启动和注册回调，补上 PHY/软复位、光学延迟、OTP 和默认 DBA，保留 FPGA 物理加载/MDIO/P6 路径。448个原厂分配用例、两模式启动逐笔 IO/故障、三后端管理/迁移/恢复、FPGA2真实守护进程与 CLI 的认证/Wi-Fi/业务槽7/迁移/启动、FPGA1策略及ASIC报文回归通过；FTTR包及MiniOLT/BOSA模块编译链接通过。完整默认业务导入、缓存退场及实机验收仍未完成。详见[FPGA2分派与启动](port/FTTR.md#fpga2-controller-selection-and-startup)。

35. 关闭撤销时的 CPU 接收暂存窗口：已复现仅等待 RCU 后 NAPI/GRO 仍保留旧 FTTR 业务包。`fttr_forward` 现暂停活动 NAPI、等待完成尾部，清理 P6 业务聚合/普通批次及跨缓冲片段，保留管理和其他端口聚合；恢复时重新调度，已停止引擎不被误启动。八桶混合帧/聚合计数/重复撤销/停机的生产函数 ASan/UBSan、既有转发/TX门控、共享FE及768组QM收包检查通过，最终Image/模块构建通过。已进入RPS/协议栈的包和硬件在途缓冲仍不属于该清理范围，完整退场未宣告完成。详见[CPU接收暂存清理](port/FTTR.md#retiring-cpu-held-fttr-receive-packets)。

36. 查实 FPGA2/ASIC 的默认业务生产链：执行原厂 `ctc_ponrelay_onu_init`，确认默认一个 `FTTR-100M` T-CONT/GEM/流及组播启用，多槽时每流增加100×序号的VLAN过滤。继续执行 relay 参数转换和新流选择，证明默认选择共享桥，不能用当前“以太网UNI序号0”策略替代；失败后原厂整ONU删除/重加且忽略恢复错误。9组配置、33个失败分支、12次转换及4组桥选择通过；后续执行共享桥UNI枚举，确认Ethernet与VEIP分别按配置/实报较小数量全部接入（3组差异数量场景）。这是原厂证据进展，共享桥完整UNI接入、组播、多业务和恢复仍待原生实现。详见[默认业务证据](port/FTTR.md#original-default-service-and-shared-bridge-selection)。

37. 接入共享桥的全部 Ethernet/VEIP UNI：显式 `shared` 业务从实际 MIB 选择所有 UNI，先完成全部只读预检，再创建共享 GEM/T-CONT 及逐 UNI 对象、管理状态和独立 VLAN 事务，成功后接入既有 NI/P6/FE 业务端口。后段失败恢复早先 UNI 的旧表/状态，清理失败保留依赖；迁移先撤销 FE/VLAN/DBA，再释放全部 UNI 和共享资源。原厂报文对比、混合 UNI/重复采用/归属冲突/中末段失败/旧表恢复、FPGA1/FPGA2 实际守护进程迁移及既有认证/Wi-Fi/控制器回归通过。原厂配置数量限制、组播、多业务和持久资源归属仍未完成，尚未实机验证。详见[共享桥事务](port/FTTR.md#shared-bridge-ethernet-and-veip-service-transactions)。

38. 查实默认组播的远端对象与本地接口要求：执行原厂选择/配置/报文链，确认优先首个VEIP、否则首个Ethernet；创建309或全局CTC分支的65531、310/311，并为共享桥首个组播用户创建指向GEM4095的type6端口。默认GEM路径创建268/281及IPv4范围；18组正常、24组失败、7组GEM与6组默认包装调用确认原厂漏查Set和清理类型/错误处理问题。保留工厂配置与脚本使用独立`.mc`及VID4095，隔离网络实测Linux VLAN拒绝4095，后续须在原生NI/P6框架接入专用组播端点及共享生命周期。这是实现依据，尚未完成组播产品代码。详见[默认组播与接口边界](port/FTTR.md#original-default-multicast-objects-and-transport-boundary)。

39. 补上原生`fttr-mc`内核端点：发送压入原厂0x8100/VID4095并复用P6父队列、DMA和DEEPQ；按实际业务VLAN/网桥归属汇集IGMP/MLD，保留客户标签，处理逐ONU重复组播与原厂来源MAC规则。来源位放入skb复制字段，转发撤销、父口关闭/重配、组播口关闭与卸载接入现有门控及NAPI/qdisc清理。原生回调ASan/UBSan、CPU门控/GRO/P6队列/共享FE/网桥学习/768组收包回归和最终Image/模块编译通过。端点初始关闭，守护进程尚未自动接入；远端组播对象、完整授权生命周期和硬件复制仍需补齐，不能据此宣告组播完成。详见[原生组播P6端点](port/FTTR.md#native-multicast-p6-transport-endpoint)。

40. 接入原厂标准309/CTC65531及310/311、共享桥type6和GEM4095的远端组播事务，按实际MIB预检、读回并保留归属；常驻控制器管理原生`fttr-mc`，关闭源MAC学习，在全部已注册ONU的认证、同步、成员及非成员GEM缺席核验完成后才开放共享组播。漂移、迁移、身份替换和退出先关闭共享端点；三后端真实守护进程/veth/网桥及原厂报文对比通过。MIB读回不等于原厂下层ACL成功，实时隔离与完整在途报文退场仍未验收。

41. 把完整显式TC多副本规则接入自研FE/NI/QM框架：全部副本动作和NI位图先就绪，再发布Hash；撤销先Hash/cache、再NI，失败保留引用重试。LAN输出和经许可的`fttr-mc`输出分别记录最终标签，P6通过独立目的匹配进入既有深队列；支持精确PCP/DEI，拒绝未知标签和不完整成员规则。补上原厂MCE阈值23、保留源编号、端口映射及既有过滤策略核验，配置可恢复；守护进程在关闭组播口后取得同步FE清理回执。原厂指令、生产函数故障/ASan/UBSan、三后端常驻流程及Image/模块/包构建通过。已发布组号仍隔离不复用（累计255组、64种不可变动作），自动MDB、PON/无线副本、完整退役和物理通流均未完成。详见[原生组播表归属与TC接入](port/FTTR.md#native-multicast-group-ownership-and-explicit-tc-integration)。

42. 接入Linux完整组播转发事务：按实际成员/路由器选择记录全部输出，在VLAN/过滤/可转发检查后核对副本，完整成功才通过原生FE/NI/QM安装LAN及共享`fttr-mc`规则。补上精确UDP端口模板、36个组播写入口与过滤/TCX/qdisc/VLAN优先级变更的代际失效、跨驱动重载事务编号、旧代撤销、失败回滚重试和整组最小MTU/CPU16回送配置。实际生产函数/内核选择ASan/UBSan、109组报文字节、574个profile故障点、原厂8组模板删除/64组MTU/256组键CRC及相关单播/P6回归、Image/模块和补丁零偏差重放通过。本机接收副本、PON/无线/ONU入口加速、cache与在途报文退场、组号安全复用及物理验收仍未完成；组号仍累计隔离255组，不能据此宣告FTTR全部重写完成。详见[完整组播事务](port/FTTR.md#complete-bridge-multicast-transactions-and-native-learning)。

43. 接入原厂专用本机组播副本语义：完整LAN入口组播事务可以包含本机副本或只有本机接收。原厂CPU18/POL56映射到自研CPU16收包池、隔离组号POL及物理源保留；QM先归还EPP信用，经原始VLAN/桥本机检查交付且不再次转发，普通未命中和MTU回送继续完整软件转发。撤销先取消本机归属，桥交付再次核对代际；补齐混杂/全组播标志修改时的规则失效。144项实际QM、全量元数据域、40项实际桥接收、原厂六组动作/1530种本机适配、768项P6回归及Image/模块构建通过。本机每代占用独立不可变动作，64槽会先于255组号耗尽；安全复用、PON/无线/ONU入口和实机验收仍未完成。详见[本机副本接入](port/FTTR.md#native-cpu-multicast-host-copies)。

44. 接入ONU/P6入口的完整组播事务：按实际GEM VLAN和FDB归属识别源，LAN/本机副本保留原地址，仅共享GEM4095副本按原厂脚本改为本机MAC。独立8项L2源MAC表接入写入核验、引用、失败重试与隔离；纠正原厂底层辅助函数把MAC改写同时变成TTL减一的行为，保持桥接TTL。P6本机副本保留来源和GEM标签，业务撤销前关闭学习规则，长帧按完整CPU缓冲预算回退；异常分段副本禁止再次作为普通包转发。原厂动作/MAC表、512组Hash键、192项QM、40项桥接收、768项P6分段回归及Image/模块构建通过。PON/无线副本、未知组播泛洪加速、8项MAC/64动作/255组号的安全复用与实机验收仍未完成。详见[ONU入口与逐副本改写](port/FTTR.md#onu-source-multicast-and-per-replica-source-mac)。

45. 接入PON上行组播副本：每个PON输出独立执行现有OMCI/EVTO选择，按原厂动作分别填写GEM索引、T-CONT目的编号和队列，不污染LAN/P6/本机副本。复用CPU发包的VLAN服务入口判定，配置歧义或未覆盖的DSCP/优先级依赖保留软件转发；深队列目的匹配接入归属、读回、错误重试和64位端口隔离。PON关闭/路径替换现在检查任意副本位置，并在撤销失败时保留旧快照。9组原厂完整动作、31组目的匹配、162项路由故障、实际混合副本/配置更换及既有CPU PON报文回归、Image/模块构建通过。PON源入口、无线副本和完整硬件退场仍未完成；8项深队列表与P6共享，资源饱和时整组回退软件。详见[PON上行副本](port/FTTR.md#pon-upstream-multicast-replicas)。

46. 接入ITU PON入站组播：WAN与LAN分类共同使用独立profile1，保留原GEM索引、OMCI配置代际和线上/逻辑两份VLAN信息；复用实际PON接收选择器后才安装完整副本规则。同一报文经不同GEM入站保持独立Hash和本机归属。本机副本由QM恢复原GEM，经过PON VLAN还原后绕过GRO交给桥，错误GEM/旧代/EPON切换后迟到副本均拒绝。双GEM并存、真实VLAN还原/服务检查、596项分类故障、1280组原厂键CRC、现有PON/桥/P6回归及Image/模块构建通过。无线副本、EPON专用组播、未知组播泛洪和完整硬件退场仍未完成，未做实机验证。详见[PON源入口](port/FTTR.md#pon-source-multicast-and-native-host-receive)。

47. 接入无线入站组播：首次NI未命中通过实际无线端点保留VIF，完整学习以source18/POL区分接口，可复制到LAN、FTTR、ITU PON和本机。共同动作清除无线直发元数据，QM本机副本复核当前VIF端点并阻止再次查询；撤销先关闭学习代际，Hash删除失败也拒绝旧副本。1792组原厂键CRC、300组原厂DMA指令、7200次原生提交、IPv4/IPv6与控制报文分流、完整混合复制/失败撤销、240项QM/40项桥接收/768项P6回归及Image/模块构建通过。无线出站仍缺原厂CPU按接口直发适配，当前PE单播队列不能承载BCMC；资源安全复用与实机验收仍未完成。详见[无线入站与VIF身份](port/FTTR.md#wireless-source-multicast-and-vif-identity)。

48. 接入AP无线出站组播：完整事务为每个VIF建立独立不可变CPU副本身份，NI复制最终VLAN后由QM核对代际、报文、桥归属与当前端点，再进入正常mac80211发送路径，保留BCMC/组密钥/DTIM处理。执行原厂回调注册发现先前PON验证工具误用了无线映射回调；改用真实PON分支后9组动作仍一致，产品路由无需修改。30组原厂CPU分派、12组原厂无线副本、765组原生动作适配、LAN/PON/ONU双VIF完整事务/撤销竞态、384项QM、既有TC/桥/P6/PE请求与密钥回归通过。每个无线副本额外消耗1个隔离编号和动作槽；STA输出、PE直接BCMC、多播转单播、完整队列退场/复用与实机验收仍未完成。详见[AP无线副本](port/FTTR.md#ap-wireless-multicast-replicas-through-native-cpu-dispatch)。

49. 接入STA无线输出并补齐软件QoS语义：按真实入站/VLAN映射恢复固定优先级，无线裸口来源保留逐包COS；QM不再覆盖已恢复的优先级。组播VLAN编译按Linux实际`reorder_hdr`规则处理，标志/接口特性变化使整组失效；COS导致输出标签变化时拒绝仅凭单包建立规则。12288组实际Linux VLAN/WME队列对比、32组原厂COS/QoS执行、LAN/PON/ONU/无线到STA完整事务、384项QM和既有回归、Image/模块及补丁零偏差重放通过。直接PE BCMC、EPON、泛洪和完整队列退场/资源复用仍未完成，无实机验证。详见[STA副本与QoS](port/FTTR.md#station-wireless-replicas-and-software-qos-equivalence)。

50. 查实EPON组播路由生产链：执行原厂EPON SID初始化、FC映射存储/替换以及完整NI副本动作，确认8个SID对应队列，目的为`32+MPCP软件索引`且POL为0，不能套用ITU GEM8..255。128次新建/替换、36组完整动作通过；注入错误复现FC忽略流撤销和LSO编程失败。当前只完成实现依据，EPON原生副本、LLID/MAC实例与注册代际、PDC/模式/队列变化的同步失效仍待接入。详见[EPON组播路由依据](port/FTTR.md#original-epon-multicast-route-production)。

51. 接入EPON完整组播：source7/POL0..31保留MAC实例身份，目的32..63使用原厂POL0规则，支持与LAN、FTTR、AP/STA和本机副本混合。以全部64行LLID、注册代际、4/8队列模式及实际队列使能建立准入快照；LLID/PDC/注册/SLA/模式变化先撤销涉及PON的完整流，失败保留旧归属并重试。修复连续撤销失败后PDC刷新可能提前改写映射的路径，关闭光口仍执行硬件停收发。固定优先级按Linux实际队列掩码编译，八队列支持无线逐包COS；四队列动态COS继续整组走软件。32种实例数生命周期、混合副本、9组EPON与9组ITU原厂动作、206项路由故障、2560组键CRC、432项QM、768项P6、12288项VLAN/WME及Image/模块构建通过。PVT真实交付、旧LLID报文退场和资源复用尚未证明，未上实机。详见[EPON原生准入与副本](port/FTTR.md#native-epon-multicast-admission-and-replicas)。

52. 修复EPON PDC重初始化与FE准入不一致：FE已开启时，业务两组表项保持目的24/bypass0，OAM保持CPU16/bypass1/COS7；此前重新配置后仅刷新POL不能修复“源实例未变但目的退回CPU”的表项。实际初始化函数的三种EPON模式、FE开关共1536行及失败重试检查通过。四队列PUC低两位COS定义已有参考证据，但中间NI/QM映射仍需查实，未据此取消整组软件回退。

53. 补齐EPON实际调度表准入：逐活跃实例读取PVT，核验表项/队列使能与全局VOQ位图的交集、原生实例队列映射；拒绝额外链表或不同调度模式，忙/超时撤销准入并保留重试。960组准入与7680次原厂AAL字段解码一致，跨寄存器字段、映射变化、读表和撤销失败以及Image/模块构建通过。四队列中间NI/QM映射与物理退场仍未证明。

54. 接入未知组播泛洪完整事务：实际Linux `br_flood`输出集合进入现有FE/NI复制、本机与无线框架，保留端口泛洪/代理ARP/STP/VLAN/隔离选择，克隆失败或不支持副本不发布部分集合。端口标志在netlink/sysfs写入及回滚前关闭学习命中，补上查询等待定时器到期从泛洪切回MDB时的失效通知。16组原厂未知组播策略、45组Linux选择、真实泛洪到FE/NI完整安装、CPU副本/撤销和现有回归通过；Image/模块构建与补丁零偏差重放通过。组播转逐用户单播、PE直接BCMC、四队列动态COS和完整退场仍未完成，未上实机。详见[未知组播泛洪事务](port/FTTR.md#unknown-multicast-flood-transactions)。

55. 接入逐用户组播转单播：按实际Linux选择记录端口与目的MAC，支持同一VIF多个收件人及混合本机/普通组播副本；NI复制后凭独立不可变cookie核对报文并由CPU改写目的MAC，再进入无线/以太网/PON/P6正常发送路径。目标原厂三参数动作生成器与参考SDK ABI不同，六组实际执行确认目的MAC改写；同步修复STA单播PE队列更新可能改写组播MCGID的问题。真实Linux到FE/NI事务、VLAN、重复决策、hairpin DAD、失败撤销、480项QM、129项解析、768项P6、12288项VLAN/WME及Image/模块通过。直接PE复制、完整退场和资源复用仍未完成，无实机验证。详见[逐收件人转换](port/FTTR.md#per-recipient-multicast-to-unicast-copies)。

56. 补上PE逐收件人转换的接收端：保留POL cookie先于普通DA/STA匹配校验，核对代际、原来源、无线目的、peer及密钥后改写DA；支持预编译固定/COS/DSCP策略，睡眠队列和已选未发布WP撤销后丢弃，不同cookie不合入同一A-MSDU。修复多个撤销WP共用单个丢弃槽造成pause-drain死锁，改用既有完成环并等待EQ信用逐个归还BID。实际MIPS执行247项准入/撤销、4组完整加密TX/RPQ及PS/STA/发布回归通过，固件26368字节、保守栈6848字节，Image/模块通过。本项尚无FE表发布与无线绑定，仍使用前项CPU转换路径；目标FC不含参考SDK同名Wi-Fi M2U路径，不能宣称二者等价。QoS/身份生命周期、NI→PE直接复制、BCMC及完整退场仍待补齐。详见[PE收件人准入](port/FTTR.md#pe-multicast-recipient-admission)。

57. 接入无线/FE共享收件人表的归属：每个无线PE上下文分配独立一致性DMA表，能力签名核验后由FE借用；组播写入、peer路由变化、接口撤销先关闭表代际。无线退出先解除FE引用，再清理NI并等待PE上下文停止回执，任一步失败保留内存；NI供应驱动退出通过绑定锁等待调用者并解除全部借用，避免与无线释放并发。两无线归属/冲突、重复绑定、端口及供应端撤销、失败重试与真实释放函数验证通过，完整学习/M2U、480项QM、40项桥交付及既有单播/密钥/BA回归通过；Image/模块和89项编译输入核对通过。表仍关闭，尚无收件人记录发布及直接NI→PE规则；首VIF之外仍走既有路径。Linux QoS/身份策略编译、所有变更的撤销、复制缓冲独占性、BCMC及完整退场继续待补，无实机验证。详见[无线与FE表生命周期](port/FTTR.md#radio-and-fe-multicast-table-lifetime)。

58. 接入Linux无线组播策略编译及FE存储：在wiphy锁下执行实际mac80211分类/WME，生成64项DSCP、8项显式优先级、内联VLAN结果、WME和no-ACK策略，并绑定真实peer地址/MACID与无线目的。17处QoS/ACM/WME/TSPEC/保留TID/分片阈值及回滚写入先关闭旧代，内置代际跨驱动重载；回调及后续软件TX触发刷新，FE拒绝旧代、错误绑定和非法TID。1179648组Linux分类、12288组VLAN/WME、实际策略写入、无线→FE失败交接及既有完整组播/密钥/BA/4224项TX/BAR回归通过；原厂30项无线派发、32项COS/QoS、12项副本及6项目的改写重放通过，Image/模块、补丁零偏差重放和95项输入核对通过。TSPEC空口计费等不支持场景继续软件；no-ACK仅记录，PE记录编码尚待应用。本项未开放直接NI→PE流，缓冲独占、不可变记录/动作发布、完整密钥模板生命周期、BCMC及退场复用仍待完成，无实机验证。详见[Linux无线策略发布](port/FTTR.md#linux-wireless-multicast-policy-publication)。

59. 接入完整事务的直接NI→PE逐用户复制：每个无线收件人先写独立不可变cookie记录，再安装NI动作和Hash，最后按代际开放PE与学习模板；撤销先清记录代际，失败保留tombstone不复用。MCP2记录固定peer/MACID、密钥CAM、AP/STA与QoS头模板，PE在收取、选择及TXBD发布前复核；授权/密钥/BA/地址和模板变化使策略失效，首次策略可用也撤销先前CPU规则。no-ACK写入无线头与WD并关闭该副本BA。双无线40项EQ/BID/格式/映射故障、6组目标原厂动作、实际学习记录到MIPS消费、264项准入/撤销及8组完整加密TX/RPQ/no-ACK、现有M2U/QM/桥/PS/密钥/BA/PON回归通过；固件26520字节、栈6896字节，Image/模块及96项输入核对通过。物理副本独占性仍基于MCE逐流输出与QM目的EQ分配契约，未实测；当前仅绑定VIF的AP/STA单播收件人，BCMC/DTIM、多VIF、TSPEC、完整退场与资源复用仍缺，未访问实机。详见[直接NI到PE复制](port/FTTR.md#direct-ni-to-pe-multicast-to-unicast-publication)。

60. 接入STA普通组播的NI→PE直发：保留Ethernet组播DA，以关联AP的单播RA和成对密钥发送；AP普通组播仍走GTK/DTIM路径。执行目标原厂属性生产前缀及完整头生成器，确认RA选择及“原厂固定本机TA、Linux保留Ethernet SA”的差异，PE按当前Linux语义修正STA三地址与A-MSDU头。补上共享表解绑的永久记录撤销，保留tombstone，防止重绑/FE代际重启让旧记录重新有效。12组原厂头、6组实际Linux接收地址/密钥/头/聚合函数、两类实际学习记录到MIPS、267项PE准入/撤销、12组加密TX/RPQ/no-ACK及现有STA/AP/PS/M2U/QM/桥回归通过；Image/模块与96项输入核对通过。普通AP BCMC、多VIF、TSPEC、完整退场和资源复用仍待补，未访问实机。详见[STA组播与解绑撤销](port/FTTR.md#sta-group-transmission-and-detached-table-revocation)。

61. 按目标原厂补齐AP普通BCMC的HIQ选择：所有普通AP组播DATA统一进入DMA9/QSEL0x11，避免休眠结束后新GTK帧进入普通队列而越过HIQ旧PN。实际执行原厂PN/完整元数据/两芯片WD，确认原厂内嵌IV、原生ARM分配PN后以SW_SEC_IV交硬件插入的适配边界；不新增PE独立PN分配者。执行也纠正此前笔记：原厂48位PN溢出到0而非1。Linux真实PS决策→原生monitor/TX的90次发送、45组实际请求到MIPS描述符、96组原厂PN/元数据及192组WD通过；旧代码可复现首个非DTIM队列断言失败，密钥/BAR/STA/组播/桥回归、Image/模块和180项源码核对通过。未执行硬件DTIM调度、空口PN排序或完整退场；AP直达PE绕过CPU、资源复用和全部FTTR验收仍未完成。详见[AP高队列与PN](port/FTTR.md#ap-bcmc-high-queue-and-software-pn-ownership)。

62. 补齐默认多业务所需的UNI/ANI VLAN语义分离：CLI与常驻策略接受`filter:VID`，UNI保持原厂透明mode4，仅ANI/GEM桥端口过滤该VID；原有数字VID的access语义不变。执行原厂UNI初始化及自动模式选择确认默认mode4，避免用多个access策略覆盖共享UNI。真实AF_PACKET单/共享UNI业务对比原厂ANI报文及EVTO规则、重复接管、部分失败恢复通过；带认证和授权清理的双ONU常驻迁移/撤销/漂移/超时/重启通过。无授权清理的模拟库存重建留下旧DBA，被控制器拒绝，不计通过。GCC14.4/musl包编译安装通过；全局同步遇已有LuCI补丁冲突后直接构建已同步的FTTR包。仍需多个GEM的共同所有权、逐业务网口与完整生命周期，不能据此称多业务或默认配置导入完成。详见[UNI与ANI独立过滤](port/FTTR.md#independent-uni-mode-and-ani-vlan-filtering)。

63. 解除同ONU本地GEM接口撞名：首个GEM保留`fttr-u<ID>`，其他按原厂序号命名；验证GEM/ONU对应及4090上限。启动FE撤销后的接管枚举清理各业务接口，核对父口、group、802.1Q/序号和所有权，兼容旧单业务高序号的无后缀接口；遇冲突报错，不承诺整批删除原子性。真实veth/桥验证同ONU序号1/2/7/60共存、双向原厂标签一致、不同桥隔离、单项删除保留兄弟FDB、旧接口清理和错协议/错序号拒绝；常驻认证迁移、FE桥学习/组播及GCC14.4/musl包构建通过。远端多业务事务与控制器调度仍未完成，未访问实机。详见[同ONU多个GEM接口](port/FTTR.md#multiple-local-gem-interfaces-for-one-onu)。

64. 接入常驻共享多业务集合：ASIC/FPGA2按原厂数据槽1..7预留独立T-CONT/queue/GEM/ANI，私有MIB规划快照不冒充远端观测；后续业务核对共享对象及完整EVTO，按实际写入保留首个所有权。集合配置完成与同步通过后才逐项接入本地网口；失败逆序恢复，清理失败保留共享依赖但关闭全部本地数据授权。迁移、掉线、重读、超时与退出覆盖全部业务，零同步值时逐GEM探测；精确SN替换整套通配策略，同桥所有GEM标记共享组播成员以避免重复复制。ASIC双业务、FPGA2七业务CTC、逆序迁移/失败/缺队列/UNI和EVTO漂移/最后GEM丢失/精确覆盖/本地接桥失败通过；FPGA1标准组播、单业务CLI、管理与FE回归通过，GCC14.4/musl包构建和55项源码核对通过。当前显式多业务限共享透明UNI加不同ANI VLAN，未自动导入原厂默认数量/带宽/卡口限制，持久所有权与物理退场仍未完成。详见[常驻多业务集合](port/FTTR.md#resident-multi-service-collections-and-shared-ownership)。

65. 按实际原厂调用链补齐ASIC/FPGA2默认业务生成：`factory total bridge`展开管理槽以外的1..7个数据业务，默认total2不做ANI VLAN过滤，更多业务为每个GEM设置VID100×slot，UNI保持透明，最后一个业务拥有组播。原厂执行确认API队列1对应Related Port优先级0；FTTR-100M名义0/50/100不写硬件，实际supervisor下发DBA为原生单位0/0/19440。FTTR-402实际配置上限为首Ethernet卡4个UNI、首VEIP卡1个UNI；执行原厂MIB解析、卡/UNI生产和分组后依实际实例选择，超额或孤立端口不修改。缺分类、空首VEIP、缺队列在业务写入前拒绝，最后GEM失败逆序恢复。单/双/七业务standard/CTC完整命名空间测试、显式多业务及CLI回归、GCC14.4/musl包构建通过；仍未导入原厂保存配置或用户修改profile，FPGA1默认生成待补，持久OMCI归属与硬件完整退场仍未完成。未访问实机。详见[原厂默认生成与卡口限制](port/FTTR.md#factory-default-service-generation-and-card-limits)。

66. 将FTTR软件代际从QM片段延伸到完整skb：内建64位闭合/启用代际跨驱动卸载保留、不回绕；业务RX、GEM/共享口发送、NFQUEUE和邻居解析前记录，已有包不能重新标记。Linux RPS接收、qdisc/BUSY最终发送、NFQUEUE恢复、mac80211与原生无线最终提交及NI DMA复制前拒绝旧代际；复制/分片保留、GRO/重组/A-MSDU禁止跨代混合。组播口单独关闭也撤销旧CPU副本。真实生产函数的门控、队列、复制、GRO/重组、无线拒绝及768组QM检查通过；旧独立预取fixture缺历史分段字段，不计通过，使用完整QM用例替代。13项补丁零fuzz复现与Image/模块构建通过。仍不证明已进入DMA/AFT/PE/ES/MiniOLT的报文退场，不能解除硬件ID隔离。未访问实机。详见[跨软件队列的FTTR代际](port/FTTR.md#fttr-packet-generations-across-software-queues)。

67. 将CPU报文代际延伸到PE私有发送队列：保留原skb的64位代号，双无线共享镜像在关闭/重开时同步，PE构造WD/TXBD前复核，未发布的旧请求通过独立WP回收字归还ARM。ARM解除DMA映射、释放skb并唤醒队列，跳过未生成的无线序号、ACK/重试/速率反馈；PE统一维护实际TXBD连续位置，避免丢弃请求造成环空洞。执行目标原厂统计/反馈函数72组，确认软件丢弃状态4不计空口成功或失败；实际MIPS主循环69组、ARM回收257批及原厂动作/组播/双无线/绑定失败/密钥BA/BAR/BCMC回归通过。固件26692字节、保守栈6896字节，Image/模块及源码一致性检查通过。仅覆盖CPU→PE尚未发布的请求；PCIe、NI DMA/AFT、ES、MiniOLT在途与完整资源复用仍未完成，硬件ID继续隔离。未访问实机。详见[PE等待请求撤销](port/FTTR.md#fttr-generation-revocation-inside-the-pe-transmit-queue)。

68. 补上CPU→无线已接受请求的完成屏障：发送在代际关闭锁下取得引用，映射后遇关闭则拒绝并解除映射；正常RPQ、PE软件丢弃或已完成设备退场的清理在解除DMA映射及处置skb后才归还引用。`fttr_forward`和组播同步关闭等待最多1秒，超时保持关闭、撤销FE启用，迟到完成后允许重试；单独注销无线镜像不会抹掉未完成引用。执行目标原厂完整`phl_recycle_payload`216组确认回调失败保留WP/count、成功后释放，原72组状态测试通过。核心0..512引用、256并发关闭/完成竞态、16448组真实RPQ回调、257批PE回收及关闭失败/重试、退出保留、P6/BAR/BCMC回归通过；Image/模块和源码一致性检查通过。该屏障仅覆盖CPU来源无线payload；直接NI→PE、NI DMA/AFT/ES/MiniOLT及硬件资源复用仍未完成，未访问实机。详见[等待CPU无线请求完成](port/FTTR.md#waiting-for-accepted-cpu-radio-requests-during-fttr-withdrawal)。

69. 补齐直接NI→PE的FTTR BID与EPP快照退场：按原HEADER_A来源6保存64位代号至BID头部，闭合到达、睡眠/已选未发包拒绝，聚合要求代号相同；每个BID从EPP接管计数至RPQ后EQ实际PUSH并屏障才减，强制ARM退出同样保留失败后缀。FE撤销后每次新请求快照8个COS的EPP producer，读完边界且BID归零后PE确认；暂停改布局时不采样，旧ACK/新无线注册/序号耗尽不误开放。249组实际MIPS来源/COS/格式/回绕/PS/WP/RPQ/EQ/部分聚合/暂停场景、原厂动作6组及40故障、核心双无线/并发和既有回归通过。固件28468字节、栈6960字节，Image/模块与源码一致性检查通过。仅保证已接管BID及捕获EPP前缀；MCE/NI DMA/AFT可在重开后迟到的上游包仍缺生产者屏障，ES/MiniOLT和完整复用仍未完成，硬件ID继续隔离，未访问实机。详见[直接NI到PE的BID退场](port/FTTR.md#direct-ni-to-pe-fttr-bid-and-epp-prefix-drain)。

70. 补齐EPON四队列动态COS：逐副本保留三位COS，由已核验PUC模式使用低两位选择四队列；其他LAN/P6/无线副本保持原优先级。准入要求全部可达队列已启用、所有八种COS产生相同最终VLAN。目标原厂EPON生成器本身覆盖COS0..7及实例31，FC/NI固定动作与动态保留动作的结果字段一致；768组实际原厂/原生动作与CPU头、960组PVT准入和7680次字段解码通过。36组低/高实例混合事务、逐队列禁用、COS变化复用、206组路由故障、12288组Linux VLAN/WME及Image/模块构建通过。PUC位切分按原厂参考契约建模，物理队列交付与完整退场仍未验收，未上实机。详见[四队列动态COS](port/FTTR.md#epon-four-queue-dynamic-cos)。

71. 查实FPGA1默认业务生产链：目标`onuinfo_fpga`固定创建T-CONT/GEM/flow1及API queue1，再调用独立UNI组播接口；15组实际执行确认首错停止、不做ASIC的删除重建。实际profile/卡分配和共享UNI枚举确认四Ethernet加一VEIP；72次带宽包装执行均无硬件调用或非栈写入。默认本地授权来源、队列与组播适配仍待补，未取消FPGA1默认策略限制，也未把光功率测量的临时授权当成业务默认值。本项是原厂核对进展，不是已完成的产品支持，未访问实机。

72. 补齐AP直达PE的共享GTK PN前置条件：CPU请求携带非回绕CAM代号，PE在实际TXBD发布前分配PN，mac80211软件CCMP/快速IV与它共用计数器；ARM内部锁及ARM/PE有界握手防止双分配者重号。关闭先撤销代号，成功归还最新PN，超时解除借用指针并禁止旧软件计数器继续发送；安装失败保留CAM归属，迟到请求不能消费新密钥PN。非FTTR GTK也接入软件丢弃回收，回读/debugfs同步共享值。6万次并发、640组进位/代号、32次实际软件CCMP交替、21组实际MIPS发布/缺信用/竞争/撤销、原厂96组PN及192组WD和既有回归通过，Image/模块、补丁重放与源码核对通过。固件28960字节、保守栈6960字节；直接BCMC准入/模板、DTIM与空口时序及完整退场仍未完成，未上实机。详见[共享GTK PN](port/FTTR.md#shared-gtk-pn-across-software-ccmp-and-pe-publication)。

73. 补齐直接AP BCMC所需的共享非QoS序列号：查实原厂bank9/TID分配与Linux接口序列号域的差异，将Linux普通/快速发送、CPU以太网转换和PE非QoS转换接入同一接口计数器。NI在BID保存接口代号，选择和最终发布均复核，取得TXBD信用后才分配；GTK竞争重试保留已分配值，旧接口包不能消费新接口序号。解绑成功归还Linux下一序号，超时解除借用指针并禁止旧计数器回退；角色创建失败回滚绑定。240次原厂调用、24576次并发分配、4100次实际Linux/PE交替、32次快速路径、24组实际MIPS收发/回收/竞争/换代、8448组请求构造及既有回归通过；Image/模块、补丁重放和源码核对通过。固件29400字节、保守栈6984字节。默认GTK选择、直接BCMC模板/准入和HIQ/DTIM接入仍待补，完整退场和物理验收尚未完成，未上实机。详见[共享非QoS序列号](port/FTTR.md#shared-non-qos-sequence-across-linux-and-pe)。

74. 对齐本地main `2ad19d9`的受保护preload、独立硬件WP标签/软件请求所有权、扩大请求池、64-MSDU批量接收和独立NI组帧。保留FTTR代际/cookie、共享GTK PN/序列号与STA/no-ACK语义，完整迁移回收/等待表。专项测试复现并修复两处集成缺陷：未发布NI请求误释放tag0，CPU软件丢弃回报误走硬件标签翻译。6144/5120次实际MIPS提交、2048/1024在途压力、251组FTTR退场、257批CPU丢弃、16448组ARM RPQ、16896组请求、强制回收及既有回归通过。Image/模块、66份无线源码一致性通过，PE_UPLINK30740字节/保守栈7048字节。仅将main整合进fttr-rewrite，未反向合并；未上实机，完整FTTR退场与直接AP BCMC仍待补齐。详见[独立TX所有权整合](port/FTTR.md#main-alignment-with-independent-radio-tx-ownership)。

## 建议接着做的工作

| 方向 | 当前缺口 |
| --- | --- |
| 真实数据路径验收 | 核验 P6 收发、光学就绪、ONU 注册、OMCI、业务双向通流及真实 FE 命中；记录实测从光猫型号和固件，而非宣称通用兼容。 |
| GEM 复用前旧报文退场 | CPU门控/代际及无线payload回收屏障已接入；直接NI→PE现等待已接管BID与关闭时8-COS EPP快照完成并归还EQ。MCE/NI DMA/AFT重开后迟到仍缺上游生产者屏障，ES/MiniOLT、保留WD页和已交付应用的数据仍未闭合。DMA有两个预取描述符；仅删除Hash/cache不能证明所有旧报文已消失，硬件ID继续隔离。 |
| Wi-Fi 配置联动 | 私有 OMCI 的 CLI/常驻策略已接入；独立 syncserv JSON 链已有发现、显式注册回复、回读及写入/可读字段比较，并加入绑定 MAC/自报 SN 的常驻多设备、多 SSID 策略和默认关闭的独立服务。注册、掉线或替换会取消旧事务；支持持久配置版本及发送前意图记录，重启后对已尝试配置只回读、不自动重发密码。自报 SN 核对不等于认证。下一步补齐实际从设备能力/身份、密码安装核验、实物掉电验收、真实 CWMP/WAN 就绪来源及 UCI 变更联动；显式 UCI 初始快照映射已实现。两条路径均缺实际从设备验收。 |
| 配置恢复与服务策略 | OMCI/业务记录仍在 `/var/run`；syncserv 新增持久版本写入意图和只读恢复，但未实测闪存掉电；原厂全局 LOID、逐 ONU 五种比较/选择、SN持久绑定/自动学习与迁移已接入；PLOAM 获取、逐 ONU 原厂配置导入、ASIC/FPGA2默认业务生成及共享多业务已接入；原厂保存配置导入、用户修改profile、FPGA1默认配置与远端资源归属恢复仍待补齐。 |
| FPGA 机型 | 已实现保留 bitstream 核验打包、P6 先于镜像加载、加载后绑定 MiniOLT、原厂 post-DONE 控制、工厂文件选择四种光学校准、UX3326 温度调节/恢复、独立启动流程及新版 FPGA1 工厂认证。FPGA2 原厂程序分派、控制器启动及业务/认证/管理已接入；下一步补齐 SFP/告警并确认实际器件与物理行为。不能按 LAN 速率猜硬件后端。 |
| 完整 FTTR 管理 | 远端组播事务、常驻共享端点、显式TC及完整报文驱动的LAN/FTTR/ITU与EPON/无线入站、AP/STA副本、未知组播泛洪和逐用户组播转单播已接入；绑定VIF的逐用户副本可经NI直接进入PE，具备Linux策略、不可变记录及密钥模板撤销。EPON四/八队列均接入动态COS；共享GTK PN前置条件已接入，直接PE BCMC准入/模板、DTIM、多VIF、TSPEC计费、组号安全复用和物理复制验收仍未完成。从设备网关配置、重启/升级、漫游协调及其他原厂能力分支仍有缺口。 |

## 资料与验证边界

原厂实现是行为依据；其他 SDK、标准和外部项目仅作参考。不能照搬原厂运行时或复制其他项目实现来填补未查实的部分。
原厂存在明显错误时可以修正，但应说明证据和修正原因。例如本分支已复现并修正了部分“超时仍返回成功”的路径。

公开 Git 分支只包含已提交的源码、说明和检查工具。以下本地输入、产物不随分支推送：

- `analysis/`、`evidence/`：原厂提取文件、反汇编研究数据及 Python 依赖。
- `artifacts/`、`logs/`：本地编译树、暂存包、定向检查报告。
- 原厂 `.ko`/`.so`、FPGA bitstream、设备校准和账号配置数据。

因此，文档提到的本地 JSON 报告不是公开仓库自带的验收结果，原厂对比检查也不能在缺少输入时直接复现。
接手者须自行准备有权使用的匹配原厂文件；不要提交设备的真实账号、密钥或校准数据。
原厂 ARM64 指令执行工具需要 Python 的 `pyelftools`、`unicorn` 和 `aarch64-linux-gnu-objdump`；具体文件由各脚本的 `--help` 或源码指定。
部分旧脚本默认读取研究者的本地目录，移植时应显式传入对应文件或调整输入路径，不能伪造原厂输入。

只想先检查源码和源端口分流，可以在有 Python 3 与 C 编译器的 Linux 环境执行：

```sh
python3 scripts/test_fttr_dispatch.py
```

此检查不需要原厂二进制，也不证明光路或硬件通流。AF_PACKET/veth 检查需要 Linux 网络命名空间和相应权限，应在独立命名空间运行。

可选 FTTR 包和内核配置分别见 [包配置片段](port/configs/tyfza1-fttr-packages.config)与[内核配置片段](port/configs/tyfza1-fttr.config)，两者不能混用。
准备包时 `TYFZA1_PROJECT` 指向项目根目录，`TYFZA1_FTTR_SERDES_SOURCE` 指向匹配原厂的 `/etc/serdes`。
已在本 worktree 的独立 OpenWrt 树完成包含 FTTR/FPGA 包的 `world` 构建，核对源码、模块、原生程序、固件输入及 DT，并生成分开的 ASIC/FPGA RAM FIT，验证载荷哈希和签名。普通 sysupgrade 仍使用普通 UBI DT，不能作为 FTTR 镜像；本地 RAM FIT 也未启动或发布。详见[整机构建范围](port/FTTR.md#full-offline-build-after-main-alignment)。**功能仍有明确缺口，尚未完成 FTTR 实机验收。**

## 如何接着贡献

直接检出这个研究分支：

```sh
git clone --branch fttr-rewrite https://github.com/LeoooChen/PonWRT-TYFZA1.git
cd PonWRT-TYFZA1
git switch -c fttr-your-topic
```

没有本仓库写权限时，先在 GitHub 上 Fork，再从自己的 Fork 克隆；提交后发起 Pull Request。
**PR 的目标分支选择本仓库的 `fttr-rewrite`**，使 FTTR 研究改动先在这里汇集。
请保留发现、未完成项、适用机型和验证范围；一次 PR 聚焦一个可审查的具体问题，做对应定向检查即可。
真实 bug 需要修复根因，不以吞错误、返回假成功、猜硬件型号或堆叠兼容分支掩盖。
通过实机验证、审查并准备好整合后，再单独考虑合入 `main`。
