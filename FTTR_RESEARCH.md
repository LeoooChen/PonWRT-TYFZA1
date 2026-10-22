# TYFZA1 FTTR 研究分支交接

本分支 `codex/fttr-rewrite` 用于协作开发 TYFZA1 的原生 OpenWrt FTTR 支持。
代码最初从 `dcf470f`（`0.7.0-alpha` 时期）分出，保留了逐步实现和修正的提交记录。
它没有自动跟随之后的 `main` 更新，不是新的发布版本，也不是已经完成验收的固件。

**当前状态：已有原生驱动、管理和转发基础；真实光路、从光猫注册及业务通流、FTTR 硬件加速均未通过实机验收。**
“实现完成”“编译通过”“原厂指令对比通过”在这里指各自明确的范围，不能相互替代。

## 从哪里开始

- [完整研究记录和未完成项](port/FTTR.md)：原厂函数地址、寄存器、已实现行为、验证范围与缺口。
- [内核驱动目录](port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/)：`rtl9617c-fttr*` 为 MiniOLT、光学、P6、OMCI 和队列控制；共享 NI/QM/DMA/FE 的接入也在此目录。
- [控制器和协议实现](port/packages/tyfza1-fttr/src/)：`fttrd.c` 为常驻控制器，`fttrctl.c` 为开发命令，`omci-*` 为原生协议和配置事务，`fttr-net.c` 管理按 GEM 分流的 VLAN 端口。
- [原厂执行工具](tools/)和[定向检查](scripts/)：相关文件名含 `fttr`。每个检查有独立用途及输入要求，不必运行全量测试。
- [业务策略示例文件](port/packages/tyfza1-fttr/files/tyfza1-fttr.services)：默认只有注释，没有替未知从光猫选择网络策略。

## 已经实现的部分

1. MiniOLT MDIO/regmap 和光学 I2C；确认的 ASIC9690/GN25L99 路径、SoC P6 与对端 SerDes、核心初始化及 DBA。另有 FPGA Manager 和开发设备树，FPGA 自动启动链尚未完成。
2. 独立 `fttr0`，P6 源端口 6 与运营商上联 PON 源端口 7 分开；原厂 OMCI 封装、CRC、异步 MIB 读取和实体模型。
3. 显式策略选择实际 UNI/T-CONT/队列，创建并读回 GEM、桥接和 VLAN 配置；原始错误及清理失败均保留。
4. 多 ONU 异步调度、运行时记录、序列号更换及 MIB 同步丢失后的重新读取；按 GEM Port-ID 添加外层 VLAN，接到明确指定的 Linux 网桥。
5. 原厂格式的 P6 CF/FE 接入、删除端口前同步撤销 Hash/cache 流；P6 ES 队列清空函数和 DMA/AFT 配对复位修正。
6. 原厂 `0xff69/0xff6a` 远端 Wi-Fi 更新事务模块，支持预读取、核验与恢复。**尚无 daemon/CLI 调用流程**，也未实现 OpenWrt 配置到原厂枚举的映射。

## 建议接着做的工作

| 方向 | 当前缺口 |
| --- | --- |
| 真实数据路径验收 | 核验 P6 收发、光学就绪、ONU 注册、OMCI、业务双向通流及真实 FE 命中；记录实测从光猫型号和固件，而非宣称通用兼容。 |
| GEM 复用前旧报文退场 | ES 队列清空函数尚未接入完整撤销流程。DMA 有两个预取描述符，PE/MiniOLT 缓存没有查实的完整退场依据；仅删除 Hash/cache 不能证明所有旧报文已消失。 |
| Wi-Fi 运行时接入 | 将已有事务接入常驻控制器，支持显式策略、能力检查、超时及身份更换。原厂 SSID/共享密钥字段各 24 字节，不能静默截断。 |
| 配置恢复与服务策略 | 当前记录在 `/var/run`，不是掉电后的持久事务日志；远端资源归属恢复、默认策略导入、多业务和认证策略仍需实现。 |
| FPGA 机型 | 原厂 bitstream 导入、加载及后续初始化时序，以及该样本的实际光学器件确认。不能按 LAN 速率猜硬件后端。 |
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
git clone --branch codex/fttr-rewrite https://github.com/LeoooChen/PonWRT-TYFZA1.git
cd PonWRT-TYFZA1
git switch -c fttr-your-topic
```

没有本仓库写权限时，先在 GitHub 上 Fork，再从自己的 Fork 克隆；提交后发起 Pull Request。
**PR 的目标分支选择本仓库的 `codex/fttr-rewrite`**，使 FTTR 研究改动先在这里汇集。
请保留发现、未完成项、适用机型和验证范围；一次 PR 聚焦一个可审查的具体问题，做对应定向检查即可。
真实 bug 需要修复根因，不以吞错误、返回假成功、猜硬件型号或堆叠兼容分支掩盖。
通过实机验证、审查并准备好整合后，再单独考虑合入 `main`。
