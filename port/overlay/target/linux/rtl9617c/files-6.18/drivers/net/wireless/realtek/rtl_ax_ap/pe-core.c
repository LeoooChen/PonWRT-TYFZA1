// SPDX-License-Identifier: GPL-2.0-only
/* Reset, SRAM and ATU sequencing follows the board's taurus_pe_reset driver. */
#include "pe_probe.h"

static int pe_probe_access(struct pe_probe *pe, u32 command)
{
	u32 state;
	writel(command, pe->atu);
	return readl_poll_timeout(pe->atu, state, !(state & (BIT(30) | BIT(31))), 1, 10000);
}

static void pe_probe_reset(struct pe_probe *pe, u32 bits)
{
	writel((readl(pe->reset) & ~pe->reset_mask) | bits, pe->reset);
	readl(pe->reset);
}

static void pe_probe_core_stop(struct pe_probe *pe)
{
	if (!pe->configured) return;
	pe_probe_reset(pe, pe->reset_mask);
	writel(readl(pe->ram) | BIT(5), pe->ram);
	udelay(1000);
}

static void pe_probe_core_free(struct pe_probe *pe)
{
	unsigned int index, i;

	pe_probe_core_stop(pe);
	if (pe->configured) {
		for (index = 0; index < 6; index++) {
			for (i = 0; i < 7; i++)
				writel(pe->entries[index][i], pe->atu + 4 + i * 4);
			pe_probe_access(pe, (pe->staging[0] & 255) | index << 24 | BIT(31));
		}
		writel(pe->ram_saved, pe->ram);
		pe_probe_reset(pe, pe->reset_saved & pe->reset_mask);
	}
	if (pe->staging_saved) {
		for (i = 1; i < 8; i++)
			writel(pe->staging[i], pe->atu + i * 4);
		writel(pe->staging[0] & ~(BIT(30) | BIT(31)), pe->atu);
	}
	if (pe->memory)
		dma_free_coherent(&pe->device->dev, PE_PROBE_BYTES, pe->memory, pe->dma);
	if (pe->device) platform_device_unregister(pe->device);
	if (pe->ram) iounmap(pe->ram);
	if (pe->atu) iounmap(pe->atu);
	if (pe->reset) iounmap(pe->reset);
}

static int pe_probe_core_alloc(struct pe_probe *pe)
{
	unsigned int i, index;
	int ret;

	pe->reset = ioremap(0xf4320098, 4);
	pe->reset_mask = BIT(18 + pe->core) | BIT(20 + pe->core);
	pe->atu = ioremap(0x90300000 + pe->core * 0x100, 0x20);
	pe->ram = ioremap(0x9030306c + pe->core * 8, 4);
	if (!pe->reset || !pe->atu || !pe->ram) return -ENOMEM;
	pe->reset_saved = readl(pe->reset);
	pe->ram_saved = readl(pe->ram);
	for (i = 0; i < 8; i++) pe->staging[i] = readl(pe->atu + i * 4);
	pe->staging_saved = true;
	if (!(pe->reset_saved & BIT(20 + pe->core)) || (pe->staging[0] & 14)) return -EBUSY;
	for (index = 0; index < 6; index++) {
		ret = pe_probe_access(pe, (pe->staging[0] & 255) | index << 24 | BIT(30));
		if (ret) return ret;
		for (i = 0; i < 7; i++) pe->entries[index][i] = readl(pe->atu + 4 + i * 4);
	}
	pe->device = platform_device_register_simple("tyfza1-pe-probe", pe->core, NULL, 0);
	if (IS_ERR(pe->device)) { ret = PTR_ERR(pe->device); pe->device = NULL; return ret; }
	ret = dma_coerce_mask_and_coherent(&pe->device->dev, DMA_BIT_MASK(32));
	if (ret) return ret;
	pe->memory = dma_alloc_coherent(&pe->device->dev, PE_PROBE_BYTES, &pe->dma, GFP_KERNEL);
	if (!pe->memory) return -ENOMEM;
	if (!IS_ALIGNED(pe->dma, PE_PROBE_BYTES)) return -EINVAL;
	memset(pe->memory, 0, PE_PROBE_BYTES);
	return 0;
}

static int pe_probe_core_start(struct pe_probe *pe)
{
	/* Stock DSP CP3 windows use28-bit addresses. Keep the reset alias,
	 * and provide the same code at0x0fc00000 for instruction-memory fill. */
	static const u32 input[] = { 0x1fc0, 0x1000, 0x1100, 0x1200, 0x1300, 0x0fc0 };
	static const u32 output[] = { 0, 0xf430, 0xa040, 0xb040, 0xf700, 0 };
	void __iomem *bist;
	u32 bist_control;
	unsigned int index, i;
	int ret;

	pe->configured = true;
	pe_probe_core_stop(pe);
	/* Stock taurus_pe_reset_probe exits PE SRAM BIST before CPU release. */
	bist = ioremap(0xf4320004, 4);
	if (!bist) return -ENOMEM;
	bist_control = readl(bist);
	writel(bist_control & ~BIT(18), bist);
	readl(bist);
	iounmap(bist);
	for (index = 0; index < 6; index++) {
		writel(input[index], pe->atu + 4);
		writel(index && index != 5 ? 0xfff0 : 0xffff, pe->atu + 8);
		writel(index && index != 5 ? output[index] : pe->dma >> 16, pe->atu + 12);
		for (i = 3; i < 7; i++) writel(pe->entries[0][i], pe->atu + 4 + i * 4);
		ret = pe_probe_access(pe, (pe->staging[0] & 255) | 63 | index << 24 | BIT(31));
		if (ret) return ret;
	}
	writel(readl(pe->ram) & ~BIT(5), pe->ram);
	pe_probe_reset(pe, BIT(20 + pe->core));
	udelay(1000);
	pe_probe_reset(pe, 0);
	return 0;
}
static struct pe_probe *pe_engines[2];
static unsigned int pe_engine_users[2];
static DEFINE_MUTEX(pe_engine_lock);

int pe_probe_alloc(struct pe_probe *pe, unsigned int slot, unsigned int core)
{
 int ret;
 struct pe_probe *pe_engine;
 pe->core = core;
 mutex_lock(&pe_engine_lock);
 pe_engine = pe_engines[core];
 if (!pe_engine) {
  pe_engine = kzalloc(sizeof(*pe_engine), GFP_KERNEL);
  if (!pe_engine) { ret = -ENOMEM; goto unlock; }
  pe_engine->core = core;
  ret = pe_probe_core_alloc(pe_engine);
  if (ret) { pe_probe_core_free(pe_engine); kfree(pe_engine); pe_engine = NULL; goto unlock; }
 }
 pe->device = pe_engine->device;
 pe->reset = pe_engine->reset; pe->atu = pe_engine->atu; pe->ram = pe_engine->ram;
 pe->slot = slot;
 pe->memory = dma_alloc_coherent(&pe->device->dev, PE_PROBE_BYTES, &pe->dma, GFP_KERNEL);
 if (!pe->memory) {
  if (!pe_engine_users[core]) { pe_probe_core_free(pe_engine); kfree(pe_engine); pe_engine = NULL; }
  pe->device = NULL; ret = -ENOMEM; goto unlock;
 }
 memset(pe->memory, 0, PE_PROBE_BYTES);
 pe_engine_users[core]++;
 ret = 0;
unlock:
 pe_engines[core] = pe_engine;
 mutex_unlock(&pe_engine_lock);
 return ret;
}

int pe_probe_start(struct pe_probe *pe, const u32 *program, unsigned int bytes)
{
 struct pe_probe *pe_engine = pe_engines[pe->core];
 u8 *control;
 u32 address, status, generation;
 int ret;
 mutex_lock(&pe_engine_lock);
 if (!pe_engine->configured) {
  memcpy(pe_engine->memory, program, bytes);
  address = (u32)(pe_engine->dma + PE_PROBE_MAILBOX) | 0xa0000000U;
  put_unaligned_le32(program[0] | address >> 16, pe_engine->memory);
  put_unaligned_le32(program[1] | (address & 0xffff), pe_engine->memory + 4);
  put_unaligned_le32(program[2] | ((u32)pe_engine->dma | 0x80000000U) >> 16, pe_engine->memory + 8);
  put_unaligned_le32(program[0x380 / 4] | address >> 16, pe_engine->memory + 0x380);
  put_unaligned_le32(program[0x384 / 4] | (address & 0xffff), pe_engine->memory + 0x384);
  dma_wmb();
  ret = pe_probe_core_start(pe_engine);
  if (ret) goto unlock;
  ret = read_poll_timeout(get_unaligned_le32, status, status == 0x50455744,
   10, 100000, false, pe_engine->memory + PE_PROBE_MAILBOX);
  if (ret) goto unlock;
 }
 control = pe_engine->memory + PE_PROBE_MAILBOX;
 generation = get_unaligned_le32(control + (142 + pe->slot) * 4) + 1;
 put_unaligned_le32((u32)(pe->dma + PE_PROBE_MAILBOX) | 0xa0000000U,
  control + (140 + pe->slot) * 4);
 dma_wmb();
 put_unaligned_le32(generation, control + (142 + pe->slot) * 4);
 pe->attached = true;
 ret = read_poll_timeout(get_unaligned_le32, status, status == 0x50455744,
  10, 100000, false, pe->memory + PE_PROBE_MAILBOX);
unlock:
 mutex_unlock(&pe_engine_lock);
 return ret;
}

int pe_probe_stop(struct pe_probe *pe)
{
 struct pe_probe *pe_engine = pe_engines[pe->core];
 u8 *control;
 u32 generation, status;
 int ret;
 if (!pe->attached) return 0;
 mutex_lock(&pe_engine_lock);
 control = pe_engine->memory + PE_PROBE_MAILBOX;
 generation = get_unaligned_le32(control + (142 + pe->slot) * 4) + 1;
 put_unaligned_le32(0, control + (140 + pe->slot) * 4);
 dma_wmb();
 put_unaligned_le32(generation, control + (142 + pe->slot) * 4);
 /* The shared executor acknowledges a slot withdrawal after its last access. */
 ret = read_poll_timeout(get_unaligned_le32, status, status == generation,
  10, 100000, false, control + (144 + pe->slot) * 4);
 if (!ret) pe->attached = false;
 mutex_unlock(&pe_engine_lock);
 return ret;
}

void pe_probe_free(struct pe_probe *pe)
{
 struct pe_probe *pe_engine = pe_engines[pe->core];
 if (!pe->memory) return;
 if (pe_probe_stop(pe)) return;
 mutex_lock(&pe_engine_lock);
 dma_free_coherent(&pe->device->dev, PE_PROBE_BYTES, pe->memory, pe->dma);
 if (!--pe_engine_users[pe->core]) {
  pe_probe_core_free(pe_engine);
  kfree(pe_engine); pe_engines[pe->core] = NULL;
 }
 mutex_unlock(&pe_engine_lock);
}

void *pe_probe_engine_memory(struct pe_probe *pe) { return pe_engines[pe->core]->memory; }
