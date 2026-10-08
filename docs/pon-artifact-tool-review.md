# 2026-10-08 最终载荷与工具入口修复

八项报告均成立，修改与验证如下。

| 项目 | 修改与证据 |
| --- | --- |
| 新固件目录混用旧FIT | 验收入口改为最终FIT选定配置。dumpimage提取kernel/fdt/script，验证每项SHA256并解压；读取实际内核里的完整newc initramfs，核对必需文件、原机校准、0600配置及全部外置普通文件/符号链接。实际验证器拒绝新版目录配旧FIT和有效旧manifest。 |
| BOSA直接校准绕过停发 | I²C设备configure变为只读；唯一校准调用位于PON bosa事务入口，执行control_lock、停traffic、停MPCP后校准。 |
| NAPI尾部重开已关闭IRQ | 新增rx_lock/rx_active，NAPI完成与IRQ恢复在同一临界区；stop在该锁内清除active并屏蔽IRQ，在关闭QM前与尾部再次同步。实际poll分支和stop函数的两个受控交错均得到QM停止、IRQ使能0；旧版交错可重现非0使能。 |
| 旧工具路径/版本 | 当前工具统一读取port/sources.lock.json；证据来自evidence/rtl-research/data/receipts。verify_artifacts、make_boot_fit、导出和Kconfig维护均在实际当前树执行。原生用户态入口运行TYFZA1 OAM；Airoha/Rust入口独立移到port/scripts/reference。RAM FIT模板直接生成，不再读取旧工程产物的ITS。 |
| UCI单引号及字段编码 | 共用uci_config.quote/field，正确编码单引号，限制控制字符及UTF-8字节数。安装前使用锁定OpenWrt实际uci/libuci解析并逐项回读；空密码遵循libuci空option语义。干净构建先生成uci包，再准备运行时文件。实际测试单引号、反斜杠、双引号、UTF-8、空串及边界。 |
| OAM探测错误LLID证据 | 删除旧LLID参数与SO_PRIORITY选通。每个OAM设备公开只读llid_index；探测工具读取驱动绑定，接口重命名也保留真实LLID。实际工具的模型测试在renamed-oam接口返回LLID7，并拒绝旧四参数用法。 |
| 抓包误捕所有接口 | 在创建socket/输出文件前检查if_nametoindex非0；接收再次核对来源ifindex。实际执行错误接口名会失败，不生成pcap。 |
| 平台补丁fuzz | 从校验归档的Linux6.18.55原文件建立Git基线，应用原补丁的新增内容，再由git diff重新生成补丁；对原文件执行patch --dry-run --fuzz=0，五个文件均通过。 |

测试入口：scripts/test_artifact_inputs.py、test_capture_probe.py、test_rx_shutdown.py及前轮native/control/OAM测试。维护工具的54项主机测试通过，1项已有跳过。签名候选使用加密启用的当前U-Boot工具，验证本机bootloader公钥、三项载荷RSA签名和配置签名，并确认损坏配置签名被拒绝。密钥仅位于原私有目录，工具导出不复制它们。

实际运行Kconfig维护时同时清除了重复末尾符号和旧编译器标识，补齐当前配置闭包。随后发现旧kmod APK依赖原kernel ABI哈希；维护入口现在清理package/kernel/linux，下一次world重建模块包。失败及修复诊断保留于logs/package-install-diagnostic.log和kernel-package-refresh.log。

最终镜像和测试记录位于manifests/pon-audit-build.json及pon-audit-tests.json。光猫离线，这些结果分别证明实际文件、代码模型、隔离用户态和构建流程；本轮未进行硬件中断竞态、光前端在线校准或OLT注册测试。

最终后台构建服务为ponwrt-tyfza1-lan-20261008-140435，成功退出。RAM载荷位于artifacts/full-pon-audit-final-6.18.55/ram-pon/image.itb，SHA256为deae61b6e06987ceb9caed813cca8587472c7d56f4e1cda31dbe853cfbb995a5。验收从该FIT提取19304456字节内核及DTB，解析966个initramfs条目并逐文件对照，实际内置pon-oamd通过13项隔离测试。其配置和三个载荷RSA签名通过验证，损坏配置签名被拒绝。当前导出入口的完整结果保存在artifacts/current-final-tool-export/，主机测试记录包含54项测试、1项既有跳过。
