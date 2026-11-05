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

1. MiniOLT MDIO/regmap 和光学 I2C；确认的 ASIC9690/GN25L99 路径、SoC P6 与对端 SerDes、核心初始化及 DBA。另有 FPGA Manager、原厂镜像打包及加载后绑定 MDIO 的开发设备树依赖；完整 FPGA 光学/核心/ONU 自动启动链仍未完成。
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

13. 新增可选 FPGA 镜像包：核对保留 top.bin 的长度和 SHA-256 后原样安装，开发 DT 在 SPI probe 中加载镜像，MiniOLT 通过设备依赖等到加载成功且 DONE 就绪后才访问 MDIO，避免把未加载的零寄存器识别成 FPGA1。加载器绑定期间禁止手动重写，设备链约束卸载顺序。新增 FPGA1/FPGA2 原厂 post-DONE 寄存器控制与错误停止检查，真实内核对象/模块链接、DT 和固件包暂存验证通过。进一步按 `libponrelay1p25g` 补上 FPGA 专属 OMCI 初始化、固定目的 MAC/基线封装、只读 ONU 状态和保留其他位的 TX 控制；12组原厂对比、18个报文、ASIC OMCI/FPGA 启动回归和6.18.55对象编译通过。原厂 FPGA 带宽配置接口为空实现，不能据此套用 ASIC 的管理 grant/DBA。完整光学、核心、ONU 业务启动或实机验证仍未完成。详见[FPGA 启动依赖](port/FTTR.md#fpga-boot-image-and-miniolt-dependency)及[管理通道](port/FTTR.md#fpga-management-transport)。

14. 补上 FPGA1 独立 DBA/资源编号及控制器接入：原厂 `libasicdrv` 的 FPGA1 授权位于 `40400+ONU*2`，fixed 与 ONU 状态共用下一字，写入只替换低16位；原厂 FPGA relay 的 Alloc-ID 为256..319，每 ONU 一个业务槽。CLI/常驻控制器按真实 backend 选择资源编号，将业务槽1映射到 FPGA1 物理槽0；管理会话不写 ASIC 管理授权，P6 就绪检查不再读取 FPGA 上不存在的 WTG 属性。63组原厂 DBA、64个分配/占用及 Set 报文、CLI 事务、FPGA1/ASIC 常驻生命周期与启动回归、真实软件包/内核模块构建通过。FPGA2 的底层 DBA 访问已对比，但其物理授权表与 relay 编号的对应尚未查实，业务入口明确拒绝且仍列为缺口。完整启动、真实授权/流量及旧报文退场仍未完成。详见[FPGA DBA 接入](port/FTTR.md#fpga-dba-and-controller-integration)。

15. 按原厂 `rtkoltbosa` 的四张可达操作表补齐 UX3326、UX3326S、GN25L99、GN25L81 初始校准及可用身份检查；FPGA 从本机工厂文件读取型号和地址，动态绑定 I²C 校准客户端。常驻启动分开 ASIC/FPGA 流程，FPGA 使用 P6 mode5、原厂 post-DONE 设置和650ms等待，再校准、配置管理通道并最后启用 TX。四种 ARM64 原厂写序列、逐笔错误停止、生产驱动配置/状态、动态绑定、启动脚本和三种后端启动检查通过，真实软件包及 MiniOLT/BOSA 模块编译链接通过。UX3326 无原厂硅片身份判据，不能把地址应答写成芯片识别；持续监控/DDMI、SFP 分支、较新 FPGA1 的认证和实际光学器件/流量仍未验收。详见[光学与 FPGA 启动](port/FTTR.md#factory-selected-optics-and-fpga-bootstrap)。

16. 接入新版 FPGA1 的本机工厂认证：根据原厂 MIB2715 导入8个32位字，校验格式并以私有运行时文件提供给驱动；仅 backend1 且版本高于 `230406ff` 才请求输入，按原厂顺序写57..50、等待1.2秒并读5e。原厂认证失败仍继续，重写则返回错误，在启用 TX 前停止并清理。36组实际原厂启动分支/寄存器/结果及 libmib 转换、逐笔 I/O 失败、输入失效、控制器认证失败清理、FPGA 管理和工厂导入回归通过，软件包及内核模块编译链接通过；没有实机认证验收。详见[FPGA1 工厂认证](port/FTTR.md#fpga1-factory-authentication)。

17. 补齐 CPU 经 `fttr0` 发包时的 DEEPQ：原厂虚拟 CPU 端口分配设置 DEEPQ，发送构造将它写到 Header A bit62；此前重写只给 FE 输出设置，CPU 发送遗漏。现补上该位，使管理和业务 CPU 报文同样使用 P6 的端口14队列与背压。执行原厂分配/位赋值及生产发送/DMA 打包，确认旧代码失败、新代码在64/1518/1996字节帧通过；4096次 DMA 混合发送、环回绕/BUSY 和 QM 分流回归通过。仍未证明物理调度和完整报文退场。详见[CPU P6 DEEPQ](port/FTTR.md#cpu-originated-p6-deepq)。

18. 补齐 UX3326 原厂常驻温度调节与复位恢复：按工厂温度开关选择200ms/1000ms周期，读取温度后查84项表并更新调制参数；每1.6s/2s检查复位特征与原厂光功率阈值，按需重载校准、等5s、关光1s及开光1s复查。恢复由 `fttrd` 事件循环持有，退出后不再有延迟开光；持续低功率或 I²C/工厂输入错误进入原有 TX/FE 清理。补齐 MiniOLT SMBus 双字节访问及字节序。514组温度边界、63组功率输入、四段完整原厂循环、生产驱动/生命周期、真实命名空间恢复/失败/退出及原有控制器回归通过，包和内核模块编译链接通过。仍未做实际温漂、功率或恢复验收；完整 DDMI/SFP 仍待补齐。详见[UX3326 常驻控制](port/FTTR.md#ux3326-temperature-adjustment-and-recovery)。

19. 修复 P6 重配期间的软件提交竞态：原有停队列后，共享 DMA 定时器可能依据旧 `configured` 状态再次唤醒；新增与 DMA 提交共用锁的 P6 门控，同时约束定时器唤醒与实际入队。停止/重配失败保持关闭，仅正常打开或重配成功恢复，其他 DMA 使用者继续工作。生产回调交错检查先复现旧错误再验证修复，包含 FE/SerDes 错误、关闭、重新打开及其他端口；共享 FE、4096次 DMA 回绕/BUSY 与内核 Image/模块构建通过。这只解决软件生产者窗口，DMA 预取及下游完整退场仍未完成。

20. 接入原厂永久停用/显式恢复 ONU：FPGA 停用整字改写下行状态、清除共用固定授权，等待10ms再清上行状态；ASIC 仅修改屏蔽位，两者 PLOAM 写入长度也不同。新 `fttrctl ... onu-admin` 通过常驻控制器按序列号校验，先暂停 FE、撤销该 ONU 的业务 VLAN/FDB 与 DBA，再调用驱动；驱动在控制锁内重查身份，成功后才恢复其他 ONU 的 FE。超时或 I/O 失败触发 TX/FE 退出清理。18组实际原厂状态入口、逐笔故障、序列号/读回/解析检查，以及 ASIC/FPGA1 两套真实守护进程与 CLI 双 ONU 生命周期测试通过；既有控制器、远端 Wi-Fi、光学启动回归及包/内核模块编译链接通过。定时停用、跨核心复位的持久屏蔽策略、完整报文退场和物理 PLOAM 接受仍未完成。详见[ONU 停用与恢复](port/FTTR.md#resident-onu-deactivation-and-explicit-resume)。

21. 补齐原厂临时停用 state4：FPGA 在约100ms扫描中等待注册位清除后仅清屏蔽位，ASIC 按8秒离线扫描及15秒回调允许释放；显式恢复仍保留原厂不同的整字/掩码行为。控制器按序列号持有待恢复状态，期间不重建业务；后续永久停用、恢复、身份变化及退出取消旧任务，驱动重新核对身份/状态且保留同字授权位。24组原厂释放条件与生产寄存器操作、事件循环边界/故障，以及 ASIC/FPGA1 双 ONU 的临时恢复、覆盖、替换和退出检查通过；控制器/CLI、FPGA 光学启动回归和包/模块构建通过。持久管理策略、物理报文退场与 PLOAM 实机接受仍未完成。

## 建议接着做的工作

| 方向 | 当前缺口 |
| --- | --- |
| 真实数据路径验收 | 核验 P6 收发、光学就绪、ONU 注册、OMCI、业务双向通流及真实 FE 命中；记录实测从光猫型号和固件，而非宣称通用兼容。 |
| GEM 复用前旧报文退场 | ES 队列清空函数尚未接入完整撤销流程。DMA 有两个预取描述符，PE/MiniOLT 缓存没有查实的完整退场依据；仅删除 Hash/cache 不能证明所有旧报文已消失。 |
| Wi-Fi 配置联动 | 私有 OMCI 的 CLI/常驻策略已接入；独立 syncserv JSON 链已有发现、显式注册回复、回读及写入/可读字段比较，并加入绑定 MAC/自报 SN 的常驻多设备、多 SSID 策略和默认关闭的独立服务。注册、掉线或替换会取消旧事务；支持持久配置版本及发送前意图记录，重启后对已尝试配置只回读、不自动重发密码。自报 SN 核对不等于认证。下一步补齐实际从设备能力/身份、密码安装核验、实物掉电验收、真实 CWMP/WAN 就绪来源及 UCI 变更联动；显式 UCI 初始快照映射已实现。两条路径均缺实际从设备验收。 |
| 配置恢复与服务策略 | OMCI/业务记录仍在 `/var/run`；syncserv 新增持久版本写入意图和只读恢复，但未实测闪存掉电；远端资源归属恢复、默认策略导入、多业务和认证策略仍需实现。 |
| FPGA 机型 | 已实现保留 bitstream 核验打包、加载后绑定 MiniOLT、原厂 post-DONE 控制、工厂文件选择四种光学校准、UX3326 温度调节/恢复、独立启动流程及新版 FPGA1 工厂认证。下一步补齐 FPGA2 授权绑定、原厂 P6/加载时序和完整 DDMI/SFP，确认该样本的实际器件。不能按 LAN 速率猜硬件后端。 |
| 完整 FTTR 管理 | 从设备网关配置、组播、重启/升级、漫游协调及其他原厂能力分支仍未完成。 |

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
