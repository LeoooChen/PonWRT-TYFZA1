include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-fttr
PKG_VERSION:=0.7.0_alpha
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

TYFZA1_PROJECT ?= $(abspath $(TOPDIR)/../..)

define Package/tyfza1-fttr
  SECTION:=net
  CATEGORY:=Network
  TITLE:=TYFZA1 native FTTR control
  DEPENDS:=@TARGET_rtl9617c +kmod-tyfza1-fttr +tyfza1-pon-control
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
	@test -n "$(TYFZA1_FTTR_SERDES_SOURCE)" || { \
		echo "Set TYFZA1_FTTR_SERDES_SOURCE to this firmware's original /etc/serdes directory" >&2; exit 1; }
	python3 $(TYFZA1_PROJECT)/tools/prepare_fttr_serdes.py \
		"$(TYFZA1_FTTR_SERDES_SOURCE)" $(PKG_BUILD_DIR)/firmware
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttrctl $(PKG_BUILD_DIR)/fttrctl.c \
		$(PKG_BUILD_DIR)/miniolt.c $(PKG_BUILD_DIR)/omci-wire.c \
		$(PKG_BUILD_DIR)/omci-socket.c $(PKG_BUILD_DIR)/omci-mib.c \
		$(PKG_BUILD_DIR)/omci-me.c $(PKG_BUILD_DIR)/omci-objects.c \
		$(PKG_BUILD_DIR)/omci-gem.c $(PKG_BUILD_DIR)/omci-association.c \
		$(PKG_BUILD_DIR)/omci-vlan.c $(PKG_BUILD_DIR)/omci-model.c \
		$(PKG_BUILD_DIR)/omci-service.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttrd $(PKG_BUILD_DIR)/fttrd.c \
		$(PKG_BUILD_DIR)/miniolt.c $(PKG_BUILD_DIR)/omci-wire.c \
		$(PKG_BUILD_DIR)/omci-socket.c $(PKG_BUILD_DIR)/omci-mib.c \
		$(PKG_BUILD_DIR)/omci-model.c $(TARGET_LDFLAGS)
endef

define Package/tyfza1-fttr/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttrctl $(1)/usr/sbin/fttrctl
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttrd $(1)/usr/sbin/fttrd
	$(INSTALL_DIR) $(1)/etc/init.d $(1)/lib/firmware/realtek/rtl9617c
	$(INSTALL_BIN) ./files/tyfza1-fttr.init $(1)/etc/init.d/tyfza1-fttr
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/firmware/*.bin $(1)/lib/firmware/realtek/rtl9617c/
endef

$(eval $(call BuildPackage,tyfza1-fttr))
