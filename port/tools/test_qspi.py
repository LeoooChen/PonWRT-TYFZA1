#!/usr/bin/env python3
"""Host regression tests of the actual PIO functions with mocked MMIO, not hardware."""
import subprocess
import tempfile
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / 'overlay/target/linux/rtl9617c/files-6.18/drivers/spi/spi-rtl9617c.c'
# Compile the production transaction functions unchanged. Only Linux services
# and MMIO are replaced; registration/probe are checked by the kernel build.
SHIM = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((UINT32_MAX >> (31 - (h))) & (UINT32_MAX << (l)))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define SPI_MEM_NO_DATA 0
#define SPI_MEM_DATA_IN 1
#define SPI_MEM_DATA_OUT 2
struct spi_controller { void *data; };
struct spi_device { struct spi_controller *controller; };
struct spi_mem { struct spi_device *spi; };
struct spi_mem_op {
    struct { unsigned nbytes, buswidth, opcode; bool dtr; } cmd;
    struct { unsigned nbytes, buswidth; uint64_t val; bool dtr; } addr;
    struct { unsigned nbytes, buswidth; bool dtr; } dummy;
    struct { unsigned nbytes, buswidth, dir; bool dtr;
             union { void *in; const void *out; } buf; } data;
};
static void *spi_controller_get_devdata(struct spi_controller *c) { return c->data; }
static bool spi_mem_default_supports_op(struct spi_mem *m, const struct spi_mem_op *o)
{ (void)m; (void)o; return true; }
static u32 registers[20], kicks[600];
static unsigned nkicks, elapsed;
static bool timeout;
static void udelay(unsigned n) { elapsed += n; }
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 value, void *p)
{
    *(u32 *)p = value;
    if (p == &registers[0x10 / 4]) {
        kicks[nkicks++] = value;
        if (!timeout) *(u32 *)p = 0;
    }
}
#define readl_poll_timeout(p, v, condition, delay, limit) \
    ((v) = readl(p), (condition) ? 0 : -ETIMEDOUT)
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "failed: %s:%d: %s\n", \
    __FILE__, __LINE__, #x); return 1; } } while (0)
'''
TEST = r'''
int main(int argc, char **argv)
{
    struct rtl_qspi qspi = { .base = registers };
    struct spi_controller ctlr = { .data = &qspi };
    struct spi_device spi = { .controller = &ctlr };
    struct spi_mem mem = { .spi = &spi };
    u8 buffer[2052] = { 0 };
    struct spi_mem_op op = { .cmd = { .nbytes = 1, .buswidth = 1 } };
    CHECK(argc == 2);
    if (!strcmp(argv[1], "no-data")) {
        op.cmd.opcode = 0x13;
        op.addr.nbytes = 3; op.addr.buswidth = 1; op.addr.val = 0x12345;
        CHECK(rtl_qspi_supports_op(&mem, &op));
        CHECK(rtl_qspi_exec_op(&mem, &op) == 0);
        CHECK(nkicks == 1 && kicks[0] == BUSY_START);
        CHECK(registers[ADDR / 4] == 0x12345);
        CHECK(elapsed >= 10);
        op.cmd.opcode = 0xff; op.addr.nbytes = 0;
        CHECK(rtl_qspi_exec_op(&mem, &op) == 0);
        CHECK(nkicks == 2 && kicks[1] == BUSY_START);
    } else if (!strcmp(argv[1], "write-guard")) {
        unsigned rejected[] = { 0x06, 0x02, 0x32, 0x10, 0xd8, 0xc7 };
        for (unsigned i = 0; i < sizeof(rejected)/sizeof(rejected[0]); i++) {
            op.cmd.opcode = rejected[i];
            CHECK(rtl_qspi_exec_op(&mem, &op) == -EROFS);
        }
        CHECK(nkicks == 0);
        op.cmd.opcode = 0x1f; op.addr.nbytes = 1; op.addr.buswidth = 1;
        op.addr.val = 0xb0; op.data.nbytes = 1; op.data.buswidth = 1;
        op.data.dir = SPI_MEM_DATA_OUT; op.data.buf.out = buffer;
        CHECK(rtl_qspi_exec_op(&mem, &op) == 0);
        CHECK(kicks[0] == (BUSY_START | BUSY_WRITE));
        op.addr.val = 0x90;
        CHECK(rtl_qspi_exec_op(&mem, &op) == -EROFS);
        CHECK(nkicks == 1);
    } else if (!strcmp(argv[1], "read-tail")) {
        op.cmd.opcode = 0x03; op.addr.nbytes = 2; op.addr.buswidth = 1;
        op.dummy.nbytes = 1; op.dummy.buswidth = 1;
        op.data.nbytes = 7; op.data.buswidth = 1;
        op.data.dir = SPI_MEM_DATA_IN; op.data.buf.in = buffer + 1;
        memset(buffer, 0xa5, sizeof(buffer)); registers[DATA / 4] = 0x44332211;
        CHECK(rtl_qspi_exec_op(&mem, &op) == 0);
        CHECK(nkicks == 2 && kicks[0] == BUSY_START && kicks[1] == BUSY_START);
        CHECK(!memcmp(buffer + 1, "\x11\x22\x33\x44\x11\x22\x33", 7));
        CHECK(buffer[0] == 0xa5 && buffer[8] == 0xa5);
        CHECK(((registers[EXT_ACCESS / 4] >> 8) & 0x1fff) == 6);
        CHECK(registers[ACCESS / 4] == 0);
        op.data.nbytes = 2052;
        CHECK(rtl_qspi_adjust_op_size(&mem, &op) == 0 && op.data.nbytes == 2048);
        op.data.buswidth = 4;
        CHECK(!rtl_qspi_supports_op(&mem, &op));
    } else if (!strcmp(argv[1], "dirmap-template")) {
        op.cmd.opcode = 0x03;
        op.addr.nbytes = 2; op.addr.buswidth = 1;
        op.dummy.nbytes = 1; op.dummy.buswidth = 1;
        op.data.nbytes = 0; op.data.buswidth = 1;
        op.data.dir = SPI_MEM_DATA_IN;
        CHECK(rtl_qspi_supports_op(&mem, &op));
        op.cmd.opcode = 0x02; op.dummy.nbytes = 0;
        op.data.dir = SPI_MEM_DATA_OUT;
        CHECK(rtl_qspi_supports_op(&mem, &op));
        CHECK(nkicks == 0);
    } else if (!strcmp(argv[1], "timeout")) {
        op.cmd.opcode = 0x03; op.data.nbytes = 8; op.data.buswidth = 1;
        op.data.dir = SPI_MEM_DATA_IN; op.data.buf.in = buffer;
        timeout = true;
        CHECK(rtl_qspi_exec_op(&mem, &op) == -ETIMEDOUT);
        CHECK(nkicks == 1 && registers[ACCESS / 4] == 0);
    } else return 2;
    return 0;
}
'''


class QspiTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'qspi-test'
        source = DRIVER.read_text()
        source = source[source.index('#define TYPE'):source.index('static const struct spi_controller_mem_ops')]
        test = Path(cls.tmp.name) / 'qspi-test.c'
        test.write_text(SHIM + source + TEST)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-unused-function',
                        '-fsanitize=undefined', '-g', str(test), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_taurus_no_data_direction(self):
        self.run_case('no-data')

    def test_readonly_array_write_guard(self):
        self.run_case('write-guard')

    def test_read_tail_count_and_capabilities(self):
        self.run_case('read-tail')

    def test_spinand_dirmap_zero_length_template(self):
        self.run_case('dirmap-template')

    def test_timeout_releases_burst(self):
        self.run_case('timeout')


if __name__ == '__main__':
    unittest.main()
