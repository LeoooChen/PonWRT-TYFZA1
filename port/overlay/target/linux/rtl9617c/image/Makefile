# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk

define Build/tyfza1-dtb
	$(CP) $(KDIR)/image-$(1).dtb $@
endef

define Device/tianyi_tyfza1
  DEVICE_VENDOR := Tianyi
  DEVICE_MODEL := TYFZA1
  DEVICE_VARIANT := native bring-up
  DEVICE_DTS := rtl9617c-tyfza1 rtl9617c-tyfza1-nand-readonly rtl9617c-tyfza1-stock-ubi-readonly rtl9617c-tyfza1-mdio rtl9617c-tyfza1-xram-lan rtl9617c-tyfza1-dma-tx rtl9617c-tyfza1-ni rtl9617c-tyfza1-pon-status
  DEVICE_DTS_DIR := ../dts
  KERNEL := kernel-bin
  KERNEL_INITRAMFS := kernel-bin
  KERNEL_SUFFIX := -Image
  KERNEL_INITRAMFS_SUFFIX := -initramfs-Image
  KERNEL_INSTALL := 1
  FILESYSTEMS := squashfs
  IMAGES := rootfs.squashfs
  IMAGE/rootfs.squashfs := append-rootfs
  ARTIFACTS := board.dtb nand-readonly.dtb stock-ubi-readonly.dtb mdio-probe.dtb xram-lan.dtb dma-tx.dtb ni.dtb pon-status.dtb
  ARTIFACT/board.dtb := tyfza1-dtb rtl9617c-tyfza1
  ARTIFACT/nand-readonly.dtb := tyfza1-dtb rtl9617c-tyfza1-nand-readonly
  ARTIFACT/stock-ubi-readonly.dtb := tyfza1-dtb rtl9617c-tyfza1-stock-ubi-readonly
  ARTIFACT/mdio-probe.dtb := tyfza1-dtb rtl9617c-tyfza1-mdio
  ARTIFACT/xram-lan.dtb := tyfza1-dtb rtl9617c-tyfza1-xram-lan
  ARTIFACT/dma-tx.dtb := tyfza1-dtb rtl9617c-tyfza1-dma-tx
  ARTIFACT/ni.dtb := tyfza1-dtb rtl9617c-tyfza1-ni
  ARTIFACT/pon-status.dtb := tyfza1-dtb rtl9617c-tyfza1-pon-status
endef
TARGET_DEVICES += tianyi_tyfza1

$(eval $(call BuildImage))
