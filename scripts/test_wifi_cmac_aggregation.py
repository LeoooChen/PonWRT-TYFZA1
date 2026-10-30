#!/usr/bin/env python3
"""Check C/XB CCTL aggregation packing against the shipped encoder contract."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/role.h').read_text()
start = source.index('static inline void rtl_ax_station_cmac_connected(')
end = source.index('\nstatic inline ', start + 1)
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static void rtl_ax_store32(unsigned char *out, u32 value) { memcpy(out, &value, 4); }
'''
test = r'''
int main(void)
{
    for (unsigned int band = 0; band < 2; band++) {
        unsigned char plain[68], output[68];
        rtl_ax_station_cmac_connected(plain, 7, band, 0);
        for (unsigned int count = 1; count <= 64; count++) {
            u32 value, mask;
            rtl_ax_station_cmac_connected(output, 7, band, count);
            memcpy(&value, output + 16, 4);
            memcpy(&mask, output + 48, 4);
            /* Board HAL count-1, then C/XB V1 encoder decrements if nonzero. */
            unsigned int expected = count - 1;
            if (expected) expected--;
            assert(value == expected && mask == 0x3ff);
            memcpy(output + 16, plain + 16, 4);
            memcpy(output + 48, plain + 48, 4);
            assert(!memcmp(output, plain, sizeof(output)));
        }
    }
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='cmac-aggregation-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + source[start:end] + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
print('C/XB CCTL: counts1..64 match factory packing; unrelated fields unchanged')
