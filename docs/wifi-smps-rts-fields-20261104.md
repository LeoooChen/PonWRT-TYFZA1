# Dynamic SMPS RTS descriptor correction

`rtl_ax_ra_peer` uses its private rate bit22 to request RTS/CTS for a dynamic-SMPS receiver. Both native WD builders previously translated that bit into WD byte32 bit12. That field is DATA_STBC. The resulting packet requested STBC and left the actual RTS fields zero.

The original TYFZA1 `rtk_wifi6.ko` (SHA256 `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`) encoders were executed for both C and XB with the same raw HT frame. Toggling only the source STBC flag changed WD word8 by `0x1000`. Toggling RTS_EN/HW_RTS_EN changed only WD word12 by `0x88000000`. The source metadata layout was obtained from `rtw_t_meta_data` and its bytes checked against the existing original metadata vectors.

The host WD builder and linked PE builder now write the RTS flags at byte48, bits27/31. Their LDPC, rate, payload, sequence and ownership fields retain their existing encoding. The generated PE header matches the tested binary.

Regression coverage updates the actual host WD helper and executes the linked PE for raw/encapsulated headers, four payload lengths and both SMPS states. The old linked PE fails the new STBC assertion; the corrected PE passes. Existing request, scatter DMA, RPQ completion, publication, BA and instruction-memory checks pass, and the ARM64 module builds.

Live validation used `rtl_ax_ap_rtsfix.ko` SHA256 `0b81fa60dfd2c6661526b72d6316ba38f4b60c1ef8593e211f8adeed4251f7f9`. First ordinary default TCP download received0.881Gbps with1278 retransmissions. A temporary diagnostic then enabled the private RTS flag for the existing Mac peer; download received1.302Gbps with0 retransmissions and the published WD showed word8=`00000800`, word12=`88000000`. Removing the diagnostic restored the original peer flags; the next default download received1.279Gbps with0 retransmissions. All rounds had no unmatched/polluted completion increase. The diagnostic module was unloaded.

The Mac's negotiated SMPS mode does not request this flag in ordinary traffic. This fixes a confirmed dynamic-SMPS descriptor defect; the current Mac's approximately1.3Gbps limit remains unresolved. The full1.6Gbps default TCP target remains active.

Original encoder outputs and live raw evidence are preserved under `artifacts/stock-rts-fields.json` and the Mac workspace `artifacts/wifi-throughput-20261104/RTSFIX-*`. No full firmware containing this correction has been flashed in this validation.
