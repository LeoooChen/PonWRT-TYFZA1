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

rf02 移除额外诊断命令后在原机重新加载验证成功，模块 SHA256 451e39052534a065bf796fa9da017e34d118395735912c4d6458c0651313953f；两颗 RF 写入/页数/读回不变，BA CAM与RXQ各1、错误0，原始输出 logs/radio-live-rf02-20261009.txt。完整镜像服务 ponwrt-tyfza1-lan-20261009-220620 已以退出码0构建完成，ram-pcie/image.itb SHA256 e3c1d08d46322a1d119037ab329eaa4de1b1b40587f3230a727dd5d065e3b60b，位于 artifacts/full-pon-radio-bbrf01-6.18.55；冷启动验证另记。

## ByRate 与 TrackTSSI 对象实机解析

power-tables.c 按 halrf_power_by_rate_store_to_array@305790 的实际目的布局保存3×145项有符号 ByRate：CCK/OFDM、HT、VHT、HE、HEDCM及rate offsets。phypg_combvalue_fromstring@1d2188 将整数byte乘4，文件H-byte→L-byte逆序保存；机器码没有把fraction加入结果。基础与FEM对象分开保留，FEM叠加到已有基础对象。实际文件中全部rate offsets为0。

原机旧文本parser只用AllRate1/AllRate2前缀，而备份文件含AllRate2_1/AllRate2_2和5/6G后缀；原生按实际文件完整label解码到store函数对应offset字段，不将旧parser对新label的含混路径当作新接口。TrackTSSI保存原文件每组30项，包括文件里的6G组；本机旧parser只显式识别2G/5G，C的store_pwr_track只复制6G每路径90B。这些差异已保留为后续校准消费时的依据，当前还没有调用6G射频/校准功能。

第一次上机发现内核sscanf不支持用于解析右方括号的scanset，TrackTSSI报EINVAL；改为原文件固定字段宽度后直接上机复测通过，未编写仿真测试。power02原生模块SHA256 9a9dd658c8e7b0a65cc7e078bfca19c193888807d35939109688eb5b30da1aa3；C基础/FEM各35行ByRate、36行Track，XB各24/20。C基础OFDM6=80/72/64 quarter-dBm，FEM=80/96/76；XB基础=84/80/0，FEM=100/100/0，与实际文件相符。全部BB/RF、BA CAM应答仍正常，输出 logs/radio-live-power02-20261009.txt。limit/RU/calibration明确待完成，此处只有host对象，不声称已经写入硬件功率或发出AP。

## 完整 RAM 启动与 RF 读回差异

bbrf01 完整FIT（e3c1d08d46322a1d119037ab329eaa4de1b1b40587f3230a727dd5d065e3b60b）经原机U-Boot校验、重启进入OpenWrt，两颗内置原生驱动从启动开始完成BB与第一遍Radio，结果与live模块相同，RX错误0。XEPON恢复192.168.71.28并公网ping2/2。原始结果 logs/radio-bbrf01-ram-20261009.txt。这个FIT包含b12b93a阶段；后续功率对象与RF读回观测通过独立原生模块上机，不能说已进入这个FIT。

直接数字RF读回发现实际值与表最后写入不完全一致，已增加只读radio_rf_bank及关键写入的即时读回。C RF00请求33c00，即时读30000；RF18请求1001读1。XB RF18请求1c01读c01，路径B最终RF00=30001。数字05、C55与两颗30读回均与表对应值匹配。不能把“完成了循环、TXBD已消费”解释成RF所有字段写入成功。

继续按原机 halbb_read_rf_reg_8852c_a@2e2954/XB_a@2ed074 实现SIPI读取：忙位24/25共31次观察、0378地址/路径字段、2us延时、26位done等待，再读取174c低20位。交易由command_mutex串行化，仅读取模拟RF寄存器，不增加初始化写入。

rf-read原生模块SHA256 b2c9a576ada5b75345739d74c31967e4dc52ab289b0ce9d767b322183f5a7304在原机运行：C模拟30两路=109b7、XB=381f2，均与实际Radio表相符；C模拟18为A1001/B1000，XB两路1c01；模拟RF00为C两路337e1，XB A377e1/B337e1。这些读取均成功且重复读取一致。模式、增益与带宽寄存器受硬件状态控制，当前尚未完成完整频道/校准；C B初始频道为0也保留为待核对事实，不以猜测修改表值。结果 logs/radio-live-rf-read-20261009.txt 与全数字窗口记录 logs/radio-live-rf-bank-20261009.txt。所有上述阶段仍不包括AP关联或无线业务。

## 本机后续 RF DM 顺序与 NCTL 数据

rtw_hal_rf_dm_init@1befc8 调用 halrf_dm_init@303c14。实际顺序为共享XTAL、命令解析host对象、final RF ability、RFE初始化及GPIO、NCTL、RFK host状态、reference power对象、RCK、DACK、RX DCK、TXA LCTK、eFuse trim、TSSI eFuse，随后按配置执行FCS和各PHY power control。它在基础及FEM完整重初始化后运行，不能越过尚未实现的limit/RU与第二遍Radio直接触发校准。

已从本机ELF提取三份NCTL参数数据到analysis/radio-stock-lifecycle/nctl（未在硬件执行）：C cut0为1769对，C非zero cut为1782对，XB为1421对。C函数@3106f8根据hal_com的cv字段选择两份表；XB@33ffb0固定其表。二者先使能0c60/0c6c及58ac/78ac的NCTL相关位，写8000=8，然后重复写8080=4、延时1us并读回，最多1000轮；原机到上限后继续加载表。提取哈希、参数和顺序在manifests/radio-stock-rf-dm.json。

另核对了原机limit文本parser：其小数路径不是ByRate的整数规则，而是integer×4+floor(fraction×4/100)，再处理正负；实际保存9B limit record。后续原生limit解析必须保留17.5等半dBm输入，不能截成整数。IQK也同时存在host与firmware路径（halrf_iqk@30ad50按host配置选择），必须追清本机配置来源及完成条件后接入。

## 两颗无线芯片的本机物理 eFuse

radio_efuse只读二进制属性复用identity阶段已在原机验证的DDV读取路径，读取1536B Wi-Fi物理bank，事务与RF控制共用command_mutex。2026-10-09通过原生模块10b49a3d939d88016a23a11843db20b6268eae788664ba49e96ba268f79452ea读取两颗芯片，各重复一次。

C和XB两次读取分别逐字节一致，均1536B，且5e9=aa与此前identity相符；非ff字节分别165/223。原始数据保存在analysis/private/radio-efuse-20261009，目录0700、文件0600；公开记录只包含哈希与长度，见manifests/radio-live-efuse-20261009.json。当前尚未解码logical map或使用trim数据进行校准。

继续核对iqk_init_C@31b43c，首次host初始化明确将FW IQK选择字段清0；后续是否有显式配置覆盖仍待核对。因此不能因为AP固件包含IQK命令就直接默认切到firmware校准路径。完整RF初始化和校准继续沿本机调用链实现。

## 当前原机运行镜像

full-pon-radio-power01-6.18.55 的 ram-pcie/image.itb（6539368B，SHA256 a2d9c7a8567e881f91c0341f413d947e1af58a300f87f9161587ce01ed70eb53）由独立后台服务ponwrt-tyfza1-lan-20261009-222938成功构建。2026-10-09经原机U-Boot TFTP、签名校验与整机RAM启动，当前两颗均绑定内置rtl_ax_ap，自动完成BB、第一遍Radio和ByRate/TrackTSSI解析；BA CAM ACK/RXQ各1，错误全0。模拟/数字RF读回差异与live模块一致，保留待完整频道/校准继续核对。XEPON自动获取192.168.71.28，公网ping2/2。结果logs/radio-power01-ram-20261009.txt。

最终FIT实际解压内核已核对BB/RF/power/读回入口，此镜像对应284e563阶段；eFuse采集接口是随后54d127e原生模块实机验证的，未包含在这个FIT。当前镜像不包含完整limit/RU、第二遍Radio/FEM执行或AP，不报告SSID/关联/无线业务已经可用。

## 普通限幅、RU 与完整基础/FEM参数阶段实机执行

limits.c 按原机_hal_parse_txpwrlmt@1d6408、RU parser及store回调保存band/bw/ntx/rate/BF/regulation/channel/quarter-dBm/PShape。原生使用稀疏记录及地区名索引，避免依赖原厂rf_info字节偏移；保留国家/DomainCode映射、6G各功率类型、NA=63与WW=-63编码。普通查询按原机store赋值规则取最后记录；WW查询按本机set_worldwide取各地区最终记录最小值，忽略-128未设置项，全缺项返回0。基础/FEM的limit集合分开，对应原厂每阶段limit_default清空再配置，而非无条件继承基础限幅。

实际加载发现原机两份6G RU文件的RU106/2T PShape缺项：LPI声明8列只有7个零，VLP声明1列为空；同文件另外五个RU表PShape均为对应列数的零。准备工具针对两份确切原始SHA256补齐缺失零值，原始证据不修改，manifest分别记录original_sha256、安装sha256与corrections。未在通用parser中添加缺值默认成功。

上机limits03：C基础8文件，ordinary10416/RU6864条；FEM6文件，ordinary5480/RU3552条。XB基础/FEM各2文件，ordinary4452/RU3024条。C CN/5G/ch36/20M/1T/OFDM=50 quarter-dBm、RU26 HE=16；XB CN/2G/ch1/20M/1T/CCK=60/PShape1、RU26 HE=32/PShape0，均对应实际文件。所有42份参数已被原生驱动读取/解码，6G记录作为文件数据保留，并非本机开放6G射频。结果logs/radio-live-limits03-20261009.txt。

按rtw_hal_init_rf_reg@1bef20与halrf_config_rf_parameter@303760接入第二遍Radio，然后按hal_start_C/XB的RFE54分支重新执行FEM BB/gain/offset/reset和两遍FEM Radio。原机XTAL tracking配置为无操作；XB且RFE>50的EDPK函数@357060只读81ac/82ac bits18..23、81f0/82f0低9位并设host reference0xa00，未添加假想寄存器写入。

fem01原生模块SHA256 76807da6c19bbad667dfb2374822765a5073b87cff4c61f5bb43cf75fea7661d在两颗原机执行，radio_passes=2,2、active_stage=FEM。C基础A/B总写4088/4022、FEM256/256，基础固件页每路2×1568B、FEM每路2×484B。XB基础974/812、FEM8/8；其FEM无数字RF缓存项，但原机最终空页仍按class8/9各发送两次。XB EDPK gain=51/51、RXBB=120/120、reference2560。SIPI读回与前一阶段一致，RX错误0。输出logs/radio-live-fem01-20261009.txt。

接下来必须先执行rtw_hal_bb_dm_init@1b7d94→halbb_dm_init@2b055c：IC hardware/TDMA/DCR、GPIO、普通/hidden eFuse、supportability、PHY status、统计、EDCCA/DFS/DIG/CFO/RA/环境监控/功率控制等，再进入RF DM的NCTL和校准。完整PHY参数配置不等于BB DM/RF校准或AP完成，状态仍为bb_rf=pending。

## BB DM 的 IC、GPIO 与实际 flash/OTP 增益来源

bb-dm.h 依本机halbb_ic_hw_setting_init@2b011c顺序初始化TDMA host字段，执行C@2e14c4的a10/2a10 bit0（DBCC80+80 EVM report selection），或XB@2ebf68的0624低6位=15、0608bits8..13=9；XB还保存4b48/4ba4 CCK阈值，并读物理5e3/5af。DCR@2d912c只初始化host状态，不增加虚构硬件写入。

随后gpio_setting_init_C@2e585c/XB@2f0218按原机RFE54分支配置两路5868/7868、TRSW查表、0334输出及RFM PAPE/TRSW/LNA。两颗的RFE54 LNA信号反相且standby=1，RFM低3bytes=0a0604；XB另置bit25。gpio02模块SHA2563ba3368e6484f22c2076749706e3c164daba35b08580e252b17df6593707f998实机TRSW两路=a9a90002，C RFM=010a0604、XB=030a0604；XB timing0624=0101030f、0608=1e1e091e，factory/ADC trim=cc/f9与物理备份相符。输出logs/radio-live-bb-dm-gpio02-20261009.txt。

继续追normal gain发现本机C@2e81ac/XB@2f2290调用的是rtw_hal_flash_get_info@1daa58→rtw_nvm_get_info@4f0→rtw_flash_get_info@26c4，不能从physicalOTP逻辑map替代。实际ELF flash字典每项44B，gain IDs329..333对应2G CCK、2G OFDM、5G低/中/高，shadow offsets2d6/2d4/2d8/2da/2dc；每个byte高nibble是A、低nibble是B，有符号4bit。原机HS备份中HW_RX_GAINGAP对应50/50/239/15/0。prepare_wifi_runtime现在额外生成两个66B native flash-calibration.bin，保存这五项、各WLAN的thermal/xtal及实际2G/5G TSSI数据，manifest记录HS原文件哈希和安装文件哈希；没有复制HS中的密码等其他配置。6G normal gain的shadow填充尚未追清，本机2G/5G数据单独标定，不声称完成6G射频。

hidden gain仍使用本机物理路径：读585开始55B，再按实际ELF地址数组解码C每路径12组/XB每路径4组，CS取高nibble、CG取低nibble；全ff则invalid。原生bb-gain模块SHA2560a590c04f46ad97b50d926c78ef8df852e348857c206bd32dd9df393ed3e80fc在原机normal A=3,3,-2,0,0、B=2,2,-1,-1,0。C hidden_valid=0、thermal=28/28、xtal=71；XB hidden_valid=1、thermal=24/24、xtal=63；均与本机flash/OTP来源相符，RX错误0。结果logs/radio-live-bb-gain-20261009.txt。当前只完成BB DM前三个入口，supportability/PHY status及动态跟踪继续接入，RF校准/AP仍未完成。

## 原机普通模式能力位与 PHY report bitmap

halbb_supportability_default@2b03dc 的C/XB基础mask为38fa/8fb；share_xtal=false清bit6，RFE>49且配置antenna_diversity才置bit10。原生保留这两个只读启动输入；默认输入显式为false，不伪称已取到原机在线的两项dev_cap。两颗在halbb_cmn_info_self_init@2b01d0选择BB_CLIENT CR type2，不能因其MAC固件为AP而误用BB_AP地址。

physts CR@2c6aa4的bitmap数组基址073c；page9保留，page10..15的实际地址索引减1。halbb_physts_parsing_init@2c61a0先保存各页并为11..15置IE9，为6/7启用IE13，supportability bit3时为0..3/8/12..15置IE24。原生按该顺序读写及保存bitmap；普通PPDU解析尚未实现，不把这个寄存器配置说成收到无线帧。

physts模块SHA256 f5c276f0ecf5a862490ac55e83b0b2c6bb777d83cab2702b900e62df7c23505c在原机两颗supportability=38ba/8bb。逐页readback与对象一致，page6=2006、7=a002、11=201、12..14=1000202、15=1000206；RX错误0。输出logs/radio-live-physts-20261009.txt。后续继续common report/debug对象、TX power diff/MACID、统计及EDCCA/DFS/DIG/CFO/RA等原机入口，暂未进入RF DM。

最后进行实际post-PHY控制应答验证：临时诊断模块在全部已实现的基础/FEM、限幅、IC/GPIO、增益和PHY bitmap后再次发送原机BA CAM8初始化命令。此额外命令只放在artifacts/live-radio-post-phy-ack生成源码，正式overlay启动顺序不增加它。模块SHA25682f7d5a3c424b31a7e4daf04471c45881c74ec60d11dfdf52c7ee9799aa45835实机两颗done_acks=2、RXQ/IRQ各2、所有RX错误0，证明上述PHY配置完成后MCU与控制DMA仍然应答。结果logs/radio-live-post-phy-ack-20261009.txt。

## 最新完整 BB DM RAM 镜像

full-pon-radio-bb-dm01-6.18.55/ram-pcie/image.itb 的6544376B FIT（SHA2563a567502bb0e271970a7d614500113230c2d678010350f0345acb8ce8b088a7f）已经原机U-Boot签名校验并整机RAM启动。当前两个endpoint绑定内置rtl_ax_ap；基础/FEM各两遍、全部限幅输入、IC/GPIO、normal flash gain及hidden OTP gain都自动执行，结果与live模块一致，RX错误0。两份66B flash校准文件从最终FIT内置initramfs提取、与构建输入及设备实际文件核对一致；XEPON恢复192.168.71.28、公网ping2/2。结果logs/radio-bb-dm01-ram-20261009.txt。

该FIT对应9eace47阶段，不包括随后0427aa8的PHY bitmap或额外诊断命令。包含bitmap的候选full-pon-radio-physts01-6.18.55正由独立后台服务ponwrt-tyfza1-lan-20261009-233526构建；是否完成、是否上机以实际服务及后续记录为准。SSID/AP、普通PPDU/TX/WP、其余BB DM及RF校准仍待实现。

## 公共报告、TX power difference、MACID 与 CRC 筛选

继续沿本机BB DM：common report@296fe4的三个11项host直方图阈值直接取自原机常量；debug init@29b47c在BB20f4 bit24和20f8 bit31开启报告/调试端口时钟。TXdiff@2c7ebc使用原机.rodata+24b58的48个DWORD，写MAC d56c..d628；mac_write_pwr_reg@21ecb8明确是直接MAC地址，不能增加BB10000前缀。MACID init@2c7e0c在非DBCC startup清MAC d36c..d568的128个DWORD，覆盖256个MACID。

statistics_init@2cfad0只重置host统计并配置CRC2/CRC3筛选，没有虚构的hardware reset。原始rate参数为4/128/256/384，即OFDM6M/HT0/VHT1SS0/HE1SS0。实际legacy_rate_2_spec_rate@2c9eb8把6M映射LSIG codeb；BB_CLIENT的CRC字段全部来自cr_cfg_stat_init@2cfb48的BB0900，依原机顺序写OFDM、HT、VHT MCS/NSS、HE MCS/NSS，最后STATE_BEACON2映射MAC subtype8。

statistics原生模块SHA256 b7848aa55eecc28ddc3ede8850fe728527a739c10112e847f504b8a6fc5937b4在两颗原机全部48项TXdiff读回与原机常量一致，128个MACID power DWORD均0；报告clock/port分别01000000/80000000。CRC selector读回0400000b，解码OFDM_LSIG11、HT/VHT/HE MCS0、VHT/HE NSS1、frame_type8，RX错误0。输出logs/radio-live-statistics-20261009.txt。这里只完成初始化和寄存器配置，统计周期采集、普通PPDU、LA/PSD/EDCCA/DFS/DIG/CFO/RA后段及RF DM继续接入。

## LA、PSD 与 EDCCA 初始化原机验证

la_init@2b3020→la_reset@2b2fc4的common参数为trigger404us；mac_lamode_cfg@222a6c使能MAC8400/8404 bit17，保留9660 bits8..19，并编码enable1、exponent2、count101。默认LA capacity是262144B/32768个8B样本，但原机此时只配置host容量，没有在Linux分配262144B buffer或执行样本捕获。PSD init@2c1b94的实际参数为(1,3,2,512,0,3,36,42,2,1)，按para_setting@2c0c44的顺序设置BB422c FFT/IQ/HW-average/path/input字段；这里不发起单独的PSD测量或频道切换命令。

edcca_dev_hw_cap@2a5264的C raw caps为2G65/5G60/6G53/carrier65，XB为68/63/未设6G/carrier75。此处使用实际HW默认能力，后续AP配置可以覆盖；不把SDK通用默认值声称为本机在线配置。edcca_init@2a52d4初始化host high/low249、low_bound46，读取4848bits17..21的power offset，并只写0c70bits20..25=41来配置9.6us collision T2R。EDCCA mode是原生只读启动输入，默认0；动态门限策略仍待AP/周期管理接入。

signal原生模块SHA256ac4a4911cd6b7dffcbf0367567b22af9f68991be7d44698bcb5aa96e1c0bdfad实机两颗LA9660=65200001，PSD422c=0078f002，解码FFT512/IQ2/HWavg3/path0/input3；EDCCA power offset18、collision_state41、0c70=069bfe60，RX错误0。输出logs/radio-live-signal-20261010.txt。

随后仅在临时diagnostic生成源码中增加post-signal原机BA CAM命令，模块SHA2562ed3be2859134f97dcec7ce05cf92aa0a1e19b6bd2e645f2a785106ae97c8f0e。两颗完成所有已实现的BB步骤后仍收到匹配成功done-ACK，done_acks/RXQ/IRQ各2、错误0。正式overlay不增加此命令。结果logs/radio-live-post-signal-ack-20261010.txt。DFS/DIG及其余BB DM、RF DM与AP业务继续实现。

## 被动接收观测与遗漏的 hal_start RX 策略

在RAM上仅用临时diagnostic源码打开RX DMA3s，复用原生rx_open/start/close与DMA解析函数，没有发送802.11探测帧或收集SSID/完整报文。两颗都只有已知type10 MCU ACK，未观察到type0普通802.11帧或Beacon，RX错误0。结果logs/radio-live-passive-20261010.txt；它不证明普通无线接收成功，也没有单凭无帧结果指定根因。

回到hal_start_C@27792c/XB@27e410，确认MAC init后、BB/RF之前遗漏了实际AP_NORMAL4过滤策略、11264B MPDU limit、RTS阈值和特定DATA子类型过滤。rtw_hal_mac_set_rxfltr_by_mode@1b1514的AP4分支经rx_fltr_opt_2_uint@22fddc得到policy440e/flagmask47ff；原生先更新该策略再明确写MPDU22×512B。RTS API@1eb5b8在c614用highbyte32us/lowbyte16B编码，C/XB HW能力默认时间/长度0，原生保留两项只读启动输入以供后续AP配置。

frame type3在本机rtw_hal_mac_set_rxfltr_by_subtype@1b199c映射MAC DATA2；继续追mac_set_typsbtyp_fltr_opt@230444确认DATA对应ce24、CTRL对应ce28、MGMT对应ce2c。第一份临时版本误把DATA寄存器填ce2c，已在正式提交前按实际callback纠正，并重做板测。不得把“type3”解释成CTRL或沿用未经核对的寄存器排序。原厂DATA13/9/4三种subtype设DROP。

start-rx正式原生模块SHA256db21a32c7fdf103ef89c498017c03fb70be35e5fd1c255b0ad7ec24ece2ff8f0两颗实机RX policy=0816440e、MPDU11264B、MGMT/CTRL55555555、DATA51515455、RX_INFO006f、RTS0000。补齐后被动观测仍无普通802.11帧，故继续完成BB/RF与role/channel阶段，不宣称RX/AP已完成。结果logs/radio-live-start-rx-20261010.txt和logs/radio-live-ap-filter02-20261010.txt。正式源码不保留三秒诊断等待。

## 本机 DFS host 参数与地区表

dfs.h 按dfs_init@29e498和rgn_dmn_dflt_cnfg@29e138保存本机Gen1单位PW200ns/PRI25us、FCC1/ETSI3/KCC4的8类波形表；MKK2的W53/W56另有频道相关配置，继续接入。原机FCC pulses=18/23/16/12/15/18/15/255，不能使用新SDK的11/14/10/7/9/11/9/255。地区输入默认0未配置，后续由AP法规/频道生命周期选择；初始化只保存host数据，没有启用雷达硬件或报告。

host默认参数继续用原机debug命令的field-name/offset交叉核对：fk_dfs_num_th=5、idle_period=35、FA threshold50、fake_radar_count4、PW diff18、long-chirp diff20、PRI diff80，PPB原始参数5。原机halbb_dfs_debug的字段映射保存在analysis/radio-stock-lifecycle/dfs-field-names.json，避免将新版struct相邻字段套到旧机器码上。

DFS-host03原生模块SHA256a4a73e4ad2949d8d2c285e846d1479bfcda48c4ab224003b17f67d33b3604747在两颗原机完成初始化，默认region0、profile未配置，RX错误0。此结果仅证明host初始化与现有启动链相容，不证明雷达检测、pulse parser或CAC已经实现。日志logs/radio-live-dfs-host-20261010.txt。后续重点是DIG、其余BB DM和RF校准，最终再接native AP与DFS运行态。

## 2026-10-10 START_RX完整镜像冷启动与DIG/CFO实机推进

START_RX01完整FIT（source3b1b0e5）SHA25690c5b7cb79ff0db351e072371d77625c551177954eb92a85cc1eeb6f2c9209df，6547104字节，U-Boot验证签名并从RAM启动。最终FIT内核SHA256f5b82e8f9a61e1f631244db23e59b63d83d703fb7308ee44741cf3f29b3a19db，DTBed3a18f2b09dbec988883e9a36b47efdae819c82db47864deb3ad1e4eecd23ac；验证器从最终FIT解出的内核/DTB/initramfs匹配外部产物。两颗原生built-in驱动均获真实BA CAM成功ACK，RXQ/IRQ1、错误0。32个PHY bitmap、96个原机ELF TXdiff项读回一致，256个MACID power DWORD均0；AP_NORMAL4=0816440e，MGMT/CTRL=55555555，DATA=51515455，MPDU11264B、RX_INFO006f、RTS0。XEPON取得192.168.71.28，公网ping2/2。日志logs/radio-start-rx01-ram-20261010.txt，证据manifests/radio-start-rx01-live-20261010.json。此FIT不包含后续DFS/DIG/CFO新增步骤。

DIG严格跟随本机halbb_dig_init@2d69f8、CR@2d8374、gain_para_init@2d6054、reset@2d678c和cfg_bbcr@2d68f0。原机bb_info由zvmalloc清零；CLIENT CR仅设置初始索引和PD相关字段，IB power/LNA/TIA error CR未填充。get_reg_cmn先与mask相与，零mask产生零校正，因此原机初始化gain对象采用LNA{-24,-16,-8,0,8,16,24}、2G TIA{12,20}、5G TIA{16,24}，不是使用较新SDK被#if0隔离的寄存器。A路径真实初始索引读取472c[26:24]、473c[9]、46a8[14:10]。reset关闭OFDM481c[29]和CCK4b74[30]；follow_PAGC host默认false，与请求相等，原机不写四个follow CR。

原机reset调用PD函数的IGI参数38，随后初始igi_rssi写28，不能把两个常量混为一谈。无关联FA阈值从原机.rodata+25148提取196/352/440/528；H/L绝对范围12..90、12..38，PD offset16，FAHM1900ms，TDMA50ms，damping count6。初始IGI28，PD函数取max(IGI,RSSI+5)-min(offset,该值)=17，请求门限110-17=93；20MHz路径OFDM加7并向上取偶数100，编码(102-100)/2=1；CCK加5取负=-98，高门限127并启用。本机C/XB各自chip函数核对，PHY0寄存器操作一致，XB CCK使用direct BB写。

DIG原生模块83972588ee95864a317693dae63baf083967a465a8820dd70fc3fd6f678c4588在两颗原机索引6/1/31、IGI28、OFDM encoded1、CCK-98/127读回一致，错误0。日志logs/radio-live-dig-20261010.txt。目前接入原机正常未关联20MHz初始化，周期DIG、关联后FA/带宽更新仍待接入。

CFO按halbb_cfo_trk_init@28e2c4和digital_cfo_comp_init@28e03c接入：flash xcap71/63分别形成21..121、13..113 host边界，阈值12/40/80/120、step1/1/3/3，timer250ms但未启用。原机此initializer不调用set_crystal_cap；rtw_hal_nvm_apply_dev_cap@1da9d8仅对8852A写xcap，因此C/XB也不在这里臆加晶振写。实际初始写4264低8位0、4494[29]=1、4490[27:24]=8；MAC d248低3位C=0（DBCC2TX数字补偿），XB=6（硬件补偿）。原机动态加速条件为dev_cap RFE<=49，板卡RFE54故false，与新版SDK的条件不同。

CFO原生模块d10d6ee9ac771c0d624f17fe66e561414635a6753502fdffbfbb5904d055781f在两颗原机读回全部一致，RX错误0、公网2/2。随后仅在临时diagnostic模块beb2f8a019c0b19de6178644ea1fc4a512ae65d4b08ad6a022674ee8476f736a追加一次post-CFO BA CAM实际命令，两颗均成功ACK，done_acks/RXQ/IRQ=2，错误0。正式overlay不增加此命令；板上已恢复正常CFO模块。日志logs/radio-live-cfo-20261010.txt和logs/radio-live-post-cfo-ack-20261010.txt，证据manifests/radio-live-dig-cfo-20261010.json。周期CFO及实际晶振调整、普通802.11收发和AP仍待实现。

下一个原机halbb_ra_init@2cd49c仅遍历128个33B host对象，非空对象直接跳过，没有硬件或H2C操作。不照搬其异常空指针memset分支；原生station/RA对象将在真正MACID关联生命周期接入。继续紧随其后的env_mntr_init@2acfa0，按CCX→CLM→NHM→IFS→FAHM→EDCCA-CLM顺序核对原机函数。

## 2026-10-10 用原机接收计数定位802.11阻塞

环境监测已按env_mntr_init@2acfa0和CLIENT CR@2ae66c接入18次初始化写，顺序CCX→CLM→NHM→IFS→FAHM→EDCCA-CLM。原机set_reg_phy0_1@2b0f54实际只操作一个0x10000 BB窗口；保留CCX32us、CLM input8、NHM includeCCA2、nav/rssi使能host状态，以及原机11档NHM/FAHM阈值。C最后编码阈值218、XB120，阈值参数留在host，测量触发和采集继续接入。两颗36项寄存器字段读回全部匹配，日志logs/radio-rx-stage01-20261010.txt。

真实802.11帧接收是当前主要阶段验收项。生产代码增加描述符type0..15、Beacon计数和radio_rx_pipeline；后者按原机CLIENT统计寄存器读取CCK/OFDM CCA、五类CRC，按mac_rx_cnt@1f6b08的selector cee0/W8→cee2/R16读取八类MAC CRC/FA，并报告HAXI、DMAC/CMAC、两路RXBD指针和CCK gate。只计数和读回，不把BA CAM成功ACK当作802.11接收成功。OFDM CCA地址通过原机CR字段168和对应统计读取链核对为1a24低16位。

scripts/build_rx_stage_observe.sh构建原生实机观察模块，tools/wifi_rx_stage_observe.py仅在生成源码加入MAC、base/FEM、DIG/CFO、环境监测后四个5000ms接收窗口；窗口内实际开启PCI bus master和原生RX，中间记录PHY/MAC及DMA指针，结束同步关闭并报告已消费的描述符。正式驱动不加入这些启动延时，也不发送802.11探测帧。

实机模块SHA256614c77907b3a8dd19fde7e832b7d65da1cdc4d5170542494181dc86a38a3a546结果：MAC阶段两颗CCA0；base/FEM后C OFDM CCA710→65535，随后读数保持上限；XB21→567，DIG/CFO603→716，环境监测726→865。所有阶段有效PHY/MAC CRC均0，XB OFDM错误CRC1，RXQ指针均00010001，只有初始C2H type10=1；802.11 type0和Beacon均0，RX owner/tag/格式错误均0。证据manifests/radio-rx-stage01-20261010.json。当前有CCA活动但未观察到有效帧解调，排查优先指向频段/信道、接收使能和RF校准；没有据此声称已证明DMA或后续解析完整可用。

原机rtw_hal_set_ch_bw@1cf3cc会执行MAC带宽→BB频道/带宽→RF频道/带宽→功率和RFK。当前链尚未接入完整频道设置，尤其本机halbb_ctrl_bw_ch_8192xb在2.4GHz路径调用halbb_ctrl_cck_en_8192xb@2ef190：0700[5]=1、2320[0]=0、0c80[31]=0。接下来在实机对比该原机使能步骤前后CCK/CRC/主机收包结果，再补齐对应频道和RFK路径。

第二次观察出现了不同的实机结果：模块60e1ca0e3a40b6e45b42428b346c5c381716616026ce8948b9658da06a0f6a1f的XB在base/FEM窗口已达到CCK PHY/MAC CRC_OK各12、type0描述符8、RXQ index00090009，主机格式/tag错误0。此时尚未执行追加的CCK使能写。追加前后gate完整读回都为00000030/00000000/38000000，说明BB表已经使能这些位，不能把收到帧归因于追加的CCK操作。只缺少调用某个函数不等于其控制位没被设置。日志logs/radio-rx-stage-cck01-20261010.txt和证据manifests/radio-rx-stage-cck01-20261010.json保留此次区别。第一轮零帧是该次窗口的结果，并非已证明所有后续阶段永久无法收帧。

接收描述符CRC/ICV状态从两颗原机WD解析函数核对为DWORD3 bits9/10；原生packet和RX统计已接入每帧CRC标志，Beacon计数要求完整24B管理帧头及12B固定参数。进一步的临时观察模块只记录前16个type0帧的WD、长度、速率及前24B帧头，确认实际802.11类型及状态；正式驱动不增加报文日志。持续观察模块在已实现的初始化后保持原生RX运行，让后续原厂信道/RF路径研究期间也能继续积累真实计数。观察模块的生命周期仍由正常RX关闭/WCPU关闭/DMA释放处理。

持续观察模块5c29a4a131b644cdfbf917a341395835f30bb2970986db5c140310fd23ee15f4最终在XB收到10个已确认的802.11 Probe Request帧：每帧200B，WD type0、hardware rate0（CCK1Mbps）、CRC_error0/ICV_error0；24B帧头FC0040，协议版本0、management type0/subtype4，目的地址和BSSID为broadcast，sequence字段变化。WD长度、payload边界、PHY/MAC CRC与DMA→IRQ→主机解析链对应。证据logs/radio-first-80211-20261010.txt、manifests/radio-first-80211-20261010.json。这是实际无线帧接收的首个阶段结果，仍不包含OFDM/5GHz成功或AP业务。此前没有源持续发送的短窗口返回零帧，不能由该结果推断接收永远阻塞。

正式原生backend现在在已实现的PHY初始化结束后恢复PCI bus master/H2C及RXQ/RPQ IRQ，保留可用RX继续收帧；正常停止入口仍先同步关闭RX、WCPU/HCI再释放DMA。radio_state根据真实HAXI enable bits报告active/paused，不再写死paused。正常overlay不加入观察延时、额外命令或帧头日志；mac80211分发暂未接入，真实type0帧仍计入unhandled。这些计数保留直到普通802.11帧转交native mac80211。

RX_ACTIVE01正式FIT（sourcee75bf3b）SHA256f34fbd0e8355adb77626418ec30bbdc49e0402ed5e6a1e819c723797bbfcbd05，6549320B，实际内核SHA2560f982cab65f79fbeab40fbc432f851e7d51a56389191e68ae6ef973ec8329208，经U-Boot签名验证及整机RAM冷启动。两颗内置rtl_ax_ap自动hci=active/RX running1，环境监测36项字段读回匹配，XEPON DHCP192.168.71.28、公网2/2。随后保留XB内置驱动持续收包，已收到14个CRC正确的真实type0帧、RXQ15（含C2H1）、格式/tag错误0。证据logs/radio-rx-active01-ram-20261010.txt、logs/radio-rx-active01-builtin-frames-20261010.txt及对应manifest。因此接收结果已进入正式启动镜像，不仅是临时观察模块。

C单独替换为原生读取模块以核对5GHz状态，XB仍使用上面正式FIT中的内置驱动收帧。C BB实际中心频率5800MHz、SCO45、channel report0；模拟RF18为A01001/B01000，低8位频道1/0，原机RF5GHz band编码位未设置。BB与RF默认频道/频段尚未统一，完整rtw_hal_set_ch_bw路径是5GHz验证的下一项。证据logs/radio-channel-read-C-20261010.txt、manifests/radio-channel-read-C-20261010.json。

频道和RFK后段需要部分RF字段写，已严格核对本机模拟写C@2e2e64/XB@2ed448与数字写C@2e3060/XB@2ed644：模拟写等待31次busy观察；部分mask时写BB0374低20位，再按首个mask位移位data并在BB0370命令中置bit31。原机使用硬件mask写，不是先读RF再拼写。数字写使用e000/f000窗口的字段更新及1us delay。公共native masked API已接入原有全20bit Radio表加载；C实际回归完成全部base/FEM加载及成功BA ACK，部分mask调用将随完整频道/RFK控制验证。另核对C频道控制中的pathB SYN修正是hardwareCV0条件，本机CV1跳过，不能把hal_com字段4误解释成PCIe bus type。

## 2026-10-10 C5GHz频道设置与实际OFDM帧接收

原生channel.h接入本板C、非DBCC/PHY0、5GHz20MHz频道操作，用于继续定位接收链；40/80/160MHz、XB频道、完整功率/RFK和mac80211配置入口继续实现。radio_channel写频道号，控制事务持command_mutex，先同步关闭RX并暂停HCI/bus master，按MAC BW→BB频道/增益/ADC/BW→RF频道/接收BB LUT顺序执行，再恢复RX。

MAC cfg_mac_bw@21d980的20MHz5GHz分支更新c010低2位0、c628字段3、c0880。BB ctrl_ch_C@2e9430按路径A→B应用原机gain表、hidden/normal校准及band mode，随后SCO中心频率/补偿和CCK参数。gain CR数组从本机.rodata+26140/260c8/26178/261b0/261d0/26208/26240逐项提取，原生RFE54分支还包含wideband ELNA/LNA/TIA、GS和ELNA。原机隐藏OTP全ff故不写hidden校正；normal低5G取A-2/B-1和PHY0初始offset24，external_loss14/10，RSSI correction-14/-10及RPL/TB字段均按原机计算。

原机C直接把频道号写0734[23:16]，并未调用新SDK的ch_idx_encode。BW20 ADC@2e333c在BB c0xx/c1xx窗口执行完整字段配置，RFE54的c0ec/c1ec[23:16]=2。接入edcca per20、5GHz CCK关闭、PD boost关闭、5M mask关闭、两路径TSSI reset和BB reset。RFE54跳过iFEM spur branch，ctrl_btg_C对RFE>49直接返回。RF ctl_band_ch_bw_C@30b1a0先读四个RF18，随后A模拟/数字、B模拟/数字各写并延迟100us；本机CV1跳过CV0 B SYN workaround。原机函数不存在新SDK末尾IQG toggle。rxbb_bw_C按原机ee[2]、33、3f LUT设置20MHz值27，再退出LUT写模式。

channel-C01模块8a5c32f098970710a1c1abfa8e95adfa2fc86267ba916c85914d13f9ae488e76在原机写36后BB5180MHz/SCO51/channel36，RF模拟A11d24、B01c00、数字两路10d24。B模拟寄存器未镜像A，这一点保留为实际读回，不据此臆加CV0 workaround。随后C PHY/MAC OFDM CRC_OK增长至2345、CRC_error116，RXQ仍只有C2H。有效解调已发生，阻塞范围缩到过滤/主机转交，而不是原先BB/RF频段未统一。

rx_mode接入本机RX_FLTR_MODE SNIFFER0、SCAN1、AP_NORMAL4：本机wrapper@1b1514和converter@22fddc分别产生SNIFFER bit0、SCAN maskbe/value0e、AP mask47ff/value440e。显式MPDU11264B继续保留。切换时先恢复AP模式，再设置请求模式，避免上一次SNIFFER标志遗留。没有把监听默认用于AP。SCAN1实机options0816440e、有效PHY解调仍无主机帧；SNIFFER0 options0816440f后，主机迅速收到681个CRC正确type0帧，RXQ/IRQ增长，格式/tag错误0。日志logs/radio-channel-C36-scan-20261010.txt和logs/radio-channel-C36-monitor-20261010.txt。

只在临时header诊断模块44a28708ee1f14fb73ac9065c9d9edc2fef8783caf40b13d31efe1c6f30c6fa0记录前16帧的WD、前24B及FCS计算，正式overlay不加帧头日志。16帧均20B（含4B FCS），frame control00b4是RTS，WD rate8为24Mbps OFDM，CRC/ICV错误0，每帧FCS与crc32_le计算逐项一致。实际air-frame→PHY→MAC→DMA→IRQ→native WD/payload解析已在5GHz确认。原机36/40/44/48四个频道各5000ms被动观察都继续收到CRC正确type0帧，RXQ绕环运行且错误0；暂无Beacon或普通数据帧证据。证据manifests/radio-5g-80211-C-20261010.json。继续完成RF校准、普通数据及mac80211/AP业务，保持全移植目标。

## 2026-10-10 RF晶振、RF GPIO、NCTL和RCK实机阶段

RF启动开始于本机share_xtal_C@30fd14/XB@33f8fc。native independent_xtal对应原厂dev_cap.is_indiv_xtal，当前false（共享）；尚未读到原机运行时此字段，不把此默认宣称为原机运行值。C共享时先清XSI8低2位，然后XSI4/5低7位写flash xcap71；独立晶振时保留XSI8，仍写flash。XB共享写127，独立晶振才写flash63。这一点不能统一成两颗都写flash。mac_read_xtal_si@21e364命令为81000000|address，busy31、50us观察、返回0271字节；native按此读回两颗XSI4/5分别47/47和7f/7f，XSI8均34。CFO host默认值保持其原机较早initializer中的flash来源，周期控制继续接入。

RF GPIO采用set_gpio_C@30f69c/XB@33e32c实际RFE54分支。C BB pin配置A10/TRSW/inverse1、A7/LNA/inverse1、A6/PAPE/inverse0，B1/TRSW/inverse1、B2/LNA/inverse1、B3/PAPE/inverse0，额外A11/inverse1/ZERO7。XB为A11/PAPE/inverse0、A7/TRSW/inverse1、A6/LNA/inverse1，B1/PAPE/inverse0、B2/TRSW/inverse1、B3/LNA/inverse1，额外A12/inverse1/ZERO7。对应MAC GPIO func19直接从本机C.rodata+1f820、XB+21508的指针表解析ELF relocation，每项12B记录func/reg/mask/value。全部14个本板pin记录均为地址2d0+pin/2、低/高nibble选择8。

实机BB path selector均00000054。C inverse00000c80/00000006，source30777777/77777477、77770347/77777777；XB inverse000010c0/0000000c，source43777777/77770777、77773407/77777777。MAC mux逐项与原机记录相符。原生重新选择RFE pin后继续验证接收，不把初始化寄存器写完当作RF校准完成。

prepare_wifi_runtime从原机host ELF提取三个NCTL微程序常量数组，验证原机host SHA和各symbol长度/hash后安装到lib/firmware/realtek/tyfza1，记录NCTL输入清单；完整FIT验证器从实际内置initramfs核对它们。driver使用native request_firmware/MMIO loader，按hardwareCV选择C1769/1782对、XB1421对。实际config_nctl_C@3106f8/XB@33ffb0先写0c60/0c6c、58ac/78ac、8000，再写8080=4、1us、读回，最多1000次；本机到上限仍加载table，native保持这个语义。NCTL callback的f9..fe延时也是原机格式的一部分，三个本板数组均无此类地址。原机数组作为设备微程序数据，NCTL加载控制由原生驱动执行。

两颗原机NCTL reset均第一次读回4，随后加载C1782、XB1421对，结束8000=8/8080=0。RCK primitive C@30cc44/XB@33c69c硬件步骤相同：RF5=0、RF0模式3、RF1b=240，先立即读取RF1c完成bit3，最多11读/10次2us延时，读取RF1b[14:10]结果并写回RF1b，RF5=1。本机等待到上限仍提取和安装结果，native不把它改成中止启动。原机rck_trigger@2f6654总是A→B。

RCK原生模块22f120fe59acad504193e7a0cd79c039c239fecea2b7e8754c37cae2d3a92d81实机C两路code16/16、XB8/8，四路全部完成位1且均首个观察完成。结果logs/radio-rck-20261010.txt和manifests/radio-rf-nctl-rck-20261010.json。RFK host状态、reference power对象、DACK/RX_DCK/TXA_LCTK/trim/TSSI及完整频道功率/RFK仍继续实现，当前不声明全RF校准或AP完成。

接收统计另对齐了本机CRC CR：HE=1a58、VHT=1a5c、HT=1a60、legacy OFDM=1a64。较早radio_rx_pipeline把HE/VHT文字标签互换，新版已修正；原始历史日志保留。5GHz24Mbps RTS对应legacy OFDM路径，已有16帧软件FCS验证证据不受此标签修正影响。

## 2026-10-10 原机 DRCK、ADDCK、DAC 测量和回填

`dack.h` 按本机 `halrf_dac_cal_8852c`、`halrf_dac_cal_8192xb` 的实际调用顺序实现：保存 RF0 A/B → DRCK → 关闭手动结果 → RF5 A/B=0、RF0 A/B=337e1 → RX 时钟 → ADDCK → 释放 RX 时钟 → 回填 ADC I/Q → RF1 A/B=0 → DAC A/B 测量、保存、回填 → 恢复 RF0 A/B、RF5 A/B=1。ADC 与 DAC 之间保持手动射频控制，整段在恢复主机 RX 之前执行。C 的 BTC 通知在这些调用处 state=2/3；实际 `halrf_btc_rfk_ntfy@2f8824` 只有 state=1 进入等待，其余只有条件日志，因此这里没有额外硬件命令。

DRCK 使用 c0c4[6] 触发、c0c8[3] 完成位，随后按原机脉冲 c094[9]、读取 c0c8[14:10]、回填 c0c4[4:0]。XB 先选择 c0c4[9]，C 无此初始操作。ADDCK 两路按实际 36 次字段写启动，c0fc/c1fc[0] 完成，I/Q 各 10 位回填 c0f8/c1f8。硬件 CV1 的 XB RX 时钟参数来自本机 `.data+196c98`，不是新 SDK 表。

DAC 两路保持本机差异：C 清 TX force → 选择时钟 → 开 force，XB 开 force → 选择时钟；C 写 c004/c024 高字段 0x30 且启动后等 1us，XB 无这三步。四个完成条件依次读取 c040/c064[31]、c05c/c080[2]。每路保存 16 个 I/Q MSBK 项、bias 和 DC，并按本机逆序四组 MSBK、第五字 bias/DC 的布局写 c200..c24c、置结果选择 bit0。原机观察到上限仍保存和回填结果，原生控制保持此语义。

模块 a66ae0f88f4c73481b8a1e3563726549aecbfe2b36da95e7ccdad9c6cf690ed4 在原机实测：C DRCK code19、XB13；两颗 ADDCK 两路均首读完成。C DAC A/B 在 465/471 次观察完成，XB 在 313/318 次观察完成。C 20 个结果字读回全部等于写入值；XB 16 个 MSBK 字、两路 I 的 bias/DC 字匹配，Q 的 c224/c24c bias 高位读回为零，DC/选择位匹配。原机 ELF 的回填函数和 GPL 均对这两字写 bias<<22 | DC<<14，当前保留真实差异，尚未证明 XB Q bias 的硬件读回语义，不用任意寄存器改写消除差异。证据 `logs/radio-dack-20261010.txt`、`manifests/radio-dack-20261010.json`。

该次校准后 C 在频道36、原机 SNIFFER0 下收到 3729 个 CRC 正确的实际 type0 帧，其中 262 个 Beacon；PHY/MAC OFDM CRC_OK 均6977，描述符格式/tag错误0。XB收到6个CRC正确type0帧。XEPON公网2/2。普通帧当前仍返回 unhandled，下一步把真实帧接入 Linux 无线层；RF 其余校准和 AP 收发业务仍继续实现。

实机观察模块718589290145d2ae6ff17488b08d6a236c63302a3f7f7b260f45a135207f77e5按802.11类型/子类型分别取两个实际帧，记录WD速率、前36B及整帧软件FCS；只在生成的观察模块加入日志，正式overlay没有这些帧头日志。18个实测帧的末尾FCS均与crc32_le结果一致，包括C的两帧289B Beacon、QoS Data、普通Data、Null Data、RTS、CTS、Probe Request、CF-End，以及XB的两帧Probe Request。QoS Data的WD rate410即原厂enum中的HE2SS MCS10；这证明已经收到真实HE数据帧，不只是CCA活动或控制帧。证据`logs/radio-dack-frames-20261010.txt`、`manifests/radio-real-beacon-data-20261010.json`。

随后接入Linux6.18.55原生cfg80211/mac80211，不同时加载OpenWrt backports中的另一套无线核心。`monitor.h`把本次已验证的C5GHz20MHz通道接到nl80211监听接口；配置复用原机MAC/BB/RF频道操作，过滤复用SNIFFER0。普通type0描述符复制实际payload到skb，按本机WD字段转换速率/带宽/GI及CRC状态，保留真实FCS，再交ieee80211_rx_irqsafe。未实现PPDU RSSI时明确使用RX_FLAG_NO_SIGNAL_VAL。驱动计数`handed_to_mac80211`用于与实际用户态Radiotap抓包对照。

此阶段只注册C的36/40/44/48频道监听接收。XB频道、原厂MAC角色、TX描述符/队列、AP关联及加密尚未接入，接口能力不宣称AP；monitor注入报文不送硬件，计入TX_unimplemented。正常接口停止同步关闭RX，注销先停止无线层再释放原生DMA。原机HS中的WLAN_MAC_ADDR随native calibration容器TYFC0002保存供接口标识，尚未把此HS字段宣称为已核实的两颗无线运行时地址分配规则。新原生mac80211入口等待完整FIT RAM上机验证。

继续核对后续原机流程：`halrf_dm_init@303c14` 在 DACK 后调用 `halrf_rx_dck_trigger@2f53e0(phy0,1)`；实际 trigger 不透传这个1，而是对RF IC8/C强制传0，对IC32/XB强制传1。因此C执行RF RX_DCK，XB执行AFE RX_DCK。C按kpath及RF5自动控制状态保存必要RF0，TSSI正在运行时暂停58/78xx，再执行RF93低4位清零、toggle和原厂条件重校准；XB在8080=4/0之间分别处理A/B，其AFE helper额外保存ADC/时钟寄存器、配置时钟和ADC直流补偿。后续实现必须保留这两条分支，不能只统一脉冲RF92/93。

原机普通TX BD入口C@27cdb4/XB@283890已提取：每个BD条目8B，低16位为WD页长度，bit30为last segment，地址高8位编码到[29:22]、低32位在第二字；生产者索引按实际ring长度回绕。原厂48B host ring对象、WD page指针数组及内核缓存维护接口属于原厂host布局，原生实现将使用Linux DMA和自己的skb所有权。普通TX仍需核对完整WD构造、MAC角色/地址CAM、功率/RFK和RPQ释放报告，不能把已有H2C固件下载通路当成普通802.11 TX。

原生mac80211和iw/tcpdump进入完整initramfs后，实际Image为24502280B，内核从2MiB加载，已越过原24MiB DTB位置。RAM FIT构建因此明确失败。新RAM FIT把DTB放到32MiB，保留2MiB内核、80MiB FIT输入，签名和实际载荷核对沿用原流程；历史已启动FIT及其manifest保留原地址。

首轮原生无线核心RAM FIT SHA25676f2e4923e0bdb9f419c47ad643a7a62087041184e62f68e3c4eaaef1f0c4d8a经U-Boot验证签名并成功冷启动，实际kernel SHA2566408685f4877e02fdd8a7ddd2100dd37d16a10e4597a644e4f5377f7083a97ec。两颗使用内置rtl_ax_ap，未加载观察覆盖模块；DACK各路完成，XEPON仍获192.168.71.28。`iw phy phy0 interface add mon0 type monitor`、接口up及频道36 NOHT均成功。tcpdump实际获得64帧，DLT127/Radiotap、kernel dropped0；拆除每个实际Radiotap头后，64个完整802.11末尾FCS全部等于软件CRC32，其中RTS30、CTS29、Beacon5。接收链已经从空口到原生mac80211再到用户态抓包，证据`logs/radio-mac80211-first01.pcap`和`manifests/radio-mac80211-first01-receive-20261010.json`。

上机另发现主线cfg80211默认要求签名regdb，但OpenWrt原package只安装重新生成的regulatory.db、不安装.p7s。本次生成文件6332B，与正式release中6348B的已签名文件不同，不能给它直接配release签名。原生构建改为保留同一官方release中的db/p7s二进制配对；补丁由当前pinned package原文件生成，以零fuzz应用。prepare_wifi_runtime按实际OpenWrt package版本/hash读取已下载release，记录并安装两文件；最终FIT逐项核对。实际release配对CMS验证通过，RAM安装并`iw reg reload`后已接受CN规则，未改变主线cfg80211签名要求。正式下一镜像继续验证冷启动加载。

进一步按实际Data类型过滤抓取8帧，用户态取得7个Null Data和1个130B受保护QoS Data，所有完整FCS均匹配、内核抓包丢包0。不是把Null Data等同为业务载荷，受保护QoS帧独立保留为数据接收证据。pcap和记录分别为`logs/radio-mac80211-data-first01.pcap`、`manifests/radio-mac80211-data-first01-20261010.json`。同时驱动已累计交给mac80211超过2万帧，TX_unimplemented=0，证据来自内置原生驱动的持续被动接收。

SIGNEDDB02正式完整构建服务ponwrt-tyfza1-lan-20261010-041510成功结束，source c4458e1。实际启动FIT SHA256aca49174f37e88cb4a05e7b433bde85dd1773f1c623f57d6590175bbd24b3368、7315360B；内核18ae2848ff5ee0ef39042c1170ea4f4cbad5d46c5d52cf9afd62b4248742d512、DTB ed3a18f2b09dbec988883e9a36b47efdae819c82db47864deb3ad1e4eecd23ac。经U-Boot实际验证签名并冷启动，内置rtl_ax_ap、kernel taint0，无RAM覆盖模块。最终FIT的内核、DTB、initramfs及原机校准/身份输入均已核对；原生radio代码与链接ELF逐项对应。

新镜像冷启动即读到正式db/p7s配对，两文件SHA与release清单一致，CN规则接受，日志无缺失签名/无效db信息。频道36 mon0再次得到64个实际Radiotap帧（RTS27、CTS27、Beacon9、Association Request1），完整FCS全部匹配。native rx_unhandled=0、格式/tag错误0、内核抓包丢包0；两颗DACK各路完成，XEPON DHCP192.168.71.28、公网2/2。记录`logs/radio-mac80211-signeddb02-serial-20261010.txt`、`logs/radio-mac80211-signeddb02-20261010.txt`、`logs/radio-mac80211-signeddb02.pcap`、`manifests/radio-mac80211-signeddb02-live-20261010.json`。当前原机运行此正式镜像，mon0保留接收。接收阶段已达到用户态真实帧验收，继续按原机补XB频道、RX_DCK及原生AP收发/关联，不把监听接收视为完整AP移植。

## 2026-10-10 XB2.4GHz频道与原生mac80211交付

继续前的原机XB统计：CCK正确110，OFDM正确0/错误65535，BB默认5800MHz/SCO45/channel0。`xb-channel.h`按本机ctrl_bw_ch@2f35fc实现PHY0/BAND2G/20MHz：MAC带宽 → CCK SCO → BB频率/增益/校准/带宽 → RF频道/带宽。LNA、bypass、op1dB和TIA地址及mask从实际ELF `.rodata+2b9a8+940..a98`逐项提取；两路normal/hidden校准按A→B保留共享CCK2340回填语义，RFE54 external FEM的WB ELNA/LNA/TIA、GS及ELNA均接入。

本板factory trim cc、ADC trim f9低5位25，实际bw_setting@2ede40走2ee188专用参数8/2/2/4/15/10、c0ec/c1ec高byte3；随后hardwareCV1分支写12a0/32a0高字段146，两次均清c000[18]，BW20选择c0ec/c1ec[14:13]=2。原机2.4GHz wrapper最终走2f3954关闭spur，不能误写5GHz151/153/155的滤波参数。原机CCK使能、loop filter20值2、5M关闭、TSSI reset、BB reset原顺序保留；频道14专用SCO及CCK系数也按原机常量处理，当前CN无线接口公布1..13频道。

RF ctrl_ch@33ba58对A模拟/数字→B模拟/数字分别调用ch_setting@33b72c，更新RF18前按频道1..4/5..10/11..13/14写RFbe=a0/800a2/800a4/a5，保留A路30次读取/5us延迟及重写的实际语义，B路直接延迟5us。RF18按原机清掩码、频道值写入并等100us；随后ctrl_bw@33b628对各路各bank保留20MHz位。op5k的初始化host备份/周期策略继续接入，RX_DCK和完整RFK仍待后续移植。

首个channel模块9d4d3124da079cf7ce1dc2d65025e1bc3fa2e1d48cc0a64f2fd95b4c2b51fe63只替换XB，C内置mon0持续接收。频道1 BB2412/SCO109/channel1，模拟RF18两路01c01、数字00c01。1/6/11各5秒被动窗口中，频道6首次得到正确OFDM2、HT3，累计Beacon40；到频道11 host正确139、Beacon71，格式/tag错误0。原始记录`logs/radio-xb-channel-passive-20261010.txt`保留了仍较多的PHY OFDM错误。

原生mac80211增加XB13个2.4GHz频道、12个CCK/OFDM速率，RX metadata按实际芯片转换band/frequency及legacy rate索引。模块5294c84b54dde6857ff940f23f03d0e1415c288a5b56552da2e7e3f3db04b0b5在原机注册phy1/mon1，频道6实测2437MHz；tcpdump获得64帧，其中Beacon36、普通Data2、Null Data14及其他管理/控制帧，完整FCS全匹配、内核丢包0，native rx_unhandled=0、格式/tag错误0。证据`logs/radio-xb-mon1-channel6.pcap`、`logs/radio-xb-mon1-channel6-20261010.txt`、`manifests/radio-xb-channel6-receive-20261010.json`。当前C内置原生mon0与XB原生live mon1同时工作，未加载原厂host模块。

复核原机晶振字段时确认此前命名有误：实际RF函数读取phl_com+58fd的`dev_cap.is_indiv_xtal`，0代表共享、1代表独立，而不是share_xtal。native输入改名independent_xtal，保留已实测的值0及对应寄存器动作。BB supportability也按此字段0关闭CFO tracking能力；C在0时清XSI8低2位，XB在0时写127，在1时写flash63。上述晶振章节语义已修正；旧日志中的share字段是旧名称下的实际布尔输入，数值不变。本机运行原厂时的最终赋值仍未读到。GPL存在仅dev_id2启用independent的triband条件，而本机实际final_cap_decision函数没有直接写58fd，继续追原机字段初始化来源。

## 2026-10-10 原机XB AFE RX_DCK

`rxdck.h`在初始DACK之后接XB原机rx_dck@33c498：8080=4，A→B的RF5手动、RF0模式3、RF93低4位8、AFE校准、RF5自动，结束8080=0。当前对应初始化is_tssi_mode=0；后续TSSI运行时的暂停/恢复继续接入。AFE helper@33be54保存12a0/32a0、c0d4/c1d4、d8、c4、e8、8034、80d4七组寄存器；强制RX时钟selector3的CV1参数严格取本机`.data+196c38={9,0,0,3,15,10}`。按原机设置ADC、滤波reset、NCTL子页，再分别用fc030001/7c030001及fc050001/7c050001测量RX与DPK，观察80fc[16]，结束恢复七组寄存器。

原机ELF和GPL参考在这里有差异：ELF两轮测量之间、结束后均没有SDK的RF8f操作，也没有其额外5670切换，原生保持实际机器码。等待保持原机最多1501次读取/1500次2us延迟，观察到上限后仍执行原机下一测量与恢复顺序。C走RF RX_DCK，仍待另外接入完整的RF LUT offset检查/修正路径，本阶段不声明全RF校准完成。

前两次观察四个完成位均1、14个寄存器恢复匹配，但c400/d400读取全部为零。随后逐项检查本机ELF所有重定位，halrf_rx_dck_check_8192xb没有任何调用或函数指针引用；其旧BB表检查不能作为本机实际AFE验收条件。原机halrf_get_rx_dck_value@2fba44由RX_DCK debug调用，实际读取RF92/93，因此观察改为保存当前RF gain处的真实RF92/93，不把未用的BB表检查塞进初始化。

模块eb65ac58e01ba18a5012cb7110d74b8479125e66799ef6369d3a14b131619bfd实机RX/DPK完成位1/1、1/1，观察222/22、222/22；14个寄存器逐项恢复匹配。A RF92/93=79c00/67808，B=65c00/88008，记录`logs/radio-xb-rxdck-result-20261010.txt`。这是当前gain处的实际结果读回，不代表整个RF gain曲线已逐项验收。 较早观察模块0ba086ba3a4a1444b3ce891d44e469e326d8f277a8ba8f42a02eef7d40c4307d在校准后频道6仍捕到64帧，host正确75、描述符错误0；PHY OFDM58/错误13157，继续核对其他原机RFK和动态跟踪，尚不能把接收错误变化归因于单一校准步骤。


DUAL_MONITOR01完整构建（source278250a，服务ponwrt-tyfza1-lan-20261010-044513）经实际载荷核对、U-Boot签名验证和RAM冷启动。FIT SHA25692dfd5a3bf320ef39ebb90323165ffd512994af672d39f8e0b5584150f6e4813、7316324B；kernel cec12fe35a11b61cd464bbcbd4febbc3f0e9cecc10be679a03e09382687d2fc6。两颗均内置rtl_ax_ap，taint0，没有live覆盖。建立phy0/mon0频道36与phy1/mon1频道6后同时实际抓包：C32帧，XB64帧，全部完整FCS匹配；XB其中Beacon53、普通Data5，内核抓包丢包0，格式/tag错误0。XEPON仍获192.168.71.28并公网2/2。当前原机保留此正式双频镜像及两路监听，证据manifests/radio-dual-builtin-20261010.json、logs/radio-dual-builtin-C.pcap、logs/radio-dual-builtin-XB.pcap。

包含初始XB AFE RX_DCK的下一完整构建已独立后台启动：source7e3848d，服务ponwrt-tyfza1-lan-20261010-050226，输出artifacts/full-pon-radio-dual-rxdck01-6.18.55。它与上述已冷启动镜像分开记录，完成后继续上机核对。C RF RX_DCK、完整RF gain曲线、动态跟踪及AP收发/关联保持后续工作范围。

## 2026-10-10 原机C RF RX_DCK

`c-rxdck.h`按本机初始化路径实现C RF RX_DCK。rx_dck_init@2f69ec读取A路RF95[3]决定额外reK能力；各路先观察RF5是否自动，手动情况下保存RF0，随后RF5=0、RF0模式3、DBCC bit1=0、RF93低4位0。toggle@30c150执行RF92[0]0→1，立即读取RF93[5]，最多501观察/500次2us延迟，最后清RF92[0]。启用reK且RF92[7:5]>1时，按原机RF8f[11:9]选择值再次触发，随后恢复该字段1。

read_offset@30be50按原机顺序读取0/1/12/13和16/17/28/29增益点的RF92/93[15:9]。check@30bd3c逐项计算I/Q无符号偏移的绝对差；超过9才以对应0/1或16/17参考分量替换12/13或28/29，不超限分量保留其原值。value_rewrite@30b854把I/Q分别右移1、Q放高6位，依次打开RFec[19]、f0[16]、92[1]，选RF33低byte的增益索引、两次写RF3f，再按原机顺序关闭三项控制。随后热检测按get_thermal@30e790对RF42[19]1→0→1、等待200us、取[6:1]；恢复RF5自动，仅原先手动时恢复保存RF0。

原生live模块5abbace9ad67f781e9d03f5bc5cf9a463c736cf7679113fa6de0670740c33ecd只替换C，XB继续内置2.4GHz接收。两路RF测量均35次观察完成，RF95额外reK能力为1、本次结果码未要求第二次测量；温度30/31。八个增益点均有真实I/Q，比较差值均不超过9，本次corrections0。RF92/93两路为68620/79800、48220/67c00。随后频道36再次由mac80211交付32个实际用户态Radiotap帧，全部完整FCS匹配，描述符/tag错误0、kernel抓包丢包0，XEPON公网2/2。证据`manifests/radio-c-rxdck-20261010.json`及对应logs/pcap。增益替换及额外reK的硬件分支本次未触发，代码逐项来自实际原机函数。

后续TXA LCTK也已核对实际模块：trigger@2f52bc仅检查supportability[25]、记录host时间/状态及BTC通知，没有两颗芯片的硬件调用；本机最终C88e17f和XB38e17f均未置bit25。因此不增加自拟TXA LCTK寄存器操作。继续移植原机trim/TSSI和频道功率控制，再接普通TX与MAC角色/关联。

## 2026-10-10 原机thermal/PA/TSSI trim与参考功率

`trim.h`按原机get_total_efuse_C@338810读取物理590..5df共80B、XB@3620b8读取5a0..5df共64B。两颗thermal取A5df/B5dc，将byte低4位中的bit0移到bit3、原bits3:1右移1，写RF43[19:16]；PA bias取A5de/B5db，C按A低nibble→高nibble、B低→高写RF60[15:12]/[19:16]，XB只写低nibble。原机两byte均ff才跳过整对，不逐路替换ff值。

TSSI trim的物理地址顺序由本机函数逐项核对：2G/5G A5d6..5cf、B5ab..5a4；C6G A5ce..5c7、B5a3..59c，各8B，有符号byte保存。原机对各频段的两路共16B一起检查，全ff才整组归零。部分擦除时ff保留signed-1；这与普通TSSI的原机HS输入是不同数据来源。前一次观察误取B组地址而报告全ff；按原机正确分组重新读取后，2G/5G整组并非全ff，A路ff保留signed-1，B路有实际编程值。详见后续地址映射修正记录。硬件TSSI初始化/按频道回填继续移植。

trim模块dcedab8919e784875486751b5e3a3628d8067395569876c9011edb692e029348实机C thermal24/24→code2/2、RF43=25000/25000；PA4f/4f→RF60=4f110/4f110。XB thermal20/24→code0/2、RF43=02180/22180；PAf3/f0→RF60=03010/00010，符合其仅2G nibble的原机动作。两路各32个实际用户态无线帧完整FCS全匹配，kernel dropped0，XEPON公网2/2；记录manifests/radio-trim-20261010.json和对应logs/pcap。

重新逐层核对原机ref_power_to_struct_C@320f18/XB@3485c4发现它不止初始化host对象：两颗均调用rtw_hal_bb_set_tx_pow_ref→halbb_set_tx_pow_ref→MAC与BB实现。RFE54原机reference偏移0、base code33、TSSI16dBm code300、OFDM/CCK reference0；MAC d200[27:19]/[18:10]写两个zero reference。两颗BB函数对上述初值计算powerCW264、TSSI_CW172，组合27bit值02b21000并按5804/5808/7804/7808顺序回填。

`ref-power.h`已在NCTL之后、RCK之前接入该实际硬件动作。模块89f2767d7a8e064b6d1e1cfcd15d283431b3a099572843e8a4c03b7541946d34两颗MAC d200=0、八个BB字段读回均02b21000；随后每路32个实际无线帧完整FCS全匹配，描述符/tag错误0、kernel抓包丢包0，XEPON公网2/2。记录manifests/radio-ref-power-20261010.json。后续普通TSSI读取实际调用的是rtw_hal_flash_get_info，需保留原机HS分组值、gain/CW/BW差异和功率策略，不能用物理trim取代。

## 2026-10-10 XB TSSI频道DE回填与OTP分组复核

`tssi-de.h`接入本机XB set_efuse_to_de@360ab8的normal-type路径，随native频道设置在恢复DMA前执行。CCK按1..2/3..5/6..8/9..11/12..13/14选择六个HS组，OFDM按原机19个2G/5G组选择，组间编码0506等按有符号和除2、向零截断。物理trim使用原机8组及0203等显式插值编码；先匹配范围再判断插值，保留原机存在但被前面范围覆盖的条件。A→B分别计算HS CCK/OFDM值加物理trim，再按5858/5860、5838/5840/5848/5850/5828/5830顺序写10bit[21:12]。硬件tracking、TSSI_CAL模式及C的BW/slope差异输入仍继续接入，当前不声明完整TSSI。

复核地址后纠正了前一轮trim观察：B路2G/5G应读取5ab..5a4，此前观察模块误取5ce..5c7，从而把整组错误报告为全ff。正式trim.h的地址已按原机机器码修正；这次实际读取A路2G/5G为ff（signed-1），B路有实际编程值，如频道6对应trim=-4。因此原机整组16个byte不全ff，不会把A的ff单独改0。先前原始日志保留，manifest和文档标明旧分组错误，正确输入见logs/radio-trim-corrected-map-20261010.txt。

原生模块15bb7ce9cb42e3c97e91405341c487a2b6e35bb0c4695de7617e2e46993a4c93实机频道6 CCK/OFDM DE两路=-1/-1、4/4；频道1=2/1、1/-1；频道11=2/1、3/1。每个频道16个字段读回全部等于原厂算法结果，负值-1实际编码1023。频道6随后32个真实用户态无线帧完整FCS全匹配、descriptor/tag错误0、kernel丢包0，XEPON公网2/2。记录manifests/radio-tssi-de-XB-20261010.json、logs/radio-tssi-de-20261010.txt及pcap。C继续同模块原生接收，但C DE/BW/slope尚未加入频道路径。

## 2026-10-10 最新完整镜像内置驱动的双频接收验收

完整服务ponwrt-tyfza1-lan-20261010-054938成功结束，source2201a0f；实际启动PCIe FIT SHA25632e7fe6dc72f89f385801ce2775267b3d66c931fc0d7a2f7dcaa44d515072679，7322244B，kernel029e2db52f2ef14c8b5d06ca3e332a3211c8f7fb713f703d8adfd00e7fd110c0。U-Boot校验签名后重启到内置rtl_ax_ap，taint0，无live覆盖模块；两路RCK/DACK/RX_DCK/thermal/PA/reference及XB TSSI DE均在该启动内核中。最终FIT里的kernel/DTB/initramfs输入已经核对。

频道36与6各取得128个真实入站Radiotap帧，256帧完整FCS全部匹配、kernel抓包丢包0；C含57RTS、56CTS、13Beacon、2Probe Response，XB含85Beacon、11Data、12Probe Request、10Probe Response、4Null Data、3RTS、3CTS。初次未指定方向的pcap混入Linux本地生成的出站IPv6以太帧，不用于验收；正式pcap明确tcpdump -Q in。日志和manifest为radio-native-tssi-in-builtin-20261010及radio-native-tssi-builtin-20261010。

各阶段读数同时保留：C PHY OFDM CRC_OK17938/错误2，HE正确23/错误1；host RXQ9847、普通802.11 CRC正确9846、交给mac80211 9846，描述符/tag错误0、RDU0。XB PHY CCK正确960/错误10、OFDM正确71/错误32385、HT正确7/错误22；host RXQ957、普通CRC正确956、交给mac80211 953（启动monitor前的3帧未交付），描述符/tag错误0、RDU0。PHY计数会包含被MAC过滤的帧且计数范围不同，不能要求与host计数逐帧相等。XB OFDM接收质量仍需对照原机RFK/动态配置，不将成功抓包等同全部调优完成。

XEPON epon-d10g注册认证、DHCP192.168.71.28与公网ping2/2均恢复。当前原机保持phy0/mon0频道36与phy1/mon1频道6接收；AP发射、关联、加密和实际业务仍待移植。本次只采用内置原生host驱动，WCPU设备微码和原机参数作为硬件输入。
