# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-storage
PKG_VERSION:=1
PKG_RELEASE:=2
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/tyfza1-storage
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=TYFZA1 factory capsules and storage migration
  DEPENDS:=@TARGET_rtl9617c +ubi-utils +uboot-envtools +tyfza1-pon-control
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Compile
	python3 ./build_overlay_seed.py $(STAGING_DIR_HOST)/bin/mkfs.ubifs $(PKG_BUILD_DIR)/overlay-seed.ubifs.gz
endef

define Package/tyfza1-storage/install
	$(INSTALL_DIR) $(1)/lib/preinit $(1)/usr/libexec $(1)/usr/share/tyfza1
	$(INSTALL_DATA) ./files/79_tyfza1_storage $(1)/lib/preinit/79_tyfza1_storage
	$(INSTALL_BIN) ./files/tyfza1-migrate-storage $(1)/usr/libexec/tyfza1-migrate-storage
	$(INSTALL_BIN) ./files/tyfza1-factory-source $(1)/usr/libexec/tyfza1-factory-source
	$(INSTALL_DATA) ./files/tyfza1-factory.sh $(1)/lib/tyfza1-factory.sh
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/overlay-seed.ubifs.gz $(1)/usr/share/tyfza1/overlay-seed.ubifs.gz
endef

$(eval $(call BuildPackage,tyfza1-storage))
