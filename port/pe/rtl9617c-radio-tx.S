/* SPDX-License-Identifier: GPL-2.0-only */
/* PE1 publishes native radio TXBD producers; RPQ keeps normal skb ownership. */
.set noreorder
.set noat
.text
.globl pe_entry
pe_entry:
    lui $t0, 0
    ori $t0, $t0, 0
    lw $s0, 8($t0)
    lui $t1, 0x5045
    ori $t1, $t1, 0x5458
    sw $t1, 0($t0)
loop:
    lw $t1, 12($t0)
    andi $t2, $t1, 1
    beq $t2, $zero, stopped
    nop
    sw $t1, 16($t0)
    addiu $t2, $t0, 32
    addiu $t3, $t0, 64
    addiu $t4, $t0, 96
    li $t5, 6
ring:
    lw $t6, 0($t2)
    lw $t7, 0($t3)
    beq $t6, $t7, next
    nop
    lw $t8, 0($t4)
    addu $t8, $t8, $s0
    sync
    sh $t6, 0($t8)
    sync
    sw $t6, 0($t3)
    lw $t9, 4($t0)
    addiu $t9, $t9, 1
    sw $t9, 4($t0)
next:
    addiu $t2, $t2, 4
    addiu $t3, $t3, 4
    addiu $t4, $t4, 4
    addiu $t5, $t5, -1
    bne $t5, $zero, ring
    nop
    b loop
    nop
stopped:
    sw $t1, 16($t0)
    b loop
    nop
.org 0x380
    lui $t0, 0
    ori $t0, $t0, 0
    mfc0 $t1, $13
    sw $t1, 20($t0)
    mfc0 $t1, $14
    sw $t1, 24($t0)
exception:
    b exception
    nop
