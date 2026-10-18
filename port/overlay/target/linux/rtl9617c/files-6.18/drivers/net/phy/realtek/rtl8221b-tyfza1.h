/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * TYFZA1 RTL8221B cold initialization, rewritten from this board's OEM
 * rtl8221b.ko Rtl8221_phy_init and Rtl8221_serdes_option_set.
 * OEM module SHA256: 0ba23162b42e457b0628b0899e89883198aac919a14ddab91997fd9ef5710652
 * The opaque revision-8 patch words are retained in their original order.
 */
#ifndef RTL8221B_TYFZA1_H
#define RTL8221B_TYFZA1_H

struct rtl8221b_reg {
	u16 reg, value;
};

#include "rtl8221b-tyfza1-patch.h"

static int rtl8221b_write_regs(struct phy_device *phydev,
			       const struct rtl8221b_reg *regs, unsigned int count)
{
	unsigned int i;
	int ret;

	for (i = 0; i < count; i++) {
		ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, regs[i].reg, regs[i].value);
		if (ret < 0)
			return ret;
	}
	return 0;
}

/* Preserve unrelated bits, but retain the OEM's required write strobes. */
static int rtl8221b_update(struct phy_device *phydev, int mmd, u16 reg,
			  u16 mask, u16 value)
{
	int old = phy_read_mmd(phydev, mmd, reg);

	if (old < 0)
		return old;
	return phy_write_mmd(phydev, mmd, reg, (old & ~mask) | value);
}

static int rtl8221b_wait_ready(struct phy_device *phydev)
{
	int val;

	return phy_read_mmd_poll_timeout(phydev, MDIO_MMD_VEND2, 0xa420,
					val, (val & 3) == 3, 1000, 100000, false);
}

static int rtl8221b_load_patch(struct phy_device *phydev)
{
	int ret, val;

	val = phy_read_mmd(phydev, MDIO_MMD_PMAPMD, MII_PHYSID2);
	if (val < 0)
		return val;
	if ((val & 0x3ff) != 0x048)
		return rtl8221b_update(phydev, MDIO_MMD_VEND1, 0x75b5, 0xf, 4);

	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0x801e);
	if (ret < 0)
		return ret;
	val = phy_read_mmd(phydev, MDIO_MMD_VEND2, 0xa438);
	if (val < 0)
		return val;
	if (val == 0x20)
		return 0;

	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb820, 0, BIT(4));
	if (ret < 0)
		return ret;
	ret = phy_read_mmd_poll_timeout(phydev, MDIO_MMD_VEND2, 0xb800,
					val, val & BIT(6), 1000, 100000, false);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0x8024);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa438, 0x3701);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0xb82e);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa438, 1);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb820, 0, BIT(7));
	if (ret < 0)
		return ret;
	ret = rtl8221b_write_regs(phydev, rtl8221b_patch_ram, ARRAY_SIZE(rtl8221b_patch_ram));
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb820, BIT(7), 0);
	if (ret < 0)
		return ret;
	ret = rtl8221b_write_regs(phydev, rtl8221b_patch_regs, ARRAY_SIZE(rtl8221b_patch_regs));
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb896, BIT(0), 0);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xb892, 0);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb896, 0, BIT(0));
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa438, 0);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb82e, BIT(0), 0);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0x8024);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa438, 0);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xb820, BIT(4), 0);
	if (ret < 0)
		return ret;
	ret = phy_read_mmd_poll_timeout(phydev, MDIO_MMD_VEND2, 0xb800,
					val, !(val & BIT(6)), 1000, 100000, false);
	if (ret < 0)
		return ret;
	ret = rtl8221b_write_regs(phydev, rtl8221b_patch_analog, ARRAY_SIZE(rtl8221b_patch_analog));
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x75b5, 0xe086);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x75b6, 0x0024);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x75b1, 0x0013);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa436, 0x801e);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa438, 0x20);
	if (ret < 0)
		return ret;
	return phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xa400, 0x9200);
}

static int rtl8221b_hsgmii(struct phy_device *phydev)
{
	int ret, val, release;

	ret = rtl8221b_update(phydev, MDIO_MMD_VEND1, 0x75f3, BIT(0), 0);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND1, 0x697a, 0x3f, 1);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x6a04, 0x0503);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x6f10, 0xd433);
	if (ret < 0)
		return ret;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND1, 0x6f11, 0x8020);
	if (ret < 0)
		return ret;
	ret = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xa400, 0, BIT(14));
	if (ret < 0)
		return ret;
	ret = phy_read_mmd_poll_timeout(phydev, MDIO_MMD_VEND2, 0xa434,
					val, val & BIT(2), 100000, 10000000, false);
	release = rtl8221b_update(phydev, MDIO_MMD_VEND2, 0xa400, BIT(14), 0);
	if (release < 0)
		phydev_err(phydev, "HiSGMII force-down release failed: %d\n", release);
	/* The OEM returns success on this timeout. A failed latch must fail attach. */
	return ret < 0 ? ret : release;
}

static int rtl8221b_tyfza1_config_init(struct phy_device *phydev)
{
	int ret;

	ret = rtl8221b_wait_ready(phydev);
	if (ret < 0)
		goto error;
	ret = rtl8221b_load_patch(phydev);
	if (ret < 0)
		goto error;
	/* Original init disables the copper PHY before changing its SerDes mode.
	 * phylib's resume callback enables it after a successful attach. */
	ret = rtl8221b_update(phydev, MDIO_MMD_PMAPMD, MDIO_CTRL1, 0, MDIO_CTRL1_LPOWER);
	if (ret < 0)
		goto error;
	ret = phy_write_mmd(phydev, MDIO_MMD_VEND2, 0xd032, 0x00a7);
	if (ret < 0)
		goto error;
	ret = rtl8221b_hsgmii(phydev);
	if (ret < 0)
		goto error;
	return 0;
error:
	phydev_err(phydev, "TYFZA1 cold initialization failed: %d\n", ret);
	return ret;
}

static int rtl8221b_tyfza1_get_features(struct phy_device *phydev)
{
	int ret = rtl8221b_wait_ready(phydev);

	return ret < 0 ? ret : rtl822x_c45_get_features(phydev);
}

static int rtl8221b_tyfza1_match(struct phy_device *phydev,
			       const struct phy_driver *driver)
{
	return phydev->is_c45 &&
	       of_property_read_bool(phydev->mdio.dev.of_node, "realtek,hsgmii") &&
	       (phydev->c45_ids.device_ids[MDIO_MMD_PMAPMD] & 0xfffffff0) == 0x001cc840;
}

#endif
