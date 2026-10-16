include $(TOPDIR)/rules.mk

LUCI_TITLE:=TYFZA1 PON configuration and live status
LUCI_DEPENDS:=+luci-base +luci-mod-network +luci-mod-status +luci-proto-ppp +luci-proto-ipv6 +luci-i18n-base-zh-cn +rpcd-mod-ucode +ucode-mod-fs +tyfza1-pon-control
LUCI_EXTRA_DEPENDS:=tyfza1-pon-control (>=0.4.0_alpha)
LUCI_PKGARCH:=all
PKG_VERSION:=0.4.0_alpha
PKG_RELEASE:=
PKG_LICENSE:=GPL-2.0-or-later

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
