# TYFZA1 XEPON 实机注册与联网

2026-10-08，在上联光口连接运营商线路，手动选择 epon-d10g（10G 下行、1G 上行）。原机 ONU MAC 用于 MPCP；LOID 从私有配置注入，口令为空，WAN 使用无标签 DHCP。

## 本轮结果

完整 FIT full-pon-live-fec02-6.18.55 通过原厂 U-Boot 的配置及子镜像签名检查。启动约16秒完成 MPCP 注册，CTC 扩展发现稳定，LOID 认证 accepted；OLT 的 07/013A=2 写入上行 FEC 后，编码器及 PUC 的载荷、REPORT 计费三处状态一致。PON IRQ19 为正常注册事件计数，未再出现 nobody cared 中断风暴。

随后在 RAM 中加载本轮下行修复模块。pon-data0 获得 192.168.71.28/24，网关192.168.71.1、租约86400秒及运营商 DNS；实际业务收发计数增长、错误/丢弃为0。网关及公网1.1.1.1分别5个ping零丢包，DNS解析成功；校正开发机时钟后，HTTPS证书验证和OpenWrt下载站9097字节页面下载成功。Mac以en5上的192.168.1.170为源，经光猫LAN/软件NAT/PON访问1.1.1.1，5个ping零丢包；fw3实际规则包含zone_wan_postrouting的MASQUERADE。

上述联网结果包含RAM中替换的PON模块，不能归到原始FIT本身。full-pon-downstream01-6.18.55正在后台整合PDC/POL_ID/NI接收修复，完整镜像的再次启动结果另记录到构建与板测清单。

## 从板测定位的缺口

- 原厂ca-ne.ko的光发送关闭引脚为GPIO15。新增五bank GPIO驱动及pinctrl请求路径，设备树绑定tx-disable-gpios；光前端与模式配置时关闭发送，队列/控制路径准备完成后恢复。GN28L97实际识别为型号205、EEPROM=0，本机599次校准写入已成功。
- 原厂遗留的光状态叶中断没有被原MPCP处理器确认。初始化接管PON顶层与光状态中断，处理并确认光状态变化，消除带光纤启动的IRQ风暴。
- 原厂DAL的最后一步同时启用PUC两个调度器。只启用第一个时，OAM发送统计增长但OLT不稳定；补齐后发现、注册与认证稳定。
- OLT发送含五个版本的CTC FE Information，并在认证后只改变Local revision。解析完整版本列表；在对端仍稳定且准确回抄本端Local时保留会话。Remote回抄丢失、实际Local属性变化和有效报文超时仍撤销认证。
- CTC FEC Get/Set读取和修改原生内核状态，成功写入并读回才返回80；无后端、写入失败、读回不符返回87，非法值返回86。
- LDPID32..63须映射到NI的QM物理目的8，四组my-MAC/deep-Q标志全部初始化。
- PDC以32+LLID作为下行来源时，真实DHCP Offer出现在eth0。将PDC来源恢复为原厂物理PON端口7；关闭policer时使用POL_ID携带LLID，NI把该流元数据传给PON接收端点。普通Ethernet/VLAN按流归入pon-dataN，OAM按原控制容器的LLID归入pon-oamN。源字段归一的具体硬件级原因继续研究；抓包证明原错误分流，修复后真实租约与联网通过。
- pon-dataN允许标准网络设备MAC设置；后续ONU MAC更新保留已由网络配置设置的业务MAC。OAM MAC仍由ONU身份派生。原厂正常DHCP业务的MAC为ONU末字节+2，但本轮使用ONU MAC同样获得了真实租约。

## 证据与可复现入口

- logs/serial-20261008-202000.txt：U-Boot完整传输、签名验证、GPIO/光前端/注册启动。
- logs/live-downstream01-device.txt：修复后注册/认证、DHCP租约、收发计数和PDC表。
- logs/live-downstream01-internet.txt：网关/公网ping与DNS；其中首次HTTPS因开发系统时间为1970失败，后续验证记录在pon-xepon-live-tests.json。
- logs/live-downstream01-lan-nat-ping.txt：实际en5经LAN和PON联网。
- analysis/private/live-oam/lan-data-route.pcap：修复前eth0收到面向ONU的DHCP Offer。认证抓包均保留在私有目录。
- scripts/test_pon_fec.c执行真实CTC解析器、参数后台与FEC寄存器序列；ASan/UBSan验证三种EPON模式和拒绝/读回失败路径。
- scripts/test_native_rx_modes.py验证实际QM函数的大budget、归还恢复，以及POL_ID13/31元数据；test_pon_pdc_map.py/test_pon_data_path.py验证32个LLID的表映射和网络分流。
- port/scripts/check-pon-userspace.sh：实际ARM64/musl守护进程13项隔离网络测试。
- scripts/start_lan_build.sh输出独立systemd用户服务；关闭Codex不影响构建。

单LLID XEPON实机联网已通过。32 LLID硬件、其他EPON速率、GPON族的MAC/PLOAM/OMCI/GEM/T-CONT、CTC业务参数、FTTR输出、Wi-Fi、USB、硬件加速和最终UBI安装流程继续开发。
