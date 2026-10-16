
/home/leo/PonWRT-TYFZA1/evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko:     file format elf64-littleaarch64


Disassembly of section .text:

00000000001c5b80 <rtw_hal_watchdog>:
  1c5b80:	a9be7bfd 	stp	x29, x30, [sp, #-32]!
  1c5b84:	910003fd 	mov	x29, sp
  1c5b88:	a90153f3 	stp	x19, x20, [sp, #16]
  1c5b8c:	aa0003f4 	mov	x20, x0
  1c5b90:	52800001 	mov	w1, #0x0                   	// #0
  1c5b94:	94000000 	bl	1b7cd0 <rtw_hal_bb_watchdog>
			1c5b94: R_AARCH64_CALL26	rtw_hal_bb_watchdog
  1c5b98:	34000220 	cbz	w0, 1c5bdc <rtw_hal_watchdog+0x5c>
  1c5b9c:	2a0003f3 	mov	w19, w0
  1c5ba0:	90000000 	adrp	x0, 9638 <rtw_drv_stop_prim_iface+0x194>
			1c5ba0: R_AARCH64_ADR_PREL_PG_HI21	phl_log_components
  1c5ba4:	b9400000 	ldr	w0, [x0]
			1c5ba4: R_AARCH64_LDST32_ABS_LO12_NC	phl_log_components
  1c5ba8:	36000220 	tbz	w0, #0, 1c5bec <rtw_hal_watchdog+0x6c>
  1c5bac:	90000000 	adrp	x0, 963c <rtw_drv_stop_prim_iface+0x198>
			1c5bac: R_AARCH64_ADR_PREL_PG_HI21	phl_log_level
  1c5bb0:	39400000 	ldrb	w0, [x0]
			1c5bb0: R_AARCH64_LDST8_ABS_LO12_NC	phl_log_level
  1c5bb4:	71000c1f 	cmp	w0, #0x3
  1c5bb8:	540001a9 	b.ls	1c5bec <rtw_hal_watchdog+0x6c>  // b.plast
  1c5bbc:	2a1303e2 	mov	w2, w19
  1c5bc0:	90000001 	adrp	x1, 0 <pci_cache_wback>
			1c5bc0: R_AARCH64_ADR_PREL_PG_HI21	.rodata+0x178c0
  1c5bc4:	91000021 	add	x1, x1, #0x0
			1c5bc4: R_AARCH64_ADD_ABS_LO12_NC	.rodata+0x178c0
  1c5bc8:	91028021 	add	x1, x1, #0xa0
  1c5bcc:	90000000 	adrp	x0, 0 <pci_cache_wback>
			1c5bcc: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0x64798
  1c5bd0:	91000000 	add	x0, x0, #0x0
			1c5bd0: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0x64798
  1c5bd4:	94000000 	bl	0 <printk>
			1c5bd4: R_AARCH64_CALL26	printk
  1c5bd8:	14000005 	b	1c5bec <rtw_hal_watchdog+0x6c>
  1c5bdc:	aa1403e0 	mov	x0, x20
  1c5be0:	94000000 	bl	1bf8ec <rtw_hal_rf_watchdog>
			1c5be0: R_AARCH64_CALL26	rtw_hal_rf_watchdog
  1c5be4:	2a0003f3 	mov	w19, w0
  1c5be8:	350000a0 	cbnz	w0, 1c5bfc <rtw_hal_watchdog+0x7c>
  1c5bec:	2a1303e0 	mov	w0, w19
  1c5bf0:	a94153f3 	ldp	x19, x20, [sp, #16]
  1c5bf4:	a8c27bfd 	ldp	x29, x30, [sp], #32
  1c5bf8:	d65f03c0 	ret
  1c5bfc:	90000000 	adrp	x0, 9638 <rtw_drv_stop_prim_iface+0x194>
			1c5bfc: R_AARCH64_ADR_PREL_PG_HI21	phl_log_components
  1c5c00:	b9400000 	ldr	w0, [x0]
			1c5c00: R_AARCH64_LDST32_ABS_LO12_NC	phl_log_components
  1c5c04:	3607ff40 	tbz	w0, #0, 1c5bec <rtw_hal_watchdog+0x6c>
  1c5c08:	90000000 	adrp	x0, 963c <rtw_drv_stop_prim_iface+0x198>
			1c5c08: R_AARCH64_ADR_PREL_PG_HI21	phl_log_level
  1c5c0c:	39400000 	ldrb	w0, [x0]
			1c5c0c: R_AARCH64_LDST8_ABS_LO12_NC	phl_log_level
  1c5c10:	71000c1f 	cmp	w0, #0x3
  1c5c14:	54fffec9 	b.ls	1c5bec <rtw_hal_watchdog+0x6c>  // b.plast
  1c5c18:	2a1303e2 	mov	w2, w19
  1c5c1c:	90000001 	adrp	x1, 0 <pci_cache_wback>
			1c5c1c: R_AARCH64_ADR_PREL_PG_HI21	.rodata+0x178c0
  1c5c20:	91000021 	add	x1, x1, #0x0
			1c5c20: R_AARCH64_ADD_ABS_LO12_NC	.rodata+0x178c0
  1c5c24:	91028021 	add	x1, x1, #0xa0
  1c5c28:	90000000 	adrp	x0, 0 <pci_cache_wback>
			1c5c28: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0x64798
  1c5c2c:	91000000 	add	x0, x0, #0x0
			1c5c2c: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0x64798
  1c5c30:	94000000 	bl	0 <printk>
			1c5c30: R_AARCH64_CALL26	printk
  1c5c34:	17ffffee 	b	1c5bec <rtw_hal_watchdog+0x6c>
