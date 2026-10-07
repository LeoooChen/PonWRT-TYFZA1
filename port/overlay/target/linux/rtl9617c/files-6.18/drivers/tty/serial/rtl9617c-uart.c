// SPDX-License-Identifier: GPL-2.0-only
/*
 * RTL9617C UART0, rewritten for the serial core in Linux 6.18.
 * Register/baud/RX-valid reference: Copyright (C) Cortina Access 2015, GPLv2 driver,
 * serial_cortina.c/.h at GPL-for-GP3000 commit 5090fbcb6ba743dd7b1314811ef557bad0460147.
 * No SDK runtime, platform hooks or binary modules are used.
 */
#include <linux/clk.h>
#include <linux/console.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/serial_core.h>
#include <linux/tty_flip.h>

#define CFG		0x00
#define RX_SAMPLE	0x08
#define TX_DATA		0x10
#define RX_DATA		0x14
#define INFO		0x18
#define IRQ_EN		0x1c
#define IRQ_PENDING	0x24
#define CFG_BITS	GENMASK(1, 0)
#define CFG_STOP2	BIT(2)
#define CFG_EVEN		BIT(3)
#define CFG_PARITY	BIT(4)
#define CFG_TX		BIT(5)
#define CFG_RX		BIT(6)
#define CFG_ENABLE	BIT(7)
#define INFO_RX_EMPTY	BIT(1)
#define INFO_TX_FULL	BIT(2)
#define INFO_TX_EMPTY	BIT(3)
#define RX_VALID	BIT(8)
#define IRQ_FRAME	BIT(1)
#define IRQ_PARITY	BIT(2)
#define IRQ_OVERRUN	BIT(3)
#define IRQ_TX		BIT(5)
#define IRQ_RX		BIT(6)
#define IRQ_BREAK	BIT(7)
#define IRQ_MASK		GENMASK(7, 0)
/* Driver-local type, not a new userspace ABI value. */
#define PORT_RTL9617C	255

static struct uart_port rtl_port;
static struct uart_driver rtl_uart_driver;

static void rtl_irq_mask(struct uart_port *port, u32 bits, bool enable)
{
	u32 mask = readl(port->membase + IRQ_EN);

	writel(enable ? mask | bits : mask & ~bits, port->membase + IRQ_EN);
}

static unsigned int rtl_tx_empty(struct uart_port *port)
{
	return readl(port->membase + INFO) & INFO_TX_EMPTY ? TIOCSER_TEMT : 0;
}

static void rtl_set_mctrl(struct uart_port *port, unsigned int mctrl) { }

static unsigned int rtl_get_mctrl(struct uart_port *port)
{
	return TIOCM_CAR | TIOCM_DSR | TIOCM_CTS;
}

static void rtl_stop_tx(struct uart_port *port)
{
	rtl_irq_mask(port, IRQ_TX, false);
}

static void rtl_tx_chars(struct uart_port *port)
{
	u8 ch;

	uart_port_tx(port, ch, !(readl(port->membase + INFO) & INFO_TX_FULL),
		     writel(ch, port->membase + TX_DATA));
}

static void rtl_start_tx(struct uart_port *port)
{
	rtl_irq_mask(port, IRQ_TX, true);
	rtl_tx_chars(port);
}

static void rtl_stop_rx(struct uart_port *port)
{
	rtl_irq_mask(port, IRQ_RX, false);
}

static void rtl_rx_chars(struct uart_port *port, u32 status)
{
	unsigned int count = 256;

	while (count-- && !(readl(port->membase + INFO) & INFO_RX_EMPTY)) {
		u32 data = readl(port->membase + RX_DATA);
		u8 flag = TTY_NORMAL;

		if (!(data & RX_VALID))
			continue;
		port->icount.rx++;
		if (port->ignore_status_mask & IRQ_RX)
			continue;
		if (status & IRQ_BREAK) {
			port->icount.brk++;
			if (uart_handle_break(port))
				continue;
			flag = TTY_BREAK;
		} else if (status & IRQ_PARITY) {
			port->icount.parity++;
			flag = TTY_PARITY;
		} else if (status & IRQ_FRAME) {
			port->icount.frame++;
			flag = TTY_FRAME;
		}
		if (status & IRQ_OVERRUN)
			port->icount.overrun++;
		if (uart_handle_sysrq_char(port, data & 0xff))
			continue;
		status &= port->read_status_mask;
		uart_insert_char(port, status, IRQ_OVERRUN, data & 0xff, flag);
		// FIFO error bits are aggregate IRQ state, not per-character metadata.
		status = 0;
	}
	tty_flip_buffer_push(&port->state->port);
}

static irqreturn_t rtl_interrupt(int irq, void *arg)
{
	struct uart_port *port = arg;
	u32 status;

	uart_port_lock(port);
	status = readl(port->membase + IRQ_PENDING) & IRQ_MASK;
	if (!status) {
		uart_port_unlock(port);
		return IRQ_NONE;
	}
	// Stock Taurus driver acknowledges this W1C register before draining RX.
	writel(status, port->membase + IRQ_PENDING);
	rtl_rx_chars(port, status);
	if (readl(port->membase + IRQ_EN) & IRQ_TX)
		rtl_tx_chars(port);
	uart_port_unlock(port);
	return IRQ_HANDLED;
}

static int rtl_startup(struct uart_port *port)
{
	u32 cfg;
	int ret;

	writel(0, port->membase + IRQ_EN);
	writel(IRQ_MASK, port->membase + IRQ_PENDING);
	ret = request_irq(port->irq, rtl_interrupt, 0, dev_name(port->dev), port);
	if (ret)
		return ret;
	cfg = readl(port->membase + CFG);
	writel(cfg | CFG_ENABLE | CFG_TX | CFG_RX, port->membase + CFG);
	writel(IRQ_RX, port->membase + IRQ_EN);
	return 0;
}

static void rtl_shutdown(struct uart_port *port)
{
	writel(0, port->membase + IRQ_EN);
	writel(IRQ_MASK, port->membase + IRQ_PENDING);
	free_irq(port->irq, port);
}

static void rtl_set_termios(struct uart_port *port, struct ktermios *termios,
			    const struct ktermios *old)
{
	unsigned int baud, divisor, cfg;
	unsigned long flags;

	baud = uart_get_baud_rate(port, termios, old, 9600, 115200);
	divisor = port->uartclk / baud;
	cfg = (divisor << 8) | CFG_ENABLE | CFG_RX | CFG_TX;
	switch (termios->c_cflag & CSIZE) {
	case CS5: break;
	case CS6: cfg |= 1; break;
	case CS7: cfg |= 2; break;
	default: cfg |= CFG_BITS; break;
	}
	if (termios->c_cflag & CSTOPB)
		cfg |= CFG_STOP2;
	if (termios->c_cflag & PARENB) {
		cfg |= CFG_PARITY;
		if (!(termios->c_cflag & PARODD))
			cfg |= CFG_EVEN;
	}
	termios->c_cflag &= ~(CRTSCTS | CMSPAR);
	uart_port_lock_irqsave(port, &flags);
	port->read_status_mask = IRQ_OVERRUN;
	if (termios->c_iflag & INPCK)
		port->read_status_mask |= IRQ_PARITY | IRQ_FRAME;
	if (termios->c_iflag & (BRKINT | PARMRK))
		port->read_status_mask |= IRQ_BREAK;
	port->ignore_status_mask = 0;
	if (termios->c_iflag & IGNPAR)
		port->ignore_status_mask |= IRQ_PARITY | IRQ_FRAME;
	if (termios->c_iflag & IGNBRK)
		port->ignore_status_mask |= IRQ_BREAK;
	if (!(termios->c_cflag & CREAD))
		port->ignore_status_mask |= IRQ_RX;
	writel(cfg, port->membase + CFG);
	writel(divisor / 2, port->membase + RX_SAMPLE);
	uart_update_timeout(port, termios->c_cflag, baud);
	uart_port_unlock_irqrestore(port, flags);
	tty_termios_encode_baud_rate(termios, baud, baud);
}

static const char *rtl_type(struct uart_port *port) { return "RTL9617C UART"; }
static int rtl_request_port(struct uart_port *port) { return 0; }
static void rtl_release_port(struct uart_port *port) { }
static void rtl_config_port(struct uart_port *port, int flags)
{
	if (flags & UART_CONFIG_TYPE)
		port->type = PORT_RTL9617C;
}

static int rtl_verify_port(struct uart_port *port, struct serial_struct *ser)
{
	return ser->type == PORT_UNKNOWN || ser->type == PORT_RTL9617C ? 0 : -EINVAL;
}

static const struct uart_ops rtl_uart_ops = {
	.tx_empty = rtl_tx_empty,
	.set_mctrl = rtl_set_mctrl,
	.get_mctrl = rtl_get_mctrl,
	.stop_tx = rtl_stop_tx,
	.start_tx = rtl_start_tx,
	.stop_rx = rtl_stop_rx,
	.startup = rtl_startup,
	.shutdown = rtl_shutdown,
	.set_termios = rtl_set_termios,
	.type = rtl_type,
	.request_port = rtl_request_port,
	.release_port = rtl_release_port,
	.config_port = rtl_config_port,
	.verify_port = rtl_verify_port,
};

#if defined(CONFIG_SERIAL_RTL9617C_CONSOLE) || defined(CONFIG_SERIAL_EARLYCON)
static void rtl_putchar(struct uart_port *port, unsigned char ch)
{
	u32 status;

	if (!readl_poll_timeout_atomic(port->membase + INFO, status,
				      status & INFO_TX_EMPTY, 0, 10000))
		writel(ch, port->membase + TX_DATA);
}
#endif

#ifdef CONFIG_SERIAL_RTL9617C_CONSOLE
static void rtl_console_write(struct console *co, const char *s, unsigned int n)
{
	struct uart_port *port = &rtl_port;
	unsigned long flags;
	bool locked;

	if (port->sysrq)
		locked = false;
	else if (oops_in_progress)
		locked = uart_port_trylock_irqsave(port, &flags);
	else {
		uart_port_lock_irqsave(port, &flags);
		locked = true;
	}
	uart_console_write(port, s, n, rtl_putchar);
	if (locked)
		uart_port_unlock_irqrestore(port, flags);
}

static int rtl_console_setup(struct console *co, char *options)
{
	int baud = 115200, bits = 8, parity = 'n', flow = 'n';

	if (!rtl_port.membase)
		return -ENODEV;
	co->index = 0;
	if (options)
		uart_parse_options(options, &baud, &parity, &bits, &flow);
	return uart_set_options(&rtl_port, co, baud, parity, bits, flow);
}

static struct console rtl_console = {
	.name = "ttyS",
	.write = rtl_console_write,
	.device = uart_console_device,
	.setup = rtl_console_setup,
	.flags = CON_PRINTBUFFER,
	.index = -1,
	.data = &rtl_uart_driver,
};
#define RTL_CONSOLE (&rtl_console)
#else
#define RTL_CONSOLE NULL
#endif

#ifdef CONFIG_SERIAL_EARLYCON
static void rtl_early_write(struct console *con, const char *s, unsigned int n)
{
	struct earlycon_device *dev = con->data;

	uart_console_write(&dev->port, s, n, rtl_putchar);
}

static int __init rtl_early_setup(struct earlycon_device *dev, const char *opts)
{
	if (!dev->port.membase)
		return -ENODEV;
	// Preserve bootloader baud and pinmux until the regular console probes.
	dev->con->write = rtl_early_write;
	return 0;
}
OF_EARLYCON_DECLARE(rtl9617c, "realtek,rtl9617c-uart", rtl_early_setup);
OF_EARLYCON_DECLARE(cortina, "cortina,serial", rtl_early_setup);
#endif

static struct uart_driver rtl_uart_driver = {
	.owner = THIS_MODULE,
	.driver_name = "rtl9617c-uart",
	.dev_name = "ttyS",
	.major = TTY_MAJOR,
	.minor = 64,
	.nr = 1,
	.cons = RTL_CONSOLE,
};

static int rtl_uart_probe(struct platform_device *pdev)
{
	struct uart_port *port = &rtl_port;
	struct resource *res;
	struct clk *clk;
	int ret;

	if (port->dev)
		return -EBUSY;
	port->membase = devm_platform_get_and_ioremap_resource(pdev, 0, &res);
	if (IS_ERR(port->membase))
		return PTR_ERR(port->membase);
	clk = devm_clk_get_enabled(&pdev->dev, NULL);
	if (IS_ERR(clk))
		return dev_err_probe(&pdev->dev, PTR_ERR(clk), "UART clock missing\n");
	ret = platform_get_irq(pdev, 0);
	if (ret < 0)
		return ret;
	port->irq = ret;
	port->uartclk = clk_get_rate(clk);
	if (!port->uartclk)
		return -EINVAL;
	port->mapbase = res->start;
	port->mapsize = resource_size(res);
	port->dev = &pdev->dev;
	port->iotype = UPIO_MEM32;
	// Fill TX using its FULL bit; no unverified FIFO depth is needed.
	port->fifosize = 1;
	port->flags = UPF_BOOT_AUTOCONF;
	port->ops = &rtl_uart_ops;
	port->line = 0;
	port->has_sysrq = IS_ENABLED(CONFIG_SERIAL_RTL9617C_CONSOLE);
	platform_set_drvdata(pdev, port);
	ret = uart_add_one_port(&rtl_uart_driver, port);
	if (ret)
		port->dev = NULL;
	return ret;
}

static void rtl_uart_remove(struct platform_device *pdev)
{
	uart_remove_one_port(&rtl_uart_driver, &rtl_port);
	rtl_port.dev = NULL;
	rtl_port.membase = NULL;
}

static const struct of_device_id rtl_uart_match[] = {
	{ .compatible = "realtek,rtl9617c-uart" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_uart_match);

static struct platform_driver rtl_uart_platform = {
	.probe = rtl_uart_probe,
	.remove = rtl_uart_remove,
	.driver = { .name = "rtl9617c-uart", .of_match_table = rtl_uart_match },
};

static int __init rtl_uart_init(void)
{
	int ret = uart_register_driver(&rtl_uart_driver);

	if (ret)
		return ret;
	ret = platform_driver_register(&rtl_uart_platform);
	if (ret)
		uart_unregister_driver(&rtl_uart_driver);
	return ret;
}
module_init(rtl_uart_init);

static void __exit rtl_uart_exit(void)
{
	platform_driver_unregister(&rtl_uart_platform);
	uart_unregister_driver(&rtl_uart_driver);
}
module_exit(rtl_uart_exit);
MODULE_DESCRIPTION("Native RTL9617C UART0");
MODULE_LICENSE("GPL");
