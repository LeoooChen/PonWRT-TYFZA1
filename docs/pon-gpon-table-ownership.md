# GPON 表读回按 PLOAM 与软件的字段归属校验

本次修复 GPON T-CONT/GEM 表读回的误报，不改寄存器写入顺序，也不重试或
吞掉失败。GPON 有 PLOAM 与软件分别管理的字段，不能把所有读回位都当成
本次软件写入的结果。

本机原厂 `ca-ne.ko` 的 `aal_gpon_tcont_set@0xf7b60` 在读回后核对
T-CONT index 和 OMCI enable，不把 PLOAM enable 当成写入成功条件。
`aal_gpon_ds_gem_port_set@0xf80c0` 对应核对 GEM index 与 valid，不核对
AES。原厂 `ca-rtk.ko` 的 `dal_rt_rtl8277c_gpon_dsFlow_set@0x1e950`
在 GPON 分支传入 `sw_aes=0`，且结构里的 AES 为0；XGTC 分支调用的是
另一套 XGEM setter。这些来自本机二进制，其他 SDK 只用于辨认字段名称。

原生两个 helper 原先把 PLOAM enable/AES 一并用于读回比较。当相应软件
控制位为0，而线路管理的字段保持1时，原厂 setter 返回成功，原生返回
`-EIO`。GEM 情况会使正在开通或替换的 OMCI 业务表失败；T-CONT 情况
也会把独立的 PLOAM 状态误判成软件映射写入失败。

修复按访问命令 bit16 的归属校验：

| 表 | 软件控制关闭时核对 | 软件控制开启时核对 |
| --- | --- | --- |
| GPON T-CONT | index、OMCI enable | 加上 PLOAM enable |
| GPON downstream GEM | index、valid | 加上 AES |

软件管理字段读回不一致仍返回 `-EIO`。XGTC helper 和 GPON 写入请求保持
不变，不加入兼容分支或扩大成功条件到映射字段之外。

`tools/trace_stock_gpon_ownership.py` 执行原厂两个 setter 的实际 ARM64
指令及当前原生 helper。边界模型负责间接表完成、bit16 控制的写入归属
和锁/日志调用，不代表硅片实测。修复前已记录两项原厂成功、原生 `-EIO`；
修复后8组比较的返回值、表值和 MMIO 写入序列相同。另4项错误注入确认
软件字段写坏、或显式软件控制的 PLOAM/AES 位写坏时仍报错。

生产 `path_program()` 的方向切换检查也采用已开启 AES 的 GPON GEM，
验证开通和重配不会因该位由 PLOAM 管理而失败。涉及的 GPON、ITU 控制及
业务表3个 ARM64 对象已编译；没有运行全量测试或构建全量固件。

证据位于 `artifacts/pon-gpon-session-20261016/` 与
`logs/stock-gpon-table-ownership.json`。本轮没有部署或改动实机配置；
目前接入的是 EPON，真实 GPON OLT 开通与加密业务仍未验证。
