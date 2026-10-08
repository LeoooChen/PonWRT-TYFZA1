# TYFZA1 光前端原生移植

2026-10-08，新增Linux I2C adapter、pinctrl和I2C BOSA client。原厂S3启动脚本调用rtkbosa，读取本机/var/config/rtkbosa_k.bin；OpenWrt现在从Linux firmware API加载同一份1024字节校准文件。S25pon先初始化光前端，再初始化SerDes/MAC/队列/管理通路。

## 板级资源

- BIW0：4f4329160，窗口28，APB125MHz，I2C100kHz，prescale249；对应本机原厂i2c@f4329160。
- pinctrl：4f43200f0，窗口24。GPIO_204、GPIO_205选择multi-fn时清除GPIO_MUX_2的位4/5；PMUX_120设置GLOBAL_PIN_MUX_NEW的位20。原厂DT的三个引脚与新DT逐项核对。
- BOSA：I2C地址51。PON通过设备树phandle关联光前端，Linux device_link记录其供应关系。驱动加载不写校准；用户态启动通过PON的bosa属性执行初始化。

适配使用I2C_RDWR/SMBus标准接口，支持重复START、逐字节ACK/NACK、STOP和错误返回。BOSA识别依据芯片页FF的身份和页2的型号位，当前实现GN25L95、GN25L98、GN28L95、GN28L96、GN28L97；本机已实际读回GN28L97（型号205）、EEPROM=0。GN28L96/97的EEPROM-present位在原机程序中为反向含义，已按机器码实现。

## 校准数据与序列验证

本机rtkbosa ELF SHA256：9b7b5325df846f70d527711186d661ac4398d4416499ce66d55cc02860b4d757。
本机通用校准SHA256：07f4014678ce52bee03b0aa2f9e2eabca85419402fcccad6b5ad07e6b18e3b5b；rtkbosa_k.bin-factory与之相同。

tools/trace_stock_bosa.py仅在Unicorn中执行原机初始化函数，用模拟I2C和文件读取记录轨迹。它不访问真实总线。scripts/test_bosa_sequence.py将原机轨迹与原生C序列逐操作比较：5个型号、原机校准和独立字节模式共10组全部一致，ASan/UBSan通过。GN25/28L95路径639次写和7次读，GN28L96/97路径597次写和2次读。数据文件与读取后的现场状态不进入Git；序列实现、验证工具和哈希记录版本管理。

通过上述测试的是初始化顺序、数据索引和读改写行为。芯片实际应答、校准完成、DDMI数值及正确上联光口的同步/注册需要下一轮板测。FTTR输出光口使用另一个原厂rtkoltbosa_k.bin文件及独立控制通路，后续单独移植。

## 板测入口

RAM启动后先读取：

```sh
cat /sys/bus/platform/devices/4f5500000.pon/bosa
cat /sys/bus/i2c/drivers/rtl9617c-bosa/*-0051/diagnostics
cat /sys/bus/platform/devices/4f5500000.pon/serdes
cat /sys/bus/platform/devices/4f5500000.pon/state
cat /sys/bus/platform/devices/4f5500000.pon/mpcp
```

LOID已保存在项目私有analysis/private/pon-auth.json，镜像生成工具写入/etc/config/pon，WAN选择DHCP。下一轮继续原生OAM发现/LOID认证及业务netdev，在正确的上联光口测真实注册、认证和上游地址获取。

完整main/GCC14.4/musl构建成功，最新RAM验证镜像为artifacts/full-bosa-luci03-6.18.55/ram-pon/image.itb，SHA256=173fbcce88703d294baee59c94a9d6fe480426f8812317318da32af9373ea605。镜像内校准与原备份一致，六种参数文件、LuCI/rpcd/ACL和S25pon均已检查，PON配置文件权限0600；硬件尚未启动此镜像。构建清单见manifests/bosa-luci-build.json。

2026-10-08晚上实机完成599次校准写入；GPIO15为光发送关闭脚，已接入原生GPIO与PON控制事务。epon-d10g同步、MPCP注册、LOID认证、OLT FEC配置和无标签DHCP联网通过，见xepon-live-network.md。FTTR输出通路仍待独立移植。
