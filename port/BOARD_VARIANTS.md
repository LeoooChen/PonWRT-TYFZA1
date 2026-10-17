# TYFZA1 board variants

The production UBI FIT contains one kernel, one root filesystem, and two signed
configurations. The boot script follows the OEM `get_gpio_val` strap mapping:

| GPIO113:111 | Configuration | Hardware | Native DT |
| --- | --- | --- | --- |
| 000 | conf-0 | Four internal GbE PHYs plus RTL8221B 2.5G | rtl9617c-tyfza1-ubi-2p5g |
| 001 | conf-1 | Four internal GbE PHYs | rtl9617c-tyfza1-ubi |

Other strap values stop with an explicit error. The script changes only the
current U-Boot environment in RAM. It does not save the environment. Both
configurations sign the kernel, their own DT, and the shared boot script.
The production image supports both OpenWrt board names during sysupgrade.

The original boards have different kernel builds, but their `ubi_DTB0` volume
contents are identical (SHA256
`61b00917fac67c08430ab4dddedc8f9dee9dcf65aba379fd6470023f28f65698`).
The new 2.5G unit's running FDT matches the original package's `fdt@0` except
for `/chosen/bootargs`. The four-GbE unit's boot log selects `conf-1/fdt@1`.
The strap mapping comes from its saved OEM U-Boot environment.

## LEDs

`rtl9617c-tyfza1-leds.dtsi` retains the four-GbE board wiring.
`rtl9617c-tyfza1-2p5g.dtsi` supplies the original 2.5G board's LED GPIOs,
polarities, and light-switch button. It removes the unwired `LED_USB0` node;
this board uses `LED_USB1` on GPIO2. USB defaults use the selected board name.
Preserved configurations referencing the nonexistent USB0 LED are migrated on
the 2.5G board. Existing custom LAN LED bindings remain intact.

The RTL8221B LED0 remains a PHY output. NI initializes its original function
register (MMD31 0xd032=0x00a7) and registers `LED_8221B_P4_0` with the shared LED
controller. Brightness 0/1/2 selects off/on/PHY link-and-activity through
MMD31 register 0xd044 bits0/4, matching the OEM callback. The global LED switch
can force off/on and restore automatic operation. GPIO and MDIO output errors
propagate to the sysfs writer and are reported during shutdown.

The shared LED callback now returns an error and retains all three brightness
values. Kernel and radio modules must be rebuilt together; the changed radio
callback is included in the focused ARM64 compilation.

## External PHY and S3

Only the 2.5G DT declares port4, Clause45 address7, and S3. Standard MDIO bus
reset properties assert GPIO128 low for 30ms before ID discovery, then allow
300ms for startup. The four-GbE DT does not reset or probe an external PHY.
GPIO14 is no longer held high by MDIO: the running 2.5G OEM system uses that
pin for the FPGA loader's RX/input path. S3 power, isolation, mux, and speed
sequences remain owned by the shared PHY provider, preserving USB/PCIe bits.

Patch206 now completes the HiSGMII PHY sequence: preserve unrelated 0x75f3 bits,
select option1, write 0x6f10=0xd433, and latch the mode through force-link-down
and its status acknowledgement. Poll timeout and MDIO failures return errors;
the force-down request is released after a failed poll. The original new-unit
RTL8221B machine code was executed to compare these values. Options0/2 retain
their original 0xd455 setting.

Patch211 allocates private state for the legacy RTL8221B PHY entries before
their config-init callback uses it. The LAN driver exposes standard ethtool
link-settings/renegotiation and MII ioctls. PHY attach errors retain their actual
errno, and a missing required PHY does not silently remove lan5. S3 speed errors
are reported and enter phylib's error state.

## Validation and limits

Focused validation is recorded under `artifacts/board-variants-20261017/`:

- Both compiled production DTs match every original GPIO LED and button.
- The exact boot selector executes all eight strap values and selects both FIT DTs.
- The production signer verifies both configuration signatures; corrupted
  signatures are rejected. Its test payload is deliberately not a bootable kernel.
- Original new-unit PHY mode values match; MDIO write errors and mode-latch
  timeout cleanup are exercised.
- Real OpenWrt UCI and BusyBox execute board LED defaults and upgrade migration.
- LED arbitration, automatic PHY mode restoration, and output error reporting pass.
- Modified LED, MDIO, NI, Realtek PHY, and radio objects compile for ARM64 in an
  isolated kernel tree. Existing MDIO and boot-contract tests pass.

No full firmware image, flash, reboot, voltage measurement, or 2.5G throughput
test was performed. Cold boot, actual lamp correspondence, link reconnection,
and sustained bidirectional 2.5G/FE forwarding still require hardware acceptance.
No physical supply-health claim follows from the register-sequence checks.

The production image Makefile always passes both DTs to `make_ubi_fit.py`.
`build_lan_ui.sh` passes both PCIe DTs to the RAM packager. Manual RAM builds
can use `--dtb-2p5g`; single-DT diagnostic images remain available.
