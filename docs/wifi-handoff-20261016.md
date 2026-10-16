# Wi-Fi 暂停交接

用户要求先收尾、暂停。冻结范围为 [112 项功能清单](wifi-porting-gaps-20261016.md)，后续继续实现这些已发现的遗漏，不再扩展审计项目。

本轮运行代码已本地提交 `16e6537`：增加 `dm_work` 两秒周期任务、两路径实时 RF 温度和 SCC LCK。C/XB 分别保留原厂 RF5 恢复语义；LCK 超时恢复运行状态、明确返回失败，并保留上次成功温度。实现和边界见 [wifi-rf-runtime-20261016.md](wifi-rf-runtime-20261016.md)。没有推送、刷机或实机验证。

验证已完成：原厂 ARM64 与编译后的生产 C 在两颗芯片各五个定点条件下对照通过，RF 写入失败恢复通过；ARM64 无线外置模块编译通过。开发模块为 `artifacts/wifi-bb-runtime-20261016/radio/rtl_ax_ap_rf_runtime.ko`，SHA256 `920dc9d733a36c9740882ccf9ab319e13dbbfdba5c03ab6415117ca6070e14ea`。只运行这些定点检查和模块构建，没有全量测试/固件构建。

尚未完成的工作仍保留在冻结清单中。F03 目前仅落地温度与 LCK，F06 目前覆盖已有 SCC 模式；DIG、环境监测、CFO、动态 EDCCA、完整 PHY/RSSI 状态及其余协议/加速/管理缺项没有在本轮宣称完成。不要把分析工具、初始化字段或源码编译成功当成完整功能实现。

恢复时先接入 BB 的共同状态与正确 PHY/RSSI 消费，再接 DIG/环境测量等算法及其资源仲裁。现有 `dm_work` 已提供生命周期入口，必须继续与换信道、RFK 和停止使用同一互斥关系。EDCCA 的原厂定点输出、RSSI/PPDU 和已完成的管理层分析已保存在 [pending-implementation-index.json](../artifacts/wifi-bb-runtime-20261016/pending-implementation-index.json) 及其归档，重用现有证据，不重跑已经完成的查询。

`tools/trace_stock_bb_edcca_runtime.py` 和 `stock-edcca.json` 仅保存原厂参考行为；11 个定点输入包括用于识别通用分支的 6GHz 输入，不表示 TYFZA1 支持 6GHz，也没有接入原生 EDCCA 运行实现。

原厂输入仍以 SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e` 的 `rtk_wifi6.ko` 为依据。所有本轮 REA 任务已关闭。既有 PON、以太网、DTS、OMCI 和 OpenWrt 子模块工作区变更保留原样，未纳入本轮 Wi-Fi 提交。
