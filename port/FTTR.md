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
* ASIC9690 peer WTG HSGMII/USXGMII and all three original PON SerDes modes.
  These are separate from the SoC P6 initialization. Original field updates,
  analog writes, soft reset/OTP/pause settings, and mode-2 upstream settings
  retain their order. Hardware lock failures now return `-ETIMEDOUT` rather
  than the original print-and-continue success. MDIO errors stop the sequence.
  The ASIC-only physical reset uses GPIO138 and the original IO-mux sequence;
  the FPGA DT removes this GPIO property because its loader owns PROGB138.
  A shared control mutex serializes the SerDes window and optical transactions.
  GN25L99 status rereads its real calibrated marker so a lost marker cannot
  retain a cached configured state.
* Native OMCI control-frame foundation for the confirmed ASIC path:
  host-MAC programming, the original default EtherType transition
  `88a8 -> 8888`, global `40029=c140` and PLOAM registration bit3 in `4000c`.
  `omci_link` reads the actual host/OLT MACs, EtherType and downstream format.
  The portable controller sources encode the two original downstream formats,
  CRC, and baseline/extended upstream messages. They fix the original extended
  receive branch which only logs and loops without passing the message upward.
  The original upstream software-CRC policy (check ONU ID zero) is retained;
  there is no evidence here to replace other ONUs' receive policy.
  AF_PACKET uses a nonblocking socket bound to the dedicated interface and
  ignores its own outgoing frames. The optional `tyfza1-fttr` package installs
  native `fttrctl` and `fttrd` executables. Automatic MIB inventory is implemented;
  authentication and service provisioning remain pending.
* `fttr0` MTU 1982, with a 1996-byte Ethernet frame budget. This fits the
  existing 2048-byte QM buffers with 32-byte headroom, a full 16-byte header
  and a possible 4-byte FCS. The MAC threshold is 2000; the shared NI maximum
  is raised to 2000 only when it is smaller. Both fields come from actual
  original `aal_ni_xge_mac_frame_len_set` / `aal_ni_pkt_len_set` execution.
  Ordinary 1500-byte configuration would reject long OMCI frames in AF_PACKET.
  Larger frames remain outside this buffer design and return a length error.
* ASIC ONU table/control primitives: all 64 hardware slots report raw downstream
  state (`40401 + 2*ID`), upstream state (`40803 + 4*ID`), EqD and the original
  vendor-first serial bytes. The registration flag is downstream bit18 from
  original `ONU_Online`; it is not a claim that OMCI/service provisioning passed.
  Native block control changes only downstream bit16. Its lifetime belongs to
  the controller instead of copying the original unconditional 15-second clear.
  PLOAM mailbox `40007..4000a` preserves the original word/trigger order and
  explicit repeat count. Real transport errors stop before further writes.
  The original wait rejects a ready mailbox on its 2000th read; native accepts
  that final ready read and returns `-ETIMEDOUT` only if all 2000 reads are busy.
* ASIC core initialization follows the original `UMM1001M_regInit` IO order,
  including its register reads, table clearing and final interrupt acknowledgement.
  The original initializer clears DBA words `41000..410ff`, covering eight ONU
  slots. The original profile API and MDIO engine address `41000..417ff` for
  64 ONUs x eight T-CONTs x four words. Native initialization clears that whole
  supported bank. The other original table boundaries, including `43ffe`, stay
  as implemented by the original initializer. Global DBA grants use original
  values 19440/38/38/10/10; individual T-CONT writes retain the original
  intermediate word and fixed/assured/maximum programming order.
* Corrected mode-specific PCS status: mode5 HSGMII uses page1/reg30 bit4;
  mode9 USXGMII uses page5/reg0 bit12. Both host P6 and peer WTG retain the
  original two status reads. The earlier implementation read page1/reg30 for
  both modes, which was incorrect for USXGMII. These selectors/branches were
  verified by executing the actual `onuinfo` caller, rather than its generic
  SDK diagnostic function. P6 and peer sysfs status now expose their real link
  bits for startup sequencing.
* Native ASIC hardware startup/stop service, ordered after factory import.
  It consumes the unit's retained enable flag, external SerDes/NWAY and OLT
  PON SerDes modes. No missing-mode default is invented. Startup disables TX,
  closes P6, resets the MiniOLT, applies GN25L99 calibration, configures peer,
  host and PON SerDes, waits for both PCS links, initializes core/OMCI/DBA,
  verifies the current calibration marker, opens `fttr0`, and enables TX last.
  The original 500 ms settle plus 200 ms additional link budget is retained;
  native waits for status without repeating initialization. Stop disables TX
  before closing the interface/P6. The control driver's probe/removal also
  put ASIC TX into the disabled state. A device-directory lock serializes
  startup/stop with explicit MIB operations.
* Native baseline MIB Upload / Upload Next transactions. Requests and class,
  instance, mask and command-count fields follow original builder/parser
  execution. High-priority transaction IDs use `8001..ffff`; three attempts
  with two seconds each match the original high-priority send path. Native
  deadlines use the monotonic clock. Other ONU/transaction replies do not
  complete the active request, and an invalid matching response returns an
  error. MIB records retain all 26 attribute bytes without guessing a vendor's
  schema or silently dropping unknown ME classes. `fttrctl mib` checks the
  hardware registration flag and serial before/after the scan, and reports
  completion only after every command succeeds for that same ONU. It does not
  reset the MIB or provision a service.
* Original management-grant policy: the registration callback's default
  `SetOnuTcontDba(ONU,0,0,3840,3840)` programs T-CONT 0 at 3840 kbit/s
  (hardware grant 60). Native writes the exact temporary `1003e`, fixed-zero
  and final `3c003c` sequence; it does not use the generic profile setter's
  larger intermediate value. Disconnect/block releases the two grant words.
  Both the resident controller and explicit MIB command prepare management
  bandwidth before transmitting OMCI.
* Resident native controller with one independent asynchronous MIB job per
  hardware ONU slot. It polls original registration state every two seconds,
  notices serial changes, allocates management bandwidth, and collects MIB
  records. A peer timeout or invalid matching reply fails that peer; other
  jobs continue. Shared transport/register IO failures stop the controller.
  Wire-format errors are counted/logged and attributed to a job when its
  ONU/transaction fields are available. No repeated full initialization or
  blanket peer rescan is used after failure.
  Completed MIB files are published only after a fresh single-slot hardware
  read confirms the same registered, unblocked serial. Disconnect/replacement
  removes the active inventory. State and records live in the root-only
  `/var/run/tyfza1-fttr/` directory. `inventory` means MIB collection completed;
  it does not mean an ONU service has been provisioned.
  The procd instance owns the device lock throughout bootstrap, management
  and teardown; a normal replacement waits for its predecessor to release it.
  SIGTERM disables TX, closes the netdev and stops P6. The separate shell
  hardware sequencer has been removed.
* Native per-ONU MIB entity model. Wire widths for 57 unambiguous original
  classes come from `plr_gponomci_medetail_get`; native storage keeps wire
  bytes and actual instance IDs, without the original packed RAM ABI.
  Split records for one class/instance merge by attribute mask. Missing
  attributes stay absent. Unknown classes/masks remain in the raw inventory
  and increment `unparsed_records`; they do not acquire invented values.
  Original class53 has two descriptors for mask0400 and no mask0200, so its
  voice layout stays explicitly opaque instead of guessing a correction.
  The original content parser ignores failed attribute parsing and returns
  success even for a 27-byte selection in a 26-byte baseline record, or an
  entirely missing 30-byte attribute. Native rejects such selections with
  `-EMSGSIZE`, fails that inventory and never publishes an incomplete value.
  Completed `.mib` files append merged `mib_entity` entries and a `mib_model`
  summary before `mib_complete`; status reports entity/unparsed-record counts.
  Disconnect, replacement and failed inventory clear the corresponding model.
* Native baseline managed-entity transactions: Create/Delete/Set/Get request
  bodies follow original `libponrelay` builders. Callers supply wire values;
  the original packed RAM structures are not used. Each job validates the
  ONU, transaction, opcode, class and instance, preserves the complete reply
  and actual ONU result byte, and treats nonzero results as `-EREMOTEIO`.
  Get success also requires the returned attribute mask to match the request.
  A received reply is not itself a successful operation. Asynchronous job
  methods use the same original two-second/three-attempt policy; the synchronous
  exchange is used by `fttrctl get`. Native GEM jobs now use Create/Get/Delete;
  automatic service planning remains pending.
  Explicit Get shares the MiniOLT lease/management grant with explicit MIB
  collection and verifies the same registered serial before publishing a reply.
* Explicit native T-CONT binding through `fttrctl tcont`. The operator selects
  an actual subordinate ME instance and hardware data slot1..7; slot0 remains
  the existing management grant. The ASIC Alloc-ID is generated by the original
  `plr_ponsrv_alloc_id_get` formula, `256 + hardware-ID * 8 + slot`.
  Native reads the entity's current Alloc-ID, sets the requested assignment,
  reads it back, checks the same registered serial, then programs local DBA.
  An already matching assignment skips Set. It reports the original result,
  prior/last-read assignment and exact failed phase. If local DBA fails after
  remote verification, `alloc_verified=true` and a failed `grant` phase keep
  that partial state visible; this is not an atomic transaction or service-ready
  state. The existing device lease prevents concurrent resident-controller work.
* Native GEM resource lifecycle. The original optional class280 descriptor,
  class268 CTP and class266 IW TP wire layouts/masks/creation order are rewritten
  as a small asynchronous job over the native ME transaction engine. Planned
  instances are queried first: code5 from the original result table means
  absent, while existing instances are reused only when all selected values
  match. An existing dependent with an absent planned prerequisite is reported
  as inconsistent before writes. New instances are read back after Create.
  The existing peer's real T-CONT, queue, association/profile and GAL pointers
  are inputs; no subordinate model or default ME instance is invented.
  `fttrctl gem` exposes the descriptor-free original unicast sequence, using
  the original ASIC GEM Port-ID allocator. Descriptor support is present in
  the job API for the pending QoS/service planner.
  A failed Create/verification cleans up only instances whose Create was sent
  after observing absence. Unknown outcomes are queried before deletion;
  every successful Delete is checked by Get for actual absence. Cleanup stops
  at its first failed/unconfirmed deletion, retains the touched objects and
  keeps both the initial failure and cleanup failure, including raw replies.
  This fixes the original `dfe98` path that discarded tracking despite Delete
  failures and continued deleting dependencies. The release API removes only
  resources created by that job; reused resources retain their state.
  The explicit command checks the same registered serial before each request,
  including retransmissions, and at completion. Replacement stops further
  writes/cleanup to the new peer.
  Its output distinguishes absent/present/verified/unknown object states; GEM
  `configured` means these MEs were verified, not that bridge/Internet works.
* Native GAL/association profiles and basic Ethernet/VEIP UNI attachment.
  GAL defaults follow actual `plr_gponrm_gal_add`: instance1, maximum payload
  1600. Shared/per-UNI bridge attributes follow their separate original
  producers, and the optional initial Mapper follows its original empty
  pointer/unmarked-frame values. Requested bridge/Mapper instances remain
  explicit inputs. `fttrctl profiles` performs these profile transactions.
  `fttrctl uni` first queries the real Ethernet or VEIP entity, then creates
  the original bridge port, the Ethernet-only VLAN filter, and extended VLAN
  association. Ethernet uses TP type1 and ordinal+1; VEIP uses type11 and
  ordinal+9. Their original Create masks, span selection and association
  types stay distinct. Both use the original TPID8100/default DSCP Set.
  The existing GEM lifecycle is now shared in `omci-objects.c`, with explicit
  prerequisite bits and separate Create/Set attribute groups. Each group is
  read back independently. This also fixes original Get content parsing that
  accepts success after its 25-byte limit prevents decoding the 28-byte
  TPID/DSCP group. Native checks all 28 bytes. Independent GAL/bridge reuse
  does not acquire a false linear dependency; GEM and UNI retain their real
  prerequisite checks and cleanup ownership. These commands prepare base
  entities; Mapper binding, ANI-side attachment and VLAN operation rules
  still need the complete service policy.

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
initial register contents (355..358 configuration writes per case). The native
startup additionally quiesces P6 before reset; original diagnostic read-address
commands are excluded from comparison because the new clock check does not
read unused diagnostic fields. GPIO/clock timing is modeled, not measured.
The same focused check verifies missing/bad profiles, reset error, clock failure
and actual link-bit conditions. Results are in `p6-comparison.json`.
`scripts/test_fttr_dispatch.py` executes the production QM dispatch branch:
FTTR source6, upstream7/LLIDs, and absent/unknown endpoints. Updated NI/QM/P6
and reset-provider objects compile for ARM64, and the NI components partially
link into `artifacts/fttr/rtl9617c-eth-linked.o`; both development DTs compile.

`scripts/test_fttr_peer_serdes.py` executes original `libasicdrv.so` field and
indirect-access helpers, intercepting only raw register IO and external calls.
Its 14 comparisons cover both host modes/NWAY values and all three PON modes,
with zero/all-one register seeds. Every register read/write and requested
delay matches. Focused fault checks reproduce the original false-success
timeouts and verify native stop/error propagation. The original mode-3 poll
limits are 101 reads for bits25/24 and 501 for bits23/22; other modes and WTG
use 2001. This is a finite hardware wait, not a startup retry loop.
`peer-serdes-comparison.json` records results. Updated FTTR/BOSA objects and
both development DTs compile; physical GPIO/optical timing remains unmeasured.

`scripts/test_fttr_omci_wire.py` compares the original caller/relay hardware
configuration writes, actual MAC/format readback, and six complete encoded
frames (baseline, short/long extended, both downstream formats). The longest
frame is 1996 bytes. It reproduces the original extended-message RX drop and
checks native decoding, truncated frames and the original CRC policy.
The ARM64 OMCI wire/socket objects compile and form `libfttr-omci.a` in the
isolated artifacts directory. `omci-wire-comparison.json` records the results;
the socket transport has not exchanged real device traffic.

`scripts/test_fttr_onu.py` compares nine original/native cases: identities at
hardware IDs 0/5/63, block/allow updates with zero/all-one seeds, and PLOAM
mailbox sends with one/three repetitions. It also reproduces the original
final-poll bug and checks real register errors and a continuously busy mailbox.
`onu-comparison.json` records results; the updated control object compiles for
ARM64. No ONU hardware status has been read from a live device by this branch.

`scripts/test_fttr_mib.py` executes the original MIB request builders and
class/instance/mask parsers. Its native local datagram peer exercise completes
a two-record scan, ignores unrelated replies, crosses `ffff -> 8001`, rejects
an invalid matching response, and verifies the three real timeout attempts.
The CLI is executed against a temporary 64-row kernel-attribute fixture,
including missing-attribute failure. This does not simulate optical ranging.
Results are in `mib-comparison.json`.
The synchronous CLI now drives the same asynchronous job engine as the
controller. The original request/parser comparisons and timeout checks still
pass. Binary single-slot state decoding and management-command formatting are
also exercised against the kernel attribute ABI.

`scripts/test_fttr_core.py` executes original core/DBA/profile functions and
the mode-specific PCS branch in `onuinfo` (`41906c..419324`) with explicit
mode/status inputs. Core IO matches with zero/all-one register seeds, with
1792 additional zero writes extending the DBA clear to all 64 supported slots.
The check also covers original global DBA writes, profile addresses at IDs
0/5/63, peer indirect IO and transport failures. `core-comparison.json` records
the scope. Updated P6 checks require two reads at the correct selector and
reject the other mode's status bit. ARM64 NI/control objects compile and the
control modules link against the same-version real kernel export table.
Management-grant writes at hardware slots 0/5/63 additionally match actual
original `SetOnuTcontDba` execution with the original startup defaults.

`scripts/test_fttr_startup.py` runs the real factory importer on a temporary
copy of retained config, then executes the native controller's bootstrap in
a private network namespace. A test-only preload models sysfs write/show
callbacks; netdev MAC/MTU/flag ioctls operate on real namespace-local veth.
The check covers original command order, PCS timeout, lost calibration marker,
core-write failure, stop during initialization and replacement waiting for the
previous controller's lock. `startup-comparison.json` records the scope.

`scripts/test_fttr_controller.py` runs the real daemon over AF_PACKET/veth in
another private network namespace, with only the hardware attributes modeled.
Two ONU jobs progress independently, an invalid matching peer response is
reported, malformed frames are counted, disconnect revokes its inventory/grant,
and a changed serial triggers a fresh scan. A prior transaction's reply cannot
complete the new scan. SIGTERM closes the namespace-local netdev and writes
TX/P6 stop commands. Results are in `controller-comparison.json`.
Neither namespace check connects to an optical device.

`scripts/test_fttr_model.py` compares the native width table with the actual
original descriptors, then executes seven original parser cases covering ONU
identity, ONU2-G, T-CONT, queue, VEIP and private Wi-Fi attributes. It verifies
split-record merging, opaque records, both original false-success cases and
native rejection without changing previously collected values. Results are
in `model-comparison.json`. The existing two-peer controller check now also
exchanges split/unknown records and confirms the merged model is revoked on
replacement (`controller-model-comparison.json`). The updated daemon compiles
with host warnings-as-errors and the project's actual OpenWrt musl toolchain.

The focused MIB check additionally compares GEM Create/Delete/Get and T-CONT
Set requests with actual original builders, and checks managed-entity reply
results, Get-mask mismatch and retransmission deadlines (`mib-me-comparison.json`).
The existing private-network-namespace controller check also runs the real
`fttrctl get` executable after releasing the controller lease. Both a successful
Get and an ONU rejection retain their raw replies and return the correct process
status (`controller-me-comparison.json`). The project's actual OpenWrt package
prepare/compile/install recipes pass in the isolated `package-root-me/` stage;
`package-stage-me.log` records them. No full firmware build was performed.

The focused MIB check now executes the original constant initializer and
Alloc-ID allocator at hardware IDs0/5/63 (only occupancy is modeled as free),
and compares native results. The existing AF_PACKET/veth check exercises the
real T-CONT command: assignment/readback before DBA, explicit ONU rejection,
readback mismatch, actual grant-attribute IO failure, an already assigned
entity and replacement before DBA. Results are `mib-tcont-comparison.json`
and `controller-tcont-comparison.json`. The actual OpenWrt package recipes
compile/install into `package-root-tcont/`; `package-stage-tcont.log` records
the isolated build. No firmware or unrelated test suite was run.

`scripts/test_fttr_gem.py` executes the original unicast producers/wrappers
and Create/Delete builders. Both association branches and the optional
descriptor match native wire bytes. It reproduces original ignored Delete
failure and verifies native retained ownership/initial error/cleanup error,
stopped dependency deletion, preservation of a reused descriptor, rejection
of an inconsistent existing dependency, cleanup after a lost Create reply,
and detection of a false successful Delete. ID generation also matches actual
original allocator execution. `gem-comparison.json` records this scope.
The existing AF_PACKET/veth check runs the real GEM executable through
creation/readback, rejected cleanup and replacement while Create is waiting
for a reply; the new peer receives no retransmission, subsequent Create or
cleanup (`controller-gem-comparison.json`).
Existing MIB/ME checks pass with the updated command linked. The actual
OpenWrt musl prepare/compile/install stage is `package-root-gem/`, with output
in `package-stage-gem.log`. This is no optical or firmware acceptance claim.

`scripts/test_fttr_association.py` executes the original GAL, shared/per-UNI
bridge, Mapper and Ethernet/VEIP producers/wrappers/encoders. Their mutation
frames match native constructors, including Ethernet-only filtering and both
extended VLAN Set sequences. It checks independent profile reuse, both
Create/Set readback groups and a mismatch in the last DSCP byte, and reproduces
the original false successful Get with an undecoded DSCP field.
`association-comparison.json` records the exact scope. The focused GEM check
passes through the shared resource engine (`gem-objects-comparison.json`).
The existing AF_PACKET/veth command check also covers profiles, Ethernet,
VEIP and a nonexistent UNI that produces no writes
(`controller-association-comparison.json`). MIB/ME checks pass with all new
command sources linked. The isolated real musl package stage is
`package-root-association/`, with `package-stage-association.log` output.

The executable links with the project's actual OpenWrt GCC 14.4.0/musl
toolchain as `artifacts/fttr/fttrctl-musl`. The optional userspace and kernel
package definitions parse successfully with the pinned OpenWrt make includes.
The two control `.ko` files link under `artifacts/fttr/modules/` using the
existing same-version 6.18.55 kernel's real export table, without unresolved
symbols. `CONFIG_MODULE_STRIPPED=y` removes descriptions and produces the
existing modpost description warnings; both sources contain their description.
This checks module dependencies, not a rebuilt firmware's ABI or live loading.
The real OpenWrt package prepare/compile/install recipes also run in an isolated
stage with the project's musl toolchain and original SerDes source directory.
`package-root-controller/` contains both executables, init script and generated
profiles; `package-stage-controller.log` records the recipe output.

To load a development image, first merge `port/configs/tyfza1-fttr.config` into
its kernel configuration, then select the matching development DT. On the
FPGA unit, load its retained image by writing its firmware-relative filename
to `/sys/bus/spi/devices/<device>/firmware`. Only after successful loading,
bind the previously failed MiniOLT MDIO device through the standard driver's
`bind` attribute. Automatic factory import and sequencing remain pending.
  Optical calibration import now runs independently of upstream BOSA import;
  absent downstream data removes any previous runtime copy.

When building the optional package, set `TYFZA1_PROJECT` to this worktree and
`TYFZA1_FTTR_SERDES_SOURCE` to the matching original root's `/etc/serdes`.
Its prepare/install recipes convert and include both development P6 profiles.
The converter follows the confirmed revision-C file selection and preserves
all original row values/delays. Original profile payloads stay outside Git.
The standalone converter remains `tools/prepare_fttr_serdes.py`.
The NI platform device's `fttr_link` sysfs attribute accepts `9 1` for the
confirmed 9690 sample, or `5 <original-nway>` for the FPGA sample. `0 0` stops
P6. The attribute reports configured mode, physical clock and actual error.
After successful P6 configuration, `fttr0` can be opened as an Ethernet device.
These remain development controls; the packaged hardware service uses them.

On the confirmed ASIC sample, the MiniOLT MDIO device additionally exposes
root-writable `reset`, `host_serdes` and `pon_serdes`. The original ordered
SerDes inputs are: `9 1` for `host_serdes`/NI `fttr_link`, and `3` for
`pon_serdes`, as selected by the retained sample config. Host mode 5 accepts
the matching NWAY setting; PON accepts original modes 1/2/3. Status shows
actual clock/PLL and mode-specific PCS bits plus the last configuration error.
These are hardware states, not ONU/service readiness. The FPGA backend does
not expose these ASIC controls.

After this sequence, write `fttr0`'s actual unicast MAC address to the ASIC
MDIO device's `omci_link` attribute and read it back. The attribute reports
the hardware envelope configuration; the controller uses the reported
EtherType, MAC pair and downstream-length flag. `omci-wire.c` and
`omci-socket.c` under `port/packages/tyfza1-fttr/src/` supply the native frame
and socket interfaces. Keep `fttr0` MTU at 1982 when using long control
frames; the current transport rejects Ethernet frames over 1996 bytes.

The ASIC `onu_table` attribute emits 64 rows with columns
`hardware-ID downstream-word upstream-word EqD serial-hex` (raw hexadecimal
words, decimal zero-based IDs). `onu_block` accepts `<ID> <0|1>`;
`ploam` accepts `<repeat:1..3> <word0-hex> <word1-hex> <word2-hex>`.
`tx_enable` accepts a boolean and uses the original register-20 polarity:
enabled writes 0, disabled writes 1. This is an explicit command, not an
optical-power or laser-readiness measurement. Initialization still leaves
TX disabled until the controller completes its startup sequence.
`core_init` accepts `1` for the ASIC register/table initialization, `dba`
accepts `1` for the original global grants, and `tcont` accepts
`<hardware-ID> <slot:0..7> <fixed> <assured> <maximum>` in original hardware
grant units. These low-level controls do not allocate a subordinate's OMCI MEs.
`management_grant` accepts `<hardware-ID> <0|1>` for the original default
management profile/release. `onu_state` is a fixed-record binary read interface:
`pread(20, hardware-ID*20)` returns LE32 downstream/upstream/EqD followed by
eight vendor-first serial bytes. It avoids rescanning all slots when validating
one completed inventory. The text `onu_table` remains available for inspection.

`port/configs/tyfza1-fttr-packages.config` is the optional OpenWrt package
selection fragment. `sync-overlay.sh` copies the package, and the target's
`modules.mk` packages/autoloads the MiniOLT and GN25L99 modules. Production
package selection and production FIT DT selection stay unchanged. The image
recipe now also offers `fttr-asic.dtb` / `fttr-2p5g.dtb` development artifacts;
their FPGA loader package/startup is still pending.

The retained settings are independently imported into
`/tmp/tyfza1-factory/fttr_startup`; credentials are not part of this file.
The procd `tyfza1-fttr` init script uses START=26 and honors its enable flag.
It locates the bound MiniOLT/GN25L99 child and passes the retained modes to
`fttrd`. The daemon performs bootstrap under its own device lease and then
manages registered ONUs. `/etc/init.d/tyfza1-fttr start` / `stop` controls this
instance; SIGTERM also handles a stop requested during bootstrap. Authentication
and service provisioning remain separate work. FPGA startup is still incomplete.

After the development hardware sequence and actual `fttr0` setup, use:

```
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> status
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> mib <hardware-ID> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> get <hardware-ID> <class> <instance> <mask> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> tcont <hardware-ID> <slot:1..7> <instance> <fixed> <assured> <maximum> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> gem <hardware-ID> <ordinal:1..60> <tcont-instance> <queue-instance> <association:0|1> <profile-instance> <gal-instance> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> profiles <hardware-ID> <bridge-instance> <per-UNI:0|1> <mapper-enabled:0|1> <mapper-instance> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> uni <hardware-ID> <kind:0=Ethernet,2=VEIP> <UNI-instance> <bridge-instance> <port-instance> <ordinal> fttr0
```

`status` emits raw hardware state and the registration bit for all 64 slots.
`mib` takes an exclusive device-directory lock, prepares the original management
grant and reads the actual OMCI envelope,
and streams JSON records. An error exits nonzero without a `mib_complete`
record. This is an explicit MIB inventory operation; authentication policy,
capability decisions and automatic service/roaming management remain work.
The resident controller holds this lease while running; inspect its completed
`onu-<ID>.mib` files and `status.json` during normal service. Raw attribute words
and hardware-registration flags are distinct from its MIB state. Failed scans
leave `.failed` diagnostics and are retried only after an actual registration/
serial transition or an explicit service restart. No data service is reported
ready by this controller yet.

`get` accepts decimal or `0x` hexadecimal class/instance/mask values (the
hardware ID remains decimal). It prints `me_reply` with the original result
byte, native error and complete 44-byte baseline reply, only after confirming
the same registered serial. A nonzero ONU result exits nonzero. An active
controller owns the device lease, so explicit MIB/Get are separate development
operations performed after stopping that controller; they do not bypass it.

`tcont` is a development write operation: it assigns the selected real
T-CONT entity to the generated ASIC Alloc-ID, then configures the selected
hardware DBA slot. Its three bandwidth values use the existing kernel
attribute's hardware grant units, not kbit/s. It emits `tcont_binding` with
the last observed remote assignment, whether a remote write was sent,
whether assignment/serial were verified and the failed/completed phase.
It does not create GEM/bridge/VLAN entities or imply an Internet service.

`gem` is a development write operation for the original unicast CTP/IW pair.
It generates Port-ID/ME instance `256 + hardware-ID + (ordinal-1)*64` and
rejects values above the original4090 limit. The remaining arguments select
the real prerequisite MEs; the original association flag0 produces option5,
and flag1 produces option1. Both target instances must be absent or match
the requested configuration. It emits `gem_state` with the captured serial,
actual observed states, whether each new Create was transmitted (`touched`),
and separate original/cleanup errors and raw replies. Failed cleanup does
not clear those records or claim removal. The resident controller still owns
its lease during normal operation; this command uses separately prepared
development hardware. GAL/profile creation, VLAN/UNI flow installation and
automatic service policy are not performed by this command.

`profiles` creates/reuses the original GAL and selected bridge profile, with
an optional initial Mapper. For the original shared default, select bridge
instance`0x8080`, per-UNI`0`; the per-UNI template uses the real selected UNI
instance and per-UNI`1`. A Mapper starts with all nine TP pointers`ffff` and
unmarked-frame option1; binding its P-bit pointers to GEM IWs is later work.
`uni` takes the real physical/VEIP ME, selected bridge/port ME and zero-based
ordinal. It prepares the original base attachment, TPIDs and default DSCP map.
The commands report `profiles_state` / `uni_state` with the same observed
states, serial and primary/cleanup errors as GEM. A `configured` result applies
to these selected entities, not a complete service or operational UNI.

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
the peer MiniOLT's separate WTG/PON initialization is also implemented.

## Original service evidence for the next implementation

In this exact `libponrelay.so`, `drv_gpon_alloc_id_config` (`43a08`) and
`drv_gpon_gem_port_config` (`43ad0`) perform no IO and return zero. Their names
are not evidence for a programmable local mapping table. Native must follow
the real hardware accesses rather than reproduce these no-op API layers.

`plr_gpon_const_val_init` (`841b4`) initializes Alloc-ID257..767,
GEM Port-ID256..4090, broadcast4094, and one-based ONU hardware IDs1..63.
The raw hardware table still has 64 slots; the library's service-range check
and the physical table size are separate facts. Actual allocator execution
gives `256 + zero-based ONU ID * 8 + T-CONT slot` and
`256 + zero-based ONU ID + (one-based GEM ordinal - 1) * 64`.
The original service allocator additionally checks whether an ID is occupied.

Executing the original unicast producer at `dfbc8`, with only its OMCI/API
boundary modeled, confirms the following order and values:

* Optional class280 traffic descriptor (`ff00`) when a descriptor is requested.
* Class268 GEM CTP Create, mask`fac0`: instance and Port-ID use the selected
  GEM ID; T-CONT and upstream queue are supplied real ME pointers; direction3;
  upstream descriptor is supplied; downstream queue/descriptor and key ring0.
* Class266 GEM IW TP Create, mask`f200`: the CTP pointer uses that GEM entity,
  interworking option5 or1 follows the original supplied association flag,
  service-profile and GAL pointers are supplied, and termination-point pointer0.

Both association branches were executed through the original Create builder
to confirm their wire bytes. These are producer inputs, not proof that every
subordinate supports either association. GAL/bridge/mapper creation and actual
capability-based pointer selection still have to precede this sequence.
The original failure cleanup at `dfe98` ignores Delete failures before clearing
its bookkeeping. This failure was reproduced; native GEM cleanup preserves
the failures/unconfirmed resources and stops further dependency deletion.

## Remaining work toward working FTTR

1. FPGA board startup: import its own retained bitstream, load it before
   binding the MiniOLT transport, and initialize the original post-load registers.
2. Optical identification for the other sample, plus real GN25L99 calibration
   and optical readiness verification. The native sequence is implemented.
3. Real P6/FTTR frame verification. Native SoC P6, ASIC peer WTG/PON SerDes,
   the dedicated RX/TX endpoint, core/DBA and ASIC hardware startup orchestration
   are implemented. Real ONU-management and service traffic still require
   hardware acceptance.
4. OLT-side discovery/registration, authentication, OMCI, T-CONT/GEM/DBA and
   VLAN/service provisioning. The native MiniOLT OMCI framing/transport
   foundation, management grants and automatic baseline MIB inventory are
   implemented, including a native entity/attribute model from original wire
   layouts. Authentication policy, capability-based service decisions and
   automatic provisioning are not. Explicit T-CONT binding/readback/local DBA
   and native GEM CTP/IW lifecycle/readback/cleanup are implemented. Native GAL,
   bridge/initial Mapper and basic Ethernet/VEIP attachment are implemented.
   Mapper binding, ANI/GEM-side bridge ports, VLAN operation rules and complete
   service orchestration remain work.
   Current upstream ONU OMCI is a different role.
5. Original subordinate gateway configuration, Wi-Fi synchronization,
   multicast, reboot/upgrade and roaming coordination, using the connected
   ONU's actual capabilities rather than assuming one fixed subordinate model.
6. FE/PE acceleration and stale-flow invalidation, then hardware acceptance
   with real downstream gateways and a recorded interoperability scope.

No optical startup, ONU registration, service traffic or hardware acceleration
has been verified for this branch. No placeholder online state is exposed.
