
/home/leo/PonWRT-TYFZA1/evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko:     file format elf64-littleaarch64


Disassembly of section .text:

000000000028fb44 <halbb_cfo_watchdog>:
  28fb44:	a9be7bfd 	stp	x29, x30, [sp, #-32]!
  28fb48:	910003fd 	mov	x29, sp
  28fb4c:	f9000bf3 	str	x19, [sp, #16]
  28fb50:	aa0003f3 	mov	x19, x0
  28fb54:	94000000 	bl	28f850 <halbb_cfo_acc_mode_en>
			28fb54: R_AARCH64_CALL26	halbb_cfo_acc_mode_en
  28fb58:	12001c00 	and	w0, w0, #0xff
  28fb5c:	350000e0 	cbnz	w0, 28fb78 <halbb_cfo_watchdog+0x34>
  28fb60:	f9469660 	ldr	x0, [x19, #3368]
  28fb64:	37300100 	tbnz	w0, #6, 28fb84 <halbb_cfo_watchdog+0x40>
  28fb68:	aa1303e0 	mov	x0, x19
  28fb6c:	94000000 	bl	28f3d0 <halbb_cfo_dm>
			28fb6c: R_AARCH64_CALL26	halbb_cfo_dm
  28fb70:	f9469660 	ldr	x0, [x19, #3368]
  28fb74:	37300160 	tbnz	w0, #6, 28fba0 <halbb_cfo_watchdog+0x5c>
  28fb78:	f9400bf3 	ldr	x19, [sp, #16]
  28fb7c:	a8c27bfd 	ldp	x29, x30, [sp], #32
  28fb80:	d65f03c0 	ret
  28fb84:	90000001 	adrp	x1, 0 <pci_cache_wback>
			28fb84: R_AARCH64_ADR_PREL_PG_HI21	.rodata+0x22f48
  28fb88:	91000021 	add	x1, x1, #0x0
			28fb88: R_AARCH64_ADD_ABS_LO12_NC	.rodata+0x22f48
  28fb8c:	91066021 	add	x1, x1, #0x198
  28fb90:	90000000 	adrp	x0, 0 <pci_cache_wback>
			28fb90: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0x9ae58
  28fb94:	91000000 	add	x0, x0, #0x0
			28fb94: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0x9ae58
  28fb98:	94000000 	bl	0 <printk>
			28fb98: R_AARCH64_CALL26	printk
  28fb9c:	17fffff3 	b	28fb68 <halbb_cfo_watchdog+0x24>
  28fba0:	90000000 	adrp	x0, 0 <pci_cache_wback>
			28fba0: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0x9b750
  28fba4:	91000000 	add	x0, x0, #0x0
			28fba4: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0x9b750
  28fba8:	94000000 	bl	0 <printk>
			28fba8: R_AARCH64_CALL26	printk
  28fbac:	17fffff3 	b	28fb78 <halbb_cfo_watchdog+0x34>
