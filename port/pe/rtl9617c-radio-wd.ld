ENTRY(pe_entry)
SECTIONS
{
    . = 0xbfc00000;
    .entry : { *(.entry) }
    . = 0xbfc00380;
    .exception : { *(.exception) }
    . = 0xbfc00400;
    .text : { *(.text.main) *(.text*) }
    ASSERT(pe_main == 0xbfc00400, "PE entry trampoline must target pe_main")
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    ASSERT(. <= 0xbfc02000, "PE code overlaps mailbox")
    . = 0xbfc02400;
    .rxq : { *(.rxq) }
    /* The -G0, -mno-abicalls, -fno-pic image has no GOT users. */
    /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.comment) *(.note*) *(.got) }
}
/* Code is loaded into the dedicated engine allocation. Per-radio request
 * rings use separate allocations; the existing IMEM window is 32KiB. */
ASSERT(. <= 0xbfc08000, "PE code exceeds instruction memory")
