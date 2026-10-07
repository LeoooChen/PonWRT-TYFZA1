# 原生 PON 数据通路接口

依据本机使用的GPL ca-network-engine和RTL8277C SDK，PON业务和OAM共享NI DMA/QM硬件。接入Linux网络栈时需要按Header A的lspid分流，PON入口源端口为7，LAN为0。CPU0的8个COS环由同一个NI NAPI处理，PDC当前将PON下行转发到CPU16。

## Header A

参考ca-network-engine/ni-drv-77c/ca_ni_titan.h的HEADER_A_T：低32位为COS[2:0]、LDPID[8:3]、LSPID[14:9]、长度[28:15]、FE bypass[29]、header type[31:30]；高32位包含no_drop[13]、deep_q[30]、cpu_flg[31]。接收RAM的两字使用网络字节序，高字在先、低字在后，本机LAN实测与rtl9617c-qm.c一致。DMA-LSO发送缓冲的LSO两个字之后使用little-endian高字、低字。

LAN当前指定LDPID0、LSPID16、bypass=1，DMA VP8。PON需要独立发送元数据，并让共享硬件所有者管理NI全局DMA/QM启停；接口停止时需要保持其他接口的DMA和CPU环运行。

## OAM封装

依据gp3000/linux-5.10.x/drivers/net/ethernet/realtek/rtl86900/sdk/src/dal/rtl8277c/dal_rt_rtl8277c_epon.c：

- dal_rt_rtl8277c_epon_oam_tx将OAM以太帧填充到至少60字节，再添加16字节内部封装：DA=00:13:25:00:00:LLID，SA=00:13:25:00:00:01，EtherType=fff1，最后2字节为0。Header A取COS7、LDPID15、LSPID17、FE bypass=1、no_drop=1。__rtk_ni_start_xmit最后一个参数1为oneDesc，DMA VP由当前CPU选择：CPU0→VP8，CPU1..3→VP1..3。
- _oam_receive_from_nic在NI接收路径识别内部DA=00:13:25:00:00:01和EtherType=fff1，也支持内部单VLAN封装。LLID取内部源MAC末字节低6位，移除16或20字节，再检查实际OAM帧EtherType=8809、subtype=03。
- 原厂将广播LLID索引8/10/11/63交给LLID0；其他索引减去MPCP软件索引偏移，原生单LLID配置当前偏移0。

PUC的GLOBAL_DA_SA0/1/2和GLOBAL_LNK_TYPE提供对应内部地址、协议类型。原生初始化需要明确配置并读回，同时核对Header A处理和内部帧剥离。

## 接入顺序与验证

先实现NI按Header A源端口选择Linux接收接口，保持LAN回归；再接入PON/OAM发送、内部封装、每LLID队列和carrier事件。PON netdev的carrier来自真实PCS同步与LLID注册。接入后用AF_PACKET收发原始8809 OAM帧，原生用户态实现发现、扩展TLV和本机认证协议。

离线验证覆盖源端口分流、封装往返、VLAN及LLID解码；实机验证覆盖CPU/QM/DMA表和LAN传输。光口注册与业务结果由真实光路收发、OLT注册状态、上下行业务及内容校验给出。当前LOS=1，尚无这项结果。
