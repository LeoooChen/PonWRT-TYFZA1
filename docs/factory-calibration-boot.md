# Factory calibration without boot-time root writes

The factory boot service used to recreate three `/lib/firmware/realtek/`
symlinks on every boot, after deleting the previous `/tmp` import. Even when
the links were already correct, a read-only firmware directory made `ln -sfn`
fail before any factory data was read. BOSA and both radios then lacked their
calibration files.

`tyfza1-pon-control` now installs these fixed links into the image:

- `rtl9617c/bosa-calibration.bin`
- `tyfza1/rtl8852ce/flash-calibration.bin`
- `tyfza1/rtl8192xbe/flash-calibration.bin`

Each points to the same relative path below `/tmp/tyfza1-factory/`. S09 still
reads this unit's `ubi_Config` and generates fresh calibration before S10
loads the radio module. It no longer writes `/lib/firmware`. A failed factory
read still returns an error and leaves the affected calibration unavailable.
There is no cached calibration fallback or retry.

Validation: `scripts/test_factory_import.py` executes the package's link
installation commands, mounts the firmware directory read-only in a private
mount namespace, and runs the actual boot service and importer in an isolated
OpenWrt root. It verifies the BOSA and both radio payloads, LAN identity, and
calibration removal on mount failure. The existing importer cases also pass.
No full firmware build, flash, service restart or radio change was performed.

This fixes the unnecessary root-write dependency. It does not establish why
the reported unit rebooted after seven or eight hours or whether its overlay
became read-only. The existing UBIFS mount ownership issue also remains a
separate investigation: requesting a read-only mount when the same volume is
already mounted read-write returns `EBUSY`.
