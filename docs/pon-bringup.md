# TYFZA1 原生 PON 移植

2026-10-08，原生SerDes、gearbox、EPON MAC/PCS/FEC初始化已在TYFZA1实机验证，并由完整OpenWrt main r0-79bc56c / Linux6.18.55 / GCC14.4.0的S25pon服务自动执行。最新RAM载荷为artifacts/full-pon-auto-init-6.18.55/ram-pon/image.itb，SHA256=37c59a602af4f01d3eced6d62b14e9beb99d6928d2cbf8f710b6d3ea3b7eb117。

## 已移植与实机结果

rtl9617c-pon-serdes.c用Linux firmware API加载本机SerDes参数，替换原厂内核内的文本解析、OSAL延时和轮询。已实现模拟复位、CSR释放、模式设置、burst极性、LOS极性、参数加载、时钟同步检查和gearbox复位；Linux delayed_work读取同步状态。

非对称10G EPON的320条参数在实机执行后，PSDS状态0x4001→0x5c01，RX/TX/MPCP时钟均0→1。模拟寄存器0420/0424/0429/0780/0796/079f读回与原厂表一致；1G、非对称10G、对称10G模式寄存器分别为040c/044c/046c，均完成参数加载及时钟初始化。当前LOS=1、BER_NOTIFY=0、MPCP注册位图0；这些模式切换测试证明初始化和寄存器行为。

rtl9617c-pon-epon.c已移植CSR复位、MAC/PCS控制、正常burst配置、帧大小/控制帧过滤、RTT补偿、统计RAM初始化和FEC配置。非对称10G的实读包括EPON_CNTL=000200ef、PCS_RX=5000、PCS_TX=03020000、XG_FEC=20、MIB_RAM_STATUS=1，配置错误0。

OpenWrt使用/etc/config/pon选择模式；S25pon在启动时加载SerDes并初始化MAC/PCS/FEC，配置变更可reload。完整主线实机于13.9s完成SerDes、14.3s完成MAC初始化。PON初始化后100000个UDP包零丢失/重复/错包，16MiB双向TCP内容一致、TCP校验和错误0、LAN接收错误/丢弃0；DHCP及LuCI登录正常。

结构化证据在manifests/pon-tests.json，原始状态和传输记录在logs/serdes*、logs/epon01-*和logs/pon-auto-*。

## 资料和板级资源

本机原厂配置选择EPON/非对称10G，LLID模式single、数量1、广播映射索引63、report模式0、laser on/off各32TQ。

PON MAC资源4f5500000/0xc000来自原厂DTB的NI resource8。新增原生DTS分别映射GLOBAL_PON_CNTL、GLOBAL_EPON_CNTL、GLOBAL_PSDS_INIT_CNTL及光模块控制/状态寄存器；verify_dtb.py检查其与原厂DTB及寄存器定义的对应关系。

参数来自原厂/etc/serdes/pon_serdes_*.txt，12组完整保留。目标ca-ne.ko的机器码确认最多512条、地址低16位映射PON窗口、延时0采用10us。tools/prepare_pon_serdes.py生成Linux固件记录：little-endian TPON magic、u32版本1、u32行数、每行u32偏移/值/延时us；构建脚本将记录装入/lib/firmware/realtek/rtl9617c。哈希见manifests/pon-serdes-profiles.json。

寄存器序列参考GPL aal_psds.c、aal_epon_mac.c、aal_epon_fec.c，同时核对本机ca-ne.ko机器码。原生代码使用Linux platform_driver、mutex、firmware API、delayed_work和内核I/O接口。

## 下一步

接入MPCP参数、ONU MAC及LLID注册事件，移植OAM收发和认证；配置PUC/PDC队列并将PON接口接入Linux网络栈。MAC收发使能与业务队列一起接入，完成注册后验证PON业务。随后按既定顺序继续Wi-Fi和硬件加速，并完成原生UBI安装/升级/恢复流程。

原生MPCP控制已在实机配置，ONU身份来自原机EPON_LLID_TBL第0项；Linux delayed_work与threaded IRQ接管注册轮询、状态事件和NACK静默期。NE IRQ拓扑匹配原机：GIC SPI1 → NE bank bit5 → PON MAC/MPCP。实机LLID间接接口成功添加、读回和删除index62/mac31/LLID1234；这项只验证硬件表操作。1/4/32项配置切换分别得到waiting=1/f/ffffffff、SYNCTIME=f820/e020/0020，回到原厂单LLID配置后LAN错误/丢弃仍0。LOS1条件下注册尝试保持0。

原生rtl9617c-pon-pdc.c已设置PDC控制及256项下行映射，通过硬件间接接口抽样读回index0/31/63/64/127/192/255。数据目标CPU16、PON源7、FE bypass=1，MPCP/OAM优先级7，控制寄存器7。重复初始化及与MPCP同时配置均成功；此项证明硬件映射，光口下行报文仍待PCS同步。NI接收已按原厂从COS7到COS0顺序处理，以优先接收PON控制帧。
