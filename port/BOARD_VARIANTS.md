# TYFZA1 board variants

The production UBI FIT contains one kernel, one root filesystem, and two signed
configurations. The boot script uses the OEM `b_conf` selection. On multi-board
loaders, the original `b1`/`b2` environment runs `conf_match` before sourcing the
FIT script, with this `get_gpio_val` strap mapping:

| GPIO113:111 | Configuration | Hardware | Native DT |
| --- | --- | --- | --- |
| 000 | conf-0 | Three wired GbE PHYs plus RTL8221B 2.5G | rtl9617c-tyfza1-ubi-2p5g |
| 001 | conf-1 | Four internal GbE PHYs | rtl9617c-tyfza1-ubi |

The failing older loader has no `gpio` command. The captured 2.5G OEM
environment has no `conf_match`, and its original FIT script calls
`bootm ${unzip_src}` without a configuration suffix. The native FIT therefore
retains `conf-0` (2.5G) as its default. The shared script does not issue
`gpio` commands or interpret command failures as electrical levels. For manual
RAM boots with no `b_conf` yet, it runs the OEM `conf_match` only when that
selector exists, then uses its result exactly as the original FIT script does.
An unset result uses the FIT default, including for unmatched OEM strap values.

The script changes only the current U-Boot environment in RAM. It does not save
the environment. Both configurations sign the kernel, their own DT, and the
shared boot script. The production image supports both OpenWrt board names
during sysupgrade.

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

The RTL8221B LED0 remains a PHY output. The PHY initializes its original function
register (MMD31 0xd032=0x00a7); NI registers `LED_8221B_P4_0` with the shared LED
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
The bus reset callback then drives GPIO14 high, releases ownership and waits
300ms, matching the OEM module initializer. Releasing ownership does not change
its output latch; the later FPGA loader can acquire it as RX input. The
four-GbE board has no GPIO14 property and the callback does not toggle it.
S3 power, isolation, mux, and speed sequences remain owned by the shared PHY
provider, preserving USB/PCIe bits.

Patch206 now registers a dedicated TYFZA1 RTL8221B Clause45 driver before the
generic entries. It matches the declared HiSGMII PHY and the original
RTL8221B model, including its revision IDs. It waits for internal readiness,
loads the original revision-8 patch only when its version is not 0x20, applies
the other revisions' original mode preparation, disables copper operation,
initializes LED0, and latches HiSGMII. Standard phylib handles copper
advertisement, link status, suspend and resume. No generic software reset
precedes the OEM cold initialization, and there is no dependency on private
state owned by a different PHY entry.

The revision-8 patch constants are immutable original register data; the
native control flow is rewritten. Successful initialization matches all
1093 OEM MDIO events (1075 writes) in the cold revision-8 case, and all 19
events in the already-patched and revision9/10 cases. A force-link-down
ACK timeout follows the OEM success result after releasing the request;
actual MDIO and release errors still fail only the affected port. See `port/RTL8221B.md`.

Patch211 allocates private state for the legacy RTL8221B PHY entries before
their generic config-init callback uses it. The LAN driver exposes standard ethtool
link-settings/renegotiation and MII ioctls. PHY attach errors retain their actual
errno, and a missing required PHY does not silently disable the affected port. S3 speed errors
are reported and enter phylib's error state.

## Validation and limits

Focused validation is recorded under `artifacts/board-variants-20261017/`.
The OEM boot handoff regression and signed UBI/RAM package checks are recorded
under `artifacts/boot-selector-fix-20261018/`:

- Both compiled production DTs match every original GPIO LED and button.
- Boot selection covers both OEM configurations, older 2.5G loaders without a
  GPIO command, and manual RAM boots using the original environment selector.
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

## Physical LAN ports (2026-10-25 correction)

Both enclosures have four Ethernet jacks and a separate gray FXS phone jack.
The four-GbE board uses NI ports 0,1,2,3. The 2.5G board uses NI ports
0,1,2,4; internal port3 is unwired. This matches the captured OEM
`PORT_REMAPPING=04,02,01,00` and the user's physical inspection. The 2.5G
DT leaves index3 of `realtek,lan-phys` empty. The enclosure labels run in the
opposite direction from the previous Linux names; the blue 2.5G jack is LAN1.

| Enclosure / Linux name | Four-GbE NI port | 2.5G-board NI port |
| --- | --- | --- |
| LAN1 / lan1 | 3 | 4 (RTL8221B, 2.5G) |
| LAN2 / lan2 | 2 | 2 |
| LAN3 / lan3 | 1 | 1 |
| LAN4 / lan4 | 0 | 0 |

PHY handles, FE/QM hardware IDs, scheduler slots and per-port MAC derivation
retain their NI indices. The NI4 asynchronous worker and its readiness barrier
still refer to hardware port4; its netdev is now lan1. CTC UNI1..4 and the
Linux names agree, while the existing CTC-to-NI PM mapping stays intact.

Existing lanN configuration references use the new enclosure numbering; no
LAN-number migration is performed.

Default network generation no longer depends on successful PHY probing.
LED defaults follow the same physical jacks. Existing LED bindings are left
as configured.
