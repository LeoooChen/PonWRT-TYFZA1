#!/usr/bin/env python3
"""Compile the actual early UBI hook and exercise its volume/data safety gates."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
patch = (root / 'port/overlay/target/linux/rtl9617c/patches-6.18/230-tyfza1-rootfs-tail.patch').read_text()
added = '\n'.join(line[1:] for line in patch.splitlines()
                  if line.startswith('+') and not line.startswith('+++'))
start = added.index('/* Older TYFZA1')
end = added.index('\n}', added.index('static void tyfza1_trim_root')) + 3
hook = added[start:end]
prelude = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint64_t u64;
#define __init
#define __setup(a, b)
#define SQUASHFS_MAGIC 0x73717368
#define UBI_STATIC_VOLUME 2
#define DIV_ROUND_UP_ULL(n, d) (((n) + (d) - 1) / (d))
#define ubi_warn(ubi, ...) ((void)(ubi))
#define ubi_msg(ubi, ...) ((void)(ubi))
struct ubi_volume {
    const char *name;
    int upd_marker, reserved_pebs, usable_leb_size, vol_type, used_ebs;
    u64 used_bytes;
};
struct ubi_volume_desc { struct ubi_volume *vol; };
struct ubi_device {
    int ubi_num, ro_mode, leb_size;
    struct ubi_volume *volumes[16];
};
static struct ubi_device device;
static struct ubi_volume volumes[16];
static u8 superblock[96];
static const char *board;
static int reads, writes, read_error, resize_error, selected;
static u64 get_le(const void *data, int size) {
    const u8 *p = data; u64 value = 0;
    for (int i = 0; i < size; i++) value |= (u64)p[i] << (8 * i);
    return value;
}
#define get_unaligned_le16(p) get_le(p, 2)
#define get_unaligned_le32(p) get_le(p, 4)
#define get_unaligned_le64(p) get_le(p, 8)
static void put_le(int offset, u64 value, int size) {
    for (int i = 0; i < size; i++) superblock[offset + i] = value >> (8 * i);
}
static int of_machine_is_compatible(const char *value) { return !strcmp(board, value); }
static int kstrtoint(const char *text, int base, int *value) {
    char *end; long n = strtol(text, &end, base);
    if (!*text || *end || n > 2147483647 || n < -2147483647) return -EINVAL;
    *value = n; return 0;
}
static int ubi_read(struct ubi_volume_desc *desc, int leb, void *data, int offset, int size) {
    assert(desc->vol == &volumes[selected]);
    assert(!leb && !offset && size == 96);
    reads++; memcpy(data, superblock, size); return read_error;
}
static int ubi_resize_volume(struct ubi_volume_desc *desc, int pebs) {
    assert(desc->vol == &volumes[selected]);
    assert(pebs > 0 && pebs < desc->vol->reserved_pebs);
    writes++;
    if (!resize_error) desc->vol->reserved_pebs = pebs;
    return resize_error;
}
'''
program = r'''
static void reset(int slot) {
    memset(&device, 0, sizeof(device)); memset(volumes, 0, sizeof(volumes));
    memset(superblock, 0, sizeof(superblock));
    board = slot ? "tianyi,tyfza1-2p5g" : "tianyi,tyfza1";
    tyfza1_slot = -1; tyfza1_slot_setup(slot ? "1" : "0");
    selected = 4 + 3 * slot;
    for (int i = 0; i < 16; i++) {
        device.volumes[i] = &volumes[i]; volumes[i].name = "factory-untouched";
        volumes[i].reserved_pebs = 793;
    }
    device.leb_size = 126976;
    volumes[selected] = (struct ubi_volume){.name = slot ? "ubi_r1" : "ubi_r0",
        .reserved_pebs = 265, .usable_leb_size = 126976, .used_bytes = 265ULL * 126976};
    put_le(0, SQUASHFS_MAGIC, 4); put_le(28, 4, 2);
    put_le(40, 8067048, 8); put_le(48, 8067000, 8);
    put_le(64, 8060000, 8); put_le(72, 8061000, 8);
    reads = writes = read_error = resize_error = 0;
}
static void run(int expected_writes) {
    tyfza1_trim_root(&device);
    assert(writes == expected_writes);
    for (int i = 0; i < 16; i++)
        if (i != selected) assert(volumes[i].reserved_pebs == 793);
}
int main(void) {
    for (int slot = 0; slot < 2; slot++) {
        reset(slot); run(1); assert(volumes[selected].reserved_pebs == 64);
        writes = 0; run(0); /* An already-sized root has no flash write. */
        reset(slot); device.ubi_num = 1; run(0); assert(!reads);
        reset(slot); device.ro_mode = 1; run(0); assert(!reads);
        reset(slot); device.leb_size = 4096; run(0); assert(!reads);
        reset(slot); board = "unrelated,board"; run(0); assert(!reads);
        reset(slot); tyfza1_slot = -1; run(0); assert(!reads);
        reset(slot); tyfza1_slot = -1; tyfza1_slot_setup("2"); run(0);
        reset(slot); tyfza1_slot = -1; tyfza1_slot_setup("0garbage"); run(0);
        reset(slot); device.volumes[selected] = NULL; run(0); assert(!reads);
        reset(slot); volumes[selected].name = "ubi_Config"; run(0); assert(!reads);
        reset(slot); volumes[selected].upd_marker = 1; run(0); assert(!reads);
        reset(slot); read_error = -EIO; run(0);
        reset(slot); put_le(0, 0, 4); run(0);
        reset(slot); put_le(28, 5, 2); run(0);
        reset(slot); put_le(40, 96, 8); run(0);
        reset(slot); put_le(40, 0, 8); run(0);
        reset(slot); put_le(40, UINT64_MAX, 8); run(0);
        reset(slot); put_le(64, 10, 8); run(0);
        reset(slot); put_le(72, 8059999, 8); run(0);
        reset(slot); put_le(48, 8067049, 8); run(0);
        reset(slot); volumes[selected].vol_type = UBI_STATIC_VOLUME;
        volumes[selected].used_ebs = 65; run(0);
        reset(slot); volumes[selected].vol_type = UBI_STATIC_VOLUME;
        volumes[selected].used_ebs = 64; run(1);
        reset(slot); resize_error = -EIO; run(1);
        assert(volumes[selected].reserved_pebs == 265);
    }
    puts("Both slots: root bounds, idempotence, static data, errors and factory-volume isolation passed");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-root-tail-') as temporary:
    source = Path(temporary) / 'test.c'
    binary = Path(temporary) / 'test'
    source.write_text(prelude + hook + program)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
