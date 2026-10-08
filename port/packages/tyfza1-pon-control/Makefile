include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-pon-control
PKG_VERSION:=0.1.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/tyfza1-pon-control
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Native RTL9617C EPON OAM control
  DEPENDS:=+libuci
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -Wall -Wextra -Werror \
		$(PKG_BUILD_DIR)/pon-oamd.c $(PKG_BUILD_DIR)/pon-oam.c $(PKG_BUILD_DIR)/pon-backend.c \
		$(TARGET_LDFLAGS) -luci -o $(PKG_BUILD_DIR)/pon-oamd
endef

define Package/tyfza1-pon-control/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/pon-oamd $(1)/usr/sbin/pon-oamd
endef

$(eval $(call BuildPackage,tyfza1-pon-control))
