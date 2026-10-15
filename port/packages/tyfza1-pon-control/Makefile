include $(TOPDIR)/rules.mk

PKG_NAME:=tyfza1-pon-control
PKG_VERSION:=0.3.1_alpha
PKG_RELEASE:=
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/tyfza1-pon-control
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Native RTL9617C PON identity, EPON OAM and ITU OMCI control
  DEPENDS:=+libuci +libgcc +ucode +ucode-mod-fs
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
	mkdir -p $(PKG_BUILD_DIR)/omci/src
	$(CP) ./omci/Cargo.toml ./omci/Cargo.lock ./omci/LICENSE ./omci/UPSTREAM $(PKG_BUILD_DIR)/omci/
	$(CP) ./omci/src/* $(PKG_BUILD_DIR)/omci/src/
endef

TYFZA1_CARGO?=$(shell command -v cargo)
define Build/Compile
	cd $(PKG_BUILD_DIR)/omci && RUSTC="$(dir $(TYFZA1_CARGO))rustc" \
		RUSTFLAGS="-C target-cpu=cortex-a55" $(TYFZA1_CARGO) build --release --locked --target aarch64-unknown-linux-musl
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -Wall -Wextra -Werror \
		$(PKG_BUILD_DIR)/pon-oamd.c $(PKG_BUILD_DIR)/pon-oam.c $(PKG_BUILD_DIR)/pon-backend.c \
		$(TARGET_LDFLAGS) -luci -o $(PKG_BUILD_DIR)/pon-oamd
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -Wall -Wextra -Werror \
		$(PKG_BUILD_DIR)/pon-identity.c $(TARGET_LDFLAGS) -luci -o $(PKG_BUILD_DIR)/pon-identity
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -Wall -Wextra -Werror \
		$(PKG_BUILD_DIR)/pon-omcid.c $(PKG_BUILD_DIR)/omci/target/aarch64-unknown-linux-musl/release/libtyfza1_omci.a \
		$(TARGET_LDFLAGS) -luci -lgcc_s -lpthread -lm -ldl -o $(PKG_BUILD_DIR)/pon-omcid
endef

define Package/tyfza1-pon-control/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/pon-oamd $(1)/usr/sbin/pon-oamd
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/pon-identity $(1)/usr/sbin/pon-identity
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/pon-omcid $(1)/usr/sbin/pon-omcid
	$(INSTALL_DIR) $(1)/usr/share/tyfza1
	$(INSTALL_BIN) ./files/factory.uc $(1)/usr/share/tyfza1/factory.uc
	$(INSTALL_DATA) ./omci/UPSTREAM $(1)/usr/share/tyfza1/omci-UPSTREAM
endef

$(eval $(call BuildPackage,tyfza1-pon-control))
