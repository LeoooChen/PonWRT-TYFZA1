# TYFZA1 原生无线驱动进度

2026-10-09继续依据动态无线资料实现，整体顺序见wifi-port-order.md。原生PCIe已进入完整镜像；当前设备离线，PCIe枚举、无线芯片身份和AP收发尚待实机验证。

## 本轮资料与配置结论

最新已读冻结目录evidence/wireless-study-20261009-104746，共502个有效文件、67861334字节。新增固件/RFE、无线软硬件协作、跨设备DMA专题全文阅读；原始资料目录保持只读。check_wireless_study.py默认与manifests/wireless-study-current.json选择的快照比较，后续新增资料在对应阶段先阅读再接入。

原机配置与实际初始化覆盖确认最终RFE54：5GHz/wlan0使用RTK66587_BPF，2.4GHz/wlan1使用RTK66287，FEM参数为0xee。初始rtw_rfe_type=50不是最终配置；PCI device ID、MAC chip枚举、固件头chip字段和host cv分别解释。原机probe请求32bit DMA，SoC coherent ATU高地址别名4与NI使用的别名2分别管理。

## 固件格式与构建输入

新增原生rtl_ax_ap_fw.h，解析8852C与8192XB AP固件的主头、动态头和section。MAC枚举2选择8852C，3选择8192XB；host cv0选择u1，其余选择u2，与原机mac_query_fw_buff一致。当前四份固件版本均0.27.84.3，firmware cut字段1/2与host cv0/1分别记录。

原始主头32字节、四项段头64字节、动态头64字节，累计160字节；段数据使用完整RISC-V地址。下载phase1仅发送主头和段头共96字节，并将分段长度字段改为2020；原始数组保持不变。sections按2020字节拆分，末包使用实际剩余长度，载荷累计与原段完全相同。该实现提供驱动下载协议的数据处理；实际PCIe H2C ring、MCU ready和mac80211接口继续实现。

tools/prepare_wifi_runtime.py直接使用原生C格式处理，向构建目录安装四份原机AP固件数据与选中FEM目录的20份RF参数。路径为lib/firmware/realtek/tyfza1，固件、RF参数均留有源哈希。没有复制或加载原机主机侧ko。参数目录含6GHz备用表，保留数据不等于启用未确认频段；实际radio能力仍以对应驱动与板级身份决定。

公共port/scripts/build.sh准备这些输入，最终tools/verify_wifi_payload.py从待启动FIT实际kernel/initramfs提取并核对四份固件、20份参数，外部files目录不代替启动载荷。显式记录radio_driver_available=false，尚未标作无线可用。

## 离线验证

四份原机固件各段的地址、offset、长度和SHA256与独立提取清单一致；96字节下载头及2020字节分段重组正确。host cv0..8分别验证18组选择；短主头、动态头长度、段范围和下载输出边界通过ASan/UBSan。结果在logs/radio-firmware-format-test.json。

PCIe修正另有实际host startup、MSI初始化与Linux轮询宏测试，说明BER未就绪继续建链、INTx/MSI位保留以及ATU忙位清零条件。对应修正已提交b7ef9cd，与无线固件协议分开验证。

## 下一步

接入两颗无线芯片的MAC/PHY与固件下载transport、RX/TX ring和completion、RFE54/FEM参数及校准，再完成原生mac80211/cfg80211、双频AP、STA/密钥生命周期及OpenWrt配置。基础无线完成后接原厂NI/QM metadata和NE流表通路；最后完成OpenWrt UBI刷入、升级与恢复。
