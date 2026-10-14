ENTRY(pe_entry)
SECTIONS
{
    . = 0xbfc00000;
    .entry : { *(.entry) }
    . = 0xbfc00380;
    .exception : { *(.exception) }
    . = 0xbfc00400;
    .text : { *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    ASSERT(. <= 0xbfc02000, "PE code overlaps mailbox")
    . = 0xbfc02400;
    .rxq : { *(.rxq) }
    /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.comment) *(.note*) }
}
ASSERT(. <= 0xbfc04000, "PE code overlaps request ring")
