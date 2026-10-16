
/home/leo/PonWRT-TYFZA1/evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko:     file format elf64-littleaarch64


Disassembly of section .text:

0000000000285a74 <halbb_watchdog_normal>:
  285a74:	a9be7bfd 	stp	x29, x30, [sp, #-32]!
  285a78:	910003fd 	mov	x29, sp
  285a7c:	f9000bf3 	str	x19, [sp, #16]
  285a80:	aa0003f3 	mov	x19, x0
  285a84:	94000000 	bl	285434 <halbb_cmn_info_self_update>
			285a84: R_AARCH64_CALL26	halbb_cmn_info_self_update
  285a88:	aa1303e0 	mov	x0, x19
  285a8c:	94000000 	bl	287fb8 <halbb_ic_hw_setting>
			285a8c: R_AARCH64_CALL26	halbb_ic_hw_setting
  285a90:	aa1303e0 	mov	x0, x19
  285a94:	94000000 	bl	2acd10 <halbb_env_mntr>
			285a94: R_AARCH64_CALL26	halbb_env_mntr
  285a98:	aa1303e0 	mov	x0, x19
  285a9c:	94000000 	bl	2d6eb4 <halbb_dig>
			285a9c: R_AARCH64_CALL26	halbb_dig
  285aa0:	aa1303e0 	mov	x0, x19
  285aa4:	94000000 	bl	2d1544 <halbb_pmac_statistics>
			285aa4: R_AARCH64_CALL26	halbb_pmac_statistics
  285aa8:	aa1303e0 	mov	x0, x19
  285aac:	94000000 	bl	299c2c <halbb_basic_dbg_message>
			285aac: R_AARCH64_CALL26	halbb_basic_dbg_message
  285ab0:	aa1303e0 	mov	x0, x19
  285ab4:	94000000 	bl	2c60dc <halbb_physts_watchdog>
			285ab4: R_AARCH64_CALL26	halbb_physts_watchdog
  285ab8:	3973f660 	ldrb	w0, [x19, #3325]
  285abc:	35000180 	cbnz	w0, 285aec <halbb_watchdog_normal+0x78>
  285ac0:	f9469660 	ldr	x0, [x19, #3368]
  285ac4:	37b800c0 	tbnz	w0, #23, 285adc <halbb_watchdog_normal+0x68>
  285ac8:	aa1303e0 	mov	x0, x19
  285acc:	94000000 	bl	285a28 <halbb_reset>
			285acc: R_AARCH64_CALL26	halbb_reset
  285ad0:	f9400bf3 	ldr	x19, [sp, #16]
  285ad4:	a8c27bfd 	ldp	x29, x30, [sp], #32
  285ad8:	d65f03c0 	ret
  285adc:	90000000 	adrp	x0, 0 <pci_cache_wback>
			285adc: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0x999a8
  285ae0:	91000000 	add	x0, x0, #0x0
			285ae0: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0x999a8
  285ae4:	94000000 	bl	0 <printk>
			285ae4: R_AARCH64_CALL26	printk
  285ae8:	17fffff8 	b	285ac8 <halbb_watchdog_normal+0x54>
  285aec:	aa1303e0 	mov	x0, x19
  285af0:	94000000 	bl	2a4430 <halbb_edcca>
			285af0: R_AARCH64_CALL26	halbb_edcca
  285af4:	aa1303e0 	mov	x0, x19
  285af8:	94000000 	bl	2a1b4c <halbb_dfs>
			285af8: R_AARCH64_CALL26	halbb_dfs
  285afc:	aa1303e0 	mov	x0, x19
  285b00:	94000000 	bl	28fb44 <halbb_cfo_watchdog>
			285b00: R_AARCH64_CALL26	halbb_cfo_watchdog
  285b04:	aa1303e0 	mov	x0, x19
  285b08:	94000000 	bl	2cd3bc <halbb_ra_watchdog>
			285b08: R_AARCH64_CALL26	halbb_ra_watchdog
  285b0c:	aa1303e0 	mov	x0, x19
  285b10:	94000000 	bl	2c7634 <halbb_pwr_ctrl>
			285b10: R_AARCH64_CALL26	halbb_pwr_ctrl
  285b14:	aa1303e0 	mov	x0, x19
  285b18:	94000000 	bl	2b2e60 <halbb_la_re_trig_watchdog>
			285b18: R_AARCH64_CALL26	halbb_la_re_trig_watchdog
  285b1c:	aa1303e0 	mov	x0, x19
  285b20:	94000000 	bl	2d3820 <halbb_antenna_diversity>
			285b20: R_AARCH64_CALL26	halbb_antenna_diversity
  285b24:	aa1303e0 	mov	x0, x19
  285b28:	94000000 	bl	2da194 <halbb_path_diversity>
			285b28: R_AARCH64_CALL26	halbb_path_diversity
  285b2c:	f9400660 	ldr	x0, [x19, #8]
  285b30:	91401261 	add	x1, x19, #0x4, lsl #12
  285b34:	7944e021 	ldrh	w1, [x1, #624]
  285b38:	790e5001 	strh	w1, [x0, #1832]
  285b3c:	aa1303e0 	mov	x0, x19
  285b40:	94000000 	bl	2d8dec <halbb_mccdm_switch>
			285b40: R_AARCH64_CALL26	halbb_mccdm_switch
  285b44:	aa1303e0 	mov	x0, x19
  285b48:	94000000 	bl	285a28 <halbb_reset>
			285b48: R_AARCH64_CALL26	halbb_reset
  285b4c:	17ffffe1 	b	285ad0 <halbb_watchdog_normal+0x5c>
