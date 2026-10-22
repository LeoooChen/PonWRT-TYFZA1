/* SPDX-License-Identifier: GPL-2.0-only */
#include <linux/irqdomain.h>
#include <linux/of_address.h>

static void __iomem *pe_event_timer;
static unsigned int pe_event_irq;
static u32 pe_event_saved[4];
static struct rtl_ax_tx *pe_event_radios[2];

static irqreturn_t pe_event_interrupt(int irq, void *context)
{
 unsigned int slot;
 writel(0, pe_event_timer + 4);
 writel(0, pe_event_timer + 12);
 writel(1, pe_event_timer + 20);
 pe_event_interrupts++;
 for (slot = 0; slot < 2; slot++) {
  struct rtl_ax_tx *tx = READ_ONCE(pe_event_radios[slot]);
  if (!tx) continue;
  put_unaligned_le32(get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 276),
   tx->pe.memory + PE_PROBE_MAILBOX + 264);
  rtl_ax_rx_pe_event_ack(tx->rx);
  wake_up_all(&tx->key_flush_wait);
  rtl_ax_rx_pe_wake(tx->rx);
  schedule_work(&tx->ni.ps_work);
 }
 return IRQ_HANDLED;
}

static void pe_event_release(struct rtl_ax_tx *tx)
{
 unsigned int slot;
 if (!pe_event_timer || pe_event_radios[tx->pe.slot] != tx) return;
 put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 256);
 dma_wmb();
 WRITE_ONCE(pe_event_radios[tx->pe.slot], NULL);
 synchronize_irq(pe_event_irq);
 for (slot = 0; slot < 2; slot++) if (pe_event_radios[slot]) return;
 writel(0, pe_event_timer + 4);
 writel(0, pe_event_timer + 12);
 writel(1, pe_event_timer + 20);
 free_irq(pe_event_irq, pe_event_radios);
 writel(pe_event_saved[0], pe_event_timer);
 writel(pe_event_saved[2], pe_event_timer + 12);
 writel(pe_event_saved[3], pe_event_timer + 16);
 writel(pe_event_saved[1], pe_event_timer + 4);
 iounmap(pe_event_timer); pe_event_timer = NULL;
}

static int pe_event_prepare(struct rtl_ax_tx *tx)
{
 struct device_node *node;
 struct irq_domain *domain;
 int ret;
 if (!pe_event_timer) {
  node = of_find_node_by_path("/soc/interrupt-controller@f4329004");
  if (!node) return -ENODEV;
  domain = irq_find_host(node); of_node_put(node);
  if (!domain) return -ENODEV;
  pe_event_irq = irq_create_mapping(domain, 5);
  if (!pe_event_irq) return -ENODEV;
  pe_event_timer = ioremap(0xf43290b0, 36);
  if (!pe_event_timer) return -ENOMEM;
  pe_event_saved[0] = readl(pe_event_timer);
  pe_event_saved[1] = readl(pe_event_timer + 4);
  pe_event_saved[2] = readl(pe_event_timer + 12);
  pe_event_saved[3] = readl(pe_event_timer + 16);
  if (pe_event_saved[1] || pe_event_saved[2] || pe_event_saved[3]) { ret = -EBUSY; goto unmap; }
  ret = request_irq(pe_event_irq, pe_event_interrupt, IRQF_NO_AUTOEN, "tyfza1-pe-event", pe_event_radios);
  if (ret) goto unmap;
  writel(1, pe_event_timer + 20);
  enable_irq(pe_event_irq);
 }
 WRITE_ONCE(pe_event_radios[tx->pe.slot], tx);
 put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 260);
 put_unaligned_le32(get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 268),
  tx->pe.memory + PE_PROBE_MAILBOX + 264);
 put_unaligned_le32(get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 268),
  tx->pe.memory + PE_PROBE_MAILBOX + 276);
 put_unaligned_le32(0xb00290b0, tx->pe.memory + PE_PROBE_MAILBOX + 256);
 dma_wmb();
 return 0;
unmap:
 iounmap(pe_event_timer); pe_event_timer = NULL;
 return ret;
}
