# 四LAN硬件转发修复与实测

2026-10-10。用户当前通过光猫Wi-Fi连接GPT，禁止为本阶段重启无线或整机。全程保留ap2/ap5、wpad/netifd、Clash和Antify配置，未写NAND。5GHz继续为频道44/VHT80；PON仍保持MPCP注册及WAN业务。

## 故障与正式修复

真实en5下载100MiB：原硬件配置只能传4,227,072字节，25秒超时；关闭硬件转发则完整传完，约87.3Mbps。进一步诊断确认真实Hash键、CRC和NAT校验和正确，不能把“流已安装/有硬件计数”直接作为出口成功。

三个问题已在生产`rtl9617c-fe.c`修正：

1. 原来只将LAN0的NI RXctrl切到LAN L3连接器25。现在保存、启用、关闭和恢复全部四口（a5f8/a688/a718/a7a8），在流表和出口准备完成之后才准入。
2. 公共LAN连接器曾强制LSPID0，与eth1～eth3的建流来源不一致，CPU miss还会失去真实网口。现在STG0保留来源，LAN分类结果不覆盖LSPID；PON仍明确使用来源7。运行中LAN3实际解析LSPID3、orig3，和原生流键一致。
3. NAT后的LAN0～3原来进入NI demux2，再做一次L2查表。索引DA条目的转发信息会覆盖Linux选择的物理出口。现在只把这四个出口改为demux3→L2TM，保留硬件NAT动作已选好的网口。CPU/PON等其余选择器保留，关闭FE恢复原值。现场读回aaaaaaaa→aaaaaaff后，下载立即恢复。

未改动Linux每口netdev/PHY、TC flowtable的来源和目的端口归属，也未把四个LAN合并成一条测试口。

## 测速路径和代理排除

下载使用[中国科大Ubuntu镜像](https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso)，HTTP Range 100MiB，实际IP202.38.95.110。上传使用[南京大学测速页面](https://fs.nju.edu.cn/speed/)的`/speed/empty.php`，实际IP202.119.32.33，发送本任务生成的16MiB随机数据。真实IP另由阿里DoH核对，HTTPS仍按域名验证证书；没有使用fake-IP。

Mac的Antify会让Codex子进程继承应用代理规则；仅指定curl `--interface en5`、IP_BOUND_IF或关闭显式HTTP代理仍不足以排除它。前期`lan-real-*`、`lan-offload-real-*`及相关普通子进程测速不作为光猫验收，其CPU结论也作废。

有效测速由独立、一次运行的LaunchAgent启动`tools/mac_lan_speedtest.c`编译出的程序，真实TCP socket绑定en5，固定DNS，禁用curl代理。en5抓包、光猫conntrack中的五元组、对端IP/MAC和完成的HTTPS传输共同确认实际路径。Clash仍运行，GPT继续走en0 Wi-Fi。所有测速LaunchAgent在结束后已卸载，没有安排重复测速。

本次南航IPv4接口直接连接失败；科大测速后端拒绝校外流量，镜像下载可用；南大主测速入口有机器人检测，采用上述正式域名的另一测速页面；上海大学候选域名解析到私网地址，未将这些失败结果当作速度。补充同济上传接口对大请求返回413/500，结果也排除。南京大学备用接口的单连接软件/硬件对照分别15.562/12.547Mbps；仅凭这个站点的差异不能确定原因。后续用户仅接网线实测100/20Mbps，说明此前结果不能作为设备上行性能上限。

## 有线实测结果

实插网线为驱动eth3（0～3编号），Mac en5以1Gbps全双工连接。

本轮线上吞吐和CPU绕行验收为IPv4 TCP/HTTPS；既有IPv6、VLAN/QinQ解析测试通过，不扩大为这些协议新的线上吞吐验收。

| 负载 | 下载 | 上传 | 完成状态 |
| --- | ---: | ---: | --- |
| 单独100MiB下载复测 | 94.392Mbps | — | 206，完整100MiB |
| 单独16MiB上传 | — | 12.770Mbps | 200，完整16MiB |
| 两连接上传，共32MiB | — | 14.687Mbps合计 | 两个200，均完整 |
| 最终四口RAM修正、同时上下行 | 91.556Mbps | 10.310Mbps | 100MiB下载及16MiB上传均完整 |
| 用户关闭Wi-Fi，仅网口连接，南大测速 | 100Mbps | 20Mbps | 用户确认正常，达到宽带上限 |

用户报告后只读复核：flow_offloading/flow_offloading_hw均1，FE enabled/prepared均1、error0，live_fe_lan_fix仍加载，当前连接有HW_OFFLOAD。自上传A/B结束恢复硬件模式后，未再切到软件模式。用户这一轮没有同步抓取每条测试流的计数，因此不把开关读回冒充该次逐包取证；同一LAN口此前的自动抓包已证明双向主体硬件路径。

最终同时上下行的en5捕获159,338个测试TCP包，LAN和PON两侧CPU各捕获12个。其中上传仅有323字节TLS ClientHello，下载连接仅有随FIN发送的24字节TLS记录；其余为TCP握手、ACK和关闭。100MiB下载和16MiB上传主体没有出现在CPU抓包中。传输中conntrack为HW_OFFLOAD，两个方向统计增长；关闭连接后回到OFFLOAD符合FIN的软件控制路径，不能用结束后的标志否定传输期间的硬件命中。

最终测试硬件标志存在期间，/proc/stat四核合计忙碌率1.797%，CPU0/1/2/3分别3.676/0.490/2.288/0.735%；测试后合计1.117%。这些值包含在线Wi-Fi、SSH、TCP捕获和采样。单独下载采样的合计约1.0%。CPU百分比并非每包路径的证明，关键证据是实际网线传输、双向硬件状态/计数与CPU只处理少量连接边缘包。

四口入口读回全部18190400，LAN classifier来源覆盖位清零，STG0为001c787e，出口demux为aaaaaaff。未插线的LAN0～2没有冒充已完成吞吐实测；正式实现和寄存器配置覆盖全部四口。

## 构建与复现边界

`scripts/test_fe_lan_lifecycle.py`直接运行生产准备、启用、关闭、恢复函数：四口不同原值、三次启停、幂等操作、失败时不准入、准备先于准入、CPU来源保留与出口选择器恢复均通过ASan/UBSan。相同测试运行07f2567旧代码会在入口准入时失败。既有NAT/IPv6/VLAN解析、统计、4096流接口容量和共享资源释放测试全部通过。

完整构建服务`ponwrt-tyfza1-lan-20261010-172604.service`退出0。PCIe最终FIT为`artifacts/full-pon-four-lan-fe01-6.18.55/ram-pcie/image.itb`，SHA256为`49c48e9011a978fd7d7fc241e8c72014410dc65028eba57fd98d643ffcbf057c`。内置initramfs1133项、PON身份/校准、双频无线管理包及最终FIT中的实际FE机器码均已核验；包括编译器拆出的`rtl_fe_disable.part.0`。

当前设备保持原RAM内核，以`tools/live_fe_lan_fix.c`生成的可卸载模块应用同样四口寄存器修正，未重启换镜像。该模块是当前已准备FE的临时修正；旧内核后续重新准备FE可能覆盖这些值，它不替代新固件的正式启停实现。新镜像和其他三口逐口插线验收，须在不依赖当前Wi-Fi维持GPT通信时完成。无线硬件加速继续暂缓。

末尾只读复核中MPCP注册仍为true；OAM当前状态waiting，历史认证成功1次、失败0次。本轮没有重启注册/认证，因此不把持续可用的WAN业务扩大为新一轮OAM认证验收。

结构化证据为`manifests/lan-four-port-hardware-20261010.json`，原始样本、抓包和分析在logs/lan-final-*；完整构建的FE代码核验为artifact目录下的fe-code-verification.json。
