include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-fttr
PKG_VERSION:=0.7.0_alpha
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

TYFZA1_PROJECT ?= $(abspath $(TOPDIR)/../..)

define Package/tyfza1-fttr-fpga
  SECTION:=firmware
  CATEGORY:=Firmware
  TITLE:=TYFZA1 retained downstream FPGA image
  DEPENDS:=@TARGET_rtl9617c +kmod-tyfza1-fttr-fpga
endef

define Package/tyfza1-fttr
  SECTION:=net
  CATEGORY:=Network
  TITLE:=TYFZA1 native FTTR control
  DEPENDS:=@TARGET_rtl9617c +kmod-tyfza1-fttr +tyfza1-pon-control +libjson-c +libuci
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
	@test -n "$(TYFZA1_FTTR_SERDES_SOURCE)" || { \
		echo "Set TYFZA1_FTTR_SERDES_SOURCE to this firmware's original /etc/serdes directory" >&2; exit 1; }
	python3 $(TYFZA1_PROJECT)/tools/prepare_fttr_serdes.py \
		"$(TYFZA1_FTTR_SERDES_SOURCE)" $(PKG_BUILD_DIR)/firmware
	$(if $(CONFIG_PACKAGE_tyfza1-fttr-fpga),python3 $(TYFZA1_PROJECT)/tools/prepare_fttr_fpga.py \
		"$(TYFZA1_FTTR_FPGA_SOURCE)" $(PKG_BUILD_DIR)/fpga)
endef

define Package/tyfza1-fttr-fpga/install
	$(INSTALL_DIR) $(1)/lib/firmware/realtek/rtl9617c
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/fpga/fttr-top.bin $(1)/lib/firmware/realtek/rtl9617c/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttrctl $(PKG_BUILD_DIR)/fttrctl.c \
		$(PKG_BUILD_DIR)/miniolt.c $(PKG_BUILD_DIR)/omci-wire.c \
		$(PKG_BUILD_DIR)/omci-socket.c $(PKG_BUILD_DIR)/omci-mib.c \
		$(PKG_BUILD_DIR)/omci-me.c $(PKG_BUILD_DIR)/omci-objects.c \
		$(PKG_BUILD_DIR)/omci-gem.c $(PKG_BUILD_DIR)/omci-association.c \
		$(PKG_BUILD_DIR)/omci-vlan.c $(PKG_BUILD_DIR)/omci-model.c \
		$(PKG_BUILD_DIR)/omci-mcast.c $(PKG_BUILD_DIR)/omci-service.c $(PKG_BUILD_DIR)/omci-services.c $(PKG_BUILD_DIR)/omci-wifi.c $(TARGET_LDFLAGS) -lm
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttrd $(PKG_BUILD_DIR)/fttrd.c \
		$(PKG_BUILD_DIR)/miniolt.c $(PKG_BUILD_DIR)/omci-wire.c \
		$(PKG_BUILD_DIR)/omci-socket.c $(PKG_BUILD_DIR)/omci-mib.c \
		$(PKG_BUILD_DIR)/omci-model.c $(PKG_BUILD_DIR)/omci-me.c \
		$(PKG_BUILD_DIR)/omci-objects.c $(PKG_BUILD_DIR)/omci-gem.c \
		$(PKG_BUILD_DIR)/omci-association.c $(PKG_BUILD_DIR)/omci-vlan.c \
		$(PKG_BUILD_DIR)/omci-mcast.c $(PKG_BUILD_DIR)/omci-service.c $(PKG_BUILD_DIR)/omci-services.c $(PKG_BUILD_DIR)/omci-wifi.c \
		$(PKG_BUILD_DIR)/upgrade-store.c $(PKG_BUILD_DIR)/omci-upgrade.c $(PKG_BUILD_DIR)/omci-auth.c $(PKG_BUILD_DIR)/onu-policy.c $(PKG_BUILD_DIR)/sync-json.c \
		$(PKG_BUILD_DIR)/fttr-net.c $(TARGET_LDFLAGS) -ljson-c
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttr-sync $(PKG_BUILD_DIR)/fttr-sync.c \
		$(PKG_BUILD_DIR)/sync-wire.c $(PKG_BUILD_DIR)/sync-peer.c $(PKG_BUILD_DIR)/sync-wifi.c $(PKG_BUILD_DIR)/sync-json.c \
		$(PKG_BUILD_DIR)/sync-register.c $(PKG_BUILD_DIR)/sync-client.c \
		$(PKG_BUILD_DIR)/sync-controller.c $(PKG_BUILD_DIR)/sync-store.c $(PKG_BUILD_DIR)/omci-socket.c \
		$(PKG_BUILD_DIR)/omci-wire.c $(TARGET_LDFLAGS) -ljson-c
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/fttr-sync-policy $(PKG_BUILD_DIR)/fttr-sync-policy.c \
		$(PKG_BUILD_DIR)/sync-controller.c $(PKG_BUILD_DIR)/sync-store.c $(PKG_BUILD_DIR)/sync-client.c \
		$(PKG_BUILD_DIR)/sync-wire.c $(PKG_BUILD_DIR)/sync-peer.c \
		$(PKG_BUILD_DIR)/sync-wifi.c $(PKG_BUILD_DIR)/sync-register.c \
		$(PKG_BUILD_DIR)/sync-json.c $(TARGET_LDFLAGS) -ljson-c -luci
endef

define Package/tyfza1-fttr/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttrctl $(1)/usr/sbin/fttrctl
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttrd $(1)/usr/sbin/fttrd
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttr-sync $(1)/usr/sbin/fttr-sync
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/fttr-sync-policy $(1)/usr/sbin/fttr-sync-policy
	$(INSTALL_DIR) $(1)/etc/init.d $(1)/etc/config $(1)/lib/firmware/realtek/rtl9617c
	$(INSTALL_BIN) ./files/tyfza1-fttr.init $(1)/etc/init.d/tyfza1-fttr
	$(INSTALL_BIN) ./files/tyfza1-fttr-sync.init $(1)/etc/init.d/tyfza1-fttr-sync
	$(INSTALL_DATA) ./files/tyfza1-fttr-sync.config $(1)/etc/config/tyfza1-fttr-sync
	$(INSTALL_DATA) ./files/tyfza1-fttr.services $(1)/etc/tyfza1-fttr.services
	$(INSTALL_DATA) ./files/tyfza1-fttr.wifi $(1)/etc/tyfza1-fttr.wifi
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/firmware/*.bin $(1)/lib/firmware/realtek/rtl9617c/
	$(LN) /tmp/tyfza1-factory/rtl9617c/fttr-bosa-calibration.bin $(1)/lib/firmware/realtek/rtl9617c/fttr-bosa-calibration.bin
	$(LN) /tmp/tyfza1-factory/fttr-private/ciphertext.bin $(1)/lib/firmware/realtek/rtl9617c/fttr-fpga-ciphertext.bin
endef

define Package/tyfza1-fttr/conffiles
/etc/tyfza1-fttr.services
/etc/tyfza1-fttr.wifi
/etc/tyfza1-fttr.onus.json
/etc/tyfza1-fttr-upgrades/
/etc/tyfza1-fttr-onu-state/
/etc/config/tyfza1-fttr-sync
/etc/tyfza1-fttr.sync.json
/etc/tyfza1-fttr-sync-state/
endef

$(eval $(call BuildPackage,tyfza1-fttr))
$(eval $(call BuildPackage,tyfza1-fttr-fpga))
