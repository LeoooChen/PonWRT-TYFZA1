# TYFZA1 FTTR 研究分支交接

本分支 `fttr-rewrite` 用于协作开发 TYFZA1 的原生 OpenWrt FTTR 支持。
代码最初从 `dcf470f`（`0.7.0-alpha` 时期）分出，保留了逐步实现和修正的提交记录。
已通过 `61d24e6` 合入最新 `main` 的 `9331c6e`，继续在 `fttr-rewrite` 开发。
这不是新的发布版本，也不是已经完成验收的固件。

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

13. 新增可选 FPGA 镜像包：核对保留 top.bin 的长度和 SHA-256 后原样安装，开发 DT 在 SPI probe 中加载镜像，MiniOLT 通过设备依赖等到加载成功且 DONE 就绪后才访问 MDIO，避免把未加载的零寄存器识别成 FPGA1。加载器绑定期间禁止手动重写，设备链约束卸载顺序。新增 FPGA1/FPGA2 原厂 post-DONE 寄存器控制与错误停止检查，真实内核对象/模块链接、DT 和固件包暂存验证通过。仍未完成 FPGA 光学、核心、ONU 业务启动或实机验证。详见[FPGA 启动依赖](port/FTTR.md#fpga-boot-image-and-miniolt-dependency)。

## 建议接着做的工作

| 方向 | 当前缺口 |
| --- | --- |
| 真实数据路径验收 | 核验 P6 收发、光学就绪、ONU 注册、OMCI、业务双向通流及真实 FE 命中；记录实测从光猫型号和固件，而非宣称通用兼容。 |
| GEM 复用前旧报文退场 | ES 队列清空函数尚未接入完整撤销流程。DMA 有两个预取描述符，PE/MiniOLT 缓存没有查实的完整退场依据；仅删除 Hash/cache 不能证明所有旧报文已消失。 |
| Wi-Fi 配置联动 | 私有 OMCI 的 CLI/常驻策略已接入；独立 syncserv JSON 链已有发现、显式注册回复、回读及写入/可读字段比较，并加入绑定 MAC/自报 SN 的常驻多设备、多 SSID 策略和默认关闭的独立服务。注册、掉线或替换会取消旧事务；支持持久配置版本及发送前意图记录，重启后对已尝试配置只回读、不自动重发密码。自报 SN 核对不等于认证。下一步补齐实际从设备能力/身份、密码安装核验、实物掉电验收、真实 CWMP/WAN 就绪来源及 UCI 变更联动；显式 UCI 初始快照映射已实现。两条路径均缺实际从设备验收。 |
| 配置恢复与服务策略 | OMCI/业务记录仍在 `/var/run`；syncserv 新增持久版本写入意图和只读恢复，但未实测闪存掉电；远端资源归属恢复、默认策略导入、多业务和认证策略仍需实现。 |
| FPGA 机型 | 已实现保留 bitstream 核验打包、加载后绑定 MiniOLT，以及原厂 post-DONE 寄存器控制。下一步补齐 P6/FPGA 核心/ONU 完整时序，以及该样本的实际光学器件确认。不能按 LAN 速率猜硬件后端。 |
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
此前验证包括定向 native/musl 包构建、6.18 驱动对象编译及部分原厂指令对比；**尚无完成 FTTR 实机验收的全量固件构建交付**。

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
