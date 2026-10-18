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
  native `fttrctl` and `fttrd` executables. Automatic MIB inventory and explicit
  policy-driven service provisioning are implemented; authentication remains work.
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
* Resident native controller with independent asynchronous MIB/service jobs per
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
  After inventory, an explicit matching service policy starts the same native
  service graph used by the CLI. Object/Set/readback, VLAN table reads/writes
  and failure restoration advance as asynchronous steps through one dispatcher;
  no per-ONU code waits on a private receive loop. Replies route by ONU and TCI,
  including matching malformed replies. Each pending transaction retains its
  own deadline/retries and fresh serial checks. A stalled or failed service
  does not stop other ONU jobs. Cancellation during cleanup preserves the
  original child-transaction error and the separate cleanup failure.
  Registered peers without a policy remain in `inventory`; selected peers
  progress through `provision`, `configured` or `service-failed`. `configured`
  verifies the selected resources, not optical/data forwarding.
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
  entities; the resident policy-driven service graph is described below.
* Native ANI/GEM-side bridge-port attachment and VLAN filtering. The selected
  port/bridge/TP IDs follow actual original producer `d8320`: mask`fc00`,
  TP type5 for direct GEM IW or type3 for Mapper, priority0 and path-cost1.
  Empty VLAN selection uses the original forward-operation0/mask`4000`.
  Nonempty selection uses the twelve-slot VLAN list, forward-operation16
  and the selected count, mask`e000`; entries are big-endian exactly like
  the original custom VLAN encoder. Native verifies all 26 selected bytes.
  `fttrctl ani` selects real prerequisite objects and an explicit VID list.
* Native updates of existing Mapper P-bit pointers. Original producer
  `d78d0` maps priorities0..7 to masks`4000..0080`; native supports partial
  or full selection and the same wire order. Explicit `ffff` clearing uses
  the original low-level Set encoder and original Mapper null-pointer value,
  without overloading a value as the high-level API's no-change selector.
  The shared job engine now handles explicit update-only objects: read and
  retain the actual selected prior bytes, Set only when needed, then Get
  to verify. Failed updates/readback restore and verify those exact prior
  bytes. Release also restores changed attributes. Unknown/failed restoration
  retains the mutation, prior bytes, primary error and cleanup error; no
  Create/Delete substitutes for an existing-object update. `fttrctl mapper`
  maps selected priorities to a real GEM IW, or explicitly clears them.
  Its serial checks cover ordinary writes, restoration and retransmission.
* Native class171 UNI VLAN rules. Six original producer policies are implemented:
  discard all, forward all, discard untagged, discard tagged, translate VID,
  and add a tag to untagged frames. They produce the original one-to-three
  16-byte rows, with matching filter keys and treatment fields. Explicit
  removal uses the original eight-byte filter key plus eight `ff` bytes.
  These are partial table operations; unrelated rules are retained.
  `omci-vlan.c` first reads the real maximum row count (`4000`) and table byte
  length (`0400`), then reassembles all baseline Get Next fragments (29 bytes
  each). A rule may span fragments. Native rejects an invalid table length
  or a table larger than the ONU's reported capacity before writing anything.
  Set success is followed by a fresh full-table read and comparison, including
  the untouched rows. Failed mutation/readback restores the actual prior rows
  (or removes newly added rows), then verifies the complete original table.
  It retains separate primary/cleanup replies and errors. Failed restoration
  invalidates the last snapshot instead of reporting it as current/verified.
  Every request, retry and completion checks the same registered serial;
  replacement stops further transmission and restoration. These explicit
  commands do not activate automatic service provisioning.
* Native explicit service orchestration (`omci-service.c`, `fttrctl service`).
  A fresh MIB scan supplies the actual selected Ethernet/VEIP UNI, an
  unassigned T-CONT and an upstream class277 queue whose Related Port equals
  `(T-CONT instance << 16) | selected priority`. The queue's entity MSB follows
  original `plr_gponrm_queue_write`; lookup follows `f55fc` and original caller
  `aa164..aa1b4`. The original ONU's `mib_Tcont.so` default row selects `00ff`
  or `ffff` via `gInfo+584`; both actual unassigned forms are recognized.
  A T-CONT already owning this Alloc-ID is reused; its missing queue never
  causes duplicate assignment to another T-CONT. Duplicate ownership fails.
  The selected local slot1..7 generates Alloc-ID and GEM Port-ID with the
  original allocators. This explicit single-service policy uses GEM ordinal
  equal to the slot; it does not infer ME IDs from model names or slot numbers.
  Original shared bridge`8080`, GAL1, direct-bridge GEM IW option1, UNI/ANI
  bridge ports and filters form one dependency/rollback plan. Existing exact
  attachments are reused; free port entities are selected within the original
  allocator's1..2048 range, and bridge port numbers are unique. The physical
  UNI administrative state is set/read back as0 (ETH mask`0800`, VEIP`8000`),
  retaining/restoring its prior byte on failure.
  Local DBA reads expose both actual grant words. The service snapshots the
  prior grant, refuses an occupied slot without a matching remote Alloc-ID,
  then programs and reads back the requested grant. It finally applies and
  verifies the original compound VLAN policy: transparent mode4 (`b5704`), or
  access mode1 (`b4c10`: discard tagged, then tag untagged). VLAN failure first
  restores its prior rows; confirmed restoration permits restoring the prior
  local grant and releasing the objects in reverse dependency order. A failed
  VLAN/grant restoration stops dependent cleanup and retains the ownership/
  error records. `configured` describes these verified remote/local resources;
  it does not assert data forwarding, authentication or optical acceptance.
  The resident daemon now runs this graph asynchronously when an explicit
  policy matches. Durable ownership recovery and cross-service policy remain work.
* Resident service policies and runtime journals. `/etc/tyfza1-fttr.services`
  is a preserved package configuration file, loaded by the procd command.
  An exact vendor-first serial matches before the wildcard. A policy names an
  Ethernet/VEIP type and zero-based ordinal; the actual UNI entity comes from
  that registration's sorted MIB. Duplicate/invalid policy rows fail before
  hardware startup. The shipped file contains comments only, so it does not
  invent a VLAN/UNI/network policy for an unknown subordinate.
  `onu-<ID>.service` is atomically replaced with mode0600 and contains selected
  configuration, complete object recipes/ownership/prior values, pending wire
  exchanges and VLAN table snapshots. It is saved before new OMCI sends and
  after processing their outcomes. Disconnect/replacement stops old remote
  requests, clears and reads back the old policy's local DBA slot, then retains
  that generation as `.service-old`. Registration restarts inventory and
  policy selection. A warm controller restart rescans and reuses actual
  matching MEs; previous-process journals are archived before new work, so
  they cannot appear as a current configured result in inventory-only mode.
  These are runtime records in `/var/run`, not a durable write-ahead recovery
  log. The controller does not yet replay prior ownership after a crash or
  power loss.
* Native MIB Data Sync monitoring and reconciliation. After the service graph
  is verified, the controller reads class2/instance0/mask`8000` before reporting
  `configured`, then samples it every two seconds while that ONU is idle.
  The observed remote value supplies the baseline; local operation counts are
  not used to predict it. Original ONU helper `11f68` increments the three-byte
  ONU Data row and wraps255 to1. The reset path `103a0 -> eaa0` installs the
  original zero default through `eb04..eb30`. Both paths are exercised with
  only MIB storage/access modeled. The original Get builder and ONU Get worker
  confirm the one-byte attribute's actual request/reply format.
  A changed value invalidates the selected configuration, clears/reads back
  its local data grant, archives the prior record and starts a fresh MIB scan
  and policy selection for that same registered serial. It sends no MIB Reset
  and does not alter the remote Data Sync value. Counter changes are evidence
  for reconciliation, not a claim that every change is a reboot.
  An existing configuration can have a zero baseline. When zero remains zero,
  the controller reads back the selected GEM CTP's complete fourteen-byte
  attribute group. Matching actual data keeps it configured; an absent or
  changed CTP triggers reconciliation. This detects MIB loss even when both
  serial/registration and the zero sync value stay unchanged, while avoiding
  repeated full scans of an intact zero baseline.
  Failed probes clear the current configuration/verification flags, withdraw
  only that ONU's selected local grant and retain the first failing exchange.
  Subsequent read-only probes wait for an actual valid reply before resuming
  inventory. If that inventory itself fails, probing/reconciliation continues
  without claiming success; the failed MIB reply is retained in `.failed`.
  The same readiness probe also resumes an initially failed inventory when an
  explicit service policy requests that registered serial.
  Before a new service job exists, probe records use private atomic `.sync`
  and `.sync-old` files. An explicit configuration rejection remains a failed
  service. Unrelated ONU jobs continue throughout. A probe never interleaves
  with that same ONU's MIB/service table transactions. Shared IO failures stop
  the controller as before. These checks verify management configuration;
  they do not verify data forwarding or replay ownership after a process crash.
* Native service data ports (`fttr-net.c`). The retained original
  `/etc/scripts/onu_setup.sh` action1 receives an outer VID from `onuinfo`,
  selects that outer TPID/VID on RX and removes one tag, and pushes one tag
  on TX while preserving customer tags. The four-GbE unit selects TPID`8100`.
  `onuinfo`'s active-handler block `40754c..407560` stores one-based ONU ID
  plus255 as that first service's VID; `407724..407770` passes it to the
  script. This equals the first GEM Port-ID (`256 + zero-based ONU ID`),
  rather than the script's separately configured physical-interface PVID.
  Native creates an 802.1Q child `fttr-u<ID>` on `fttr0` with this service's
  actual GEM ID, then attaches it to the policy's explicit existing/up bridge.
  The child uses that bridge's MTU; its MTU plus the outer four bytes must fit
  `fttr0`. Linux materializes this tag for the current FTTR endpoint, which
  advertises no hardware VLAN TX insertion. Existing bridge VLAN filtering
  remains the administrator's network policy; transparent customer VLANs
  require a bridge that admits them.
  Creation happens only after the remote service graph, local grant, VLAN
  rules and initial sync/probe have been verified. Periodic sync completion
  also checks the actual child identity, GEM tag, master, MTU and up flag.
  A data-port failure is exposed as `service-failed` with `datapath_error`,
  withdraws its local grant and retains any separate cleanup error. It does
  not silently become an OMCI-only service or restart unrelated peers.
  Disconnect, replacement and MIB reconciliation remove the port before
  withdrawing its grant; kernel bridge removal flushes its learned FDB.
  Shutdown disables optical/P6 forwarding and removes all owned ports.
  The creation request marks the device with group`0x46545452`; a subsequent
  existing-link request applies its ONU serial/GEM alias and bridge. This
  follows the actual Linux `rtnl_create_link` behavior: it applies GROUP but
  ignores IFALIAS. Startup deletes prior owned children, including an
  interrupted down child before alias setup, before any new inventory.
  Parent/name/kind/group and nonempty alias ownership mismatches are exposed
  without deleting an unrelated or reassigned device. This handles local
  port lifecycle; it is not durable recovery of remote ME ownership or FE/PE
  flow invalidation. `datapath_configured` describes verified kernel setup,
  not measured optical throughput.

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
VEIP and a nonexistent UNI that produces no ONU configuration writes
(`controller-association-comparison.json`). MIB/ME checks pass with all new
command sources linked. The isolated real musl package stage is
`package-root-association/`, with `package-stage-association.log` output.

The same focused association check now executes actual original ANI direct/
Mapper producers with zero/two VLANs, the high-level Mapper mask producer,
and the low-level explicit-clear encoder. Native mutation frames match.
It also verifies previous-value retention, no-op reuse, successful restoration
on failed readback/release and an explicitly rejected restoration
(`ani-mapper-comparison.json`). Existing GEM and association checks pass
through the extended engine. The actual AF_PACKET/veth command check covers
transparent/list ANI, both TP types, Mapper binding/clearing, rejected Set,
rejected restoration and replacement while Set awaits a reply. It confirms
there is no retransmission or restoration to the replacement peer
(`controller-forwarding-comparison.json`). MIB/ME checks and the isolated
real musl package build/install pass (`package-root-forwarding/`,
`package-stage-forwarding.log`). These checks do not exercise optical traffic.

`scripts/test_fttr_vlan.py` executes original master producers `fb9b8`,
`fbeb0`, `fbbcc`, `fbd08`, `fc20c`, `fc0c0` through the actual Set encoder.
The delete producers `fc420`, `fc654`, `fc7b0`, `fcad4`, `fc978` confirm deletion
bytes. All six native policies and VID/priority boundary examples match.
The same firmware's ONU program `bin/omci_app` supplies the original
`OMCI_NormalizeMsg`, Get worker `13e00`, Get Next worker `122d4`,
`OMCI_ResponseMsg` and serializer `beb4`. Only their MIB storage/access API
is modeled. This confirms the four-byte table length and 29-byte fragments
using the actual response construction and CRC code, rather than assuming
that the master library's ordinary Get parser can read a table.
The native CLI runs over real AF_PACKET/veth in a private namespace against
these replies. Focused cases cover empty/multiple-fragment reads, add/reuse/
remove, unchanged unrelated rows, exact prior-row restoration, false successful
Set, partial Set rejection, failed restoration, invalid/capacity-exceeding
tables and serial replacement before retransmission. Results are in
`vlan-comparison.json`; this is offline protocol evidence, not optical/ONU
interoperability acceptance. The shared MIB/ME check passes with VLAN linked
(`mib-vlan-comparison.json`). Actual GCC14.4/musl package prepare/compile/install
passes in `package-root-vlan/` (`package-stage-vlan.log`). No full firmware
build or full test suite was run for this change.

`scripts/test_fttr_service.py` executes the original high-level access and
transparent policy producers, original GEM/GAL/bridge/ME builders and the ONU
T-CONT default initializer. It uses original attribute descriptors and ONU
table Get/Get Next replies. The actual native command runs over AF_PACKET in
a private namespace; the peer models MIB storage and the synchronous DBA
sysfs boundary. Ethernet transparent and VEIP access services configure and
verify the full graph, local grant, UNI state and VLAN rows. Repeating the
command with the resulting real MIB causes no configuration writes. Cases
cover unavailable queues, duplicate Alloc-ID ownership, an existing owner
without the requested queue, occupied local grant, GEM rejection, failed
object deletion, false-successful grant writes/restoration, partial VLAN
failure and rejected VLAN restoration (`service-comparison.json`). The narrow
core/DBA comparison also checks the actual two-word grant readback
(`core-grant-readback.json`). Shared MIB/ME checks pass with the service linked
(`mib-service-comparison.json`). Actual musl package prepare/compile/install
passes (`package-root-service/`, `package-stage-service.log`), as does the
isolated6.18.55 module build against the retained real export table
(`modules-service.log`, with the existing stripped-description warnings).
These checks do not exercise optical traffic or the ASIC data-forwarding path.

`scripts/test_fttr_autoprovision.py` runs the actual resident daemon with two
independent peers over AF_PACKET/veth. Original ARM64 attribute descriptors and
ONU Get/Get Next replies supply protocol evidence; peer MIB storage and the
synchronous DBA sysfs boundary are modeled. A withheld T-CONT response on one
ONU does not block the other service. Exact/wildcard policies select distinct
actual Ethernet/VEIP instances. Serial replacement during an ordinary Set and
during failed-GEM cleanup stops old transmissions, withdraws local bandwidth,
retains the original/cleanup errors and provisions the new registration.
Warm restart without hardware bootstrap and an observed same-serial link
disconnect/reconnect reuse actual remote MEs. Duplicate policies fail before
hardware changes. Omitting policies preserves inventory-only operation and
archives stale current journals. Mode0600 journal snapshots and SIGTERM
hardware shutdown are checked. Taking the shared packet endpoint down while
a service awaits a reply also confirms controller failure and TX shutdown
(`autoprovision-comparison.json`). The prior
focused service/VLAN checks pass through the asynchronous engines
(`service-async-comparison.json`, `vlan-async-comparison.json`). Actual musl
package prepare/compile/install passes in `package-root-autoprovision/`
(`package-stage-autoprovision.log`), including the policy conffile and procd
argument. No full firmware build or full test suite is used here.

`scripts/test_fttr_datapath.py` executes the retained SMUX module's actual
`smuxdev_vlan_push.part.0` and RX `smuxdev_vlan_pop.part.0` instructions,
modeling only skb pointer helpers and memmove. It also executes the original
`onuinfo` VID assignment. Actual native rtnetlink-created VLAN/bridge ports
forward untagged, single-tagged and QinQ frames in both directions over veth;
the resulting frames match the original bytes. The full1500-byte QinQ
customer payload case explicitly uses LAN/bridge MTU1508. Unknown GEM tags
are not delivered to that LAN; ingress frames are not reflected to the ONU.
Veth RX tag metadata is
reconstructed through PACKET_AUXDATA before comparison. The check confirms
learned FDB removal, stale-port cleanup and explicit ownership mismatch
failure (`datapath-comparison.json`). The resident-controller check now uses
real VLAN/bridge ports and verifies their create/remove/recreate lifecycle
across MIB loss, replacement, reconnect and shutdown. A missing requested
bridge fails that peer and withdraws its grant; an explicit `-` bridge keeps
OMCI-only provisioning separate (`autoprovision-datapath-comparison.json`).
The package's actual musl prepare/compile/install passes in
`package-root-datapath/` (`package-stage-datapath.log`). These are private
network namespace checks; they do not establish optical or FE/PE acceptance.

The same focused controller check now executes original ONU sync increment
`11f68`, the ONU Data reset block `eb04..eb30`, and the actual
`mib_OntData.so` default initializer. It checks0/1/254/255 transitions and
zero reset. Sync responses run through the original normalizer, Get worker,
response builder and CRC serializer. With hardware registration and serial
bytes held unchanged, a peer MIB reset selects newly reported UNI/T-CONT/queue
instances and reconfigures the service. A forced failed reconciliation reply
retains its actual body/error and resumes only after a new valid sync reply.
An intact adopted zero baseline stays configured without repeated scans;
subsequent zero-to-zero MIB loss is detected by the actual missing CTP. Probe
timeout withdraws only the affected grant, retains its three-attempt failure,
and resumes from fresh inventory when replies return, reusing intact remote
MEs. Initial inventory failure for a requested service is also exercised.
The other ONU is neither stopped nor rescanned. Results are in
`autoprovision-sync-comparison.json`. Actual musl package prepare/compile/
install passes in `package-root-sync/` (`package-stage-sync.log`). No optical
traffic or full firmware/test build is exercised.

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
`tcont_state` provides eight-byte LE records at `(hardware-ID*8+slot)*8`:
fixed grant followed by the packed assured/maximum word. Reads hold the same
control lock as grant programming and access the actual `base+1` and `base`
registers, not a software copy of a successful write.

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
instance; SIGTERM also handles a stop requested during bootstrap. The procd
command loads `/etc/tyfza1-fttr.services`; authentication and FPGA startup remain work.

Service policy rows have ten fields:

```
serial-or-* kind UNI-ordinal slot priority VID-or-none fixed assured maximum bridge-or--
```

The serial is16 hexadecimal digits in the hardware inventory's vendor-first
order. Kind0 selects Ethernet; kind2 selects VEIP. Ordinals are zero-based
within that actual MIB class. Slot1..7, priority0..7, VID0..4094/`none` and
hardware grant units have the same meanings as `fttrctl service`. There is
one policy per exact serial and at most one wildcard; exact matches win.
The final field names an existing/up Linux bridge (for example `br-lan`), or
`-` to request only OMCI/local-grant provisioning. The bridge's MTU plus four
outer tag bytes must fit the FTTR parent. A requested bridge failure is
reported explicitly, with no data-ready claim. The prior nine-field
development format is rejected; add the intended bridge or `-` explicitly.
An ordinal not present on the actual peer produces `service-failed` without
configuration writes. Edit the policy file and restart this development
service to load changes. A direct daemon launch can use
`--services <policy-file>` after its existing positional arguments; omitting
the option performs inventory only. No active policy ships by default.

After the development hardware sequence and actual `fttr0` setup, use:

```
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> status
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> mib <hardware-ID> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> get <hardware-ID> <class> <instance> <mask> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> tcont <hardware-ID> <slot:1..7> <instance> <fixed> <assured> <maximum> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> gem <hardware-ID> <ordinal:1..60> <tcont-instance> <queue-instance> <association:0|1> <profile-instance> <gal-instance> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> profiles <hardware-ID> <bridge-instance> <per-UNI:0|1> <mapper-enabled:0|1> <mapper-instance> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> uni <hardware-ID> <kind:0=Ethernet,2=VEIP> <UNI-instance> <bridge-instance> <port-instance> <ordinal> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> ani <hardware-ID> <port-instance> <bridge-instance> <port-number> <mapper:0|1> <TP-instance> <VID-list|none> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> mapper <hardware-ID> <mapper-instance> <P-bit-mask> <GEM-IW-instance|0xffff> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> vlan-table <hardware-ID> <EVTO-instance> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> vlan <hardware-ID> <EVTO-instance> <policy:0..5> <input-VID> <output-VID> <priority:0..7> <remove:0|1> fttr0
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> service <hardware-ID> <UNI-kind:0|2> <UNI-instance> <slot:1..7> <priority:0..7> <VID|none> <fixed> <assured> <maximum> fttr0
```

`status` emits raw hardware state and the registration bit for all 64 slots.
`mib` takes an exclusive device-directory lock, prepares the original management
grant and reads the actual OMCI envelope,
and streams JSON records. An error exits nonzero without a `mib_complete`
record. This is an explicit MIB inventory operation; authentication policy,
broader capability decisions and roaming management remain work.
The resident controller holds this lease while running; inspect its completed
`onu-<ID>.mib` files and `status.json` during normal service. Raw attribute words
and hardware-registration flags are distinct from its MIB state. Failed scans
leave `.failed` diagnostics and are retried only after an actual registration/
serial transition or an explicit service restart when no service policy
requests the peer. For a requested service, failed inventory waits for a valid
read-only readiness reply before another scan. `status.json` also reports
the loaded policy count and each peer's service configuration/cleanup status.
For configured services it also reports the last observed MIB sync baseline,
value, error, verification time and reconciliation count. `sync` is the
initial verification phase before `configured`. Runtime object/table records
retain their last observations; a failed sync/current configuration flag
does not assert those remote objects still exist.
No operational data-forwarding claim follows from MIB or configuration status.

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
unmarked-frame option1. Use the explicit Mapper step to bind its P-bit
pointers after the selected GEM IW exists.
`uni` takes the real physical/VEIP ME, selected bridge/port ME and zero-based
ordinal. It prepares the original base attachment, TPIDs and default DSCP map.
The commands report `profiles_state` / `uni_state` with the same observed
states, serial and primary/cleanup errors as GEM. A `configured` result applies
to these selected entities, not a complete service or operational UNI.

`ani` configures the selected ANI/GEM-side bridge-port and filter. Port
numbers are1..255. The TP is a real GEM IW when mapper0, or a real Mapper
when mapper1; its existence is queried before business configuration writes.
VIDs are an explicit comma-separated list (up to12 entries, decimal or hex),
or `none` for the original unfiltered operation. It reports `ani_state`.
`mapper` uses an eight-bit P-bit selection: bit0 selects priority0 and bit7
priority7. Selected entries point to the supplied real GEM IW; `0xffff`
explicitly clears them. It reports `mapper_state`, `operation=update`, prior
wire bytes and actual verified/restored/unknown state. An unchanged mapping
causes no Set. Restoration failures remain distinct from the first failure.
These are development operations; authentication and automatic service policy
remain separate work.

`vlan-table` reports the actual class171 table as `vlan_table`, including
capacity, declared/received byte counts, completeness and the last raw ME
exchange. `vlan` reports `vlan_state` with the serial, before/last observed
table, sent-row bitmap, verified/restored flags and separate original/cleanup
errors and wire replies. A failed rollback leaves the last observed table
incomplete/currently unverified even if its recorded bytes were read earlier.
Policies0..5 are respectively discard-all, forward-all, discard-untagged,
discard-tagged, VID translation and tag-untagged. Translation uses both VIDs;
tag-untagged uses output VID and priority. Other policies ignore these three
fields (supply0). VID values used by a policy are0..4094. `remove=1` removes
only that policy's filter keys. A configured result applies to these selected
rules, not a complete forwarding path or supported subordinate model.

`service` performs a fresh inventory and selects real T-CONT/queue resources
for the explicitly selected UNI. Kind0 is Ethernet; kind2 is VEIP. `none`
selects the original transparent UNI policy; a VID0..4094 selects the original
access policy with the chosen priority. Bandwidth values are the existing
hardware grant units, not kbit/s. It emits `service_state`, `service_objects`
and (when reached) `service_vlan`, retaining the captured serial, actual
resource IDs, grant observations and primary/cleanup diagnostics. It uses the
same exclusive device lease as the other development commands. No automatic
onboarding or running service journal is created by this explicit operation.

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

### P6 FE admission evidence

The exact NI module's `aal_ni_port_rx_ctrl_set` (`9b10`) accepts ports0..5.
Executing its selector update for port0 writes `a5f8` bits16..21; port6
returns2 with no MMIO. `aal_ni_xaui_mac_rx_ctrl_set` (`6ca0`) is a no-op that
returns zero. The ordinary LAN selector address formula is therefore not a
valid P6 admission implementation (`fe-p6-admission-probe.json`).

`tools/trace_stock_fttr_cf.py` executes the actual
`fc_8277c.ko:rtk_8277c_cls_init` producer, then passes its240-byte recipes
through the actual `ca-ne.ko` CF key/action setters and indirect writers.
Only formatting, spinlocks and indirect-table hardware are modeled. The
producer deletes CF entries0..15, creates entry0 matching source7/care63
with forward action3 and destination24, then entry1 matching any source
with the same action and destination25. The latter is the original LAN
connector; it includes source6. This identifies the original L2 CF route
to the L3 classifier, separately from the LAN MAC bypass selector.

Narrowing that original recipe to source6/care63 produces TCAM X words
`fffffe7f ffffffff ffffffff`, Y words`fffff1bf ffffffff ffffffff` and action
words`00000000 00001960 00000000`. The instruction trace confirms the key
port field, valid bits and three-word action format; this narrowed rule is
a proposed native input, not a rule observed installed on a live unit.
CF TCAM uses indirect control`1e04`, data registers`1e10/1e0c/1e08`, and
the second bank at entry+512. Action control is`1e14`, with data at
`1e20/1e1c/1e18`. The trace and original hashes are retained in
`cf-p6-producer.json`. It also shows the original add publishes the valid
key before its new action. Native runtime activation must prepare its
action before publishing the key, with readback and errors retained.

P6 admission and FE port registration now use this CF transaction, coupled
to synchronous FE/cache withdrawal when a service is removed. The existing
asynchronous bridge worker alone does not provide that barrier.
In the selected kernel, `__netdev_upper_dev_unlink` ignores the return from
both PRECHANGEUPPER and CHANGEUPPER notifications, so a notifier rejection
cannot report failed hardware cleanup to `fttrd`; the native NI sysfs
operation described below returns those failures directly. Actual P6 traffic
verification remains required before claiming FTTR hardware acceleration.

`rtl9617c-fttr-cf.h` now implements the native CF slot transaction from those
actual bytes and register accesses. It selects a currently unused slot,
retains its exact X/Y/action words, and rejects a preceding live rule that
could shadow source6. It prepares and reads back the action before publishing
the source6 key; subsequent CPU16/LAN25 transitions update the owned action.
Release first invalidates and reads back the live key, then restores and
checks the original action and both TCAM banks. An indirect-operation error
or unequal readback is returned; failed restoration retains the slot's owner,
saved words and distinct original/cleanup errors for an explicit retry.

`scripts/test_fttr_cf.py` compares the actual native header with execution of
the original FC producer and NI writers. It checks action-before-key ordering,
CPU16 selection, preservation of the source7 rule and exact prior slot,
an acknowledged write that did not change hardware, a completion timeout
after a committed write, failed retirement/retry and a shadowing wildcard.
Results are in`cf-native-comparison.json`.

### Native P6 FE lifecycle and acknowledged withdrawal

The FTTR parent is now registered with FE as physical source6/destination6,
separately from upstream source7/LLIDs, and supplies its own TC callback.
Opening `fttr0` acquires the CF slot and installs the CPU16 action. When FE
is globally enabled and FTTR admission is requested, its action switches to
the original LAN connector25. FE setup also changes only P6's NI output
selector (bits12..13 of`a1e0`) to direct L2TM output; the existing snapshot
restores it on global FE disable. No invalid LAN MAC selector is used for P6.
P6 close withdraws its flows and restores the exact prior CF slot. Failed
cleanup remains owned and visible for retry. Removing the FE owner performs
withdrawal while its bridge epoch owner still exists, then stops that owner;
this avoids invalidating an already freed learning context.

The NI device's `fttr_accel` attribute accepts`0` or`1`. A zero first blocks
new P6 flow installation, switches the live CF action to CPU16 with readback,
invalidates queued bridge-learning generations, and synchronously deletes
every existing flow that receives from or sends to P6 through the existing
Hash age/cache withdrawal transaction. Other physical ports' flows stay
installed. Existing FTTR ports continue through Linux while admission is
closed. A one repeats the withdrawal check before permitting admission,
so an unfinished failed deletion cannot be bypassed by an enable request.
Global FE enable prepares its tables before P6 admission; an unconfirmed
P6 rollback retains the prepared snapshot instead of restoring dependent
tables underneath an unconfirmed route.

`fttr_accel` reads report`requested`, `admitted`, `live`, `owned`, CF index,
destination, primary FE/CF errors and separate withdrawal/CF restoration
errors. Requested admission does not enable the global FE owner by itself;
`admitted=1` also requires that owner and the FTTR parent to be active.
It describes programmed setup, not measured hits, bandwidth or an optical
link. Coordination of global FE ownership for standalone FTTR remains work.

The resident controller requires this new NI operation. Startup withdraws
before deleting prior-process ports. Service replacement/reconciliation
withdraws while the old VLAN still exists, removes that port/FDB, clears
and reads back the local grant, then permits learning from current ports.
On failure it retains the error, does not resume admission, and stops the
shared TX/netdev/P6 path. Shutdown also withdraws before closing the parent.
The controller does not treat a missing operation as successful cleanup.

`scripts/test_fttr_fe.py` executes the production P6 control functions with
modeled indirect hardware and flow deletion. It checks verified admission,
both-direction P6 withdrawal, an unaffected LAN/PON flow, failure ownership
and retry. The controller's existing private-namespace check additionally
records actual VLAN existence at each modeled kernel call and injects a
withdrawal timeout (`autoprovision-p6-fe-comparison.json`). The startup check
includes the new operation (`startup-p6-fe-comparison.json`); existing LAN
FE enable/restore checks still pass. The actual6.18 NI composite driver
object compiles/links, and the musl package prepare/compile/install passes
in`package-root-p6-fe/` (`package-stage-p6-fe.log`). No full firmware build
or full test suite is used.

This implements CF admission and FE/cache lifecycle, not complete physical
queue retirement. NI/PE and MiniOLT packet-buffer drain/flush before GEM
reuse still require original-path research and implementation. Existing FE
parser/editor support is limited to two wire VLAN tags, including the outer
GEM tag; customer QinQ with three total tags continues through Linux. Traffic
between two ONUs on the same physical P6 also retains the current software
path. Actual P6 Hash keys/hits, optical traffic and throughput remain unverified.

### P6 ES queue flush and remaining producer barrier

`tools/trace_stock_fttr_queue.py` executes the exact NI module's
`aal_l2_tm_es_voq_flush_set` (`43220`) together with
`aal_l2_tm_es_voq_status_get` (`43190`). Physical port6 uses control`234c`:
bits8..10 select COS0..7 and bit15 starts flushing. Occupancy is at`23d8`
bits16..23; a set bit is occupied. The original getter inverts that bit.
Both already-empty and delayed-completion paths confirm the write sequence.
On a continuously occupied queue, the original flush executes4002 status
reads, overwrites its local empty result with1, clears flush enable and
returns0. This false-success path is reproduced with the original instructions
in`queue-stock-comparison.json`; it cannot be copied as a completion check.

`rtl_fttr_p6_flush_queues` now implements this ES stage natively. Each of
the eight queues is selected/flushed and its real empty bit polled with a
100ms limit. An unapplied command returns`-EIO`, a stuck queue returns
`-ETIMEDOUT`, and a final all-queue observation that has refilled returns
`-EAGAIN`. The original COS/flush control fields are restored even after
failure and read back. Completed-per-queue bits, failed queue number, last
occupancy, primary error and separate restoration error remain in the P6
state. Existing flush ownership is rejected before writes. These state
fields record an ES observation; they do not assert that the entire FTTR
path is empty.

`scripts/test_fttr_queue.py` compares the actual native function's writes
with original execution, and covers timeout, an unapplied command, failed
control restoration (including simultaneous timeout), refill and existing
flush ownership. Poll time and hardware occupancy are modeled; results are
in`queue-native-comparison.json`. The6.18 composite NI object containing the
new function compiles and links. No full firmware/test run is used.

The function is deliberately not called from shutdown or per-service reuse
yet: stopping software submission and clearing Hash/cache do not establish
that DMA/PE can no longer refill ES. The same original-code trace executes
`ca_dma_tx_complete_easy` (`a5a10`) and confirms its two-descriptor retention:
software read0/hardware read2 frees none; hardware read3 frees descriptor0;
software read62/hardware read2 frees62/63 after wrap. A DMA read/write-pointer
equality therefore does not, by itself, establish downstream packet retirement.
Integration requires that producer barrier; the existing synchronous FE/cache
withdrawal remains in place.

The original MiniOLT diagnostic table at`libasicdrv.so:2a010` (137 entries,
16-byte rows, selected by`Dump_FTTR_FPGA_AllReg`) names Ethernet/PON buffer
warnings at`30040..30043`, PON TX error/out/in counters at`30084..30086`, and
PON RX drop/out/in counters at`30087..30089`. Extracted rows are retained in
`miniolt-buffer-registers.json`. These are labels and counters, not a proved
empty-buffer condition or a per-GEM flush protocol. `drv_gpon_ni_deactive`
and`drv_gpon_onu_clear` in the retained relay are no-op implementations.
DMA/PE producer retirement and MiniOLT buffer retirement still require their
actual original implementation or further hardware evidence before GEM reuse
can be declared fully protected.

Further disassembly of the relay's local deactivation helpers shows
`479d0 -> 4788c`: set bit16 at`40401 + 2 * ONU`, register a timer and send
a Deactivate PLOAM. The callback at`4775c` checks the software keep-blocked
flag and clears that hardware bit when permitted. This call chain does not
probe queue emptiness. It cannot serve as the missing MiniOLT buffer barrier;
blocking an ONU and proving that its old packets are gone are distinct steps.

### Shared DMA/AFT reset correction

The original `ca_ni_dma_lso_reset` in `.text.unlikely` (`1ac64`) asserts
DMA control bit8 and AFT control bit31 at DMA base offsets`000` and`f00`,
then clears DMA reset and AFT reset in that order. Only afterward does it
rebuild rings, reset pointers and initialize DMA-LSO. Executing the original
function with those unrelated callees modeled confirms all four MMIO writes.
The prior native start/stop code reset only the DMA control block, omitting
the paired AFT reset despite reusing the descriptor/packet storage on startup.

`rtl_dma_tx_reset` now performs both resets. Native start/stop first disables
the selected VP and DMA submission; it preserves unrelated control bits,
asserts DMA/AFT reset, keeps the existing10us reset interval, then clears
DMA/AFT reset and reads back the AFT control register to complete posted IO.
This corrects the existing shared-engine lifecycle. It does not reset the
global DMA engine during an individual ONU replacement, and does not claim
that NI/PE or the MiniOLT's separate buffers have been retired.

`scripts/test_fttr_dma_reset.py` failed against the prior native stop code
because the two AFT writes were absent. It now matches original execution
for two nonzero control/configuration seeds after disabling submission
(`dma-aft-reset-comparison.json`). The actual6.18 composite NI driver object
also compiles/links. Hardware reset effects and end-to-end packet retirement
remain unverified; this fix supplies the missing original reset sequence.

### Original remote Wi-Fi configuration transaction

`tools/trace_stock_fttr_wifi.py` executes
`libponrelay.so:plr_gponrm_onu_wifi_set` (`ef5a4`), its real per-class Set
producers/adapters and baseline encoders. The protocol uses existing
class`ff69` (Wi-Fi configuration) and`ff6a` (security) with the same supplied
instance. The radio group is mask`d000` (mode/channel/bandwidth) for original
mode codes3/4/6/7 and`c000` (mode/channel) otherwise; SSID is a separate
mask`2000` group. Security code0 sends mask`8000`; codes5/6 send mask`a800`
(security/authentication/key-index) then four mask`0400/0200/0100/0080`
26-byte key groups. The other original branch sends mask`8040` (security
and encryption codes) and mask`4000` (24-byte shared key). SSID-enable and
radio-enable masks`0020`/`0010` are last. Mode and security values here are
wire codes, not an asserted mapping to OpenWrt/hostapd settings or a claim
of Wi-Fi6/WPA3 support by those private classes.

The original input layout has32-byte spacing for the four indexed keys
(offsets114/146/178/210), while their wire fields are26 bytes. SSID and shared
key wire fields are24 bytes. The native API accepts an explicit byte length
and rejects oversized used fields instead of truncating an OpenWrt setting.
Synthetic names/keys in the retained trace are test data, not device secrets.
The original master stops when a Set fails, without restoring earlier groups;
the trace reproduces a third-group failure after the first two Sets.

`omci-wifi.c` now builds these update groups natively and runs them through
the existing asynchronous ME transaction. It requires the actual MIB's
existing configuration/security instance, probes every selected attribute
group before any Set, skips unchanged groups, reads back each write and
restores touched groups on rejection/readback failure. If restoration fails,
the first error, cleanup error and unresolved groups remain available. No
vendor runtime or fixed-model assumption is used. Its diagnostic function
reports group states and result codes without SSIDs, keys, prior values or
raw OMCI exchanges. Callers must use it instead of the generic object dump
when presenting this transaction's diagnostics.

`scripts/test_fttr_wifi.py` verifies native bytes against five original
radio/security combinations, all-read-before-write ordering, unchanged
configuration, unsupported/missing entities, field limits, rejected writes,
wrong readback, restoration and failed restoration. Protocol group replies
and MIB storage are modeled (`remote-wifi-comparison.json`); the original
producer trace is`remote-wifi-producer.json`. The musl package compiles and
stages both tools with the new module (`package-root-remote-wifi/`,
`package-stage-remote-wifi.log`). This is a configuration transaction module,
not an enabled daemon/CLI workflow yet. Automatic main/subordinate settings
mapping, runtime integration, alternative capability branches and actual
subordinate execution/roaming remain work; a successful modeled transaction
is not wireless acceptance.

### Outstanding implementation and acceptance

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
   layouts. Authentication policy and broader capability-based service decisions
   remain work. Explicit T-CONT binding/readback/local DBA
   and native GEM CTP/IW lifecycle/readback/cleanup are implemented. Native GAL,
   bridge/initial Mapper and basic Ethernet/VEIP attachment are implemented.
   Mapper binding and ANI/GEM-side bridge ports/VLAN filtering are implemented.
   UNI VLAN operation rules, table readback and exact prior-rule restoration
   are implemented. Explicit fresh-MIB selection, direct-bridge service
   orchestration and local DBA readback/restoration are implemented. Resident
   provisioning from explicit policies and asynchronous per-ONU dispatch are
   implemented, with runtime journals and observed disconnect/replacement
   handling. MIB sync monitoring now also detects/reconciles same-serial MIB
   loss without an observed registration transition. Durable ownership
   recovery of remote resources, default policy import and multiple services
   per ONU remain work. Explicit bridge attachment through native per-GEM
   VLAN ports, their lifecycle and kernel FDB removal are implemented.
   Native P6 CF/FE admission and synchronous Hash/cache withdrawal are
   implemented. Physical queued-packet retirement and hardware acceptance
   remain work.
   Current upstream ONU OMCI is a different role.
5. Original subordinate gateway configuration, Wi-Fi synchronization,
   multicast, reboot/upgrade and roaming coordination, using the connected
   ONU's actual capabilities rather than assuming one fixed subordinate model.
   The original ff69/ff6a Wi-Fi update transaction is implemented with
   preflight/readback/restoration; settings mapping, a runtime caller and
   actual subordinate capability/behavior verification remain work.
6. FE/PE acceleration and stale-flow invalidation, then hardware acceptance
   with real downstream gateways and a recorded interoperability scope.

No optical startup, ONU registration, service traffic or hardware acceleration
has been verified for this branch. No placeholder online state is exposed.
