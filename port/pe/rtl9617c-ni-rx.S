/* SPDX-License-Identifier: GPL-2.0-only */
/* PE1 consumes its own EPP24 ring, traces eight frames, and returns EQ6 RAM. */
.set noreorder
.set noat
.text
.globl pe_entry
pe_entry:
    lui $t0, 0
    ori $t0, $t0, 0
    lui $s0, 0xb000
    lw $s1, 8($t0)
    lui $t1, 0xa000
    or $s1, $s1, $t1
    lw $s2, 12($t0)
    move $s3, $zero
    move $s4, $zero
    lui $t1, 0x5045
    ori $t1, $t1, 0x4e49
    sw $t1, 0($t0)
loop:
    addiu $s4, $s4, 1
    sw $s4, 4($t0)
    lw $t1, 16($t0)
    beq $t1, $zero, stopped
    nop
    lw $t1, 0x7060($s0)
    andi $t1, $t1, 1023
    beq $t1, $s2, loop
    nop
    sync
    addu $t2, $s1, $s2
    lw $t3, 0($t2)
    lw $t4, 4($t2)
    sw $t3, 28($t0)
    sw $t4, 32($t0)
    lui $t5, 0xffff
    ori $t5, $t5, 0xff80
    and $s5, $t3, $t5
    lui $t5, 0xa000
    or $t6, $s5, $t5
    andi $t5, $s3, 7
    sll $t5, $t5, 7
    addiu $t7, $t0, 256
    addu $t7, $t7, $t5
    li $t5, 32
copy:
    lw $t1, 0($t6)
    sw $t1, 0($t7)
    addiu $t6, $t6, 4
    addiu $t7, $t7, 4
    addiu $t5, $t5, -1
    bne $t5, $zero, copy
    nop
    sw $t3, -128($t7)
    sw $t4, -124($t7)
    sync
    addiu $s2, $s2, 8
    andi $s2, $s2, 1023
    sw $s2, 0x7160($s0)
ready:
    lw $t1, 0x6340($s0)
    bgez $t1, ready
    nop
    andi $t1, $t1, 0x3fff
    beq $t1, $zero, ready
    nop
    lw $t1, 0x6380($s0)
    bgez $t1, ready
    nop
    ori $t1, $s5, 6
    sync
    sw $t1, 0x6384($s0)
    addiu $s3, $s3, 1
    sw $s3, 20($t0)
    b loop
    nop
stopped:
    li $t1, 1
    sw $t1, 24($t0)
    b stopped
    nop
.org 0x380
    lui $t0, 0
    ori $t0, $t0, 0
    mfc0 $t1, $13
    sw $t1, 40($t0)
    mfc0 $t1, $14
    sw $t1, 44($t0)
    lui $t1, 0x5045
    ori $t1, $t1, 0x4558
    sw $t1, 48($t0)
exception:
    b exception
    nop
