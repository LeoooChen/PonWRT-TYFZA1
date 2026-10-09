# TYFZA1 原生 PCIe 基础实现

2026-10-09按wifi-port-order.md的板级阶段实现，设备当前离线。源码、编译、寄存器序列和最终载荷已经验证；PCIe link、两颗radio的实际枚举、DMA和AP仍待实机验证。

## 实现

pcie-rtl9617c.c接入Linux6.18 DesignWare host，使用PCI core枚举/BAR、DesignWare MSI domain与共享wrapper中断，INTx按Linux映射后使能。实现端点GPIO复位、core/DPHY复位、SerDes参数/校准、link启停、SoC coherent ATU及退出恢复。GPIO、reset、clock和PHY使用原生Linux接口，未加载原厂PCI/Wi-Fi模块。

phy-rtl9617c-pcie.c提供两路时钟、四个reset ID和四个lane PHY。Taurus的电源与隔离位在f432036c；旧phy-ca77xx.c中的f43200c4电源位属于另一套布局，不用于本板。固定版本phy-rtk-taurus.c及原机寄存器头确认该差异，来源收据为manifests/pcie-native-reference.json。

f43200c0同时含PCIe时钟和watchdog reset routing。两者共用syscon/regmap，避免独立申请同一MMIO区域与read/modify/write覆盖；原生watchdog仍通过Linux watchdog core/procd管理。板级DTB验证器同时验证原厂watchdog的两个资源被拆成timer和shared syscon后仍对应正确地址。

原机PCIe0/1各启用单lane；第三控制器disabled。新专用DTS继承现有PON/NI DT，保存原机两套A/B SerDes参数；当前选Taurus参考的B/default分支，首次实机继续核对实际cut和校准结果。DesignWare iATU与SoC coherent ATU保持不同资源；后者按512MiB实际DDR配置CCI高地址别名4，并保存/恢复原8项配置。MSI与DMA正确性需要实机确认。

## PCIe 复位 GPIO 修正

用户指出初始生成DTS把bank3的局部引脚5/6写成了合并GPIO控制器的5/6。原机DTB的两个gpio-reset节点引用gpio-controller@f43292c0/bank@3，reg=3且gpio-ranges的全局基址为96；当前原生GPIO驱动按pin/32选择bank，正确引脚为101、102，active-low保持不变。

tools/stock_dtb.py统一读取GPIO bank与gpio-ranges，tools/inspect_wifi_inputs.py输出bank、bank_pin、native_pin。tools/make_pcie_dts.py从原机DTB与已核验输入生成原生DTS，不再手写bank局部编号。未启用的PCIe2原厂GPIO255记录为unused，不生成原生控制线。

tools/verify_pcie_dtb.py对实际编译DTB核对reset引脚/极性、PCIe与coherent ATU资源、GIC中断、ranges、复位等待和两套SerDes。回归测试将编译后的两条线分别改回5/6，均被验证器拒绝。完整构建入口在打包前验证DTB，并通过tools/verify_pcie_payload.py验证最终FIT实际kernel/DTB与产物及原机接线一致。

## 编译与离线验证

200-native-pcie.patch针对固定Linux6.18.55的Kconfig/Makefile生成，零fuzz对齐。补齐PCIe/PHY/syscon的Kconfig闭包；normalize-kconfig.sh现在解析源码树绝对路径，并在清理内核包前运行defconfig，适用于后台构建。

2048组单lane SerDes寄存器写序列与固定GPL函数逐项一致；4096组PHY电源开关向量与Taurus函数一致。测试模拟普通内存中的寄存器，仅验证代码序列，不冒充设备运行。结果为logs/pcie-sequence-test.json、pcie-controls-test.json。

完整构建服务ponwrt-tyfza1-lan-20261009-102606正常退出0。载荷artifacts/full-pon-pcie04-6.18.55/ram-pcie/image.itb，6207972字节，SHA256 a5d38ef7c243371438b02061b20c9e0d834c5f9c5da14926c0b1b8fbaa72d139。最终选中的DTB包含101/102，DTB SHA256为60d1b8a0388516325024c390c13edcdb4376d910c34722223060d247a39efde8；实际kernel/DTB与外置目录一致。PON载荷的校准、身份和内置initramfs995项一致验证通过。

## 下一步

继续落实两颗radio的cut/PCI subsystem/RFE及固件协议，按原生mac80211/cfg80211接口完成无线驱动。设备恢复后先验PCIe枚举与DMA，再验独立AP收发；基础无线完成后，按已读资料接专用NI TX与QM metadata路径，最后完成无线NE卸载和原生UBI交付。
