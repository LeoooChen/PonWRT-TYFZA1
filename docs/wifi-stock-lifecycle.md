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

## 按原机机器码接入 CMAC 寄存器阶段

mac-runtime.h 的 rtl_ax_mac_cmac_start 接在 DMAC hardware engines 后，依 cmac_init@0x23f040 顺序执行 band0 ASIC 的 scheduler、ADDR CAM、RX filter、CCA/NAV、spatial reuse、TMAC、TRX response、RMAC、CMAC common 和 PTCL。原机 rst_port_info@0x225324 只清 host port/beacon-report 对象；这些对象随后接入 native AP role 生命周期，不添加虚构的硬件重置。SEC_CAM、MACID、port 的 host 生命周期，以及 mac_trx_init 后段 IMR/feature/PCIe normal-channel 初始化仍需继续，当前状态因此报告 cmac_registers，不报告完整 MAC/AP 已就绪。

SIFS_MACTXEN_T1 在 C/XB 分别为 0x3e/0x40；C 独有 c340 高字节 0x6a。BCN EDCA 经 get_edca_addr@0x1ea748 确认为 c326，16 位写 0x3219。ADDR CAM 设置 ce34 的 0x007f0101，按原机读取宽度用 16 位等待 bit8 清除。RX 三类 subtype 转发均为 HOST；原机 ELF 的初始参数五字节为 3f0000087f，全掩码为 ffffffffff，经原机 converter 得到 ce20=0800003f、ce04=007f。

CCA 的 response-check patch 对两颗 MAC chip ID 2/3 均不启用，按正常分支设置 NAV/CCA，保留 ACK/BA 忽略 CCA 的原机设置。AP NAV 上限是 0x5d 个 128us 单位（12ms），不是把十进制 12 直接写字段。spatial_reuse_init@0x244f04 只清 ce4a bit0；没有照抄新版 SDK 额外的 PLCP/BSSID 修改。原机 TMAC 也没有新版 SDK 的 SW_PREFER_AC 操作。

RRSR 的间接调用经实际 ELF private-ops 槽 0xa0/0xa8 分别确认为两颗芯片的 mac_get_rrsr_cfg/mac_set_rrsr_cfg；只保留这些实现认可的字段，开启 legacy rate，CCK 应答只用 1M，参考 RX rate 与 CMAC table 的较小值。RMAC 先重置全部 BA CAM，等 bits0/1 清除，再设置 SSN/DLK/RCR。原机 RCR 低字节 SW TX mode 写 0x0f，HW mode 写 1；不把新版 SDK 的统一 RX_CH_8_USER 写法强套进本机。SCC 的 C0 quota 100×256B 高于 MPDU 规格上限，RX 长度字段为 23 个 512B 单位。

原机 is_qta_poh@0x202c00 对 PCIe SCC 返回真，因此 PTCL 配置 CTS-to-self 和 2ms arbitration timeout。这个布尔判断与 HFC POH 字段的 mode0 编码不是一个含义。C/XB 的 VHT aggregation/timer-source/CMAC DMA patch 返回无需写寄存器；原机 RMAC 也没有新版 SDK 的 RX_TIME_MON 写入。完整来源、地址及 private-ops 记录在 manifests/radio-stock-cmac.json。

后台服务 ponwrt-tyfza1-stock-cmac-compile 完成 Linux 6.18.55 的 ARM64 pci.o 编译，退出码 0。编译仅确认当前真实内核接口和生成对象；设备离线，本阶段还没有上板结果或新的最终 FIT。接下来依据 hal_start 的实机顺序处理基础 BB/RF、RFE54 FEM 扩展、普通 TX/RX 和 AP role/key 对象。

## 原生 RFE54 BB/RF 输入及执行接口

phy.c 已实现两颗芯片基础与 FEM 目录的 PHY_REG、PHY_REG_GAIN、RadioA、RadioB 文本读取，共 16 份实际文件、8919 对数据。当前 probe 读取它们并保留两个层次；radio_state 报告 phy_inputs=RFE54-base-and-FEM、bb_rf=pending。这是实际文件读取与执行接口接入，尚未在 probe 执行 BB/RF 写入。MAC TRX 后段、运行态 H2C、增益/功率对象和校准流程接齐后，AP start 才按原厂顺序调用执行接口。

BB 写入链是 halbb_cfg_bb_phy → halbb_set_reg → halbb_set_cr@0x2b0da4 → _hal_write32；原机最后 OR 0x10000，故 PHY_REG 的 0x4000 对应 PCI BAR 的 0x14000。f9..fe 在 BB 文件中分别表示 1/5/50us、1/5/50ms 延时。PHY_REG_GAIN 的地址编码 band/path/type 的增益对象，不是 PCI BAR 地址；保留该输入供原生增益对象实现使用。

RF 地址 bit16 决定 die：analog 使用 SIPI（BB 0x174c bits24/25 空闲、0x370 命令），digital 使用 path A/B 的 BB 0xe000/0xf000 窗口加低八位寄存器号×4，并按原厂保留寄存器高位。原机 analog 写入等待计数是 31，命令只写一次；新版 SDK 的 500 次回读重试和额外写后延时没有进入本机机器码，原生不加入这些动作。digital 写后保留 1us 延时。两个芯片实际函数均已反汇编核对。

尤其不能把 BB 延时语义套进 Radio 文件：本机 halrf_cfg_rf_radio_a/b → halrf_wrf@0x3040dc 直接调用 RF 写入，f9..fe 也经过这条写入链。随后 store_radio_* 才排除所有 analog 地址及这些值，只将 digital 地址/数据打包为 ((address & 0xff)<<20)|value。原生实现保留这个区别。

halrf_config_radio@0x3030e8 对 C 按 B→A，对 XB 按 A→B。每条路径写入后按原机发送 category OUTSRC、LONG_DATA、class8(A)/9(B)、function=page，每页 500 个 little-endian word（2000B）；结尾即使剩余长度为 0 也发送最后一页。执行接口要求调用者提供真实的 send_radio 运行态 H2C 入口，没有以计数或本地回调成功代替 WCPU 通路。

rtw_hal_init_rf_reg@0x1bef20 先调用 radio 配置，保存 by-rate/track/limit/RU，再调用 halrf_config_rf_parameter@0x303760；后者还会再次配置 radio，并加载功率、tracking、XTAL 和 EDPK。这两次 radio 配置不能擅自合并。hal_start 对基础表与 FEM 扩展重复整个 BB/RF 初始化也要保留。相应执行顺序和功率/增益/校准对象继续实现；当前没有声明 RF 已初始化。

来源地址、每份实际表的数量/哈希及 digital-RF 分页数量见 manifests/radio-stock-phy-inputs.json。脱离 Codex 的后台服务 ponwrt-tyfza1-stock-phy-input-compile 已编译 pci.o 和 phy.o，退出码 0。

## MAC TRX 的共存、错误中断和释放报告寄存器

沿原机 mac_trx_init@0x240178 继续落实后段：private ops 的 0x128 指向 coex_mac_init_C/XB，两者实际只写 da48=0；0x78 指向 ser_imr_config_C/XB，依次配置 DMAC 的 19 处、CMAC band0 的 7 处 SER 掩码；mac_err_imr_ctrl(1) 写 DMAC/CMAC0 总错误掩码。每处保留位与使能值来自对应实际机器码，SCC 不触碰 band1。

set_host_rpr@0x21e430 的 PCIe SCC POH profile 选择 path0，开启 TXOK/retry-limit/lifetime-drop/MACID-drop 四类结果。原机 _hal_pre_init_Ce/XBe 根据 RP buffer 算聚合上限，再 cap 到 121；当前计划的原生 RP buffer 同样容纳该值。原机 ELF data+c2f0 的 agg/timer 默认是 79/ff，timer 保持 ff；按原机设置 9408、9410、9414，值未变化时跳过写入。set_l2_status 仅修改软件状态，未添加对应假想寄存器。

mac_feat_init 的 private ops 0x1d8 实际指向 mac_bacam_init，对两颗芯片调用 mac_bacam_init_upd(8,0,0)，经 category MAC、class12、function2 的 H2C 初始化 8 个 BA CAM，并要求 done-ACK。其完成必须接上运行态 RX/C2H。这个阶段和 PCIe normal-channel/LTR 接入尚未完成；当前状态仅为 trx_registers=ready，features 与 bb_rf 仍为 pending。原生 ordinary host RX/TX 继续处于暂停阶段。实际函数、ops 槽位和报告配置的依据见 manifests/radio-stock-trx-tail.json。

## 运行态 H2C 与原机 RX/C2H 完成链

继续沿 mac_hal_init → mac_feat_init → mac_bacam_init_upd 实际调用链时，确认原生下载准备漏掉 hci_func_en@0x22128c。原机在 DMAC pre-init 前将 7880 的 TX/RX DMA function bits0/1 置位；1000 的 HAXI run/stop 不能替代这次写入。download.h 已补齐该先后顺序。

两颗原机 ELF 的 intf_info_def 均为前九个 word {0,0,1,1,2,1,1,1,7}；_hal_pre_init 把 RXBD mode 从默认 SEP 改为 packet，并保留多 tag。mode_op_pcie 最终使用 TX burst2（256B）、RX burst1（128B）、WD idle/active interval1（256ns）、tag count7（8 tags）。transport.h 在设置下载环前按该值写 1000/1004/1204，不继续依赖复位默认值。

本轮重新按原机实现 RXQ/RPQ，而非恢复旧 RX 试验。hal_handle_rxbd_info_8192xbe@0x283030 只接受 FS=LS=1；旧试验的分片拼接没有这条原机路径依据，未纳入当前驱动。hal_check_rxrdy_8192xbe@0x2830cc 从 coherently mapped buffer 读取 13-bit tag，10000 次读取上限，8191 后回到 1，只在匹配时更新已消费 tag（这里与 SDK 的失败时提前推进不同）。RX WD 根据实际字段计算 4B RXBD info、16/32B descriptor、2B shift 单位、16B driver-info 单位，以及非 PPDU 的可选 4B MAC-info。按原机 payload end 与 DMA write size 的检查读取完整包。

原生 RXQ 使用 256×12288B，覆盖 11966B MPDU 加最大描述符/附加信息，并满足 RXBD 的 14-bit buffer size 字段；RPQ 使用 256×512B，容纳原机聚合121个4B释放结果及20B RXBD/short WD。这是原生分配器的几何选择，不冒称原机 allocator 的内存布局。回调同步消费 payload 后清 tag、补 RXBD、dma_wmb，再通过16-bit host index归还；使用 Linux DMA API，不读取原机 skb owner/headroom。

运行态 H2C 使用与原机 txdes_proc_h2c_fwdl@0x251adc 一致的16B WD(type13)和8B命令头。CMD、DATA、LONG_DATA各有8个保留slot加1个当前slot，总计27×2048B；不会把CMD的完成覆盖进DATA/LONG_DATA的回收环。category/class/function/sequence 和 receive/done ACK 位按 h2c_pkt_set_hdr 编码，成功发布后推进 sequence。

RX中断工作队列将完整 C2H 交给命令处理。FW_INFO(class0)函数0是rec-ACK、函数1是done-ACK；两者的被应答 H2C sequence分别在ACK payload的bits16..23和24..31，不能使用外层C2H sequence匹配。目标 category/class/function/sequence必须与当前命令一致。done-ACK同时保存固件返回码并唤醒completion；TXBD消费和rec-ACK不代替done-ACK。

当前启动实际配置并开启RXQ/RPQ及channel12，发送MAC/class12/function2、4B payload=08000000、CMD类的BA CAM8项初始化命令。features只有在收到匹配且返回码0的done-ACK后才置为BA-CAM8-acknowledged。原厂请求done-ACK但提交函数本身不阻塞；原生将确认纳入启动事务，使用明确的1000ms命令deadline，不冒称这个超时来自原机参数。此后暂停HCI并保留环资源，接下来继续normal-channel/LTR、RF对象/校准和AP启动。

RX关闭与中断恢复共用irq_lock的running状态，并等待IRQ及工作队列退出后释放DMA；迟到的工作尾部不会重新使能中断。当前控制回调只处理上述MAC ACK，普通Wi-Fi/WP/PPDU/PHY事件明确记为unhandled，继续按原厂相应路径接入，不以收到报文计数宣称业务完成。

Linux6.18.55使用PCI_IRQ_INTX，已采用当前接口。后台服务ponwrt-tyfza1-stock-runtime-c2h-driver在真实内核配置下编译pci.o、phy.o、rx.o和builtin archive，退出码0。初次命令误用了不存在的单独builtin composite对象目标，随后对象编译发现旧PCI IRQ宏；两项已修正并以目录构建通过。源地址、默认表及ACK协议记录见manifests/radio-stock-runtime-c2h.json；尚无本阶段的板测结果。

## 2026-10-09 原生无线运行态实机验证

runtime-c2h01 的实际 RAM FIT（SHA256 80709389748fc564ab313cb65378f10dcfe94c98ce052df1a1079d402340b92d）经原机 U-Boot 签名/哈希校验并启动。两个 PCIe endpoint 分别枚举为 10ec:c852（Gen2 x1）和 10ec:0192（Gen1 x1），使用各自独立的 PCI domain。BER-notify 超时后继续训练的原厂路径在本机完成 link up。

原生 driver 读回两颗芯片 cv=1，C 为 factory U/ADIE6758，XB 为 factory T/ADIE6816，分别下载 u2 AP 固件0.27.84.3。C 实际发送186包/369096B，XB195包/387688B；两者 WCPU均就绪，完成 SCC DMAC/CMAC/TRX 寄存器和RFE54基础/FEM输入读取。

两颗芯片都收到自己BA CAM8项初始化的真实done-ACK，features=BA-CAM8-acknowledged、done_acks=1。各自RXQ=1、IRQ=1，malformed/tag_pending/unhandled/RDU均0。这是本机PCIe DMA→WCPU→RX DMA→C2H实际闭环；尚不包括普通无线收发、BB/RF校准或AP接口。完整串口记录为logs/serial-20261009-204954.txt，结构化结果为manifests/radio-live-20261009.json。

同次启动XEPON/epon-d10g自动完成LLID0注册并取得192.168.71.28/24，网关192.168.71.1和公网1.1.1.1各3个ping零丢包。网线当前接LAN port3而旧原生LAN只绑定port0，这个四口支持缺口正在修复。

## 原生 BB 增益对象与芯片 reset 差异

PHY_REG_GAIN按本机halbb_cfg_bb_gain_C/XB实际回调解码为LNA/TIA、bypass、op1dB、RXSC RPL、WB gain index、GS与eLNA对象。机器码只接受band0..7/path0..1，不能照搬新版SDK12-band布局。FEM输入叠加到基础对象，未提及的RXSC字段保留；不把gain地址当成MMIO地址。

原机halbb_reset_bb_phy进一步指向C/XB不同函数：C callback只有日志；XB临时保持58dc/5818/78dc/7818的TSSI bit30，0704 bit1按1→0→1写，再释放TSSI。原生执行接口按这条差异实现，未加虚构的C reset写入。该阶段对象/接口已通过ARM64目录构建，具体函数来源为manifests/radio-stock-bb-gain.json；此改动在runtime-c2h01之后，尚未在该FIT中。

2026-10-09 晚间 four-lan03（FIT SHA256 46539868ddcffac3c957ad9573db0cbe5ed18def18c40c7d2789adc141231f38）已包含增益对象并在原机运行。两颗无线芯片各收到 BA CAM 成功应答，RXQ/IRQ 各1、RX错误全0；通过 SSH 依次 unbind/bind 后，两颗再次完成固件下载和成功应答。实际输出保存在 logs/radio-rebind-20261009.txt。此复测证明当前供电、下载、运行态控制 DMA 与启停可重复，BB/RF 与 AP 仍未完成。

## 基础 BB 原机执行验证

hal_start_C@277a14/XB@27e4f8 在 MAC init 后先 set_enable_bb_rf(0→1)。off 分支@21e29c 对 C/XB 清 WLRF_CTRL 02f0 bit17，再清 SYS_FUNC 0002 bits0/1；原生已接此顺序。rtw_hal_init_bb_reg@1b7ea4 依次执行 halbb_init_reg 和 halbb_reset_bb。halbb_init_reg@2af4c4 先写 PHY_REG，再解码增益对象、调用 halbb_get_efuse_ofst_init；该名字对应的是读取表初始化后的 BB 寄存器，并非再次读取物理 eFuse。

C@2e80ec 保存 4df0/6df0 低8位与4e0c/6e0c高8位的有符号 offset；XB@2f2210 只保存共享 PHY0 的4df0/4e0c。基础执行在此处暂停，尚未执行完整 RF/power/FEM/DM/AP。

scripts/build_live_radio.sh 将 overlay 的同一份 pci.c/phy.c/rx.c 编为本机 Linux6.18.55 原生模块，仅将 PCI driver 名改成 rtl_ax_ap_live，以便在 RAM 系统 unbind 内置版本后上机迭代。没有加载原厂 host ko。bb01 模块 SHA256 8f6baa01dc171952331a0e4aa1a95c6c775a5da9ed3af4614f3e11191d261add，经 SCP 校验后在本机加载。

实际 C 的1432项BB、336项gain，XB的1417项BB、128项gain完成；SYS_FUNC均c3、WLRF_CTRL均00820000。C的0704=601c0502、4df0=08080618；XB的0704=601e0d02、4df0=0，与各自原机基础表最终值一致。捕获C offset=24,0，XB=0,0；两颗BA CAM应答和RX错误计数仍正常。原始输出 logs/radio-live-bb01-20261009.txt，不将 BB 基础执行等同于 RF/AP 完成。

## 第一遍基础 Radio 原机执行验证

rtw_hal_init_rf_reg@1bef20 的第一项是 halrf_config_radio@3030e8；C先B后A，XB先A后B。此阶段接上现有原生 RF 写入接口与 H2C：halrf_fill_h2c_cmd@304254 的 header 请求 LONG_DATA（2），mac_outsrc_h2c_common@20d404 编码 OUTSRC category2、class8(A)/9(B)、function=page。每页最多500 words、最后不足页也发送；原厂不请求 ACK，不能把 TXBD 消费说成固件执行确认。

analog 写入按本机 halbb_write_rf_reg_8852c_a@2e2e64 的31次 busy 观察执行：分别读174c bits24/25，每次包括最后空闲观察都延时1us，然后写0370。数字RF走e000/f000对应窗口。没有引入新版SDK重写/重试循环。

rf01 原生诊断模块 SHA256 ded1a418155b37cb5e03821a7cdb174ad8fad100f6a5b4283dadc9405c4e0b9a 实机完成 C A/B=2044/2011 次、XB=487/406次 RF 写入；SIPI=0、两条路径各一页，C每页1568B、XB312B。数字RF00读回A/B=30000/30001，RF18的C=1/1、XB=c01/c01。该临时诊断模块在RF结束额外重复一次BA CAM命令，两个MCU都成功应答，RXQ/IRQ各2、错误全0；额外诊断命令已从正式启动源码移除，正式代码继续遵守原厂顺序。输出 logs/radio-live-rf01-20261009.txt。

当前停止在第一遍基础Radio后，尚未接 power-by-rate/track/limit/RU 对象、第二遍Radio、FEM重初始化和DM/校准。因此 radio_base=applied 与 bb_rf=pending 同时报告，不等同于完整RF或AP。
