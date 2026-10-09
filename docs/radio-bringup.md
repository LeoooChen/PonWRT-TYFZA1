# TYFZA1 原生无线驱动进度

2026-10-09继续依据动态无线资料实现，整体顺序见wifi-port-order.md。原生PCIe已进入完整镜像；当前设备离线，PCIe枚举、无线芯片身份和AP收发尚待实机验证。

## 本轮资料与配置结论

最新已读冻结目录evidence/wireless-study-20261009-104746，共502个有效文件、67861334字节。新增固件/RFE、无线软硬件协作、跨设备DMA专题全文阅读；原始资料目录保持只读。check_wireless_study.py默认与manifests/wireless-study-current.json选择的快照比较，后续新增资料在对应阶段先阅读再接入。

原机配置与实际初始化覆盖确认最终RFE54：5GHz/wlan0使用RTK66587_BPF，2.4GHz/wlan1使用RTK66287，FEM参数为0xee。初始rtw_rfe_type=50不是最终配置；PCI device ID、MAC chip枚举、固件头chip字段和host cv分别解释。原机probe请求32bit DMA，SoC coherent ATU高地址别名4与NI使用的别名2分别管理。

## 固件格式与构建输入

原生rtl_ax_ap/fw.h解析8852C与8192XB AP固件的主头、动态头和section。MAC枚举2选择8852C，3选择8192XB；host cv0选择u1，其余选择u2，与原机mac_query_fw_buff一致。当前四份固件版本均0.27.84.3，firmware cut字段1/2与host cv0/1分别记录。

原始主头32字节、四项段头64字节、动态头64字节，累计160字节；段数据使用完整RISC-V地址。下载phase1仅发送主头和段头共96字节，并将分段长度字段改为2020；原始数组保持不变。sections按2020字节拆分，末包使用实际剩余长度，载荷累计与原段完全相同。后续已接入原生PCIe H2C ring及MCU ready控制；mac80211接口继续实现。

tools/prepare_wifi_runtime.py直接使用原生C格式处理，向构建目录安装四份原机AP固件数据与选中FEM目录的20份RF参数。路径为lib/firmware/realtek/tyfza1，固件、RF参数均留有源哈希。没有复制或加载原机主机侧ko。参数目录含6GHz备用表，保留数据不等于启用未确认频段；实际radio能力仍以对应驱动与板级身份决定。

公共port/scripts/build.sh准备这些输入，最终tools/verify_wifi_payload.py从待启动FIT实际kernel/initramfs提取并核对四份固件、20份参数，外部files目录不代替启动载荷。显式记录radio_driver_available=false，尚未标作无线可用。

## 离线验证

四份原机固件各段的地址、offset、长度和SHA256与独立提取清单一致；96字节下载头及2020字节分段重组正确。host cv0..8分别验证18组选择；短主头、动态头长度、段范围和下载输出边界通过ASan/UBSan。结果在logs/radio-firmware-format-test.json。

PCIe修正另有实际host startup、MSI初始化与Linux轮询宏测试，说明BER未就绪继续建链、INTx/MSI位保留以及ATU忙位清零条件。对应修正已提交b7ef9cd，与无线固件协议分开验证。

## 原生 PCI radio 后端

新增独立rtl_ax_ap内核后端，匹配原机的10ec:c852和10ec:0192；读取第一个memory BAR，使用32bit Linux DMA约束。该后端并未给8192XB添加rtw89_8852c的别名，也未加载原机rtk_wifi6.ko。主线rtw89的mac80211数据面仍作为后续复用基础。

pci.c中的probe实际执行芯片识别、AP上电、DDV efuse读取、最终身份解码和request_firmware校验。R_AX_SYS_CHIPINFO=0xfc读到0x52/0x53分别代表8852C/8192XB；0xf1高四位是寄存器初始cv。原机get_fv_info对efuse0x5ea全零或CV高四位为0xf时保留初始cv，其余值覆盖cv。先取得最终cv，再选择u1/u2，不提前缓存固件选择。固件头chip字段分别是0x885202/0x819201，与MAC枚举2/3、硬件ID0x52/0x53分开核对。

power.h按本机AP PCIe流程实现。8192XB包含0x244[25]、0x16[2:3]、0xcc[3]和0x80[3/6]的模拟电源与隔离控制；8852C包含0x218[16]、0x200[15:13]和CMAC1关电。两颗芯片的XTAL0x24[6:4]、GPIO弱上下拉和DMAC0x8400[14]配置分别处理；8192XB还按校准标志、工艺和ADIE位选择SPS电压。本机8852C上电函数没有SDK新版的efuse校准/SPS电压写入分支，原生流程依原机省去该分支。原厂LED1代码计算但丢弃新值，最后只写回原值；原生流程省去这一无寄存器效果的写入。

0x5e9校准标志、0x5ea身份/工艺字段和0x5f0/1 ADIE位于1536字节DDV物理bank，不使用1216字节逻辑映射区作为读取上限，也不误切DAV。efuse读取只发读命令，并配对开关读电源窗口；超时也释放该窗口。ADIE读取及未烧录时的0x6758/0x6816值依据GPL对应芯片分支；本机模块没有独立get_aid_info符号，ADIE查询的GPL验证与本机power/FV验证分别记录。固件缺失、截断或chip/cut不匹配时执行已上电设备的关电流程；remove/shutdown也调用同一路径。

上电准备阶段只验证固件；后续channel12已接入probe，radio_state现在报告最终MCU状态、下载包数/字节、download_memory及mac80211=pending。该只读属性只在probe完整成功后创建，firmware_state=ready要求MCU实际返回初始化完成状态，不从固件文件校验结果推导。RFE54文件已进入载荷，BB/RF表尚未应用，无线接口尚未注册。

## 后端离线验证与构建

scripts/test_radio_stock_power.py用Unicorn在模拟内存中执行本机rtk_wifi6.ko的四个AP上、下电函数和get_fv_info。1024组本机power轨迹逐次匹配MAC/GPIO写入、XTAL操作、poll和8192XB校准读取，8192组本机CV/FV解码与原生实现一致，包含SDK与本机不同的全零FV处理。模拟IO没有访问真实寄存器，也没有加载原机模块；结果在logs/radio-stock-power-test.json。

scripts/test_radio_power.py另编译真实GPL函数，1536组匹配8192XB上电与两芯片下电的完整寄存器/延迟轨迹，覆盖校准、工艺和ADIE配置；8160组非零FV/CV/AID向量对照GPL。8852C上电和全零FV以本机机器码测试为准。每个XTAL握手、MAC power握手及efuse超时退出路径另有ASan/UBSan检查。

scripts/test_radio_probe.py运行实际pci.c控制函数，使用四份完整原机固件数据及模拟MMIO。28组案例覆盖寄存器cv与efuse cv不一致、全零/未烧录FV、选取第一个memory BAR（测试为BAR2）、32bit DMA、原厂chip/cut匹配、输入错误后的关电和shutdown/remove配合。测试通过ASan/UBSan；设备枚举和无线收发仍待实测。报告位于logs/radio-power-test.json及logs/radio-probe-test.json。

201-native-radio.patch从锁定Linux6.18.55原始源码生成，patch --fuzz=0 --dry-run通过。CONFIG_RTL_AX_AP已接入目标配置并完成Kconfig闭包。首次ARM64编译发现sysfs属性数组与ATTRIBUTE_GROUPS宏命名不一致，已修正；接着按本机机器码落实上述SDK差异。完整镜像artifacts/full-pon-radio-power03-6.18.55已由systemd用户服务ponwrt-tyfza1-lan-20261009-115208构建成功，退出码0。最终ram-pcie/image.itb为6500696字节，SHA256=bab2cd618002b9f0c2dc324d0e012a0c78b275a3bd8c3fb975bd616eae43174f；从实际选中FIT内核核对到原生backend，并验证其中1026项initramfs、四份固件和20份RF参数。此镜像尚未上板。

## 原生下载前 DLE/HFC 准备

download.h接入PCI probe，在固件身份/格式校验后准备下载专用DMAC、DLE和HFC；radio_state包含download_memory及hci状态。主机bus mastering保持关闭，HCI TX/RX和所有TX channel暂停，直到channel12 host ring完成初始化。失败退出撤销DLE使能，remove/shutdown关闭下载资源。

两颗芯片的本机PCIe DLFW表相同：WDE64字节页、0 linked/2048 unlinked；PLE128字节页、2544 linked/16 unlinked，总计458752字节。WDE下载最大配额为零，最小WCPU配额从选中的运行配置复制：本机SCC表是60，STF表是20，二者不能混用。当前原生入口按SCC选择60。PLE C2H最小16/最大32、H2C最小和最大均256；其余下载通道配额为零。只在WDE与PLE状态寄存器的queue/buffer ready位都置位后继续。

SDK中的hfc_preccfg_pcie_dlfw表不能直接当作本机下载写入值：本机hfc_reset_param(DLFW)只复位部分软件状态，保留已分配precost结构，实际set_fc_h2c使用保留的值。原生冷启动状态明确为零；后续运行态将另行配置。H2C的256页PLE配额与HFC pre-cost是不同字段，不将它们混为一项。

scripts/test_radio_download_memory.py直接解开本机ELF的两份PCIe表及其重定位，并执行真实DMAC预启用、DLE布局/配额和HFC寄存器函数。768组写入轨迹与原生实现一致；另用H2C pre-cost为0/40/256的六组初始状态执行真实hfc_reset_param，验证DLFW保留行为。结果在logs/radio-download-memory-test.json。PCI probe已扩展到30组，覆盖WDE/PLE ready超时后DLE关闭与HCI暂停；ASan/UBSan通过。

## 固件 transport 的实现顺序

原厂fwdl_phase0等待WCPU_FW_CTRL=0x1e0的H2C_PATH_RDY位1；发送带H2C头的96字节固件头后，phase1等待同寄存器FWDL_PATH_RDY位2，再清HALT H2C/C2H控制。phase2按2020字节分段发送裸固件段的FWDL类型描述符，随后检查0x1e0[7:5]等于7；checksum、security和cut错误是不同终止状态。第一阶段带H2C command header，段数据没有同样的H2C header。transport.h已实现这三阶段，错误分别返回EBADMSG、EKEYREJECTED和ENOEXEC。

原厂fwdl_precheck核对PLE PAXIDMA队列0/1和HAXIDMA channel12是否空；原生实现通过DFI target7和TXBD index执行对应检查后才启用WCPU。Linux rtw89的8852C使用16字节fwcmd描述符、32bit TXBD和同一channel12思路；本后端使用Linux DMA API并按两颗芯片原机包格式实现。常规RX/TX、C2H/IRQ、BB/RF以及完整AP接口仍继续落实。

## Channel12 下载队列及 WCPU 控制

PCI probe在最终chip/cut/固件校验之后，先执行本机CPU-off流程：关闭WCPU、下载标志、CPU clock，并按chk_patch_pcie_hang覆盖两芯片的原机行为，切换PLATFORM_EN。这个复位在DLE及host ring初始化之前完成。启用时重新清debug/错误状态、配置下载标志及power-on boot reason，核对空队列/空index，再启用WCPU并等待H2C ready。

本阶段分配256项8字节TXBD及2048字节coherent packet，均归PCI设备的32bit DMA地址域。配置0x1280/1284 descriptor地址、0x1038项数和0x1328的原机BDRAM参数{60,4,1}；数量/BDRAM字段用对应掩码更新。清channel12 index并等待消费/生产指针为零，等待BDRAM reset位自行清零。host ring就绪后仅开放channel12 TX，RX和普通TX仍暂停；本阶段没有常规RX ring。

固件包使用16字节短RX格式WD。第一包为WD type13 + 8字节H2C头 + 96字节固件头，H2C type0/MAC category1/class3/function0；段包为WD type14 + 原始段字节，最长2020字节。TXBD长度包含WD，其LS为bit30，低32位填写PCI DMA地址。dma_wmb在发布生产指针前执行；硬件消费指针完成后dma_rmb配对完成读取。2026-10-09进一步追清本机txbd_upd_lmt=1及八项H2C work ring，现已纠正消费后立即复用单packet的偏差，改为DATA/LONG_DATA每类八项保留加一项可用的独立pool。下载成功后暂停HCI并关闭bus mastering；remove/shutdown停止WCPU、关闭下载资源并释放host缓冲。该回收修正尚未生成新的完整FIT。

MCU状态等待使用当前Linux6.18.55的readb_poll_timeout按elapsed time计时，400ms超时；状态2/3/4/7立即结束等待，再映射对应错误/成功。没有将400000次可睡眠轮询当作400ms。512组本机mac_disable_cpu/mac_enable_cpu寄存器轨迹与原生实现一致。实际Linux轮询宏另用100ms调度睡眠验证，仍按400ms边界结束；四个终止状态均有延迟出现测试。结果在logs/radio-WCPU-control-test.json。

scripts/test_radio_transport_format.py执行本机两芯片mac_txdesc_len/txdes_proc_h2c_fwdl及h2c_pkt_set_hdr_fwdl机器码，8080组WD/H2C包逐字节匹配。PCI控制函数测试扩展为43组，四份完整固件的每个段包对应原始offset/长度/字节，另外600次发送验证生产指针绕过256项边界。DMA分配、idle/index/BDRAM超时、队列不空、H2C/FWDL/消费指针超时、MCU校验/安全/cut错误及ready超时均退出并释放对象，ASan/UBSan通过。

artifacts/full-pon-radio-transport02-6.18.55已由systemd用户服务ponwrt-tyfza1-lan-20261009-130452完成构建，退出码0。最终FIT SHA256=2d1c5f58d41a88c62789046f54e182517a5fd19bcecaf19341e25c12ad238cbb。已提取实际选中FIT内核，逐字节核对probe/H2C发送/MCU等待/shutdown四个函数与同次编译链接ELF中的机器码，并验证四份固件/20份RF参数。构建入口也已接入verify_wifi_payload.py --kernel-elf，报告保存在产物目录。所有MCU ready和DMA完成验证目前是模拟硬件；两颗芯片实际枚举、下载成功、常规收发和AP都尚未上板验证。

## 下一步

2026-10-09用户纠正工作重点后，先完整贯通原厂初始化、配置覆盖、收发和完成回收，再按确认的原机行为实现。新的RX试验已存入Git stash，当前主线保留9b76b2e的下载后端；RX试验不作为候选固件或完成证据。具体缺口和原机调用/ops表记录在docs/wifi-stock-lifecycle.md。后续仍完成下载后MAC配置、常规RX/TX与IRQ/C2H、RFE54/FEM参数和BB/RF校准，再接mac80211/cfg80211、双频AP、STA/密钥生命周期及OpenWrt配置；之后接无线NE及原生UBI交付。
