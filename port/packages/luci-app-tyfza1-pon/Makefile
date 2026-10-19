include $(TOPDIR)/rules.mk

LUCI_TITLE:=TYFZA1 PON configuration and live status
LUCI_DEPENDS:=+luci-base +luci-theme-argon +luci-i18n-argon-config-zh-cn +luci-mod-network +luci-mod-status +luci-proto-ppp +luci-proto-ipv6 +luci-i18n-base-zh-cn +rpcd-mod-ucode +ucode-mod-fs +tyfza1-pon-control
LUCI_EXTRA_DEPENDS:=tyfza1-pon-control (>=0.8.1_beta)
LUCI_PKGARCH:=all
PKG_VERSION:=0.8.1_beta
PKG_RELEASE:=
PKG_LICENSE:=GPL-2.0-or-later
# CSSTidy 1.3 predates CSS variables and responsive grid expressions.
CONFIG_LUCI_CSSTIDY:=

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
