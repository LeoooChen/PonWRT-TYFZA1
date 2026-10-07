# 原生 NI/DMA 移植

2026-10-08，接续已通过实机的 XRAM 管理 LAN。

TX 增加 rtl9617c-dma.c：Linux DMA API分配环和数据缓冲，VP8/TXQ0，64个描述符。原机scfg启用DMA_LSO_ACE_TEST，环和包使用CCI高位2；LSO参数和Header A按本机模块及GPL参考组织。描述符经dma_wmb发布，读指针回收。

实际结果：DMA读指针正常推进；关闭NI管理端口选择（port_to_cpu=15），启用普通L2TM/LAN调度，并改用LSO+Header A后，Mac抓包看到光猫的ARP报文。此记录证明DMA发送到网线，尚不证明完整NI双向业务。

RX 增加 rtl9617c-qm.c：CPU0/EQ4、8个1024-byte EPP64环、256个2048-byte缓冲。原生pool、refill、NAPI与Header A/CPU解析已实现。入口TCAM分类已命中（monitor=0x04001040），继续连接硬件CPU队列。端口映射明确区分：AAL_PPORT_CPU=9，AAL_PPORT_QM=8；后者是deep-Q接口。

增量载荷保存在artifacts/ram-dma-tx-*和artifacts/ram-ni-*，每份含Image/DTB/FIT哈希。实机状态见manifests/ni-tests.json。已有XRAM/LuCI载荷仍可通过本机U-Boot TFTP RAM启动。

NI RX旁路字段定向测试成功：RX_CNTRL_CFG=0x98100400（CPU16、旁路使能），L3QM产生实际EPP64描述符，HeaderA=0x80002000/0x00350080，对应CPU头和实际以太帧。1000个MTU1500的IPv4 ping全部收到回复；16MiB随机内容双向TCP传输SHA256相同，设备cmp=0。发送至Mac耗时0.397s，反向耗时64.509s；反向性能仍需要改进。

现已将CPU旁路作为本阶段的软件转发接收配置，并接入CPU0 GIC SPI84中断。原厂描述符回收逻辑保留两个预取位置，当前驱动也补齐此距离。中断及改动后的性能测试分别记录。

ni-14 中断接收累计45591次，16MiB接收47.604s，设备SHA256正确，但TCP累计158个校验和错误。ni-15补入GPL参考的AXI reorder读/写ID映射后，16MiB接收76.462s、224个校验和错误，故该初始化没有解决接收完整性问题。

tools/lan_capture.c在实机AF_PACKET入口抓包；tools/analyze_lan_capture.py核验IPv4/TCP校验和并与固定随机数据比对。ni-15的一帧1514-byte报文，IP头校验正确，TCP数据前590bytes正确，后部来自固定数据的下一段TCP内容，明确存在接收缓冲内容交叉。原始pcap与分析保存在artifacts/lan-tcp/ni15-rx.pcap、logs/ni15-pcap-analysis.json。下一项对照使用普通AXI地址/属性，匹配Linux非一致性设备的dma_alloc_coherent内存映射。

普通AXI地址对照ni-16仍出现435个TCP校验和错误。EPP深度对照ni-18将profile64改为32，产生15178个驱动rx_errors；实机证明64位EPP仍按16-byte单位设定容量，1024-byte环需要profile64。已恢复原值并改为从环字节数/16计算。未把该对照镜像当作可用版本。ni-17继续验证CCI属性配合Linux dma-coherent映射。

ni-17加入dma-coherent后，16MiB接收67.464s，TCP校验和错误降至5，驱动rx_errors为8。ni-19进一步在EPP读指针提交之后归还缓冲，且用完整内存屏障覆盖CPU读取，16MiB接收55.216s、TCP校验和错误3。Mac en5专用抓包ni19-en5.pcap确认设备发出的ACK校验和无错误，但重复ACK与源端重传很多；入口no-drop字段继续做单项对照。

完整主线GCC14.4.0/LuCI/NI镜像503002c64dc2eb021a3397836b1683e41b7c9046f820ed4b073d75cbf7cc4388在实机启动，LuCI空密码登录返回302、登录后Overview返回200。带序号UDP证明接收路径在1000pps产生3次重复/3个缺号，在10000pps产生5019次重复/5019个缺号，接收总数都是10000、正文一致、UDP校验和错误0。由此把丢失/重复问题进一步定位到EPP命令或缓冲可见性，不归因于TCP机制。下一对照恢复原厂ES_CTRL读改写保留的cpuepp_wait_timer_max=1。

ni-21的EPP等待字段1/2/3在10000pps对照中均产生约一半重复包，因此等待字段未解决原因。当前实现的EQ profile把主池和备用池都选为EQ4，原厂为独立池。ni-22改为独立EQ4/EQ5，分别256个2KiB缓冲，BID范围0..255和256..511，回收使用EPP中实际EQID。

ni-22双池、ni-23释放BID计数检查/批量归还均未消除UDP重复；CPU0固定接收程序也复现。当前ni-24修正OpenWrt preinit后重开：先将各VOQ读指针推进到保留的硬件写指针，再重新配置缓冲池；避免把重开前的EPP位置当作新队列。可缓存/不可缓存两份DTB使用同一内核Image作映射对照。

ni-24实机重开后的rx_errors为0，修正了原来每次启动约8个无效EPP；10000pps仍重复约半数。使用同一个Image而只移除dma-coherent的ni-24-nc，也复现重复，排除了单纯CPU缓存映射这一项。下一组用XRAM管理通路作为独立对照，并继续核验QM CRC/低延迟配置。

已解决接收根因：ni-26跟踪中不同EPP位置反复指向同一地址，ni-27硬件BID表/monitor证明池链表计数异常。ni-28补入原厂Linux启动时的TQM复位，并清空未使用EQ、EQ_CFG_LOAD全16项重载后，10000包全部收到且地址各异。突发流量进一步触发EPP满环：读写指针相等且EPP_STATUS0仍置位。ni-29将其作为满环处理后，100000包/10000pps全部收到、零重复/错包/丢失；16MiB接收0.289s、发送0.368s，SHA256一致、cmp0、TCP校验和错误0、驱动rx_errors/rx_dropped均0。
