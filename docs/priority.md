# 当前开发顺序

2026-10-10用户最新要求：Wi-Fi功能收尾之后，先核验有线LAN↔PON硬件转发在百兆业务下的CPU占用及真实硬件命中，修正问题；有线验收通过后再推进Wi-Fi硬件加速。测速使用国内可信站点，从Mac en5穿透PON，保留GPT必须的Clash连接，并排除fake-IP和应用透明代理对测试连接的干扰。当前HT/VHT双频AP及UCI/netifd管理已通过，HE仍有独立接续工作，详见wifi-openwrt-management.md。

1. 完成 EPON 族协议的有线 LAN↔PON 硬件转发与实机验收；现有 XEPON 注册、LOID、FEC、DHCP 和软件 NAT 已通过整包启动验收。
2. Wi-Fi 适配：先复核用户动态资料并按docs/wifi-port-order.md执行。依次落实板级身份、原生PCIe/clock/reset/ATU、中断与DMA、两颗无线芯片及固件、完整软件AP服务，再接无线NI/QM元数据和NE卸载。最新502个有效文件已冻结，16份研究文档阅读收据为manifests/wifi-reading-20261009-update.json；无线固件格式与RFE54输入实现见docs/radio-bringup.md。
3. 完成 OpenWrt UBI 方式与架构的正式安装、升级、恢复产物和步骤。

2026-10-08用户明确暂缓GPON/XG-PON/XGS-PON，因为没有对应光纤。保留已有手动模式和资料，当前不推进其MAC/PLOAM/OMCI适配。其他原机功能的完整移植目标仍保留。

采用原生 Linux/OpenWrt 接口和可维护源码；参考GPL代码与本机机器码，原厂业务ko不进入运行时。SerDes参数及本机校准保留为固件数据。硬件转发进度见hardware-forwarding.md。
