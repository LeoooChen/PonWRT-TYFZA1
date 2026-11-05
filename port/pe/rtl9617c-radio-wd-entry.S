/* SPDX-License-Identifier: GPL-2.0-only */
.set noreorder
.set noat
.section .entry,"ax"
.globl pe_entry
pe_entry:
    lui $a0, 0
    ori $a0, $a0, 0
    lui $sp, 0
    ori $sp, $sp, 0xfff0
    mfc0 $t0, $20
    sw $t0, 208($a0)
    li $t1, -3
    and $t1, $t0, $t1
    mtc0 $t1, $20
    nop
    nop
    nop
    nop
    nop
    nop
    ori $t1, $t1, 2
    mtc0 $t1, $20
    nop
    nop
    nop
    nop
    nop
    nop
    # The board's DSP uses28-bit CP3 addresses and a32KiB instruction window.
    mfc0 $t0, $12
    lui $t1, 0x8000
    or $t0, $t0, $t1
    mtc0 $t0, $12
    nop
    nop
    nop
    nop
    nop
    nop
    lui $t0, 0x0fc0
    .word 0x4c880000 # mtc3 t0,register0: instruction start
    ori $t0, $t0, 0x7fff
    .word 0x4c880800 # mtc3 t0,register1: instruction end
    # Stock DMEM is8KiB. Only the private stack belongs in this window.
    lui $t0, 0x0fff
    ori $t0, $t0, 0xe000
    and $t0, $sp, $t0
    .word 0x4c882000 # mtc3 t0,register4: data start
    ori $t0, $t0, 0x1fff
    .word 0x4c882800 # mtc3 t0,register5: data end
    mfc0 $t0, $20
    li $t1, -113
    and $t0, $t0, $t1
    mtc0 $t0, $20
    nop
    nop
    nop
    ori $t0, $t0, 0x10
    mtc0 $t0, $20
    nop
    nop
    nop
    ori $t0, $t0, 0x440
    mtc0 $t0, $20
    nop
    nop
    nop
    nop
    nop
    nop
    mfc0 $t0, $20
    sw $t0, 216($a0)
    sw $sp, 244($a0)
    # ATU entry5 supplies the physical28-bit fill/execution alias.
    lui $t9, 0x8fc0
    ori $t9, $t9, 0x0400
    sw $t9, 212($a0)
    jr $t9
    nop
halt:
    b halt
    nop
.section .exception,"ax"
    lui $t0, 0
    ori $t0, $t0, 0
    mfc0 $t1, $13
    sw $t1, 20($t0)
    mfc0 $t1, $14
    sw $t1, 24($t0)
exception:
    b exception
    nop
