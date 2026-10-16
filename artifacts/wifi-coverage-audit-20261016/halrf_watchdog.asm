
/home/leo/PonWRT-TYFZA1/evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko:     file format elf64-littleaarch64


Disassembly of section .text:

00000000002f7194 <halrf_watchdog>:
  2f7194:	a9be7bfd 	stp	x29, x30, [sp, #-32]!
  2f7198:	910003fd 	mov	x29, sp
  2f719c:	f9000bf3 	str	x19, [sp, #16]
  2f71a0:	aa0003f3 	mov	x19, x0
  2f71a4:	52800001 	mov	w1, #0x0                   	// #0
  2f71a8:	94000000 	bl	2f6aa0 <halrf_get_thermal>
			2f71a8: R_AARCH64_CALL26	halrf_get_thermal
  2f71ac:	39012e60 	strb	w0, [x19, #75]
  2f71b0:	52800021 	mov	w1, #0x1                   	// #1
  2f71b4:	aa1303e0 	mov	x0, x19
  2f71b8:	94000000 	bl	2f6aa0 <halrf_get_thermal>
			2f71b8: R_AARCH64_CALL26	halrf_get_thermal
  2f71bc:	39013260 	strb	w0, [x19, #76]
  2f71c0:	aa1303e0 	mov	x0, x19
  2f71c4:	94000000 	bl	2f5f48 <halrf_tssi_ant_open>
			2f71c4: R_AARCH64_CALL26	halrf_tssi_ant_open
  2f71c8:	aa1303e0 	mov	x0, x19
  2f71cc:	94000000 	bl	2f5e70 <halrf_tssi_tracking>
			2f71cc: R_AARCH64_CALL26	halrf_tssi_tracking
  2f71d0:	aa1303e0 	mov	x0, x19
  2f71d4:	94000000 	bl	2f5d98 <halrf_dpk_tracking>
			2f71d4: R_AARCH64_CALL26	halrf_dpk_tracking
  2f71d8:	aa1303e0 	mov	x0, x19
  2f71dc:	94000000 	bl	2f7128 <halrf_iqk_tracking>
			2f71dc: R_AARCH64_CALL26	halrf_iqk_tracking
  2f71e0:	aa1303e0 	mov	x0, x19
  2f71e4:	94000000 	bl	2f5a1c <halrf_lck_tracking>
			2f71e4: R_AARCH64_CALL26	halrf_lck_tracking
  2f71e8:	aa1303e0 	mov	x0, x19
  2f71ec:	94000000 	bl	2f57e4 <halrf_rx_dck_tracking>
			2f71ec: R_AARCH64_CALL26	halrf_rx_dck_tracking
  2f71f0:	aa1303e0 	mov	x0, x19
  2f71f4:	94000000 	bl	2f5adc <halrf_op5k_tracking>
			2f71f4: R_AARCH64_CALL26	halrf_op5k_tracking
  2f71f8:	f9400bf3 	ldr	x19, [sp, #16]
  2f71fc:	a8c27bfd 	ldp	x29, x30, [sp], #32
  2f7200:	d65f03c0 	ret
