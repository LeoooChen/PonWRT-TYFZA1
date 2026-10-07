# TYFZA1 原生 OpenWrt 实机启动

2026-10-07 首次 RAM 启动成功。原厂 U-Boot 2020.01 接受自建签名 script/FIT，启动原生 Linux 6.18.52 和 OpenWrt initramfs。串口 shell、ubus/procd、GICv3、25MHz timer 已运行；watchdog 由 procd 接管，163 秒仍可交互。

本机原厂 FIT 的 DTB SHA256 为 `5124f860df3bcd2e20b87d058e19b1ddfdc7a46c9d876663ba9feeff5a0f13ac`，与资料库采用的 TYFZA1 DTB 相同。

已发现原方案 Image load=0 与原厂 4KiB VoIP vector 预留重叠。新 RAM FIT 将 kernel load/entry 调整到 `0x00200000`，FDT 保持 `0x01800000`，FIT 下载到本机 U-Boot 的 `unzip_src=0x05000000`。

网口主机 `192.168.1.170`，U-Boot `192.168.1.2`。TFTP 服务读取项目 artifacts；Mac en5 在重启后失去 DHCP，因此临时添加 192.168.1.170 地址。TTL 日志和 TFTP 传输记录保存到项目 logs。

U-Boot RAM 启动命令：

```text
setenv serverip 192.168.1.170
setenv ipaddr 192.168.1.2
tftpboot ${unzip_src} image.itb
source ${unzip_src}:script
```

固件驻留 RAM。首次使用的完整载荷保存在 artifacts/prior-native-20261002，新的 NAND/SMP 载荷由 scripts/make_ram_fit.py 生成。最新主线固定为 79bc56cb7483bc716da07c369530b792631904b4、Linux 6.18.55，后续修正继续应用在 port/。

第二次 RAM 启动：kernel load/entry=`0x00200000`，CPU0–3 全部 online；原厂 VoIP 4KiB 预留不再冲突。SPI-NAND 已识别 GigaDevice 256MiB，但 dirmap 创建返回 -95；QSPI supports_op 已修复零长度模板判断，新增回归用例在修复前失败、修复后通过。

NAND 页外空间：原厂导出的 64B 是 ECC 开启时的用户页外视图，物理页外区为 128B，后 64B用于 ECC。Linux 报告 128B 与芯片规格对应。参考 [GigaDevice GD5F2GM7 官方数据手册](https://download.gigadevice.com/Datasheet/DS-00820-GD5F2GM7UE-Rev1.5.pdf)，页 4、9、45；本地 SHA256=`c34de661b6e4b59b89ac6422bd2dda932b8cedd78bb0ef75bf2d6e680a30f8bb`。
