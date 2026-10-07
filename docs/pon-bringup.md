# TYFZA1 原生 PON 移植

本机原厂配置选 EPON，速率选非对称10G。原生NI/DMA软件通路已通过100000包UDP序号校验和16MiB双向TCP验证，作为PON上行/下行接入Linux网络栈的基础。

rtl9617c-pon.c以Linux platform_driver绑定PON MAC，不依赖原厂ca-ne.ko。MAC资源4f5500000/0xc000来自本机原厂DTB的NI resource8，GLOBAL_PON_CNTL为4f4320094。新增pon-status DTS将此驱动与已验证的原生NI组合。DTB校验同时检查NI、DMA-LSO、QM AXI reorder与PON窗口和原厂对应关系。

第一轮实机读数：GLOBAL_PON_CNTL=0x0000030f，EPON_STATUS=0x00000021，signal_loss=1，PCS10G/PCS1G均0，MPCP注册LLID位图0。四核与原生NI保持运行。这里只证明PON平台资源和寄存器读取，注册、OAM和业务尚在移植。

原厂初始化调用链已定位：aal_pon_init → __epon_init → aal_psds_init → aal_epon_init；后者依次设置CSR/数据通路复位、MAC、FEC、SerDes burst、PUC、PDC、MPCP、加密、广播LLID映射和中断。Linux原生实现需要把OSAL锁/定时器/中断和SCFG参数转为内核API及OpenWrt配置。

SerDes参数实际来自原厂/etc/serdes/pon_serdes_*.txt。该机所有PON制式表均已保留；epon_d10g为320行，每行地址、值、读回字段、延时。目标ca-ne.ko的__psds_prof_load_from_file反汇编确认最多512行、地址低16位映射PON窗口、延时0时使用10us，源码与机器码一致。资料及源文件哈希见manifests/pon-serdes-profiles.json。

tools/prepare_pon_serdes.py把本机表转为原生固件记录，输出artifacts/pon-serdes-firmware。TPON格式为little-endian：4-byte magic、u32版本1、u32行数，其后每行u32寄存器偏移/u32值/u32延时us。下一步以Linux firmware API加载这些参数，并移植SerDes复位和gearbox序列；随后连接MPCP事件、OAM与NI业务队列。
