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

原机`rtw_hal_set_ch_bw@1cf3cc`计算中心频道，先配置MAC，再调用BB和RF频道/带宽链。`halrf_ctrl_bw_ch@2f8c60`分别执行ctl_ch、ctl_bw、ctl_band_ch_bw和rxbb_bw：C使用`halrf_ctl_band_ch_bw_8852c@30b1a0`及`halrf_rxbb_bw_8852c@30db98`；XB使用独立ctrl_ch/ctrl_bw及`halrf_rxbb_bw_8192xb@33ce5c`。C读取HAL+1580作为DBCC开关，HAL+4作为CV；本板为DBCC0/CV1，两条射频路径都配置。之前把+1580标为CV的文档错误已更正。

现有原生`rtl_ax_receive_set_channel`有完整停TX、停RX/HCI、BB/RF、TPU、RX_DCK、GAPK、IQK、TSSI及恢复/通知顺序，但频道参数和多个校准入口仍限定20MHz。接续将主频道、中心频道、带宽和PHY primary index作为同一频道状态传递，分别对齐以下原机步骤：

1. BB/ADC和MAC TXSC，依据已取得的函数输出。
2. RF18模拟/数字两组、RXBB LUT及XB专有OP5K/频道辅助操作，保持原厂CV1分支。
3. 40/80MHz普通功率限值、RU限值、by-rate和shape，并核对原机TPU构造器。
4. 相应带宽的IQK、TSSI/DE、RFK频道通知和BBinfo。
5. 逐包WD的BW/GI/速率与终端控制表，再发布HT40及C的VHT/HE能力。

当前完整构建服务`ponwrt-tyfza1-lan-20261010-130028`使用已实测提交`cd402d5`；这份更高带宽提取尚未改变生产驱动。

## IQK原机对齐及HT40板测

继续执行原机完整`iqk_start_iqk_8852c`及其局部helper，确认20MHz采用四次LOK（0x008/0x308/0x508/0x308），40/80MHz才用六次增益/LUT程序。接收IQK的ADC selector在20/40MHz为1、80MHz为2；接收AGC命令是0x1708。原生旧实现混用了宽带LOK、selector2和0x708，另多写了原机这条路径不存在的808c=10010。已按完整原机输出修正，且让TX/RX宽带IQK命令、诊断状态随带宽变化。

原机指令输出为`radio-stock-C-calibration-bandwidth.json`；显式宿主完成值和温度仅用于提取执行路径。实际AP18板上两条射频路径全部完成、失败标志0，用户复测仍为50/20Mbps。没有将这次校准修正称作已测得吞吐增益。日志`radio-ap18c-IQK-first.txt`、`radio-ap18c-IQK-connected-50Mbps.txt`。

AP19cb接入C的HT40：主频道44、中心46（44+48）、PHY primary index2、MAC TXSC2，RF18/RXBB、普通功率及RU限值对应40MHz，BBinfo通知包含带宽和TXSC，逐包WD7的BW[29:28]由mac80211选定速率传入。C628 bit4也按实际原机cfg_mac_bw补齐。原机功率构造器及WD编码见`radio-stock-power-registers-40.json`、`radio-stock-HT40-WD.json`。

用户实测5GHz下载达到百兆宽带上限、上传20Mbps；真实站点双向MCS15、40MHz/短GI、300Mbps。快照中70792个聚合MPDU全部收到RPQ完成，full0、pending0，停队列/恢复各7次，TX failed0；RX曾出现1次RDU，记录保留。普通/RU/by-rate的43个DWORD实机readback全部吻合。证据`logs/radio-ap19cb-HT40-100Mbps.txt`。2G目前仍为已验收的20MHz/2SS/BA路径。

接续80MHz还需普通限值中的40m_0p5双子带最小值、四个20MHz子带及RU列、ADC数字时序、完整主子频道/TXSC，并补齐VHT/HE的实际发送和能力发布；然后接2G的HT40及HE。

## 构建期间的80MHz依据

`radio-stock-power-registers-80.json`已执行C实际80m普通/RU构造器和MAC writer。普通限值每NSS40字节：OFDM主20MHz在4..5，四个HE20子带在6..13，两个HE40子带在22..25，HE80在30..31，40m_0p5在36..37，为两侧40MHz限值先按有符号半dBm比较后的最小值。RU26/52/106各8字节，80MHz填四列，对应中心频道-6/-2/+2/+6。C/XB实用频段分开，80MHz仅接C。

BBinfo实际20字节通知也已追通并归档：C80/主44/中心42为`01102c2a02000000000000000100000001090000`，末word含TXSC20=1、TXSC40=9；主36则TXSC20=4、TXSC40=10。20/40MHz的当前原生通知与原机输出吻合。

原机`halbb_5m_mask_8852c`随带宽变化，40MHz会配置4c4c/4d10低6位4、使能bit12及方向位，80MHz还按其入口参数选择边缘子带。当前原生40MHz沿用初始关闭值，下一次更高带宽接入时按完整调用入口再对齐该步骤。

HE发送的实际主机注册入口是`rtw_hal_bb_ra_register@1b822c -> rtw_halbb_raregistered@2cbb84`，更新入口为`rtw_halbb_raupdate@2cc1d0`，RSSI更新为`halbb_ra_rssi_setting_watchdog@2ccfd0`。当前主机采用mac80211/minstrel HT速率选择；HE接续需完整接入原机WCPU速率控制及真实反馈，不能仅在Beacon发布HE能力。

生产源码`05dd751`的完整构建运行在独立服务`ponwrt-tyfza1-lan-20261010-133756`，输出`artifacts/full-pon-radio-ht40-ap19-6.18.55`。实机当前C为AP19cb/HT40，XB为AP19/HT20；这份80MHz提取没有改变已验收的生产源码。
