# SPDX-License-Identifier: GPL-2.0-only
obj-$(CONFIG_RTL_AX_AP) += rtl_ax_ap.o
rtl_ax_ap-y := pci.o phy.o rx.o tx.o pe-core.o power-tables.o limits.o
# Keep the ARM64 optimization used by the validated standalone module.
ccflags-y += -fcaller-saves
