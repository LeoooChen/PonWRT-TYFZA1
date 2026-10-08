# 当前开发顺序

1. 完成 EPON 族协议的有线 LAN↔PON 硬件转发与实机验收；现有 XEPON 注册、LOID、FEC、DHCP 和软件 NAT 已通过整包启动验收。
2. Wi-Fi 适配：PCIe/clock/reset/ATU、RTL8192XB/RTL8832C 驱动与 AP，随后接入无线转发。用户新增研究已复制到 evidence/wireless-study-20261008，433 个文件留有哈希收据。
3. 完成 OpenWrt UBI 方式与架构的正式安装、升级、恢复产物和步骤。

2026-10-08用户明确暂缓GPON/XG-PON/XGS-PON，因为没有对应光纤。保留已有手动模式和资料，当前不推进其MAC/PLOAM/OMCI适配。其他原机功能的完整移植目标仍保留。

采用原生 Linux/OpenWrt 接口和可维护源码；参考GPL代码与本机机器码，原厂业务ko不进入运行时。SerDes参数及本机校准保留为固件数据。硬件转发进度见hardware-forwarding.md。
