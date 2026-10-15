# PON 修复的完整内核与 initramfs 集成

本轮把截至 `60df87a` 的修改做了实际最终链接。此前单独编译过的 PON/NI
对象不能证明完整内核已包含当前 FE，也不能证明启动时使用的是新版用户空间。

输入核对发现构建目录仍有旧 FE回滚实现，既有 initramfs 还带着旧身份导入
和不区分制式的停止脚本。已保存旧 vmlinux、Image、配置和被替换的构建源码，
把内核 overlay 对齐到当前仓库，再构建完整 ARM64 vmlinux。最终链接和
MODPOST通过，无编译警告；GPON/XGTC恢复、NI gate、FE启停及业务分片接口
均在最终ELF中解析。核对覆盖120个overlay文件，其中模块源码对齐不代表
本轮编译或验证了无线模块。

随后只构建 `tyfza1-pon-control` 包，C/Rust当前输入与实际准备目录逐项对应。
`0.3.1_alpha.apk` 使用现有构建公钥验证通过；在独立的rootfs副本内离线升级
该包，同时覆盖12个当前平台文件。没有执行安装脚本或启动这些服务。

使用这份独立rootfs作为命令行 initramfs输入，生成
`artifacts/pon-kernel-integration-20261016/pon-current-initramfs-Image`。
没有改动内核`.config`，命令行输入覆盖记录在同目录。其余基础用户空间和
无线模块来自既有rootfs；本轮没有重建整套固件或开展Wi-Fi排查。

验证直接从最终ELF定位内嵌initramfs，并核对ARM64 Image里的同一压缩段，
解压内容必须与本次CPIO一致。再逐字节核对三个PON程序、factory.uc、PON
服务及工厂导入服务，并确认包数据库为`0.3.1_alpha`。这证明核对的是最终
镜像中的内容，而非仅验证打包前的目录。

本轮没有刷写、启动或发布产物。该Image是集成构建产物，尚未完成DTB/FIT
引导封装及实机验证。它不证明EPON/GPON/XG/XGS的物理注册、FIFO/DMA排空、
OLT重连或Wi-Fi高速流量结果。当前实机也未因此换成这份内核。

详细输入、产物SHA256和日志见 `manifests/pon-kernel-integration.json`。

## 2026-10-16：签名 RAM 引导与 EPON 实机复测

上述尚未引导的状态是集成构建时的边界；随后已用 PON/NI 专用 DTB 封装
签名 FIT，并从 TFTP 加载到 RAM，引导了同一份新版内核和 initramfs。实际
U-Boot 验证配置、内核和 DTB 的 SHA256/RSA2048 签名，通过后运行内核。

打包时发现 RAM FIT 仍被错误地套用 NAND kernel volume 容量限制。现将该
限制限定于 ubi 模式；RAM 模式继续按已有内存布局、加载地址及预留区检查。
11,527,632 字节 FIT 已完成签名校验、破坏签名拒绝检查、TFTP CRC 对照及
实际引导，不需要为这个单行修复另做全量构建或全量测试。

新内核 EPON-D10G 的 LLID0 注册、OAM 认证 accepted、WAN DHCP 和公网
ping 均成功。执行一次 PON 服务重启后再次注册并认证，服务和 FE 错误码
均为0；随后两次公网 ping 均收到。没有可用 GPON/XG/XGS OLT，不能把
EPON 成功外推到 ITU 制式。Mac 绑定有线接口的 HTTPS 探测连接超时，且
该探测读取了不存在的 FE sysfs 文件，因此不作为转发性能通过依据。

本次未执行固件刷写、saveenv 或 UBI 写入命令，未修改保存的 PON 配置。
必须说明：原厂 U-Boot 在正常重启中自行保存冗余 NAND 环境，串口已观察
到擦写；因此不能声称整个设备完全没有发生 flash 写入。

RAM DTB 不含 PCIe 无线节点，本轮不验证 Wi-Fi。实机结果与产物哈希见
`manifests/pon-ram-live.json`；历史构建清单保持原有时间点的状态。
