# 原生 PON 数据通路接口

依据本机使用的GPL ca-network-engine和RTL8277C SDK，PON业务和OAM共享NI DMA/QM硬件。接入Linux网络栈时需要按Header A的lspid分流，PON入口源端口为7，LAN为0。CPU0的8个COS环由同一个NI NAPI处理，PDC当前将PON下行转发到CPU16。

## Header A

参考ca-network-engine/ni-drv-77c/ca_ni_titan.h的HEADER_A_T：低32位为COS[2:0]、LDPID[8:3]、LSPID[14:9]、长度[28:15]、FE bypass[29]、header type[31:30]；高32位包含no_drop[13]、deep_q[30]、cpu_flg[31]。接收RAM的两字使用网络字节序，高字在先、低字在后，本机LAN实测与rtl9617c-qm.c一致。DMA-LSO发送缓冲的LSO两个字之后使用little-endian高字、低字。

LAN指定LDPID0、LSPID16、bypass=1，DMA VP8。原生NI现已提供每包Header A和前缀元数据，并由NI统一管理DMA/QM启停。每个DMA描述符记录发送接口，完成统计计入对应netdev；接口关闭时清除描述符中的接口引用，DMA缓冲仍由NI持有。接收通过Header A的LSPID分流，PON源7或32..63交给PON端点，LAN源0交给eth0。

原厂aal_port.c将普通LDPID7映射到blackhole。业务上行需要使用L3QM的PON深队列，而OAM走LDPID15/PDPID12。aal_l3qm.c的upper LDPID map控制PON偏移；dal_rtl8277c_ponmac.c的背压重映射将PUC队列0..127对应到NI队列64..191。下一步业务队列应沿此路径移植。

## OAM封装

依据gp3000/linux-5.10.x/drivers/net/ethernet/realtek/rtl86900/sdk/src/dal/rtl8277c/dal_rt_rtl8277c_epon.c：

- dal_rt_rtl8277c_epon_oam_tx将OAM以太帧填充到至少60字节，再添加16字节内部封装：DA=00:13:25:00:00:LLID，SA=00:13:25:00:00:01，EtherType=fff1，最后2字节为0。Header A取COS7、LDPID15、LSPID17、FE bypass=1、no_drop=1。__rtk_ni_start_xmit最后一个参数1为oneDesc，DMA VP由当前CPU选择：CPU0→VP8，CPU1..3→VP1..3。
- _oam_receive_from_nic在NI接收路径识别内部DA=00:13:25:00:00:01和EtherType=fff1，也支持内部单VLAN封装。LLID取内部源MAC末字节低6位，移除16或20字节，再检查实际OAM帧EtherType=8809、subtype=03。
- 原厂将广播LLID索引8/10/11/63交给LLID0；其他索引减去MPCP软件索引偏移，原生单LLID配置当前偏移0。

PUC初始化明确写入GLOBAL_DA_SA0/1/2、GLOBAL_MAC_TYPE和GLOBAL_LNK_TYPE。NI配置LDPID15→PDPID12和port12调度；rtl9617c-pon-oam.c创建pon-oam0，将原始8809/subtype03帧加上内部封装后交给共享VP8。LLID发送索引使用SO_PRIORITY高16位；接收支持内部普通和单VLAN容器，并保留SDK的广播索引归一化。

S25pon在SerDes/MAC/PUC/PDC初始化后打开pon-oam0，启用当前模式的MAC收发，再启动MPCP。非对称10G实机读回RX_XG=0d481290、TX_1G=0001c001。carrier由LOS、PCS同步和注册位图决定，当前LOS1时为0。

## 接入顺序与验证

已通过scripts/test_pon_oam_format.py的SDK容器字节、RX/VLAN解析、其他subtype及截断帧测试（ASan/UBSan）。实机验证了共享NI生命周期：仅关闭LAN时DMA/QM继续运行；两个接口均关闭后DMA/QM停止；先打开OAM时硬件重新初始化，随后LAN恢复千兆链路。原始记录在logs/serial-20261008-071004.txt和logs/oam01-*。

tools/pon_oam_probe.c可向控制接口提交原始OAM Information PDU。当前LOS1时AF_PACKET接受了10个提交，OAM TX完成计数仍0；这项只验证用户态接口。原生OAM发现、扩展TLV和认证守护进程、业务PON netdev和L3QM深队列继续移植。光口注册、OAM往返及业务仍需实际光路结果。
