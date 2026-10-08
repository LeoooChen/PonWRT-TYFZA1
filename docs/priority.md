# 开发顺序

1. 原生 OpenWrt 在 TYFZA1 上启动：CPU/PSCI/GIC/timer/UART/watchdog，RAM 根系统与串口 shell，然后原生 NAND/UBI。
2. PON：NI/DMA 和网口数据通路、目标光前端与 MAC/TC、MPCP/PLOAM、OAM/OMCI、业务配置。
3. Wi-Fi：clock/reset/ATU/PCIe、RTL8192XB/RTL8832C 适配与 AP。
4. 硬件加速：有线与 PON 通路稳定后再接 flow offload/硬件表。

pbs05/ponwrt 面向 Airoha，只引用其协议接口和集成方案。RTL9617C 是新增 target，厂商底层代码需要移植到主线内核接口。

第一轮 RAM 启动先使用资料库已经构建且与本机原厂 DTB 对应的 Linux 6.18.52 原生启动载荷，取得实机结果后把修正应用到新主线 Linux 6.18.55。

2026-10-08用户补充：完整镜像构建期间，优先追并移植非Wi-Fi的LAN↔PON硬件转发。已完成原生Hash流键/动作/掩码编码对照及掩码表实机读写；硬件转发路径和Linux flowtable接入继续推进，见hardware-forwarding.md。
