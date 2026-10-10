# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

ARCH:=aarch64
BOARD:=rtl9617c
BOARDNAME:=Realtek RTL9617C (Taurus)
CPU_TYPE:=cortex-a55
SUBTARGETS:=generic
FEATURES:=fpu nand ramdisk squashfs targz
DEPENDS:=+@DRIVER_11AC_SUPPORT +@DRIVER_11AX_SUPPORT
KERNEL_PATCHVER:=6.18
KERNELNAME:=Image
DEVICE_TYPE:=basic

include $(INCLUDE_DIR)/target.mk

DEFAULT_PACKAGES += ubi-utils nand-utils

define Target/Description
  Native RTL9617C support. TYFZA1 serial bring-up; hardware validation pending.
endef

$(eval $(call BuildTarget))
