# TYFZA1 无线移植实施顺序

2026-10-09，依据用户指定的动态资料目录重新阅读制定。当前目标是把本机原厂无线功能及其FC/NE通路移植到原生Linux/OpenWrt；整机顺序仍为EPON有线硬件转发、Wi-Fi、OpenWrt UBI安装升级恢复。GPON族按用户要求暂缓。

## 本轮输入与阅读范围

README、docs/01至11和logs/research-log.md已全文阅读；结合17项metadata源码锚点、原机TX/RX/FC回调、固件选择、RF检查、header conversion、A-MSDU反汇编及测试程序复核实现依据。433个有效文件共66186467字节，与evidence/wireless-study-20261008逐文件一致；本輪未发现增量。89份反汇编、93份参考源码、125份原机材料作为证据索引，不将整个SDK视为本机同版本实现。

76项原始来源收据与147项补充收据的文件哈希均匹配。资料的旧SHA256SUMS只覆盖早期246项，后续材料有独立补充收据；本工程另存本轮完整逐文件清单。阅读收据在manifests/wifi-reading-20261009.json，完整比对在logs/wireless-study-current.json。

每次进入新的无线实现阶段，先运行tools/check_wireless_study.py比较用户目录与已读快照；新增或变化材料先阅读，再保存新的日期快照及哈希。该步骤按开发阶段执行，不是定时监控。用户原资料目录保持只读，研究记录、源码和产物写入Ubuntu工程。

## 实施依赖

| 次序 | 实现内容 | 完成依据 |
| --- | --- | --- |
| 1 | 板级身份、PCIe资源与DMA地址域 | 原机DTB、芯片cut、PCI主/子ID、RFE/FEM及校准来源分别落实 |
| 2 | 原生PCIe host、时钟、reset、SerDes、SoC ATU、MSI/INTx | 两个控制器稳定枚举、BAR/IRQ及DMA验证，关闭和重启可重新初始化 |
| 3 | 两颗无线芯片的原生MAC/PHY、固件协议和mac80211/cfg80211接口 | 各自固件ready、efuse/RF参数、RX/TX completion、扫描和AP基本收发 |
| 4 | 完整软件无线服务 | 双频、hostapd/UCI/LuCI、加密、VAP、桥接/VLAN、重连及PON业务正常 |
| 5 | 无线与NI/QM之间的描述符、缓冲和接口ID生命周期 | TX专用lookup、普通与紧凑RX、命中与miss语义、回收及ID退场实现一致 |
| 6 | 原厂无线NE静态闭环 | 先LAN↔Wi-Fi，再XEPON↔Wi-Fi；key/action/metadata/sw_id和实际报文对应 |
| 7 | Linux动态路由与桥接卸载 | flowtable负责路由/NAT，桥接按TC/switchdev语义实现；删除、邻居与STA变化撤销旧流 |
| 8 | 原厂性能功能与整机验收 | 多核调度、Shortcut、聚合、吞吐和长时运行；之后进入原生UBI交付 |

阶段1至3的静态研究、编译和协议测试可以离线推进；枚举、AP可用、硬件命中和稳定性由重新连接光猫后的记录判定。完成软件无线前，不启用无线NE入口。

## 1：身份与原生PCIe

原机DTB启用PCIe0/1，PCIe2 disabled；0/1分别具有global、DBI、config、DesignWare iATU和SerDes资源。设计使用Linux6.18 DesignWare host框架，编写RTL9617C glue，不移植旧PCI core。SoC ATU位于f432c000/c100，与DesignWare iATU是不同模块；原机声明coherent-hi-addr=4，其DDR映射必须与Linux DMA API和实际地址验证对应。NI已经验证的高地址别名2不能直接代替PCIe的映射。

原机无线模块编译匹配10ec:0192、10ec:c852、10ec:b814；这是驱动表，尚不是本机枚举。c832是外部RF代码的subsystem ID线索，不作为主PCI ID。SerDes A/B选择依据硅片revision；原机DTB的两组参数分别保留来源。每颗无线芯片继续读取cut、efuse、RFE/FEM与board subtype，再选择固件和RF表。

Linux6.18现有rtw89_8852ce匹配c852，是8852C主线基础候选；该树没有8192XB驱动。两颗radio分别适配，不能给8192XB改PCI ID便套入8852C。原厂AP固件为四份u1/u2数组，版本0/27/84/3；原生驱动须核对H2C/C2H、下载section、ready握手和cut选择，不能假定原厂AP数组可直接替代rtw89的固件文件。

原机FEM配置238、ELF初始RFE50及目录RFE54属于不同层，初始化存在覆盖；按实际分支落实最终RF表。源代码允许借鉴寄存器及协议行为、复用适合主线的GPL实现；运行时不加载原厂rtk_wifi6.ko、fc_mgr.ko或fc_8277c.ko。

2026-10-09增量阅读：新增docs/12、13、14及相关证据，当前冻结快照为evidence/wireless-study-20261009-104746，502个有效文件。原机内核配置、频段覆盖和FEM字符串已经共同确认最终RFE54，5GHz/wlan0选择RTK66587_BPF、2.4GHz/wlan1选择RTK66287。固件选择读取MAC层chip_id/cv，cv0选u1，其他已支持cut走u2；固件头cut1/2与主机cv0/1分别解释。无线probe请求32bit DMA，与SoC ATU alias4配合。无线WD与NI lookup描述符、无线MACID与NE接口ID分别实现。

## 3至4：无线先完成正常服务

基础实现依次完成设备power/reset、MAC初始化与固件下载、DMA rings/IRQ、RX/TX completion、BB/RF参数及校准，然后mac80211/cfg80211的AP/STA、密钥、beacon、STA增删和频道控制。两频段分别验证后接hostapd、OpenWrt wireless UCI与LuCI，再验证LAN桥和XEPON业务。

无线芯片自身header conversion、A-MSDU、主CPU Shortcut和NE快转各自实现、记录。原机mac_hdr_conv_en涉及9b00位1/2、A-MSDU还涉及9a04/8420和cut修补；逐STA描述符/密钥状态必须与这些开关一致。基础无线使用正常Linux数据面，NE功能后置接入。

## 5至7：按原厂无线NE链实现

无线RX由主CPU驱动取得Ethernet skb，经原生显式接口送NI专用lookup描述符。该描述符HP=11、mode0，携带source/dev_id/dest_sel，控制字基础81000000，与现有通用NI描述符不同。跨PCIe与NI的DMA映射分别由所属设备管理；completion释放由自己的软件对象记录，不复制原厂skb私有偏移。

同一个逻辑源端口18可以对应多个无线netdev。先完成接口ID分配、注册和退出，再把ID纳入Hash metadata低8位匹配；STG0 keep_lspid bit19与实际LPB/classifier一起核对。TX ID7bit、metadata8bit、EPP sw_id6bit，整条通路按有效ID和保留语义分配，不能推导出128个SSID。

下行Hash动作指定CPU逻辑出口和无线mdata_byte；QM/EPP返回后恢复对应netdev，再调用原生无线发送路径。RX必须区分：紧凑EPP无线命中、普通Header CPU的lookup miss/异常入口身份、组播pol_id语义。非零ID不等于无线命中出口。把miss重新注入该无线入口的Linux栈，不能当作下行发送。

长度契约按原机QM crc_en bit25=0，紧凑无线长度扣16；64字节headroom由本工程缓冲分配器明确管理。原厂head+0x7f8的owner指针只作行为证据，不写入主线skb。现有NI缓冲的DMA地址到对象索引继续独立管理。

静态规则顺序：无流时miss回正确入口；单向LAN→Wi-Fi命中；Wi-Fi→LAN；双向XEPON；同五元组不同SSID/VLAN；删除后立即miss。之后接Linux动态flowtable，并另行实现桥接FDB/VLAN/STP/组播；路由flowtable不能代替二层卸载。STA删除、AP关闭和ID复用先撤流及失效cache，再等待队列/RCU引用退场，最后释放ID。

## 8：性能与交付

先保持接收布局、包长、加密及metadata正确，再优化拷贝、回收、多核和无线Shortcut。验证普通MTU、聚合、多个SSID、重连、桥接/VLAN及PON双向业务，并对照硬件计数、CPU记录和线上载荷。GPON族仍暂缓。

本机IKCONFIG关闭PE A-MSDU、RTK_WFO和WFO_VIRT。当前恢复的是原厂FC/NE无线通路；07F WFO镜像、77C TXAMSDU镜像及PE0语音DSP分别保留用途，不为基本无线接管PE。用户要求的OpenWrt UBI固件、安装、sysupgrade和恢复流程在Wi-Fi之后完成。

## 已执行的离线复核

2026-10-09在Ubuntu重新执行原机普通内存指令测试：65536组专用TX编码、1031184组EPP解码、10036组QM CRC位、5组owner位置、5组headroom覆盖、3组stream-index初始化全部通过。结果在logs/wifi-study-*-recheck.json；这些复核确认协议和字段处理，实机无线验收尚待执行。
