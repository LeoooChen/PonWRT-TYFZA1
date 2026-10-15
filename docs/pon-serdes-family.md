# ITU 延迟同步后的 EPON CSR 交接

原生初始化在 ITU 模式临时释放 EPON CSR；如果初次同步成功，会按原厂
清除 GLOBAL_EPON_CNTL bit0，把 EPON CSR 收回复位。若初始化时 LOS，
稍后插入光纤而由周期工作检测到同步，原来的工作只更新状态，没有完成
这一步。因此相同 ITU 模式的最终复位状态取决于首次初始化时是否有光。

本机原厂 `aal_psds_out_of_reset@0xca700` 在 ITU SerDes 模式同步成功后
清除 EPON CSR bit0，失败时保留；三个 EPON 模式不会走这条清除路径。
原生周期工作现在在 ITU 从未同步转为同步时执行同一步，保留寄存器其余位。
LOS 或时钟未齐时不执行交接。

核对时还排除了编号混淆：PON 模式编号并非 SerDes 模式编号。实际执行
原厂转换、mode_init、sync 与 out_of_reset 后确认如下：

| 制式 | PON编号 | SerDes编号 | mode寄存器低位 |
| --- | --- | --- | --- |
| EPON1G | 0 | 6 | `0x40c` |
| EPON D10G | 1 | 4 | `0x44c` |
| EPON BI10G | 2 | 0 | `0x46c` |
| GPON | 3 | 3 | `0x408` |
| XG-PON | 4 | 2 | `0x448` |
| XGS-PON | 5 | 1 | `0x428` |

这六种模式均采用当前同步条件：`status & 0x9c01 == 0x9c00`。原厂 sync
中另有编号5/8/9不检查 bit12 的分支，但它们不是本项目六种模式的转换结果，
因此没有把该分支误加到 XGS-PON 上。

`trace_stock_pon_serdes.py` 的18组原厂指令执行覆盖全同步、LOS和缺少bit12；
profile文件读取成功、burst polarity主体和日志/延时由模型提供。生产
`rtl_pon_serdes_work()` 的定向检查先复现缺口，修复后六种制式的交接与
保留位检查通过，ASan/UBSan通过。受影响 ARM64 SerDes对象编译通过。

记录在 `manifests/pon-serdes-family.json`。没有全量测试、固件全量构建
或部署；没有匹配的 ITU OLT，不能据此声称实际插纤注册与时序已验证。
