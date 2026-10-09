# TYFZA1 原厂无线生命周期：实现依据与当前缺口

2026-10-09。按用户要求调整工作方法：先理解本机原厂完整链路，再实现；局部测试结果不用于推导整条链路已经正确。本文是已确认路径及待追清问题的记录，尚未声称全部原厂行为已经贯通。

## 本机依据

主依据是原机rtk_wifi6.ko，SHA256=745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e。函数反汇编保存在analysis/radio-stock-lifecycle；函数位置/哈希见manifests/radio-stock-lifecycle-functions.json，实际ELF ops重定位见manifests/radio-stock-lifecycle-ops.json。GPL代码用来解释函数和结构字段，差异以原机机器码/实际配置为准。

ELF的mac8852c_ops/mac8192xb_ops均将slot0x40绑定mac_pwr_switch、0x48绑定mac_sys_init、0x50绑定mac_trx_init、0x58绑定mac_feat_init；对应PCIe ops的slot0x40/0x48/0x50分别绑定pcie_pre_init/pcie_init/pcie_deinit。这些slot与mac_hal_init中的BLR加载偏移一致，不能只扫描直接BL指令就认为已经得到完整调用链。

## probe 与 start 是两个阶段

rtw_dev_probe负责PCI资源、DMA mask、设备/接口对象、efuse/FEM检查及host资源分配。它调用rtw_hw_init，而rtw_hw_init依次建立PHL/HAL对象、处理NVM/efuse、覆盖默认设置、准备cap、分配TRX资源、preload并做final cap decision。

实际start在rtw_phl_start：先rtw_hal_start，再启动dispatcher/message hub及phl_datapath_start。rtw_hal_start通过芯片HAL ops分别进入hal_start_8852ce/hal_start_8192xbe，PCIe版本又进入hal_start_8852c/hal_start_8192xb。将PCI probe、HAL资源初始化、MAC启动和上层AP启动合并为一个“probe成功”，会丢失这些阶段的状态与顺序。

## 原机 MAC 初始化顺序

mac_hal_init机器码地址0x221878，主要过程如下；间接调用已经用实际ops表解析。

| 顺序 | 原机动作 | 对当前移植的影响 |
| --- | --- | --- |
| 1 | DUT环境、SWR配置、mac_pwr_switch；已上电时执行off→on | 要从整个pwr_switch理解已上电和重启状态，不能只搬AP上电函数 |
| 2 | PCIe PLDR处理、hci_func_en | 当前下载后端未涵盖完整HCI入口 |
| 3 | dmac_pre_init：DLFW DLE及下载HFC | 这是下载配置，不是正常收发配置 |
| 4 | pcie_pre_init：芯片/cut修补、模式、host BD、BDRAM及下载channel启用 | 目前仅实现部分ring配置，尚未逐项复原所有实际分支 |
| 5 | WDT/固件来源设置、enable_fw或外部ram_buff下载 | 当前原生路径固定使用提取的AP基线，外部覆盖及secure相关准备仍需核对 |
| 6 | set_enable_bb_rf | 固件ready之后仍有BB/RF硬件控制 |
| 7 | mac_sys_init | 重新启用完整DMAC/CMAC并检查安全状态 |
| 8 | mac_trx_init：dmac_init、cmac_init及IMR/DBCC相关配置 | 当前仍缺正常DMA、调度、安全、过滤、CAM等初始化 |
| 9 | mac_feat_init | 功能、角色和能力配置并非下载流程的一部分 |
| 10 | pcie_init、gpio_init | 原厂在后段启用普通TX/WPDMA并处理LTR/PCIe状态 |

尤其是步骤8中的dmac_init明确调用dle_init运行profile、preload_init、hfc_init(1,1,1)、STA scheduler、MPDU、安全引擎及安全表初始化。cmac_init还包括scheduler、port reset、ADDR CAM、RX filter、CCA、NAV、spatial reuse、TMAC、TRX protocol、RMAC、PTCL等。当前原生代码的DLFW ready不能代表这些步骤已经完成。

## 实际参数覆盖与 memory profile

原机_hal_pre_init_8192xbe地址0x280d74：先从cap决定FW/HW TX mode，从hal_com的DBCC状态填qta_mode；再根据RPQ buffer大小计算release aggregation，最大121，并配置TX成功/重试限额/生命周期/MACID丢弃报告；随后取得已经分配的TXBD/RXBD。

本机机器码明确写txbd_trunc_mode=1、rxbd_trunc_mode=1、rxbd_mode=0、tag_mode=1。对应GPL含义是BD_TRUNC、packet RX、多tag。SDK的intf_info_def中RXBD_SEP不是最终值；它被_hal_pre_init覆盖。TX/RX burst、WD DMA间隔和multi-tag数量填DEFAULT，之后由mode_op和芯片默认值解析，不能把DEFAULT=0xfe直接当作硬件字段。

实际ELF的运行表在manifests/radio-stock-operational-memory.json。

| Profile | WDE | PLE | WCPU最小页 | 含义 |
| --- | --- | --- | --- | --- |
| DLFW | 64字节，0 linked+2048 unlinked | 128字节，2544 linked+16 unlinked | 从运行profile复制 | 448KiB下载布局 |
| SCC | 64字节，3328 linked | 256字节，952 linked+8 unlinked | 60 | 本机AP运行表包含256字节PLE页 |
| SCC_STF | 64字节，256 linked | 128字节，3440 linked+16 unlinked | 20 | 不同运行布局，不能沿用SCC的60 |

SCC的PLE最小/最大配额还为CMAC TX、CMAC RX DMA、MPDU、安全/WCPU、BB report和WD release留页；DLFW中这些大量为零。当前入口硬编码SCC的下载WCPU值，只覆盖这一选择；未做完整运行profile切换。原机SCC/STF具体选择及final cap的覆盖来源继续追清。

## BB/RF 参数有两层加载和两次应用

原机hal_start_8852c/hal_start_8192xb都存在rtw_hal_dl_all_para_file与rtw_hal_dl_all_ext_para_file调用。基础表加载之后，BB/RF先disable→enable，再分别执行BB/RF参数初始化；当RFE满足扩展条件时，再读FEM子目录并重新应用BB/RF。随后才进入BB/RF dynamic-management、FCS/ICV、PPDU状态、driver-info/header-conversion等设置。

_hal_dl_para_file构造ic_name/RFE目录中的基础文件路径；_hal_dl_ext_para_file再加入rtw_get_fem_name所得的子目录。原机RFE54的基础目录含8852C14份、8192XB8份文件；之前的构建输入只安装所选FEM目录的20份扩展表。完整实现还需要基础层、扩展覆盖/累加语义、各类文本解释器、BB/RF写入方法和校准程序。保存20份扩展文件只是数据准备，没有满足这条链路。

构建输入准备现已按明确的两层路径补齐22份基础表，共42份参数，并在输入清单记录base/FEM-extension。这只修正输入缺口，驱动应用、功率/频段选择和校准还没有据此完成；不能把文件齐全等同于BB/RF初始化。

## TX 有三种不同的完成

1. TXBD硬件消费指针前进：设备已读到WD/命令缓冲，是descriptor/H2C队列回收依据。
2. 正常无线payload的WP release：RX返回release report，由rtw_hal_handle_wp_rpt解析DMA channel、wp_seq、MACID、queue及TX status；phl_recycle_payload通过wd_ring[channel].wp_tag[sequence].ptr找到原始tx_req，执行tx_recycle，并清tag/更新忙计数。
3. H2C receive/done ACK：C2H命令响应，反馈固件接收或完成某项命令，不能代替TXBD消费或空口TX结果。

普通发送先分配WD page、分配WP sequence并记录tx_req，填WD+sequence info+address info，再发布TXBD。WD page与payload分别回收。当前下载后端仅处理channel12消费完成，尚未实现普通无线发送的两层对象和WP报告回收；RX试验中的“报告计数”不代表完成回收。

### 本机 H2C 已消费缓冲还有保留期

原机hal_hci_cfg_8852ce地址0x27b1a0、hal_hci_cfg_8192xbe地址0x281c44都直接写txbd_multi_tag=8和txbd_upd_lmt=1。原机phl_release_busy_h2c_pkt地址0x19148c在这个flag为1时进入按CMD/DATA/LONG_DATA分类的work ring：把刚消费的对象放入该类八项ring，取出被替换的旧对象归还idle pool。不是消费指针一到就立即复用刚发送的缓冲。

下载头属于DATA、固件段属于LONG_DATA；两者保留队列独立。当前原生单缓冲在这一点上偏离原厂，已按上述机制改为每类八个保留slot加一个当前可用slot，消费完成只轮转该类slot。两类共18×2048字节coherent pool。这个改动依据原机flag及实际回收分支；新的全量初始化与板测仍未完成。

## RX/C2H 理解仍需补齐的部分

RXBD info、短/长WD、packet offset及C2H头的局部格式已有原机依据，但完整RX还包括RXQ/RPQ资源配置、tag与RXDMA index、PPDU/driver-info、FCS/ICV处理、header conversion和A-MSDU。原机hal_handle_rx_buffer已在HAL层调用rtw_hal_mac_parse_c2h和hal_c2h_post_process，PHL接收到C2H类型后主要回收RX buffer。因此原生C2H只读取类别和计数尚不等于命令响应处理。

## 工作区与后续顺序

未提交的RX/IRQ代码及其局部测试已经保存到Git stash（描述RX experiment retained pending complete stock lifecycle understanding），可以恢复重做；不把这份试验作为新候选固件。其后台构建已经终止为failed，当前最后成功载荷仍为9b76b2e下载阶段的transport02。

接下来先把pcie_pre_init实际cut分支、运行态DMAC/CMAC、cap/profile选择、基础/扩展BB/RF和普通TX/WP生命周期继续追清，给每个步骤记录原机入口、参数来源、寄存器/对象变化、完成条件和依赖；据此调整移植顺序和实现。验证用于核对已明确的原机行为，不能用自行设定的模拟条件替代行为依据。

## 已依本机实现的运行内存阶段

mac-runtime.h现在接在WCPU download ready之后。它按本机mac_hal_init的先后实现set_enable_bb_rf、mac_sys_init中的完整function/clock及安全模式查询，然后执行SCC DLE、preload和运行HFC。PCIe host数据面仍暂停，STA scheduler/MPDU/security/CMAC TRX等后段尚未执行，radio_state明确记录mac_memory=SCC、mac_trx=pending。

依据不是主线NIC默认表：原机dmac_func_en_8852c@0x24c1b8与dmac_func_en_8192xb@0x26a558都写DMAC=fb7d4000、clock=0b1f0000；cmac_func_en(BAND0,1)把c004的4000003f和c000的f000003f置位；mac_chk_sec_rec@0x2454d8读取DDV efuse5ed的bit7并反转为secure标记。BB/RF使能还保留8852C的SPS/ADC LDO设置，XTAL3分别为8852C的0d、8192XB的0f。

SCC直接使用本机ELF的wde_size19/ple_size19/wde_qt18/ple_qt46/ple_qt47。原机AP HFC并非主线8852C常见group0/group1各1614：本机hfc_pubcfg_pcie_scc为{3228,0,3228,0}，普通有效channels0..3/8/9的min=26、max=3098、group0；XB跳过不可用的4..7/10/11。实际hfc_preccfg_pcie={2,40,0,0,X2,X1,X1,X1}，SCC为POH mode0。preload(BAND0)的9f48/9f4c字段按原机82080000/134设置，并保留CAM entry字段。ARM64对象编译完成；本机运行、完整MAC TRX和新的最终FIT仍待后续。

## DMAC hardware engines 后段

原机dmac_init@0x23e898在DLE/preload/HFC后启用9e10的STA scheduler，等待INIT_DONE_1(bit31)，设置warm-init(bit29)，按原机FW TX policy控制bit28，并把52C/92XB的9e50 SS2F目的队列字段修正为0a。FW调度分支还写9e24=3001b、9e1c=10050、9ea4/9e9c=ffff及9e20的3f3f；这些值直接由该原机函数的内联实现确认。

发送模式的来源是原厂netdev-open阶段的txbf_mu || ofdma_enable || force_fw_tx，不是chip_id或PCI编号。原生后端保留txbf_mu/ofdma/force_fw_tx三个只读加载输入，未指定输入时三者为零；这属于当前后端的显式启动配置，不伪称读取了本机在线MIB。后续AP/UCI启动将接同一规则。单纯移植寄存器时不得固定删掉另一个模式分支。

STA scheduler之后按原机顺序写MPDU action/trigger forwarding、FCS/ICV追加以及A-MSDU cut配置；再使能安全引擎cipher clocks和TX/RX、组播/管理帧加解密，配置MIC/ICV、TX timeout及AP driver-info patch。PCIe分支保留TX_PARTIAL_MODE；不误套原厂USB分支的清位动作。radio_state只报告dmac_engines，不将其等同于key/STA表或CMAC/PHY完成。

sec_info_tbl_init的原机行为是分配并置INVALID的host SEC_CAM/MACID信息对象，另由key/role路径写硬件。原生对应key/STA生命周期继续接入mac80211，不能将“安全引擎使能”或“空的报告计数”当作密钥安装成功。普通RX/TX仍未开放。
