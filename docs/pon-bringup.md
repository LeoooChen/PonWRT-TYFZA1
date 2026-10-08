# TYFZA1 原生 PON 移植

截至2026-10-08，TYFZA1的原生PON驱动已包含SerDes、gearbox、EPON MAC/PCS/FEC、MPCP注册控制、PDC下行映射、PUC上行队列和原生OAM控制网络接口。代码位于port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek，使用Linux platform_driver、firmware API、mutex、delayed_work及threaded IRQ。

当前光口LOS=1、PCS同步=0、注册LLID位图=0。实机验证覆盖初始化、寄存器/间接表操作及LAN回归；PON注册和光口业务尚待验证。结构化证据和载荷哈希见manifests/pon-tests.json。

## 板级资源与参数

PON MAC窗口4f5500000/0xc000来自原厂DTB的NI resource8。DTS另映射GLOBAL_PON_CNTL、GLOBAL_EPON_CNTL、GLOBAL_PSDS_INIT_CNTL、光模块控制/状态和顶层IRQ；NE IRQ拓扑为GIC SPI1 → NE bank bit5 → PON MAC/MPCP。PON通过realtek,network-engine引用原生NI，由NI驱动配置PUC报告阈值。port/tools/verify_dtb.py核对窗口、IRQ和supplier绑定。

原厂配置为非对称10G EPON，LLID模式single、数量1、广播映射索引63、report模式0、laser on/off各32TQ。tools/prepare_pon_identity.py从备份EPON_LLID_TBL第0项提取ONU MAC，生成镜像内/etc/config/pon。

原厂/etc/serdes/pon_serdes_*.txt的12组参数完整保留。tools/prepare_pon_serdes.py生成little-endian TPON记录：magic、版本1、行数及偏移/值/延时us；原厂延时0按10us执行。本机ca-ne.ko反汇编确认原加载器最多512条，地址低16位映射PON窗口。记录装入/lib/firmware/realtek/rtl9617c，哈希见manifests/pon-serdes-profiles.json。

## SerDes、MAC与注册控制

rtl9617c-pon-serdes.c实现模拟复位、CSR释放、模式选择、burst/LOS极性、参数加载、时钟同步检查和gearbox复位。非对称10G的320条参数实机执行后，PSDS状态0x4001→0x5c01，RX/TX/MPCP时钟均0→1。模拟寄存器0420/0424/0429/0780/0796/079f读回与原厂表一致；1G、非对称10G、对称10G均完成参数加载，模式值分别为040c/044c/046c。

rtl9617c-pon-epon.c实现MAC CSR复位、PCS、FEC、burst、帧大小/控制帧过滤、RTT补偿及统计RAM初始化。非对称10G实读包括EPON_CNTL=000200ef、PCS_RX=5000、PCS_TX=03020000、XG_FEC=20、MIB_RAM_STATUS=1。

rtl9617c-pon-mpcp.c配置ONU MAC、1..32个LLID及MPCP定时参数，处理注册轮询、REGISTER帧、NACK静默期、注册变化和中断。实机间接接口成功添加、读回、删除index62/mac31/LLID1234；1/4/32项配置的waiting位图分别为1/f/ffffffff。当前LOS1时注册尝试保持0。

## PDC与PUC

rtl9617c-pon-pdc.c配置256项下行映射，目标CPU16、源PON7、FE bypass=1，控制报文COS7。实机抽样读回index0/31/63/64/127/192/255正确；重复配置及与MPCP同时初始化通过。NI按COS7→COS0处理CPU接收环。

rtl9617c-pon-puc.c配置64项PVT、256项CIR/PIR、VOQ有效位图、缓冲限制、report queue set、上行调度与NI报告阈值。1..16 LLID配置时每项使用8队列，17..32项时使用4队列；未使用的PVT项清零，保留原厂特殊控制队列127。

快速镜像ram-puc-01在实机验证1、4、32 LLID队列模式及映射，并恢复单LLID。ram-puc-02进一步读回NI报告阈值00370052（82/220）和CIR/PIR抽样0/7/127/255；CIR配置为rate=0、burst=0、enb=2，PIR为K=3ff、M=ffff、burst=fff、enb=2。DATA1的29:9是硬件动态token计数，会随运行变化。端口0的DATA0..4为1c0a0300/c1a0b048/00001083/0/0，解码为队列0..7全部使能；端口1为0。缓冲限制7a0/3b0/200，report阈值ffff8000/80008000，错误码0。证据在logs/puc01-*和logs/puc02-*。

S25pon启动顺序为SerDes → ONU身份/LLID数量 → MAC/PCS/FEC → PUC → PDC → OAM接口/模式对应的MAC收发使能 → MPCP，支持/etc/init.d/pon reload。

## 主线验证与后续工作

完整OpenWrt main r0-79bc56c / Linux6.18.55 / GCC14.4 / musl的MPCP/PDC自动启动已通过实机测试。初始化后100000个UDP包零丢失/重复/错包，16MiB双向TCP内容一致，LAN接收错误/丢弃0；DHCP和LuCI登录/Overview正常。载荷为artifacts/full-pon-mpcp-pdc-6.18.55/ram-pon/image.itb，SHA256=fe556c4135ca2e42858496faad1c10c91b5be51715b6444c05c0711d0c9eefc2。

PUC完整主线自动启动已在实机验证，14.75s完成PUC、14.77s完成MPCP，reload通过；100000个UDP包零丢失/重复/错包，16MiB双向TCP sha256与cmp一致，TCP校验和错误和LAN接收错误/丢弃均0。en5已恢复DHCP并收到192.168.1.170，LuCI登录302、Overview200。新载荷artifacts/full-pon-puc02-6.18.55/ram-pon/image.itb，SHA256=810465cb518aa24a38723a54bd30fc2bfd817e8dc8177315b8f464030451185b。原生pon-oam0、NI源端口分流、每包发送元数据和OAM内部封装已接入，详见docs/pon-data-path.md。共享NI启停已在板上验证；1G、非对称10G和对称10G的模式对应MAC使能读回正确。原生OAM控制接口打开后，完整主线100000个UDP包零丢失/重复/错包，16MiB双向TCP内容一致，LAN错误/丢弃0。下一步接入业务PON netdev/L3QM深队列及原生OAM发现、认证和业务配置，完成光路注册与端到端业务测试。随后继续Wi-Fi、硬件加速及原生UBI安装/升级/恢复流程。

## 离线光前端与多协议工作（2026-10-08）

原生BIW I2C、pinctrl和BOSA校准已接入。5个Semtech型号的原厂初始化函数与原生序列共10组逐操作对照通过；板级识别及光口验收待设备恢复连接。完整记录见optical-front-end.md。

六种手动模式的SerDes入口和原厂参数已接入，GPON族MAC/PLOAM/OMCI和业务继续移植，当前阶段见protocol-matrix.md。自动识别按用户要求暂不实现。LuCI网络→PON管理增加身份、版本和WAN参数，同时读取内核实态；新增字段的管理协议消费者继续开发。参考pon-fw-r1的经验及落点见pon-reference-lessons.md。
