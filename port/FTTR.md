# TYFZA1 native FTTR worktree

Original base: `dcf470f` (`0.7.0-alpha`). Branch `fttr-rewrite` now includes
`main` through `9331c6e`, merged in `61d24e6`.
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
  SPI device's root-writable `firmware` sysfs attribute for manual DTs. The FPGA
  development DT now requests its named boot image during probe and makes
  MiniOLT binding depend on successful loading; see the FPGA boot section below.
  The name is relative to the firmware directory. Original `top.bin` stays outside Git.
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
  ownership and detaches RX before freeing the device. P6 CF admission and
  FE flow programming are implemented below; real hardware hits and packet
  retirement remain unverified.
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
  existing2048-byte DMA TX storage. With the current64-byte headroom/384-byte
  tailroom CPU QM layout, RX frames above1592/1584 payload bytes use two
  EPP buffers and are reassembled natively (see the segmented RX section). The MAC threshold is 2000; the shared NI maximum
  is raised to 2000 only when it is smaller. Both fields come from actual
  original `aal_ni_xge_mac_frame_len_set` / `aal_ni_pkt_len_set` execution.
  Ordinary 1500-byte configuration would reject long OMCI frames in AF_PACKET.
  Larger frames remain outside this buffer design and return a length error.
* ASIC ONU table/control primitives: all 64 hardware slots report raw downstream
  state (`40401 + 2*ID`), upstream state (`40803 + 4*ID`), EqD and the original
  vendor-first serial bytes. The registration flag is downstream bit18 from
  original `ONU_Online`; it is not a claim that OMCI/service provisioning passed.
  Native block control changes only downstream bit16. Its lifetime belongs to
  the controller; the original15-second callback checks the per-ONU keep flag.
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
FPGA development DT, select the optional FPGA package and supply its retained
image as described below. Its loader requests the image during probe; MiniOLT
binding is deferred until loading succeeds and DONE is high. Manual DTs without
a boot image retain the SPI `firmware` attribute. Full FPGA optical/core/service
startup remains incomplete.
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
the optional FPGA loader/firmware package now supplies the image-to-MDIO
binding stage. Full optical/core/service sequencing is still pending.

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
serial-or-* kind UNI-ordinal slot priority VID-or-none-or-filter:VID fixed assured maximum bridge-or--
```

The serial is16 hexadecimal digits in the hardware inventory's vendor-first
order. Kind0 selects Ethernet; kind2 selects VEIP. Ordinals are zero-based
within that actual MIB class. Kind`shared` selects all reported Ethernet/VEIP
UNIs and requires ordinal0. Slot1..7, priority0..7, VID0..4094/`none` and
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
fttrctl /sys/bus/mdio_bus/devices/<MiniOLT-device> service <hardware-ID> <UNI-kind:0|2|shared> <UNI-instance:0-if-shared> <slot:1..7> <priority:0..7> <VID|none> <fixed> <assured> <maximum> fttr0
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
for the explicitly selected UNI. Kind0 is Ethernet; kind2 is VEIP; `shared`
with instance0 selects all reported Ethernet/VEIP UNIs. `none`
selects the original transparent UNI policy; a VID0..4094 selects the original
access policy with the chosen priority. `filter:VID` retains transparent UNI
rows and filters only the ANI/GEM bridge port by that VID. Bandwidth values are the existing
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

### CPU business admission during forwarding withdrawal

The controller now requires NI `fttr_forward`, distinct from `fttr_accel`.
The old acceleration withdrawal left CPU business submissions active: the
production-function regression reproduces a GEM/VLAN frame entering DMA while
FE was being withdrawn. This can keep downstream queues populated while the
controller removes/reassigns the service.

`fttr_forward` accepts `0` to close business forwarding, and
`1 ETHERTYPE` to open it with the observed MiniOLT OMCI ethertype in hexadecimal
(e.g. `1 8888`). The latter requires configured P6 and rejects an active FPGA
load. Business admission starts closed at NI registration and closes on
P6 reconfiguration, parent stop and FPGA loading. Parent stop also clears the
FE request, so a later netdev open cannot silently restore an earlier owner.
The default management ethertype is the original ASIC/FPGA8888 for explicit
manual bring-up; the daemon passes the actual value from MiniOLT `omci_link`.

Closing serializes with DMA submission under the existing NI `tx_lock`, then
retires held CPU RX as described below. It deactivates/resets the parent qdisc using Linux
`dev_deactivate` before FE withdrawal, and reactivates it afterward for
management traffic. Linux6.18's operation resets the queued skb lists and
waits for outstanding qdisc runs, including BUSY/requeue paths; NI spinlocks
are not held across qdisc locks or the RCU wait. The successful enable path
purges again before publishing admission, so packets accumulated while closed
are not intentionally retained for the next service generation.

While closed, new nonmanagement CPU TX frames are consumed/dropped before DMA
rather than returned BUSY for later replay. RX callbacks also drop new business
frames. Only an **untagged outer** frame with the configured OMCI ethertype
passes: in-band GEM/VLAN tags and skb VLAN metadata cannot turn a customer
frame containing an inner8888 payload into management. Short/nonlinear packet
headers are read through the skb accessor. This is management/data
classification, not an OMCI peer-authentication assertion or payload validator.
The existing P6 physical TX gate still rejects all submissions during SerDes
changes, and LAN/other DMA owners remain independent.

FE failures keep CPU business admission closed and preserve primary and
rollback errors. A failed enable explicitly cancels its pending FE request,
preventing a later shared-owner enable from reopening it. `fttr_forward` reads
report `forwarding`, `omci_type`, `error`, `cleanup_error`. An acceleration-only
`fttr_accel=0` retains Linux forwarding when business admission is already
open; `fttr_accel=1` cannot bypass a closed business gate. Manual service tests
must explicitly open `fttr_forward` after P6 setup; raw OMCI on8888 remains
available with business closed.

Startup, service cancellation/reconciliation, administration, migration and
shutdown all use this operation. The same native FE Hash/cache withdrawal
is retained. On a missing/failing operation the daemon stops, rather than
assuming CPU forwarding has been withdrawn. An asynchronous remote release
continues to use management and resumes healthy peers between local mutation
stages as before.

`test_fttr_forward_gate.py` executes production TX/RX and sysfs control under
ASan/UBSan with interleaved submissions at the RCU/qdisc/FE boundaries; it
reproduces the preceding CPU-admission failure, verifies repeated close/open,
outer/metadata VLAN rejection, short frames, management/LAN continuation,
failed enable/rollback, and startup/loading constraints. Qdisc/RCU timing is
modeled; the matching Linux implementation was inspected and the complete
kernel Image/modules compiled. Existing P6 TX/loading, shared FE, original
CPU-DEEPQ and768 QM RX cases pass. Actual daemon/CLI/veth suites cover ASIC
migration/global authentication, FPGA1 policy/administration, both backend
bootstrap/replacement and missing-operation failures. Final FTTR package
compile/install passes. Reports/logs use `artifacts/fttr/forward-gate-*`.

This is a necessary producer-admission step, **not complete packet retirement**.
DMA/AFT-prefetched packets, FE/PE work already in flight, packets already
delivered to Linux/RPS and MiniOLT queues are not proven empty. The retained
NAPI/GRO window is now closed as described below. The existing L3/L2 ES
flush function therefore remains unconnected to a claimed GEM-reuse fence.
There was no physical port, ONU or acceleration acceptance in this step.

### Retiring CPU-held FTTR receive packets

A grace period alone did not retire packets already retained by the shared
NAPI GRO node. The actual Linux6.18 GRO code keeps both hash-bucket aggregates
and a normal receive batch; `synchronize_net` waits for readers, and the parent
qdisc reset addresses TX, but neither purges these RX lists. A busy poll can
therefore leave an old P6 packet to be delivered after service reconstruction.
`test_fttr_rx_purge.py --revision 52f0c38` reproduces that missing cleanup.

After closing business admission, `fttr_forward` now disables active shared
NAPI and waits for its completion tail before inspecting those lists. It drops
only skbs whose device is the FTTR parent and whose saved Ethernet protocol or
VLAN metadata identifies business, updates the held-bucket counts/bitmask, and
preserves management and other ports' aggregates. Surviving normal-batch packets
are delivered with bottom halves disabled before its segment count is reset;
the segment count is not incorrectly decremented once per aggregated skb.
All retained P6 split-frame copies are released. If the engine was already
stopped, the same cleanup runs without a second NAPI disable or an enable.
Otherwise NAPI is re-enabled and scheduled so an interrupt masked during the
pause cannot strand shared RX. The subsequent qdisc and FE withdrawal order
is unchanged; no NI spinlock spans NAPI/RCU/stack operations.

The production helper runs under ASan/UBSan across all eight hash buckets,
mixed management/LAN/FTTR frames, VLAN metadata, custom OMCI type, aggregated
normal batches, split frames, repeated withdrawal and running/stopped engines.
The fixture asserts synchronization before freeing and exact preservation of
unrelated GRO state; it fails on the prior source. Existing forward/TX gate,
shared FE and768 original/native QM receive cases pass. Current Image/modules
compile successfully, with exact NI source identity checked, in
`artifacts/fttr/rx-purge-build-20261106.log`. This is software lifecycle evidence
with modeled NAPI scheduling, not a live-kernel concurrency or hardware test.
Packets already passed into RPS/the protocol stack and physical DMA/FE/PE/
MiniOLT buffers remain outside this purge and the full GEM-reuse barrier.

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
installed. Existing FTTR ports continue through Linux when only acceleration
admission is closed; `fttr_forward=0` also closes CPU business admission. A one repeats the withdrawal check before permitting admission,
so an unfinished failed deletion cannot be bypassed by an enable request.
Global FE enable prepares its tables before P6 admission; an unconfirmed
P6 rollback retains the prepared snapshot instead of restoring dependent
tables underneath an unconfirmed route.

`fttr_accel` reads report`requested`, `admitted`, `live`, `owned`, CF index,
destination, primary FE/CF errors and separate withdrawal/CF restoration
errors. With an active FTTR parent, requested admission now starts the shared
FE tables without requiring the upstream PON to be enabled. `admitted=1`
still describes programmed setup, not measured hits, bandwidth or an optical
link. PON and FTTR hold independent enable requests; details follow below.

The resident controller now uses `fttr_forward`, which wraps this FE operation
and the CPU admission/qdisc step described above. Startup withdraws
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
between two ONUs on P6 now has the constrained bridge-learning path described
below; physical operation remains unverified. Actual P6 Hash keys/hits, optical traffic and throughput remain unverified.

### Shared FE ownership after main alignment

The main alignment in `61d24e6` retains asynchronous LAN4 registration, optional
NI services, current DMA/QM/PE code and the factory-storage layout. Both FTTR
DTs now retain the required DMA AXI reorder window alongside P6/QX. FTTR BOSA
calibration uses a package-installed symlink, matching the main read-only
firmware layout. FTTR registration also runs inside the LAN registration RTNL
section: an early LAN open cannot start QM before the FTTR endpoint is published.
The existing threaded LAN tests assert P6 publication before the first engine
start; 90 per-port failure cases and 21 concurrent open/remove cases pass, and
the NI composite object recompiles (`registration-build.log`). Main itself is
not changed by this merge.

Previously `fttr_accel=1` only changed the CF destination if the upstream PON
had already enabled FE. Standalone FTTR therefore never prepared the Hash,
classifier, cache and packet-editor tables. FTTR now calls the same verified
FE preparation transaction while holding the table mutex. Repeated requests
are idempotent; a request made while the parent is closed takes effect on open.

PON retains its own request. After its PDC ingress has switched to CPU16,
PON disable blocks new source7 flows, invalidates queued bridge learning and
withdraws every flow with source7 at either endpoint. Active FTTR keeps the
shared tables and its unrelated flows. Failed withdrawal is returned and a
subsequent PON enable must finish that withdrawal before reopening admission.
FTTR disable/close retains the analogous P6 barrier. The last active owner
restores the original FE snapshot; final supplier removal withdraws both.

`scripts/test_fttr_fe.py` executes the production owner transactions and
flow-admission checks, using real CF helpers and modeled indirect tables,
flow deletion and global preparation. It covers standalone FTTR, enable-before-
open, both owner orders, both PON directions, failed deletion/re-enable,
failed preparation/restoration and last-owner release under ASan/UBSan.
`scripts/test_fe_lan_lifecycle.py` still executes actual shared preparation
and restoration against five distinct LAN register states. The unchanged
CF bytes are compared again to actual FC/NI ARM64 execution in
`artifacts/fttr/main-20261105/cf-stock.json`. GCC14.4/musl compiles and links
the current 6.18.55 NI composite object (`fe-owner-build.log` in that directory).
These checks establish software transactions, not physical queue retirement
or measured FTTR acceleration. Those remain required acceptance work.

### Non-IP bridge packet-editor mode

Actual execution of `fc_8277c.ko:_rtk_8277c_asic_flow_action_gen` (`ca360`)
shows that path12 without source-MAC translation sets `modify_vlan_only_vld`
and `modify_vlan_only` when its input `ip_vld` is zero. The retained firmware
reads that flag at key offset24 bit0, and sets action byte127 bits5/6. The
actual NI `convert_act_flow_nomal_mode` converts these to native action bits
212/213. The native bridge action previously left both zero, including when
removing or inserting the outer GEM VLAN on non-IP service packets.

Both TC Ethernet rules and learned bridge flows now select this mode for
non-IP Ethernet-II payloads. IPv4, IPv6 and PPPoE session traffic retain the
existing IP editor selection; 802.3 length encodings do not use this mode.
This changes neither the direct-L2TM output policy nor the bridge source-MAC
preservation policy. It does not enable P6-to-P6 forwarding yet.

`scripts/test_fttr_bridge_action.py` executes the exact FC producer followed by
the actual NI packer, modeling only the TPID lookup against the native table.
Its30 vectors cover ARP/private control EtherTypes, IPv4/IPv6/PPPoE, zero to
two output tags and LAN/P6 destinations. Packet-edit, destination, COS and
MAC/TTL preservation fields match; global interface/mirror/deep-Q policy is
outside that comparison. `scripts/test_bridge_learning.py` exercises the real
hooks, learner and native packer through tagged non-IP/IP flows, along with
existing FDB movement, VLAN/PCP/DEI, PPPoE and STP cases under ASan/UBSan. The
current flow-parser check passes1028 NAT/IPv6,288 VLAN/QinQ,8 PPPoE and24
transparent bridge vectors. The NI composite rebuild succeeds.

Reports/logs are in `artifacts/fttr/main-20261105/` (`bridge-action.json` and
`bridge-action-build.log`). The four FTTR programs also pass real OpenWrt
GCC14.4/musl prepare/compile/install under `package/`, including the calibration
symlink. The native startup test passes factory import, command order, failure
cleanup and controller replacement in a private network namespace (`startup.json`).
No optical traffic or physical packet-editor execution is implied by these checks.

### P6 hardware queue path

The earlier native action forced P6 to direct L2TM output with DEEPQ clear.
Execution of this unit's `aal_ni_init_nirx_l3fe_demux` instead confirms P6
DEEPQ selector0 (L3QM), and `aal_ni_init_ni` sets logical destination6's
QM-return bit in `a1c8` to L2TM. Original FC path12 actions set DEEPQ for
unicast destination6. The retained SCFG selects P6 as a LAN DQ port;
actual `aal_l3qm_init_empty_buffer` chooses QM port14, EQ profile0
(private EQ0/shared EQ2), CB port profile2 and VOQs112..119/profile2.
The previous native QM setup only initialized PON DQ ports16..31.

Native FTTR now allocates a512-buffer/1MiB coherent private EQ0 pool when P6
hardware is present. Its BID range6528..7039 follows both Wi-Fi pools; CPU,
PON and Wi-Fi ranges do not move. The original DRAM EQ0 builder's address,
size/count and BID fields are executed with these native values; Linux
coherent AXI attributes replace the original reserved-memory attributes.
Shared EQ2 remains owned by the existing PON/QM initialization. P6 credits,
VOQ occupancy and port free-buffer thresholds use the native pool capacity
and the actual original CB setters. Profile0's pool-selection thresholds
remain the stock4096/12; private VOQ credit remains25. All eight COS queues
receive their real selectors and scheduler weights before the BID caches
and shared engines start. QM return destination6 selects L2TM.

The real DQSCH profile-selector APIs are no-ops in this module. Native does
not write guessed selection registers from another chip's SDK. It initializes
the original port backpressure profile0 (1000 buffers) and all eight VOQ
backpressure profiles (768 buffers), as actual CB initialization does.
These values count2KiB buffers and fit the combined private/shared capacity.

The common FE installer sets DEEPQ on every flow targeting the FTTR parent,
covering bridge, routed and Wi-Fi flows. Shared FE preparation clears only
P6's DEEPQ selector at `a1f0` and restores its exact prior value on disable;
the ordinary P6 selector at `a1e0` is preserved. Admission still follows QM
initialization. This enables the intended buffering path; it does not prove
physical queue retirement, throughput or optical delivery.

`tools/trace_stock_qm_contract.py --p6 --stock-project <stock-project>` executes
the relevant initialization prefixes and asserts completion addresses.
`scripts/test_fttr_qm.py` compares native configuration to those mappings,
the real DRAM EQ0 builder and real CB setters, including three neighboring
register seeds and18 injected indirect-operation failures. Existing allocation
checks cover both P6 presence states and every allocation failure under
ASan/UBSan. The30-vector bridge action comparison now checks the production
P6 DEEPQ selection against actual FC/NI output. Shared FE restore, source
routing, asynchronous registration, BID teardown and EPP field checks pass.
The cached EPP test passes17024 cases and repeated stop/open; its fixture now
includes the FTTR endpoint. The old `test_native_rx_modes.py` fixture is stale
for cached-EPP synchronization and was not used as a passing result.

Reports are under `artifacts/fttr/p6-deepq-20261105/`, including
`factory-register-contract.json`, `qm-native.json`, `bridge-action.json` and
`kernel-build.log`. The current6.18.55 NI composite compiles with GCC14.4/musl.
P6-to-P6 learning is implemented below; complete producer/queue/MiniOLT
retirement and real device acceptance remain outstanding.

### CPU-originated P6 DEEPQ

FE output already selected P6 DEEPQ, but `fttr0` CPU transmission previously
set only Header A `no_drop`. Original `ca_ni_virtual_instance_alloc`
(`a30e0`) sets the private DEEPQ bit, and the header assignment in
`__ca_ni_start_xmit_by_specific_vp` (`a9cf0..a9d04`) copies it to bit62.
Native `rtl_fttr_xmit` now sets high-word bit30 as well as bit13, so both
CPU management/service traffic and FE output use the configured port14
VOQ112..119 path and its buffering/backpressure.

`test_fttr_cpu_deepq.py` executes the original virtual-port allocation and
header assignment block, then runs the production FTTR xmit/DMA packing
functions. It failed on the prior missing bit and passes for64/1518/1996-byte
frames after the fix. ASan/UBSan,4096 mixed DMA submissions with wrap/BUSY
checks and QM P6/PON dispatch regression pass. Evidence is
`artifacts/fttr/cpu-deepq-20261105.json`. This validates header construction,
not physical scheduling or packet retirement.

The normal Linux6.18.55 `Image modules` build passes with current NI,
MiniOLT/BOSA and authentication sources verified against the compiled tree
(`cpu-deepq-build-native-20261105.log` and `cpu-deepq-build-20261105.json`).
The target builds NI into vmlinux; an initial external-module check was invalid
because it duplicated those exported symbols. Normal in-tree build resolves
that check without changing product code. Existing module-description warnings
remain. The shared FE owner checks and1358 flow-parser cases, including P6
routing, pass. No new rootfs/FIT or hardware validation is implied.

### Learning between two ONU service ports on P6

With original P6 DEEPQ/QM return programmed, the native bridge learner can
represent two distinct ONU service VLANs on the same physical FE port6.
Previously it rejected all equal physical endpoints. It now accepts this
case only for the registered FTTR parent, with distinct logical bridge ports
and a changed outer service TPID/VID. The kernel bridge's FDB, STP, carrier
and forwarding decision are still prerequisites. Same-service reflection
and equal physical endpoints on other devices are still rejected.

The existing exact Ethernet/VLAN Hash uses source6 and the input GEM tag;
the packet editor replaces that tag with the output service tag and forwards
to destination6 with DEEPQ. This uses the same actual FC action and NI mapping
verified above: it does not depend on the L2FE same-port filter, since the
verified QM return selects L2TM. New rules still go through FTTR admission;
the existing both-direction P6 withdrawal includes these equal-endpoint flows.

The real bridge-hook/learner check verifies both directions between GEM tags
263/264, output priority, distinct native Hash keys/CRCs, same-service and
non-FTTR rejection, and FDB movement cleanup. The FE owner check verifies
that a P6-to-P6 rule is removed by the same withdrawal barrier. Both pass
ASan/UBSan; the current NI composite rebuild passes (`same-p6-build.log` under
`artifacts/fttr/p6-deepq-20261105/`). This is implementation and offline evidence;
ONU-to-ONU throughput/hardware hits and physical old-packet retirement remain
unverified.

### Routed and explicit TC forwarding between P6 services

The same physical P6 can also be the input and output of a Linux routed
flow or an explicit TC redirect between two ONU service VLANs. The native
flowtable/TC parser previously rejected every equal-endpoint pair even after
the bridge learner had gained this path. It now accepts a P6 pair only when
the parsed outer TPID/VID changes, both input/output retain a service tag,
and the endpoint is the registered FTTR parent. PCP-only changes, inner-tag
changes with the same service, untagged reflections and other ports remain
rejected. Linux supplies the routing/firewall or explicit TC decision;
installation still uses the existing FTTR admission, destination6 DEEPQ,
Hash/cache publication and bidirectional withdrawal.

Executing Linux6.18.55's actual flowtable action constructor exposed a
separate QinQ failure on this path. `flow_offload_fill_route` and ingress
matching store `encap[]` outermost first, while the output constructor
iterated that array forward and emitted VLAN_PUSH in the same order.
Each push prepends a tag, so the resulting hardware action put the customer
VLAN outside the FTTR service/GEM tag. Patch221 emits output encapsulation
actions from inner to outer; input matching/pops keep wire order. PPPoE is
therefore pushed before its enclosing VLAN too. The native parser continues
to interpret ordinary TC push order literally.

`test_fe_flow_parser.py` first failed on the same-P6 rejection and then
on the reversed outer service tag. With both fixes it passes1028 NAT/IPv6,
288 VLAN/QinQ,8 bidirectional PPPoE,24 transparent-bridge and10 P6 service
vectors against Linux's actual constructors under ASan/UBSan. It checks
both ONU directions, IPv4/IPv6, one/two tags, TC ARP, same-service rejection
and non-FTTR rejection. Earlier QinQ expectations mirrored the constructor's
reversed result; they now require the original tuple's wire order. FE owner,
bridge-learning and callback-lifetime regressions also pass. The report is
`logs/fe-flow-parser.json`. This establishes software action construction and
admission; it does not establish physical flow hits or packet retirement.

### Shared FE ingress limit for long FTTR frames

The custom FE LPB builder used MRU1700 for both ingress connectors. P6's
original MAC contract permits2000 bytes including FCS (1996 delivered bytes),
so its shared FE entry must cover that limit before Hash lookup or a CPU miss.
Executing retained `aal_l3fe_stg0_set_normal` (`68af0`) confirms LDPID25 selects
LPB1 at3414..341c. The original masked setter/getter (`67b20`/`67a70`)
confirms MRU bits24..37 and preservation of all other fields. Original default
initialization writes zero to this field; its zero semantics are not inferred
as an unlimited native setting.

When the FTTR endpoint is present, native preparation now sets that shared
LPB1 limit to the P6 MAC budget2000. The upstream-PON profile and the ordinary
no-FTTR configuration retain1700, and existing FE ownership restores the prior
registers on exit. This changes the shared LAN/P6 connector limit; individual
netdev and P6 frame checks remain in force.

The production FE preparation test reproduces the previous1700 result and
passes after the fix, with repeated enable/restore, neighboring profiles and
failed rollback checks under ASan/UBSan. Original routing and48 masked
MRU write/read cases, four full P6 configuration traces, FE admission and768
actual QM RX poll cases pass. Linux6.18.55 Image/modules build and source
identity checks pass (`artifacts/fttr/p6-fe-mru-*-20261106.*`). These establish
configuration consistency; actual long-frame forwarding/drop behavior still
requires hardware acceptance.

### Segmented CPU receive for long FTTR frames

Main's stock-compatible CPU EQ layout reserves64 bytes of headroom and384
bytes of tailroom. The previous FTTR code still advertised MTU1982, while
QM accepted only SOP+EOP in one buffer and discarded long OMCI envelopes.
Executing this unit's `ca_ni_rx_napi` arithmetic confirms first-segment
payload1592 (HeaderA only) or1584 (HeaderA+CPU header), and continuation
payload up to1600. These are the actual machine-code values, not a newer
SDK's buffer-size constants.

Native P6 RX now copies a long frame into a bounded1996-byte skb, one partial
frame per COS. The first segment's source/POL_ID is retained; a continuation
has payload after headroom and no new HeaderA. Every EPP/BID is retired after
its bytes have been copied, including when the second EPP is published in a
later NAPI poll. No additional hardware buffers are held by reassembly.
A new SOP replaces an incomplete frame; malformed lengths/flags, allocation
failure, a stale continuation or a one-second completion deadline reject
completion. Parent open/close and acknowledged accelerator withdrawal advance
a software generation so an old partial frame cannot complete after those
transitions. Shared QM stop/free releases all partial skbs. This protects
software reassembly only; it is not the outstanding physical producer or
MiniOLT packet-retirement barrier.

`scripts/test_fttr_rx.py` executes the original segment arithmetic and the
actual native QM poll/stop using separately published DMA-ring bytes. Its768
cases cover both header modes, boundary/max lengths, all8 COS queues, ring
wrap, budget1, delayed continuation, first-buffer overwrite after early return,
ENOMEM, oversize, timeout, generation change, stop, absent endpoint and invalid
EOP. Byte-for-byte payload checks and ASan/UBSan pass. Existing source dispatch,
17024 cached-EPP poll cases, allocation cleanup, P6 startup and FE ownership
checks pass. The6.18.55 NI composite compiles/links with GCC14.4/musl.
Reports are in `artifacts/fttr/p6-rx-20261105/`. Physical long-OMCI RX has not
been tested; no hardware acceptance is implied.

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

The same trace now executes original L3 `aal_l3_tm_es_voq_flush_set` at6f120
and status getter at6f0a0. P6 DEEPQ port14/VOQs112..119 use control63fc and
occupancy683c with the same COS/enable/occupied bit positions. The original
L3 function repeats the same4002-read false-success bug. Native now flushes
this L3 stage before L2 port6, matching the configured packet path. It checks
both existing flush owners before changing either control. A failed L3
stage, including failed control restoration, stops before L2 writes.

`rtl_fttr_p6_flush_queues` implements both ES stages natively. Each stage's
eight queues are selected/flushed and their real empty bits polled with a
100ms limit. An unapplied command returns`-EIO`, a stuck queue returns
`-ETIMEDOUT`, and a final all-queue observation that has refilled returns
`-EAGAIN`. The original COS/flush control fields are restored even after
failure and read back. Completed-per-queue bits, failed queue number, last
occupancy, primary error and separate restoration error remain separately
in P6's `l3_queue` and `l2_queue` state. After L2 completes, both occupancy
words are checked again to catch L3 refill during the L2 stage. Other ports'
occupancy bits are ignored and left intact. These state
fields record an ES observation; they do not assert that the entire FTTR
path is empty.

`scripts/test_fttr_queue.py` compares the actual native function's writes
with original execution, and covers timeout, an unapplied command, failed
control restoration (including simultaneous timeout), failed flush-disable,
refill, cross-stage refill, neighboring ports and existing flush ownership.
Six original traces and22 native scenarios pass. Poll time and hardware
occupancy are modeled; current results are in
`artifacts/fttr/p6-l3-queue-20261106.json`. P6 configuration/profile/NWAY and
TX/loading-gate regressions pass. The6.18 Image/modules build passes with
these sources (`p6-l3-queue-build-20261106.log`); no firmware was loaded.

The function is deliberately not called from shutdown or per-service reuse
yet: stopping software submission and clearing Hash/cache do not establish
that DMA/PE can no longer refill ES. The same original-code trace executes
`ca_dma_tx_complete_easy` (`a5a10`) and confirms its two-descriptor retention:
software read0/hardware read2 frees none; hardware read3 frees descriptor0;
software read62/hardware read2 frees62/63 after wrap. A DMA read/write-pointer
equality therefore does not, by itself, establish downstream packet retirement.
Integration requires that producer barrier; the existing synchronous FE/cache
withdrawal remains in place.

The retained `aal_ni_wait_dma_lso_misc_interrupts` at6450 only reads the
per-VP interrupt word168+VP*a0, waits for the selected `txq_empty` bit and
acknowledges it; it does not inspect AFT/PE or MiniOLT occupancy. The actual
synchronous `__ca_ni_send_single_pkt` blockadbc8..adc28 clears that interrupt,
publishes the producer, then compares two read-pointer queries. Neither
routine supplies the missing end-to-end retirement evidence. They have not
been substituted for such a barrier.

The original MiniOLT diagnostic table at`libasicdrv.so:2a010` (137 entries,
16-byte rows, selected by`Dump_FTTR_FPGA_AllReg`) names Ethernet/PON buffer
warnings at`30040..30043`, PON TX error/out/in counters at`30084..30086`, and
PON RX drop/out/in counters at`30087..30089`. Extracted rows are retained in
`miniolt-buffer-registers.json`. These are labels and counters, not a proved
empty-buffer condition or a per-GEM flush protocol. `drv_gpon_ni_deactive`
and`drv_gpon_onu_clear` in the retained relay are no-op implementations.
The retained `ca_ni_stop` at`a2040..a2150` disables CPU TX and RX interrupts,
then netdev TX, NAPI and IRQs. It contains no AFT/MiniOLT empty observation.
`dma_lso_stuck_check.constprop.0` at`a6010` tests DEBUG0 offset50 bits4..7
for value5; the TX paths at`aa7f8..aa888` use this as a stuck-engine reset
trigger. This failure detector does not establish a normal idle/retirement
condition. Neither path was added as a GEM reuse barrier.
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

P6 software submission has a separate synchronized gate. Previously
`fttr_link` stopped the netdev queue, but the shared DMA timer could see the
old `p6.configured` value and wake it again before/during SerDes changes.
The gate now closes under the same `tx_lock` used by DMA submission and
the timer, then disables the netdev queue without inverting its lock order.
Both the timer wake and actual P6 submission check the gate. Open or a
successful reconfiguration re-enables it; close, stop or failed FE/SerDes
configuration leaves it closed. Other DMA owners continue to transmit.
This closes a demonstrated software-producer race, not the outstanding
DMA-prefetch/FE/PE/QM/MiniOLT retirement barrier.

`test_fttr_tx_gate.py` interleaves the production wake/submission callbacks
inside the real link store/close transitions. It fails on the previous
unexpected wake, and passes after the gate, including FE/configuration
failures, netdev-down state, reopen and other-owner submissions. ASan/UBSan,
shared FE lifecycle and4096 DMA wrap/BUSY submissions pass. Normal6.18.55
Image/modules builds pass with these sources (`p6-tx-gate-build-final-20261105.log`).

`scripts/test_fttr_dma_reset.py` failed against the prior native stop code
because the two AFT writes were absent. It now matches original execution
for two nonzero control/configuration seeds after disabling submission
(`dma-aft-reset-comparison.json`). The actual6.18 composite NI driver object
also compiles/links. Hardware reset effects and end-to-end packet retirement
remain unverified; this fix supplies the missing original reset sequence.

### Original remote Wi-Fi configuration transaction

This section describes the library's Sino private OMCI branch. The retained
firmware also contains a separate syncserv Ethernet/JSON implementation; see
the original syncserv evidence below. No live subordinate capability inventory
has established which path it accepts. An OMCI model test is not evidence for
syncserv compatibility, and the two protocols' enums/string limits differ.

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
`package-stage-remote-wifi.log`). The explicit CLI workflow below now uses this
transaction module. Automatic main/subordinate settings
mapping, alternative capability branches and actual
subordinate execution/roaming remain work; a successful modeled transaction
is not wireless acceptance.

### Explicit remote Wi-Fi command

`fttrctl <MiniOLT-dir> wifi <hardware-ID> <config-file> [interface=fttr0]`
uses the existing exclusive MiniOLT lease. It requires an already initialized
MiniOLT, ready management transport and registered, unblocked ONU; it does not
bootstrap the optics. A running daemon holds the lease and the command fails
without sending traffic. Stopping that daemon also disables its hardware, so
this is an explicit development command for a separately initialized setup,
not a live configuration API for the resident controller.

The command parses the whole file before opening an OMCI session, collects a
fresh MIB, verifies the same registered serial and requires the actual matching
`ff69` and `ff6a` instance. The shared transaction then reads all selected
groups before any Set, verifies each write, restores touched groups on failure
and checks the hardware identity before every request/retransmission and at
completion. Replacement prevents further writes, including restoration to the
new peer; the original error and any separate cleanup error remain visible.
The inventory itself is read-only and is checked again before any Wi-Fi request.
Only the redacted `wifi_state` diagnostic is emitted, including on failure.

Each non-comment line contains one field and one value. Unknown/duplicate
fields, missing required fields, embedded NULs, invalid numbers or hex, and
oversized strings fail before management traffic. Numbers accept decimal or
`0x` notation. `instance` is a 16-bit actual ME ID, booleans are 0/1, and other
numeric fields are byte-sized **original protocol codes**, without an invented
hostapd mapping. All ten numeric fields plus `ssid_hex` are required, including
numeric fields unused by the selected branch. `shared_key_hex` is required for
nonzero security other than5/6; codes5/6 require all four `key1_hex` through
`key4_hex` fields. The maximum byte lengths are24 for SSID/shared key and26 for
each indexed key. Hex preserves spaces, UTF-8 and full-length fields; `-`
explicitly selects an empty zero-filled value. Short fields are zero-padded.

This is the synthetic configuration used in the original comparison, **not a
recommended wireless policy or a claim that instance `0x101` exists on a peer**:

```text
instance 0x101
mode 3
channel 6
bandwidth 1
security 3
encryption 2
key_auth 1
key_index 2
ssid_enabled 1
radio_enabled 1
ssid_hex 46545452206e6174697665207265736561726368
shared_key_hex 746573742d706173737068726173652d313233
```

Store real configuration files with mode0600; credentials are read from the
file, not command-line arguments. The command does not print SSIDs, keys,
previous values, MIB attributes or raw exchanges. Success means the requested
OMCI attributes were read back, not that wireless clients associated or roamed.

`scripts/test_fttr_wifi_cli.py` compiles and runs the actual command inside a
fresh network namespace. The matching original `libponrelay.so` SHA-256 is
`a72925a031144bca86ef53eb48fa0e1c8ea379285dadcf017305dcf81896cf8d`;
other firmware versions must not be substituted into its fixed-address oracle.
The test executes original Set producers/encoders and attribute descriptors,
while modeling peer MIB storage and MiniOLT sysfs. Five mode/security branches
match wire bytes and unchanged configuration sends no Set. Cases cover missing
capabilities, bad input, lease contention, NACK, wrong readback, failed restore,
lost replies, three-attempt timeout and replacement during inventory, Set,
restoration and final verification. No writes follow replacement. Unrelated
ONU/TCI replies, including malformed ones, cannot fail the active object
transaction; matching malformed replies still trigger failure/restoration.
The latter check reproduced and corrected misattribution in the shared
`fttr_objects_execute` receive path.

```sh
sudo unshare -n env PYTHONPATH=tools:scripts:/path/to/python-deps \
  python3 scripts/test_fttr_wifi_cli.py \
  --master /path/to/matching/libponrelay.so --output /tmp/fttr-wifi-cli.json
```

Local results are `artifacts/fttr/wifi-cli-20261103.json`,
`wifi-baseline-20261103.json` and `mib-wifi-cli-20261103.json`. The existing
controller/CLI namespace regression also passes with the updated shared
engine (`controller-wifi-cli-20261103.json`), including inventory/replacement,
SIGTERM and Get/T-CONT/GEM/profile/UNI/forwarding commands. The actual OpenWrt
package prepare/compile/install recipes are also exercised with GCC14.4/musl
and warnings-as-errors in `artifacts/fttr/wifi-cli-build-20261103/`; its command
and log are retained there. These checks do not include a firmware image,
optical hardware, subordinate wireless execution or automatic daemon policy.

### Resident remote Wi-Fi policy

`fttrd` now accepts `--wifi <policy-file>` alongside `--services`. The packaged
procd service loads `/etc/tyfza1-fttr.wifi`, whose default contains comments
only. Each row is `serial absolute-config-path`; the serial is 16 hex digits
in vendor-first order or `*`. Referenced files use the exact `fttrctl wifi`
format above. Paths cannot contain whitespace. A file selects one actual
`ff69/ff6a` instance; multiple instances per ONU are supported in file order.
An exact serial entry overrides the wildcard for the same instance only.
Duplicate serial/instance entries and all invalid configuration files fail
before the controller acquires the device or changes hardware. Files are read
once at startup; restart the service to load changes. There is no live reload
or inferred hostapd-to-vendor enumeration conversion.

```text
# Synthetic example; prepare files for actual reported instances first.
* /etc/fttr-radio-first.conf
* /etc/fttr-radio-second.conf
5445535400000007 /etc/fttr-radio-first-override.conf
```

Each ONU progresses from fresh MIB inventory through selected Wi-Fi instances,
then its optional wired service and the shared MIB-sync baseline. An instance
performs all preflight Gets, ordered Sets, readbacks and any failure restoration
before the next instance begins. Other ONUs continue asynchronously. Wi-Fi Sets
therefore cannot be mistaken for an unexpected change to an already established
service sync baseline. ONU/TCI dispatch, the original two-second/three-attempt
deadline policy and fresh registration/serial checks apply to requests,
retransmissions and completion. A lost Set reply restores the touched groups;
replacement cancels writes/restoration to the old identity and starts fresh
inventory. A shared socket/register IO error stops the controller and disables
TX. SIGTERM cancels the pending jobs and records their unresolved groups before
exit; this is not a remote rollback after controller shutdown.

Instances are separate transactions: one rejected or unsupported instance does
not undo a previously successful instance or prevent the ONU's wired service.
`status.json` reports `wifi_instances`, the number `wifi_configured`, and separate
`wifi_error`/`wifi_cleanup_error`. The `wifi` phase means a transaction is active;
the existing `configured` phase continues to describe wired service. A Wi-Fi-only
ONU can remain in `inventory` with verified Wi-Fi instances and no GEM port.
`onu-<ID>.wifi` records the serial, summary and redacted per-instance group state;
replacement archives it as `.wifi-old`. Initial failures are retained until a
new inventory after observed disconnect/replacement, sync recovery or restart;
there is no unbounded automatic retry of rejected policy writes.

The existing sync cycle also reads every selected group of successful Wi-Fi
instances. This verifies actual configuration even if private attributes change
without updating standard ONU Data sync, or while its value stays zero. A
missing/mismatching group triggers fresh inventory and policy evaluation;
timeout invalidates Wi-Fi state, and a later valid sync reply starts recovery.
When a wired service exists its old local port/grant is withdrawn during this
reconciliation. Wi-Fi-only operation never invents a GEM probe. Failed instances
are not treated as verified probes. This is explicit policy enforcement; remote
manual changes to successful instances are restored to the selected policy.

Credential-bearing Wi-Fi request/reply bodies, raw MIB records and merged MIB
attribute values are omitted from daemon journals. Class/instance/masks,
attempt counts and actual result/error codes remain available. The shared MIB
model still retains actual values in memory for protocol work; its diagnostic
export now marks `ff69/ff6a` attributes `redacted`. Policy credential files should
be mode0600 and are not generated from this host's saved Wi-Fi credentials.
Runtime snapshots remain in `/var/run`; they do not provide durable restoration
of remote configuration ownership after a crash.

`scripts/test_fttr_autoprovision.py --wifi` runs the actual daemon with two ONUs
and two instances per ONU over AF_PACKET/veth. It uses the matching original
attribute descriptors, ONU sync increment/reset instructions and VLAN table
responses; peer MIB storage, registration and sysfs are modeled. Checks include
independent progress, exact/wildcard selection, no self-induced rescan, periodic
attribute drift, Wi-Fi-only/zero-sync recovery, lost Set replies, malformed
matching replies, three-attempt probe timeout, replacement during a later
instance's cleanup, retained primary/cleanup errors, missing capability without
loss of wired service, duplicate-policy rejection, restart reuse, omitted-policy
archive, redacted journals and shared transport shutdown.

Local reports are `artifacts/fttr/wifi-runtime-20261103.json`,
`autoprovision-wifi-runtime.json` (existing service regression),
`wifi-cli-runtime-20261103.json`, `model-wifi-runtime-20261103.json` and
`wifi-groups-runtime-20261103.json`. The updated real OpenWrt GCC14.4/musl package
prepare/compile/install runs with warnings-as-errors in
`artifacts/fttr/wifi-runtime-build-20261103/`, including the new policy conffile
and procd argument. Neither these tests nor the earlier original Set-byte
comparison establish optical connectivity, subordinate Wi-Fi interoperability,
client association or roaming. Those remain hardware acceptance work.

### Original syncserv Ethernet path

The retained December2024 firmware's `bin/sync_controllerd` SHA-256 is
`cfe3afada669992624f087aebef91e4000f361c83cdaeb15cfd6f3906f2d21e2`.
Its `do_wifi_sync` dispatcher calls the JSON producer at `410cc4` for peers
whose protocol field is2. `410c60` builds `GetWifi`. `405320` builds the JSON
transport header, and `407be8` prepends Ethernet addresses/type. These are
independent of `libponrelay`'s private `ff69/ff6a` OMCI classes. This establishes
an implemented path in the retained firmware, not a live capture proving that
a particular connected slave uses it.

`tools/trace_stock_fttr_sync.py` executes those actual ARM64 functions. Synthetic
MIB rows model only `wlan_getEntry` and radio-disable values; JSON-C allocation
and serialization, packet allocation and slot-to-peer lookup are explicit
fixtures. Original field selection, security branches, pktbuf pointer arithmetic,
length/header writes and Ethernet construction execute unchanged. The shared
ELF oracle now accepts explicit COPY-symbol byte fixtures; it still rejects
COPY relocations without a named, correctly sized fixture. The unused Unix
socket descriptor/optarg/stderr fixtures are zeroed and no original daemon is
launched on a network.

Verified framing:

| Offset | Meaning |
| --- | --- |
| 0..11 | Destination/source Ethernet MAC |
| 12..13 | Big-endian EtherType; this executable's initialized default is `88aa` |
| 14 | High nibble: syncserv peer slot0..15; low nibble6=request,7=response |
| 15 | Flags; zero for the verified unfragmented sender |
| 16..19 | Big-endian JSON byte length |
| 20 onward | JSON bytes, without a transmitted trailing NUL |

The four-bit peer slot is **not** the MiniOLT's six-bit hardware ONU ID. The
executable has an EtherType override and a fallback-message string mentioning
`8899`, so native queries require an explicit value rather than inferring it
from that string. Peer MAC remains explicit for targeted queries; `auto` now
discovers its local slot as described below. Original receive `405760` sends
frames with nonzero count bits to reassembly. Native now reassembles bounded
messages before interpreting JSON; see the fragment evidence below.

`GetWifi` is `{"RPCMethod":"GetWifi","MAC":"<host MAC as 12 hex digits>"}`.
The original `SetWifi` contains `RPCMethod`, host `MAC`, `2.4GEnable`, `5GEnable`
and a `SSID` array. It selects the first WLAN entry on each band, with `Band`
`2.4G`/`5G`, one-based `Index`, `Enable`, `Name`, and security fields. Execution
confirms these source-MIB security mappings:

| Source MIB code | JSON BeaconType |
| --- | --- |
| 0 | None |
| 2 | WPA |
| 4 | WPA2 |
| 6 | WPA/WPA2 |
| 16 | WPA3 |
| 20 | WPA2/WPA3 |

WPA/WPA2 cipher byte2 produces `AES`; bytes1/other produce `TKIP`/`TKIP+AES`
in the disassembled branch. `WPAKey` is a JSON string. A synthetic32-byte SSID
and63-byte shared key survive original production intact; the OMCI24-byte limit
does not apply. Unknown source security255 falls back to `BeaconType:"None"`
while still emitting `WPAKey`; this is observed original behavior, **not** a
permitted native fallback or a reason to silently weaken requested security.
WEP branches, full UCI conversion, radio configuration and slave-side application
still need their own evidence. The strings here do not prove WPA3 interoperability.

Original response dispatch `40d198` accepts a result-only acknowledgement while
a request is pending; execution with `Result:0`/`Result:1` confirms it retires
the pending slot in either case. Its zero result takes the failure branch.
An acknowledgement need not carry `RPCMethod`, `MAC` or Wi-Fi values. Therefore
it cannot prove configuration readback. The later Flag1 read/report path below
provides a separate observation, still without authenticated transaction IDs.

The package now installs a native, read-only diagnostic:

```sh
fttr-sync <interface> <peer-MAC> <sync-slot:0..15|auto> <ethertype> [wifi-settings | wifi-apply <private-json-file> | register <private-policy-file>]
fttr-sync <interface> discover <ethertype>
```

Choose the actual management interface (potentially an already configured GEM
VLAN child), peer MAC and configured EtherType. The tool does not initialize
hardware. `auto` discovers the peer's local slot under the same interface lease;
an explicit slot remains available for diagnostics. It sends one GetWifi
and waits up to three seconds; this is a diagnostic budget, not a claimed
original retry policy. A namespace-scoped abstract Unix socket prevents two
native probes from sharing an interface concurrently. Source/destination MAC,
message type, complete length and strict UTF-8 JSON are checked. The received
slot nibble belongs to the remote sender and does not override local source-MAC
binding. Unrelated methods
are ignored, matching malformed data fails, and a rejected `Result` returns
`-EREMOTEIO`. `sync_ack` means a positive acknowledgement; `sync_wifi_reply`
means an identified JSON reply was received. Both explicitly report
`configuration_verified:false`. Output includes only shape/count metadata;
SSID/key values, unknown fields and raw replies are never printed.

There is no request transaction ID in the verified header. A stale identical
response from the same peer cannot be distinguished by this diagnostic;
its output must not be used as configuration-transaction acceptance. The optional
`wifi-settings` command uses the read action described below. Explicit
`wifi-apply` adds a single write and field readback with the limitations below;
no reboot or upgrade command is exposed.

`scripts/test_fttr_sync.py` compares36 native frames against actual original
encoding (six security cases, slots0/5/15, request/response), checks size limits,
padding/truncation/fragment rejection, then runs the real diagnostic over
AF_PACKET/veth. Peer replies are modeled using original encoding. It covers
data/ACK/rejection, unrelated source/method, a different remote slot, malformed JSON, timeout,
interface lease and output redaction. Reports are
`artifacts/fttr/sync-stock-20261103.json` and `sync-native-20261103.json`.
Existing OMCI Wi-Fi oracle checks still pass (`wifi-oracle-regression-sync.json`).
Actual OpenWrt GCC14.4/musl prepare/compile/install, including the explicit
`libjson-c` dependency and installed `fttr-sync`, passes in
`artifacts/fttr/sync-build-20261103/`. No live slave, optical throughput or
full firmware image has been validated by these checks.

### Syncserv heartbeat discovery and local MAC binding

`tools/trace_stock_fttr_discovery.py` extends the original execution past the
previous slot-lookup fixture. This revealed two corrections to the first native
probe: original `403efc` checks the selected local slot and looks up its MAC,
but ultimately passes group `01:10:ec:00:00:02` to `407be8`; JSON requests are
therefore multicast in this firmware. Original ingress `403f80`/`40a1a8` maps the
Ethernet source MAC to a local slot before dispatch. A received slot nibble of
0/5/15 all maps to12 for an actual MAC retained in local slot12. The earlier
encoder-only comparisons remain valid within their narrower scope, but did
not establish this routing behavior. The native probe now sends to that group
and correlates a response with the explicit source MAC, independently of the
sender's slot nibble. Socket memberships include both original groups
`01:10:ec:00:00:01` and `01:10:ec:00:00:02`, as selected by `libsyncserv:get_socket`.

Actual heartbeat execution at `4038d0` produces16 bytes before Ethernet minimum
padding: the multicast/source/type header, byte14=`01`, and byte15=`80 | seq`
with a three-bit sequence wrapping0..7. The initialized timer period is two
seconds (`42b458=2`); `40a068` runs after sending. `403868` ignores request-bit7
and observes the source MAC of responses. It does not authenticate a serial
number, echo-check a transaction ID or establish the peer's JSON capability.

The original table initializer `413f80`, lookup `409a20`, state transition
`409b64` and ageing function are executed with empty saved-MIB side effects:

- Sixteen local slots, initialized with their own index; the MAC is the lookup
  key, not the packet's slot field or the MiniOLT ONU-ID.
- Prefer an exact retained MAC, even in an offline slot. Otherwise select the
  first offline slot. A full online table cannot accept another MAC.
- A response sets online and resets the missed count. Each subsequent timer
  tick increments it; count3 exceeds the original threshold2 and expires it.
- Keep the MAC after expiration, allowing the same peer to regain its old slot
  unless that slot has since been reused.

Native `sync-peer.c` implements these verified behaviors with a portable table.
It additionally rejects zero/multicast/self source addresses, wrong Ethernet
type/destination, non-heartbeat/request frames and invalid lengths before
changing state. Full capacity returns `-ENOSPC`; it never silently evicts an
online peer. This state is discovery liveness only, not an authenticated FTTR
registration or a durable configuration owner.

`discover` runs a bounded6.5-second observation with immediate first heartbeat
then two-second intervals. It returns current responders, local slots and
miss counts as `sync_discovery`, with `identity_authenticated:false`. A peer
that answers only the first heartbeat expires on the fourth send and is not
listed. An empty list is a completed observation, not a link-up claim. `auto`
uses the same discovery but returns as soon as the specified target MAC replies,
then sends GetWifi while retaining the interface lease; absence returns timeout.
The process-local slot table is not saved between invocations, so a listed slot
must not be treated as a persistent device identifier. Discovery does not query
or mutate unrelated peers' configuration. A malformed or reflected frame cannot
allocate a slot. Full table aborts the observation rather than reporting an
apparently complete inventory.

The current `scripts/test_fttr_sync.py` executes both original/native table
lifecycles and complete routed queries, then covers15 AF_PACKET/veth cases,
including auto discovery, expiring an unresponsive neighbor, absent target,
multicast replies, different remote header slots and invalid identities. It
retains the36 original JSON encoder comparisons. Reports are
`artifacts/fttr/discovery-stock-20261103.json` and `discovery-native-20261103.json`.
The updated real OpenWrt GCC14.4/musl package stages successfully under
`artifacts/fttr/discovery-build-20261103/`. Timer/saved-MIB state and peer packets
are modeled in these checks; no optical or real slave interoperability is claimed.
Capability/identity authentication and physically verified writes remain
required work; Flag1 reads, explicit writes and resident policies are covered below.

### Syncserv JSON receive fragments

`tools/trace_stock_fttr_fragments.py` executes original `405760`, `40552c` and
`405498`, capturing the bytes passed to reply dispatch `40d198`. Packet storage
is modeled; header extraction, fragment table lookup, copying and cleanup are
actual retained instructions. The second sync header byte contains a two-bit
sequence in bits0..1, index in bits2..4, and count minus1 in bits5..7. Counts2..8
enter reassembly. Low sequence bits alone are legal on an unfragmented message;
native now accepts them as well. Native rejects index >= count.

The original concatenates packet bodies (bytes after the16-byte Ethernet/sync
header) in index order, including a single four-byte big-endian JSON length at
the start of the joined body. All56 combinations of counts2..8, four sequences
and forward/reverse arrival order were executed. However, the retained binary
then passes the **last arriving packet's body and local slot** to reply dispatch,
not the joined buffer at `42bdc0`. Its table and sequence are global across peer
slots, and a duplicate index invokes an assertion. These are observed defects,
not behavior to copy into the native implementation. Changing sequence frees
incomplete state; an unfragmented reply also clears it.

Native `fttr_sync_receive()` owns bounded state per query and checks source MAC,
message type and EtherType before accepting another slice. An unrelated owner
cannot splice into or clear active state. The sender's slot nibble remains
independent of local MAC binding. Out-of-order fragments are supported; an
identical duplicate is ignored without extending the deadline. Conflicting
duplicates, inconsistent count and invalid index fail explicitly. The context
has a fixed three-second reassembly budget; the diagnostic's total three-second
query budget still applies across sequence changes. These are native bounds,
not a claimed original timeout policy or authentication mechanism.

Each frame is limited to1996 bytes and each body slice to1980 bytes, with eight
slots and15840 bytes total joined storage. The declared JSON length must fit;
only surplus bytes wholly in a short terminal frame may be Ethernet minimum
padding. No partial JSON is exposed. A successful fragmented message points at
the joined data; strict JSON/UTF-8 decoding and value redaction still apply.
Native requests remain single-frame; received reports can be fragmented.

`scripts/test_fttr_sync.py` compares native concatenation with the original
joined buffer for56 cases, while deliberately correcting its final-dispatch
defect. Additional checks exercise maximum size, tiny/truncated bodies,
duplicate conflicts, ownership, sequence changes, count changes, deadline and
terminal padding. The21 AF_PACKET/veth cases include a modeled multi-kilobyte
slave reply, reverse order, duplicate delivery, another source interleaved,
missing fragments and invalid counts. Reports are
`artifacts/fttr/fragments-stock-20261103.json` and `fragments-native-20261103.json`;
OpenWrt GCC14.4/musl package stage and source hashes are in
`artifacts/fttr/fragments-build-20261103/`. There is no retained slave fragment
transmitter execution or real slave acceptance evidence yet. Authenticated
identity/capability and verified configuration writes remain separate work.
The Wi-Fi read/report path is now implemented below.

### Wi-Fi settings read and completion report

Further original execution changed the next-step assumption: a pending GetWifi
(`method2`) consumes `Result` and retires the request without saving its Wi-Fi
values. GetAllSetting (`method10`, producer `411a10`) instead passes a successful
reply to `40ad50`, saving the entire JSON as `/var/sync_onuconfig_<JSON MAC>`.
Neither behavior establishes a later same-name GetWifi report. The original
pending Result handler also fails to correlate an optional RPCMethod, so its
acceptance is not sufficient evidence for a native transaction.

The retained `fttrManage:getWifiSsidInfo` at `44d634` reveals another read path:
it calls `get_syncserv_setting(1, "{\"act\":\"get\"}", target, NULL)` in libmib,
then reads `content.List` from the returned document. `libmib:get_syncserv_setting`
at `1a4228` writes a request file, sets the operation flag, wakes the controller
and waits for the flag/result file. Controller `411e78`/`411c50` construct:

```json
{"RPCMethod":"SetAllSetting","MAC":"<host>","DMAC":"<target>","Flag":1,"content":{"act":"get"}}
```

The fttrManage/libmib caller evidence is disassembly, not execution. Their
SHA-256 values are respectively
`9c8b1c80b3efe72f0a6f03b27bdf93b3cd6027021c00350b307807f522df195b` and
`5e14985931f51977c0b0e190bfa1c16122b8614b596f58f7ad121a83beb37ec2`.

Despite the outer method name, this is the original **read** action. Unlike the
older GetWifi command, its JSON explicitly selects the target DMAC while its
Ethernet destination remains the slave multicast group. The specific request
file is `/tmp/syncserv_bssc_<target>`. This request construction is executed in
`tools/trace_stock_fttr_settings.py`, with the MIB flag and file reads modeled.

Original `40d198` also handles a separate `RPCMethod:"SetAllSetting"`, `Flag:1`
report. With the modern branch enabled at `42ea84`, it saves `content` to
`/tmp/syncserv_bssg_<source-peer-MAC>` and clears the pending flag on `STATUS:0`.
The manager then reads `content.List` inside that saved object (`44d834` and
`44d860`); consequently the complete Ethernet JSON has `content.content.List`.
The transport content wrapper and the management document's content wrapper
are separate. An unwrapped `content.List` reply cannot establish compatibility
with that actual consumer.
Execution proves this works after a positive ACK has retired the original
request state. A nonzero STATUS does not clear the flag; its progress/error
subcodes have not been established. Original ingress `403e24` queues both
type6 and type7 into the same JSON dispatcher; source MAC, not a JSON MAC or
received slot echo, owns this report. This contrasts with the older
GetAllSetting persistence path above.

The native entry point is:

```sh
fttr-sync <interface> <peer-MAC> <sync-slot:0..15|auto> <ethertype> wifi-settings
```

It sends exactly the fixed Flag1 `act:get` request and waits within the existing
three-second diagnostic budget. Positive Result only records `ack_seen`; a
standalone ACK cannot complete the read. Completion requires a matching source,
SetAllSetting method, integer Flag1 and STATUS0, plus the nested array
`content.content.List` of objects. An empty List is a valid empty observation. Nonzero
STATUS waits without extending the budget. An explicit rejected Result fails;
wrong method/flag/source is ignored, and malformed matching content fails.
Result/Flag/STATUS comparisons use full64-bit integers to avoid truncating a
large number into a success value.

Both type6/type7 reports are accepted. Each has separate bounded fragment state,
and standalone ACK parsing does not clear a fragmented report, including one
using type7. The output event `sync_wifi_settings` includes only entry count,
`ack_seen`, `report_complete:true`, `values_redacted:true` and
`configuration_verified:false`. It never prints returned fields or credentials,
and does not persist raw configuration. A matching report is an observation;
the protocol still cannot distinguish a stale identical report from the same
source, and no authenticated serial/capability or applied-setting claim is made.

The original producer, pending reply/save path, post-ACK report/save/flag path
and ingress classification are executed by the new oracle. The expanded
`scripts/test_fttr_sync.py` covers39 AF_PACKET/veth cases, including delayed
post-ACK reports, direct reports, auto discovery, incomplete STATUS followed by
completion, both frame types, ACK interleaving within both kinds of fragmented
report, invalid fields and redaction. Existing36 encoder and56 concatenation
comparisons remain. Evidence is `artifacts/fttr/settings-stock-20261103.json`,
`settings-native-20261103.json` and the real GCC14.4/musl package stage with source
hashes in `settings-build-20261103/`. Slave replies are modeled; slave-side
production and real optical transport remain required work. The explicit
field mapping/write/readback implementation below does not establish credential
verification or full configuration acceptance.

### Explicit Flag1 Wi-Fi write and readable-field comparison

`tools/trace_stock_fttr_wifi_fields.py` now executes the retained
`fttrManage:setWiFiInfo`, `getWifiSsidInfo` local producer and libmib
`map_encrypt_to_mib` / standard conversion. JSON-C, MIB rows and the final
`sync_syncserv_setting` call are fixtures; no network, local Commit or WLAN
reconfiguration is run. The original remote branch builds a Flag1 document:

```json
{"act":"config","SSIDIndex":"1","SSIDAdvertisementEnabled":"true","Standard":"11bgn","SSID":"example","ENCRYPT":4,"PWD":"example-passphrase","Enable":1}
```

Original UI indices17..32 address the first subordinate and33..48 the second;
the wire document carries the subordinate-local1..16 index as a string.
The native command already identifies the peer by MAC and therefore accepts
only its local index. Do not pass the UI's combined index or a MiniOLT ONU-ID.
The retained local producer reports indices1..8 as2.4G and9..16 as5G, returns
Enable as string0/1, advertisement as stringtrue/false, and standard strings
such as `11bgn` / `11ac` / `11ax`.

Read and write encryption representations differ:

| Local read/UI ENCRYPT string | Write ENCRYPT integer | Original MIB meaning |
| --- | --- | --- |
| 1 | 0 | Open |
| 2 | 1 | WEP |
| 3 | 2 | WPA |
| 4 | 4 | WPA2 |
| 5 | 6 | WPA/WPA2 |
| 9 | 16 | WPA3 |
| 10 | 20 | WPA2/WPA3 |

The original invalid-encryption fallback is not used. Its explicit SSID setter
silently truncates to31 bytes, although an existing32-byte SSID survives both
read production and omitted-SSID remote construction. Native preserves up to32
UTF-8 bytes and rejects overflow rather than truncating. The original also
emits empty PWD/Standard/advertisement when those inputs are omitted. Native
requires all eight fields, so that omission cannot silently change those values.

```sh
# Use the actual management interface and observed peer MAC.
chmod 600 /root/fttr-wifi.json
fttr-sync <interface> <peer-MAC> auto 0x88aa wifi-apply /root/fttr-wifi.json
```

The file is the complete content object shown above. It must be a regular file
owned by the effective user, with no group/other permissions; symlinks and
nonregular files are rejected before any network operation. Strict UTF-8 JSON,
known fields/types, local index, band-appropriate standard, exact encryption
enum and key lengths are checked. Open mode requires an explicit empty PWD;
WEP accepts5/13-byte strings and WPA-family modes8..63 bytes. These are native
input bounds matching the verified branches, not claims of real radio support.
Known round-trippable standard strings are `11b`, `11g`, `11bg`, `11gn`,
`11bgn`, `11ax` for2.4G and `11a`, `11ac`, `11na`, `11ax` for5G.

Under one interface lease, the command first performs the Flag1 read. It requires
the selected index exactly once, rejects duplicate/invalid indices and more
than16 rows, and validates the selected row's radio band and readable fields.
It then sends exactly one `act:config` request. Positive Result or a matching
STATUS0 report permits the next phase; neither means the setting was applied.
An independent `act:get` follows even after write timeout/rejection. Each
exchange has its own fixed three-second budget. Queued frames are discarded
between phases with a bounded drain; reassembly state is fresh for each phase.
There is no retry of the write. Original write errors remain visible even when
the later readable fields happen to match.

Readback compares SSID, Enable, ENCRYPT after enum conversion, advertisement
and standard for the selected local index. The **retained local read producer
omits PWD entirely**. Consequently the command reports `readable_fields_match`,
`write_result`, `readback_result`, peer/slot/index and always
`credentials_verified:false`, `configuration_verified:false`. A0 exit status
means only that the write response and readable-field comparison passed.
No raw values or credentials are printed or persisted. There is no automatic
restoration: without the old remote password, complete rollback cannot be
proved, and replaying guessed or local credentials could break the peer.

This explicit tool advances the usable write path but is not resident Wi-Fi
synchronization or complete acceptance. Slave read/apply behavior, actual
credential installation/client association, identity/capability binding and
recovery policy are still required. An identical delayed same-source report
cannot be distinguished by this protocol, so queue draining alone is not a
freshness/authentication guarantee. The native comparator's schema comes from
the retained manager's local producer, with remote packets modeled in tests.

Evidence:56 actual remote builder cases cover seven encryption conversions and
eight combined-index boundaries;28 native field comparisons use actual original
read output. `scripts/test_fttr_sync_wifi.py` runs30 AF_PACKET/veth cases,
including direct/auto selection, open/WEP/WPA3/UTF-8, preserved32-byte SSID,
lost ACK, rejection, mismatched/missing readback, bad target and invalid private
files. The existing39 sync protocol cases and36 encoder/56 concatenation
comparisons still pass after sharing the exchange loop. Reports are
`artifacts/fttr/wifi-fields-stock-20261103.json`, `wifi-apply-native-20261103.json`
and `wifi-apply-sync-regression-20261103.json`; actual GCC14.4/musl package stage
and matching source hashes are in `wifi-apply-build-20261103/`.

### Explicit syncserv registration reply

`tools/trace_stock_fttr_registration.py` executes the baseline Register branch
of original `40d198`, its LOID lookup/row construction and reply producer. It
models JSON-C, source MIB values, an empty stored LOID list and `isMasterReady`;
modern/province-dependent side effects are explicitly disabled. The executed
scope does not stand in for those other branches or for an actual slave.

On `RPCMethod:"Register"`, original code sets peer protocol offset472 to1 and
copies optional DevName, LOID, Manufacturer, ModelName, OUI, SN and GPONSN into
the source-MAC-selected row. Sizes are128/64/64/64/12/64/16 bytes respectively;
the native parser leaves room for a terminator except the16-byte GPONSN. The
original even accepts a registration with none of those fields, so registration
is not authentication. Its baseline reply is:

```json
{"Result":1,"MAC":"<host MAC>","OUI":"<host OUI>","SN":"<host serial>","Ready":0}
```

Ready comes from actual `isMasterReady`, not heartbeats or link speed. The
original may also return an assigned LOID or province-dependent web password;
neither is inferred or sent by the native baseline entry point. A positive
pending SetAllSetting ACK sets protocol2. A repeated Register sets protocol1
again, even if the prior protocol was2. Those executed transitions are also
used by the resident controller below.

The native one-shot command is:

```sh
fttr-sync <interface> <peer-MAC> <sync-slot:0..15|auto> <ethertype> register /root/fttr-registration.json
```

Its private policy file provides the master identity/readiness and the expected
peer serial explicitly, for example (synthetic values only):

```json
{"OUI":"020304","SN":"synthetic-host-serial","Ready":0,"ExpectedSN":"synthetic-peer-serial","ExpectedGPONSN":"ABCD12345678"}
```

OUI must be six hex characters; host SN and ExpectedSN are required nonempty
strings up to63 bytes. ExpectedGPONSN is optional, up to16 bytes. Ready must be
integer0/1 and represents the operator-supplied state, not a native optical or
service measurement. Unknown policy keys are rejected. The file uses the same
owner/private-permission/regular-file checks as Wi-Fi configuration, now shared
in `sync-json.c`. No hardware identity is fabricated or taken from the peer.

Under the interface lease, `auto` first resolves the target MAC using heartbeat
discovery. Registration then sends heartbeats and listens for up to6.5 seconds
for that target's Register, accepting type6/type7 and bounded reassembly. It
ignores other sources/methods, validates optional field lengths/types, requires
the expected SN and optional GPONSN to match, and only then sends one baseline
type7 reply to the original slave multicast group. Missing/mismatched identities
fail before a reply; optional empty LOID/description fields are accepted. No
received text, LOID or raw registration is printed.

Success reports `sync_registration_reply`, the local slot,
`identity_pin_match:true`, `identity_authenticated:false`,
`peer_acceptance_verified:false` and the explicit `advertised_ready`. This means
the expected self-description was observed and a reply was sent; it does not
prove the slave accepted it, has optical service, or supports Flag1 writes.
The association is not persisted and this separate invocation does not bind a
later `wifi-apply` call to that serial. The resident mode below binds its own
transactions to registration and cancels on replacement. Readiness measurements,
LOID policy, capability negotiation and real subordinate acceptance remain work.

Six original baseline cases exercise Ready0/1 and prior protocol0/1/2, plus
missing identifiers and positive SetAllSetting ACK transition. The new
`scripts/test_fttr_sync_register.py` covers20 AF_PACKET/veth cases: original
reply-byte comparison, auto discovery, both JSON types, fragmented Register,
empty optional fields, wrong/missing serials, oversized/control-containing
fields, malformed policy and timeout. Existing39 sync and30 Wi-Fi apply cases
pass after sharing the private JSON loader. Reports are
`artifacts/fttr/registration-stock-20261103.json`, `registration-native-20261103.json`,
`registration-sync-regression-20261103.json` and `registration-wifi-regression-20261103.json`.
Real GCC14.4/musl package staging and source hashes are in `registration-build-20261103/`.

### Resident syncserv registration and Wi-Fi policies

`fttr-sync <interface> serve <ethertype> <private-policy-file>` now holds the
same interface lease as the one-shot commands for its whole lifetime. It keeps
16 heartbeat slots and separate type6/type7 reassembly for each source MAC.
Heartbeats remain on the original two-second cadence while other peers are
waiting for JSON. Three missed ticks expire a peer. Policy is selected by the
observed MAC and requires the same explicit expected serials as one-shot
Register; no JSON report or heartbeat alone authorizes a write.

The owner-only regular JSON policy is bounded below128 KiB. It contains1..16
unique peer MACs, with a registration policy and optionally0..16 complete Wi-Fi
configurations with distinct device-local SSID indices. All entries are
validated before socket ownership or any transmission. Example (synthetic
values; the Wi-Fi content has exactly the same validation as `wifi-apply`):

```json
{
  "Peers": [{
    "MAC": "02:00:00:00:00:01",
    "Registration": {
      "OUI": "020304", "SN": "synthetic-host", "Ready": 1,
      "ExpectedSN": "synthetic-peer", "ExpectedGPONSN": "ABCD00000000"
    },
    "WiFi": [{
      "act": "config", "SSIDIndex": "1", "SSID": "example",
      "SSIDAdvertisementEnabled": "true", "Standard": "11bgn",
      "ENCRYPT": 4, "PWD": "example-passphrase", "Enable": 1
    }]
  }]
}
```

A matching Register gets the baseline original reply and resets protocol to1.
A positive pending SetAllSetting ACK promotes it to2. Each configured SSID uses
an independent preflight read, one write and another readback. Peers advance
independently; a slow peer cannot block the other peer's exchange or heartbeat.
An omitted/empty WiFi list only requests settings after registration. Ready0
also permits observation but never sends a configuration; Ready remains an
explicit operator-supplied value, not a measured optical/service state.

Before each request, the ingress queue must reach EAGAIN; unsolicited reports
received while a send is pending are discarded. A bounded ingress batch keeps
heartbeats and termination responsive under traffic. Incomplete fragmented
messages hold subsequent sends until completion or their fixed deadline, so
an interleaved ACK cannot erase an in-progress Register. Every repeated
Register, malformed identity, expired peer or slot replacement cancels old
transactions and clears fragments. New matching registration may restart a
preflight only if no write has been attempted for that peer during this
process. Once a write was sent, re-registration/disconnection blocks further
policy writes for that peer; failed write/readback also stops remaining SSIDs.
Lost or negative write replies still trigger an independent readback while the
same registration is valid. Passwords are never retried automatically.

Without `--state`, write-attempt memory is process-local and restart can
reapply passwords. The durable mode described below records intent before
transmission and recovers with reads. The separate `/etc/init.d/tyfza1-fttr-sync`
service requires a private state directory, has no respawn and is disabled by default.
`/etc/config/tyfza1-fttr-sync` requires an explicit interface; it does not guess
whether the actual syncserv network is the management bridge or a hardware
endpoint. Supply a chmod0600 policy at `/etc/tyfza1-fttr.sync.json`, configure
`main.interface`, `main.enabled` and `main.state_directory`, and start/enable
the service only with the intended interface and readiness policy. Each peer
with Wi-Fi writes needs a Revision when using the service. This does not alter hardware `fttrd`
startup. There is no dynamic readiness or policy reload; the explicit UCI
snapshot converter below supplies initial Wi-Fi content.

Redacted JSON events include local slot, registration generation, protocol and
error. Apply events retain independent write/readback results and SSID index.
`identity_authenticated`, `credentials_verified` and `configuration_verified`
remain false. Registration reports `peer_acceptance_verified:false`. The
protocol still has no trustworthy transaction ID; later identical same-source
replies cannot be conclusively separated from current replies. No claim of
physical identity authentication, credential installation or real slave
interoperability follows from a successful resident run.

`scripts/test_fttr_sync_controller.py` covers37 AF_PACKET/veth cases with
modeled peers and original local Wi-Fi producer rows: concurrent peers,
multiple SSIDs,16-policy input, reverse fragments/interleaved ACKs, all-phase
re-registration and replacement, partial Register retirement, lost/negative
ACK and readback errors, expiry/slot reuse, interface ownership, termination,
Ready0 and malformed/private policy handling. `test_fttr_sync_init.sh` checks
disabled startup, explicit interface/policy, quoted procd arguments and absent
respawn. One-shot39 sync,30 Wi-Fi and20 Register regressions also pass after
extracting shared request/decoder code into `sync-client.c`.

Reports are `artifacts/fttr/resident-native-20261103.json`,
`resident-sync-regression-20261103.json`, `resident-wifi-regression-20261103.json`
and `resident-registration-regression-20261103.json`. GCC14.4/musl package
prepare/compile/install and staged source hashes are in `resident-build-20261103/`.
These are offline behavioral/build checks, not hardware acceptance.

### Original master readiness predicate

`tools/trace_stock_fttr_ready.py` now executes `libmib:isMasterReady` at
`0x81ac0`, instead of treating its return as an unspecified fixture. Original
`configd` definitions identify chain3004 as `ATM_VC_TBL`, row size11637,
`applicationtype` at offset8502, and scalar4111/4115 as
`CWMP_USERINFO_STATUS`/`CWMP_USERINFO_RESULT`.

Ready requires a WAN row whose applicationtype bit0 is set, a successful
`ifGetName`, successful `getInFlags` with IFF_UP, and successful IPv4
`getInAddr`. It then requires USERINFO_STATUS==0 and USERINFO_RESULT==1.
It does not check MiniOLT optics, IFF_RUNNING/carrier, nonzero returned IP,
Internet reachability or subordinate acceptance. The29 executed fixtures cover
row/name/flag/address failures, mixed service bits, later usable WAN rows and
all tested CWMP status/result combinations. They are in
`artifacts/fttr/ready-stock-20261103.json`, including input hashes and original
schema definitions. These are simulated external reads, not current WAN or
CWMP observations.

The current native branch has no equivalent live CWMP user-registration
provider. Therefore Ready stays explicit in both registration entry points;
neither `fttrd` optical state nor OpenWrt interface-up is substituted for the
original full predicate. Automatic readiness remains incomplete.

### OpenWrt Wi-Fi policy snapshot

The package now installs `fttr-sync-policy`, using real libuci to import one
specified wireless file and turn explicit bindings into a resident JSON policy:

```sh
fttr-sync-policy /etc/config/wireless /root/fttr-bindings.json /root/fttr-generated.json
fttr-sync br-lan serve 0x88aa /root/fttr-generated.json
```

The interface in the second command is an example, not inferred topology.
Both input files must be regular files owned by the effective user, with no
group/other permissions or symlinks. Wireless and binding inputs are bounded
at128 KiB (JSON strictly below the bound). Only the supplied committed wireless
file is imported: no `/tmp/.uci` deltas, default configuration directory,
shell evaluation or current radio-state inference. The output is a validated
private snapshot, not a live subscription to UCI changes. Output is staged at
mode0600, fsynced and linked into an absent destination atomically; an existing
file or symlink is never replaced. No SSID, password or raw JSON is printed.

Binding example (synthetic identity; each entry requires explicit Registration
as in the resident policy above):

```json
{
  "Peers": [{
    "MAC": "02:00:00:00:00:01",
    "Registration": {
      "OUI": "020304", "SN": "synthetic-host", "Ready": 1,
      "ExpectedSN": "synthetic-peer"
    },
    "WiFiFromUCI": [
      {"Section": "default_radio0", "SSIDIndex": "1", "Standard": "11bgn"},
      {"Section": "default_radio1", "SSIDIndex": "9", "Standard": "11ax"}
    ]
  }]
}
```

Bindings use existing, named `wifi-iface` AP sections and their `wifi-device`
reference. Positional/anonymous sections are rejected. Radio `band` must be
explicitly2g/5g and match the subordinate index1..8/9..16. SSID and key are
copied as UTF-8 without shell interpolation or truncation. Enable is false if
either radio or AP is disabled; advertisement is the inverse of hidden. Absent
disabled/hidden options use their normal false defaults; malformed/list values
are rejected.

| OpenWrt encryption | Original Flag1 ENCRYPT |
| --- | --- |
| none | 0 |
| psk | 2 |
| psk2 | 4 |
| psk-mixed | 6 |
| sae | 16 |
| sae-mixed | 20 |

The converter supports these exact modes with a single passphrase, and empty
key only for open mode. It rejects WEP key slots,64-digit raw PSKs, enterprise,
OWE, explicit cipher suffixes and separate authentication/PMF/FT/WPS fields
that Flag1 does not represent. This is SSID/passphrase and authentication-family
mapping, not a complete hostapd security/radio policy clone. The direct JSON
entry point remains available for independently established native settings.

Standard is intentionally explicit for the subordinate. OpenWrt htmode includes
channel width, and a master's configured HE/VHT mode does not prove subordinate
support. Ten retained read-to-MIB standard pairs were executed:2g11b/11g/11bg/
11gn/11bgn/11ax and5g11a/11na/11ac/11ax. Although the original setter also accepts
11n, its retained getter returns `undefined` for raw8; it is not included in the
native verified readback set. Mapping does not change channels, widths, power,
VLANs, network attachment, ACLs or roaming settings.

`scripts/test_fttr_sync_policy.py` runs49 converter invocations using the actual
packaged AArch64/musl executable and libuci. It covers both bands, escaping and
UTF-8,32-byte SSID, flags, supported authentication families, invalid/missing/
ambiguous bindings, unsupported security settings, private file checks and
existing output preservation. It compares24 generated entries with the retained
original remote writer, then feeds a generated dual-band snapshot into the
packaged resident daemon over AF_PACKET/veth: two writes and four reads pass.
`credentials_verified` remains false. Reports are
`artifacts/fttr/uci-native-20261103.json` and `uci-wifi-fields-stock-20261103.json`.
Final GCC14.4/musl package build/install and source hashes are in
`uci-build-20261103/`; its stand-alone staging command supplies rpath-link for
libuci's libubox dependency. No physical slave or password installation was tested.

### Durable syncserv write intent and restart recovery

The resident CLI accepts an optional persistent state directory:

```sh
mkdir -m 700 /etc/tyfza1-fttr-sync-state
fttr-sync br-lan serve 0x88aa /root/fttr-generated.json --state /etc/tyfza1-fttr-sync-state
```

The directory must exist, be owned by the effective user and have no group/other
permissions. A symlink directory is rejected. The controller holds an exclusive
filesystem lock on it, in addition to its network interface lease. Use storage
that survives restart and honors fsync; `/tmp` and `/var/run` do not provide
reboot persistence. The service's `main.state_directory` defaults to
`/etc/tyfza1-fttr-sync-state` and is not automatically created or cleared.
The package conffile list includes the directory for configuration preservation.

Each peer with a nonempty WiFi list must have an explicit `Revision`, for
example `"Revision":"setup-1"`, alongside MAC, Registration and WiFi.
It is1..32 ASCII letters/digits/underscore/hyphen. The UCI converter accepts
and preserves this same field alongside WiFiFromUCI. A new revision explicitly
requests a new application; do not automatically generate one on every boot.
Old revision records remain retained, so returning to an old revision does
not replay it. There is no automatic record pruning or policy reload.

Before each configuration packet, `sync-store.c` writes the complete desired
peer configuration and a cumulative attempt bitmap to `<mac>-<revision>.json.new`
with mode0600, fsyncs the file, atomically renames it to the committed record,
and fsyncs the directory. Only after all those calls succeed can transmission
proceed. It then returns to ingress/liveness processing before sending anything
else, so a replacement queued during disk IO cancels the pending write.
Persistence failures stop the controller without sending that configuration.
A crash before commit leaves a disposable staging file and no sent packet;
a crash after commit, including before the send syscall, leaves a possible
attempt and forbids automatic replay.

The record binds host source MAC, EtherType, peer MAC, revision, master/expected
peer identity and the entire ordered WiFi list. Changing any of these under
the same revision fails with ESTALE before the first heartbeat. Ready is omitted
from the immutable identity so explicit readiness changes do not force a new
password application. Invalid JSON/masks, public permissions and record symlinks
also fail before network activity. Records contain passwords to compare exact
policy identity, so retain their private permissions and do not publish them.
The record proves local intent only, not transmission or remote acceptance.

After restart the controller still requires fresh discovery and matching
Register. An attempted row enters a read-only recovery phase. If its readable
fields match, the controller advances to the next row; a row never attempted
can then follow normal preflight/write/readback. A recovery mismatch, malformed
report or timeout stops the remaining rows for that peer. It never resends an
attempted row, restores an unknown previous password or marks credentials
verified. `sync_resident_recovery` includes revision/index/result,
`prior_write_outcome_unknown:true`, `write_replayed:false` and the readable-field
comparison. Regular events also include revision when present. Re-registration
or expiry still cancels all in-process transactions.

This is conservative recovery of possible writes, not exactly-once delivery.
If a crash happened after intent commit but before actual transmission, a new
explicit revision is required to retry after inspecting the peer. Loss/deletion
of the state directory removes replay history. Physical flash/power-cut
behavior, final credential verification, failure outcome journaling, status
inspection tooling and automated rollback remain unverified or unimplemented.
Synchronous fsync can briefly delay heartbeats; link/ingress is revisited after
it completes, but no bound on physical storage latency is claimed.

`scripts/test_fttr_sync_store.py` covers38 native AF_PACKET/veth cases: successful
restart, SIGKILL after a modeled peer receives a write with/without application,
file-fsync and directory-fsync failure/stops, replacement queued during fsync,
recovery mismatch/timeout, resuming never-attempted rows, policy/password/pin/
source/row-order mismatch, returning to old revisions, malformed/private records,
and directory ownership. Test-only libc hooks stop/fail fsync; no production
fault switches are added. Packaged UCI tests now run51 conversions and26 original
writer comparisons, then send the dual-band generated policy using the real
musl resident executable with persistence enabled and verify intent files before
replying. The existing37 resident and89 one-shot network cases also pass.

Reports are `artifacts/fttr/store-native-20261103.json`,
`store-resident-regression-20261103.json`, `store-uci-regression-20261103.json`,
`store-sync-regression-20261103.json`, `store-wifi-regression-20261103.json` and
`store-registration-regression-20261103.json`. GCC14.4/musl package build/install
and staged source hashes are in `store-build-20261103/`. These establish process
crash behavior and filesystem ordering, not physical power-loss survival or
actual subordinate password installation.

### FPGA boot image and MiniOLT dependency

The retained image at `selected/ubi_fpga/top.bin` is3,789,476 bytes with SHA-256
`9c7e8ba3b1521277d2af4b777869287010bc3e8e7884a2542f6ab5ec4af58ab5`.
`tools/prepare_fttr_fpga.py` checks that exact identity and installs unchanged
bytes as `realtek/rtl9617c/fttr-top.bin`; an image of the same size but different
content is rejected. This identifies the available sample, not every FPGA
revision. The binary remains external to Git.

For the FPGA development build, merge `tyfza1-fttr-fpga-packages.config` with
the existing FTTR package fragment, use `fttr-2p5g.dtb`, and provide:

```sh
TYFZA1_FTTR_FPGA_SOURCE=/absolute/path/to/original/top.bin
```

`tyfza1-fttr-fpga` installs the retained image and depends on
`kmod-tyfza1-fttr-fpga`, which packages/autoprobes `tyfza1-pg2l100h` and depends
on the DesignWare MMIO SPI package. FPGA Manager is built in for the FTTR
kernel package so MiniOLT's optional manager calls remain linkable. The normal
FTTR package fragment does not select or require the FPGA image.

The FPGA development node has `firmware-name` and `realtek,network-engine`;
the SPI probe waits for the linked NI/P6 prerequisite before GPIO/SPI setup,
then requests and loads the image through FPGA Manager. Missing image, handshake
failure and SPI errors fail that probe; there is no blind loading retry loop.
MiniOLT's `fpga-mgr` phandle resolves this exact supplier before any MDIO
identification. Absent/unbound supplier defers the consumer. Under the manager
lock, both its completed-operation state and current DONE state must say
operating. This prevents pre-load zero registers from being treated as FPGA1.
The consumer retains a manager reference and a managed device link, so a
supplier unbind retires its consumers before loading can occur again.

For a node with firmware-name, the manual firmware store always returns EBUSY.
An explicit loader unbind/reprobe is the reload path, with consumer retirement
handled by the device link. No mutex is held across a consumer's lifetime;
manager locking stays within a single operation/thread. A manual node without
firmware-name keeps the explicit firmware store but is not accepted as the
managed MiniOLT supplier. Failed allocation/load paths release image and manager
resources. Physical device removal/reprobe behavior still needs hardware testing.

The retained `lib/modules/users/sloader.sh` contains a further post-DONE register
stage. Native `fpga_init` on FPGA MiniOLT devices now implements it:

| Detected backend/input | Ordered operations |
| --- | --- |
| FPGA1, no calibration file | `40023=100000`,1ms delay,`10=1` |
| FPGA1, calibration file present | `40001=25`,`40002=22`,`40022=5`,each followed by1ms; then the preceding sequence |
| FPGA2 | No writes in this stage |

Values are hexadecimal except delay. Writing0/1 to `fpga_init` explicitly
selects absent/present calibration; it does not itself calibrate the optic or
claim optical readiness. The attribute is absent on ASIC9690 and preserves
MDIO errors before any further operation. The controller selects the calibration branch after factory optical import.
The FPGA DT keeps the unconfirmed static GN25L99 child disabled; the native
factory-selected client and FPGA bootstrap below handle the actual model.
Successful bitstream loading alone does not establish complete FTTR startup.

`scripts/test_fttr_fpga_boot.py` executes the original post-ready shell suffix
with modeled MDIO and compares its four FPGA/calibration combinations with the
native helper, including every write-error cutoff. It also runs ten production
MiniOLT dependency branches and six loader branches in C shims, checks image
identity/rejection, and confirms MDIO identification follows dependency acquisition.
The prior `test_fttr_fpga.py` still matches original PROGB/SPI payload ordering
and verifies SPI/handshake error propagation. Reports are
`artifacts/fttr/fpga-boot-native-20261103.json` and
`fpga-loader-regression-20261103.json`.

Real6.18.55 kernel objects compile in `fpga-boot-kernel-20261103.log`. External
module compilation/modpost/linking succeeds under `fpga-modules-20261103/`,
including the actual kernel FPGA Manager to supply the new symbol references;
this isolated symbol check does not replace a full firmware ABI build. Its
stripped module metadata configuration emits description warnings. The FPGA
DT compiles to `fpga-boot-20261103.dtb`, with the manager phandle and image name
checked. `fpga-package-20261103/` runs real OpenWrt prepare/compile/install for
the control and optional firmware recipes, verifying the installed image hash.
No physical FPGA, GPIO timing, optical link or ONU interoperability was tested.

### FPGA management transport

The retained `onuinfo_check` selects `onuinfo_fpga` only for FPGA1;
FPGA2 and ASIC use `onuinfo`, which links `libponrelay.so`. Executing
the supervisor dispatch corrects the earlier non-ASIC grouping. The FPGA1
executable links `libponrelay1p25g.so`. Its `plr_fpga_init` (`487e0`)
and `drv_gpon_fpga_glb_reg_init` (`45e3c`) differ from the ASIC relay:
they write the whole EtherType register `30004=8888`, then `40029=c0c8`,
and enable bit3 of `4000c`. The FPGA2/ASIC relay uses `40029=c140` and updates
the EtherType only when the prior low16 bits are `88a8`.

Native `omci_link` configuration now selects those actual backend sequences.
The FPGA1 relay's baseline sender (`485e0`) always uses destination
`00:11:11:11:11:11`, host source MAC, EtherType8888 and a16-byte envelope;
it does not consult the peer-source-MAC or downstream-length-mode registers.
The FPGA1 `omci_link` report supplies this effective destination/envelope to
the existing native packet builder. FPGA2/ASIC reporting is register-derived,
including the optional downstream length field.
The explicit host MAC write uses the existing original `drv_set_omci_dst_mac`
register layout; it binds native reception to `fttr0`'s selected MAC.

FPGA backends now expose `omci_link`, read-only `onu_table`/`onu_state` and
`tx_enable`, in addition to version/backend and post-DONE control. Original
FPGA `drv_gpon_trx_set` (`451b4`) changes only bit0 of register20, so native
FPGA TX control preserves every other bit. Probe and removal both disable
TX through this backend helper and retain errors. The ASIC whole-register
write is unchanged. These controls do not automatically start the optical
link or assert that a downstream ONU is ready.

The original FPGA bandwidth entry points `drv_gpon_alloc_id_config` (`42c54`)
and `drv_gpon_alloc_id_clear` (`42cc0`) return without programming registers.
They do not establish the ASIC's per-ONU T-CONT/DBA recipe for FPGA hardware.
ASIC core clearing, WTG/PON SerDes and reset GPIO remain unavailable on FPGA.
FPGA2 uses a separate management grant at slot0; FPGA1 uses global OMCI setup. The backend-specific DBA access in the retained
`libasicdrv.so` provides additional evidence described below. It supersedes
the earlier assumption that the empty relay API was the only grant interface.

`scripts/test_fttr_fpga_control.py` executes the original relay initialization,
TX and serial reader, plus the retained state getters with FPGA1/FPGA2 inputs.
Twelve groups compare IO/state,18 baseline frames compare the selected relay
envelope, including70-byte FPGA2 length-mode frames,
and each initialization/TX IO error stops at the failed operation. Production
sysfs visibility and removal predicates run in C shims. The ASIC OMCI and
FPGA boot/dependency regressions pass, as does GCC14.4/musl compilation of
the MiniOLT object against Linux6.18.55. Reports/logs are
`artifacts/fttr/fpga-control*-20261105.*` and
`fpga2-control-20261106.json`. MDIO, registration and optical
state are modeled; FPGA extended OMCI, grant behavior and physical traffic
remain unverified.

### FPGA DBA and controller integration

The retained `libasicdrv.so:drv_set_onu_tcont_dba_profile` (`d428`) branches on
the actual backend. FPGA1 uses `40400 + 2 * zero_based_ONU`: write `14bf0`,
replace only the low16 bits of the next word with the fixed grant, then write
`assured << 16 | maximum` to the first word. Preserving the high16 bits is
essential because they contain registration/block/FEC state. The slot argument
is ignored by the original FPGA1 API; native exposes one physical slot0 and
rejects other slots rather than creating independent aliases. FPGA2 and ASIC
use `41000 + ONU * 32 + slot * 4` and a separate fixed-grant word. Raw readback
retains the existing `ONU * 8 + physical_slot` eight-byte record layout.

`libponrelay1p25g:plr_gpon_const_val_init` (`81104`) declares Alloc-ID256..319;
its allocator (`8e6d8`) gives `256 + (ordinal-1)*64 + (ONU-1)` and checks the
per-PON occupancy bitmap. Only ordinal1 fits that pool. Native FPGA1 service
slot1 therefore uses Alloc-ID`256 + zero_based_ONU` and physical grant slot0;
GEM tagging continues through the existing service/VLAN/FE path. The original
FPGA Set builder (`62530`) confirms the resulting class262 wire assignment.
FPGA2/ASIC service numbering uses `256 + zero_based_ONU * 8 + slot`,
with data slots1..7 and management slot0.

CLI and resident control now read the mandatory `backend` attribute. Missing
or unknown identity fails explicitly. FPGA1 OMCI sessions do not request the
separate management grant: the retained FPGA per-ONU OMCI GEM function (`43608`)
does no IO, with management setup performed by the existing global FPGA init.
This avoids overwriting the single data grant when management sessions open
or close. FPGA link checks require the real SoC P6 clock/link, without reading
an absent WTG peer-SerDes attribute. This checks host connectivity, not optical
readiness. FPGA bootstrap and factory-selected optical calibration are described
below; complete original FPGA behavior remains incomplete.

FPGA2 uses the ASIC relay selected by the original supervisor, not the FPGA1
relay. Its native service/explicit T-CONT path now uses that relay's Alloc-ID
formula and the independently verified FPGA2 DBA layout. The prior refusal
was based on the wrong executable selection; the corrected startup and
controller integration are detailed below.

Executing the actual `plr_ponsrv_hw_bw_set/clear` chain confirms that its
`drv_gpon_alloc_id_config/clear` implementations are no-ops in both retained
relays (FPGA42c54/42cc0, ASIC43a08/43a74). Across144 direct/wrapped calls with
both resource-number ranges, the real routines return success without any
nonstack memory write or hardware call. The wrapper reaches the actual driver;
it is not replaced by a success hook. Thus neither its success nor the
high-level `tcont_add` path proves physical Alloc-ID/DBA binding or grant
withdrawal. Native verified register programming remains required. In contrast,
`onuinfo`, used by FPGA2 and ASIC, separately calls `SetOnuTcontDba`;
the FPGA1 executable does not import that entry. The executed FPGA2 startup
and registration callback below establish the independent DBA producer. The optional `--asic-relay` comparison records both paths in
`artifacts/fttr/relay-bw-noop-20261106.json`.

`test_fttr_fpga_dba.py` compares63 original/native DBA write/readback groups,
every IO failure cutoff,64 original Alloc-ID/occupancy cases and FPGA Set
frames, plus native backend/physical-slot/link checks. The real CLI service
test covers FPGA1 creation, repeated adoption and failed-VLAN restoration.
`test_fttr_autoprovision.py --backend fpga1` runs the full resident lifecycle
with no WTG or management-grant attributes and the fixed FPGA management MAC:
replacement, MIB loss, disconnect, restart, real Linux GEM VLAN creation and
the existing acceleration-withdrawal failure ordering. ASIC resident/startup,
core, MIB and FPGA control regressions pass. These tests model registers,
subordinate MIB storage and DBA completion; they do not prove optical grant
delivery, FE hits or physical packet retirement.

Reports are `artifacts/fttr/fpga-dba-final-20261105.json`,
`dba-final-{service,asic-resident,fpga1-resident}-20261105.json` and
`dba-startup-20261105.json`. GCC14.4/musl OpenWrt package compilation and
Linux6.18.55 MiniOLT module compilation/modpost/link pass in
`dba-build-final-20261105.log`, using the FTTR build tree with main mounted
read-only. The previously assembled full FIT images predate these DBA changes.

### FPGA2 controller selection and startup

`tools/trace_stock_fttr_backend.py` executes the retained supervisor main
(`onuinfo_check:400adc`, dispatch at400bac..400bf8): backend1 execs
`/bin/onuinfo_fpga`; backend2/3 exec `/bin/onuinfo`. The latter's ELF dependencies
select `libponrelay.so`. Physical FPGA2 and protocol-family selection are
therefore separate: its MDIO completion, image/optical/P6 setup stay FPGA-specific,
while OMCI framing, registration/admin/recovery, Alloc-ID and management grants
follow the ASIC relay. This supersedes the earlier FPGA2 refusal and fixed
FPGA1-envelope assumption; it does not infer the physical backend from a library.

Executing `onuinfo:418e30` with backend2 confirms the startup sequence:
reset PHY0/1 (`10010=0`, `10011=0`), optical delay (`40022[3:0]=6`), soft reset
(`10=0`,100us,`10=1`), then OTP mode (`11=6` for host mode5, or7 for mode9).
Mode9 also sets pause enable (`30005` bit4). The native FPGA2-only `fpga2_init`
attribute applies these operations under the MiniOLT control mutex and stops
at the first failed IO. The existing post-DONE helper remains a separate no-op
for FPGA2. Resident boot passes mode5, matching the original forced FPGA P6 mode,
and applies common default DBA values19440/38/38/10/10 before opening TX.
ASIC GPIO/core/WTG/PON SerDes remain ASIC-only.

The actual registration callback's DBA loop (`415ec4..415fc0`) uses the table
initialized by that executed startup. For registered one-based ONU IDs1/6/64,
it calls `SetOnuTcontDba(id,0,0,3840,3840)` for management and
`SetOnuTcontDba(id,1,0,0,19440*64)` for its default business. The retained relay
allocator supplies `256 + zero_based_ONU * 8 + slot` for all448 data slots;
the direct FPGA2 register driver maps them to `41000 + ONU*32 + slot*4`.
Native explicit policies use their requested data-slot grant and the separate
management slot. Importing the complete factory default service remains work.

FPGA2 now participates in service creation/readback/withdrawal, global and
per-ONU authentication, residual-grant cleanup, serial migration and admin/error
recovery. It uses the ASIC relay's masked downstream block bit, three-word
Deactivate PLOAM, upstream error mask01f00000,8s scans and15s temporary release.
FPGA1 retains its whole-word/one-word/100ms behaviors. Both FPGA physical TX
helpers preserve unrelated register20 bits.

Verification executes original dispatch/startup,448 allocator cases, both host
mode register sequences and every native IO failure cutoff; management wire,
DBA, all three backend admin/move/recovery and startup tests pass. Actual
`fttrd`/`fttrctl` run in isolated network namespaces with veth, Linux bridges
and VLANs: FPGA2 global auth/Wi-Fi, per-ONU selection/learning/migration,
slot7 grant isolation, explicit migration and admin lifecycle pass. FPGA1
policy and ASIC wire regressions plus P6 forward/TX gates pass. Hardware/peer
registers and MIB storage remain modeled. Reports are
`artifacts/fttr/fpga2-*-20261106.json`; FTTR package and MiniOLT/BOSA module
compilation/linking pass in `fpga2-package-modules-20261106.log`.
No physical FPGA2, DBA delivery, FE hit or complete queued-packet retirement
acceptance is claimed.

### Factory-selected optics and FPGA bootstrap

The retained `rtkoltbosa` dispatches four calibration operation tables:
UX3326 (type1,588 bytes), UX3326S (type2,901 bytes), GN25L99
(type10,661 bytes), and GN25L81 (type20,284 bytes). The factory file contains
the model and the eight-bit I2C address; the address is not restricted to the
fallback Cx/Dx scan list. FPGA `optical_init` validates that file and creates
the optical I2C client at its address. Repeated creation at the same address
is idempotent; a changed address returns `EBUSY`. Client teardown precedes
adapter teardown. The retained ASIC DT still binds its confirmed GN25L99.

Native calibration reproduces the original56/514/128/256 register writes.
UX3326S includes the unlock, six page ranges, final page0 selection and
register90 bit3 toggle. Identity checks use the original `UX3326S1` bytes
or GN register60 markers `b0`/`a1`. UX3326 has no original silicon-ID
predicate: native requires an acknowledged register0 read and trusts the
validated factory type. File checksum/length/type mismatch and I2C errors
stop before success, unlike the original initializer's checksum warning
followed by writes. Calibration status rechecks identity and clears readiness
on failure. These checks do not prove laser power or optical readiness.

The rc script configures host P6 mode5 with the factory nway setting before
FPGA loading. The resident FPGA bootstrap stops TX/netdev and preserves that
configured P6; a warm restart reconfigures it if the preceding owner stopped
it or the requested NWAY differs. It performs original post-DONE setup,
FPGA2 controller initialization or version-gated FPGA1 authentication, waits650ms,
calibrates the selected optics and checks P6. It then configures the original
management envelope, verifies calibration, opens the interface and enables TX
last. FPGA paths skip ASIC reset/WTG/PON/core attributes; FPGA2 also applies
the original common default DBA settings. The rc script creates the optical
client and waits for its driver before launching the controller. FPGA2
authenticated startup and service provisioning pass with modeled devices.

`test_fttr_optical_models.py` executes all four original ARM64 initializers
with three patterned files each, compares every write and identity check,
and injects errors at each operation. It also exercises the production
configure/status paths, userspace status parser, dynamic client creation and
the shipped rc script, including delayed binding and missing-device failure.
The retained actual GN25L99 factory calibration, FPGA control/dependency and
ASIC/FPGA1/FPGA2 namespace startup regressions pass. Reports are
`artifacts/fttr/optical-*-20261105.*`. The current OpenWrt package and both
MiniOLT/BOSA modules compile and link against Linux6.18.55; module postprocessing
retains the build's `MODULE_DESCRIPTION` warnings. Full FITs predate this change.

Remaining differences include broader optical DDMI and SFP dispatch.
The actual optical model of the retained FPGA
sample is not established. No physical optical, FPGA or traffic acceptance
is claimed by the offline recipe and startup comparisons.

### P6 prerequisite for FPGA loading

Original `olt_startup.sh` forces mode5, calls `serdesInit` (whose main invokes
`rtk_fc_external_virtual_port_init`), and only then inserts the FPGA loader.
The native rc script now follows that order after factory import, passing
the actual NWAY value. The FPGA DT points at NI; the loader retains a device
reference and managed link, checks the bound supplier, and acquires a P6
load hold before any FPGA GPIO setup. A down `fttr0`, configured mode5 and
P6 clock bit13 are required. The peer PCS link is intentionally checked
later, after loading its logic. Missing configuration returns EPROBE_DEFER
without FPGA GPIO or SPI activity. Kconfig requires the native NI driver.

The NI hold is serialized by RTNL. It closes the software TX gate and FE
admission; while held, netdev open, P6 reconfiguration and FE enable return
EBUSY. Other NI/DMA users remain available. Successful loading releases the
hold with P6 configured and software transmission still closed. GPIO,
firmware, manager-lock, SPI or DONE failure stops P6 before returning the
error and releases references. Both initial setup and optional manual image
programming use this hold. NI unbind retires the FPGA/MiniOLT consumers via
device links. This is boot/reload ordering, not a per-ONU packet-retirement
barrier; queued-packet retirement remains unresolved.

Early autoprobes defer until the rc script configures P6. The script then
uses module loading and `drivers_probe`, checking the driver's `firmware`
attribute that appears after successful probe. A `driver` symlink alone is
insufficient because the core creates it before calling probe. It waits for
the resulting MiniOLT consumer before optical setup. Missing supplier/load
failure stops startup. A warm start keeps the loaded image and uses the
controller lease/normal teardown. On a cold start, the daemon preserves the
preload P6 clock rather than resetting it after the bitstream was loaded.

`test_fttr_preload.py` executes the production rc sequence for both NWAY
values, warm/probing states and P6/module/load/consumer failures, plus the
production dependency/probe functions' reference and error paths.
`test_fttr_tx_gate.py` interleaves production load-hold callbacks with DMA
wake/transmit and checks open/reconfigure/FE exclusion, wrong mode, absent
clock, FE failure and failed-load stop. Original loader post-DONE/GPIO/SPI
checks remain separate. FPGA1/FPGA2 and ASIC startup regressions pass,
including a preconfigured cold start with no extra P6 reset and a warm
replacement after teardown. Four optical recipes, shared FE and21 threaded
LAN registration/removal regressions pass. Reports/logs are
`artifacts/fttr/p6-preload-*-20261105.*`. Linux Image/modules, the new exported
NI symbols and FPGA module link, the FPGA DT phandle and native package
build pass. No bitstream was loaded onto a physical device.

### UX3326 temperature adjustment and recovery

Original `rtkoltbosa` UX3326 initializer (`40222c`) remains resident when
called with argument1. Its monitor (`402650..402b54`) now runs natively
through the optical driver's `maintenance` attribute and `fttrd`'s existing
event loop. Calibration byte6 enables200ms temperature adjustment; otherwise
the loop interval is1000ms. Read register2e, round/map/clamp to one of84
entries at calibration offsetfc, compare register6's packed10-bit value,
and write6 then7 only when it differs. The initial56-write calibration
count is unchanged by runtime updates.

The original reset test runs after1600ms (2000ms with adjustment disabled),
reloads the factory file, and checks registers0..3 against `00 80 00 00`.
Only when that reset pattern differs from the calibration does it test TX
power. The original conversion at404d08 uses the register34 word, the
factory gain at8 and signed offset at10. Native compares the original
`-30dBm` threshold in linear units, avoiding floating point in kernel.
It preserves the original signed32-bit product semantics; invalid negative
power is reported as an error instead of treating a NaN as good power.

Low power with a reset signature requests the original56-write reload,
waits5s, rechecks, and if necessary disables TX for1s then enables it and
waits1s before the last power check. These waits use event-loop deadlines;
OMCI, ONU identity refresh and stop signals continue to run. The driver
performs no independent periodic writes, so controller teardown cannot be
followed by a delayed recovery re-enabling the laser. I2C/firmware errors
stop the controller and its existing cleanup disables TX and withdraws FE.
Continued low power after the last recovery returns `ENOLINK`; the original
only logged the failure and continued. Each power read reloads the factory
input; model/address/length/checksum changes fail before IO to a mismatched
client. Runtime recovery is available through the normal bootstrap form
of `fttrd`, which retains the optical directory descriptor.

MiniOLT SMBus now advertises byte and word transfers. Its register engine
returns the first wire register in the high byte; the adapter swaps to the
Linux SMBus convention, and BOSA uses `i2c_smbus_read_word_swapped()` to
retain the original numeric values. Tests cover both directions and error
propagation as well as the original underlying I2C register transactions.

`test_fttr_optical_monitor.py` executes514 temperature-boundary cases,
63 original floating-point power cases, reset predicates and four complete
original monitor/recovery loops with external IO modeled. Production driver
commands and userspace lifecycle checks cover write failures, firmware
ownership, invalid/missing input and recovery deadlines. The real daemon's
namespace startup includes successful recovery, persistent low power,
temperature IO failure and SIGTERM during recovery; ASIC startup and existing
OMCI controller/CLI regression pass. GCC14.4/musl package and Linux6.18.55
MiniOLT/BOSA module builds pass. Reports/logs are
`artifacts/fttr/optical-monitor-20261105.json`, `monitor-startup-*-20261105.json`,
`monitor-controller-20261105.json` and `monitor-build-final-20261105.log`.
These checks do not establish measured optical power, actual temperature
stability, laser safety margins or physical recovery on a board.

### FPGA1 factory authentication

`onuinfo_fpga` startup (`4122f8`) runs authentication only for backend1 and
version greater than `230406ff`. MIB2715 is `HW_OLTFPGA_CIPHERTEXT`, a32-byte
type13 field in `configd`; `libmib:string_to_mib` stores its eight hexadecimal
words as little-endian u32s. The factory importer now converts that field into
private runtime storage, and the firmware package links to it. Missing fields
remove stale input. Malformed supplied fields fail rather than generating a
default authentication value. No device ciphertext is stored in Git or logs.

The `fpga_auth` operation checks the actual FPGA version, loads the per-unit
input when required, then reproduces `drv_set_auth_ciphertext` (`11420`):
eight writes from register57 down to50. After the original1200000us delay,
`drv_get_auth_result` (`11490`) reads5e; bit0 clear means success. A read of
`ffffffff` or IO failure returns an error. The controller executes this after
post-DONE setup and before optical calibration/TX enable. Older FPGA1 skips
the credential request; FPGA2/ASIC do not expose the operation. Original
startup logs a rejected authentication but continues; native returns `EACCES`
and the controller keeps TX disabled and releases ownership.

`test_fttr_fpga_auth.py` executes the actual original startup prefix,
`libasicdrv` authentication and `libmib` type13 conversion. It compares36
backend/version/result cases and each IO failure cutoff, plus the production
sysfs handler and private factory import, including stale-data removal.
FPGA1 namespace startup covers rejected authentication and cleanup before TX;
FPGA2 startup, FPGA management and the existing whole factory importer
regressions pass. GCC14.4/musl packages and Linux6.18.55 MiniOLT/BOSA modules
compile and link. Evidence: `artifacts/fttr/fpga-auth-20261105.json`,
`auth-startup-*-20261105.json`, `auth-build-20261105.log` and
`auth-factory-build-20261105.log`. All credentials/registers in these protocol
tests are synthetic. Physical authentication acceptance remains unverified.

### Full offline build after main alignment

An isolated OpenWrt checkout under this worktree now builds `world` with
both FTTR package fragments enabled, using the pinned OpenWrt revision,
GCC14.4/musl and Linux6.18.55. The kernel was prepared from all current
patches and overlay sources; full Image, modpost, MiniOLT/BOSA/FPGA modules,
userspace packages, rootfs and package indexes complete successfully at
`0ca3e4b`. The current source hashes match the compiled kernel tree.

The first full build exposed11 newly visible FPGA Kconfig prompts. The
target config now explicitly disables those unrelated FPGA drivers, so
enabling the optional package is noninteractive (`c695db6`). Copying the
existing build inputs into the isolated tree also required relocating
absolute symlinks and26 pkg-config files; otherwise two copies of the same
compatibility endian header hid libc definitions when compiling host apk.
That relocation is local build preparation, not an FTTR product fix. The
source main checkout was mounted read-only inside each build's private
mount namespace to catch accidental writes through residual paths.

Offline inspection verifies21 native driver source hashes,9 packaged
AArch64 programs/modules, the original FPGA image hash, both P6 SerDes
profiles, calibration symlink and FTTR service script. Both FTTR DT artifacts
retain DMA reorder/P6 resources; the FPGA DT manager phandle and image name
match and its unconfirmed optic remains disabled. The rootfs contains no
original NI/FC/relay/ASIC host runtime. The receipt is
`artifacts/fttr/world-20261105/build-verification.json`; final build log is
`artifacts/fttr/world-p6-routing-20261105.log`.

The ordinary sysupgrade FIT still contains ordinary UBI DTs, so it is not
a complete FTTR boot image. Separate explicit FTTR RAM FITs were assembled
with the built initramfs and ASIC/FPGA DT artifacts, at
`world-20261105/ram-asic/image.itb` and `ram-fpga/image.itb`. Their kernel,
FDT and script SHA256/RSA payload signatures independently verify against
the supplied certificate. They are local research artifacts, not released
or boot-tested firmware, and do not imply completion of the functions below.
Per the user's current instruction, further work remains offline and no
device access, loading or flashing is authorized at this stage.

### Resident ONU deactivation and explicit resume

`fttrctl /var/run/tyfza1-fttr onu-admin <hardware-ID> <16-hex-serial> block`
asks the resident controller to permanently deactivate that exact ONU. `resume` clears
the block and lets the normal inventory/service policy run when registration
is observed. `deactivate` requests original temporary state4, with automatic
release owned by the running controller. This command uses the controller state directory; existing
read-only/OMCI commands still use the MiniOLT sysfs directory. A successful
reply confirms the local operation and blocked-bit readback, not remote
PLOAM reception or optical re-registration.

Original ASIC `libponrelay:drv_gpon_onu_state_set` at47dcc uses local4788c
to mask downstream bit16, then479d0 sends `(ONU<<24)|00050000,0,0` through
`SendDsPloamMsg`. State5 sets `g_onu_deactive_keep_flag`; the original15s
callback at4775c consequently leaves the block set. Native permanent block
has no background unblock timer. State3 explicitly clears bit16.

The temporary state4 differs from permanent state5 in software keep state.
The original ASIC `ONU_Offline` block at48acc..48c10 clears bit16 when
keep is0, including when bit18 is still set; its15s callback is an additional
release path. FPGA's polling block47ac0..47cd0 only reaches this release
when downstream bit18 is clear, and does not schedule that timer. It clears
only bit16 of the observed word, whereas the explicit state3 operation below
replaces the whole FPGA downstream word. The original loop scans64 ONUs
with1ms per-ONU and36ms between scans, in addition to register work. Native
checks outstanding FPGA operations on100ms scan deadlines and ASIC operations
on8s scan deadlines; ASIC also has the15s per-operation deadline. The8s
interval comes from `polling_onu_state` at48e98..48ed8: every fourth2s
iteration calls `ONU_Offline`. Register work and scheduler latency can extend
these intervals. The extended original test executes24
offline state/keep combinations and the actual ASIC callback for states4/5.

FPGA `libponrelay1p25g:drv_gpon_onu_state_set` at47188 instead calls46acc:
write the **whole** `40401+2*ONU` word to10000, wait10000us, then clear
`40803+4*ONU`. Its write-check routine45920 does not mask/preserve the
fixed grant. Local46c30 sends only mailbox word40008 between40007=0 and1;
it does not write40009/4000a. Resume writes the whole downstream word to0,
so it must wait for hardware registration again. These operations are now
backend-aware; unrelated ASIC low-level `onu_block`/generic `ploam` sysfs
attributes remain hidden on FPGA. The new `onu_admin` attribute serializes
identity check and operation with DBA under `control_lock`; the existing
MDIO transport performs the backend-specific table-completion wait.

The supported administration command goes through the controller's private
Unix socket and MiniOLT lease. It rejects a stale serial before touching FE,
closes FTTR FE admission, cancels pending OMCI/Wi-Fi work, removes the owned
GEM VLAN/FDB and withdraws/readbacks its DBA grant. Only then does it call
the driver's serial-checked state change. FE stays closed throughout that
sequence; successful completion restores admission for the remaining ONUs.
A second identity mismatch permits fresh inventory without touching the new
ONU state. Other hardware/cleanup failures stop the controller and disable
TX; they cannot silently resume acceleration. Socket access is0600 with
kernel peer-UID checks and bounded client send/receive waits. Shutdown
removes the socket. Direct sysfs writes are low-level driver operations and
do not themselves perform controller/FE cleanup.

The original FPGA mailbox timeout still writes the command and returns
success; the original ASIC outer state setter also loses its helper error.
Native reports ETIMEDOUT without overwriting the busy mailbox, propagates
every IO failure and checks bit16 after the operation. The blocking path
may already have changed ONU state before a later failure, so controller
shutdown is required rather than a success response or speculative rollback.

`scripts/test_fttr_onu_admin.py` executes both original state setters for
18 backend/ID/action combinations and compares register/delay sequences,
with separate serial, every-IO-failure, timeout, lost-write and production
sysfs parsing/locking checks. `test_fttr_autoprovision.py --admin-control`
uses the real daemon/CLI, two ONUs and real VLAN/bridge ports in a private
namespace; modeled sysfs refuses administration unless FE is paused and
the target port and grant are already gone. FPGA1 and ASIC block/resume,
malformed/stale requests, driver recheck failure, IO failure and shutdown
checks pass. Reports: `artifacts/fttr/onu-admin-{original,fpga1,asic}-20261105.json`.
Existing controller, resident Wi-Fi and optical-startup regressions pass;
the OpenWrt package and MiniOLT/BOSA modules compile/link.

The controller records each temporary operation's serial and skips inventory
and provisioning for that slot until release completes or the serial changes.
Driver action2 checks identity and the FPGA registration condition under the
control lock before clearing only bit16. `EAGAIN` leaves the operation pending;
`ESTALE` cancels it without modifying the new ONU. A later block/resume replaces
the outstanding operation, and shutdown cancels all deadlines. Release checks
that the old service/grant/port/Wi-Fi owners are gone; unexpected ownership or
IO failure stops the controller. There is no timer worker in the driver and
no delayed write after process shutdown. `temporary_deactivation` in status
reports pending work. A normal startup/core reset discards this volatile
operation; persistent administration policy remains work.

`scripts/test_fttr_admin_lifecycle.py` executes the production dispatcher at
scan/15s boundaries, including FPGA remaining blocked beyond15s, stale identity,
superseding operations, ownership violations and IO/readback/publish failures.
The extended real-daemon test covers both backends' temporary release, the
healthy peer, replacement, permanent-block override and SIGTERM/restart.
Original/native release writes preserve all non-block bits. Reports:
`artifacts/fttr/onu-temporary-{original,fpga1,asic}-20261105.json`; package and
MiniOLT/BOSA module builds pass. Controller/CLI and FPGA optical-startup
regressions also pass. No physical timing or PLOAM reception has been tested.

This implements original permanent/temporary deactivation and explicit resume,
with custom FE/DBA ownership ordering. Persistent block policy across a core
reset, physical PLOAM acceptance and **full queued
packet retirement before GEM reuse** remain open. This work does not
establish a DMA-prefetch/PE/MiniOLT retirement fence. FPGA2 now follows the
same relay control operations/timing as ASIC; its physical platform stays separate.

### Original upstream-state error recovery

The original ASIC `ONU_Offline` block48914..489ec reads `40803+4*ONU`.
When bits31..30 are both zero and any of bits24..20 is set, it clears mask
`01f00000` with a masked write and calls the deactivation helper479d0.
FPGA polling479bc..47ab0 uses only bits23..20 and writes the observed word
with mask`00f00000` cleared, then calls46c30. FPGA skips the remainder of
that ONU's polling pass after recovery; ASIC proceeds to reread DS state
and can release a non-permanent block in the same scan. These are the exact
observed conditions; broader interpretations of individual error bits are
not assumed.

Native `fttrd` scans for this condition on the original backend cadence
(FPGA100ms, ASIC8s) together with temporary block release. Deadlines start
after the pass finishes, so slow MDIO/recovery IO cannot cause a continuous
catch-up loop that starves OMCI/stop handling. It samples the64-row hardware
table, checks a candidate's serial/state again, withdraws its software/FE/
VLAN/DBA owners, and asks `onu_admin` action3 to recheck identity and the
error under the driver's control lock before clearing and deactivating.
An absent error or already blocked ONU returns EAGAIN without state writes;
a changed serial returns ESTALE. Other errors stop the controller and its
existing TX/FE cleanup. Successful recovery uses the existing temporary
release lifecycle. An administratively permanent block is deliberately not
converted into a temporary block by automatic recovery.

`test_fttr_onu_admin.py` now executes384 original upstream-state combinations,
compares both kernel/userspace predicates and recovery IO, and injects each
native IO failure. Existing state/identity/timeout/readback cases still pass.
`test_fttr_admin_lifecycle.py` covers scan/deadline boundaries, a slow IO pass,
ASIC same-pass versus FPGA next-pass release, stale/no-longer-needed cases,
ownership conflicts and errors. The real daemon's ASIC/FPGA1 namespace tests
verify automatic detection, the healthy peer, port/grant withdrawal before
recovery and permanent-block preservation. Reports:
`artifacts/fttr/onu-upstream-{original,fpga1,asic}-20261106.json`. The native
package and MiniOLT/BOSA modules compile/link. Physical error induction,
latency and optical recovery have not been tested.

### Original BOSA diagnostics

The bound optical driver's read-only `diagnostics` attribute now reads the four
factory-selected BOSA models under the same mutex as calibration and UX3326
maintenance. It requires successful configuration. It does not probe a guessed
SFP address or alter the laser/FE state. `fttrctl <optical-sysfs-directory> optical`
prints the model, temperature in degrees C, voltage in V, bias in mA, and optical
power in uW/dBm as JSON. This command is also usable while the controller runs;
the driver serializes the optical transactions.

The reference is `rtkoltbosa`'s model status output, reached through operation
table offset112, and its diagnostic tails at4057c4,40a7a0,40c914,40e70c:

| Model | Voltage | Bias | TX power | RX power | Temperature |
| --- | --- | --- | --- | --- | --- |
| UX3326 | word30 | word32 | word34 | unavailable | word2e |
| UX3326S | word6f | word71 | word73 | word75 | word6d |
| GN25L99/GN25L81 | word70 | word6a | word6c | unavailable | word66 |

Register numbers are hexadecimal. Reads follow the table order and the existing
external-I2C wire byte order. UX3326S supplies calibrated lower-page words and
requires no diagnostic page writes. Other models reload and validate this
unit's calibration file, including model/address, and use BE16 gain/offset pairs
at offsets24/20/8/16 for voltage/bias/TX/temperature. Original signed32 multiply,
unsigned saturating conversion, 16-bit addition and signed temperature behavior
are preserved. Voltage units are100uV, bias2uA, temperature1/256C. The kernel
retains fractional TX power in1/256 of0.1uW; user space computes
`10*log10(power_in_0.1uW)-40`. It does not use floating point in the kernel.

Measured zero power is reported as0uW with a null dBm value. Unsupported RX
is explicit and both RX numbers are null. Negative calibrated power is ERANGE;
firmware or I2C errors return an error without a partial/fabricated status.
This deliberately avoids the original getters' zero-on-IO-error behavior.
These BOSA functions differ from relay's SFP EEPROM getters and do not establish
SFP dispatch or alarm policy.

`test_fttr_optical_diagnostics.py` executes400 original ARM64 input combinations:
353 valid results match exactly and47 invalid negative-power inputs are rejected.
It additionally checks distinct words, all17 read failure positions, firmware
validation/ownership, driver locking, unsupported models, and the actual CLI's
units, zero/unavailable fields and malformed-input rejection. Four calibration
recipes,514 temperature cases,63 power cases and real Wi-Fi/MIB CLI regressions
pass. OpenWrt package and Linux6.18.55 MiniOLT/BOSA module compilation/linking
pass (the existing external-module MODULE_DESCRIPTION warnings remain).
Reports/log: `artifacts/fttr/optical-diagnostics*-20261106.{json,log}`.
No physical sensor accuracy, SFP compatibility or optical readiness is claimed.

### Original ONU authentication evidence (integration outstanding)

`tools/trace_stock_fttr_auth.py` executes the retained ASIC and FPGA relay
comparators, global LOID predicate, remote LOID acquisition and result writer.
Each backend passes45 comparison cases,8 global result cases,6 acquisition
branches and9 result-write/error cases. Inputs are synthetic and the report
contains no credential bytes (`artifacts/fttr/onu-auth-original-20261106.json`).
This establishes the original behavior. The native global-policy integration
is described below; the five per-ONU methods are not yet integrated.

`plr_ponsrv_auth_check_func_get` selects methods1..5: SN; PLOAM credential with
optional SN binding; SN+PLOAM credential; LOID with optional SN binding; and
LOID+key with optional SN binding. SN compares8 bytes and PLOAM credential10,
including bytes after an embedded NUL. LOID/key use C-string comparison.
Result0 is an exact match,1 requests SN binding (methods2/4/5 when the expected
SN's first byte is zero),2 rejects. Padding bytes do not participate. Retained
local comparator addresses are ASIC92ec8/92f40/92ff8/93098/9314c; FPGA addresses
are selected at runtime by its own exported dispatcher.

Global LOID checking (`plr_ponsrv_pon_loid_auth_check`, ASIC8d6c8) separately
returns output0 when disabled,1 on match,2 on LOID mismatch and3 on key
mismatch. `onuinfo` reads MIB1539/1540, whose configd definitions are
`CTC_FTTR_MASTER_GLOBAL_LOID`/`CTC_FTTR_MASTER_GLOBAL_LOID_PASSWD`. Disassembly
of the startup tails at ASIC419c04 and FPGA4125c4 enables global checking only
when both strings are nonempty. These are separate from the unit's upstream
PON LOID and the FPGA bitstream authentication input.

The registration callback packs PON1-based/hardware ONU0-based IDs, SN8 and
PLOAM credential10. On ASIC, the local serial reader47104 reads only the two
SN words and leaves the caller's zeroed credential buffer unchanged. Native
must not invent a PLOAM credential from the serial/state registers.
The auth task then calls `plr_gponrm_authinfo_get`, which uses the remote OMCI
LOID operation. Original `plr_gponrm_onu_loid_get` first reads class`fffa`,
instance0, mask4000 (24-byte LOID), then2000 (12-byte key). If either fails,
it reads ONU-G class100, instance0, masks0040 then0020. Its fallback contains
false-success paths: both attributes can be absent, or the LOID can be copied
without a key, while the function still returns0. The native implementation
must retain explicit missing/read-failed status rather than treating those
zero/partial outputs as acquired credentials. The result writer sends class
fffa instance0 mask1000 and propagates its return value.

`plr_ponsrv_onu_is_authed` accepts internal states2 and5; state5 is set by
`plr_ponsrv_fttr_loid_auth_fail_set`. The main auth handler at93eb8 distinguishes
this from success, writes the LOID result, reports state5 and still sends
driver state2. The `onuinfo_fpga` status callback at410e28 handles state5 in a
separate path from successful registration. Therefore a state/registration
bit or the helper's name alone cannot authorize native service/FE activation.
Full per-ONU policy selection, SN binding, assignment and callback behavior
remain to be integrated. Current explicit development service policies alone
do not establish ONU authentication.

The same oracle now executes the complete local selection function
ASIC938b4/FPGA907f4, including the real comparator dispatcher and global
predicate with modeled registry access. Each backend passes39 scenarios
(`onu-auth-assignment-original-20261106.json`). Its return values differ from
the comparator:0 accepts,1 waits/inactive,2 unconfigured/pending,3 requests a
different logical ONU slot,4 denotes global LOID rejection. The logical slot
is1-based, while the observed hardware slot is0-based. Exact credentials in
the wrong slot return3; unbound methods2/4/5 bind the SN only in the matching
slot, then return1 for another authentication attempt. Rows in state2 on
another hardware slot are skipped.

An existing state2/5 row on the same hardware slot bypasses its per-row
credential comparison and checks global LOID again. Native must not adopt
that shortcut across serial replacement. Automatic add requires both auto-learn
and auto-find, prefers the observed slot if free, otherwise the first free
logical slot, and returns2 with a separate added flag; adding a configuration
row does not authorize service. Vendor refusal precedes all assignments.
The original vendor-check storage holds four-byte SN prefixes, but the selector
test models its verdict; vendor policy loading and persistent SN/slot assignment
remain native implementation work. These results must not be represented as
completion of the five per-ONU authentication methods.

### Resident ONU serial reservation migration

`drv_gpon_onuid_mismatch_deactive` (ASIC47ec0/FPGA4727c) receives the target
hardware slot, the SN and the source slot. The retained auth callback converts
its1-based logical destination to0-based before calling it. Its local SN helper
(4733c/466bc) writes the vendor word at40801+slot*4, then the serial word at
40800+slot*4. With an unused target the mismatch path writes that reservation,
deactivates the source through its backend-specific state/PLOAM sequence, then
clears the source SN. The extra source deactivation for a prior target OMCC
flag is a software-state branch; native rejects a registered/blocked target.

The controller now exposes this operation through its owned local socket:

```sh
fttrctl /var/run/tyfza1-fttr onu-move SOURCE_ID SOURCE_SN_HEX TARGET_ID TARGET_SN_HEX
```

IDs are0..63; both SN arguments are exact16-hex-digit snapshots, including the
target's current stale reservation or all-zero empty SN. The new version2
message extends the local request; the original20-byte version1 administration
messages remain supported. Only a same-user client can request it, one move
is active at a time, and conflicting administration on its two slots rejects.
It is an explicit operation and does not yet implement automatic five-method
authentication-policy selection or persistent SN binding.

Before moving a configured ONU, the controller verifies that it owns the
remote T-CONT/GEM recipe, withdraws the local service VLAN/DBA with synchronous
FE Hash/cache invalidation, then asynchronously restores its VLAN rules and
releases its remote MEs in dependency order. Current table/object attributes
are read again before release; changed values, rejected operations, false
success and timeouts stop the move. This closes the observed ENOSPC failure
where a moved ONU retained its old Alloc-ID and no free T-CONT remained.
Other ONU transactions continue while remote release waits. Local authenticated
and configured status is withdrawn immediately.

After release, FE is paused again through final two-slot cleanup and hardware
mutation. The new `onu_move` driver operation holds the common control mutex,
rechecks both identities/status, verifies all source/target data grants are
zero, and checks identities again after that potentially long MDIO scan.
Each SN write is read back; a source change after target publication returns
EUCLEAN as a partial mutation and never permits an automatic FE resume.
The old slot uses the existing temporary-deactivation release timing with its
now-empty SN; the destination must actually register, authenticate and build
its service under the new ID. The CLI's success reports only
`reservation_updated:true, registration_verified:false`.

Current-process ownership is required to release a configured service;
restart adoption without its original recipe returns ENODATA before FE/peer
writes. Inventory-only operation also rejects unmanaged GEMs or assigned
T-CONTs. Durable ownership recovery remains incomplete. Automatic
policy-triggered moves and FPGA2 integration are now implemented below. IO/partial cleanup failures stop TX/FE rather
than guessing how to undo hardware-observed state. This operation does not
claim a physical DMA/PE/MiniOLT packet-retirement barrier.

`test_fttr_onu_move.py` compares six original/native backend/slot write
sequences, both identities, occupied/blocked destinations, all data-grant
checks, every IO failure cutoff, readback failure and source replacement after
target publication. Real daemon/CLI tests on both ASIC/FPGA1 use original OMCI
encoders, veth, Linux bridge/VLAN and modeled MiniOLT state: release and move
back, registration/authentication before rebuilding, held deletion with a
healthy peer, VLAN/T-CONT drift, refusal/false success/timeout, target replacement,
shutdown and restart-adoption refusal. Existing management, controller/CLI,
VLAN, FPGA visibility and register regressions pass. FTTR package and
Linux6.18.55 MiniOLT/BOSA module builds pass, with source identity checked.
Local reports/logs: `artifacts/fttr/onu-move-*-20261106.*`; no hardware was used.

### Resident global LOID authentication

The factory importer now reads the two original CTC global fields into a
private runtime file, independently of upstream PON credentials and FPGA
authentication. The exact42-byte format is `TFL1`, LOID[25], key[13], with
NUL termination. Directory mode0700 and file mode0600 protect the input.
Empty/missing fields produce an explicit disabled policy, matching the original
startup predicate; invalid lengths fail import and remove the previous file.
The normal FTTR init requires this file and passes `--loid-auth` to `fttrd`.
Manual invocation without that option retains the explicitly unprotected
development mode. Invalid, exposed, nonregular or duplicate inputs fail before
the daemon takes hardware ownership or writes TX/FE/DBA state.

An authenticated controller first withdraws residual data grants left by a
previous process, after taking exclusive ownership and withdrawing FE/owned
VLANs. It checks all64 ONUs and all seven ASIC data slots, including currently
absent/unconfigured identities, while retaining ASIC management slot0. FPGA1
uses its single physical data slot0. Zero grants cause no write; nonzero grants
must be cleared and read back as zero. In full optical bootstrap this happens
after core/FPGA initialization and before TX enable. The management-only
invocation performs it before FE admission/OMCI. Missing readback, failed writes
or false-success clears stop the controller through normal TX/FE teardown.
FPGA2 uses the same seven data slots and separate management slot as ASIC. This is a local grant barrier;
it does not establish downstream buffer retirement or per-GEM ingress filtering.

Each new registered identity then receives management access, followed by the
asynchronous CTC LOID/key acquisition. Peer rejection or timeout on either CTC
field restarts acquisition through ONU-G; data from the two paths is never
mixed. Malformed frames, local IO and stale identities do not trigger a
capability fallback. Each operation uses the existing three2-second attempts.
Both credentials must be acquired before applying the original1/2/3 predicate.
The daemon writes classfffa result1000, then reads it back; rejected writes,
wrong readback and failed/partial acquisition remain explicit errors. Unlike
the original fallback, no missing or partial credentials produce success.

Only a verified successful result starts the MIB/Wi-Fi/service chain. A failure
leaves management available, records `auth-failed`, and creates no owned data
grant, GEM configuration or Linux service VLAN. Authentication does not disable
the healthy peer's service. Serial replacement, explicit management resume and
same-serial MIB loss cancel the old assertion and authenticate again. A failed
identity is retried on new registration/resume/controller restart, not by an
unbounded auth timer. Runtime policy changes currently require a controller
restart. Shutdown cancels pending authentication and clears authenticated status.

Status reports whether the policy is required, credentials were acquired,
the original result was verified, and primary/fallback errors; it does not
publish credentials. Normalized/raw MIB journals redact CTC/ONU-G credential
attributes, and malformed replies retain at most the noncredential header.
Explicit developer `fttrctl mib/get` output is not covered by that journal
redaction. The global FE gate is unchanged: this binds authentication to native
owned service creation/lifecycle, not a new per-GEM hardware ACL or a complete
queued-packet retirement guarantee.

`test_fttr_auth.py` compares native requests with original master encoders and
both global predicates, including fallback, partial/error cases and private
factory inputs. Result readback executes the retained slave GET worker and its
actual `mib_LoidAuth` metadata (modeled result storage). Both ASIC/FPGA1 real
daemon/veth/bridge/VLAN suites pass with authentication and Wi-Fi enabled,
including held requests, failures, stale replies, MIB loss, shutdown, disabled
policy and zero physical data-grant slots before acceptance. Existing resident
management and Wi-Fi regression suites pass. GCC14.4/musl FTTR and factory
packages compile/install, and installed sources/init/importer match this tree.
Reports/logs are `artifacts/fttr/onu-auth-*-20261106.*`. These are offline
protocol/lifecycle checks, not physical authentication or traffic acceptance.
The additional `onu-auth-restart-*` suites reproduce the prior residual-grant
failure, verify both backend service/Wi-Fi lifecycles, absent/unconfigured slots,
unreadable/false-success withdrawal and the full bootstrap ordering/replacement.
The final FTTR package build includes this grant barrier.

### Resident per-ONU authentication and assignment

`fttrd --onu-auth PRIVATE_JSON` enables the original logical ONU selector before
Wi-Fi/service provisioning. The startup script adds this option when
`/etc/tyfza1-fttr.onus.json` exists. No default per-ONU credentials or permissive
policy are installed. This is independent of `--loid-auth`: when both are
configured, a per-ONU match must also pass the global check/result readback.
All three verified backends use their own data-grant layout. Either
authentication policy triggers withdrawal/readback of residual
data grants before admission, including grants for absent/unconfigured ONUs.

The version1 JSON has exactly these fields (all required):

```json
{
  "version": 1,
  "revision": "v1",
  "state_directory": "/etc/tyfza1-fttr-onu-state",
  "auto_learn": false,
  "auto_find": false,
  "vendors": ["54455354"],
  "onus": [
    {
      "id": 6,
      "method": 1,
      "active": true,
      "serial": "5445535401020304",
      "ploam": "00000000000000000000",
      "loid": "",
      "key": ""
    }
  ]
}
```

The example contains a synthetic SN and configures **logical ID6 / hardware
slot5**. Logical row IDs are1..64, while runtime/CLI hardware IDs remain0..63.
Methods1..5 compare SN, PLOAM, SN+PLOAM, LOID, LOID+key respectively. SN is8 bytes
and PLOAM10 bytes expressed as16/20 hex digits, including bytes after embedded
NUL. LOID/key are strings up to24/12 bytes. Methods2/4/5 with configured SN first
byte zero require a durable SN binding before a later authentication pass;
method3 never binds implicitly. Inactive exact rows cannot create services.
`vendors` is an allow-list of up to32 four-byte SN prefixes as8 hex digits;
an empty list allows all prefixes, following the original empty vendor list.
Unknown fields, duplicate row IDs, invalid types/widths and unsupported versions
are rejected. JSON-c parsing does not detect duplicate JSON property names.

The complete five comparators and selector branches are implemented, but
**PLOAM acquisition is still unproven**: the original serial-registration helper
only reads the two SN words, and the OMCI auth-info helper supplies LOID/key.
Native methods2/3 therefore return `ENODATA` rather than treating an unfilled
buffer as a valid all-zero credential. Missing required inputs also prevent
auto-learning from bypassing a configured credential rule. LOID reads use the
original CTC then ONU-G fallback and require complete acquisition. An independent
SN match can proceed without those MEs when global LOID is disabled; malformed
or identity/transport errors are not converted into a capability fallback.

`auto_learn` **and** `auto_find` must both be true to create a new method1 SN row.
The current logical row is preferred if free, otherwise the first free row.
Binding/learning uses a single two-second retry after durable persistence; it
does not immediately declare authentication. No runtime authenticated state is
persisted. New registrations, same-SN MIB loss, restart and management resume
must authenticate again. Other rejects wait for registration/resume/restart.

The private JSON must be a regular file owned by the daemon UID with no group/
other permissions. Create the state directory as the same UID, mode0700; it must
already exist. The daemon takes an exclusive directory lock and writes
`REVISION.json` with the exact policy and64 SN binding slots, mode0600, using
file fsync, rename and directory fsync. The same revision with a changed policy
returns `ESTALE`. Choose a new revision deliberately when replacing a policy;
that starts new bindings. Bind failures stop the controller because a failed
fsync can leave uncertain persistence. The config and default state directory
are package conffiles; other custom state locations need their own backup.
This has not been tested against physical flash power loss.

A match in a different slot performs MIB inventory without creating Wi-Fi,
GEMs, DBA or VLANs, then enters the existing serial reservation migration path.
Unowned remote GEM/IW objects or assigned T-CONTs refuse migration; no unknown
remote resources are deleted. The source/target hardware identities and the
current target policy binding are checked again before mutation. The target
must externally register and authenticate before service creation. A new target
binding during the source's inventory cancels the older selection. A full
registration snapshot retires every departed/replaced authenticated identity
before any new selection: the real daemon test reproduced a lower slot
auto-learning a SN while its departed higher slot still looked authenticated;
the corrected two-pass refresh preserves the original reservation. Automatic
assignment uses the same FE pause, native port/grant withdrawal and driver
identity guards as explicit migration; it does not establish full retirement
of packets cached in DMA/AFT/PE/MiniOLT.

Status separates `onu_authenticated` from verified global
`loid_authenticated`. `onu_auth_logical_id` is1-based (zero means none);
`onu_auth_method` and `onu_auth_error` describe the policy decision.
`onu_auth_action` enumerates0 match,1 inactive/wait,2 reject,3 move,4 bind,5 learn.
Credentials are absent from status and MIB journals, and terminal authentication
jobs clear their credential/reply buffers.

`test_fttr_onu_policy.py` checks240 comparisons and96 selection cases against
both original ARM64 implementations, plus private parsing, revision locking,
persistence/restart and failed-write behavior. The real daemon/veth tests cover
both ASIC/FPGA1 with Wi-Fi, binding/learning retries, wrong-slot migration,
unavailable/partial credentials, global result rejection, stale response/SN
replacement, target binding during inventory, unowned GEM rejection, failed
persistence and shutdown. Global authentication and explicit migration regressions
also pass. The snapshot regression passes on both backends; GCC14.4/musl package
compilation/install passes; reports are
`artifacts/fttr/onu-policy-*-20261106.*`. No physical ONU/authentication or queued
packet-retirement acceptance is implied.

### Original default service and shared-bridge selection

`tools/trace_stock_fttr_default.py` follows the actual `onuinfo` operation table
at430bd8: `fpga_api_onu_init` selects `ctc_ponrelay_onu_init` at41243c.
This executable serves FPGA2 and ASIC. Executing that producer for ONU IDs1/6/63
and configured slot counts2/3/8 establishes the following recipe:

* The default count2 creates logical data T-CONT1 with profile name `FTTR-100M`,
  GEM1 referencing T-CONT1, and remote flow1 referencing GEM1.
* Counts3..8 add T-CONT/GEM/flow ordinals2..7. Each data flow, including flow1,
  gets a VLAN filter `100 * ordinal` in this branch.
* Successful setup then calls `plr_api_remote_onu_mcast_enable` with its actual
  type2/instance1 record. This is part of the original default recipe; the
  current native unicast service must not be described as reproducing it.
* A failed operation stops subsequent configuration, reads the ONU record and
  deletes/re-adds that ONU. It returns the initiating error and does not check
  those recovery results. Native cleanup must preserve separate recovery errors
  and its existing ownership checks instead of treating re-add as confirmation.

Executing the relay flow API adapters through `plr_gponrm_flow_op_api` shows
that GEM assignment uses mask2 and VLAN filtering uses mask4, with no UNI-binding
bit. The original new-flow selector (`aac90` through`ab024`) selects shared-bridge
mode1 for the first such flow and calls `plr_gponrm_dft_mbs_me_id_get`; adding it
to an existing per-UNI bridge returns35. An explicit UNI-binding mask instead
selects the Ethernet UNI bridge. Thus mapping the default to a wildcard native
policy for Ethernet ordinal0 would be an unsupported change in topology.

The original `plr_gponrm_cfg_uni_bp_autocfg` (`b68c4`) visits Ethernet and
VEIP through separate helpers (`b6310`, `b65e8`). Executing them with different
configured/reported card and UNI counts confirms that each uses the smaller
count and attaches every selected UNI to the shared bridge. It does not discard
the Ethernet UNIs merely because a VEIP exists. Three mixed/missing-card cases
exercise these enumeration limits; counts and MIB boundaries are modeled.

Nine producer recipes,33 first-failure/recovery paths,12 API translations,
four bridge-selection branches and these UNI enumerations execute successfully
in the offline oracle.
The report is `artifacts/fttr/default-service-original-20261106.json`. Remote
configuration/MIB storage, configuration records and semaphore operations are
modeled; the bridge selector stops before GEM inventory. Native shared-bridge
attachment is implemented below. Multicast, factory service restoration and
multiple services per ONU remain work.

### Shared-bridge Ethernet and VEIP service transactions

The explicit service policy now accepts `shared` in the kind field and requires
ordinal0. It attaches every class11 Ethernet UNI and class329 VEIP in the actual
MIB to bridge8080, sharing one selected T-CONT/GEM/ANI. Ethernet and VEIP both
remain present; missing inventory is an error. This explicit policy does not
yet import the original configured card/UNI count limits, multicast enable or
multi-service default recipe. The installed policy file still has no active rows.

For example, `* shared 0 1 3 200 50 100 200 br-fttr` selects access VLAN200 on
all reported UNIs. `none` retains the original transparent mode4. The CLI takes
`service 7 shared 0 1 3 none 50 100 200 fttr0` after its MiniOLT directory.
Backend-specific Alloc-ID, GEM and physical DBA slot mappings are unchanged.

Entity IDs and bridge port numbers are reserved for the whole service before
IO. Existing matching bridge ports can be reused; a selected UNI on another
bridge, duplicate UNI association/port number or a missing parent bridge
rejects before writes. Core and every UNI object group finish their read-only
preflight before the first Create/Set. Each UNI owns its attachment, admin
snapshot and independent EVTO table transaction. A later failure restores
earlier VLAN tables, restores the prior local grant, then releases owned UNI
objects in reverse order before the shared core. Failed cleanup retains the
dependent resources and separate initiating/cleanup errors.

The resident controller exposes one per-GEM Linux VLAN through the existing
NI/P6/FE lifecycle only after all UNI transactions and DBA readback succeed.
Explicit SN migration first withdraws local FE/VLAN/DBA, restores all owned UNI
state and releases the common GEM/T-CONT before mutating reservations. Runtime
journals include each UNI's desired object recipe, ownership, prior admin value
and VLAN tables. These records are not yet durable restart ownership recovery;
adopted resources still reject migration without the original owner.

`test_fttr_service.py` passes original wire/schema comparisons for mixed
three-Ethernet/one-VEIP services, transparent/access rules, read-only repeated
adoption, all-UNI preflight, inconsistent ownership, middle/last UNI failures,
preservation of preexisting tables and cleanup failure dependency retention.
Actual daemon/CLI migration passes with ASIC9690/FPGA1/FPGA2 in private veth/bridge
namespaces, including authentication, Wi-Fi, peer isolation, drift, timeout,
identity replacement and restart adoption. Existing GEM/association/Wi-Fi,
per-ONU policy and controller regressions pass. GCC14.4/musl package compilation
and staging installation pass. Reports are
`artifacts/fttr/shared-*-20261106.json`. No optical traffic or physical packet
retirement is exercised by these modeled MiniOLT/ONU peers.

### Original default multicast objects and transport boundary

`tools/trace_stock_fttr_multicast.py` executes the actual default multicast API,
configuration state machine, object producers and baseline wire encoders.
The pool initializer `plr_gponrm_cfg_card_uni_mul_init` supplies the original
IGMP defaults; only reported UNI records, configuration storage, allocation
and OMCI responses are modeled. The default API selects card0/UNI0 of VEIP
when any VEIP card is reported, otherwise card0/UNI0 of Ethernet. This differs
from the shared unicast bridge's enumeration of both classes.

The ordinary path creates class309 Multicast Operations Profile, sets its
downstream tag control, creates class310 Subscriber Config and class311
Subscriber Monitor at the selected UNI bridge-port entity. The
`g_plrGponrmCtcDebug` branch uses class65531 Extended Multicast Operations
Profile instead, followed by its own required Set. This is an actual global
selector, not evidence that every peer supports the extended class. The first
shared-bridge multicast user also creates a class47 port with TP type6 and
pointer4095 plus class84 filter; later shared users retain that common port.

The separate `plr_gponrm_cfg_gem_dft` path creates downstream-only class268
GEM CTP4095, class281 Multicast GEM IW4095 referencing GAL1, then installs the
IPv4 range224.0.1.0 through239.255.255.255. The initialization callsite at
`a7c7c` follows default bridge setup. Full startup ordering remains outside this
focused execution. The default API does not allocate another unicast T-CONT
or its local DBA for these downstream multicast objects.

Failure execution identifies original behavior that native ownership handling
must correct: the ordinary class309 downstream-tag Set failure is overwritten
by the next Create result; early extended-profile failure tears down class309
despite having created class65531; cleanup results are ignored; and
`plr_gponrm_cfg_gem_dft` returns success for errors other than13, including
failed GEM Create/Set. Native implementation must read back requested values,
delete only objects it owns and preserve separate cleanup failures.

The retained `onu_setup.sh` creates a dedicated `.mc` SMUX interface when
`EXTERNAL_SWITCH_HANDLE_IGMP_SINGLE_NETDEV` is enabled. The retained hardware
configuration has this value1, TPID0x8100 and IGMP VID4095. The script pushes
this outer tag, filters duplicate multicast on individual external interfaces
and applies its multicast/source-MAC loop rules. This is not an ordinary
per-ONU service VLAN. Native Linux `net/8021q/vlan_netlink.c` rejects VID4095;
an isolated veth/rtnetlink check accepts4094 and returns `Invalid VLAN id` for
4095. A native multicast endpoint therefore needs explicit P6/NI forwarding
and lifecycle integration, including shared ownership across ONUs, before
remote multicast provisioning can constitute a complete working feature.

Eighteen selection/profile/reference cases,24 operation/cleanup failures,
seven direct multicast-GEM cases and six default-GEM wrapper cases pass;
reports are `artifacts/fttr/multicast-original-20261106.json` and
`multicast-vlan-boundary-20261106.json`. This is original implementation
evidence. Native multicast provisioning/forwarding and hardware acceptance
remain incomplete.

### Native multicast P6 transport endpoint

The NI driver now registers `fttr-mc` as an initially down upper of `fttr0`.
It implements the dedicated transport required by the original VID4095 SMUX
path without treating the reserved VID as a Linux VLAN interface. Sending
adds TPID0x8100/VID4095 outside up to two customer tags and submits through
the parent P6 qdisc/DMA path, retaining the original DEEPQ destination6/header.
No second DMA owner, ring or hardware port is allocated. Hardware replication
and remote multicast provisioning are not implemented by this transport step;
the endpoint is not registered as a unicast FE port.
Its TX lock has a separate lockdep class from the parent, matching the stacked
submission order.

When attached to the same live bridge as an owned ONU service VLAN, the endpoint
receives IPv4 IGMP and IPv6 MLD131/143 from that service. The outer GEM tag is
removed while customer tags survive. IPv6 extension-header traversal is bounded;
truncated headers, non-first fragments, unknown GEMs and foreign/down/blocked
service ports are not redirected. The original local/unicast/broadcast/IEEE
multicast rules and per-ONU duplicate IPv4/IPv6 multicast drops apply while the
multicast upper is running. The dedicated `rtl9617c_fttr_ingress` skb bit replaces
the original `EXT_SWITCH_EN` provenance for source-MAC loop handling; it remains
in the copied skb header group, so bridge/routing changes to `skb_iif` do not
erase the origin. This bit is not authentication or a GEM-reuse epoch.

`fttr_forward` closes the multicast producer and purges its qdisc before the
shared CPU RX/P6 qdisc/FE withdrawal. Successful FE admission is required to
restore the producer. Parent reconfiguration, close and FPGA loading close it;
the carrier follows actual P6 state and never brings an administratively down
multicast upper online. Closing the multicast upper retires shared-NAPI
reports and queued parent submissions; parent close also purges retained RX.
Unregister stops the link worker, removes the RCU RX endpoint, waits for held
RX and retires the upper before freeing the parent. New opens reject removal.
DMA-prefetched and downstream packets are still outside this software purge.

`test_fttr_mcast.py` executes the native packet/gate callbacks and actual Linux
`skb_vlan_pop/push` control flow under ASan/UBSan, with modeled buffer storage,
topology, qdisc and delivery. It covers in-band/metadata tags, IGMP/MLD, IPv6
extensions, malformed packets, preserved customer tags, VID4095 wire bytes,
return-source handling, duplicate filtering and closed-upper rejection.
Existing forward/TX gates, multicast-aware GRO purge, original CPU-DEEPQ,
P6 queue setup, shared FE/bridge learning and768 receive-poll cases pass.
Linux6.18.55 Image/modules compile with the new skb field and multicast object;
the edited patch hunks reverse cleanly against the build tree. Reports/logs
are under `artifacts/fttr/multicast-*`. These are offline checks; no physical
device or optical link was touched.

The controller now opens/enslaves `fttr-mc` after the admission checks described
below. Remote GEM4095 and the default multicast profile are implemented.
Verified ownership across restart, additional profile/ACL policy, hardware
replication and packet retirement still require further work.

The subsequent original ONU table check also exposes a readback incompatibility:
`MultiGemIwTpDrvCfg` builds its class281 IPv4 Get snapshot with a declared
length and allocation of24 bytes per entry at`52ec..5404`, while
`5448..547c` writes contiguous12-byte entries. Executing the real snapshot and
`McastAddrEntry_to_network` for zero/one/two rows confirms that the unused half
retains the allocation fixture's poison bytes. `--table-mib` on
`trace_stock_fttr_multicast.py` records this separately from the original master
producer. This is not evidence that every subordinate has this defect, and
uninitialized bytes must never be accepted as real rows. Remote table
verification and restart adoption must account for this concrete behavior
before enabling a complete default multicast service.

### Remote default multicast transaction

`omci-mcast.c` reproduces the actual standard/CTC producers: downstream-only
GEM268/4095, IW281/4095, profile309 or65531, subscriber310, monitor311,
shared-bridge port47 with TP type6 and pointer4095, and filter84. It sets the
original12-byte IPv4 row for224.0.1.0..239.255.255.255. The service selects the
first reported VEIP, otherwise Ethernet, and reserves its extra bridge port
before any writes. Core objects, every UNI and multicast resources pass live
read-only preflight before service application begins.

CLI/service-policy kinds `shared-mcast` and `shared-ctc` add this remote recipe
to `shared`; the existing ten fields and other kinds retain their meaning.
With a bridge these kinds also admit the local multicast transport after the
controller checks the full current ONU set. A bridge of `-` provisions remote
objects only. The service transaction becomes ready after every multicast object
and table readback passes. Failures restore the multicast row/objects first,
then prior UNI VLAN/admin, DBA and core resources. Explicit migration withdraws
local forwarding and DBA, verifies all multicast object groups and the live
table before any deletion, then releases multicast before shared dependencies.
Adopted resources remain unowned; a cleanup failure retains dependent objects.

Normal12-byte matching tables can be adopted without writes. Existing24-byte
snapshots are ambiguous and return `ENOTRECOVERABLE`. Only a newly owned IW,
a verified empty table and our one sent row permit the original doubled-length
interpretation; only its initialized12 bytes are compared. This also applies
when the Set took effect but its response was lost. Cancellation immediately
after transport send retains uncertain Create/Set ownership in the journal.
The original ONU's Set handler inserts a zero-address row when a delete key is
absent. Rollback therefore sends no delete after proving an empty table, and
row deletion opts out of the ME transport's usual retransmission: timeout
resolves through a fresh table read. The executed original add/delete/lost-reply
retry sequence produces row counts1,0,1, confirming that this callback is not
idempotent. This does not assume that every ONU lacks a transaction cache.

`test_fttr_mcast_omci.py` compares every Create/Set byte against executed original
producers and covers each rejected write, each applied-write timeout, deferred
preflight, table adoption, false-success Set, cancellation and object/table
drift before release. `test_fttr_service.py` verifies full shared service and
rollback over AF_PACKET. `test_fttr_autoprovision.py --shared --multicast
standard|ctc --move-control ...` verifies resident authentication, journaling,
migration and failed cleanup, including preserving a healthy peer. ASIC9690,
FPGA1 and FPGA2 cases pass, as does the target OpenWrt package build. Reports
are `artifacts/fttr/mcast-omci-20261106.json`, `service-mcast-20261106.json`,
`resident-mcast-*-20261106.json` and `package-mcast-20261106.log`.

Withdrawing an ONU's unicast grant does not establish that it cannot receive
shared downstream GEM4095. Per-ONU GEM encryption in `drv_set_onu_gemport_encrypted` uses
the unicast GEM numbering and does not prove shared multicast isolation.
Executing original `MultiGemIwTpDrvCfg` with failing hardware ACL callbacks
also shows that both add and delete still change the MIB list and can return
success. `trace_stock_fttr_multicast.py --table-mib ...` now records these five
mutation cases alongside the three snapshot cases. OMCI table readback proves
MIB state, not successful subordinate hardware ACL programming.
These offline results do not claim optical reception, hardware multicast
replication, queued-packet retirement or complete FTTR acceptance.

### Resident multicast admission and native bridge lifecycle

`fttrd` manages the kernel-created `fttr-mc` through rtnetlink. It verifies its
parent and dedicated group0x46544d43, applies its ownership alias and bridge MTU,
enslaves it while down, disables bridge source learning, then opens and reads
back the configuration. IGMP/MLD reports retain ONU source MACs; learning those
MACs on the shared port would displace the correct unicast service FDB entries.
Close stops the upper (invoking its existing NAPI/qdisc retirement), detaches
it and verifies down/unmastered state without deleting the driver-owned device.
Startup resets an owned leftover upper even when no new multicast policy is
selected. Foreign parent/group/alias is an explicit error.

All multicast policies must name the same bridge; conflicting policy rejects
before hardware ownership. After remote configuration and sync verification,
admission checks every currently registered ONU. Each must satisfy the selected
authentication policy, current serial and MIB sync. Selected members additionally
need the matching bridge, configured remote multicast and live unicast service.
Other ONUs require live class268/281 reserved-instance absence; the inventory
also rejects foreign alternate CTP instances using Port-ID4095 or missing that
attribute. An unselected ONU retaining GEM4095 prevents shared admission without
inventing deletion authority. A fresh full hardware snapshot is checked again
before opening. Migration, observed identity loss, sync failure and FE withdrawal
close multicast before changing service ownership. Healthy unicast peers remain
operational while shared multicast is closed.

The existing two-second sync loop now checks every multicast object group and
the initialized IPv4 row, including when ONU Data stays zero. Nonmembers repeat
the two live absence probes; admission changes only after both complete, while
discovering a present GEM invalidates absence immediately. Netlink port/bridge/
MTU/learning checks run at the registration scan cadence rather than on every
OMCI reply. Runtime status reports shared admission, member count and reason;
sync journals record multicast progress and proven absence. Members use service
group0x46544d55, preserving the original group for ordinary unicast. Kernel
report redirection and duplicate multicast suppression require the member group
and same up/forwarding bridge, so unselected unicast ports keep their traffic.

`test_fttr_autoprovision.py --shared --multicast standard|ctc --auth --wifi
--move-control ...` now verifies actual Linux upper/bridge admission (macvlan
models the driver's netdev), held/failed authentication, two members, migration,
zero-sync multicast drift, recovery, restart with unselected retained GEMs,
cross-bridge refusal and shutdown. ASIC9690/FPGA1/FPGA2 cases and existing
unicast migration pass. `test_fttr_datapath.py` verifies native rtnetlink lifecycle,
foreign ownership, disabled source learning and drift detection. Native packet
callbacks with Linux VLAN helpers remain covered by `test_fttr_mcast.py` under
ASan/UBSan. Reports use `local-mcast-*` and `mcast-netlink-20261106.json`; target
Image/modules and package logs use `kernel-local-mcast-20261106.log` and
`package-local-mcast-20261106.log`.

This admission follows observed registrations and MIB state. It does not prove
instantaneous isolation during the interval before a new registration is
observed, removal of already queued DMA/AFT/PE/MiniOLT packets, or successful
subordinate hardware ACL changes when its original callback hides errors.
GEM4095 transmits through the native shared DMA/P6 CPU path. Explicit complete
TC multicast rules can now use the native NI/arbitration owner described below;
the bridge does not yet produce multicast hardware rules automatically. These
limits remain part of the full FTTR goal and physical acceptance is deferred.

### Original multicast hardware table boundary

`trace_stock_fttr_mcast_hw.py` executes original `ca-ne.ko` functions for NI
lookup and L2 arbitration multicast FIB with explicit MMIO/indirect completion.
`aal_ni_mc_lkup_set/get` validate indices0..1023 but insert only the low8 bits
into command register0xaa64. The executed indices0/256/768 and255/511/1023
therefore alias. The writer sends a64-bit value low/high to0xaa6c/0xaa68,
then issues the write command. A permanently busy NI engine returns5 after5002
reads. Do not allocate a supposedly independent1024-entry native NI table
based only on the original parameter validation.
The original `ni_mce_indx_table` descriptor actually limits its allocator to256
eight-byte entries, so this is an unsafe direct-setter boundary rather than
evidence that ordinary factory allocation overwrites other groups. The distinct
`swMcgid` namespace contains1024 fourteen-byte software records. The
`aal_port_forward_action_add` path builds a64-bit bitmap of arbitration-action
indices; `aal_hw_mcgid_new` allocates a separate NI bitmap entry and retains
current/previous hardware indices in that software record. The NI bitmap must
therefore not be interpreted as a simple physical-port bitmap.

`aal_arb_mc_fib_set` uses64 indices and three32-bit words at0x1640/0x163c/0x1638
with command0x1634. All27 selected fields were independently exercised against
nonzero prior words, confirming their actual packed bit positions and preservation
of other fields. Both a read-engine timeout and a write-engine timeout are
logged after10000 polls each, but the original setter continues and returns0.
The native multicast implementation must propagate those failures and retain
ownership until withdrawal is verified. Report:
`artifacts/fttr/mcast-hardware-original-20261106.json`. This does not establish
bitmap destination semantics or a working FE/PE replication recipe; the
software allocation/caller/action chain is additionally exercised below. No
device IO was performed.

The same check executes `_aal_arb_mc_fib_action_add` for16 destination/tag/source
rewrite recipes. A no-edit destination uses words `[0, 0x40000000 | ldpid<<24,
0]`; outer4095 with TPID8100 adds `1 | 4095<<2 | 1<<14` to word1. The source
MAC input is byte-reversed before the8-entry my-MAC allocator. Its source-swap
recipe also enables `ip_ttl_dec_cmd`; the bridge implementation must not inherit
that routed edit accidentally. The generic action describes VLAN command1 as
"tag by table", so packed bits alone do not prove push versus replacement for
an already tagged packet.

Executing group add/publish/change/free confirms action-index bitmaps and
reference sharing between old/new NI entries. Original free drops action
references before the NI entry, ignores a failed NI deletion, clears software
ownership and returns0. Successful port-action deletion also has an unmatched
lock callback; the lock primitive itself is modeled, not executed. Native
ownership must survive failed withdrawal and must not reuse an index while an
old packet may still carry it.

With `--fc` pointing to the original `fc_8277c.ko`, the tool executes
`rtk_fc_syncToHw_G3addGroupFwdHash` and `rtk_fc_syncToHw_G3updateGroupHash`,
then the actual `convert_act_flow_nomal_mode`. Both produce MC1/MCGID=NI index,
DPID-valid/priority/permit1 and DEEPQ0 before replication for indices1/63/255.
Both wrappers return0 even when their Hash callee returns5. These are different
from the PON GEM action that reuses MC/MCGID with DEEPQ1 and T-CONT fields.
This establishes the replication-entry encoding, not physical replication or
the downstream arbitration/deep-buffer route. Those routes and native table
ownership remain required before enabling multicast offload.

`rtl9617c-fe-mcast.h` now provides the native NI/arbitration table transactions
through the existing `rtl_dq_io` boundary. NI writes reject0 and indices256+;
arbitration writes reject64+. Both check an already-busy engine before touching
DATA registers, propagate completion errors, and require an exact readback.
An applied write with a lost completion is still an error, and no subsequent
transaction overwrites its outstanding command. These helpers do not allocate
or release indices and are not yet called by a live multicast offload path.
`test_fe_mcast_table.c` exercises all usable indices plus pre-existing busy,
both write/readback timeout stages, late completion and corrupt readback under
ASan/UBSan. Clearing a bitmap is explicitly not a packet-retirement proof.

The existing FE single-output parser also now rejects every action after a
redirect. Previously it could silently retain only the last redirect or apply
a later VLAN edit before the first redirect. The regression fails against the
previous production parser and passes with the fix; the1028 NAT/IPv6,288 VLAN,
8 PPPoE,24 bridge and10 P6 vectors,30 original bridge action comparisons, and
Image/modules build pass (`mcast-table-build-20261106.log`). Proper multi-output
offload still requires one complete replica set, rather than accepting such
rules through the single-output path.

The oracle also executes `aal_arb_init` and the FC
`rtk_8277c_asic_l2fe_init.isra.0` overrides, using the actual NI setters for
port-deep-buffer and destination-map writes. With the documented reset seed,
ARB_CTRL becomes `0x89c71c82`: original multicast source retention is enabled,
flow-ID deep-buffer selection is used, and HeaderA deep-buffer gating is off.
All8 port-deep-buffer rows are initially cleared; FC subsequently writes only
row7, matching source17 with no destination restriction (`0x2301`). P6 maps to
PDPID6 when dbuf=0 and PDPID8/QM when dbuf=1 for both my-MAC cases. No P6
destination match is added by these actual initializers. Therefore the
factory `CFG_ID_RTK_PHY_PORT_TO_DEEP_Q[6]=1` flag, used by ordinary Hash/CPU
producers, is not by itself a proof that multicast replicas traverse P6's
deep queue. Native multicast integration must own and verify that separate
arbitration selection rather than infer it from the unicast configuration.

The actual `rtk_fc_abstrSwSyncToHw_add` member loop and
`rtk_fc_parseAbstrSwActionField`, followed by the NI arbitration producer, now
confirm the replica VLAN contract: each present S/C tag uses command1 with
explicit VID/PCP/DEI/TPID, and each absent tag uses command2. FC sets
source-port-filter-disable for every selected recipient. With no source-MAC
replacement it leaves TTL decrement clear. This is a final tag-stack recipe;
it is not appropriate to interpret command1 alone as a relative VLAN push.

`rtl_fe_mc_replica_encode` implements that contract for Ethernet/P6 with zero
to two final tags. It reads the actual **L2** TPID registers0x1704..0x1710:
outer tags can use A..D, inner tags C/D only. The native L3 parser TPID table
does not establish those L2 selectors. Unsupported ports, tag counts or
missing TPIDs return an error without publishing output words. Six complete
FC/NI recipes match the native encoder, including GEM4095 plus inner customer
VID100 and PCP/DEI; each L2 selector and missing TPID is also checked by
`test_fe_mcast_replica.py` (`mcast-replica-20261106.json`). The encoder is now used
by the native group owner below; it does not establish packet retirement.

### Native multicast group ownership and explicit TC integration

The existing FE flower backend now accepts complete Ethernet multicast output
sequences: zero or more mirrors followed by a terminal redirect, with VLAN
push/pop edits captured at each output. It uses the original MC1/NI-group/
DEEPQ0 Hash recipe, the256-entry NI bitmap table (entry0 reserved), and the64
immutable arbitration actions. Each action holds its complete final S/C tag
stack. The common L3 editor preserves MACs, TTL/Hop Limit, VLANs and COS; it
does not apply the terminal recipient's tag edits to the other replicas.

Current ingress is native LAN0..4. Outputs are native LAN0..4 or the admitted
`fttr-mc` upper. The latter resolves to P6 with an added outer0x8100/VID4095,
and at most one customer tag. A raw P6 redirect cannot substitute for this
upper. Exact source/destination MAC and EtherType are required. Unknown
retained PCP/DEI, mixed exact/wildcard tag masks, mirror-only sequences,
duplicate identical replicas, input reflection, broadcast/IEEE-reserved MACs,
PON and radio recipients are explicitly rejected. Fully matched PCP/DEI on
one or two input tags uses existing exact bridge profile59 and is preserved.
These explicit rules do not represent automatic bridge/MDB membership.

`rtl9617c-fe-mcast-owner.h` keeps action references before any writes, stages
every recipient before publishing its NI bitmap, verifies the entire group,
then marks it referenced **before** publishing the Hash. A failed write,
including lost completion after application, retains enough ownership to retry.
Withdrawal invalidates Hash age/cache before clearing and reading back NI;
action references are released only after that succeeds. Identical immutable
actions are shared. Inherited nonempty tables are rejected without clearing
them or claiming their ownership.

`rtl9617c-fe-mcast-route.h` separately snapshots the logical27/PDPID11 connector,
its scheduler and destination maps, L2 editor configuration, and P6 destination
deep-buffer rule. Both my-MAC mappings are checked; final P6 enters the existing
PDPID8/QM port14 path, preserving the private/shared buffer pools. The owner
does not overwrite occupied DBUF rows, including original FC's source17 rule.
Conflicting egress classifier/VLAN lookup/marking policy rejects an output.
The MCE return source27/nondeep/bypass and original source-to-POL copy must
already match; foreign flush state rejects admission. The route enables the
original keep-source control, removes HeaderA DBUF gating, and applies MCE
FIFO threshold23, preserving and restoring other fields. If L2 multicast port
membership checking is already enabled, the source/connector bitmaps must
permit the complete path; no inherited isolation policy is widened or disabled.

The original `aal_ni_init` is now executed through its multicast stage with
three seeded MCE controls. It sets only threshold bits0..9 to23 and calls the
NI clear API1024 times, which aliases the8-bit hardware table. Native ownership
does not repeat that clearing. The original arbitration initializer confirms
keep-source, HeaderA gating and multicast membership-enable controls; original
membership, egress and port-DBUF setters verify the native table encodings.
This proves register production, not physical packet scheduling.

Following actual `rtk_fc_mc_delKnownGroupEntry.part.0` through
`_rtk_fc_mc_groupToHwAsUnknown` and `rtk_8277c_flow_mcast_group_asunknown`
adds six last-reference checks. The observed lower boundary is `aal_hash_delete`;
there is no separate NI/MCE/PE retirement operation in these wrappers. On its
failure, the Hash software owner remains set, but the caller still decrements
the group reference to zero and clears the known-group flag. A second call
does not retry. Native group withdrawal therefore retains its cookie/NI/action
ownership on Hash/cache failure; original software unlearn is not treated as
proof of hardware retirement.

Each flow retains every replica's port owner, so removal/FTTR withdrawal also
finds P6 when it is only a mirror. Link, topology, address and MTU events
withdraw multicast groups. Closing `fttr-mc` synchronously attempts FE cleanup.
Because Linux ignores `ndo_stop` errors, `fttrd` additionally writes
`fttr_mcast_flush=1` after closing the upper and during startup takeover; the
kernel refuses an open upper and propagates unfinished Hash/NI cleanup errors.
Remote multicast release cannot treat an unacknowledged close as success.

`test_fe_mcast_table.c` exercises complete ownership, sharing, inherited state,
capacity, applied-write timeouts and every indirect route/restore fault under
ASan/UBSan. `test_fe_mcast_integration.py` runs production install/delete against
the same native owner, including P6-as-mirror admission, common editor
preservation, cache failure and NI-clear retry. Parser checks cover all16
PCP/DEI combinations on one/two tags. Existing CPU packet/gate, shared LAN/P6,
bridge learner and callback/flow release tests pass. Actual resident
controller/CLI/veth/bridge tests with shared multicast, authentication, Wi-Fi
and migration pass on the three modeled backends (`mcast-fe-resident-*-20261106.json`).
The original comparison is `mcast-hardware-original-20261106.json`, the latest
Image/modules build is `mcast-owner-kernel-20261106.log`, and the package build
is `mcast-integration-package-20261106.log`.

**Published NI indices remain quarantined for this FE owner's lifetime.**
Exposed action words remain immutable, although identical recipes can be shared.
Consequently this implementation allows at most255 group publications and64
distinct exposed action recipes before returning capacity errors. Unpublished
aborts can reuse their entries. Re-enabling FE does not reset quarantine, and
warm reattachment refuses nonempty action tables. This is an explicit temporary
limit pending a proven packet-retirement barrier; table readback and queue
clearing alone do not justify recycling indices. Automatic MDB production,
TC replacement semantics, complete per-replica long-frame handling, returned
ONU source-MAC rewriting, PON/radio replicas and complete DMA/AFT/FE/PE/MiniOLT
retirement remain work. No physical forwarding or complete FTTR acceptance
is claimed by these offline checks.

### Source/group IP multicast Hash profile

The primary Hash profile already uses all eight tuples for unicast, per-VIF,
exact VLAN and PPPoE forwarding. IP-specific multicast rules now use a separate
profile1 and classifier IF_ID slot0 in the LAN half (key128/FIB512). This
classifier matches multicast MACs except broadcast, preserves the source port,
and keeps CPU16 as the miss destination. Profile1 retains the explicit
MAC-based templates61/59 ahead of the new IP template55. It is prepared lazily
for the first IP-specific multicast rule; ordinary unicast keeps profile0.

The new mask includes full source and group IPv4/IPv6 addresses, IP protocol,
EtherType, source/destination MAC, ingress, LLID and two exact VLAN tags with
PCP/DEI. Errors/fragments/options miss it. The complete replica sequence is
still required. Partial IP prefixes, a nonmulticast group, mismatched address
family, and wildcard tag PCP/DEI are rejected. This prevents two IP groups
sharing an Ethernet multicast address from sharing a hardware recipient set,
and keeps UDP data rules from consuming IGMP/MLD control traffic.

`rtl9617c-fe-mcast-profile.h` owns the classifier key/FIB, masks55/54 and profile1
register snapshots. It verifies that profile1 is inactive, no active tuple
references mask55, and no existing classifier key is active before taking
ownership. All mask/profile/FIB data is written and read back before the key
becomes valid. Restoration first invalidates and reads back that key, then
restores the saved profile, mask and FIB; failed operations retain ownership
for retry. It does not reset the separate NI/action quarantine.

`trace_stock_fttr_mcast_classifier.py` executes the actual FC profile producer
and original mask initializer. Original profile2 has a destination-only128-bit
IP template and a source-plus-destination128-bit template. The native mask adds
MAC, protocol, ingress and exact-tag constraints required by Linux ownership.
Twelve actual classifier key/action conversions match the native encoder.
256 IPv4/IPv6 source/group cases, including different groups with the same
multicast MAC, match the original masked key and CRC16/CRC32 with randomized
ignored fields. Original CRC input selects main-table/profile before those
fields are masked; mask lookup responses are modeled explicitly.

`test_fe_mcast_profile.c` covers574 indirect-command failures, applied-write
completion loss, publication order, exact restoration and foreign-state
refusal under ASan/UBSan. Production install/delete tests now include this
profile, and parser, namespace, LAN/P6 and bridge-learner regressions pass.
The current Image/modules build and source consistency are verified; reports
are `mcast-classifier-20261106.json` and `mcast-ip-profile-kernel-20261106.log`.

This profile supplies the hardware entry for the complete-packet producer
described below. It does not itself establish complete MDB semantics or a
physical hardware hit.

`test_bridge_multicast_selection.py` executes this kernel's actual
`br_multicast_flood`, `maybe_deliver`, `should_deliver` and clone-control path
under ASan/UBSan. Fourteen cases cover member/router de-duplication,
(*,G)/(S,G) INCLUDE/block behavior, STP/VLAN/hardware-domain/isolation/hairpin
filtering, local receive, multicast-to-unicast dispatch, router-only miss and
failure at each clone. The router branch can remain selected when the same
port's member entry is blocked or INCLUDE. A late clone failure leaves only
part of the original recipient set transmitted. Automatic learning must
capture a complete successful forwarding transaction and retain local delivery;
observing individual post-routing copies or simply listing MDB ports is
insufficient. The report is `bridge-multicast-selection-20261106.json`.

The current kernel also defers ordinary switchdev MDB updates, while
`br_mdb_flag_change_notify` skips switchdev. Router deletion notifications
coalesce the IPv4/IPv6 state. Those notifications alone cannot supply the
complete immediate invalidation needed by an automatic source/group flow.

### Complete bridge multicast transactions and native learning

`222-native-bridge-multicast.patch` connects the kernel's actual
`br_multicast_flood` decision to the native FE owner. Each received UDP
multicast packet starts one numeric transaction, records every recipient
selected by `should_deliver`, then records that output's final frame in
`br_dev_queue_push_xmit` after bridge VLAN, netfilter and forwardability checks.
Completion requires every selected copy exactly once; clone failure, missing
or repeated outputs, modified IP/MAC/UDP identities, multicast-to-unicast,
GSO and a concurrent generation change reject the entire transaction.
The core allocates tickets across driver unload/reload; skb extensions do
not retain driver pointers. There are16 pending transactions, each with at
most64 recipients. Deferred netfilter copies cannot complete an earlier
transaction after its final completion.

The worker resolves the complete decision under RTNL and the FE mutex, checks
source FDB ownership and all logical/physical output owners, and installs it
through the existing Hash/NI/action/QM pipeline. The current supported input
is a native LAN source0..4. Outputs are native LAN ports and the admitted
shared `fttr-mc`; it adds the original outer8100/VID4095 and at most one customer
tag. The established per-UNI duplicate suppression is preserved. An output
outside this set, raw P6, PON, radio or hairpin keeps the entire decision in
software. Local CPU copies were initially excluded; the implementation below
now supports them. These remaining paths are an incomplete hardware coverage
boundary, not a claim that those FTTR functions are done.

Learned mask54 includes both full IP addresses, UDP source/destination ports,
EtherType, source/destination MAC, physical ingress and exact tag stack.
Explicit TC templates retain slots0..2; learned traffic uses slot3. IPv4
options/fragments and IPv6 extension headers remain outside the learner.
The parser checks IP-declared lengths and requires the UDP length to equal
the IP payload, so Ethernet padding cannot make a malformed UDP frame valid.
The complete set's minimum output MTU is allocated from the existing PE MTU
owner and the CPU16 failure destination is checked before publication.
Oversized traffic is intended to return through the native receive path for
Linux's per-output decision; actual failure-frame contents and absence of
hardware replicas on that path remain physical acceptance items.

A short IRQ-safe gate lock brackets36 bridge multicast writer entry points,
including report/query handling, source/member/router expiry and MDB changes.
The gate removes learned slot3 from the profile's active tuple count before
those mutations, advances a generation at both ends, and cannot reopen during
a writer. Old learned generations are withdrawn before a new generation is
published; uncertain Hash/cache/NI cleanup keeps the gate closed. A failed
install retains its learned/deleting identity even when its own rollback
fails. Duplicate keys are compared against the complete immutable action
bitmap and MTU; a changed decision closes the profile and requires a fresh
transaction. The worker refreshes active source FDB entries and expires idle
learned multicast flows after30 seconds.

Netfilter and TCX pointer publication/removal also close the gate. The learner
refuses foreign bridge/netdev hooks and TCX programs, and checks each VLAN
lower and each actual TX queue. Only noqueue, pfifo_fast and fq_codel are
accepted; classful/action qdiscs keep software forwarding. RTNL-held qdisc
grafts and VLAN priority-map writes invalidate before mutation. Registration
holds a writer generation across `synchronize_net()` so pre-registration
writers with NULL tokens cannot be missed. Unregistration closes first and
waits for all existing callback readers before freeing the driver owner.

Offline checks execute the actual production callbacks, install/delete and
MTU allocator, the actual Linux selection/writer functions, and a threaded
RCU reader model under ASan/UBSan. They cover filter/TCX/qdisc rejection,
partial sets, late/duplicate copies, source/group recipes, generation drift,
failed install plus failed rollback, cleanup/relearning, and owner reload.
`test_bridge_mcast_frames.py` supplies109 actual packet-byte vectors. Original
ARM64 execution additionally confirms eight last-tuple removals,64 MTU
set/get cases and CPU16 failure routing,12 classifier conversions and256
native masked keys/CRCs. Profile IO tests cover574 command failures/lost
completions. Unicast bridge, flow-parser, flow-release, namespace, LAN/P6 and
CPU-DEEPQ regressions pass. The final Image/modules build is
`artifacts/fttr/mcast-learn-kernel-20261106.log`; the new patch applies and
reverses with zero fuzz and the built driver/header/core sources match.

This still does not establish complete FTTR multicast parity. Local CPU
copies are implemented below; PON/radio/ONU-source acceleration and safe
resource reuse remain work. Original tuple deletion only clears a tuple and reduces its count; it
has no explicit cache-drain wait. The native gate is therefore not a proof
that already-selected/cache/in-flight hardware packets have retired. Published
NI groups remain quarantined (255 cumulative publications,64 immutable action
recipes), so membership churn eventually falls back to software. Immediate
policy transitions, over-MTU fallback, actual FE hits and physical replication
have not been tested on a device. No device was accessed for this change.

### Original CPU multicast replica and receive distinction

The executed FC member/action/NI chain also accepts a CPU16 recipient for
zero, one and two final VLAN tags. These three recipes use the same absolute
S/C tag commands and explicit source-filter disable as LAN/P6 recipients.
The result is recorded separately in
`artifacts/fttr/mcast-cpu-replica-original-20261106.json`; it proves the original
producer can encode the CPU destination, not how a returned packet is marked.

`trace_stock_fttr_mcast_hw.py --mgr <fc_mgr.ko>` now executes the real
`rtk_fc_skb_rx` receive conversion. Fifty-four cases vary physical source0/4
versus MCE source27, POL0/55/56, multicast versus unicast, and hardware
reason0/4/15. For multicast from source27 with POL56, original fc_mgr rewrites
the software receive reason to17 and passes to its normal receive consumer.
Neighboring POL55 instead becomes reason15 plus interface metadata and takes
the direct-transmit consumer. The original hardware reason alone does not
distinguish a local CPU replica. The final consumers and scheduling boundary
are modeled; this does not establish Linux duplicate-suppression behavior.
The report is `artifacts/fttr/mcast-cpu-receive-original-20261106.json`.

The dedicated host-copy branch is now traced back to its actual producer too.
Setting the original abstract flow's extra-copy flag makes
`rtk_fc_abstrSwSyncToHw_add` prepend **destination18/POL56**, followed by the
ordinary LAN/P6 recipient actions. Six full FC/action/NI executions confirm
this for zero/one/two egress tags. The fixture has no ingress VLAN pattern, so
the extra action keeps both tag commands at NOP. This is distinct from merely
placing CPU16 in the ordinary member list. Its report is
`artifacts/fttr/mcast-host-copy-original-20261106.json`.

This matters to the native integration because it deliberately preserves
physical source0..4 across MCE, unlike that source27 predicate. The native framework also uses18 as its Wi-Fi source. A CPU replica
must retain an unambiguous indication that all other recipients were already
forwarded, while ordinary Hash misses and PE MTU returns must still run the
full bridge decision. Merely adding destination16 or treating every received
multicast as hardware-forwarded would duplicate or drop traffic. The native
learner previously rejected transactions requiring local delivery. The native
producer/receiver below now implements that distinction.

### Native CPU multicast host copies

Complete learned LAN-input multicast transactions can now include one local
CPU copy, including a group with no external recipients. The extra NI action
preserves ingress VLAN bytes and COS/source MAC/TTL. Its intentional adaptations
from the executed original CPU18/POL56 producer are CPU16 (native CPU0 receive
pool), POL256+the quarantined NI group number, and `keep_o_lspid` (DATA2 bit11).
CPU16's two port-map entries, CPU mapping and NI receive selection must match
native QM configuration before publication; foreign routing is rejected.
There is no new packet-byte marker, userspace skb mark or raw CPU18 bypass.

The FE owner records the exact source/group/UDP tuple, original wire tags,
physical and logical ingress, namespace, bridge, VLAN and generation before
publishing Hash. QM recognizes only CPU16/physical LAN0..4/nonzero tagged group,
copies the frame privately and returns EPP credit before owner lookup. It uses
`netif_receive_skb` so GRO cannot merge a host-only copy with an ordinary Hash
miss. The physical and logical VLAN receive path retains the skb extension;
normal native capture hooks leave it intact. The bridge still runs ingress,
MDB/local eligibility, bridge VLAN and local netfilter checks, but never floods
or forwards a valid host copy again. The local-copy decision is consumed at
this bridge so it cannot authorize a later stacked bridge or routed path.
Ordinary misses and PE MTU returns have no reserved POL and retain full Linux
forwarding. The supported learner remains complete snooped UDP transactions;
this change does not introduce unknown-multicast flood learning.

Withdrawal removes the local owner before attempting Hash/cache deletion,
including when deletion fails. Receive and bridge-delivery checks both reject
retired generations, stopped owners and mismatched ingress/bridge/VLAN. The
core's monotonically increasing owner ticket survives driver reload. Promisc
and allmulti changes now bracket the actual flag publication with learned
profile invalidation, including calls with notifier delivery disabled. The
callback ends before RX driver operations, so no RCU writer spans potentially
sleeping device callbacks.

`test_bridge_mcast_host.py --kernel <prepared-kernel>` executes the production
QM poll/metadata decoder and actual Linux bridge finish/pass-up functions:
144 QM cases, the full 64x64x512 destination/source/POL input space, 40 bridge
cases and receive-flag reference/overflow transitions pass under ASan/UBSan.
The DMA and VLAN/filter helpers are modeled; physical hardware and the complete
kernel receive stack are not emulated. `test_bridge_mcast_learning.py` exercises
production host ownership, complete/host-only learning, source/tuple mismatch,
late generations and failed withdrawal. Core lifecycle,109 packet parser
vectors,16 Linux member-selection cases, unicast/flow release, CPU route and
every indirect fault/restore regression pass. Original FC/NI execution agrees
with six ordinary replica recipes and1530 explicit native adaptations of six
host-copy recipes. The actual QM P6 regression retains all768 cases.
The final Image/modules build is
`artifacts/fttr/mcast-host-kernel-final-20261106.log`; patch223 replays and reverses
with zero fuzz/offset and the compiled RTL9617C sources match the overlay.
The worktree subsequently merged main457f1c4 (mesh and STA offload); shared
FE unicast/multicast/peer-route regressions and the merged Image/modules build
also pass (`artifacts/fttr/mcast-host-main-kernel-20261106.log`, mesh enabled).

Resource retirement is still incomplete. Each published host generation needs
its own immutable action (64 total action slots, shared with other recipes),
so host-bearing groups can exhaust actions before the255 cumulative NI group
limit. Exhaustion falls back to software without reassigning old IDs. The
tests explicitly cover this limit; neither profile closure nor Hash deletion
proves cache/MCE/PE/NI packet retirement. PON/radio multicast
acceleration, safe reuse and real hardware acceptance remain work. No device
was accessed.

### ONU-source multicast and per-replica source MAC

The original `onu_setup.sh` `.mc` TX rules push the shared GEM4095 tag and,
when `EXT_SWITCH_EN` identifies a returned ONU packet, set its source to the
factory LAN MAC. Per-UNI IPv4/IPv6 multicast copies are suppressed separately.
The native complete learner now accepts physical P6/source6 through a live,
exact802.1Q unicast GEM VLAN on `fttr0` (GEM1..4094), with source FDB/bridge
ownership and FTTR admission checked. LAN recipients keep the ONU source MAC;
only the shared `fttr-mc` recipient replaces it with that endpoint's MAC and
adds outer4095. A shared P6 output may use the same physical input port because
its logical endpoint and outer GEM differ. Ordinary same-port hairpin and raw
P6 output remain outside this producer. IGMP/MLD still use the established
report path; learning remains complete snooped UDP transactions.

The per-copy edit uses the independent eight-entry **L2** PE MAC table at
1714/1718+8*index, not the L3 source-MAC CAM. Original direct-write execution
confirms byte order, all eight indices and index8 rejection. Executing the
complete FC action/parser/NI producer with a source edit shows DATA2
`mac_sa_cmd`, a three-bit selector and **also** `ip_ttl_dec_cmd`. The native
bridge copy intentionally clears that TTL bit: the FTTR script requests an
Ethernet source rewrite for loop prevention, not a routed hop. Its tags/COS
and source-filter settings otherwise match the executed recipe. The original
host copy remains unchanged even when its accompanying P6 copy edits the MAC.

MAC words are written/read back before their actions and group become visible.
The group owns a MAC reference even after an uncertain partial write. Abort
withdraws group/actions first and restores an unexposed MAC last; a failed
restore retains the dependency for retry. Foreign nonzero slots are skipped,
reserved high bits preserved, identical native addresses shared, and hardware
drift rejected. Exposed MAC contents remain immutable with exposed actions:
changing the shared endpoint MAC invalidates learned flows and consumes a
fresh address slot, never repurposing an old in-flight selector. At most eight
distinct exposed L2 MAC values are available, in addition to existing64-action
and255-group cumulative limits. Full safe retirement/reuse is still required.

P6 host replicas carry the same quarantined group/owner metadata as LAN host
copies; the original outer GEM/customer tags re-enter the physical/VLAN receive
path, and the logical bridge validates the owner before local delivery.
P6-origin metadata is preserved. FTTR business gating now invalidates the
learned profile before its CPU RX purge and FE teardown. Ordinary P6 misses
retain segmented RX. For a hardware host copy, the common PE MTU is capped to
fit the complete ingress frame in one CPU buffer (including the16-byte HeaderA
and captured tags). An unexpectedly segmented host copy is dropped instead of
being treated as a new P6 miss. Hardware MTU-return byte fidelity still needs
physical validation; these offline checks do not prove it.

Verification: `test_fe_mcast_source.py` executes three original FC/NI source-edit
recipes plus all eight original direct MAC writers, compares native edits
with TTL preservation, and covers seven lost-write positions, sharing,
restoration retry, foreign slots, drift and eight-address exhaustion.
`test_bridge_mcast_learning.py` executes full ONU→LAN/shared-P6/CPU installation,
exact GEM rejection, withdrawn admission, shared MAC changes, duplicate learning
and the1566-byte host versus1978-byte non-host MTU bounds in its tagged fixture.
The actual QM/bridge host tests now cover192 poll cases including source6 and
malformed segmented copies.512 LAN4/P6 IPv4/IPv6 masked keys/CRCs agree with
original ARM64 execution. Existing bridge/FE/CPU-gate/GRO, original LAN/host
recipe and768 P6 fragmented-RX regressions pass. Image/modules build and source
identity are recorded in `artifacts/fttr/mcast-onu-kernel-final-20261106.log`.
No device was accessed; PON/radio replicas, unknown-multicast flood offload,
safe hardware retirement and complete FTTR acceptance remain open.

### PON upstream multicast replicas

A complete learned LAN/ONU-source group may now include an upstream ITU PON
recipient along with LAN, shared FTTR and local copies. Each PON replica runs
the existing OMCI/EVTO path selector against that recipient's final frame.
The native CPU transmitter and learner share `rtl_pon_itu_service_entry`, so
an explicit VLAN upper skips the same UNI treatment stages in both paths;
raw PON bridge traffic retains the OLT's UNI policy. Ambiguous GEMs, unavailable
PON admission, downstream-only multicast GEMs, or unkeyed priority/DSCP choices
that change GEM/queue/output tags reject the entire learned transaction.
This is upstream transmission over a selected bidirectional business GEM;
it is not a downstream multicast GEM used backwards.

Executing `rtk_fc_abstrSwSyncToHw_add`, its action parser and the NI action
producer confirms separate per-member **POL=GEM index**, **LDPID=32+T-CONT**,
and **COS=queue**. Native actions reproduce these words for GEM indices8..255,
T-CONT1..31 and queues0..7, including all final VLAN tags. The shared FE action
continues to select NI replication; it cannot hold a single PON GEM or COS
for all recipients. CPU/P6/LAN actions retain their independent metadata.
The original member7/action-bit12 branch reads a six-byte PON route row;
the fixture supplies its enable, destination, queue and GEM fields from the
selected native PON snapshot. The action producer and field packing execute
original instructions. The earlier fixture mislabeled helper5+160 as PON;
executed manager registration identifies it as `rtk_fc_wlanDevIdx2port`.
The corrected PON branch still produces all nine matching native recipes
(`artifacts/fttr/mcast-pon-route-corrected-20261107.json`).

PON logical destinations must retain all four PDPID8 mappings and native
upper-PON map0xa. A destination-specific DBUF entry sends each replica through
the existing native WAN QM pools/backpressure, as P6 uses its separate matched
entry. The exact entries reproduce the original `aal_arb_port_dbuf_set` field
encoding. The owner snapshots/restores entries, retains reservations across
failed writes, and never overwrites foreign rules. Port isolation now checks
both32-bit halves and egress ownership uses all64 bits, including T-CONT31.
The common MTU also accounts for PON's2000-byte final Ethernet limit after
EVTO tags, in addition to each logical output's own MTU.

PON admission is checked for every recipient. PON disable now finds PON in
any replica position, not just the input or terminal output. Replacing an OMCI
path snapshot closes the learned profile before withdrawing its dependent
flows; a failed Hash/cache withdrawal keeps the old snapshot and references
for retry. This prevents a software configuration switch from silently
publishing a new GEM mapping over retained old flows. It is not proof that
hardware packets already in flight have retired before GEM reuse.

Offline checks: nine original complete FC/NI PON recipes match native words,
31 destination DBUF writes match the original setter, and162 native indirect
failure/restore cases cover mixed P6/T-CONT5/T-CONT31 routing. Checks include
four port-map variants, upper-PON configuration drift,64-bit membership,
invalid fields, shared8-entry DBUF exhaustion and retry. Actual production
capture/FE install/delete/path-replacement tests cover PON in the middle of a
group, local copies, ambiguous and downstream-only GEMs, VLAN service entry,
DSCP/priority dependencies, duplicate learning and failure-preserving cleanup.
Existing CPU PON PPPoE/DHCP/IPTV byte-path, unicast bridge, FTTR reports,
multicast core/gate/frame and FE ownership regressions pass. Image/modules and
source identity are checked in
`artifacts/fttr/mcast-pon-kernel-final-20261107.log`.

This still leaves PON-source acceleration and native radio replicas to add.
The8 DBUF entries are shared with P6 and foreign users; saturation rejects
new groups. Existing255-group/64-action/8-source-MAC quarantines remain.
Unknown-multicast flood offload, safe hardware retirement and real optical/
forwarding acceptance remain incomplete. No device was accessed.

### PON-source multicast and native host receive

The complete multicast learner now accepts the native ITU PON source7 with
its received **GEM table index** and current OMCI path generation. WAN IF_ID
row0/FIB0 joins LAN row128/FIB512 in profile1; both classifiers preserve the
physical source and POL/GEM and default to CPU16. The profile owns/restores
both key/action snapshots and closes learned tuple3 for both directions with
the same gate. The original IF_ID index function confirms row0/128's four FIB
slots, and original key/action converters confirm the profile's encoding.
Ordinary unicast retains profile0 and its eight templates.

The PON receive driver already saved original wire tags. Native ingress and
bridge capture now also preserve the post-OMCI tag image and skb priority,
before a local VLAN upper or bridge removes a tag. The learner runs the same
`rtl_pon_itu_receive_path` helper as CPU RX: it first tries the configured local
VLAN service boundary, otherwise applies the full UNI chain. It checks exact
GEM/generation, all final tags and priority, including any unkeyed DSCP variants.
Missing/changed service topology or a different output image rejects learning.
The original GEM index is included in the hardware key and existing-flow
identity, so otherwise identical packets arriving on two GEMs get independent
rules. Downstream multicast GEMs are accepted here; their upstream use remains
excluded by the distinct output selector.

A PON host replica still preserves original wire bytes and uses POL256+group
for its quarantined identity. The FE host owner recovers the original GEM;
QM passes it to the existing PON endpoint without applying `eth_type_trans`
first. PON RX rechecks GEM, active paths and configuration generation, applies
the normal OMCI VLAN restore, preserves the host skb extension and delivers
without GRO. The bridge then performs local eligibility checks and consumes
the host-copy decision without forwarding again. A late ITU host copy after
an EPON mode switch is dropped before it can be interpreted as an LLID packet.
PON disable or path replacement invalidates these flows through the same
whole-group ownership used for PON outputs. Source7 host MTU limits and the
one-buffer guard use the existing complete-copy receive contract.

Offline verification includes two same-tuple GEM55/56 flows present at once,
independent hardware keys/host owners, VLAN901-to900 wire/logical capture and
OMCI receive, multicast GEMs, actual service lookup, wrong GEM/generation,
withdrawn admission and extension-allocation failure. The production QM poll
and PON dispatcher checks cover source7 GEM recovery, no premature Ethernet
pull/GRO and late EPON-mode rejection.596 profile command faults/applied
write timeouts restore both classifier snapshots;1280 original masked-key/CRC
cases now include source7/GEM8/55/255, with12 classifier conversions and eight
IF_ID FIB-index executions. Existing unicast bridge, FTTR reports, complete
multicast ownership/core/gate,109 parser byte vectors, ordinary PON PPPoE/DHCP/
IPTV receive and768 P6 fragmented-RX cases pass. Final Image/modules and source
identity are checked in
`artifacts/fttr/mcast-pon-input-kernel-final-20261107.log`.
The older `test_pon_data_path.py` fixture cannot compile the current ITU driver
interfaces; `test_pon_host_mode.py` executes the changed receive dispatcher
with a focused model instead. That stale aggregate check is not claimed as
passing and no physical device was accessed.

Direct PE radio multicast, EPON-specific multicast integration,
unknown-multicast flood offload and safe hardware resource retirement remain
work. The existing group/action/MAC/DBUF capacity restrictions remain. Neither
these checks nor the new generation validation prove physical PON packet
retirement, lossless transitions or full FTTR acceptance.

### Wireless-source multicast and VIF identity

Native Wi-Fi ingress now submits plain IPv4/IPv6 UDP multicast to the same
NI lookup used by unicast. The first miss retains its actual registered VIF
through the QM endpoint, wire hook and bridge capture. Complete decisions
can install LAN, shared FTTR, ITU PON and local CPU recipients with source18
and POL/VIF1..60. The multicast mask ignores MDATA, so the stored key zeros
that field while retaining the exact POL. The common action clears ingress
VIF metadata before NI replication; PON and host copies must not be decoded
as radio direct-TX EPPs.

Host copies preserve their original VIF in the quarantined group owner and
validate the live QM endpoint before returning to Linux. They bypass a second
hardware query and use the existing bridge-only local-copy decision. Port
withdrawal closes the learned profile before attempting Hash removal, so a
failed removal cannot keep an old host owner usable. Broadcast, IGMP/MLD,
multicast TCP/PPPoE, GSO and frames exceeding the complete CPU RX buffer remain
in software. Existing foreign-hook/TCX/qdisc restrictions still apply; only
the exact native Wi-Fi ingress hook is additionally accepted.

Validation is offline. The original classifier/Hash oracle now checks1792
keys including source18/VIF1/60 and varying ignored MDATA. Native DMA tests
execute7200 submissions and300 comparisons against the original descriptor
instructions. Actual Wi-Fi receive/ingress functions cover IPv4/IPv6 data,
control exclusions, VLAN metadata, BUSY and host-copy reinjection; actual
bridge hooks preserve VIF identity. Complete learner/table tests include
wireless-to-LAN/FTTR/PON/local copies, wrong-VIF rejection and failed withdrawal.
The240-case QM host-copy suite,40 bridge receive cases,768 P6 fragmented RX
cases, gate/core and relevant unicast/FE rollback regressions pass.
Image/modules built successfully in
`artifacts/fttr/mcast-wifi-input-kernel-20261107.log`; all83 driver/header/core
inputs match the compiled source tree.

AP wireless output is implemented in the following increment. The present direct PE downlink uses
unicast peer MACIDs and discards an unmatched multicast destination. It
cannot be used for multicast merely by removing the FE guard. The original
CPU interface-directed replica path and normal mac80211 BCMC/key/DTIM behavior
are connected through the CPU replica path below. This increment does not establish safe VIF/group
reuse, full hardware retirement or physical traffic acceptance.

### AP wireless multicast replicas through native CPU dispatch

Complete learned groups now include AP VIF recipients alongside LAN, shared
FTTR, ITU PON and local reception. Each wireless copy carries the final S/C
tags selected by Linux and its own immutable CPU dispatch cookie. NI replicates
the group; native QM validates the cookie's flow generation, original source,
IP/UDP tuple, final tags, current bridge output and registered VIF endpoint,
then uses that VIF's normal `dev_queue_xmit` path. It never re-enters bridge
forwarding or the direct PE unicast-peer queues. Normal mac80211 AP group TX
retains BCMC, group-key and DTIM handling, including its existing PE-assisted
TX request path when enabled. No physical multicast, encryption or DTIM
acceptance is claimed.

Original evidence is now executed across helper registration, replica
construction and CPU TX selection. `rtk_fc_helper_init` installs wireless
helper5+160=`rtk_fc_wlanDevIdx2port` and+200=`rtk_fc_wlan_hard_start_xmit`.
Member bit16 identifies a wireless VIF; its replica has POL=VIF and the
registered destination. `rtk_fc_skb_rx` sends MCE source27 copies to the live
VIF's saved TX callback; POL56 instead retains its reserved local-copy role.
The native implementation adapts this to CPU16, preserved physical source and
quarantined cookies, independently from local copies and normal misses.
`trace_stock_fttr_mcast_wifi.py` checks30 dispatch cases and12 full replica
recipes. Its synthetic route rows do not assert factory VIF allocation policy.

Cookies share the existing255-ID quarantine with actual NI groups: a flow
with N wireless recipients consumes N+1 IDs and N immutable wireless actions.
Failed/retired cookies remain unavailable, including partial reservations;
withdrawal removes all live dispatch references before Hash/cache deletion.
An unchanged complete decision retains its cookies; a changed decision or
failed teardown closes the learned profile. CPU MTU limits cover the final
wireless frame with tags. AP output is supported; STA-output VLAN/QoS
equivalence, direct PE BCMC queues and bridge multicast-to-unicast rewriting remain
outside this path. Unsupported recipients retain whole-group software forwarding.

Offline validation includes LAN/PON/ONU-to-two-VIF transactions with optional
local copies, separate cookies/final VLANs, unchanged-decision reuse, wrong
source/tags, interface withdrawal, failed Hash deletion, cookie pressure and
a generation change during final parsing/allocation.384 actual QM poll cases
cover local/direct/miss separation, both internal headers,0..2 tags and all
eight COS values, with endpoint absence/reassignment rejection.765 native
wireless action adaptations agree with original FC/NI output;1530 host-copy
adaptations and existing LAN/P6/PON action tests remain valid. Six actual
wireless-output byte cases cover IPv4/IPv6 and0..2 final tags. Existing FE/TC
parser, bridge/gate/core,768 P6 RX,4224 PE request and PTK/GTK lifecycle
regressions pass. Image/modules and source identity are recorded in
`artifacts/fttr/mcast-wifi-output-kernel-final-20261107.log`.

Full MCE/PE/NI/MiniOLT packet retirement is still unresolved. The final
generation check rejects a writer during parsing, but it cannot recall copies
already enqueued in qdisc/mac80211/driver/firmware. These queues and safe
cookie/action/group reuse require a separate retirement implementation.

### Station wireless replicas and software QoS equivalence

The owned CPU-replica path now accepts both AP and STA destinations. It
reconstructs the software ingress priority from the native receive path and
the last VLAN ingress-priority map: LAN/P6 start at0, PON uses the validated
OMCI receive priority, and raw wireless input preserves each packet's COS.
VLAN map values such as256..263 retain their mac80211 direct-priority meaning;
replacing262 with COS6 would incorrectly turn a DSCP0 packet from VO into BE.
QM initializes COS before owner dispatch and does not overwrite a priority
restored by that owner.

The native radio driver advertises SG but no VLAN TX offload. The Linux core
therefore materializes metadata tags before mac80211 chooses its queue, so
the native final-wire-tag recipe needs no separate tag extraction. Admission
and late receive both reject changed VLAN TX features or wireless interface
type. The multicast VLAN compiler now follows the actual
`vlan_dev_hard_start_xmit` reorder/protocol decision, including an existing
same-protocol tag with `reorder_hdr` disabled. VLAN flags gain synchronous
multicast invalidation, while existing ingress/egress priority-map invalidation
remains in place. Feature changes also withdraw complete multicast groups.

Before installing a wireless recipient, the compiler checks its observed
priority against the derived source and reproduces its complete VLAN chain.
For raw wireless sources all eight possible COS values must produce the same
final tags; a COS-dependent output-tag recipe remains software. Current WME,
DSCP, admission-control, reserved-TID and association behavior still execute
in mac80211 for every CPU replica. These policies are not frozen by the FE
sample. The path retains AP/STA TX and peer/association checks rather than
constructing a wireless frame directly.

`test_bridge_mcast_qos.py` executes Linux VLAN TX, software tag validation,
ingress-priority lookup and WME/DSCP functions against native recipes:12288
IPv4/IPv6,0..2 VLAN, all DSCP/PCP and ordinary/magic-priority cases agree on
wire bytes, protocol, priority and queue. It also checks no-reorder behavior
and actual VLAN flag publication invalidation. Original wireless execution
adds32 per-copy COS/QoS cases; native STA transactions cover LAN, PON, ONU and
wireless sources, fixed/mapped/dynamic priority, type/features drift and
COS-dependent VLAN refusal.384 QM cases confirm restored priority survives
dispatch. Existing AP/bridge/TC/FE rollback/core/gate and Wi-Fi byte regressions
pass. The new224 patch applies with zero fuzz and matches the prepared kernel;
Image/modules and83 driver/header/core source identities pass in
`artifacts/fttr/mcast-sta-output-kernel-final-20261107.log`.

No hardware was accessed. Direct PE BCMC, EPON-specific multicast, unknown
flood acceleration and complete hardware/queued-packet retirement remain
unfinished; cookie/action/group quarantine and capacity restrictions remain.

### Original EPON multicast route production

`trace_stock_fttr_mcast_epon.py` now executes the retained
`ca-rtk-epon-drv.ko:us_pon_sid_mapping_adding.isra.0` through
`fc_8277c.ko:rtk_fc_pon_sid_set`, then the multicast member/action parser and
NI action converter. The initializer creates exactly eight SIDs0..7: enabled,
DEEPQ1 for direct DMA, destination32+the reported MPCP software-index offset,
COS=SID and flowId/POL0. It does not assign ITU GEM indices. The FC six-byte
route rows retain enable/destination/COS/POL, and multicast member7/action-bit12
consumes them with each recipient's final S/C tags. Original register-offset
inputs0/4/15/31 and queues0/3/7 cover destination32 as well as higher ports.

Replacing a valid SID first invokes flow deletion by that SID. Injected
failures show that FC nevertheless updates its software row and returns0
after failed deletion or LSO programming. The native integration must keep
withdrawal errors/retry ownership.128 original SID new/replace executions
and36 complete FC/NI replica recipes pass in
`artifacts/fttr/mcast-epon-stock-20261107.json`. The software-index getter,
cross-module API forwarding, allocation, actual LSO hardware writes and
physical queue delivery remain modeled boundaries.

This evidence drives the separate EPON path below. Native PDC maps received
LLID table entries to business MAC instances; multicast LLIDs use the primary
interface. ITU GEM and EPON MAC-instance identities remain distinct, including
EPON instance0. No device was accessed.

### Native EPON multicast admission and replicas

The complete Linux multicast producer now accepts EPON source7/POL0..31 and
EPON output destinations32..63. Each EPON replica uses POL0 and the native MAC
instance, following the original SID/action semantics above rather than an
ITU GEM index. The same transaction can include LAN, shared FTTR, AP/STA and
CPU-local recipients; failed or unsupported recipients keep the entire group
in software. Existing source/group/VLAN/UDP keys distinguish ingress instances.
The ordinary PON receiver stamps EPON identity only while its published
snapshot is admitted. QM-owned local copies restore the exact instance and
snapshot generation, and PON RX validates the selected netdev before entering
the bridge without GRO. Instance0 is valid and is not confused with absent GEM
metadata or a delayed ITU packet after a mode switch.

`rtl9617c-epon-path.h` records all64 LLID rows, registration epoch, current
active instances, mode,4/8-queue width and actual queue-enable masks. PDC must
match every row before publication. Unchanged snapshots do not consume a new
generation. Same-MAC LLID replacement, registration changes, holdover expiry,
RX reset, LLID resize, PUC/SLA changes, FE disable and protocol shutdown revoke
admission and withdraw all flows touching PON, including PON as a nonterminal
replica. A failed Hash/cache deletion retains the old snapshot and flow owners
for retry, with admission closed. PDC refresh also retries a previous failed
withdrawal before any subsequent mapping write. Cleanup failure still allows
optical TX and hardware RX/TX shutdown and is returned to the caller.

PDC reconfiguration while FE remains enabled now preserves business rows'
FE destination24/bypass0, while both OAM banks keep CPU16/bypass1/COS7.
Previously the reinitializer rewrote all rows for CPU16; a later POL-only
refresh could not restore unchanged business mappings before publishing the
next snapshot. The actual reinitializer is tested across all three EPON modes
with FE on/off (1536 table rows), withdrawal, partial IO failure and retry.

Fixed output priorities follow actual native TX: `priority & (queues-1)`.
VLAN ingress mappings, including priorities256..263, are reconstructed from
the complete input; output VLAN bytes must match Linux. Raw Wi-Fi COS is
preserved per packet with eight enabled EPON queues, provided every COS gives
the same final VLAN bytes. Four-queue raw Wi-Fi output is still unsupported:
the current shared key/action cannot apply the needed per-copy COS mask.
The reference AAL describes four-queue PUC indexing with COS[1:0], and the
retained binary confirms the mode control at0x804c. The intermediate native
NI/QM queue selection is not yet established as equivalent for COS4..7, so
this reference observation alone does not lift the restriction.
Disabled queues, failed SLA application and foreign VOQ width refuse
admission. EPON output MTU follows the native1522-byte Ethernet limit.

Queue admission now reads the actual PVT for every active instance before
publishing the snapshot. Both the PVT row/queue enables and the global VOQ
bitmap must permit the queue. Enabled queue IDs must match the native
instance/queue identity; linked rows and a foreign deficit scheduler are
rejected. Busy/timeout results revoke admission and retain withdrawal retry.
Unchanged decoded masks keep the generation. The retained AAL PVT decoder
agrees with960 native admission cases and7680 queue-field decodes, including
fields spanning register words; both original mode setters/getters also pass.
The lifecycle test covers PVT enable/remap, linked rows, read failures and
failed flow withdrawal. Reading tables on refresh does not make uncoordinated
foreign MMIO writes atomic with packet lookup.

Validation on2026-11-07 executes the actual snapshot producer, LLID writer,
PDC update, complete learner, FE table lifecycle, PON receive and QM poll:

- All32 instance-count configurations, unchanged snapshots, same-instance
  LLID changes, repeated withdrawal failures before PDC writes, failed PDC,
  registration/holdover/mode/queue changes and publication retry.
- Instance0/31 ingress and local copies, two simultaneous EPON outputs,
  mixed EPON/LAN/FTTR/wireless recipients, fixed4/8-queue priorities,
  preserved COS, disabled-queue and four-queue dynamic refusal.
- Nine ITU and nine EPON complete original FC/NI recipes,32 original DBUF
  destinations,206 route failures/retries and2560 original classifier keys/CRCs.
- 432 actual QM poll cases,160 EPON host-receive cases,40 actual bridge receive
  cases,768 P6 RX cases and12288 Linux VLAN/WME comparisons; existing FE
  parser/ownership, unicast bridge, multicast gate/core, PON shutdown/mode
  and management/SLA checks pass.
- ARM64 Image/modules build passes;84 native driver/public-header/core inputs
  match the prepared kernel, with no compiler errors or warnings.

This does not prove queued-packet retirement or safe wire-LLID reuse. Ordinary
packets already inside hardware when an LLID changes have no native snapshot
cookie. Actual LLID/PVT queue delivery and physical traffic remain untested.
PVT state is now checked at admission refresh; physical in-flight behavior is
still unproven. The255 cumulative group/cookie IDs,
64 immutable actions,8 source-MAC entries and8 shared DBUF rules retain their
documented limits. Direct PE BCMC, full retirement,
remaining service/management policies and hardware acceptance remain open.

### Unknown multicast flood transactions

The native producer now also captures received UDP multicast sent through
Linux `br_flood`, covering unknown groups and the snooping-disabled/no-querier
fallback. Linux still selects all recipients using multicast-flood flags,
proxy-ARP policy, STP, VLAN, source-port/hairpin and isolation checks. The
existing complete transaction records selected ports and successful final
output recipes, including the CPU-local copy; a failed clone or unsupported
recipient prevents installation of the entire set. A local-only flood can
install the existing owned host-copy action. Broadcast, unknown unicast,
locally originated traffic, IGMP/MLD and reserved01:80:c2 control MACs do not
enter this producer. Membership appearance/disappearance invalidates the old
generation before a new complete flood or MDB decision is admitted.

Port flag changes now close learned lookup before the actual assignment in
netlink/sysfs, including netlink rollback. RTNL already serializes the whole
configuration operation with the learner; the RCU-pinned non-sleeping callback
brackets only the flag publication, not switchdev validation or other sleeping
work. The previously empty query-delay expiry callback now also invalidates
learning: expiration changes `timer_pending()` and can switch a packet from
flooding to MDB selection without another membership event.

Executing retained `rtk_fc_igmp_unknownMulticast_action` confirms16 original
policy cases: it selects profile2's default action and updates software flags
under its lock, and ignores lower programming errors. This is policy evidence,
not a portable original flood bitmap. Native policy remains the actual Linux
bridge selection; it does not open all hardware ports or replace per-port
restrictions with an inferred factory mask.

`test_bridge_multicast_selection.py` now executes45 real Linux selection
cases. `test_bridge_mcast_flood.py` joins the actual `br_flood`, selection and
clone control flow to production capture/learn/FE/NI ownership, checking full
LAN/P6/local copies, local-only groups, isolation/VLAN/flood flags, clone
failure and stale-copy rejection. Actual gate/flag-publication/rollback and
query-delay checks pass, together with complete multicast learning,432 QM
poll cases,40 bridge receive cases,109 parser vectors and unicast/EPON
regressions. Image/modules build passes; the five kernel files in patch225
replay with zero fuzz and match the compiled sources.

No hardware traffic or in-flight retirement was tested. Existing group,
cookie, action and DBUF capacity/quarantine rules still apply to flood groups.
Direct PE BCMC and the remaining EPON four-queue dynamic-COS path remain
separate unfinished work; per-subscriber conversion is described below.

### Per-recipient multicast-to-unicast copies

Linux `maybe_deliver_addr` now records each selected `(port, destination MAC)`
before allocating its copy. The complete transaction supports multiple
subscribers on one VIF or Ethernet port, a preserved multicast recipient from
the broadcast-address placeholder, and mixed ordinary multicast/local copies.
Linux still decides source/hairpin/DAD eligibility. A missing allocation or
output, duplicate recipient or unselected destination rejects the entire
transaction. A group containing only converted copies can complete without a
local-host recipient; zero-recipient groups still remain in software.

The retained target `rtk_fc_abstrSwFlowActGenBySkb` has a three-argument ABI,
unlike the six-argument reference SDK version. Six actual target executions
confirm its ingress/current-DA comparison, per-copy destination replacement
and unchanged source MAC. NI's multicast action table has source-MAC editing,
but no destination-MAC replacement field. Native conversion therefore uses a
distinct quarantined CPU cookie/action per recipient. NI supplies the final
VLAN bytes and original multicast DA; the current owner validates the full
packet, makes the Ethernet header writable, rechecks the generation, replaces
only the intended DA and selects normal physical-port TX. Wireless copies keep
the current VIF endpoint validation and mac80211 key/WME handling. Ethernet,
EPON and per-GEM P6 copies use their normal TX path after the same ownership
checks. The shared FTTR multicast endpoint is not used for converted unicast.

Final output capture explicitly parses the original UDP/IP fields even after
DA becomes unicast. The normal unicast bridge learner keeps its existing
parser behavior. Fixed input priority and dynamic Wi-Fi COS, output VLAN
invariance and MTU checks remain enforced. Repeating an identical complete
decision reuses the existing per-recipient cookies; a changed DA or tag recipe
closes the generation. A related STA peer-route bug is fixed: its unicast PE
queue update now skips multicast flows, whose MCGID names an NI group rather
than a peer destination.

Offline validation executes actual Linux selection/copy functions through
production FE/NI installation and direct-copy receive: same-VIF subscribers,
LAN, VLAN, EPON/P6 output, local/no-local copies, broadcast placeholders,
hairpin DAD, duplicate/missing copies, writable-header failure, repeated
decisions, failed withdrawal and late copies. Six original DA recipes,
existing original wireless recipes,480 QM poll cases,129 actual parser byte
vectors,768 P6 cases,12288 Linux VLAN/WME comparisons, multicast/flood/core/
gate and FE peer-transaction regressions pass. Image/modules and patch226
zero-fuzz replay pass, with84 driver/header/core inputs matching the build.

This provides NI replication plus CPU destination editing and normal TX,
not direct PE multicast-to-unicast or BCMC. Each converted recipient consumes
one additional quarantined group/cookie ID and immutable action slot. Physical
delivery, full queue retirement and safe resource reuse remain unverified;
the overall FTTR service/management rewrite is still incomplete.

### PE multicast recipient admission

The native PE executor now recognizes the reserved POL256..511 cookie space
before its ordinary destination lookup. A coherent, little-endian record names
one immutable recipient, original source, peer and compiled Linux TID policy.
Admission requires a live matching epoch, the correct radio destination, an
admitted peer and hardware key; AP recipients must match that peer's MAC. A
failed cookie cannot fall through to the STA catch-all. Fixed, HeaderA COS and
IPv4/IPv6 DSCP policies are supported, but their values must come from the
current Linux WME/ACM/reserved-TID policy rather than a guessed priority map.

PE retains the cookie in BID headroom. Queued and selected unpublished copies
are checked again after revocation, including sleeping stations. Aggregation
never combines different cookies. Stale selected WPs enter the existing
completion ring: using the single discard slot would deadlock a pause draining
multiple stale requests while its recycler is disabled. BIDs remain owned until
EQ push credit permits their return. Already published PCIe traffic still
requires the existing peer/key drain; closing the table is not physical packet
retirement and does not permit cookie reuse.

The initial consumer ABI asserted its80-byte record and20496-byte table size;
the direct publisher below extends this contract. Linked MIPS
execution covers247 admission/revocation cases, all64 DSCP values and8 COS
values, plus four complete encrypted TX/RPQ cases. The existing publication,
two-peer PS isolation, queue budgets, EQ recycling, STA RX/TX and host-header
checks pass. Firmware is26368 bytes; the conservative stack bound is6848 bytes
within8KiB DMEM. Image/modules build passes. MMIO, DMA and hardware completion
are modeled; this is not a radio or throughput measurement.

The target FC module does not contain the reference
SDK's named `rtk_fc_abstrSwSyncToHw_WifiMcToUc` path, so that reference is not
claimed as target instruction equivalence.

### Radio and FE multicast table lifetime

Each radio now owns a separate coherent recipient table for its complete PE
context lifetime. ARM supplies its address before starting the context; binding
requires the executor's capability signature. FE borrows the pointer under an
explicit per-radio binding and holds the netdev reference. Multicast writers
close both radio epochs under the same short lock that closes learned lookup.
Peer-route changes close admission before updating the destination, including
when no unicast Hash exists. Interface flush/removal also detaches its table.

Binding follows the existing radio NI mutex into FE, never calls back into the
radio and never takes a sleeping lock inside the multicast gate. A supplier
binding mutex joins outstanding callers and detaches all FE borrowers before
NI removal clears its public pointer. Radio teardown first detaches FE, then
drains NI and waits for the PE context-stop acknowledgement, and only then frees
the coherent table. Failed detachment, NI drain or context join retains storage
and permits retry; host callbacks are still withdrawn on teardown failure.
NI restarts retain the allocation and its cookie history.

Production-function tests cover two-radio ownership conflicts, repeated binds,
port/supplier detachment, failed binding/drain/context join and exact release
order. The actual multicast writer test verifies that it closes a bound PE
table. Peer/key/BA retirement, complete learning and M2U,480 QM receive and40
bridge local-delivery cases, callback lifetime, LAN restoration and captured
wireless ingress regressions pass. Image/modules build passes and89 driver,
public-header and generated-firmware inputs match the compiled sources.

Binding alone leaves the tables closed; the direct publisher below opens them
only with a complete learned transaction. The current binding belongs to the first NI VIF on each radio; other
VIF peer setup continues normally without gaining direct multicast admission.
The policy compilation below supplies current Linux QoS and peer identity to FE.
Writing immutable recipient records, completing every key/template transition,
proving replica buffer exclusivity, publishing the NI actions and extending
BCMC remain work. Full packet retirement and safe
resource reuse are still unresolved; no physical-device test was performed.

### Linux wireless multicast policy publication

mac80211 now compiles the policy through its actual `ieee80211_select_queue`
implementation while the radio offload worker holds the wiphy lock. It supplies
all64 DSCP mappings, eight explicit priority overrides, the inline-VLAN result,
WME state and the no-ACK bitmap. IPv4 and IPv6 are evaluated separately and
must agree. Ordinary priority0..7 still uses Linux DSCP classification;
priority256..263 retains its explicit override semantics. The existing first
VIF binding receives the policy together with the exact peer MACID/address and
radio destination. FE rejects a stale generation, active writer, foreign
binding/peer or out-of-range TID. A changed recipe or MACID closes PE admission.

Seventeen kernel publication sites now close old multicast admission before
changing QoS maps, no-ACK policy, WME, ACM, TSPEC admission/deletion, reserved
TID, or fragmentation threshold, including threshold rollback. Only bounded
field changes hold the RCU writer token; allocation, network synchronization,
driver calls and work scheduling run outside it. The policy generation lives
in the built-in bridge core across driver reload. Radio callbacks queue a fresh
snapshot, while subsequent software TX also detects an outdated attempt. Failed
allocation/publication retries; an unsupported policy is remembered only for
that generation. No new policy opens a PE table by itself.

TSPEC airtime accounting, four-address/multi-link operation, fewer than four
queues and software fragmentation are not represented by this direct path;
the compiler explicitly declines them. Control-port EtherTypes that overlap
the accelerated IP/VLAN cases are also declined. No-ACK is recorded here, but
the future PE record encoder still has to apply it to each transmitted copy.

Offline checks compare1179648 actual Linux IPv4/IPv6 classification cases,
covering all ACM bitmaps, reserved TIDs, WME and QoS-map combinations, plus the
existing12288 VLAN/WME cases. Actual no-ACK/QoS-map/TSPEC mutations and the17-site
writer inventory, radio-to-FE stale-policy and ownership failures, complete
multicast/M2U, QM/bridge delivery, peer/key/BA transactions,4224 host TX requests
and BAR construction pass. Re-executed original modules confirm30 wireless
dispatch,32 COS/QoS,12 replica and six destination-edit cases. Image/modules
build passes; patch227 replays seven inputs with zero fuzz/offset, and95 overlay
inputs match compiled sources. MMIO and completion remain modeled.

Policy calculation and FE storage alone do not install a multicast flow. The
following publisher integrates supported M2U recipients; the established CPU
path still handles ordinary multicast and unavailable direct recipients.

### Direct NI-to-PE multicast-to-unicast publication

Complete learned transactions now select CPU18/19 for admitted wireless M2U
recipients. Each recipient reserves an independent immutable cookie from the
same quarantined NI namespace. Its record is written before NI actions and the
group Hash; PE admission opens before the learned tuple becomes visible under
the shared gate lock. Repeated identical complete decisions retain their
cookies. Disagreement closes the generation. Withdrawal clears each owned
record epoch before attempting Hash/cache deletion, even on failure; its
nonzero control tombstone prevents reuse after revocation. CPU receive rejects
cookies belonging to PE recipients.

The MCP2 ABI uses96-byte records and a24592-byte coherent table. Records pin the
actual peer MAC, MACID, key/CAM identifier, AP/STA and Ethernet-template flags,
header QoS shape and Linux TID policy. PE verifies these at ingestion, selection
and before TXBD publication. Key/BA retirement, authorization removal, address
or template identity changes revoke the policy generation before reuse. First
policy publication also withdraws existing CPU-copy rules, so acceleration can
progress after the initial software packet creates a valid radio template.
No-ACK policy reaches both the wireless QoS header and WD, and disables BA for
that copy. Non-WME frames keep ordinary ACK behavior.

Native destination admission checks the original four PDPID mapping cases
(the table address is8 bits), HeaderA routing, RMU enable, per-COS profiles,
exact radio EQ/BID allocation and packet layout, with bandwidth relocation
disabled. Original MCE emits separately edited packet streams into QM; the
native contract requires each RMU CPU output to allocate from that destination's
EQ, retained until its matching RPQ/EQ return. These register and ownership
checks support the implementation, but do not measure physical buffer
exclusivity or hardware replication. That remains an explicit hardware
acceptance item; no device was accessed.

Offline verification executes complete LAN/local/PE installation, repeated
decision reuse, failed withdrawal, tombstone refusal and fixed/VLAN/DSCP recipe
selection. A record emitted by this actual learner is consumed by the linked
MIPS program. PE checks cover264 admission/revocation cases and eight complete
encrypted TX/RPQ/no-ACK cases, including key/peer/header changes and stale
selected requests. Forty route/pool/header faults are rejected and six actual
target FC/NI wireless actions agree after the intentional cookie/source
adaptation. Existing M2U,480 QM/40 bridge delivery, PS/STA/host headers,4224 ARM
requests, BAR, key/BA and ITU/EPON206 route-fault regressions pass. Firmware is
26520 bytes with a conservative6896-byte stack bound. Image/modules build
passes and96 overlay inputs match compiled sources.

Direct acceleration covers the bound VIF's admitted AP/STA unicast recipients
and the STA group extension below, with the existing complete software/CPU path
for unsupported cases.
It does not implement direct BCMC/DTIM, multiple VIF bindings, TSPEC accounting
or prove physical packet retirement. Group/cookie/action quarantine and the
existing finite capacities remain; the overall FTTR rewrite is incomplete.

### STA group transmission and detached-table revocation

Ordinary STA multicast now uses the same complete NI-to-PE transaction while
preserving its Ethernet group destination. A three-address STA sends it to the
associated AP's unicast receiver address, so the existing admitted peer and
pairwise key apply. AP group traffic still uses the CPU/mac80211 GTK/DTIM path.
PE accepts a group destination only with the pinned STA template; an AP group
record is rejected. The actual bridge learner emits both M2U and preserved-group
records which the linked MIPS executor consumes.

The target wireless module's `update_attrib` prefix and full `rtw_make_wlanhdr`
confirm associated-AP receiver selection for group/unicast Ethernet traffic.
They also reveal an intentional adaptation: the original producer fixes the
local adapter as transmitter, while Linux's three-address TX keeps Ethernet SA
in addr2. Native PE now follows Linux for STA Ethernet encapsulation, including
the A-MSDU head; individual subframes retain their own SA/DA. The reference check
executes actual Linux receiver lookup, pairwise-key selection, the relevant
normal ToDS address branch and A-MSDU preparation. It does not claim the entire
Linux TX pipeline or physical association was simulated.

Table detachment now permanently clears every record epoch before releasing
the FE pointer, retaining each nonzero control tombstone. Previously only the
table epoch was closed, which could make an old row current if a retained radio
table was rebound to a restarted FE generation. Port removal, explicit unbind,
supplier stop and fresh binding all use this revocation; ordinary temporary
writer closure still preserves records until their owning flow withdraws.
Tests cover same-table rebind and an FE epoch restart without allowing old
cookies to become valid. This does not release quarantined identities.

Twelve original header executions, six actual Linux receiver/key/header/A-MSDU
cases,267 linked PE admission/revocation cases (including both learner-produced
records) and twelve complete encrypted TX/RPQ/no-ACK cases pass. The existing
STA/AP/host-header, publication, PS, M2U,480 QM and40 bridge-delivery regressions
pass, together with Image/modules and96 matched overlay inputs. Firmware remains
26520 bytes and the conservative stack bound6896 bytes. No device, physical
replication, over-the-air interoperability or throughput was tested.

### AP BCMC high queue and software PN ownership

Normal AP broadcast/multicast DATA now always uses ring5/DMA9/QSEL0x11,
including while no station is asleep. Previously only Linux's
`SEND_AFTER_DTIM` flag selected HIQ; after a station woke, a newer GTK frame
could enter a normal queue while an older PN remained in HIQ. The actual
target `fill_txreq_mdata` also always selects category9 for BCMC DATA.
Management, injected frames, STA traffic and mesh keep their existing queue
policies. The existing DTIM/HIQ-window programming remains in AP startup;
current Linux AP beacon updates do not change the configured DTIM period.

`trace_stock_bcmc_tx.py` executes the target PN producer, complete metadata
producer, cipher/category helpers and both C/XB DATA encoders. Group RA uses
the software PN and GTK lookup; unicast RA selects PTK/hardware PN. The original
BCMC metadata clears both HW_SEC_IV and SW_SEC_IV because the IV is already in
the packet. Native mac80211 hands down an IV-less hardware-encryption frame:
ARM allocates the GTK PN and SW_SEC_IV supplies it through the WD while the
chip inserts the IV. This is an explicit packet-layout adaptation, with no
second PE-side PN allocator. Contrary to the earlier disassembly note, the
executed original wraps a maximum48-bit PN to **zero**, not one; this observation
does not establish a safe key-exhaustion policy.

The new regression executes Linux's actual multicast PS decision followed by
the actual monitor/TX functions, across awake/asleep/awake transitions without
retiring earlier packets. It covers open traffic, all four GTK IDs, PN carry,
IPv4/IPv6/broadcast destinations, and AP/STA/injected/mesh/management controls.
The pre-fix branch fails the first awake-state HIQ assertion. Ninety native
submissions and45 linked MIPS WD/WP/TXBD byte comparisons pass, alongside96
original PN/metadata cases and192 C/XB encodings. Existing BAR, key lifecycle,
STA selection, multicast admission and complete bridge learning regressions
pass. Image/modules build passes and180 overlay C/H/build inputs match the
kernel sources (`artifacts/fttr/bcmc-hiq-kernel-20261107.log`).

These checks validate software decisions, descriptor construction and queue
selection. They do not execute the hardware HIQ scheduler or prove DTIM air
timing, physical PN order, queue drainage or safe resource reuse. AP ordinary
BCMC still uses the owned CPU-dispatch path, matching the factory's wireless
entry while using native NI/QM replication and PE-assisted TX construction.
Direct NI-to-PE BCMC bypass and complete FTTR acceptance remain unfinished.

### Independent UNI mode and ANI VLAN filtering

The service CLI and resident policy accept `filter:VID` in the existing VLAN
field. It configures the original ANI class84 filter while keeping the selected
UNIs in transparent mode4. A numeric VID still selects access mode; `none`
remains transparent with no ANI filter. Runtime service and ownership journals
record `filter_only` so this distinction survives inspection and cleanup.

This separation is required by the original shared multi-service recipe.
`trace_stock_fttr_default.py` now executes
`plr_gponrm_cfg_card_uni_init` and its automatic VLAN selector: the initialized
UNI mode is4 and produces the transparent rule, independently of each flow's
ANI VLAN filter. Merely generating numeric VID100/200/etc. policies would
incorrectly replace every UNI's untagged/access behavior on each service.

Actual AF_PACKET CLI tests compare ANI Create messages and transparent EVTO
rows with the target original producers, cover single and shared Ethernet/VEIP
attachments, read-only repeated adoption, partial failure and restoration.
The resident two-ONU test uses the original authentication/grant-clear startup
and checks journals, VLAN bridge attachment, migration, drift, timeout,
replacement and restart adoption. A run without that grant-clear setup stopped
on stale DBA after replacing the simulated ONU inventory; it is not counted as
a passing lifecycle test. GCC14.4/musl package compilation and staging install
pass (`artifacts/fttr/filter-package-direct-20261107.log`); the global overlay
script encountered an existing LuCI patch conflict, so only the already-synced
FTTR package was built directly. No full firmware image build is claimed here.

The controller still permits one service per ONU. Multiple GEM plans need
shared object ownership, per-GEM Linux ports, complete rollback and sync checks
before importing the full default count2..8 recipe. This change supplies the
correct VLAN behavior for that work, not complete multi-service support or
physical traffic acceptance.

### Multiple local GEM interfaces for one ONU

The native netlink layer now names GEM ordinal1 `fttr-u<ID>` and higher
ordinals `fttr-u<ID>-<ordinal>`. Each interface retains its own GEM VID, serial
alias, bridge, MTU and multicast-member marker. The original formula
`256 + hardware-ONU + (ordinal-1)*64` and Port-ID4090 ceiling are checked before
creating the VLAN; the reserved multicast GEM cannot use this path.

Controller startup already withdraws FE before local interface takeover.
That takeover now enumerates all named GEM interfaces for each ONU and deletes
only matching native parents/groups/802.1Q IDs and ownership prefixes. The
unsuffixed name can still hold a higher valid GEM from an older single-service
controller. A suffixed name must match its exact ordinal. Interrupted Create
without an alias remains recognizable by the native group and exact VLAN.
An ownership conflict returns an error; this enumeration is not an atomic
all-port deletion and earlier confirmed native interfaces may already be gone.

The existing real veth/bridge datapath test now creates GEM ordinals1/2/7/60
for one ONU. It compares original tag push/pop with both directions through a
second isolated bridge, covers no tags/VLAN/QinQ, proves that deleting the
first service preserves the sibling's FDB, and checks all-port restart cleanup,
foreign owners, protocol/ordinal mismatch and legacy unsuffixed recovery.
The resident authenticated migration regression and existing actual FE bridge
and multicast-learning tests pass. GCC14.4/musl package compilation/install
passes (`artifacts/fttr/multi-gem-package-20261107.log`). These are local Linux
and source-level FE checks, not physical NI/P6/ONU traffic acceptance.

This removes the local interface collision; the controller's policy and remote
OMCI transaction still require the multi-service owner/scheduler implementation.

### Outstanding implementation and acceptance

1. FPGA board startup: retained image packaging, automatic loading before
   MiniOLT binding, explicit original post-load register control, baseline
   management envelope, ONU state reads, TX control and FPGA1 DBA/controller
   integration are implemented. Factory-selected optical calibration and separate
   FPGA bootstrap and version-gated FPGA1 factory authentication are implemented.
   FPGA2 now follows the supervisor-selected ASIC relay, including independent
   startup, default DBA, resource/grant numbering, authentication and management
   recovery/migration. Physical startup and traffic acceptance remain open.
2. Four original optical calibration recipes and available identity checks are
   implemented, together with the original UX3326 temperature/reset recovery
   loop and model-specific diagnostic readings. SFP handling, optical alarms, identifying
   the retained FPGA sample's actual optics and physical readiness remain open.
3. Real P6/FTTR frame verification. Native SoC P6, ASIC peer WTG/PON SerDes,
   the dedicated RX/TX endpoint, core/DBA and ASIC hardware startup orchestration
   are implemented. Real ONU-management and service traffic still require
   hardware acceptance.
4. OLT-side discovery/registration, authentication, OMCI, T-CONT/GEM/DBA and
   VLAN/service provisioning. The native MiniOLT OMCI framing/transport
   foundation, management grants and automatic baseline MIB inventory are
   implemented, including a native entity/attribute model from original wire
   layouts. Original global LOID acquisition/result verification is integrated;
   per-ONU comparison/selection, durable SN binding/learning and automatic
   assignment are integrated. PLOAM credential acquisition, factory per-ONU
   policy import and broader capability-based service decisions remain work. Explicit T-CONT binding/readback/local DBA
   and native GEM CTP/IW lifecycle/readback/cleanup are implemented. Native GAL,
   bridge/initial Mapper and basic Ethernet/VEIP attachment are implemented.
   Mapper binding and ANI/GEM-side bridge ports/VLAN filtering are implemented.
   UNI VLAN operation rules, table readback and exact prior-rule restoration
   are implemented. Explicit fresh-MIB selection, direct-bridge service
   orchestration, shared Ethernet/VEIP attachment and local DBA readback/restoration are implemented. Resident
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
   preflight/readback/restoration, explicit CLI and resident per-ONU policy;
   settings mapping and
   actual subordinate capability/behavior verification remain work.
   The independent syncserv JSON path now has original producer/frame evidence
   and native heartbeat discovery/local MAC binding, bounded fragments and
   Flag1 Wi-Fi read/report completion and explicit write/readable-field
   comparison plus pinned registration and asynchronous resident Wi-Fi policies;
   authenticated identity/capability, real readiness/credentials, complete
   configuration transactions and physical power-loss recovery remain work.
   Durable per-revision write intent and conservative read-only restart recovery
   are implemented for the resident syncserv path.
6. FE/PE acceleration and stale-flow invalidation, then hardware acceptance
   with real downstream gateways and a recorded interoperability scope.

No optical startup, ONU registration, service traffic or hardware acceleration
has been verified for this branch. No placeholder online state is exposed.
