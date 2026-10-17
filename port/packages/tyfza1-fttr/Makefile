include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-fttr
PKG_VERSION:=0.7.0_alpha
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/tyfza1-fttr
  SECTION:=net
  CATEGORY:=Network
  TITLE:=TYFZA1 native FTTR control
  DEPENDS:=@TARGET_rtl9617c +kmod-tyfza1-fttr
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttrctl $(PKG_BUILD_DIR)/fttrctl.c \
		$(PKG_BUILD_DIR)/miniolt.c $(PKG_BUILD_DIR)/omci-wire.c \
		$(PKG_BUILD_DIR)/omci-socket.c $(PKG_BUILD_DIR)/omci-mib.c $(TARGET_LDFLAGS)
endef

define Package/tyfza1-fttr/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttrctl $(1)/usr/sbin/fttrctl
endef

$(eval $(call BuildPackage,tyfza1-fttr))
