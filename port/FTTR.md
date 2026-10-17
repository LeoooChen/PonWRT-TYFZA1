# TYFZA1 native FTTR worktree

Base: `dcf470f`, version `0.7.0-alpha`. Branch `codex/fttr-rewrite`.
Production DTs, release configuration, and the primary checkout are unchanged.

## Original hardware evidence

The 20261007 four-GbE unit boot log selects chip `9690`, `onuinfo (ASIC)`,
`libponrelay.so`, and P6 USXGMII 10G. Its downstream BOSA is GN25L99.
The 20261017 five-LAN/2.5G unit log executes sloader and reports loading
3,789,476 bytes successfully. The raw running FDT supplies its SPI/GPIO wiring.
These observations apply to the two samples; LAN speed is not the hardware ID.

`rtk_get_miniolt_chiptype` in the original `rtk-taurus_mdio.ko` reads
`0xc00044 & 0xffff == 0x9690` first. Otherwise register 3 values 0/1 select
FPGA1, and 2 selects FPGA2. The native driver uses these register results.
It does not use the names of the original libraries as a capability probe.

## Implemented

* Native MDIO-device/regmap driver, at the original C22 address 15.
  Address and data use registers 16..21. The table windows
  `40400..4047f`, `40800..408ff`, and (newer engines) `41000..417ff`
  use completion bits 1, 0, and 2 in `40040` respectively.
  FPGA1 rereads the target; newer engines read result registers `40072..40074`.
  Whole transactions hold `mii_bus::mdio_lock`, including result retrieval.
  Original table timeouts return -100; native returns `-ETIMEDOUT`.
  Real bus errors retain their errno and terminate the transaction.
* Optical SMBus byte-data adapter through original OLT registers `70,78..7d`.
  It supports the channel-zero, 8-bit register protocol used by rtkoltbosa.
  7-bit Linux slave addresses are converted to the original address byte.
  Register errors and busy/completion timeouts are returned to the caller.
  The helper also matches the original word access, but the adapter advertises
  byte-data only until the physical word byte order is verified.
* GN25L99 calibration client for the confirmed 9690 sample. It reads this unit's
  retained `rtkoltbosa_k.bin` through the existing read-only factory import.
  The 661-byte file has a big-endian length, XOR checksum, type 10 and optical
  address byte. The exact original 128 writes use bytes `1c..9b`.
  A root write of `1` to the client's `calibration` attribute applies it;
  the result includes the real register-60 calibrated marker and write count.
  The original function at `40aa24` only prints a checksum error and still
  programs all registers. Native calibration stops before IO on this error.
  The FPGA unit's optical client stays disabled until its actual model is known.
* PG2L100H FPGA Manager: PROGB/INITB/DONE handshake, original SPI payload,
  and real SPI error propagation. Firmware is loaded explicitly through the
  SPI device's root-writable `firmware` sysfs attribute. The name is relative
  to the kernel firmware directory. Original `top.bin` stays outside Git.
  The driver removes the original automatic seven-attempt loading loop;
  the actual failed phase is reported by FPGA Manager.
* Two development DTs and an optional configuration fragment. These do not
  replace the production DTs. The FPGA DT follows the sample's 4 MHz SPI,
  100 MHz controller clock, IRQ29, GPIO13/14 inputs, GPIO105 high, and
  PROGB138/INITB136/DONE137. Handshake inputs use physical levels, avoiding
  the original DT flags/raw-GPIO double inversion trap.
* Native P6/QX mode 5 (HSGMII 2.5G) and mode 9 (USXGMII 10G) startup,
  including per-board SerDes profile writes, clock readiness, the exact MAC
  FIFO/reset/PCS sequence, and NWAY. Reset bit31 shares the existing provider's
  register lock with PCIe/USB. Parameters failing to load or clock/reset errors
  stop initialization and leave the P6 traffic gates closed.
* Dedicated `fttr0` Ethernet netdev on the shared NI/DMA/QM engine. P6 source6
  has its own ingress table setup and RX endpoint, and TX uses destination6.
  Source7 and logical LLIDs keep using the upstream ONU endpoint. CPU-facing
  carrier uses the original P6 clock/link bits, not the optical ONU count.
  A delayed worker reads the real link state; close cancels it, retires DMA
  ownership and detaches RX before freeing the device. FE/PE offload for this
  interface is pending; the interface currently uses the kernel data path.

## Validation

The existing `tools/arm64_module_oracle.py` executes selected original module
functions, with MDIO modeled in memory. `scripts/test_fttr_mdio.py` compares
24 transactions across the three actual backends. `scripts/test_fttr_optical.py`
executes original libasicdrv routines and compares 8 read/write transactions.
Both also exercise hardware errors and the finite hardware completion waits.
They do not connect to a device. The kernel objects and development DTs are
compiled separately, without a firmware or whole-project build.

`scripts/test_fttr_fpga.py` compares PROGB output order and the SPI payload with
the original loader; it checks SPI failure and handshake timeout handling.
Its waits are modeled, so this does not prove electrical timing. The existing
module oracle was corrected to preserve section-relative call relocation
addends, which the original sloader uses for its GPIO helpers.

The validation outputs are `mdio-comparison.json`, `optical-comparison.json`,
`fpga-comparison.json`, `kernel-objects.log`, `fttr-asic.dtb`, and `fttr-2p5g.dtb`
under `artifacts/fttr/`. The FTTR control, FPGA and GN25L99 sources compile for
ARM64 Linux 6.18.55; both development DTs compile without warnings.

`scripts/test_fttr_bosa.py` executes the original stripped GN25L99 initializer
and compares its 128 writes for the local file and an independent byte pattern.
It reproduces the original checksum bug, and verifies that native calibration
rejects corrupt data before IO and stops on a real write error.
`bosa-comparison.json` contains hashes and results. `factory-comparison.json`
records actual OpenWrt ucode execution in an isolated root: exact downstream
file import, independent import during an upstream error, and revocation of
old data when the downstream file disappears. No private payload is committed.

`scripts/test_fttr_p6.py` executes the original mode, indirect-access, profile
loader, MAC and NWAY functions. Both modes are compared with zero and all-one
initial register contents (354/356 configuration writes per case). The native
startup additionally quiesces P6 before reset; original diagnostic read-address
commands are excluded from comparison because the new clock check does not
read unused diagnostic fields. GPIO/clock timing is modeled, not measured.
The same focused check verifies missing/bad profiles, reset error, clock failure
and actual link-bit conditions. Results are in `p6-comparison.json`.
`scripts/test_fttr_dispatch.py` executes the production QM dispatch branch:
FTTR source6, upstream7/LLIDs, and absent/unknown endpoints. Updated NI/QM/P6
and reset-provider objects compile for ARM64, and the NI components partially
link into `artifacts/fttr/rtl9617c-eth-linked.o`; both development DTs compile.

To load a development image, first merge `port/configs/tyfza1-fttr.config` into
its kernel configuration, then select the matching development DT. On the
FPGA unit, load its retained image by writing its firmware-relative filename
to `/sys/bus/spi/devices/<device>/firmware`. Only after successful loading,
bind the previously failed MiniOLT MDIO device through the standard driver's
`bind` attribute. Automatic factory import and sequencing remain pending.
  Optical calibration import now runs independently of upstream BOSA import;
  absent downstream data removes any previous runtime copy.

Generate the two development P6 firmware files with
`tools/prepare_fttr_serdes.py <original-rootfs>/etc/serdes <output>` and copy the
resulting `.bin` files into the development root's `/lib/firmware/realtek/rtl9617c/`.
The converter follows the confirmed revision-C file selection and preserves
all original row values/delays. The original profile payloads stay outside Git.
The NI platform device's `fttr_link` sysfs attribute accepts `9 1` for the
confirmed 9690 sample, or `5 <original-nway>` for the FPGA sample. `0 0` stops
P6. The attribute reports configured mode, physical clock and actual error.
After successful P6 configuration, `fttr0` can be opened as an Ethernet device.
These are development controls; automatic full FTTR startup is still pending.

Original hashes and detailed results are emitted under `artifacts/fttr/`.
Original binaries and the kernel build tree are read from the private evidence
and copied build tree, not modified in the main checkout.

## P6 evidence for the next implementation

The four-GbE unit's retained `config_hs.xml` selects external SerDes mode 9
and NWAY 1. The FPGA startup script forces mode 5. Executing the original DAL
conversion gives mode 5 -> `aal_sds_mode_set(0,6,1,3)` (HSGMII 2.5G), and
mode 9 -> `aal_sds_mode_set(0,6,4,5)` (USXGMII 10G). NWAY 1 converts to the
three arguments `aal_sds_nway_set(0,6,0)`; the original meanings are inverted
between those API layers. Conversion output is in `analysis/fttr/p6-dal-conversion.json`.

Offline execution of `aal_sds_mode_set` with the original MAC and indirect
SerDes helpers confirms the P6 QX block, shared reset bit31 and NI MAC at
`b200..b2a8`. NI traffic gates are at `2300` bit6 and `6108` bit14, and drain
is `a1f4` bit20. These are different from the existing S3/copper PHY path.
The original 10G MAC threshold is `0xa0e`, and its additional reset/PCS pulses
are present in machine code; the older related SDK source is insufficient.
The profile function's failure now stops initialization instead of following
the original print-and-continue behavior. The SoC P6 integration is implemented;
the peer MiniOLT's WTG/PON SerDes still requires its own initialization.

## Remaining work toward working FTTR

1. FPGA board startup: import its own retained bitstream, load it before
   binding the MiniOLT transport, and initialize the original post-load registers.
2. Optical identification for the other sample, plus real GN25L99 calibration
   and optical readiness verification. The native sequence is implemented.
3. Peer MiniOLT WTG/PON SerDes startup and real P6/FTTR frame verification.
   The native SoC P6 configuration and dedicated RX/TX endpoint are implemented.
4. OLT-side discovery/registration, authentication, OMCI, T-CONT/GEM/DBA and
   VLAN/service provisioning. Current upstream ONU OMCI is a different role.
5. Original subordinate gateway configuration, Wi-Fi synchronization,
   multicast, reboot/upgrade and roaming coordination, using the connected
   ONU's actual capabilities rather than assuming one fixed subordinate model.
6. FE/PE acceleration and stale-flow invalidation, then hardware acceptance
   with real downstream gateways and a recorded interoperability scope.

No optical startup, ONU registration, service traffic or hardware acceleration
has been verified for this branch. No placeholder online state is exposed.
