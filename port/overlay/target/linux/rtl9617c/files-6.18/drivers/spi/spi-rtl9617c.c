// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native RTL9617C spi-mem PIO driver, single chip select / single data lane.
 * Register and Taurus transaction reference: Cortina Access Inc. 2020,
 * GPL-2.0+ spi-cortina-qspi.c in GPL-for-GP3000 at
 * 5090fbcb6ba743dd7b1314811ef557bad0460147. Not a vendor-kernel wrapper.
 */
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/spi/spi.h>
#include <linux/spi/spi-mem.h>

#define TYPE		0x0c
#define BUSY		0x10
#define ACCESS		0x30
#define EXT_ACCESS	0x34
#define ADDR		0x38
#define DATA		0x3c
#define TYPE_ADDR_MASK	GENMASK(10, 9)
#define TYPE_ADDR4	(2 << 9)
#define BUSY_START	BIT(1)
#define BUSY_WRITE	BIT(9)
#define BUSY_MASK	(BIT(16) | BUSY_START)
#define ACCESS_ADDR	(5 << 8)
#define ACCESS_EXT	(15 << 8)
#define ACCESS_TERM	BIT(12)
#define ACCESS_BURST	BIT(13)
#define EXT_ENABLE	BIT(31)
#define MAX_DATA	2048

static bool allow_writes;
module_param(allow_writes, bool, 0400);
MODULE_PARM_DESC(allow_writes,
	"Allow NAND program/erase (default off; requires verified ECC and recovery)");

struct rtl_qspi {
	void __iomem *base;
};

static int rtl_qspi_kick(struct rtl_qspi *qspi, bool out)
{
	u32 status;

	writel(BUSY_START | (out ? BUSY_WRITE : 0), qspi->base + BUSY);
	return readl_poll_timeout(qspi->base + BUSY, status,
				  !(status & BUSY_MASK), 1, 100000);
}

static bool rtl_qspi_supports_op(struct spi_mem *mem, const struct spi_mem_op *op)
{
	if (op->cmd.nbytes != 1 || op->cmd.buswidth != 1 || op->cmd.dtr ||
	    op->addr.dtr || op->dummy.dtr || op->data.dtr ||
	    op->addr.nbytes > 4 || op->dummy.nbytes > 8)
		return false;
	if ((op->addr.nbytes && op->addr.buswidth != 1) ||
	    (op->dummy.nbytes && op->dummy.buswidth != 1) ||
	    (op->data.nbytes && op->data.buswidth != 1))
		return false;
	if (!op->data.nbytes && (op->dummy.nbytes ||
	    (op->addr.nbytes && op->addr.nbytes != 3 && op->addr.nbytes != 4)))
		return false;
	return spi_mem_default_supports_op(mem, op);
}

static bool rtl_qspi_readonly_op(const struct spi_mem_op *op)
{
	// Permit SPI-NAND reads, reset and volatile feature setup; refuse WREN,
	// program-load, program-execute, erase and any unknown vendor command.
	switch (op->cmd.opcode) {
	case 0x9f: // READ_ID
	case 0x0f: // GET_FEATURE
	case 0x03: // READ_CACHE
	case 0x0b: // READ_CACHE_FAST
		return op->data.dir == SPI_MEM_DATA_IN;
	case 0x13: // PAGE_READ, into volatile chip cache
		return op->addr.nbytes == 3 && !op->data.nbytes;
	case 0xff: // RESET
		return !op->addr.nbytes && !op->data.nbytes;
	case 0x1f: // SET_FEATURE: protection, configuration, target configuration
		return op->addr.nbytes == 1 && op->data.nbytes == 1 &&
		       op->data.dir == SPI_MEM_DATA_OUT &&
		       (op->addr.val == 0xa0 || op->addr.val == 0xb0 || op->addr.val == 0xd0);
	default:
		return false;
	}
}

static int rtl_qspi_data(struct rtl_qspi *qspi, const struct spi_mem_op *op)
{
	bool out = op->data.dir == SPI_MEM_DATA_OUT;
	bool burst = op->data.nbytes > 4;
	unsigned int offset = 0;
	u32 access = ACCESS_EXT | (burst ? ACCESS_BURST : 0);
	u32 ext, word;
	int ret = 0;

	// COUNT-1, including wrap for absent address/dummy, follows the Taurus
	// SDK encoding. EXT_ACCESS holds the FULL data length, not a 4-byte chunk.
	ext = op->cmd.opcode | EXT_ENABLE |
	      (((op->addr.nbytes - 1) & 7) << 21) |
	      (((op->dummy.nbytes * 8 - 1) & 63) << 24) |
	      (((op->data.nbytes - 1) & 0x1fff) << 8);
	writel(access, qspi->base + ACCESS);
	writel(0, qspi->base + EXT_ACCESS);
	writel(ext, qspi->base + EXT_ACCESS);
	if (op->addr.nbytes)
		writel(op->addr.val, qspi->base + ADDR);

	while (offset < op->data.nbytes) {
		unsigned int n = min_t(unsigned int, op->data.nbytes - offset, 4);
		unsigned int i;

		if (out) {
			const u8 *buf = op->data.buf.out;

			word = 0;
			for (i = 0; i < n; i++)
				word |= (u32)buf[offset + i] << (8 * i);
			writel(word, qspi->base + DATA);
		}
		ret = rtl_qspi_kick(qspi, out);
		if (ret)
			break;
		if (!out) {
			u8 *buf = op->data.buf.in;

			word = readl(qspi->base + DATA);
			for (i = 0; i < n; i++)
				buf[offset + i] = word >> (8 * i);
		}
		offset += n;
	}
	// Always release burst CS, including on timeout; do not leave a partial
	// command live while the caller handles a failed transaction.
	if (burst)
		writel(ACCESS_EXT | ACCESS_TERM, qspi->base + ACCESS);
	writel(0, qspi->base + ACCESS);
	return ret;
}

static int rtl_qspi_exec_op(struct spi_mem *mem, const struct spi_mem_op *op)
{
	struct rtl_qspi *qspi = spi_controller_get_devdata(mem->spi->controller);
	u32 type;
	int ret;

	if (!allow_writes && !rtl_qspi_readonly_op(op))
		return -EROFS;
	if (op->data.nbytes)
		return rtl_qspi_data(qspi, op);
	type = readl(qspi->base + TYPE) & ~TYPE_ADDR_MASK;
	if (op->addr.nbytes == 4)
		type |= TYPE_ADDR4;
	writel(type, qspi->base + TYPE);
	writel(op->cmd.opcode | (op->addr.nbytes ? ACCESS_ADDR : 0),
	       qspi->base + ACCESS);
	if (op->addr.nbytes)
		writel(op->addr.val, qspi->base + ADDR);
	// Taurus issues command-only/address-only operations with WR_ACCESS clear,
	// including RESET and PAGE_READ. WR_ACCESS describes a data phase, not
	// whether the command changes chip state. Match the GPL Taurus sequence.
	ret = rtl_qspi_kick(qspi, false);
	if (!ret)
		udelay(10);
	writel(0, qspi->base + ACCESS);
	return ret;
}

static int rtl_qspi_adjust_op_size(struct spi_mem *mem, struct spi_mem_op *op)
{
	op->data.nbytes = min_t(unsigned int, op->data.nbytes, MAX_DATA);
	return 0;
}

static const struct spi_controller_mem_ops rtl_qspi_mem_ops = {
	.supports_op = rtl_qspi_supports_op,
	.adjust_op_size = rtl_qspi_adjust_op_size,
	.exec_op = rtl_qspi_exec_op,
};

static int rtl_qspi_probe(struct platform_device *pdev)
{
	struct spi_controller *ctlr;
	struct rtl_qspi *qspi;

	ctlr = devm_spi_alloc_host(&pdev->dev, sizeof(*qspi));
	if (!ctlr)
		return -ENOMEM;
	qspi = spi_controller_get_devdata(ctlr);
	qspi->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(qspi->base))
		return PTR_ERR(qspi->base);
	ctlr->bus_num = -1;
	ctlr->num_chipselect = 1;
	ctlr->bits_per_word_mask = SPI_BPW_MASK(8);
	ctlr->mem_ops = &rtl_qspi_mem_ops;
	ctlr->dev.of_node = pdev->dev.of_node;
	// Do not invent/program QSPI frequency, pinmux, reset or ECC. The NAND
	// core selects JEDEC geometry and chip-specific on-die ECC support.
	dev_info(&pdev->dev, "native PIO; NAND array writes %s\n",
		 allow_writes ? "enabled" : "blocked");
	return devm_spi_register_controller(&pdev->dev, ctlr);
}

static const struct of_device_id rtl_qspi_match[] = {
	{ .compatible = "realtek,rtl9617c-qspi" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_qspi_match);

static struct platform_driver rtl_qspi_driver = {
	.probe = rtl_qspi_probe,
	.driver = { .name = "rtl9617c-qspi", .of_match_table = rtl_qspi_match },
};
module_platform_driver(rtl_qspi_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Native RTL9617C QSPI (Taurus PIO, writes blocked by default)");
