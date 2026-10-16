# 安装失败时持有并回收密钥CAM

## 根因及反例

旧实现直到CAM写入和DMAC角色更新都返回成功，才设置CAM分配位。若DMAC
命令已经生效但ACK超时，该角色仍引用新CAM，而软件分配器把该CAM当成空闲。
另一客户端安装密钥时会覆写它。原生函数反例的结果是：新客户端仍分到
CAM0，失败客户端的硬件角色指向新客户端的密钥；cleanup也没有待处理记录。

`artifacts/wifi-key-install-20261016/before.json` 记录该反例。它执行实际
原生函数，硬件命令边界显式注入“已执行、未收到ACK”，不是实机捕获的
跨客户端异常，也不能据此宣称此前高速UDP缺包就是由换钥失败触发。

## 原厂与Linux依据

原厂模块SHA256仍是
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`。
REA/Ghidra分析了8852C专用DMAC密钥插入/删除、`upt_dctl_secinfo_entry_8852c`
及独立PN写入函数；摘录、Evidence ID、原始输出哈希见 `rea-index.json`。

`tools/check_stock_key_pn.py` 执行原厂两个函数的实际ARM64打包指令，确认：
普通密钥选择更新的PN掩码为0；显式更新PN时才写低16位与高32位；独立
seciv写入不覆盖密钥选择字段。DMAC命令边界由回调提供，没有模拟固件或射频。
原厂插入代码提前修改软件缓存，失败没有完整回滚；本工程不复制这一缺口。

当前Linux 6.18.55的 `ieee80211_key_replace` 对同一keyidx先disable旧钥，
再enable新钥。驱动提供set_key、未声明Extended Key ID，cfg80211会拒绝
CCMP的非零PTK keyidx；mac80211也不会为提供set_key的驱动自动声明该能力。
因此本次正常PTK0失败恢复不存在需要复活的旧PTK。对应本地源码摘录及哈希
在 `mac80211-key-order.json`。这没有为不支持的双PTK切换增加兼容分支。

## 修复

选中空闲CAM后立即持有分配位，标记为未完成安装，再发硬件命令。CAM写入
失败也必须清理；若已经尝试DMAC更新，则记录对应MACID，cleanup先应用当前
目标角色解除失败CAM的引用，再清CAM，最后释放分配位。成功安装才发布
hw_key_idx、RX密钥指针和成功计数，并撤销清理标记。

复用上一轮的删除生命周期处理，清理时读取当前角色，保留之后安装的新
PTK/GTK；角色修正不写PN，也不重新启用已经被mac80211删除的旧PTK。
站点退出及MACID复用仍先解除pending关联。monitor启动已有的cleanup调度
会继续处理stop期间保留的条目，本次没有新加调度机制。

新增一个128位aborting位图及 `keys_aborted`，只在未完成安装的CAM最终
清理成功时计数；这类回收不计入成功密钥删除数，原错误照常返回并保留在
key_result中。速率、160MHz、队列和缓冲容量均未修改。

## 验证

实际原生函数在ASan/UBSan下覆盖CAM/DMAC命令执行与未执行两种超时结果、
清理再次失败、跨客户端槽位隔离、新PTK/GTK保留、同站点新PTK硬件PN保持、
abort统计；上一轮删除及MACID复用用例也通过。修复后相同反例的新客户端
使用CAM1，失败客户端引用解除，CAM0回收，新客户端密钥保留。

只构建外置ARM64无线模块，确认输入与当前无线源码一致，PE程序头与上一轮
保持一致。MODPOST仍有原有的缺少MODULE_DESCRIPTION提示。没有实机验证、
全量测试、全量固件构建或推送；PON及其他未提交以太网改动不在本次提交中。
