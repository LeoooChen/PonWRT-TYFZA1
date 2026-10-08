# RTL9617C 原生 target

本目录从用户提供的 9617C 研究资料继承 UART、peripheral IRQ、watchdog、QSPI 与 MDIO/OCP 适配，并继续实际移植。原代码来源和固定参考在 sources.lock.json。

当前 target 对应 ARM64 Cortex-A55 / Linux 6.18。2026-10-07 已在本机跑通 6.18.52 四核 OpenWrt RAM shell；修改后的源代码继续适配 OpenWrt main `79bc56cb7483bc716da07c369530b792631904b4` / Linux 6.18.55。

实际启动记录见 ../docs/bringup.md，优先级见 ../docs/priority.md。

构建完整 OpenWrt：

```sh
sh port/scripts/build.sh /home/leo/PonWRT-TYFZA1/sources/openwrt 2
```

三个构建入口共用 `tools/prepare_pon_runtime.py`，在 make 前生成六种协议的 SerDes 固件、复制本机 1024 字节 BOSA 校准，并从原厂配置提取 ONU MAC、注入私有认证配置。输入默认位于 `analysis/private/live-files/` 和 `analysis/private/pon-auth.json`；缺失输入会直接终止构建，已有 `files/` 内容不能替代输入。完整构建入口可通过 `TYFZA1_STOCK_INPUTS`、`TYFZA1_AUTH_CONFIG` 指定输入位置。认证配置以 0600 安装，准备结果及哈希保存于 `artifacts/pon-runtime-manifest.json`。

需要后台构建和签名 RAM 测试载荷时运行 `sh scripts/start_lan_build.sh`。它以 systemd 用户服务运行，调用上述完整构建入口，日志保存在 `logs/lan-build-*.log`。

快速验证启动修正：

```sh
sh scripts/build_bringup_kernel.sh
```

后者使用当前主线 generic patches 和本 target，编译 6.18.55 内核，用户态沿用已成功启动的 OpenWrt initramfs 根文件归档。它服务于驱动实机迭代；完整固件通过前一条构建。
