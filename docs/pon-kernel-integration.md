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
