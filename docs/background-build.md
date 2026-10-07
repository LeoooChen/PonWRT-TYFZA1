# 独立后台构建

Ubuntu user systemd service：`ponwrt-tyfza1-build`。用户 manager 已启用 Linger，关闭 Codex 不会终止构建；Ubuntu 虚拟机与 Mac 的 7897 代理需要继续运行。

任务范围：GNU 大包优先清华直连，其他来源走 7897；构建固定 OpenWrt main / RTL9617C target，然后保存 firmware 和 RAM FIT。后台不操作光猫。

在 Ubuntu 查看：

```sh
systemctl --user status ponwrt-tyfza1-build
tail -f /home/leo/PonWRT-TYFZA1/logs/background-build.log
cat /home/leo/PonWRT-TYFZA1/logs/background-status.json
```

产物：`artifacts/full-main-6.18.55/firmware/` 和 `artifacts/full-main-6.18.55/ram/image.itb`。目前 target 属于已通过实机启动、存储读取的基础系统，PON/Wi-Fi/硬件加速按后续优先级继续移植。
