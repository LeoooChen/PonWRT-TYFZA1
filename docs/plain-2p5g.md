# TYFZA1 平纹散热器 2.5G 批次兼容记录

日期：2026-11-03。样本为网友提供的 `backup_partitions.zip`，原厂版本 `11DXF30002`。

本次补充的是备份原厂 U-Boot 新增的两种 NAND 支持。**备份不包含运行时 JEDEC ID，尚不能证明故障机器实际使用哪一种 NAND，也不能据此认定所有不能启动的原因已经解决。**

## 启动基线与板型

- 16 个分区备份均通过随附 MD5；分区与 UBI 卷布局符合现有迁移程序。
- 新样本 BL2、BL31 与项目 `4ge` 早期启动基线逐字节一致，和旧 `2p5g` 基线不同。
- 新样本的 2.5G FDT 与已有板型样本逐字节一致，SHA256 为 `5124f860df3bcd2e20b87d058e19b1ddfdc7a46c9d876663ba9feeff5a0f13ac`。
- 原厂 GPIO 111/112/113 取值 0 对应 `conf-0`，1 对应 `conf-1`。该样本使用 2.5G 的 `conf-0`。
- GPT 两个 FIP 分区的末扇区是 1967、3503。现行 U-Boot 1.6 的安装器已支持这样的长度变化，并按实际 GPT/BL2/BL31 选择基线。
- 因此本次没有增加第三种 loader，也没有修改设备树或将 `4ge` 早期代码与千兆板型强行绑定。原厂 boot SHA256：`19c21f952198fe43fdfe965764b5afd64639335f54b9f6201c36a34f83a01eff`。

## NAND 差异与改动

对原厂 BL33（加载地址 `0x04000000`）反汇编得到：

| 型号 | JEDEC ID | 主数据/OOB | 块组织 | ECC |
| --- | --- | --- | --- | --- |
| DS35Q2GBS-IB | e5:b2 | 2048/128 字节 | 64 页 × 2048 块 | 8 bit/512 字节 |
| FM25G02D | a1:f2 | 2048/64 字节 | 64 页 × 2048 块 | 4 bit/512 字节 |

表项位于 BL33 偏移 `0x63840`、`0x63a40`。原厂 ECC 回调分别位于 `0x400485c`、`0x40047c8`。状态字节的 bits 6:4 含义：

- DS35Q2GBS：0 → 无错误；1 → 最多 3 位；2 → 不可纠正；3 → 最多 6 位；5 → 最多 8 位；其余保留。
- FM25G02D：0..4 → 对应纠正位数；7 → 不可纠正；5/6 保留。

Linux 新增 Dosilicon 厂商注册和 `b2` 表项，并在 FMSH 表中新增 `f2`。U-Boot 通过 `scripts/prepare.py` 对锁定的厂商驱动应用同样的支持。保留值按错误处理，避免静默宣称数据正常。

DS35Q2GBS 的官方手册和 Linux 投稿都使用单 plane。旧 DS35Q2GB `e5:f2` 使用双 plane，不能直接作为新型号别名。原厂新表沿用 plane 标志，与手册/投稿不一致；本次遵循手册的 12 位列地址，不设置 plane 选择。OOB 64..127 为 ECC，保守保留前两个字节后，2..63 可用。

FM25G02D 的 ECC 位在 feature `0x90` bit 4，`0xB0` 仍用于 QE/OTP。本次为这个表项增加独立 ECC 寄存器标志；其余现有型号行为不变。每个 16 字节 OOB 段的后 8 字节为 ECC，前段保守保留两个字节，与厂商参考布局一致。U-Boot 同时配置正确的 OOB 自动布局，避免落入默认的 128 字节布局。

## 验证与适用范围

- 新增 `scripts/test_nand_batch.py`：编译生产回调，覆盖两种芯片全部 256 状态字节、OOB 范围、容量/页/块配置、ID 匹配和 ECC 寄存器读写错误。带 `--uboot` 参数可连同生成的 U-Boot 源码验证。
- 完整 U-Boot 编译、签名打包、181 项现有测试通过；通用包对真实备份 `--check` 通过。使用生产安装器在临时文件模拟 MTD 写入，只修改两个 FIP 区域，保留 GPT，更新后仍可识别并继续升级。
- Linux 6.18.55 独立副本完成完整内核编译；正式候选使用项目相同 OpenWrt 工具链与原配置。
- 现有板型测试、工厂参数导入测试、12 项 UBI 安装/升级测试、存储板型与失败路径测试通过。
- 真实 Config/apps 备份通过 Linux UBIFS 日志回放读取。普通离线解包器的旧索引报错并不代表备份无法迁移。QEMU/nandsim 验证可迁移、保存两份工厂数据、恢复原始 Config/apps 并阻止不兼容降级；这项测试不模拟 RTL9617C SPI 控制器或这两颗真实 NAND。
- 工厂 `PON_MODE=0` 为 Active Ethernet。现有 importer 可成功保留无线、光学校准与设备身份，不因此中断迁移；并不代表固件新增 Active Ethernet 业务支持。

## 本地候选构建

PonWRT 的 NAND 内核补丁和测试纳入本地 `main`，项目版本更新为 `0.8.5-beta`。配套 U-Boot 改动仍保存在独立工作区 `/home/leo/U-Boot-TYFZA1-plain-2p5g-20261103`，基于公共 main `c989ce7`，版本仍为 1.6。未推送或发布。下面记录的是升版前生成的候选包，未将旧二进制重新标记为 0.8.5-beta。

候选内核基于项目当前 Linux 构建目录的独立副本，应用本补丁后，以相同 OpenWrt GCC 14.4.0 和 `.config.modules.save` 构建常规 UBI 内核。最终 FIT 为 4,016,872 字节，低于 6 MiB 上限；含两个原有板型配置。没有打包中途用于编译检查的 initramfs 内核。

候选 rootfs 来自项目当前构建目录，内部版本为 `0.8.4-beta r0-79bc56c`，SHA256 为 `019f7f7d2d11fca1902d2b6e640378ebd0667a46b31778b9397fe1bf17e7754b`。它与 `artifacts/release-0.8.4-beta/firmware` 中的发布包不同，因此候选不是对已发布镜像的逐字节单补丁重构，也不宣称包含当前 main 全部未提交更改。sysupgrade 元数据标为 `0.8.4-beta-plain-nand-candidate-20261103`；系统界面仍显示 rootfs 的原版本。

候选文件保存在本任务 `candidate` 目录：

- `sysupgrade-plain-nand-candidate.bin`：已运行 PonWRT 时的候选系统包。
- `tyfza1-telnet-install-plain-nand-candidate.tar.gz`：原厂环境安装所需的内核、rootfs 与安装工具。
- `tyfza1-uboot-install.run`、`tyfza1-uboot-universal.bin`：加入 NAND 支持的本地 U-Boot 1.6 候选；文件名遵循项目约定，版本号未升版。
- `SHA256SUMS`：上述文件和单独 kernel/rootfs 的校验值。

两种系统包中的有效 squashfs 内容一致；sysupgrade 保留原构建的 512 字节对齐尾部。所有候选均未刷入设备。

## 实机确认

先收集失败机器从上电开始的完整串口日志，重点记录 BL2/BL31/U-Boot 版本、NAND 名称和 ID、选中的 FIT 配置，以及停止位置。`e5:b2` 或 `a1:f2` 可直接对应本次新增支持；若是其他 ID，应沿日志另查，不能凭散热器外观确定 NAND。

候选包仍需在该批次实机验证冷启动、重启、两槽启动、UBI 读写与 2.5G 链路。测试时保留这台机器自己的原厂备份；早期启动选择应由通用包自动匹配，Linux 板型为 `conf-0`。

## 参考资料

- [Dosilicon DS35X2GBSXXX Rev.02](https://www.dosilicon.com/resources/SPI%20NAND/DS35X2GBSXXX_rev02.pdf)：页 22、33、34、35，ID、ECC、OOB、有效块数量。
- [Linux Dosilicon 支持投稿](https://lkml.iu.edu/hypermail/linux/kernel/2605.3/05624.html)：DS35Q2GBS 的单 plane、40 坏块上限和 OOB 布局。
- [FMSH FM25G02 V0.4](http://www.fmsh.com/nvm/FM25G02_ds_eng.pdf)：页 11、13、30、32、33，ID f2、ECC 寄存器、状态、41 坏块上限和 OOB。
- [Rockchip FMSH 驱动](https://github.com/rockchip-linux/u-boot/blob/next-dev/drivers/mtd/nand/spi/fmsh.c)：FM25G02D ID/几何与保守 OOB 布局交叉验证。

备份包含设备身份与校准参数；本记录只保存兼容性事实，不将原厂 Config/apps 纳入源码。
