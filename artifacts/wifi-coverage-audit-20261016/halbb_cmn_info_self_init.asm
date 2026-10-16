
/home/leo/PonWRT-TYFZA1/evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko:     file format elf64-littleaarch64


Disassembly of section .text:

00000000002b01d0 <halbb_cmn_info_self_init>:
  2b01d0:	a9be7bfd 	stp	x29, x30, [sp, #-32]!
  2b01d4:	910003fd 	mov	x29, sp
  2b01d8:	f9000bf3 	str	x19, [sp, #16]
  2b01dc:	aa0003f3 	mov	x19, x0
  2b01e0:	f9400400 	ldr	x0, [x0, #8]
  2b01e4:	b9400000 	ldr	w0, [x0]
  2b01e8:	35000660 	cbnz	w0, 2b02b4 <halbb_cmn_info_self_init+0xe4>
  2b01ec:	52800040 	mov	w0, #0x2                   	// #2
  2b01f0:	b90c1660 	str	w0, [x19, #3092]
  2b01f4:	b94c1660 	ldr	w0, [x19, #3092]
  2b01f8:	52800021 	mov	w1, #0x1                   	// #1
  2b01fc:	b90c1e61 	str	w1, [x19, #3100]
  2b0200:	528005c1 	mov	w1, #0x2e                  	// #46
  2b0204:	6a01001f 	tst	w0, w1
  2b0208:	54000c00 	b.eq	2b0388 <halbb_cmn_info_self_init+0x1b8>  // b.none
  2b020c:	52800040 	mov	w0, #0x2                   	// #2
  2b0210:	39308260 	strb	w0, [x19, #3104]
  2b0214:	f9469660 	ldr	x0, [x19, #3368]
  2b0218:	37e80c20 	tbnz	w0, #29, 2b039c <halbb_cmn_info_self_init+0x1cc>
  2b021c:	b2407fe0 	mov	x0, #0xffffffff            	// #4294967295
  2b0220:	f9068660 	str	x0, [x19, #3336]
  2b0224:	3930a67f 	strb	wzr, [x19, #3113]
  2b0228:	b90c2e7f 	str	wzr, [x19, #3116]
  2b022c:	52800021 	mov	w1, #0x1                   	// #1
  2b0230:	3930c261 	strb	w1, [x19, #3120]
  2b0234:	3930ca61 	strb	w1, [x19, #3122]
  2b0238:	52800042 	mov	w2, #0x2                   	// #2
  2b023c:	3934c262 	strb	w2, [x19, #3376]
  2b0240:	3934c67f 	strb	wzr, [x19, #3377]
  2b0244:	3934ce7f 	strb	wzr, [x19, #3379]
  2b0248:	12800000 	mov	w0, #0xffffffff            	// #-1
  2b024c:	3934d260 	strb	w0, [x19, #3380]
  2b0250:	12800000 	mov	w0, #0xffffffff            	// #-1
  2b0254:	791a6e60 	strh	w0, [x19, #3382]
  2b0258:	12800000 	mov	w0, #0xffffffff            	// #-1
  2b025c:	b90d3a60 	str	w0, [x19, #3384]
  2b0260:	91401260 	add	x0, x19, #0x4, lsl #12
  2b0264:	3909301f 	strb	wzr, [x0, #588]
  2b0268:	3909341f 	strb	wzr, [x0, #589]
  2b026c:	528000a3 	mov	w3, #0x5                   	// #5
  2b0270:	7904c803 	strh	w3, [x0, #612]
  2b0274:	39708263 	ldrb	w3, [x19, #3104]
  2b0278:	390a1403 	strb	w3, [x0, #645]
  2b027c:	390a1003 	strb	w3, [x0, #644]
  2b0280:	3933f661 	strb	w1, [x19, #3325]
  2b0284:	3930a261 	strb	w1, [x19, #3112]
  2b0288:	3930c662 	strb	w2, [x19, #3121]
  2b028c:	f9428e60 	ldr	x0, [x19, #1304]
  2b0290:	528001c1 	mov	w1, #0xe                   	// #14
  2b0294:	39000001 	strb	w1, [x0]
  2b0298:	aa1303e0 	mov	x0, x19
  2b029c:	94000000 	bl	2a5264 <halbb_edcca_dev_hw_cap>
			2b029c: R_AARCH64_CALL26	halbb_edcca_dev_hw_cap
  2b02a0:	aa1303e0 	mov	x0, x19
  2b02a4:	94000000 	bl	285280 <halbb_cmn_info_self_reset>
			2b02a4: R_AARCH64_CALL26	halbb_cmn_info_self_reset
  2b02a8:	f9400bf3 	ldr	x19, [sp, #16]
  2b02ac:	a8c27bfd 	ldp	x29, x30, [sp], #32
  2b02b0:	d65f03c0 	ret
  2b02b4:	7100081f 	cmp	w0, #0x2
  2b02b8:	54000200 	b.eq	2b02f8 <halbb_cmn_info_self_init+0x128>  // b.none
  2b02bc:	71000c1f 	cmp	w0, #0x3
  2b02c0:	54000320 	b.eq	2b0324 <halbb_cmn_info_self_init+0x154>  // b.none
  2b02c4:	7100041f 	cmp	w0, #0x1
  2b02c8:	54000340 	b.eq	2b0330 <halbb_cmn_info_self_init+0x160>  // b.none
  2b02cc:	7100101f 	cmp	w0, #0x4
  2b02d0:	54000360 	b.eq	2b033c <halbb_cmn_info_self_init+0x16c>  // b.none
  2b02d4:	7100141f 	cmp	w0, #0x5
  2b02d8:	540003c0 	b.eq	2b0350 <halbb_cmn_info_self_init+0x180>  // b.none
  2b02dc:	7100181f 	cmp	w0, #0x6
  2b02e0:	54000420 	b.eq	2b0364 <halbb_cmn_info_self_init+0x194>  // b.none
  2b02e4:	b94c1660 	ldr	w0, [x19, #3092]
  2b02e8:	52800241 	mov	w1, #0x12                  	// #18
  2b02ec:	6a01001f 	tst	w0, w1
  2b02f0:	540000e0 	b.eq	2b030c <halbb_cmn_info_self_init+0x13c>  // b.none
  2b02f4:	17ffffc1 	b	2b01f8 <halbb_cmn_info_self_init+0x28>
  2b02f8:	52800080 	mov	w0, #0x4                   	// #4
  2b02fc:	b90c1660 	str	w0, [x19, #3092]
  2b0300:	52800280 	mov	w0, #0x14                  	// #20
  2b0304:	b90c1a60 	str	w0, [x19, #3096]
  2b0308:	b94c1660 	ldr	w0, [x19, #3092]
  2b030c:	52800501 	mov	w1, #0x28                  	// #40
  2b0310:	6a01001f 	tst	w0, w1
  2b0314:	54000320 	b.eq	2b0378 <halbb_cmn_info_self_init+0x1a8>  // b.none
  2b0318:	52800041 	mov	w1, #0x2                   	// #2
  2b031c:	b90c1e61 	str	w1, [x19, #3100]
  2b0320:	17ffffb8 	b	2b0200 <halbb_cmn_info_self_init+0x30>
  2b0324:	52800100 	mov	w0, #0x8                   	// #8
  2b0328:	b90c1660 	str	w0, [x19, #3092]
  2b032c:	17fffff7 	b	2b0308 <halbb_cmn_info_self_init+0x138>
  2b0330:	52800200 	mov	w0, #0x10                  	// #16
  2b0334:	b90c1660 	str	w0, [x19, #3092]
  2b0338:	17ffffaf 	b	2b01f4 <halbb_cmn_info_self_init+0x24>
  2b033c:	52800400 	mov	w0, #0x20                  	// #32
  2b0340:	b90c1660 	str	w0, [x19, #3092]
  2b0344:	52800640 	mov	w0, #0x32                  	// #50
  2b0348:	b90c1a60 	str	w0, [x19, #3096]
  2b034c:	17ffffef 	b	2b0308 <halbb_cmn_info_self_init+0x138>
  2b0350:	52800400 	mov	w0, #0x20                  	// #32
  2b0354:	b90c1660 	str	w0, [x19, #3092]
  2b0358:	52800660 	mov	w0, #0x33                  	// #51
  2b035c:	b90c1a60 	str	w0, [x19, #3096]
  2b0360:	17ffffea 	b	2b0308 <halbb_cmn_info_self_init+0x138>
  2b0364:	52800080 	mov	w0, #0x4                   	// #4
  2b0368:	b90c1660 	str	w0, [x19, #3092]
  2b036c:	528002a0 	mov	w0, #0x15                  	// #21
  2b0370:	b90c1a60 	str	w0, [x19, #3096]
  2b0374:	17ffffe5 	b	2b0308 <halbb_cmn_info_self_init+0x138>
  2b0378:	3617f440 	tbz	w0, #2, 2b0200 <halbb_cmn_info_self_init+0x30>
  2b037c:	52800061 	mov	w1, #0x3                   	// #3
  2b0380:	b90c1e61 	str	w1, [x19, #3100]
  2b0384:	17ffff9f 	b	2b0200 <halbb_cmn_info_self_init+0x30>
  2b0388:	f27c001f 	tst	x0, #0x10
  2b038c:	52800080 	mov	w0, #0x4                   	// #4
  2b0390:	1a9f1400 	csinc	w0, w0, wzr, ne	// ne = any
  2b0394:	39308260 	strb	w0, [x19, #3104]
  2b0398:	17ffff9f 	b	2b0214 <halbb_cmn_info_self_init+0x44>
  2b039c:	39708262 	ldrb	w2, [x19, #3104]
  2b03a0:	b94c1e61 	ldr	w1, [x19, #3100]
  2b03a4:	90000000 	adrp	x0, 0 <pci_cache_wback>
			2b03a4: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0xa5e30
  2b03a8:	91000000 	add	x0, x0, #0x0
			2b03a8: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0xa5e30
  2b03ac:	94000000 	bl	0 <printk>
			2b03ac: R_AARCH64_CALL26	printk
  2b03b0:	f9469660 	ldr	x0, [x19, #3368]
  2b03b4:	36eff340 	tbz	w0, #29, 2b021c <halbb_cmn_info_self_init+0x4c>
  2b03b8:	52800305 	mov	w5, #0x18                  	// #24
  2b03bc:	2a0503e4 	mov	w4, w5
  2b03c0:	52800203 	mov	w3, #0x10                  	// #16
  2b03c4:	52800182 	mov	w2, #0xc                   	// #12
  2b03c8:	39708261 	ldrb	w1, [x19, #3104]
  2b03cc:	90000000 	adrp	x0, 0 <pci_cache_wback>
			2b03cc: R_AARCH64_ADR_PREL_PG_HI21	.rodata.str1.8+0xa5e50
  2b03d0:	91000000 	add	x0, x0, #0x0
			2b03d0: R_AARCH64_ADD_ABS_LO12_NC	.rodata.str1.8+0xa5e50
  2b03d4:	94000000 	bl	0 <printk>
			2b03d4: R_AARCH64_CALL26	printk
  2b03d8:	17ffff91 	b	2b021c <halbb_cmn_info_self_init+0x4c>

Disassembly of section .init.text:

Disassembly of section .exit.text:

Disassembly of section .plt:

Disassembly of section .text.ftrace_trampoline:
