# TYFZA1 更高带宽接续（2026-10-10）

双频20MHz、2SS HT、WMM和BA已在原机取得约50Mbps下载/20Mbps上传。接续使用同一原机`rtk_wifi6.ko`（SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`）追完整频道变化链，再接40/80MHz。

## 已取得的原机函数输出

`tools/trace_stock_bandwidth.py`直接执行两颗芯片的`halbb_ctrl_bw`、C ADC、XB bw_setting及EDCCA子函数，并执行`rtw_hal_mac_set_bw -> cfg_mac_bw -> rtw_hal_bb_get_txsc -> halbb_get_txsc`。真实寄存器接口替换为记录边界，原始函数指令保持不变。结果为`manifests/radio-stock-bandwidth.json`和`logs/radio-stock-bandwidth.txt`；当前只作为离线实现依据。

| 芯片/带宽 | 主频道/中心频道 | BB primary index | MAC C088 TXSC |
| --- | --- | --- | --- |
| C 20MHz | 36/36 | 0 | 0 |
| C 40MHz | 40/38 | 1 | 1 |
| C 40MHz | 36/38 | 2 | 2 |
| C 80MHz | 44/42 | 1 | 0x91 |
| C 80MHz | 40/42 | 2 | 0xa2 |
| C 80MHz | 48/42 | 3 | 0x93 |
| C 80MHz | 36/42 | 4 | 0xa4 |
| XB 40MHz | 8/6 | 1 | 1 |
| XB 40MHz | 4/6 | 2 | 2 |

MAC C010低2位分别为20/40/80的0/1/2，C628本轮零初值下C为0x13、XB为0x10。实际实现继续保留各字段外的初始化状态。两颗芯片均使用MAC band0；RF band5G不意味着切到MAC band1。

C 80MHz相对20MHz的ADC变化包含每条路径C0/C1窗口的D4 mask0x0c000000：3→2，D8 mask0x1e0：15→8，EC mask0xff0000：2→3。BB4974带宽字段0→2，4978携带正确主20MHz索引。XB的40MHz另置4c00/4cc4 bit4。2.4GHz板型按20/40MHz频道推进。

## 下一条完整链

原机`rtw_hal_set_ch_bw@1cf3cc`计算中心频道，先配置MAC，再调用BB和RF频道/带宽链。`halrf_ctrl_bw_ch@2f8c60`分别执行ctl_ch、ctl_bw、ctl_band_ch_bw和rxbb_bw：C使用`halrf_ctl_band_ch_bw_8852c@30b1a0`及`halrf_rxbb_bw_8852c@30db98`；XB使用独立ctrl_ch/ctrl_bw及`halrf_rxbb_bw_8192xb@33ce5c`。C函数读取HAL+1580的真实CV字段，沿用本板CV1。

现有原生`rtl_ax_receive_set_channel`有完整停TX、停RX/HCI、BB/RF、TPU、RX_DCK、GAPK、IQK、TSSI及恢复/通知顺序，但频道参数和多个校准入口仍限定20MHz。接续将主频道、中心频道、带宽和PHY primary index作为同一频道状态传递，分别对齐以下原机步骤：

1. BB/ADC和MAC TXSC，依据已取得的函数输出。
2. RF18模拟/数字两组、RXBB LUT及XB专有OP5K/频道辅助操作，保持原厂CV1分支。
3. 40/80MHz普通功率限值、RU限值、by-rate和shape，并核对原机TPU构造器。
4. 相应带宽的IQK、TSSI/DE、RFK频道通知和BBinfo。
5. 逐包WD的BW/GI/速率与终端控制表，再发布HT40及C的VHT/HE能力。

当前完整构建服务`ponwrt-tyfza1-lan-20261010-130028`使用已实测提交`cd402d5`；这份更高带宽提取尚未改变生产驱动。
