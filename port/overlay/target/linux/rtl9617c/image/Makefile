# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk
TYFZA1_PROJECT ?= $(abspath $(TOPDIR)/../..)
TYFZA1_SIGNING_KEYS ?= $(TYFZA1_PROJECT)/evidence/rtl-workspace/private/tyfza1-build-keys

define Build/tyfza1-ubi-fit
	python3 $(TYFZA1_PROJECT)/scripts/make_ubi_fit.py \
		$@ $(KDIR)/image-rtl9617c-tyfza1-ubi.dtb $@.fit \
		--keys $(TYFZA1_SIGNING_KEYS)
	$(CP) $@.fit/image.itb $@
endef

define Build/tyfza1-dtb
	$(CP) $(KDIR)/image-$(1).dtb $@
endef

define Build/tyfza1-rootfs-size
	@size="$$(stat -c%s $(IMAGE_ROOTFS))"; \
	[ "$$size" -le 33648640 ] || { \
		echo "TYFZA1 rootfs is $$size bytes; OEM rootfs volume holds 33648640 bytes" >&2; \
		exit 1; \
	}
endef

define Device/tianyi_tyfza1
  DEVICE_VENDOR := Tianyi
  DEVICE_MODEL := TYFZA1
  DEVICE_VARIANT := native bring-up
  DEVICE_DTS := rtl9617c-tyfza1 rtl9617c-tyfza1-nand-readonly rtl9617c-tyfza1-stock-ubi-readonly rtl9617c-tyfza1-mdio rtl9617c-tyfza1-xram-lan rtl9617c-tyfza1-dma-tx rtl9617c-tyfza1-ni rtl9617c-tyfza1-pon-status rtl9617c-tyfza1-pcie rtl9617c-tyfza1-ubi
  DEVICE_DTS_DIR := ../dts
  DEVICE_PACKAGES := kmod-rtl-ax-ap tyfza1-pon-control luci-app-tyfza1-pon \
	ubi-utils nand-utils uboot-envtools iw wpad-mbedtls wifi-scripts wireless-regdb
  KERNEL := kernel-bin | tyfza1-ubi-fit
  KERNEL_INITRAMFS := kernel-bin
  KERNEL_SUFFIX := -kernel.itb
  KERNEL_INITRAMFS_SUFFIX := -initramfs-Image
  KERNEL_INSTALL := 1
  FILESYSTEMS := squashfs
  IMAGES := rootfs.squashfs sysupgrade.bin
  IMAGE/rootfs.squashfs := append-rootfs | tyfza1-rootfs-size
  IMAGE/sysupgrade.bin := tyfza1-rootfs-size | sysupgrade-tar | append-metadata
  ARTIFACTS := board.dtb nand-readonly.dtb stock-ubi-readonly.dtb mdio-probe.dtb xram-lan.dtb dma-tx.dtb ni.dtb pon-status.dtb pcie.dtb ubi.dtb
  ARTIFACT/board.dtb := tyfza1-dtb rtl9617c-tyfza1
  ARTIFACT/nand-readonly.dtb := tyfza1-dtb rtl9617c-tyfza1-nand-readonly
  ARTIFACT/stock-ubi-readonly.dtb := tyfza1-dtb rtl9617c-tyfza1-stock-ubi-readonly
  ARTIFACT/mdio-probe.dtb := tyfza1-dtb rtl9617c-tyfza1-mdio
  ARTIFACT/xram-lan.dtb := tyfza1-dtb rtl9617c-tyfza1-xram-lan
  ARTIFACT/dma-tx.dtb := tyfza1-dtb rtl9617c-tyfza1-dma-tx
  ARTIFACT/ni.dtb := tyfza1-dtb rtl9617c-tyfza1-ni
  ARTIFACT/pon-status.dtb := tyfza1-dtb rtl9617c-tyfza1-pon-status
  ARTIFACT/pcie.dtb := tyfza1-dtb rtl9617c-tyfza1-pcie
  ARTIFACT/ubi.dtb := tyfza1-dtb rtl9617c-tyfza1-ubi
endef
TARGET_DEVICES += tianyi_tyfza1

$(eval $(call BuildImage))
