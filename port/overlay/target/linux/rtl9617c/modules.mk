# SPDX-License-Identifier: GPL-2.0-only
define KernelPackage/rtl-ax-ap
  SUBMENU:=Wireless Drivers
  TITLE:=TYFZA1 native RTL8852C/RTL8192XB AP driver
  DEPENDS:=@TARGET_rtl9617c
  KCONFIG:=CONFIG_CFG80211 CONFIG_MAC80211 CONFIG_RTL_AX_AP
  FILES:= \
	$(LINUX_DIR)/net/wireless/cfg80211.ko \
	$(LINUX_DIR)/net/mac80211/mac80211.ko \
	$(LINUX_DIR)/drivers/net/wireless/realtek/rtl_ax_ap/rtl_ax_ap.ko
  AUTOLOAD:=$(call AutoLoad,50,rtl_ax_ap)
endef

define KernelPackage/rtl-ax-ap/description
  Native TYFZA1 Wi-Fi stack with PE forwarding. Load after the root
  filesystem is mounted so regulatory and PCI firmware requests can read
  the packaged images.
endef

$(eval $(call KernelPackage,rtl-ax-ap))
