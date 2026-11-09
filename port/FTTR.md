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
up to seven policies with distinct slots per exact serial or wildcard set;
exact-serial rows replace the complete wildcard set. FPGA1 remains limited
to its single hardware data slot. Multiple rows require shared UNIs and
`filter:VID`, with distinct VIDs and at most one multicast-owning row.
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
this syncserv diagnostic exposes no reboot or upgrade command. The separate
resident OMCI commands are described below.

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
preserved per packet with all four or eight EPON queues enabled, provided
every COS gives the same final VLAN bytes. The PUC mode applies the queue
width independently of the other replicas; the full three-bit COS remains
available to LAN/P6/radio recipients. The original metadata, NI action and
PUC contract supporting four-queue admission are detailed below.
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
  preserved COS and disabled-queue refusal; the later four-queue extension
  also checks dynamic COS at the highest instance in each mode.
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

### EPON four-queue dynamic COS

The previous learner rejected raw wireless priority whenever EPON used four
queues. It now retains incoming COS in that replica and requires all reachable
queues (`0x0f` for4Q, `0xff` for8Q) in the admitted PVT snapshot. It still
compiles all eight possible input priorities through the actual VLAN path;
different final tags, a missing queue, stale registration or an invalid
recipient keep the complete set in software. No COS-specific Hash or common
COS rewrite is added, so other recipients retain their own priority semantics.

The reference `aal_puc.h` explicitly defines mode1 as
`VoQID[6:0]={HdrA.LDPID[4:0],HdrA.CoS[1:0]}` and mode0 as
`{HdrA.LDPID[3:0],HdrA.CoS[2:0]}`. Target `ca-ne.ko` mode setters/getters
control PON0x804c accordingly. More than a reference comment supports this:
target EPON `us_pon_sid_mapping_adding.isra.0` produces all eight COS values
with destination32+software-index, including index31, and the actual FC-to-NI
replica chain retains those values in fixed-COS actions. Our dynamic action
produces the same Header A destination/COS for each arriving packet, using
the original NI COS-retention command. Existing native route admission still
checks all four PDPID8 cases, deep-buffer destination and QM0x68f8 PON offset;
PUC publication checks mode and every enabled PVT queue identity.

`test_epon_mcast_puc.py` now takes the original FC/NI modules and the cited
PUC header explicitly. For all32 four-queue and16 eight-queue instances and
all eight COS values, it compares768 fixed/preserved native actions with the
actual target NI encoder, using complete target FC-generated recipes. Actual
native CPU TX headers and target PVT decoding agree with the documented PUC
bit slicing. Existing960 admission faults and7680 field decodes remain passing.
The PUC bit slicing and indirect completion are modeled hardware boundaries;
this is not a physical queue-delivery or NI/QM timing measurement.

Complete capture/selection/install tests cover36 mixed EPON/LAN/P6/radio
scenarios at instances0 and15/31, every missing4Q/8Q queue, and repeated
COS0..7 packets reusing the same complete flow. Target EPON SID/FC/NI replay,
206 route fault/retry cases,12288 actual Linux VLAN/WME comparisons, snapshot
and bridge regressions pass. Linux6.18.55 Image/modules build passes
(`artifacts/fttr/mcast-epon-cos-kernel-20261107.log`). No physical device was
accessed; all existing retirement and resource-quarantine limits remain.

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
Direct PE BCMC remains
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

### Shared GTK PN across software CCMP and PE publication

Before direct AP BCMC can use PE, its PN must share ownership with CPU
transmissions. A second independent PE counter would collide with ARM's
ordinary GTK TX and mac80211's software-encrypted management frames. The
native GTK now binds to one shared counter per CAM generation when PE is
active. CPU hardware-encryption requests carry the binding generation in
request word30, after all ten DMA address pairs and the FTTR generation.
PE assigns the PN only at final publication, after TXBD credit is available.
Ordinary PTK/hardware-PN and non-PE descriptor behavior remain supported.

`rtl9617c-key-pn.c` serializes ARM callers under an IRQ-safe lock. ARM and
PE1 use a two-participant Peterson handshake with full barriers in coherent
memory. PE never waits for ARM: it yields the publication turn. ARM waits
at most1000 one-microsecond checks and returns an error if the peer does not
release. Published counters use two banks with a commit sequence, allowing
readback of the last committed value if a writer stops before publication.
Per-radio records occupy0x1e900..0x1fd00 of the existing context allocation;
the actual engine DMEM stack is a separate allocation. Firmware advertises
`PN01` independently of the FTTR and multicast capabilities.

The actual mac80211 CCMP software encryption and fast-IV paths call the same
allocator. Binding snapshots the software PN while holding that allocator's
lock, covering an encryption sender racing key installation. Unbinding closes
the generation before waiting for a current allocator, transfers the final
counter back to software on success and drops the borrowed pointer. A timeout
also drops that pointer, because Linux may free the key despite the driver's
error, but poisons the key's software fallback rather than risk an old PN.
Delayed requests retain their generation and cannot consume a reused CAM's
counter. Binding generations never wrap; exhaustion is explicit failure.
Key install/DMAC failures keep CAM ownership and restore the prior desired
role through existing cleanup. GTK teardown, including STA release, detaches
the binding before CAM reuse. Already published WPs still wait for real RPQ.

The existing software-discard worker now includes non-FTTR GTK requests, so
revocation returns their DMA/skb ownership without fabricated ACK/rate/sequence
feedback. `get_key_seq` reads the shared PN; it retains the current Linux
`ieee80211_get_key` consumer's LSB-first TX byte order. The key-sequence struct's
RX-oriented byte-order comment alone is not used to change that behavior.
Debugfs reads the shared value and rejects setting a bound/poisoned counter;
it cannot silently modify only the stale software shadow. The target factory
48-bit wrap-to-zero behavior is retained, without claiming a safe key lifetime
at exhaustion.

Validation on2026-11-07 includes60000 concurrent ARM/PE allocations plus
concurrent committed reads,640 carry/wrap/CAM-generation cases,32 alternating
actual mac80211 software-CCMP/PE encryptions, real radio bind/read/unbind,
debugfs refusal and failed-disable software rejection. Twenty-one linked MIPS
publication cases cover zero TXBD credit, ARM contention, revocation during
admission, paused drain and same-CAM replacement. Ninety actual BCMC monitor/TX
submissions yield45 linked PE publisher/PN/WD/WP/TXBD comparisons matching the
ordinary ARM descriptors. Target factory96 PN/metadata and192 C/XB WD cases
pass. Key install/delete faults,16448 RPQ batches,257 software-discard batches,
4224 request-copy cases,69 FTTR request generations,249 direct-NI FTTR drain
cases,265 multicast cases plus12 encrypted TX/RPQ cases and1024 MPDU/2048 BID
returns pass, alongside bridge, AP/mesh, BAR, pause and binding regressions.

Firmware is28960 bytes, SHA256
`daed79fc25adb012c752118d560ecdee456f2300eb5d6151a8f17ff6264061d8`;
the conservative sum of all function stack frames is6960 of8192 bytes.
Linux6.18.55 Image/modules build and six overlay/firmware input comparisons
pass; patch230 replays its five kernel inputs with zero fuzz/offset and
identical results (`artifacts/fttr/key-pn-kernel-final-20261107.log`).

This completes the shared PN prerequisite, not AP direct NI-to-PE BCMC.
Direct group admission/template/key selection and HIQ/DTIM integration are
still required. Software-encrypted frames can already have allocated a PN
before reaching the driver; these tests do not prove cross-queue air ordering.
No physical CCI/PE handshake, encryption, DTIM scheduling or RF traffic was
tested. Existing full-retirement and quarantined-resource limits remain.

### Shared non-QoS sequence across Linux and PE

The target `rtk_wifi6.ko:rtw_get_sw_seq` uses station bank9 for non-QoS
(including a null attribute) and the selected TID bank for QoS, incrementing
and masking to12 bits. Native mac80211 instead owns the next interface
sequence: normal non-QoS, management and group-QoS frames use
`sdata->sequence_number`; unicast QoS keeps its per-TID allocator. The PE's
previous per-peer/TID non-QoS allocation would split that Linux sequence
space when direct AP BCMC is added.

The native single AP/STA interface now binds a20-byte shared record at radio
context`0x1fd00`. ARM serializes software senders and uses the same bounded
ARM/PE Peterson handshake as the shared GTK PN implementation. Both actual
Linux assignment sites (normal TX before fragmentation and fast TX) use it.
CPU Ethernet conversion obtains a sequence from that interface before
mapping and carries the assigned value in request word31. Raw Linux frames
retain their already assigned sequence; QoS per-TID operation is unchanged.

NI ingestion stamps the interface generation in BID word7, before header
editing. Selection and final publication reject withdrawn generations. The
publisher allocates only after TXBD credit is available; if GTK PN contention
delays publication, it retains the assigned sequence and checks the BID
generation again on retry. Request word31 survives the main-loop CPU copy;
NI constructs its request directly in that publish slot. Header conversion
preserves BID metadata at bytes28..31. The12-bit sequence wraps as on Linux;
the31-bit interface generation never wraps. Firmware advertises SEQ1 in
mailbox214, which ARM requires before enabling this path.

Unbind revokes direct multicast admission and the shared generation, then
returns the next sequence to Linux after the peer leaves its critical
section. Timeout detaches the borrowed allocation and poisons software
fallback for that interface; a stale Linux counter must not resume sending.
Interface creation failures detach a successfully bound record, and a
failed bind does not proceed to role creation. No multi-VIF support is
implied by the single per-radio record.

Offline checks execute240 target original sequence calls,24576 concurrent
ARM/PE allocations,4100 actual normal-Linux/PE alternations and32 fast-path
iterations; they cover wrap, exclusions, radio bind/unbind, timeout and
generation exhaustion. `test_pe_nonqos_sequence.py` executes24 linked-MIPS
EPP/header/RPQ/EQ, CPU request-copy, credit/ARM/PN stall and withdrawal cases.
The existing QoS sequence/offset test is retained with an explicit original
module input; request construction covers8448 raw/encap/layout/key cases.
The multicast, GTK PN, FTTR generation/drain, BCMC, BAR and interface
lifecycle regressions pass. Test emulators now use the production's separate
PE stack rather than placing it over the radio context's new shared record.

Firmware is29400 bytes, SHA256
`a799e92e714506dfa6669e68cd3176f3edbfbc00a214149cec45f8cdd273b0a0`;
the conservative sum of all function frames is6984 of8192 bytes. Image/modules
build, seven overlay input comparisons and zero-fuzz/offset replay of the
three patched kernel inputs pass. Local evidence is in
`artifacts/fttr/sequence-kernel-20261108.log` and
`artifacts/fttr/sequence-source-identity-20261108.json`.

This resolves another prerequisite for direct AP BCMC. Default-GTK selection,
immutable group template/admission and direct HIQ/DTIM integration are still
required. These offline checks do not prove physical interconnect ordering,
RF ordering or full upstream packet retirement; hardware IDs stay quarantined.
No physical device was accessed.

### Main alignment with independent radio TX ownership

The worktree incorporates local main`2ad19d9` after the shared-sequence
increment. Its guarded AP preload,2048/1024 NI request pools, per-channel
hardware WP tags,4096-entry completion ring,512KiB TX contexts, bounded
64-MSDU admission and separate NI payload builder are integrated. The
original main rationale and validation limits are retained in
`docs/wifi-downlink-retained-fixes-20261108.md`; this integration did not
repeat or extend its physical performance claims.

FTTR's generation, immutable multicast cookie, shared GTK PN and non-QoS
sequence checks remain in front of publication. The new NI payload builder
retains the FTTR branch's STA source/RA/DA and no-ACK semantics and uses the
shared interface sequence for non-QoS. Hardware tag availability now precedes
sequence/PN allocation, just like TXBD credit. All owner/rate/TID/grant and
completion accesses, including ARM forced teardown and peer/key waits, use
the expanded tables.

Two integration errors were reproduced and fixed. A selected NI request
withdrawn before publication has no WP tag: allocation initializes its cookie
to an invalid sentinel, and final EQ recycling returns its software credit
without releasing another request's tag0. CPU unpublished-discard reports
already contain software IDs and therefore bypass hardware-tag translation;
otherwise the FTTR worker cannot return its skb/DMA/reference ownership.

Linked-MIPS checks cover6144 submissions/2048 in flight and5120/1024,
random RPQ order, tag4094/zero wrap,4096 completion wrap and EQ credit failure.
The251 FTTR NI cases include high request IDs, an unpublished completion
at the wrap boundary and preservation of another CPU tag0 owner. The257 ARM
discard batches verify that unrelated tag mappings stay untouched. Existing
265 multicast plus12 full encrypted cases,24 non-QoS cases,21 GTK publisher
cases,69 CPU FTTR generation cases,1024 MPDU/2048 BID RPQ cases, full payload
and opaque host-DMA cases pass. ARM RPQ16448 cases,16896 request recipes,
preload/STA/mesh/key/NI lifecycle, forced teardown (including the dedicated
discard slot), bridge learner and shared PN/sequence regressions pass.

The integrated PE_UPLINK firmware is30740 bytes, SHA256
`9f5052764cfae4e98fcb36978c6a902e2533c63396f14aa6250d9ee01aed9b2a`;
the conservative sum of all stack frames is7048 of8192 bytes. The alternate
direct firmware also builds (14200 bytes). Image/modules build and66 radio
source/header comparisons pass; local evidence is in
`artifacts/fttr/main-align-kernel-final-20261108.log` and
`artifacts/fttr/main-align-identity-20261108.json`. Ordinary kernel modpost
MODULE_DESCRIPTION warnings remain. No physical device was accessed.

This alignment preserves the pending direct AP BCMC implementation work.
Independent radio request/tag reuse follows RPQ/EQ ownership; it does not
prove upstream FTTR multicast/GEM retirement or justify releasing their
quarantined hardware IDs.

### Default GTK selection invalidates native multicast

The target `rtk_wifi6.ko:update_attrib` inlines the security selection:
802.1X group recipients use `dot118021XGrpKeyid`, while unicast uses key0;
legacy authentication modes use the configured default index. The bounded
original instruction range`e343c..e3960` is executed for32 combinations
with different group and legacy default indices. Authentication, packet
parsing and encryption are outside that range and are not claimed here.

Linux selects group DATA from the current link's `default_multicast_key`.
Selecting an already installed GTK does not call the driver's `set_key`, so
installation/removal callbacks alone cannot revoke a cached TX identity.
Patch231 brackets changes to that pointer with the existing native Wi-Fi
policy writer: both radio PE epochs and learned FE admission close before
publication, and the radio receives its policy-change callback after the RCU
writer token ends. Selecting the identical pointer does not churn the gate.
Clearing the default, selecting a different installed slot, and replacing a
key in the same selected slot all change pointer identity and invalidate.

`test_mcast_default_key.py` executes66 actual Linux setter/selector cases
through the real core dispatch and native FE/PE change callbacks. It checks
all four indices, explicit links and the default link, legacy interface-key
precedence, unicast-only selection, clear/missing keys and same-slot pointer
replacement. At the publication instruction it asserts both PE tables are
closed, the FE tuple count is3 and the writer is active; the callback runs
afterward. The1,179,648 policy classifications, key install/cleanup and60,000
shared-PN concurrency regressions pass. Image/modules build and zero-fuzz,
zero-offset patch replay match the prepared kernel; ordinary modpost
MODULE_DESCRIPTION warnings remain. Evidence:
`artifacts/fttr/default-gtk-kernel-20261108.log` and
`artifacts/fttr/default-gtk-identity-20261108.json`.

This closes the default-key notification gap. A direct AP BCMC producer must
still snapshot/validate that selected GTK, publish its immutable template,
and implement group admission and HIQ/DTIM lifecycle. No direct group
producer or physical validation is claimed by this patch.

### Direct AP BCMC through native NI and PE

The complete multicast learner can now publish AP group recipients to the
bound radio's NI/PE path for the existing IPv4/IPv6 UDP multicast learner.
The PE encoder also handles broadcast DA in its offline vectors; the current
FE classifier/learner still excludes broadcast ingress, so automatic broadcast
offload is not claimed. It remains a
single AP VIF per radio: a group recipient uses MACID0, a non-QoS three-address
FromDS header, the current default GTK (or open-network mode), basic rate,
DMA9/QSEL0x11 and HIQ. It does not borrow a station's PTK, BA session or
per-station power-save grant. Existing AP/STA per-user copies keep their
separate behavior.

mac80211 exports a wiphy-locked snapshot of its actual
`deflink.default_multicast_key`; it rejects unsupported ciphers, tainted or
software-only keys and unbound PN state. No key pointer is retained after
snapshot. The radio publishes a28-byte template at context`0x1fd40`, including
nonwrapping template generation, interface sequence generation, GTK CAM/key
index and PN generation, basic rate and AP address. The existing96-byte
immutable replica record uses control bit15 to name BCMC and stores its
captured template identity in the otherwise unused DSCP area. Capability
advances to MCP3 (`0x4d435033`). A record is accepted only if its source,
destination radio, group DA, template, key, interface and MAC identity match.

NI ingress retains the immutable cookie and interface/FTTR generations in
BID headroom. Accepted BCMC shares queue node0 with the discard queue, but
requires an explicit valid BCMC record; failed ingress is permanently marked
invalid and cannot revive when a template reopens. Selection creates a
MACID0/HIQ request and carries word30 GTK generation through PE's actual
publish queue. The final publisher obtains the common interface sequence and
GTK PN only after TXBD/tag credit, reusing an assigned sequence across PN
contention. No A-MSDU or A-MPDU is formed for this recipient. A real RPQ
releases the WP, and EQ PUSH returns each BID; FTTR source6 accounting still
spans that final return.

Group-only traffic can initialize the NI supplier without a unicast peer or
BA session. Partial initialization retains the netdev/resources and retries
cleanup before another attempt. A last unicast peer's withdrawal preserves
an enabled BCMC supplier; full interface withdrawal still closes and drains
both. Default GTK, basic-rate/beacon changes and policy writers invalidate
old recipes. mac80211's multicast-to-unicast switch is now also bracketed by
the writer token (patch232), and direct BCMC is declined while that switch is
enabled so its software replication policy remains effective.

AP stop/start and software scanning close the template, join the PE
publisher, issue the original HIQ-MBSSID drop selector8 for MBID0, then wait
for MACID0 completion before reuse or RF movement. The ordinary four-AC
`drop_peer` command does not drain HIQ and is not used for this step. A
failed pause/drop/completion keeps BCMC retirement closed and blocks a new
template; AP start retries before role reconfiguration. HIQ window50,
port0 DTIM registers, beacon template and hardware broadcast buffering come
from the existing original-backed AP port setup. This change adds the
hardware recipient and its lifecycle; it does not establish physical DTIM
or on-air ordering.

Offline validation executes the exact record emitted by the complete
bridge/FE learner in linked MIPS EPP→HIQ→RPQ→EQ code. The98 BCMC cases cover
open/CCMP, all four key IDs, low/high CAM slots, IPv4/IPv6/broadcast DA, all
input COS, compact/full HEADER_A, exact QM capacity and one-byte overflow,
invalid source/destination/flags, stale epochs/templates/keys/interfaces,
PN contention, PS-grant exclusion and refusal followed by reopen. Actual
Linux/radio template functions cover snapshot and publication failures,
unchanged-template idempotence, partial NI restart, retirement timeout and
generation exhaustion. AP/STA scan, AP/mesh lifecycle, group-only worker,
last-peer and global release tests cover the integration boundaries.

The target factory96 PN/metadata and192 C/XB descriptor cases pass;
`test_bcmc_drop.py` compares the real native MBID0 command byte-for-byte with
original `h2c_pkt_drop`, including no receive/done ACK. Existing multicast265
plus12 encrypted cases,251 FTTR NI drain cases,69 CPU PE-generation cases,
24 non-QoS cases,21 PN-publication cases,6144 submissions/2048 in-flight
ownership stress,257 ARM discard batches,16448 ARM RPQ and16896 request
recipes pass, with shared PN, key, policy, binding and lifecycle regressions.

Final PE_UPLINK firmware is31832 bytes, SHA256
`2adb0ca855e300917f9f29686a596b0ac1f7afa751e0ad7ff8bdb0c9bb3e7345`;
conservative stack sum is7120 of8192 bytes. Alternate direct firmware builds
at14200 bytes. Image/modules build,87 relevant source/header comparisons and
zero-offset/fuzz patch replay pass. Local evidence is in
`artifacts/fttr/bcmc-kernel-final-20261108.log`,
`artifacts/fttr/bcmc-identity-20261108.json` and
`artifacts/fttr/bcmc-stock-20261108.json`. Ordinary modpost missing
MODULE_DESCRIPTION warnings remain. No device was accessed or flashed.

Direct AP BCMC implementation is now present for this supported single-VIF
open/CCMP path. Multi-VIF, broader cipher/policy support, TSPEC accounting,
physical crypto/DTIM/air ordering and full upstream packet retirement remain
unverified or incomplete. In particular this does not permit reuse of the
quarantined FTTR multicast groups/actions/cookies or GEM identities.

### AP GTK deletion drains HIQ before unlink

A follow-up lifecycle test reproduced a remaining gap: AP GTK deletion
waited for key ownership without sending the HIQ drop command. A packet
waiting for DTIM could therefore keep deletion and deferred cleanup retrying.
Both normal GTK deletion and failed/aborted MACID0 key cleanup now close the
BCMC template, join PE publication, issue the original MBID0/selector8 drop,
and wait for MACID0 completion before changing the DMAC key map or releasing
CAM. CPU-only HIQ traffic takes the same path even if direct BCMC was never
admitted. A late completion after timeout does not skip clearing the retained
retirement state on retry.

While any MACID0 key unlink remains pending, the AP offload worker keeps
BCMC closed; it cannot republish the newer default GTK over an unresolved
hardware key map. Successful synchronous deletion or deferred DMAC unlink
queues a fresh snapshot, including deletion of a non-default GTK. Failed
operations retain the old CAM allocation and current desired role, so a newer
installed GTK survives cleanup.

The actual native key callbacks reproduce the pre-fix HIQ stall and pass
successful deletion, drop failure, repeated cleanup failure and newer-key
preservation. Actual template stop tests cover CPU-only traffic and timeout
followed by late completion; worker tests verify publication is suppressed
until unlink completes. Existing key/STA/mesh/NI, shared PN,257 ARM discard
batches and98 linked-MIPS BCMC cases pass; native HIQ command still matches
the target original producer. Image/modules and changed-source identity
checks pass (`artifacts/fttr/gtk-hiq-kernel-final-20261108.log`,
`artifacts/fttr/gtk-hiq-identity-20261108.json`). PE firmware is unchanged.
The tests model firmware response and RPQ completion; physical DTIM/crypto
and upstream packet retirement remain unverified. No device was accessed.

### Per-channel PE publication and pause-time completion progress

The target `rtk_wifi6.ko:phl_tx_pcie` processes separate pending WD rings
per DMA channel and continues to other channels when one has no TXBD credit.
Executing its complete scheduler with explicit resource-query and WD-handler
boundaries confirms this for each of ten channels. The native PE instead
stopped at the first blocked entry in its shared15-request staging queue;
HIQ waiting for DTIM or a contended GTK allocator could prevent ready ordinary
channels from publishing. The linked-MIPS pre-fix reproduction fails with
channel0 blocked and channel1 still idle despite available credit.

The native publisher now scans the bounded staging prefix, retains FIFO
order within each channel, and allows other channels to use available
TXBD/tag credit. Sequence/PN contention blocks only that channel for the
current turn. Stale requests are still checked and returned even behind an
older blocked request. After publication, live requests are packed backwards
against the original write cursor, copying all32 words including FTTR,
GTK and already assigned non-QoS sequence metadata. No second queue or new
shared ABI is introduced; normal turns still complete at most eight entries
and the scan remains bounded by the15 pending slots.

The pause-drain path also processes actual RPQ and EQ recycling before
retrying publication. Previously it skipped both while waiting for its
staged requests, so tag exhaustion could prevent consuming the very report
needed to complete a pause. RPQ completion does not prematurely free an NI
tag: the existing recycler still waits for the last BID's EQ PUSH.

`test_pe_channel_progress.py` covers the ten target scheduler cases and32
linked-MIPS cases: six blocked native channels at three wrap positions,
per-channel order and queue compaction, real CPU RPQ tag release during
pause, stale entries behind blocked channels, a compacted NI BCMC request
with PN contention and retained sequence, and NI RPQ followed by withheld
then restored EQ credit during pause. Existing main-loop publication,
98 BCMC,21 GTK PN,24 non-QoS,69 FTTR CPU-generation,251 FTTR NI-drain,
265 multicast plus12 encrypted cases, PS, ownership stress and retained-WD
checks pass. IO, resources and completion arrival are modeled; no physical
throughput, latency or RF behavior is inferred from these checks.

Final firmware is31964 bytes, SHA256
`8303c7f4fe2995a92d67e38ada3bcc5ac9b9e267dd5bf96eaa588cf11c049052`;
conservative stack sum is7208 of8192 bytes. Image/modules build and firmware
header identity pass (`artifacts/fttr/channel-progress-kernel-20261108.log`,
`artifacts/fttr/channel-progress-identity-20261108.json`). The alternate
non-PE_UPLINK firmware remains14200 bytes. A finite staging queue filled
entirely with one blocked channel still backpressures new arrivals; this is
not a claim of unlimited per-channel buffering. Full upstream FTTR retirement
and physical acceptance remain incomplete. No device was accessed.

### NI restoration failures retain ownership

The radio NI restore path previously discarded the return values of all
eight arbitration-table writes and both EQ/EPP AXI-attribute writes. It could
restore the ES enable bit, reopen TX and unmap state despite an indirect
access remaining busy. The test-only VP release path likewise ignored its
queue-base reload timeout. The actual production release function reproduces
this failure under injected IO stalls.

Restoration now returns the first error, including the normal and forced
radio-teardown callers. The ES enable bit is restored only after all indirect
writes complete. Failed cleanup keeps NI ownership and MMIO mappings for
retry and leaves admission closed. Before retrying, both indirect engines
must finish their old access before any staging data is overwritten. Normal,
forced and upstream VP restore paths also wait for an earlier queue-base
reload before modifying its bases; a failed reload does not release its
allocation. These checks do not interpret an access-complete bit as packet
retirement: EPP, WP/RPQ and BID/EQ ownership checks remain separate.

`test_ni_restore.py` executes42 native normal/forced teardown scenarios across
both radios: each of ten indirect writes, an applied-but-unacknowledged
write, repeated still-busy retry and VP reload failure. Successful retry
restores the captured arbitration/attribute/EPP state while preserving the
other CPU/LAN/PON ES bits. Optional target `ca-ne.ko` execution covers16 AXI
attribute entry/completion cases and confirms data/access ordering and its
300-read timeout; the original helper only logs that timeout and has no
error result, whereas native cleanup propagates failure. Eight actual uplink
start/stop cases additionally check withheld reload completion and unchanged
base words until retry succeeds.

BID partial teardown, unrecycled-WP release, BCMC template, FE/radio binding,
NI retirement, offload transaction and STA lifetime regressions pass.
Image/modules build and changed-source identity pass; ordinary modpost
MODULE_DESCRIPTION warnings remain. Local evidence is in
`artifacts/fttr/ni-restore-kernel-20261108.log` and
`artifacts/fttr/ni-restore-identity-20261108.json`. PE firmware is unchanged.
The IO model does not prove physical drain, and full upstream retirement and
quarantined FTTR ID reuse remain unresolved. No device was accessed.

### Resident remote ONU reboot

`fttrctl STATE_DIRECTORY onu-admin ID SERIAL16 reboot` now sends the factory
ONU-G reboot through the owning `fttrd` control socket. ASIC and FPGA1 target
libraries execute `plr_gponrm_onu_reboot` → `plr_gponrm_pr_onug_reboot` →
`plr_gponomci_onug_reboot` → `plr_gponomci_reboot_meadapter`: internal action15,
class256/instance0, baseline operation0x59 with a zero condition/body. The
factory encoder submits48 bytes including the CRC reservation; native OMCI
uses the same first44 bytes and its existing link framing/CRC implementation.
`trace_stock_fttr_reboot.py` executes both factories, including failed
transaction allocation/transmission, and compares exact bodies at three ONU
IDs and three transaction IDs.

The actual factory ACK functions expose a result bug: their common parser
only copies the status byte for selected response opcodes0x24..0x32. Reboot
0x39 leaves its caller's zero-initialized result untouched, reporting success
even for a nonzero response status. Fourteen executed ACK cases confirm this
in both libraries. Native `omci-me` checks byte8 and reports a remote rejection
as `EREMOTEIO`; this defect is deliberately not reproduced.

The controller validates local credentials, the supplied serial and the live
registration, closes FE admission, removes local business VLANs and withdraws
business DBA. It cancels prior per-ONU jobs while retaining the management
grant needed to transmit OMCI. FPGA1 has global OMCI setup and its per-ONU
management close performs no DBA IO; the common session ownership still
tracks that lifetime. The shared event loop sends exactly once,
checks identity before send and completion, and handles ACK, malformed reply,
two-second timeout, replacement and shutdown. The generic job marks reboot
non-retryable. A pending reboot excludes conflicting local administration and
migration; a second reboot while awaiting departure is rejected. Client
exit does not cancel or repeat a transmitted request. Wrong-TCI and late ACKs
cannot complete a different transaction or reopen service.

On completion the controller withdraws the management grant before reopening
shared forwarding for other ONUs. An unconfirmed withdrawal is fatal and keeps
forwarding closed. An accepted or uncertain request remains `reboot-wait`
with no business port/grant until observed departure/replacement or explicit
`onu-admin ... resume`. Departure starts the normal inventory/authentication
and full service validation before admission. A definite remote rejection or
unsent request can return to fresh inventory. The CLI reports
`command_accepted:true`, `false` for a definite remote rejection, or `null`
when no acceptance can be established, and always `restart_verified:false`.
Runtime status adds `reboot_sent`, `reboot_accepted` and `reboot_error`; fresh
inventory clears this previous registration's result. If registration drops
and returns between polls, the wait deliberately requires explicit resume;
there is no claimed positive restart detection or durable reboot receipt.

The actual daemon/CLI AF_PACKET/veth tests cover ACK, timeout with one send,
rejection, malformed ACK, wrong/late TCI, serial replacement, disconnected
client, shutdown, FE withdrawal failure and management cleanup failure.
The healthy peer retains its VLAN, and departure/resume goes through fresh
LOID authentication before service. ASIC and FPGA1 framing are covered;
existing ONU-admin, dispatch and migration regressions and native/aarch64-musl
builds pass. Reports: `artifacts/fttr/reboot-stock-20261108.json`,
`reboot-resident-20261108.json`, `reboot-resident-fpga1-20261108.json` and
`reboot-{admin,move}-regression-20261108.json` in the same local directory.
These use modeled ONU/MDIO/FE IO and prove no physical restart, packet drain
or hardware-ID reuse. No device was accessed.

### Resident Software Image download, activation and commit

The resident controller now implements the target relay's Software Image
(class7, slots0/1) management through separate explicit commands:

```sh
fttrctl STATE_DIRECTORY onu-upgrade ID SERIAL16 download IMAGE_FILE
fttrctl STATE_DIRECTORY onu-upgrade ID SERIAL16 activate
fttrctl STATE_DIRECTORY onu-upgrade ID SERIAL16 commit
fttrctl STATE_DIRECTORY onu-upgrade ID SERIAL16 abort
fttrctl STATE_DIRECTORY onu-upgrade ID SERIAL16 check
```

`download` reads both image slots with mask0xf000 and chooses the first slot
that is neither committed nor active. Start Download0x53 requests31 sections;
the ONU may negotiate1..31. Each section contains31 image bytes, with zero
padding only in the final partial section; sequence numbers restart at zero
for every window. Ordinary sections use0x14, the window/final-image last
section uses0x54 and requires a matching ACK/section. Confirmed bytes advance
only on that ACK. An explicit rejected window retransmits the entire window
at the same offset, at most twice; old TCI replies do not acknowledge retries.
End Download0x55 carries the factory non-reflected CRC32 and exact image size.
A rejected End Download waits4s before retry, at most120 times, to match the
factory flash-write waiting path. No blocking sleep is used in the resident.

Every acknowledged image request has a30s deadline, corresponding to the
original300 ticks of the100ms timer task. Missing or malformed replies fail;
they are not treated as remote rejections that authorize retransmission.
After End Download succeeds, a fresh class7 read must report valid, inactive
and uncommitted before `upgrade_verified` becomes true. `activate` independently
rereads both slots and selects the first valid inactive image; `commit`
selects the first valid uncommitted image. These match384 executed factory
selection combinations across ASIC and FPGA1. Activation ACK only proves
request acceptance; commit additionally reads back valid/committed state.
Download never implicitly activates or commits. Native validation also
rejects malformed booleans, oversized negotiated windows and wrong section
ACKs rather than accepting corrupt state.

Version3 of the local credential-checked Unix control protocol carries
exactly one SCM_RIGHTS descriptor for download and none for other operations.
The controller accepts a nonempty regular file within the32-bit wire size,
copies it once and checks size/mtime/ctime stability; all packets and retries
use that immutable snapshot. It never opens a path supplied in a datagram.
Other descriptor counts, truncated messages, pipes and empty images reject
before FE/DBA changes, and received descriptors are closed on every path.
The CLI waits up to an hour for the local result; CLI disappearance or timeout
does not abort the remote transaction. This wait is not a network retry.

Before OMCI image selection/transmission, the owning daemon checks the live
serial, withdraws FE admission and business VLAN/DBA, and cancels other jobs
for that ONU while retaining management connectivity. It checks identity
before every packet and completion, and yields between section sends to
service other ONUs. Concurrent management of a different ONU may close but
cannot reopen shared forwarding while an image or reboot transaction is
pending. The current implementation holds shared FTTR acceleration closed
for the command duration; other peers retain their VLANs. On finish it
releases the management session, frees the snapshot and reopens shared
forwarding only after cleanup succeeds. A cleanup error remains fatal/closed.

The affected ONU stays in `upgrade-wait` with business withdrawn. Separate
activation/commit commands can run there after fresh identity/image reads.
A durable upgrade record keeps the same serial held across departure, slot
movement and controller restart. Explicit `onu-admin ... resume` releases that
record and starts normal inventory/authentication before services resume;
a replacement serial follows its own record, if any. Local `abort` stops
further transmission and frees ownership; it does not invent an unsupported
remote flash rollback/abort message. Status reports action, stage, selected
image, total/confirmed bytes, accepted/verified and error. The CLI's
`completed` describes only the requested command and always reports
`boot_verified:false`.

`trace_stock_fttr_upgrade.py` executes both original image producers, exact
wire encoders, partial/full windows, selection, CRC,12 window failure/retry
cases and End Download retry boundaries. `test_fttr_upgrade.py` drives the production C state machine in70
scenarios, including all31 negotiated windows, multiwindow byte identity,
full-window retries, all120 end retries, timeout, malformed responses,
readback failure and cancellation. The actual daemon/CLI tests use AF_PACKET,
veth, bridge/VLAN and descriptor passing, with modeled ONU storage/MDIO/FE:
download/activate/commit, rejected window/end, abort, replacement, shutdown,
client exit, concurrent peer administration, failed FE withdrawal, missing
Start ACK, and immutable file snapshot. Native and aarch64-musl builds plus
existing ONU administration/reboot/migration regressions pass. Local reports
are `artifacts/fttr/upgrade-{stock,resident,resident-fpga1}-20261108.json` and
`upgrade-{admin,reboot,move}-regression-20261108.json`.

This adds the factory protocol and controller ownership path, not physical
flash or boot acceptance. Image content is not authenticated against a known
subordinate model/vendor signature; the operator must supply its exact
compatible vendor image. Durable intent and read-only version/state recovery
are implemented below. Positive boot-event/firmware-content verification,
resumable image transfer and automatic coordination with subordinate
capabilities remain work. A controller restart never replays an interrupted
download. Full upstream packet retirement and hardware-ID
reuse also remain unresolved. No device was accessed or upgraded.

### Durable upgrade intent and image-state reconciliation

Upgrade commands now require `fttrd --upgrade-state PRIVATE_DIRECTORY`.
The init service creates `/etc/tyfza1-fttr-upgrades` mode0700 and passes this
persistent path; it is included in package configuration preservation.
Manual daemons without this option retain their ordinary functionality but
reject image operations with `ENODATA`. The directory is owned by the daemon
UID, leased with flock and checked for directory-fsync support before any
hardware access. It is separate from volatile status/MIB journals.

Before Start Download, End Download, Activate or Commit is sent, a64-byte
versioned record is written through a0600 exclusive staging file, file fsync,
rename and directory fsync. It records the ONU serial, controller host/OLT
MACs, backend, action, selected image, expected14-byte version, download
size/CRC, operation stage and pending state; a CRC covers the record. The
serial is the filename key, so moving hardware slots cannot lose the hold
and a different ONU cannot inherit it. Segment progress stays in memory:
there is no per-packet flash write and no promise of resumable transfer.
A START/END pending record means the new downloaded version has not been
verified. Activation/commit records capture the actual selected version
before their first mutation. Record errors prevent mutation; failure while
saving a verified result leaves admission closed and the durable pending
record intact. A rename followed by failed directory fsync is treated as
uncertain, never as permission to transmit.

The controller checks records before automatic inventory/provision and
before automatic upstream-error recovery. Its startup clears inherited
business grants when this store is configured even without a LOID policy.
A matching record leaves that ONU at `upgrade-wait` with no business port,
while other ONUs can recover normally. Corruption, unexpected permissions or
controller binding mismatch fails closed. Replacement ONU serials use their
own records. A pending record prevents another download/activate/commit with
`EALREADY`; merely restarting the daemon, receiving a late ACK, or observing
registration again cannot clear it. The record survives clean shutdown and
SIGKILL. No upgrade write is automatically replayed.

`onu-upgrade ... check` reads the recorded class7/image with mask0xf000 and
compares the exact14-byte version plus valid and action-specific state:
activation requires active, commit requires committed, and completed download
requires inactive/uncommitted. Original ASIC/FPGA1 `plr_gponrm_image_get`
execution confirms both slot reads and the version/committed/active/valid
mapping (16 inventory combinations). The check cannot prove a pending
incomplete download matches the supplied image, so that case returns
`ENODATA`. An activation or commit whose ACK was lost can be confirmed by
this independent readback. A successful check records the verified result
and allows subsequent explicitly requested image operations, while keeping
business held. `onu-admin ... resume` explicitly removes and fsyncs the hold,
then performs the normal fresh authentication/inventory/service path. It
also provides recovery from an incomplete download; it is not a remote flash
rollback. `upgrade_recovery_required` exposes the held state.

This verifies the ONU's reported version and software-image flags, not a
physical reboot event or cryptographic image identity. `boot_verified` stays
false. Two different firmware contents may report the same version; this
mechanism cannot replace image signature/model validation. Recovery does not
resume data transfer or reconstruct remote service ownership.

Offline checks execute the actual C store with64 single-byte corruption
cases, file/directory fsync faults, directory lease, permissions, symlink and
stale staging cases. Production upgrade checks now include76 transaction
scenarios, including read-only recovery and wrong-version rejection. Real
resident/CLI/veth tests force SIGKILL during download, after activation and
after commit with their ACKs lost, then restart against retained storage:
no automatic upgrade writes occur; wrong versions and corrupt/foreign-bound
records reject; correct readback permits explicit continuation. Staging and
verified-result write failures never reopen forwarding. Tests also cover
operation without LOID policy so startup grant cleanup is not accidentally
dependent on authentication. Native and aarch64-musl builds, controller
administration/reboot regressions and init syntax checks pass. Local reports
are `artifacts/fttr/upgrade-store-{stock,resident,resident-fpga1,noauth}-20261108.json`
and `upgrade-store-{admin,reboot}-20261108.json`; build identities are under
`upgrade-store-build-20261108/`. No device was accessed.

### Upgrade hold release follows confirmed administration

An explicit `onu-admin ... resume` previously removed its durable upgrade
record before closing FE admission, removing business ports/grants or
executing the driver's serial-guarded operation. A later IO failure could
therefore leave no record for the next controller to consult. The record now
remains until local withdrawal, the driver operation and a same-serial
hardware readback all succeed. A replacement discovered in that readback
fails closed. Only then is the record removed and its directory synced,
before shared admission reopens. An unlink/directory-sync failure also stops
the controller; because unlink may already have happened, such a sync failure
is not a claim that the old file necessarily remains on disk. At that point
the explicit hardware resume has already been confirmed.

The upgrade hold also used to prevent the release timer of an explicitly
requested temporary deactivation from running. The timer may now release
registration blocking while retaining the independent upgrade hold. Automatic
error recovery remains disabled for held ONUs. Subsequent refresh reloads the
record and keeps business VLAN/DBA withdrawn; only explicit successful resume
clears it. ASIC's scan/15s handling and FPGA1's offline-only release still use
the existing original-verified driver paths.

Both defects were reproduced with actual production functions before the
fix. `test_fttr_upgrade_admin.py` checks each withdrawal/driver/readback error,
post-operation serial replacement, record-removal failure and successful
ordering; `test_fttr_admin_lifecycle.py` checks held temporary release across
all three backend selectors. Real daemon/CLI/veth tests cover IO/stale resume
failure, restart with the retained record, held deactivation release and later
successful resume on ASIC/FPGA1. Existing76 upgrade transaction/store checks,
ordinary administration and native/aarch64-musl builds pass. The original
administration oracle still passes18 state operations and384 upstream error
recovery combinations. Reports are
`artifacts/fttr/upgrade-admin-{resident,resident-fpga1,stock}-20261108.json`,
`upgrade-admin-regression-20261108.json`, with build identities under
`upgrade-admin-build-20261108/`. No physical device was accessed; complete
hardware packet retirement and ID reuse remain unresolved.

### Alignment with main 8ee9791 and combined PE policies

The worktree incorporates local main8ee9791 (the factory Wi-Fi policy branch)
without merging FTTR back to main. ARM request/AC quotas, original total2048
sleep admission, traffic/vendor/EDCA policy, bounded64-descriptor NI ingestion,
per-peer flow statistics and original A-MSDU queue/size/count/timer rules are
combined with FTTR multicast actions, source6 generations, EPP drain markers,
BCMC HIQ, shared sequence/GTK PN and per-channel pending publication.

Conflict resolution preserves multicast recipes' resolved TIDs and no-ACK
policy; only generic NI unicast is reclassified from Ethernet. Per-peer flow
accounting sees the final converted Ethernet header. BCMC uses its dedicated
MACID0/HIQ route and does not consume per-station WMM quota counters. Unicast
and multicast replicas for a station share ARM/NI per-AC quota; rejected
replicas keep BID ownership until EQ return. Gathered subframes retain the
same immutable recipe and FTTR generation. ARM completion retains both quota
release and the FTTR discard-aware sequence/PN handling.

The combined -O2 image exceeded both the pre-mailbox region and32KiB IMEM.
The build now uses -Os; no forwarding/lifetime features were removed to fit.
The final PE is32572 bytes (196 bytes below32KiB), SHA256
`478ad6037b0cc3d84c5388c38f62ddd817f8d951d5310f9d58689339022ac160`;
conservative summed stack frames are7288/8192 bytes. The non-PE_UPLINK variant
is14168 bytes. Both linked entry/memory/relocation checks pass and regenerating
the tracked firmware with the build script gives identical bytes. This
compiler choice has not been benchmarked on hardware; main's measured
performance does not establish the combined firmware's performance.

Linked MIPS checks cover273 multicast admission/revocation/quota cases plus12
full encrypted/no-ACK TX/RPQ cases,251 FTTR EPP/BID-generation cases,98 BCMC
cases, shared sequence/GTK PN, per-channel credit stalls and actual RPQ/EQ
retirement, software WP ownership and stock queue/quota behavior. An isolated
build of main8ee9791 agrees with the combined image for2048 IPv4/IPv6/PPPoE
classification/flow-key cases and14/15/16-byte frames. Stock policy execution
checks1920 A-MSDU decisions,82944 EDCA cases,220 vendor cases,156 WMM transitions
and320 quota boundaries. ARM request construction passes8448 vectors;
NI restore/uplink, station/key/mesh and userspace recovery regressions pass.

Old tests that assumed16-entry ingestion,4ms ordinary timers or older DDR
ranges now use the actual64-entry bound,10ms ordinary timer and current
layout. The EPP observer is removed after its first-batch ordering assertion
to avoid a Unicorn hook/re-entry exception; function calls verify return PC.
Image/modules build and23 changed kernel-source identities pass. Evidence is
under `artifacts/fttr/main-8ee9791-*`, with final PE in
`main-8ee9791-pe-final-20261108/`. No device was accessed. Full MCE/NI DMA/AFT
producer retirement and safe hardware multicast ID reuse remain open.

### Selected NI requests obey peer withdrawal at final publication

The custom PE staging FIFO can retain an ordinary unicast request after
`ni_select` has admitted it, including while its TXBD or hardware WP credit is
unavailable. ARM key/BA/STA teardown already clears the peer route before
withdrawing FE and asking PE to pause. The final publisher checked immutable
multicast recipes and FTTR generations, but neither covers ordinary NI
unicast from LAN/PON with no multicast cookie. Such a selected request could
still be published after its peer route was cleared. The linked production
image reproduced this behavior before the fix.

`pe_tx_publish` now checks the software owner's MACID against the current
route bit, the per-peer retirement marker and the global retirement marker
before hardware-credit allocation or descriptor construction. A revoked
request joins the existing completion FIFO; it does not consume a hardware
WP tag or claim a transmission. It retains its admitted per-AC quota and
whole BID chain until the final EQ return. Partial A-MSDU returns decrement
only the returned BID/TID/FTTR count and leave the single MPDU quota held
until the last BID. MACID0 BCMC remains governed by its immutable HIQ/GTK
contract and global retirement; a different station's retirement does not
block it. Already published packets still require a real hardware RPQ and
EQ return, and this change does not attempt to reclaim them by inspecting a
software route bit.

`test_pe_peer_withdrawal.py` executes25 linked-MIPS scenarios across NI
source0/6/7, ordinary route removal, per-peer/global fences, zero TXBD credit,
all four ACs, two-BID partial aggregate retirement, another peer's fence,
BCMC and actual RPQ completion. It verifies zero publication for withdrawn
requests, exact quota/BID release and no foreign tag release for an
unpublished request. Existing multicast, FTTR drain, BCMC, PS/WMM, shared
sequence/GTK PN, stock WP ownership, channel progress, RX and ARM station/key
retirement tests pass. The ARM retirement test also verifies that a failed
FE withdrawal has already closed the peer route; its fixture now includes
the existing BCMC lifetime field.

The final PE is32668 bytes, SHA256
`18e52bdffdaebbbc94bcbe4e62684644e02384fb2ffc761a562e8b6bb9eeebfa`,
with conservative summed stack7296/8192 bytes. The repeat build matches the
tracked header, the non-PE_UPLINK image remains14168 bytes and byte-identical,
and Image/modules plus kernel firmware-input identity pass. Local reports
are `artifacts/fttr/peer-withdrawal-*`, including the memory and build identity
JSON files. This closes a software staging/peer teardown window. It does not
establish MCE/NI DMA/AFT producer retirement or safe reuse of quarantined
hardware multicast IDs, and it does not claim physical throughput or timing
acceptance. No device was accessed.

### Canceled selected PS requests finish their service period

A sleeping STA's NI selection consumes the ARM-published PS grant and stores
its generation in the selected request owner. If peer/FTTR/multicast
withdrawal cancels that request in the final publisher, no hardware RPQ will
arrive. Previously this path queued the BID for recycling but omitted the
PS completion record. The normal empty-queue scanner could not repair it
because selection had already acknowledged the command, leaving mac80211's
service period and the driver's `ps_issued` state outstanding. The original
linked firmware reproduces the missing notification.

The final publisher now completes the selected PS generation when canceling
its request. A small shared helper checks the current generation, publishes
the record before its producer counter and requests the existing PE-to-ARM
event. Hardware RPQ completion and WMM quota rejection use the same helper.
A newer grant suppresses the old notification; repeated publication or
recycling cannot notify twice. This completion ends the software PS grant,
not BID/PCIe ownership: quota, TIM/pending BID counts and FTTR drain still
follow their existing RPQ/EQ lifetime. An unpublished cancellation does not
report a successful transmission or fabricate an over-the-air EOSP frame.

`test_pe_ps_withdrawal.py` executes132 PS-Poll/U-APSD cases with route,
peer/global, FTTR-generation and multicast revocation, TXBD stalls,
zero/wrapped/current/old grant generations, EQ stalls and real RPQ success or
radio-discard reports. It feeds the exact generated PE records into the
actual ARM `ni_ps_done`/`ni_ps_complete` and mac80211 generation-aware EOSP
functions, checking service-period closure, issued-grant release and duplicate
suppression. The existing PS generation concurrency, grant/TIM, selected peer
withdrawal, multicast/BCMC, FTTR BID retirement, queue quotas, channel progress,
GTK PN/sequence and station/NI lifecycle checks pass. Original PS-Poll helper
execution still matches eight register cases and four AID/U-APSD decisions;
the new completion is the custom mac80211/PE adaptation, not a claimed factory
software structure.

To keep the full image inside fixed IMEM, the small PS grant/completion
helpers occupy unused pre-mailbox text; linker bank limits and boot-entry
checks remain in force. The final image is32692 bytes, SHA256
`e71e4f07efd3349588f1c294cc0c1e78c25d9e4ea169ad8ad3ca5dfd16683efc`,
with conservative summed stack7296/8192. The non-PE_UPLINK build remains14168
bytes and passes its entry/memory and retained-WD tests. Image/modules,
repeat PE generation and kernel firmware identity pass. Evidence is under
`artifacts/fttr/ps-withdrawal-*`. Test fixtures were updated only for current
traffic/peer fields and DDR ranges needed by these production functions.
No physical device was accessed. Complete upstream producer retirement,
quarantined hardware-ID reuse and physical power-save interoperability
remain unverified.

### CPU multicast replicas remain revocable in Linux TX queues

The CPU-direct multicast fallback previously validated its complete-set
owner in `fe_mcast_host_receive`, then handed the skb to ordinary Ethernet or
mac80211 TX. A membership/policy change after that validation could leave an
old replica in qdisc, BUSY requeue or a mac80211 queue. The existing FTTR skb
generation did not cover this independent multicast decision, particularly
for LAN/PON-origin traffic with no FTTR generation. Executing the old
production TX gate reproduces an old replica being transmitted after its
multicast epoch changed.

Direct CPU replicas now retain an explicit direct-copy flag together with
the existing numeric group/owner/epoch skb extension. The built-in multicast
core calls the current FE owner under RCU; FE rechecks the direct record,
live host ownership, gate/writer/error state and FTTR/PON ingress admission
under its short multicast lock. Missing or replaced FE owners reject the old
copy; no driver pointer is stored in the skb. Local-only bridge RX copies
continue to use their existing local-delivery check.

The existing shared packet-staleness checks now include this direct-copy
validation at qdisc enqueue and BUSY/dequeue, mac80211's driver handoff and
native radio submission, plus the existing netfilter/reassembly boundaries.
Metadata copies share the refcounted extension without allocation. Aggregation
and reassembly distinguish direct-copy owner/group/epoch from each other and
from ordinary packets, so an old copy cannot borrow another packet's valid
decision. The extension remains attached through mac80211 fragmentation and
normal skb clones/copies.

The complete learner/FE test checks live copies, writer/error closure,
ingress admission and failed flow withdrawal; the core test covers concurrent
owner detach, copy identity and reload. `test_fttr_packet_generation.py`
executes actual Linux BUSY/qdisc retry, driver handoff, NFQUEUE, IPv4/IPv6
metadata and GRO discrimination with multicast callbacks, including a
LAN-origin copy with no FTTR generation. Its old-header run fails the stale
queue test and the new run passes. Existing bridge host/selection/M2U/writer,
parser, normal learning and FTTR admission tests pass. Image/modules and all
five changed kernel input identities pass; no kernel patch or PE firmware
change is required. Reports are under `artifacts/fttr/mcast-queued-*`.

This closes the Linux software queue window before the last submission
check. The pending ARM-to-PE request generation is added below; a reference barrier
and atomic policy-writer/final-hardware handoff are still absent. Published CPU requests,
MCE/NI DMA/AFT producer retirement and hardware-ID reuse remain work. The
existing direct NI-to-PE immutable-cookie checks are unchanged. No device was
accessed and no physical packet-retirement claim is made.

### CPU multicast stamps survive ARM-to-PE submission

A direct CPU multicast replica can pass Linux's last queue check and then
wait in the ARM request ring or PE's private pending FIFO. That pending
request previously carried only FTTR ingress, GTK PN and sequence identities;
a multicast-only policy change could therefore leave it publishable. The
old linked PE reproduces this even for a CPU copy with no FTTR stamp.

The built-in multicast core now owns an independent nonwrapping32-bit CPU
copy generation and two coherent radio mirrors. FE multicast writers and
individual learned-flow removal invalidate this generation while holding
the same short FE lock used to validate a live copy. ARM obtains a token by
checking the live skb owner between two generation reads; a concurrent
invalidation fails admission. Zero after overflow permanently refuses new
stamped copies. The mirror is registered for the radio allocation lifetime,
including when no NI multicast table is bound, and is detached only after
PE stop and completion-work cancellation.

The radio records the token in a dedicated256-word table at DDR0x68000,
indexed by the already-owned ARM WP ID. It writes zero on every ordinary WP
reuse. This leaves all ten scatter/gather address pairs and the four existing
request metadata words intact. PE reads the WP token before hardware-credit
allocation and compares it with the mirror in the formerly reserved table
word; mismatch or a missing/closed mirror uses the existing unpublished-WP
release marker. Private FIFO compaction retains software WP IDs, so it cannot
lose this check. The CPU multicast capability is nowMCP4 (`0x4d435034`);
stamped submissions reject older firmware.

The ARM completion worker now also polls multicast-only accepted requests.
A software cancellation unmaps/frees the skb and releases its WMM quota once,
without ACK/rate feedback or a fabricated sequence result. Hardware RPQ
remains the normal completion source after PCIe publication. The token table
needs no pointer in a request and cannot outlive its owning radio allocation.
Any multicast invalidation conservatively revokes pending CPU copies on both
radios; unrelated ordinary requests are not stamped.

The core tests cover two registrations, duplicate/full registry rejection,
a writer during token acquisition, overflow, detach and FE ownership reload.
The actual ARM submission block runs1024 stale/ordinary/token/old-capability
cases across all256 WP IDs, verifying no request-word overlap. Linked PE
checks103 cases across six channels, ten-address requests, no NI binding,
credit stalls, generation rollover, WP reuse, mixed pending entries and
already-published packets. The original image fails the stale-publication
case. Actual ARM software completion runs514 partial/full batches including
multicast-only skbs and quota release. Binding teardown verifies
join-before-unregister-before-free. All related FE, bridge, FTTR/PS, BCMC,
GTK PN, shared sequence, queue/quota and RPQ regressions pass.

The full PE image is32760 bytes, SHA256
`db16d7b3b06129485176b83b3371f6c7653d9ea5016114f5947f510bb6f2a524`,
with conservative summed stack7296/8192. Linker/entry bounds pass with only8
IMEM bytes spare. The non-PE_UPLINK image remains14168 bytes and byte-identical;
it does not advertiseMCP4 or accept stamped CPU copies. Repeat generation,
Image/modules and seven changed kernel input identities pass. Reports are
under `artifacts/fttr/cpu-mcast-*`.

This adds cancellation while CPU requests are pending on PE. It is not an
end-to-end completion barrier: an invalidation racing after the final PE
check, already published PCIe payloads, MCE/NI DMA/AFT upstream producers and
safe reuse of quarantined multicast IDs still require further work. No
physical device was accessed and no hardware packet-retirement or performance
claim is made.

### Learned multicast reopening waits for accepted CPU radio copies

Generation invalidation alone could cancel pending PE requests but did not
prevent a new learned multicast generation from opening while a previous
CPU-origin replica still owned a radio DMA mapping. The old production
learner reproduces reopening with outstanding CPU-copy ownership.

The built-in multicast core now counts accepted stamped radio submissions.
Admission checks the token and increments the count under the same short
spinlock as generation invalidation; either the old request is counted or
it is refused. Ordinary unstamped packets take no reference. Submission
rollback drops the reference after unmapping, and normal RPQ, unpublished PE
cancellation and joined device teardown drop it only after unmap and skb
disposition. Removing a radio mirror or unloading FE does not reset this
built-in count. Overflow refuses admission, and underflow warns instead of
pretending the owner is idle.

A closed learned gate checks this count before allocating another group and
again before publishing PE/profile state. While any accepted CPU copy
remains, learning returns `EAGAIN`, leaving complete software forwarding in
place. It retries on a later complete packet after the final release; it does
not wait under the multicast writer/FE lock. Same-generation traffic can
continue using an already active group. The existing FE-lock → core-lock
order pairs the idle check with closed-gate admission, so an old token cannot
join after invalidation. This is one conservative count across both radios,
not a per-flow resource reuse claim.

Tests execute256 concurrent token-admission/invalidation races,512 accepted
requests across detach/rebind and final-reference release,1536 actual ARM
submission/rollback cases,514 partial software-completion batches and16448
RPQ report comparisons. Actual learner tests verify that two outstanding
copies, and then one, prevent installation/reopen without consuming another
group; zero permits a fresh complete rule, while the active same generation
remains usable. Device teardown tests require PE join and skb/DMA disposal
before reference release. Existing bridge/multicast/FTTR and station lifecycle
checks pass. Image/modules and four changed kernel-input identities pass;
PE code/ABI is unchanged by this reference change. Reports are under
`artifacts/fttr/cpu-mcast-reference-*`.

This waits for accepted CPU radio copies before learned acceleration reopens,
including copies already published to PCIe. It does not stop an already
published radio packet from reaching its old recipient during a concurrent
policy change, does not cover direct NI/MCE hardware copies or CPU Ethernet
DMA, and does not establish the full MCE/NI DMA/AFT producer fence. Hardware
IDs remain quarantined and physical packet-retirement acceptance remains
outstanding. No device was accessed.

### Alignment with main 43dd462 QoS admission cache

The worktree incorporates main43dd462's NI association QoS cache. Generic
unicast reuses the immutable association header shape inside each bounded
EPP batch while rereading the peer route on every packet. FTTR multicast
cookies retain their separately compiled TID policy. The old/new linked
images agree for1024 packets across mixed/QoS/non-QoS peers, compact/full
headers, short frames and mid-batch route withdrawal; an unchanged64-packet
peer batch reduces QoS-template reads from64 to1 while all64 route checks
remain. A further2048 IPv4/IPv6/PPPoE classification/flow-key comparison passes.

A small RX work-dispatch wrapper is placed in unused pre-mailbox text to fit
the combined image; both linker limits remain checked. The final PE is32716
bytes, SHA256 `0220760d8898eab9a7f9a579b5d31a0c5c73a3d7237f516b5c947af5da4a4b10`, conservative
summed stack7288/8192 bytes. The non-PE_UPLINK
image remains14168 bytes and byte-identical. CPU multicast, PS/peer withdrawal,
BCMC, FTTR EPP/BID, queue/quota, sequence/PN, RPQ ownership, channel progress,
RX and memory-layout regressions pass, as do Image/modules, repeat generation
and five changed kernel input identities. Local evidence is
`artifacts/fttr/main-43dd462-*`. No device was accessed and main's physical
performance measurements are not measurements of this combined FTTR image.

### CPU multicast DMA copies retain references through descriptor retirement

CPU Ethernet/PON/FTTR transmission copies an skb into a coherent NI DMA-LSO
buffer and consumes the skb immediately. That consumption previously left
no multicast reference even though the copied payload could still be queued
or prefetched by DMA. `rtl_dma_tx_frame` now validates the direct-copy token,
performs the skb copy, then joins the same built-in multicast admission count
before publishing its descriptor. A writer racing the copy fails admission;
copy failure consumes/drops the skb without publishing or acquiring a
reference. Ring-full BUSY retains the caller's skb and takes no reference.

A per-descriptor boolean holds the reference independently of netdev
statistics ownership. `rtl_dma_tx_forget` still removes a disappearing
interface pointer but cannot complete its payload. Normal completion releases
only the descriptors behind the original two-descriptor prefetch holdback,
and does so once before their ring slots may be reused. Equality of hardware
read/write pointers does not release the last two payload references. A
shared-engine stop/start uses the existing VP-disable and paired DMA+AFT
reset sequence, then releases any remaining references; this is the same
reset boundary already used before reusing/freeing the coherent ring memory.
It does not claim that downstream NI/ES queues have retired.

The existing closed learned-multicast gate now sees these DMA references
alongside radio references. Subsequent ordinary CPU traffic can move the DMA
consumer beyond the two held descriptors; otherwise those final references
remain until engine reset, leaving new learned acceleration closed and the
complete software forwarding path available. No artificial descriptors or
packets are generated to force completion. Direct XRAM mode and downstream
packet retirement remain outside this DMA ownership proof.

`trace_stock_dma_completion.py` executes the target module's actual
`ca_dma_tx_complete_easy` for4096 reclaim/read-pointer combinations. Normal
states agree with the two-descriptor holdback. Four synthetic short-distance
states at slots62/63 expose the factory wrapped-comparison defect (including
no pointer progress causing releases); native modulo-distance accounting
retains those slots instead. These synthetic states are not evidence of an
observed factory runtime failure. Original asynchronous publication and
paired DMA/AFT reset instruction comparisons also pass.

`test_dma_mcast_reference.py` executes836 wrap/prefetch/reset/owner-forget,
stale admission, copy failure, mid-copy invalidation, BUSY and ordinary-
traffic-progress cases. Running the prior DMA source fails the reference
assertion. Mixed LAN/OMCI/Wi-Fi DMA tests,300 original Wi-Fi submission
contracts, PON PPPoE, FTTR CPU DEEPQ, bridge/core lifetime, DMA reorder/reset
and queue admission regressions pass. Image/modules and both changed kernel
input identities pass; PE firmware is unchanged. Evidence is under
`artifacts/fttr/dma-mcast-*`.

This accounts for native CPU DMA payload ownership before learned-group
reopening. It is not an end-to-end link-delivery barrier, does not cancel a
packet already beyond DMA/AFT, and does not cover direct NI/MCE replication.
The full upstream producer fence and safe hardware-ID reuse remain work;
no physical device was accessed.

### Direct multicast BID accounting and EPP-prefix completion

Direct NI-to-PE multicast copies now have an independent drain record at
DDR0x1e840, separate from the source6 FTTR record at0x1e800 and below the GTK
PN table. Both use the same prefix algorithm but keep distinct request/ACK,
seen, pending-BID and eight-COS target/mask fields. MCP5 (`0x4d435035`)
identifies this capability. The two radio allocations register these records
with their CPU-copy generation mirrors for the entire PE lifetime.

PE increments the multicast count for every incoming reserved multicast
cookie before any admission decision, including invalid/stale recipes,
closed arrivals and sleeping-queue overflow. The cookie is retained in BID
headroom even on the early overflow path. Queued, selected and published
copies retain ownership until EQ accepts each BID; partial aggregates release
only returned members. Metadata is sampled before PUSH and the counter is
decremented after its fence. Forced ARM cleanup also samples the cookie before
PUSH and preserves the unreturned suffix/count on failure. Ordinary non-cookie
traffic is not included, while source6 multicast belongs to both independent
counters.

The FE closes the learned tuple and PE epochs before requesting a new
multicast EPP snapshot. PE captures all eight producer positions and services
the union of FTTR/multicast unfinished COS prefixes, so high-priority traffic
cannot indefinitely starve a lower-COS boundary. It ACKs only after that
prefix and all currently owned cookie BIDs are complete. NI-disabled state
has no ring geometry to sample; a paused enabled NI waits until its geometry
is stable again. A new request invalidates the old ACK. The shared core idle
check now requires CPU references zero and both radios' current request ACKs
with zero pending BIDs. It does not borrow the FTTR ACK or silently ignore a
new radio. Individual old-flow deletion triggers a new request, and the
learner checks again after deleting old flows before allocating another
hardware group.

Linked MIPS tests cover125 source/COS/full/compact-header, prefix wrap,
malformed EPP, invalid-cookie, sleeping-pool overflow, mixed FTTR/multicast
priority, actual RPQ, partial EQ, late closed arrival, retry, disabled geometry
and direct BCMC cases. The real PE main loop updates the two independent
ACKs. Existing251 FTTR cases, CPU multicast/PS/peer withdrawal, quotas,
sequence/PN, RPQ/WD ownership, channel progress and RX checks pass. Core tests
require both ACKs and reject pending-after-ACK; FE tests require another wait
when deleting old flows creates a fresh prefix. Forced teardown/restore tests
cover cookie metadata overwritten immediately at EQ handoff. The1024 QoS
cache and2048 protocol/flow-key old/new comparisons pass.

To fit the full firmware, only `ni_ingest` uses the compiler's minsize
attribute and the queue helper moves to the RX/TX bank. Linker limits remain
unchanged. Final PE is32544 bytes, SHA256
`5652f74d23081629faeaa3e94bc031659c81ba26b9df926b8814765c682a11b9`,
conservative summed stack7304/8192. The non-PE_UPLINK image remains14168 bytes
and byte-identical. Repeat generation, Image/modules and eight changed
kernel-input identities pass. Evidence is under
`artifacts/fttr/mcast-prefix-*`.

This covers already-ingested multicast BIDs and the captured EPP prefix.
Packets still in MCE/NI DMA/AFT may arrive later, including after an idle
sample; ACK/count observations are not an atomic upstream producer fence.
An old late cookie stays permanently retired, but this work does not prove
that every upstream packet has disappeared or permit any quarantined group,
action, cookie or GEM ID reuse. No physical device was accessed.

### Multicast withdrawal refreshes the prefix after hardware cleanup

The first close of a host/direct record protects CPU/PE consumers, but its
EPP snapshot could be acknowledged before `fe_delete` finished removing the
Hash/cache and NI group. Cleanup now requests another multicast generation
and EPP prefix only after all those hardware withdrawals succeed. The
existing `mc_group` ownership transition to zero makes this request occur
exactly once even when later MAC/interface restoration fails and retries.
Hash-age, cache or NI-group failure retains ownership and cannot issue this
post-withdrawal request. The next closed-gate learning pass must wait for its
new ACK, rather than accepting the earlier close's ACK.

Host/direct record removal is now idempotent: its first live ownership
removal still invalidates CPU/PE copies immediately, but retries after those
records are already detached do not advance the request number again. An
unpublished rollback with no live host/direct record does not invent a new
close event. A partially staged direct record is still revoked exactly once.

The old production teardown fails the post-hardware-request assertion. Tests
now cover age/cache/group failures, repeated failures before hardware
withdrawal, two later MAC restoration failures, exactly one successful
post-withdrawal request, host-only and partial direct rollback. The actual
learner still waits for the new prefix before allocating a replacement group.
The target FC last-reference unlearn path was executed again; it confirms
Hash-delete ordering but provides no native MCE/PE completion fence and loses
its own retry after a lower delete failure. Native retained ownership and
retry semantics are preserved. Eleven related FE/bridge/multicast/flow tests,
Image/modules and two changed kernel input identities pass. PE firmware is
unchanged; reports are `artifacts/fttr/mcast-withdraw-*`.

A later EPP snapshot is stronger than an early one, but is still not proof
that every MCE/NI DMA/AFT upstream producer is empty. In-flight downstream
packets and safe hardware group/action/cookie/GEM reuse remain unresolved.
No device was accessed.

### A drain race retains the unopened multicast rule

A late old-cookie arrival can become visible after a new learned rule has
finished Hash/NI installation but before its final gate-opening check. The
previous code deleted that valid new rule on `EAGAIN`, permanently retiring
its group/cookies and forcing another allocation on the next attempt. The
old production learner reproduces this avoidable quarantine consumption.

For a pure drain wait in the same generation, the installed rule now remains
behind the closed tuple/PE gate. Repeated busy observations allocate nothing
and do not refresh the drain request. A later complete Linux packet decision
rechecks the actual recipient actions, direct recipes, tags, peer policy and
MTU against the retained rule before opening that same group. It may carry
a different transaction ID; numeric live ownership remains with the original
immutable installation. Host/direct copies are not accepted while the gate
is closed. A membership generation change, writer or gate error still uses
normal withdrawal; MTU/recipient differences invalidate rather than reviving
the pending rule. Normal worker idle expiry also remains in force.

Tests inject an old-copy count when the production Hash becomes valid, then
exercise eight busy retries and a later fresh transaction. Shared-P6 and
mixed local-host/LAN/direct-PE groups retain the same flow, group/action and
cookie counts. The PE table stays closed until revalidation succeeds. MTU
and generation changes reject pending state and preserve existing quarantine
semantics. Fourteen related FE/bridge/CPU-DMA/reference tests pass, as do
Image/modules and changed-input identity. No PE firmware change is needed.
Evidence is `artifacts/fttr/mcast-pending-*`; the old-header run fails the
retained-owner assertion.

This reuses an existing live installation, not a retired hardware ID. It
does not add an upstream producer fence or prove that late MCE/NI DMA/AFT
packets have disappeared. Full retirement and hardware-ID reuse remain
unresolved, and no physical device was accessed.

### Pending multicast rules are isolated from the shared gate

The retained-rule wait state needs its own publication barrier: another flow
in the same multicast epoch can open the shared tuple/PE epoch. A waiting
rule previously kept a valid Hash and could therefore become active without
its own fresh complete-recipient decision. The old implementation fails the
new per-Hash isolation check.

A drain-delayed rule now marks itself waiting, suspends its CPU host owner,
zeros only its direct PE record epochs, and withdraws its Hash/cache while
preserving the allocated group/actions/cookies. Another flow may open the
shared profile, but that cannot revive the waiting rule or its CPU/PE copies.
When its own complete packet decision matches again, it checks drain state,
closes the shared tuple during indirect Hash publication, and rechecks epoch,
writers and drain before restoring its own record epochs and host access.
Hash/cache suspension or republish failure closes the generation and uses
the existing delete/retry path. A pure late-copy wait remains suspended.

The worker continues to schedule waiting rules for the normal30-second idle
expiry, but never refreshes their Hash age as if they had a hardware hit.
Tests exercise two different keys sharing one profile, per-slot age words,
local host/LAN/direct PE copies, no extra group allocation during retries,
matching fresh transactions, suspension/republish IO failures, MTU and epoch
changes and the actual worker expiry path. Seventeen related tests pass,
including bridge/flow ownership and packet metadata boundaries.

This turn also merges local main6380aa3's SU-rate retention across MU reports.
Its actual factory parser comparison passes256 SU/MU cases and nine malformed
boundaries; RSSI/NHM watchdog comparison passes18928 complete commands plus36
NHM cases. The supplied original-module path is now an optional positional
argument to those tests. Image/modules and four changed kernel input
identities pass; PE firmware is unchanged. Reports are under
`artifacts/fttr/mcast-shared-gate-*` and `main-6380aa3-ra-feedback-20261108.json`.
Main's unrelated working-tree changes were not modified.

The suspension state is a live allocation with its lookup withdrawn, not a
reusable retired hardware ID. It does not establish MCE/DMA/AFT upstream
retirement or physical delivery behavior. No device was accessed.

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

### Resident multi-service collections and shared ownership

`fttrd` now provisions one to seven explicit shared-bridge services per ONU,
matching the original default producer's data-slot1..7 range on ASIC/FPGA2.
FPGA1 keeps its actual one-slot restriction. Rows with the same serial/wildcard
must have distinct slots and filter VIDs; they execute in ascending slot order.
An exact serial replaces the complete wildcard collection. Multiple rows use
`shared`/`shared-mcast`/`shared-ctc` with `filter:VID`, so one service cannot
replace another's UNI access policy. The factory-default generator below
also enters this collection; saved factory profile import remains separate work.

The collection first copies the reported MIB into a private planning snapshot
and reserves distinct T-CONTs with matching queues, GEMs and ANI bridge ports
for every member. Projected values never replace the observed MIB or claim
remote success. Every member still performs live object preflight/readback.
Later members must find the shared GAL/bridge/UNI objects intact and compare
the complete EVTO table against the preceding member's verified snapshot.
They adopt these parents without deletion ownership. The first member retains
its actual created/modified object and prior-table ledger.

Failure rolls completed members back in reverse order, restoring their prior
local grant and only their owned remote writes. A failed cleanup stops before
earlier shared dependencies, retains the initiating/cleanup errors and closes
all local data grants. Missing resources in the complete planning pass cause
no business writes. Local ports are attached only after every member and the
MIB sync checks succeed; a later port failure withdraws already-created ports
and all grants. Netlink creation itself is sequential, so this does not promise
an atomic, lossless physical transition.

Migration preflights release rights for every member, withdraws all local
FE/VLAN/DBA state and releases members in reverse order before changing the SN
reservation. Restarted adoption still refuses migration without a durable
original ownership journal; the committed-journal recovery below now supplies it. Disconnect, identity replacement, sync recovery,
timeouts and shutdown now dispatch across the complete collection. With a
zero MIB-sync implementation every GEM CTP is probed, including later members.
Journals retain each service and a `service_set` aggregate; status reports
`service_count` and whole-collection readiness.

One selected job owns the remote multicast transaction. Every local GEM in its
bridge carries the existing native multicast-member group, so the parent and
FE learner suppress duplicate ordinary-GEM copies beside shared GEM4095.
Shared endpoint admission still requires every registered ONU's authentication,
sync and multicast membership/absence checks. Different bridges remain separate.

Offline tests execute the real daemon/CLI/AF_PACKET/netlink in isolated
namespaces with original OMCI encoders and modeled MiniOLT/ONU storage: two
services on ASIC, seven with CTC multicast on FPGA2, first/last resource
selection, complete reverse migration, last-job rejection, failed cleanup,
missing last queue, inter-member UNI/EVTO drift, zero-sync last-GEM loss,
exact-serial override and a failed second local bridge. Existing FPGA1 standard
multicast, single-service CLI, temporary-ONU, FE bridge and multicast-learning
regressions pass. GCC14.4/musl compilation/install passes;55 package source
files match build inputs (`artifacts/fttr/services-package-final-20261107.log`).
No physical optical traffic, packet retirement, safe hardware-ID reuse or
complete factory default restoration is established by these checks.

### Factory default service generation and card limits

`fttrd --services` now accepts a complete default collection in one row:

```text
serial-or-* factory total-TCONTs bridge-or--
serial-or-* factory-ctc total-TCONTs bridge-or--
```

For example, `* factory 2 br-lan` selects the original one-data-service default
on ASIC9690/FPGA2. The total includes management slot0; the original startup
initializes it to2 and its runtime setter accepts2..8. Data slots1..total-1
use queue priority0, transparent UNI mode4 and local grant units0/0/19440.
With total2 the ANI is unfiltered; larger collections install ANI VID100*slot
on every data GEM. The final job owns shared multicast; `factory` uses the
standard recipe and `factory-ctc` explicitly selects the implemented CTC recipe.
Exact serial replaces the entire wildcard collection. Mixing factory and
explicit rows for the same selector is rejected before device ownership.
The supplied policy file retains no active rows: local bridge selection remains
explicit. These rows generate defaults; they do not read a saved factory XML
or restore user-modified factory profiles. FPGA1 has a separate supervisor and
profile library, so this generator rejects it before hardware writes while its
existing explicit service policies remain supported.

The constants follow actual original execution, not the profile labels:

- `plr_pon_bw_profile_init` creates `FTTR-100M` with type3 and nominal
  fixed/assured/maximum0/50/100. The existing wrapper oracle establishes that
  `drv_gpon_alloc_id_config` is a no-op. `onuinfo` initializes its own rate arrays
  and its registered-status callback sends0/0/(19440*64) for every selected
  data slot; native DBA takes the corresponding grant units0/0/19440.
- `plr_api_gem_add` uses API queue1. Actual service/remote adaptation subtracts1;
  the flow composes Related Port as `(actual T-CONT << 16) | 0`. A missing
  matching upstream queue rejects the complete collection before service IO.
- `plr_pon_onutype_profile_init` creates `FTTR-402`. Its ONU adapter and actual
  configured-card allocator produce one Ethernet card with four UNIs and one
  VEIP card with one UNI. The label's2 is not the VEIP count.
- Actual class5/class6 first attributes classify Ethernet types22/23/24/34/47/49
  and VEIP type48. The original producers sort cards and UNIs by entity ID.
  All Ethernet PPTP UNIs resolve onto the first Ethernet card; VEIP requires
  `(UNI instance >> 8) == (card instance & 255)`. The generator caps selection
  at four Ethernet UNIs and one UNI on the first VEIP card. It does not alter
  excluded ports. Missing card classification fails with `ENODATA`; absence of
  any eligible UNI fails with `ENOENT`. A present but empty first VEIP card
  cannot silently retarget its required multicast service to Ethernet.

Every generated job uses the existing observed-MIB reservation, live readback,
reverse ownership cleanup, all-services-before-local-port admission and
multicast-member/FE withdrawal paths. `service_recipe.factory` records the
source of the recipe. Factory and explicit configurations cannot form a mixed
collection with different shared-UNI sets.

`tools/trace_stock_fttr_default.py` executes profile initialization, the
configured-card allocator/getters, real MIB card parsing and original
card/UNI producers/resolution, GEM queue translation, the default service
producer and eight-slot supervisor callback. Pools/lists/platform IO are
modeled; original dataflow and selection instructions execute. The report is
`artifacts/fttr/default-profiles-20261107.json`.

Namespace tests run the actual daemon/CLI, AF_PACKET and Linux bridge/VLAN
ports with original OMCI encoders and modeled ONU/MiniOLT storage. Default
single/two/seven-service collections pass, covering standard/CTC multicast,
actual default DBA, UNI exclusions, reverse migration, missing card type,
empty first VEIP, missing final queue and final-GEM failure rollback. Existing
explicit multi-service and CLI regressions also pass. GCC14.4/musl package
compilation/install passes (`artifacts/fttr/default-package-20261107.log`).
No device access or physical traffic/acceleration acceptance was performed.
Durable remote ownership, saved factory policy import, FPGA1 default profiles,
multiple VIFs and complete physical retirement/reuse remain open. Direct AP
NI→PE BCMC has since been implemented within the scope described above.

Further target execution on2026-11-07 narrows the FPGA1 default gap without
enabling an unproven policy. `onuinfo_fpga` links `libponrelay1p25g.so`; its
operation table at0x428818+24 selects the producer at0x40e00c. It always
creates T-CONT1/GEM1/API queue1/flow1, without an ANI filter, then requests
`plr_api_remote_onu_uni_mcast_enable` for card1/type2/UNI1. Unlike the ASIC
producer, it stops at the first error and does not delete/re-add the ONU.
Fifteen actual success/failure cases cover API ONU1/6/64. Its real profile
initializers, ONU adapter and configured-card allocator also yield nominal
FTTR-100M0/50/100, four Ethernet UNIs and one VEIP; actual shared enumeration
still intersects configured and reported counts.

The FPGA1 bandwidth set/clear wrapper replay executes72 calls through the
real no-op `drv_gpon_alloc_id_config@0x42c54` and
`drv_gpon_alloc_id_clear@0x42cc0`, with no nonstack write or hardware call.
This does not identify a usable default local grant after the native
controller clears old authorization. The original optical-power measurement
path temporarily changes grant words to obtain a sample and restores them;
those measurement values are not evidence of a normal service default.
FPGA1 `factory` remains rejected until default grant ownership and the
backend-specific multicast/queue adaptation are established. Explicit FPGA1
services retain their existing support. The optional `--fpga-caller` and
`--fpga-relay` arguments to `trace_stock_fttr_default.py` produce this evidence
alongside the ASIC regression in
`artifacts/fttr/default-fpga-profiles-20261107.json`; no device was accessed.

Further FPGA1 execution on2026-11-08 resolves the queue and multicast
adapter uncertainty. Its `plr_api_gem_add`→`plr_ponsrv_gemport_add`→remote
GEM adapter uses API queue1 as priority0; the actual flow instruction range
`a6a28..a6a7c` composes Related Port`0x80010000` for T-CONT`0x8001`.
The service notification has a1147-byte FPGA layout, distinct from the
1074-byte ASIC layout, and the comparison uses the target-specific entry.

`plr_api_remote_onu_uni_mcast_enable` preserves the caller's UNI type:
type0 selects Ethernet and type2 selects VEIP regardless of the VEIP-card
count. The FPGA1 default producer explicitly passes type2. A missing
configured target returns18 before any OMCI write; it does not fall back to
Ethernet. This differs from the ASIC default wrapper's card-count selection.
After selecting the port, the actual FPGA profile/GEM producers and baseline
encoders generate the same standard309 or CTC65531, subscriber310,
monitor311, bridge47/filter84 and GEM268/281 frames as the current native
transaction. The original ignored standard Set error and incorrect CTC
failure cleanup also exist on FPGA1; the native transaction keeps its
stricter checked rollback.

The extended original comparison covers72 UNI/profile/reference/bridge
selections for API ONU1/6/64,48 operation/cleanup failures,8 multicast-GEM
cases and two missing-UNI cases. `test_fttr_mcast_omci.py --fpga` executes
both Ethernet and VEIP native transactions against that target's own
encoders, covering standard/CTC, table readback/adoption, ambiguous doubled
tables, mutation refusal, applied-write timeout, cancellation and drift.
ASIC regressions and the default producer/profile/queue oracle also pass.
Local evidence: `artifacts/fttr/fpga1-mcast-comparison-20261108.json`,
`artifacts/fttr/fpga1-mcast-native-20261108.json`,
`artifacts/fttr/fpga1-default-queue-20261108.json` and
`artifacts/fttr/asic-mcast-regression-20261108.json`.

This is original-implementation verification, not newly enabled FPGA1
`factory` support. The default local grant remains unresolved: the retained
FPGA supervisor has no `SetOnuTcontDba` import, its service bandwidth and
ONU-enable driver wrappers are no-ops, and the inspected initialization does
not establish normal service grant values. Measurement-only temporary
writes remain excluded. Queue/OMCI equivalence does not prove physical
bandwidth, and no guessed grant or preserved foreign authorization is used.
No device was accessed.


### Retained ONU status is not a saved service recipe

The target configd descriptor identifies MIB chain3209 as
`FTTR_MASTER_ONU_TBL`, with107-byte records and a16-entry limit. Its13 fields
are ONU ID, MAC, serial text, online/rule state, internal VID/interface,
down-cause/timestamps and SFU ID; it contains no authentication method,
PLOAM/LOID credential, T-CONT/GEM definition or bandwidth profile.
The complete `onuinfo` status loader at`0x406208` converts those records to
its in-memory observation table, skips failed record reads and stops at16;
no ONU/service configuration API is called in that path.

The separate `onuinfo reset` path calls`0x4062a4`. For active records it
invokes `onu_setup.sh` action2 (inactive-interface cleanup), then clears
ONU ID/active/VID/interface/rule fields. Executing the target function with
command results0,1 and127 shows that it clears the record even after command
failure. That is not proof that old forwarding has retired. Native startup
continues to close forwarding, verify local port cleanup, archive prior
journals, authenticate and obtain fresh MIB state; it does not import these
operational fields as authorization or recreate the old interface from XML.

`plr_ponsrv_tcont_data_save` also does not establish on-flash service
persistence:12 complete target calls, plus successful-case duplicate calls,
execute the real ordered-list, profile-reference, used-bandwidth and Alloc-ID
bitmap helpers. They retain a profile pointer in an allocated34-byte
in-memory node; missing ONU/allocation and duplicate-node cases fail without
file or MIB writes. This bounds only these inspected paths, not every possible
external configuration source. A general saved XML service/profile format
has not been identified, so no invented converter is added. Evidence is
reproducible with `tools/trace_stock_fttr_saved_state.py`; the local report is
`artifacts/fttr/saved-status-20261108.json`.

While checking the native policy restoration boundary, a concrete parser
failure was reproduced: `--services` accepted a valid row followed by an
embedded NUL and ignored the rest of that line. It now rejects embedded NUL
bytes as the existing Wi-Fi policy parser already does. Focused tests execute
the actual service parser for valid explicit/default rows and corrupt rows,
including a corrupt line after an earlier valid policy. Full-daemon tests
verify rejection before backend/configuration work and preserve all supplied
MiniOLT/NI/state sentinels. Native and OpenWrt-musl daemon builds pass with
`-Wall -Wextra -Werror`; no kernel/firmware change or device access was needed.
This fixes corrupt native policy handling and narrows the factory-import
research gap; durable remote OMCI ownership recovery remains incomplete.

### FTTR packet generations across software queues

The native forwarding gate previously checked the current boolean at P6
submission/receive and purged the parent qdisc, NAPI GRO and QM reassembly.
An already-complete skb held in RPS, a neighbour queue, NFQUEUE, another
netdev's qdisc or mac80211 could survive that withdrawal. Checking only the
reopened boolean could then allow an older service packet to leave after a
GEM reassignment. The original EXT_SWITCH_EN marker identifies origin but is
not a packet-lifetime fence; original ES/Hash withdrawal still does not prove
complete hardware retirement.

A built-in64-bit FTTR generation now covers native CPU business packets.
Odd generations are closed; zero in an skb means unrelated traffic. The
counter survives native driver unload and never wraps to a previously used
identity. RTNL serializes transitions; P6 closes the generation under its
existing TX lock before waiting for NAPI/qdisc cleanup. Successful forwarding
enable opens a new generation; a failed enable leaves it closed and removes
the pending FE request. Closing the shared multicast endpoint also invalidates
queued FTTR CPU copies while preserving an already-closed parent state.

P6 business RX is stamped before report redirection/GRO, including source6
hardware multicast copies delivered through the validated host-copy callback.
Native per-GEM VLAN and shared-multicast egress are stamped before enqueue,
NFQUEUE hold and routed neighbour resolution. An existing stamp is never
replaced. The parent exempts only its configured untagged OMCI protocol;
AF_PACKET's protocol argument cannot disguise a VLAN in its actual Ethernet
header. Other ports' unrelated packets remain unstamped.

`230-fttr-packet-generation.patch` adds checks at Linux receive-core entry
(including RPS and repeated VLAN/virtual-device rounds), enqueue and final
hard-start after qdisc/BUSY requeue. NFQUEUE reinjection rejects a stale skb
before repeat hooks or its saved continuation. IPv4/IPv6 fragmentation and
mac80211 fragmentation preserve the generation and FTTR origin; skb header
copy/clone/GSO copies preserve it through the existing headers group. GRO,
skb coalescing, IP fragment reassembly and software A-MSDU aggregation cannot
mix different generations. mac80211's final driver call and native radio TX
submission check again after software PS/TXQ waiting. NI checks again under
its own TX lock before copying a packet into DMA-owned storage. The bridge
wire/forwarded and multicast-capture hooks also refuse stale packets, so an
old queued skb cannot become a fresh hardware-learning sample.

`test_fttr_packet_generation.py` executes the real core helper, Linux
hard-start/BUSY retry, NFQUEUE enqueue/reinject, metadata-copy, GRO selection,
IP fragment insertion and mac80211 final TX functions with modeled skb/netdev
storage. It exercises close/reopen, existing-stamp preservation, mixed valid/
invalid TX lists, queue-bypass attempts, exact OMCI exemption, raw AF_PACKET
VLAN headers, cross-generation aggregation/reassembly and counter exhaustion.
Source checks cover the surrounding receive, fragment, neighbour and A-MSDU
hook positions; they are not live-kernel queue tests. Production P6 gate tests
also execute shared-endpoint invalidation and failure cleanup. The actual
radio submit test rejects a stale packet before DMA or PE publication; existing
BAR and90 BCMC submissions with45 linked-PE descriptor checks pass. The768
QM P6 receive cases, held-RX purge, multicast endpoint and bridge learning
regressions pass. The old isolated QM prefetch fixture lacks prior FTTR
reassembly fields and did not compile; the complete P6 QM test supplies those
fields and executes the actual poll instead.

The new patch applies with zero fuzz and reproduces all13 changed kernel
sources. Linux6.18.55 Image/modules compile with the actual native core,
NFNETLINK_QUEUE, mac80211 and radio driver
(`artifacts/fttr/generation-kernel-final-20261107.log`). These checks establish
software metadata/admission behavior and build consistency. They do not
purge data already delivered to application sockets, represent newly created
application responses, or prove retirement of packets already copied into
DMA/AFT, PE, ES or MiniOLT hardware. Direct NI→PE copies remain protected by
their existing immutable recipient/generation records and retirement gaps.
Hardware IDs remain quarantined; complete packet retirement and safe reuse
are still unfulfilled requirements.

### FTTR generation revocation inside the PE transmit queue

The CPU skb generation now survives the ARM-to-PE request boundary. Previously,
an accepted request could wait in the PE's private16-slot queue after FTTR
closed/reopened, then reach PCIe when TXBD credit returned. ARM stores the
original64-bit stamp in request words28/29, after the maximum ten address
pairs. PE copies those words with the request and compares them again before
constructing a WD/TXBD. Zero remains unrelated traffic. This applies to CPU
unicast and BCMC requests on all six channels, with or without NI offload.

The existing coherent multicast table's reserved header words4/8 now hold
the FTTR low/high generation; cookie record offsets and allocation size stay
unchanged. A built-in IRQ-safe registry owns two borrowed mirror pointers.
Registration and generation publication share one lock, independent of PCI
probe's RTNL state. ARM writes odd low, high, final low with DMA barriers;
PE reads low/high/low with barriers and declines partial snapshots. Both
mirrors update before the CPU generation opens. An independent capability
`FTG1` prevents the driver from using firmware lacking the new request ABI.
Unregister closes the mirror and removes its pointer before DMA free, after
PE context join; failed NI withdrawal or PE join retains the allocation and
subscription. MAC80211 unbind joins the discard worker before clearing `hw`.

A stale request consumes no TXBD or WD credit. PE publishes one release word
per ARM WP in context memory `0x1e400..0x1e7ff`; ARM polls only while accepted
FTTR WPs remain pending. This works on radios without the NI event timer.
ARM acquires the release, clears it before making the WP reusable, and uses
the existing completion path with explicit software-drop status4. It releases
the DMA mapping and skb, wakes ownership waiters/queues, and skips sequence
readback, ACK, retry/rate and AMPDU-air feedback. The existing status file
exposes `FTTR_PE_software_discarded`. The release is software-generated and is
not a hardware RPQ completion or proof of transmission.

PE now owns continuous TXBD producer positions before as well as after NI
binding. Dropped ARM requests therefore cannot leave holes in the hardware
ring; NI setup no longer overwrites those positions with ARM request counts.
WD retention and hardware RPQ ownership of actually published packets retain
their prior behavior.

Original evidence is target `rtk_wifi6.ko`:
`_phl_wp_rpt_statistics@0x195e68` excludes status4 from air success/failure
counts, `_phl_wp_rpt_chk_txsts@0x196054` forwards status4 to the completion
callback, and `phl_recycle_payload@0x196314` hands ownership back before clearing
the WP tag. `trace_stock_radio_software_drop.py` executes the first two actual
functions for72 age/channel/status combinations, with explicit modeled clock,
HAL and feedback boundaries. Reference `phl_trx_def.h` names status4
`TX_STATUS_TX_FAIL_SW_DROP`; target disassembly also branches explicitly on4.
The native generation/release ABI integrates this ownership distinction into
our PE framework; it is not claimed to be a factory FTTR generation API.

Validation executes69 linked-MIPS main-loop scenarios: six rings, NI on/off,
64-bit carry, partially published generations, closure during a PE read,
ten-segment request capture, zero-credit pause drain and same-WP reuse without
a TXBD hole. `test_fttr_pe_completion.py` executes the actual ARM worker and
completion for257 partial/full WP batches under ASan/UBSan, checking exactly
one unmap/free, no sequence/air feedback, retry and reuse. Core registration,
two mirrors, duplicate/full registration, unregister, rollover and exhaustion
tests pass. Existing complete bridge learning, original multicast actions,
binding failure/join lifetime, key/BA retirement, dual-PE publication, late
ARM producer acquire, BAR and90 BCMC submissions/45 linked descriptors pass.
Firmware is26692 bytes, SHA256
`1427f939eed52d0c661f636c51c3f232954fdda58a1e208ff1bc824b3c2d67bd`, with
the conservative6896-byte stack bound within8192-byte DMEM. Linux6.18.55
Image/modules build uses the same source and generated firmware header
(`artifacts/fttr/pe-generation-kernel-final-20261107.log`).

No physical device was accessed. A packet already passing the final check
can still enter a WD/TXBD before closure; already published PCIe, NI DMA/AFT,
ES and MiniOLT packets remain outside this revocation guarantee. Direct
NI-to-PE copies continue to use their immutable cookie records. This increment
does not establish a complete producer barrier for P6 ES flushing and does
not release quarantined group/cookie/action IDs for reuse.

### Waiting for accepted CPU radio requests during FTTR withdrawal

The generation mirror rejects requests still waiting in PE, but it cannot
revoke a WD already published to PCIe. The FTTR control path now waits for
all accepted CPU radio requests to return their payload ownership before it
reports successful withdrawal or reopens a generation. This extends the CPU
radio path through its actual completion; it does not reset the shared DMA
engine or assume an empty hardware producer/read pointer means completion.

`rtl9617c_fttr_tx_get` checks the skb's original generation and increments a
built-in pending count under the same short lock used to close the generation.
Radio TX calls it after mapping and every fallible admission check, before
publishing either a PE request or a direct PCIe descriptor. A sender already
mapping when closure occurs is rejected and unmapped without acquiring a
reference. Unrelated generation0 packets do not contribute. Accepted slots
retain their generation independently of skb lifetime; normal RPQ and PE
software-drop completion decrement only after DMA unmap and skb disposition.
Joined device teardown also releases retained references after unmap/free;
failed NI/PE teardown keeps them. Unregistering a radio's generation mirror
alone cannot make its unfinished requests disappear.

`fttr_forward` closes the generation and FE, waits up to1000ms for the pending
count to reach zero, then configures FE and opens business forwarding if
requested. `rtl_fttr_stop` also waits, and multicast-only close now returns
its error. Because Linux ignores `ndo_stop` errors, `fttr_mcast_flush` retries
the complete multicast quiesce and wait before acknowledging the resident
controller. Timeout returns `-ETIMEDOUT`, leaves the generation closed and
withdraws any pending FE enable. A core-level `-EBUSY` check additionally
prevents reopen with outstanding references. Late genuine completions permit
an explicit retry; no timeout clears the count or fabricates a completion.
The drain sleeps under RTNL without NI, FE or radio spinlocks; normal RPQ and
PE discard workers do not acquire RTNL.

The target factory module's `phl_recycle_payload@0x196314` supplies the
ownership contract. Extended `trace_stock_radio_software_drop.py` executes
the entire function for216 combinations of six DMA channels, six status
codes, three WP IDs and callback success/failure. The real target code invokes
the payload callback while its WP tag is still populated, retains that tag
and its pending count on callback failure, and clears/decrements them after
success. The oracle models the clock/HAL, absent station, feedback/payload
callbacks and BH-enable boundary; it does not emulate physical radio DMA.
The existing72 target status/statistics cases remain passing.

Validation includes the actual core's0..512 pending-reference cases, closed
admission, partial completion, timeout, reopen refusal and delayed retry.
`test_fttr_radio_drain.py` executes the production helpers with pthread-backed
locks/waits for256 concurrent dual-radio completion and sender/close races,
plus missing completion across mirror unregister. Its timeout duration is
shortened in the fixture. Actual ARM submission tests reject a sender after
mapping and leave no accepted reference. The16448 existing RPQ batches and257
PE software-discard batches verify that each accepted reference is released
once after unmap/disposition; ownership-failure teardown retains it until join.
Production forwarding/multicast callbacks exercise drain timeout, closed
retry, FE cleanup and later successful retry. Linked MIPS RPQ regression still
forwards256 ARM records and handles1024 NI MPDUs/2048 BID returns. BAR, BCMC,
P6 gate and multicast endpoint tests pass, as does Linux6.18.55 Image/modules
(`artifacts/fttr/radio-drain-kernel-final-20261107.log`). PE firmware is unchanged.

This is a bounded completion barrier for CPU-origin FTTR radio payloads,
including requests which raced the last PE check before closure. It does not
cover direct NI-to-PE copies, NI DMA/AFT/ES/MiniOLT queues, packets already
delivered to applications, or retained hardware WD pages. It therefore does
not authorize group/cookie/action or wire-GEM reuse and does not yet permit
connecting the P6 ES flush helper as a complete retirement guarantee. No
physical-device test was performed.

### Direct NI-to-PE FTTR BID and EPP-prefix drain

Direct NI-to-PE packets bypass the CPU skb reference count. Their lifetime
now carries the existing64-bit FTTR generation in BID headroom words5/6,
below HEADER_A at byte64 and below the earliest radio header rewrite. The
source predicate uses HEADER_A logical source6, the native P6 identity; owned
multicast replicas already preserve original source with `keep_o_lspid`.
It applies to ordinary peer selection and multicast-cookie selection. A
source6 arrival while closed is marked with a nonzero closed identity and
sent to the discard queue. Other sources receive generation0. Selection and
final TXBD publication reject stale source6 requests, and A-MSDU gathering
requires equal generations as well as equal cookies.

Each source6 BID increments a PE-owned pending count when its EPP is consumed,
before it enters a queue or the excess-arrival discard WP. The count persists
through sleeping queues, selected unpublished requests, published WPs, RPQ
completion and partial EQ recycling. PE samples the generation before PUSH,
issues the EQ PUSH and its fence, then decrements; it never reads the BID
after returning it. The forced ARM cleanup similarly saves link/generation
before PUSH, reads back the port, then decrements only for returned FTTR BIDs.
A timeout retains the unreturned suffix and count for retry. Normal NI release
already checks queued/EPP and WP owners before switching pool geometry.

The radio now registers a second borrowed coherent status pointer in its
existing context at `0x1e800`. The layout contains ARM request, PE ACK/pending,
last-seen request, eight EPP targets and a target mask. A fresh nonzero32-bit
request is published **after FE withdrawal on every drain attempt**, including
retries within the same closed generation. PE snapshots all eight producer
positions and acknowledges only after each nonempty captured prefix has been
consumed and the FTTR BID count is zero. The ingest loop favors unfinished
captured COS prefixes so sustained high-COS traffic cannot starve a lower-COS
cutoff. Invalid/not-ready EPPs do not advance the target. PE does not sample
geometry while NI is paused; it resumes after ARM finishes the transition.

The core drain now polls the two registered ACK/count pairs and the CPU radio
reference count within one1000ms deadline. Registry locking protects borrowed
pointers against successful radio detach/free without holding any pointer or
NI/FE/radio lock over sleep. CPU completion uses release decrement and drain
uses acquire read. ACKs from an earlier request or generation cannot permit
reopen. A newly registered radio must acknowledge the current request too;
sequence exhaustion returns an error without wrapping. Firmware capability
is now `FTG2`, rejecting a firmware/host ABI mismatch. There is no recurring
host poll for direct NI traffic: polling occurs during the bounded control
operation only.

Factory evidence remains the target HEADER_A/EPP64 and EQ PUSH contracts,
original MCE source retention and final-recipient actions, and radio RPQ/WP
ownership. `test_fe_mcast_pe_route.py` executes six target FC/NI action recipes
and40 native pool/map/header fault cases. The new generation/request/ACK
protocol is our integration with that contract, not a claimed factory API.

`test_fttr_ni_pe_drain.py` executes249 linked-MIPS scenarios: seven source
ports, eight COS queues, short/full HEADER_A, ordinary/cookie paths, EPP wrap
and invalid descriptors, all-COS progress, sleeping/selected/published WPs,
actual RPQ, EQ credit stalls,2/7/8-BID aggregate partial return, mixed-source
aggregation exclusion, paused geometry and closed late arrivals. Core tests
cover stale ACK, one missing radio, pending BID despite ACK, new registration,
retry and sequence exhaustion; the256 concurrent CPU drain/admission cases
still pass. Forced ARM teardown tests poison each returned BID and verify
partial retries never reread it or lose FTTR counts. Existing MIPS publication,
265 multicast admission/revocation cases plus12 encrypted TX/RPQ cases,
69 CPU request generation cases,1024 NI MPDUs/2048 BID RPQ returns, PS budget,
gathering, key/BA, binding, bridge, BAR and BCMC regressions pass.

Firmware is28468 bytes, SHA256
`cfa33f3919e8907551f8e4d69fca2a80c354e9cbd0cdee88c655371bd8f5cfc7`,
with conservative6960-byte stack usage within8192-byte DMEM. Image/modules
and source/generated-header identity checks pass
(`artifacts/fttr/ni-pe-drain-kernel-final-20261107.log`). No device was accessed.

This acknowledges already ingested source6 BIDs and the captured EPP prefix.
It is **not an upstream MCE/NI DMA/AFT production fence**. A source6 packet
arriving after the ACK is rejected/counts while closed, but a packet still
upstream until after reopen can still acquire the new generation at ingest.
The next required retirement step is to stop/prove completion of those upstream
producers before this drain. P6 ES/MiniOLT retirement, retained WD pages and
group/cookie/action/GEM reuse remain unresolved; hardware IDs stay quarantined.

The2026-11-07 upstream review did not find a sufficient empty predicate.
MCE0xaa60 `buf_flush_en` has a register definition but no established target
drain sequence; ingress/egress counters cannot be compared because replication
changes packet counts. AFT0x1f1c/0x1f28 FIFO counts do not establish retirement
of active contexts, and FE PP-ready is backpressure readiness. RXMUX empty
bits cover only those buffers; the original DMA pointer/EMPTY checks retain
the already documented two prefetched descriptors. None of these observations
is used to acknowledge full retirement or release a quarantined hardware ID.

### Durable remote service ownership and read-only restart recovery

The resident controller now accepts `--service-state DIRECTORY`; the package
uses `/etc/tyfza1-fttr-services` with mode0700 and a directory lease. The
versioned, endian-defined, CRC-protected record binds ONU serial and hardware
slot, backend, both controller envelope MACs and framing. It retains each
service configuration, selected ME IDs, exact attribute groups, created versus
borrowed ownership, original update-only attributes, prior DBA, full before/after
UNI VLAN tables, changed-row bitmap and multicast ownership/table evidence.
No process pointers, file descriptors, credentials or kernel-resource ownership
are serialized. Records are bounded at32MiB per ONU; allocation/format errors
stop the operation.

Before the first remote service mutation, the controller fsyncs an intent file,
renames it and fsyncs the directory. Full ownership is committed only after the
whole service collection succeeds, before local bridge admission. An explicit
release first persists a pending state containing the original committed recipe;
it removes the record only after remote release completes. Repeated read-only
sync polls do not rewrite flash. A storage failure propagates before the pending
mutation or before admitting local data.

On restart or reconnect, authentication and fresh inventory still run. A saved
committed collection must match the selected policy and the newly planned complete
UNI/ME selection. Recovery reads every selected create/set attribute, full VLAN
table and multicast table without remote writes, with ONU Data sync reads before
and after; it then checks the same registration identity, restores/readbacks local
DBA and enters the existing MIB/FE/netdev admission checks. Original touched flags,
previous attributes and VLAN rows survive, so a later explicit move can restore
and release the resources it actually owns. Current values alone never grant
delete rights to a controller without a record. The original factory service
producers, queue adapters, attribute encoders and checked release transactions
remain the behavior basis; the persistent journal is the native integration.

A complete fresh MIB showing every saved service-created object absent and the
update-only attributes back at their saved prior values permits retiring the old
record and normal reprovisioning. Partial disappearance, changed attributes,
changed policy/UNI inventory, another slot/controller/backend, corruption and
uncertain initial/release transactions retain the record and refuse automatic
replay. Status exposes `service_error`, `service_recovering` and
`service_durable`. Partially completed releases now use the checked recovery below;
new initial-write journals support checked rollback as described below; legacy state0 records still lack that evidence. Removal of a
policy does not implicitly delete a committed active service, but does not cancel
an already-journaled release.

`test_fttr_service_store.py` executes the production codec under ASan/UBSan:
824 single-byte corruptions,23 bounded dimension faults, identity/slot/backend,
private permissions, symlinks, exclusive lease, both fsync failure points, pending
records and preservation of a release recipe. The actual daemon/CLI namespace
suite with original OMCI encoders covers ASIC/FPGA1/FPGA2, ordinary and shared
Ethernet/VEIP, standard/CTC multicast and a seven-service collection: SIGKILL,
read-only restart, drift, full MIB reset, UNI expansion, restored migration,
interrupted Set/release, planner ENOENT, partial local DBA failure and persistence failure. Existing19 upgrade scenarios, service
policy and administration regressions pass. Native and aarch64-musl builds use
`-Wall -Wextra -Werror`. Reports are local under
`artifacts/fttr/service-store-*-20261108.*`. No device was accessed; these tests
establish neither physical flash power-loss behavior nor queued-packet retirement.

The branch also incorporates local main953cf7c CPU EQ credit batching. Its729
credit/FIFO/resume cases, original P6 QM register/18 fault-cutoff comparison and
complete multicast learner regression pass. Image/modules compilation and both
changed kernel input identities pass
(`artifacts/fttr/service-store-main-kernel-20261108.log`). No PE firmware changed.

### Automatic UDP broadcast replication

The complete bridge transaction now includes received Ethernet broadcast UDP
frames from `br_flood`, using Linux's actual `BR_BCAST_FLOOD`, proxy-ARP,
VLAN and port-isolation selection. It still requires complete successful copies,
a source FDB match, no incompatible packet hooks/qdiscs, exact source/destination
MAC/IP/UDP ports/tags and current admission generations. ARP, non-UDP, fragmented
and locally originated frames remain on their existing software path; this is
not a claim of complete broadcast-protocol acceleration.

The original `__l3_cls_key_entry_update` execution confirms two independent
IF_ID slots: slot0 requires MC=1/BC=0; slot1 requires BC=1 and ignores MC. Both
WAN0 and LAN128 now own both associated FIB entries, selecting native profile1.
The second slot does not assume that the parser marks broadcast as multicast.
Default misses remain CPU16, including non-UDP broadcasts with no learned key.
Profile ownership snapshots/restores both FIB entries and publishes the valid
classifier only after their readback. Existing explicit TC restrictions are
unchanged.

FTTR broadcast follows its existing original transport policy: shared
`fttr-mc` drops broadcast, while per-ONU business GEM VLANs carry it. The learner
therefore skips the already-dropped shared output and validates each selected
business VLAN before generating its P6 replica. It preserves the source MAC,
never inserts VID4095 for broadcast, and permits a P6 ingress to reach a different
selected ONU VLAN on the same physical P6. Full FTTR admission is required; shared
multicast admission is not substituted for business-VLAN authorization. CPU-local
copies retain their original ingress tags and pass through normal bridge-local
checks without another flood. The existing direct AP BCMC and STA peer paths
accept the resulting complete broadcast records with their key/generation rules.

Offline validation includes20 actual Linux flood-selection/capture/FE/NI scenarios,
ONU301-to-302 broadcast/closed-admission cases, independent BC/MC classifier
truth tables converted by the original module,616 profile fault/applied-timeout
cases and2560 source/group/GEM masked-key CRC comparisons including broadcast.
The complete broadcast FE record executes in the linked PE through HIQ/GTK/shared
sequence and actual software RPQ/EQ handling, with98 BCMC regressions.139 parser
byte cases,480 production QM poll cases,80 bridge-host cases,45 Linux selection
cases, existing learner/TC/CPU generation/multicast gate and core tests,18 original
ITU/EPON action recipes and206 route failures pass. Image/modules and five changed
build-input identities pass; patch233 reverse/replay is byte-identical. Reports
are local `artifacts/fttr/broadcast-*-20261108.*`. Hardware IO/completion are modeled;
no device was accessed and no physical broadcast or DHCP acceptance is claimed.

The branch also incorporates mainf08ad1c's NI sleep snapshot fix while retaining
FTTR BCMC MACID0 treatment and generation-aware canceled-PS completion. Linked
selection race, PS/peer withdrawal, FTTR and multicast prefix-drain, CPU stamps,
multicast/BCMC and IMEM tests pass. Repeated PE builds are identical:32540 bytes,
SHA256 `e7c00be968237405ed2d69f32fe79589115925df9c75001056edcc7ef5647656`,
conservative stack7304/8192; the non-uplink build is14168 bytes. Hardware IDs remain
quarantined; this change does not establish the missing upstream/downstream
production fence or permit their reuse. Multi-VIF remains unsupported by the
underlying single-role wireless driver and TSPEC accounting remains open.

### Recovering an interrupted remote service release

A bound `FTS1` record with state2 now loads its original committed ownership and
continues the interrupted release after authentication and fresh MIB inventory.
This happens before Wi-Fi or new service provisioning, even if the service policy
was removed. Local FE/VLAN/DBA withdrawal completes first. The saved recipe still
must carry release authority; ordinary adoption without original ownership does
not gain permission to delete. State0 initial-apply uncertainty remains blocked.

The existing reverse service/UNI/object dependency order is retained. Before
removing an owned ME, checked recovery accepts either its exact configured
attributes or an unknown-instance reply; before restoring an update-only ME, it
accepts either the configured value or the saved prior value. Already completed
operations lose their in-memory touched flag and are skipped. Any other value
or failed Get retains the persistent intent and stops further dependent cleanup.
Borrowed objects are preserved. These are target original baseline Get/Set/Delete
operations, not MIB reset or unconditional object deletion.

UNI VLAN recovery reads the complete current table and accepts only the original
prior table plus a subset of the at most three rules still awaiting restoration.
Unchanged rows must match and foreign additions/edits reject recovery. Only the
remaining changed rows are restored, with the existing final full-table readback.
An absent class171 instance is acceptable only if its original journal identifies
an owned instance (including the earlier owner in a shared collection). A later
service which never changed its borrowed table does not rewrite it. Synchronous
completion of these skipped tables now advances the service dispatcher correctly.

Multicast recovery checks its attributes before removing the IPv4 row. An already
empty table or already deleted owned IW skips the delete Set. This matters because
the original ONU inserts a zero-address ACL when a delete is repeated against an
empty table. Existing no-retry/readback behavior remains active for a lost delete
reply. A fully absent multicast plan no longer emits an uninitialized request.

Completion is reported as `service-released` with local business traffic closed.
The original interrupted move's target is not stored in this record, so recovery
does not guess a target or silently reprovision. The release record remains across
shutdown/disconnect and another restart verifies completion without remote writes.
Explicit `onu-admin ID SERIAL resume` may remove it only after this controller has
completed remote cleanup and the existing local withdrawal, locked driver action
and same-serial readback succeed. A failed resume retains the hold. FPGA resume
can release registration; a new discovery is required, not inferred from its ACK.

The actual daemon/CLI/namespace suite covers every applied remote release write:
65 cuts in an ASIC seven-service collection,35 in FPGA1 shared standard multicast,
14 in an FPGA2 ordinary service and35 in ASIC shared CTC. It also covers foreign
attribute drift, repeated recovery, removed policies and explicit resume. The
original encoder comparison tests nine partial multicast states for every
standard/CTC and single/doubled-table combination (both ASIC and FPGA1), including
its delete-empty behavior. Nine ASan/UBSan VLAN partial/foreign-table cases,
original GEM/ownership and VLAN CLI regressions, durable codec, admin failure and
19 upgrade scenarios pass. Daemon and CLI compile for the host and aarch64-musl
with `-Wall -Wextra -Werror`. Evidence is local under
`artifacts/fttr/release-recovery-*-20261109.*`.

No kernel or PE image changed. No device was accessed, and the tests do not prove
physical flash failure behavior, ONU interoperability or packet retirement. Initial
provisioning crash recovery, arbitrary hardware reservation repair, complete upstream
and downstream retirement and hardware-ID reuse remain open. Persisted move-target
recovery for an intact or fully completed reservation is described below.

### Durable migration targets and reservation reconciliation

The resident controller now persists each accepted migration in the existing
private service-state directory as a64-byte `FTM1` record. It binds source/target
slots and both serials, whether remote service ownership is required, backend,
controller MACs and OMCI framing, with a version marker and CRC. The file is
fsynced, atomically renamed and its directory fsynced before any remote cleanup;
a second durable stage is written after remote release and before the original
MiniOLT reservation operation. One controller lease serializes migrations.
Neither record contains credentials or process pointers.

Recovery checks the original hardware order already established by
`test_fttr_onu_move.py`: reserve target SN (vendor then serial word), deactivate
source, clear source SN. An intact original source/target pair resumes the saved
target after fresh source authentication and MIB inventory. With prior services,
the original committed or release-state journal remains required and its checked
cleanup runs again; already completed remote operations are skipped. Without
services, the existing inventory-only migration check still rejects foreign GEMs
or assigned T-CONTs. Removal of a current service policy does not erase an already
accepted migration target. Authentication failure retains both reservations and
journals without performing service cleanup or a move.

If the source SN is empty and its registration is inactive or blocked, while the
target has the requested SN and is not blocked, the reservation stage is complete:
recovery removes obsolete service ownership and then the move record without
reissuing the command. The normal successful path now performs this same readback
before dropping durable intent. A target/source half-write, target conflict,
changed identity or missing required ownership fails and retains evidence. The
original write-prefix recovery described below handles specific partial states;
ambiguous states are still rejected. A failed stage fsync
prevents the reservation command. Status exposes `move_recovery` with recorded
state, phase and the two slots; reservation completion is not registration proof.

A full procd-style startup normally resets MiniOLT. With pending move intent,
it instead validates that record against the existing OMCI envelope, requires
live host/peer link and valid retained optical calibration, clears inherited
business grants and opens management on the retained controller. This preserves
the reservation evidence needed by recovery. Lost link/calibration or an invalid
record stops startup without using reset to manufacture a known state. Power
loss that loses those hardware conditions still needs explicit recovery work.

Actual daemon/CLI/AF_PACKET/bridge tests run15 scenarios on ASIC, FPGA1 and FPGA2:
release interruption, policy removal, the gap between move intent and release
journal, driver-before-write and completed-write/lost-reply crashes, partial target
write, occupied target, authentication failure in both stages, missing ownership,
stage/initial storage failure, inventory-only before/after crashes and lost link.
The complete startup arguments are exercised on retained hardware models, and
all three normal factory-import/startup regression suites pass. Original relocation
write/fault comparisons,64 record byte corruptions, both fsync failure points,
separate vendor/serial partial observations, permissions and symlinks pass; existing
move/release and19 upgrade scenarios pass. Host and aarch64-musl daemon builds use
`-Wall -Wextra -Werror`. Local evidence is
`artifacts/fttr/move-recovery-*-20261109.*` and `move-startup-*-20261109.*`.

This is control-state recovery integrated with native FE/VLAN/DBA withdrawal;
it does not establish complete packet retirement or safe GEM/action/group reuse.
No device was accessed and no kernel or PE code changed. The worktree includes
local mainda681f8 (release notes); nothing was merged back to main.

### Recovering original partial MiniOLT reservation writes

The native MiniOLT driver now exposes `onu_move_recover`, separate from ordinary
`onu_move`, and the controller calls it only for a bound durable reservation-stage
record after local FE/ports/business DBA have been withdrawn. Its accepted states
are prefixes of the target original write order: target vendor word written while
its serial word is still old; target fully reserved with source identity intact;
or source administratively blocked with only its vendor word cleared. The complete
source-cleared/target-reserved state needs no writes. An entirely unchanged pair,
including a target which already had the same reserved SN, continues through fresh
source authentication/inventory rather than this recovery-only entry.

The driver checks both source and target identities, rejects a registered or
blocked target for partial repair, reads every data grant for both slots, then
rereads both identities/downstream states under the existing control mutex. A
conflict or grant prevents writes. It finishes a partial target with only the
remaining serial word and its readback. Before source deletion it rereads the pair
and repeats the original deactivation sequence if the full source SN still exists:
the block bit alone cannot prove that the PLOAM mailbox command was sent. FPGA1
still clears upstream state after its original delay. An already half-cleared
source only needs its remaining serial word cleared and verified. Errors preserve
the durable intent for retry; no ID-reuse or packet-drain claim follows from a
successful reservation update.

`test_fttr_onu_move.py` executes the original relocation implementation, creates
all prefixes from its actual non-read IO sequence, and tests6321 native prefix,
IO-failure and retry cases across three backends and source/target edge IDs. It
also checks user/kernel state interpretation, read-only repeat, stale identities,
occupied targets, outstanding DBA, source changes during the grant scan and after
target writes, and false-success readback. The existing ordinary move's original
write sequence remains unchanged. The full daemon namespace test now invokes the
actual native recovery function behind modeled registers, covering19 scenarios
per ASIC/FPGA1/FPGA2, including all four partial/conflict extensions. Migration,
standalone release recovery and19 upgrade regressions pass.

Image/modules compilation, two changed kernel-input identities, host/aarch64-musl
daemon builds,64-byte record corruption/fsync tests and admin lifecycle tests pass.
Local reports are `artifacts/fttr/partial-move-*-20261109.*`. PE firmware is unchanged.
No device was accessed. A persistent reservation intent authorizes finishing the
already admitted transaction when source authentication is no longer possible
because its SN is half-cleared; it does not authorize replacing an unrelated SN.
Unknown hardware state, full power-loss recovery, legacy initial-write records,
complete upstream/downstream packet retirement and hardware-ID reuse remain open.

### Initial service-write checkpoints and interrupted provisioning cleanup

Initial resident provisioning now writes a full `FTS1` state3 checkpoint before
each remote service Create/Set/Delete, including rollback mutations. The existing
private directory, binding, CRC, atomic rename and file/directory fsync rules are
retained. The checkpoint contains the complete selected multi-service plan and
only the ownership actually attempted so far, plus original update-only attributes
and complete prior VLAN tables. The pending operation is marked in a private copy
before send; serialization never changes live touched flags or verification state.
A persistence failure stops before that remote mutation. No periodic sync polls
write these records.

A created ME which has not yet completed verification can lack its later Set
group; recovery checks its exact Create attributes before deletion. Already
verified objects retain both attribute groups and reject external Set drift.
Update-only objects retain their actual prior attributes. VLAN intent records
the current row before send and requires its complete prior table; unfinished
read buffers are not serialized as evidence. Multicast table intent records the
original IPv4 row only after its empty precondition, and skips untouched plans.
Unattempted objects never acquire deletion rights.

On restart, authentication and inventory precede the existing checked reverse
cleanup. It restores the recorded attempted writes instead of adopting uncertain
configuration as a working service. Objects that were never created are accepted
only through unknown-instance readback; partially applied VLAN rows use the
already implemented whole-table subset check. A no-write VLAN release now advances
the service state machine synchronously. After cleanup the state3 record now
retires only after local withdrawal and a same-identity check, then automatic
rebuilding starts fresh authentication and inventory as described below. An
initial-apply record cannot authorize an unrelated migration.

Full procd-style startup checks private service record headers before its normal
MiniOLT reset. Any interrupted record selects retained-controller recovery with
live link/calibration checks, avoiding destruction of the remote state needed for
cleanup. This precheck grants no ownership; later full record loading validates
CRC, controller/backend/serial binding and recipe bounds. Legacy state0 records
remain unrecoverable because they never stored prior ownership. A hardware power
loss or incompatible remote change is not treated as a successful recovery.

The actual daemon/CLI/original-OMCI-encoder namespace test covers before and after
every initial mutation:69 points in ASIC seven-service provisioning,39 in FPGA1
shared standard multicast and15 in FPGA2 ordinary service (246 crash placements).
Repeated recovery, removed policy, verified attribute drift, a second interruption
during cleanup and explicit resume pass. First/last checkpoints also exercise the
full startup argument path. Production codec ASan/UBSan checks cover824 byte
corruptions,23 bounds, pre-send marking without live mutation, incomplete prior
table refusal, verified Set retention and both fsync boundaries. Original standard/
CTC multicast, partial VLAN, migration/release, admin and19 upgrade regressions,
three normal startup suites and host/aarch64-musl daemon/CLI builds pass. Reports
are local `artifacts/fttr/apply-recovery-*-20261109.*` and
`apply-startup-*-20261109.*`.

No kernel or PE code changed and no device was accessed. Recovery uses original
object/VLAN/GEM operations integrated with native FE/DBA withdrawal; it does not
prove physical flash power-loss behavior, packet retirement or hardware-ID reuse.
Automatic return to active service now follows checked rollback, fresh authentication
and inventory as described below; it never resumes an unverified partial stage.

### Automatic rebuilding after initial provisioning rollback

The resident controller distinguishes state3 initial-write checkpoints from state2
explicit release records. Only the former requests automatic rebuilding after its
checked cleanup succeeds. It completes local FE/VLAN/DBA/management withdrawal,
rechecks the same registered serial, removes and synchronizes the old checkpoint,
and starts fresh authentication and MIB inventory. The normal policy selection,
remote preflight, all-service completion, MIB sync and FE/netdev admission then
run again. A removed service policy leaves inventory only; a changed policy is
selected from the new configuration. Explicit releases remain closed and migration
records retain their separate target-driven recovery.

A crash before record removal retries checked cleanup; a crash after removal
starts ordinary discovery/provisioning. Cleanup, identity or persistence failures
prevent new provisioning, and stop requests keep the checkpoint until withdrawal
is complete. A failure in the second authentication never opens business traffic.
No automatic loop retries a newly failed provisioning within this process: it
retains the existing failure state and checkpoint for the established recovery
path. This is a fresh transaction after proven rollback, not adoption of an
uncertain earlier write.

Actual factory executable replay confirms that the ASIC default service producer
calls cfgonu_get/onu_del/onu_add after each failed service step; FPGA1 instead
returns on its first error. Both preserve the initiating error. The native durable
restart mechanism is our integration with the original service transactions, not
a claim that the factory FPGA implementation already provided it. Evidence:
`artifacts/fttr/apply-rebuild-original-20261109.json` (eight original failure cases).

The real daemon/CLI namespace suite covers all246 before/after initial-write crash
placements across the ASIC seven-service set and FPGA1/FPGA2 services. It holds the
second MIB upload to verify complete rollback, closed ports and removed old intent,
then releases it and verifies automatic reprovisioning. Policy removal, foreign
attribute drift and a second cleanup interruption also pass. Additional FPGA1/2
cases cover second-authentication refusal, changed bandwidth policy and a second
process crash after cleanup. Actual rebuild-function ASan/UBSan checks inject
teardown, identity, unlink/fsync boundary and restart failures, plus cancellation
and refusal to rebuild an explicit release. Migration/explicit-release/19 upgrade,
record and admin regressions and host/aarch64-musl builds pass. Reports are local
`artifacts/fttr/apply-rebuild-*-20261109.*`.

No kernel or PE code changed; no device was accessed. Unknown old state0 records,
full hardware power-loss recovery, saved factory policy import, remaining radio
capabilities and complete packet retirement/hardware-ID reuse remain unfinished.

### ARP and RARP remain on the bridge control path

Review of non-UDP broadcast support found a pre-existing unicast-learning error:
`fe_bridge_forwarded` could learn a unicast ARP using only source/destination MAC,
EtherType and VLANs. The target original `ca-ne.ko:aal_l3_spcl_pkt_init` explicitly
creates a WAN ARP rule with CPU16, keep-original-packet, Hash bypass15 and COS7.
`trace_stock_fttr_arp.py` now executes that producer, its real key cleaner and FIB
converter; both CAM-allocation and classifier-add errors propagate. The reference
struct declarations decode the actual target-produced rule, rather than supplying
a guessed rule. This proves the WAN initialization contract, not a universal
factory LAN ARP policy or physical ingress behavior.

Linux `br_do_proxy_suppress_arp` handles ARP/RARP before bridge forwarding and
can choose a proxy reply or suppress flooding based on operation, target protocol
address and current neighbour/FDB state. The current learned MAC/VLAN Hash key
contains none of those fields. Actual Linux-function execution demonstrates that
two packets with identical native keys but different ARP targets have different
reply/suppression decisions. Therefore the automatic bridge producer now excludes
ARP/RARP, including unicast traffic; delayed snapshots are rejected again at the
installation boundary, and the worker withdraws any old learned control rule
before refreshing its hardware age. Software bridge forwarding remains available.
Existing explicit TC behavior is unchanged by this correction.

The committed old learner reproduces the unexpected ARP queue insertion. The new
real ingress/capture/forward/install/worker tests pass for ARP and RARP, together
with12 actual Linux proxy/suppression/opcode cases and native Hash-key collisions.
PPPoE, IPv4/IPv6, non-control Ethernet editing, ONU VLAN hairpin, multicast/broadcast,
CPU-local copy and FE lifecycle regressions pass. Image/modules and the changed
kernel input identity pass. Local evidence is `artifacts/fttr/arp-*-20261109.*` and
`arp-control-*-20261109.*`; PE firmware did not change. No device was accessed.
This is a control-path correctness fix, not added ARP acceleration. Any future
ARP acceleration must preserve those dynamic control decisions and their
invalidation; it cannot be enabled by broadening the UDP multicast template.

### DMA reset failures retain payload ownership

The shared CPU DMA/AFT reset now checks that VP submission and the DMA engine
are disabled, both original reset bits asserted, and both reset bits cleared
before returning accepted multicast payload references. The original target
`ca_ni_dma_lso_reset` reset write order is unchanged; native readback establishes
whether the requested reset state was observed. An unapplied disable, assertion
or deassertion returns `-EIO` from start and is retained in `dma_state.reset_error`.
The state output also reports whether coherent memory may still be hardware-owned.
A later start/stop repeats the complete paired reset before clearing or reusing
that memory.

Completion polling does not advance software ownership after a reset failure.
Ordinary TX drops instead of publishing new descriptors, and Wi-Fi reinjection
returns the reset error without consuming its skb. Start refuses to zero coherent
buffers until reset succeeds. Final free retries an unconfirmed stop; if it still
fails, it reports the error and retains the coherent allocation and accepted
references rather than returning memory whose addresses hardware may retain.
This failed-removal case deliberately retains resources; it does not provide a
new post-unbind recovery mechanism or pretend that the retired references drained.
The existing shared engine owner stops its timer, RX and submission before reset.

`test_dma_mcast_reference.py` reproduces the old premature release when a reset
write is ignored. Its842 production-function cases now include each of the six
disable/assert/deassert write failures, reference retention despite an advanced
RPTR, rejected submissions, failed-free retention and successful retry with
exactly-once release. Start tests cover the same six failures without clearing RAM
or programming AXI reorder, then successful restart; the original16 initialization
cases remain. Wi-Fi7200 submissions/300 original descriptor comparisons, PON
PPPoE/DHCP, TX publication, RX shutdown, P6 DEEPQ, bridge multicast and FE ownership
regressions pass. Actual original DMA/AFT reset instruction replay still matches
the normal native write sequence. Image/modules and both changed kernel inputs
match (`artifacts/fttr/dma-reset-kernel-20261109.log` and corresponding identity
report); PE firmware did not change. No device was accessed.

Readback does not establish physical reset timing or downstream retirement. The
last two prefetched descriptors are still retained during normal completion;
RPTR equality and TXQ-empty are not used to release them. Full upstream MCE/NI/AFT
producer fencing, P6 ES/MiniOLT drain and safe hardware-ID reuse remain open.

### Upgrade activation and commit retain the recorded image identity

After a confirmed download, the resident controller now pins activate/commit to
the persisted Software Image instance and14-byte version. It still reads both
remote class7 records using the original attribute map, but cannot silently pick
another first-eligible slot. Missing eligibility, invalid flags or version drift
fail before the mutation and leave the prior durable record unchanged. Commit
readback also compares the pinned version, so an unrelated valid/committed image
cannot be reported as verified. A standalone action without a prior record keeps
the original first-eligible selection contract. Download selection and the
independent read-only `check` behavior remain as previously implemented.

The target relay's exported `plr_gponrm_swimg_dld_check_ver_exist` was executed
on both backend libraries with empty, missing and non-image names: it returns1
without inspecting any input. The nearby file reader loads bytes; it does not
establish a header/model/signature check. This narrows the evidence boundary but
does not implement image compatibility verification. Similarly, the examined
profile save calls still establish in-memory storage, not a known saved-service
file format; no speculative factory-import converter was added.

The production upgrade state machine now passes94 factory-wire, timeout, retry,
selection and verification scenarios, including both slots, another earlier
eligible image, version/flag drift, post-commit version change and invalid pin
requests. The old selection/verification source fails these pin-contract tests.
Actual daemon/CLI tests pass23 scenarios on each of ASIC/FPGA1/FPGA2, with download,
controller restart, competing eligible slot, failed activation/commit preflight,
unchanged durable receipts, existing crash recovery and administrative interactions.
Original target producer/window/encoder/image-selection comparisons, durable-record
and admin tests, host and aarch64-musl daemon builds pass. Local evidence is
`artifacts/fttr/upgrade-pin-*-20261109.*` and
`upgrade-pinned-original-20261109.json`. No device was accessed and no kernel/PE
code changed. Matching an ONU-reported version is not a firmware signature,
model-compatibility or physical boot verification; those remain open.

### Resident remote ONU optical diagnostics

`fttrctl STATE-DIRECTORY onu-optical HARDWARE-ID SERIAL-HEX` now requests a
fresh remote ONU optical diagnostic through the resident controller. It uses the
reported ANI-G instance (lowest ME ID in the current inventory), not a hard-coded
instance or a guessed subordinate model. The ONU must be authenticated and have
completed inventory or service configuration. Missing ANI, stale registration,
an administrative/upgrade hold or an overlapping disruptive transaction refuses
the request. The test itself does not close business ports, withdraw FE or change
DBA; other ONUs and normal MIB sync continue while it is pending.

Actual target ASIC/FPGA1 relay encoders produce the same baseline class263 Test:
operation0x52, selected ANI and test selector7. The original ONU `mib_Anig.so`
test handler emits a separate0x1b Test Result with five numbered fields:1 voltage,
3 receive power,5 transmit power,9 laser bias and12 temperature. The native
transaction requires both matching0x32 acknowledgement and Test Result, accepts
either arrival order and validates ONU/TCI/class/instance/device-ID/trailer and
field IDs. It sends once with a2-second transaction deadline; missing ACK/result,
remote rejection, conflicting results and malformed fields are errors. A sensor
whose original handler emitted an all-zero triplet is absent, not a zero reading.
All missing sensors return ENODATA. Registration replacement, same-ONU teardown
or controller exit cancels the query. No old result is retained for a new caller.

The CLI returns JSON fields `voltage_v`, `rx_power_dbm`, `tx_power_dbm`, `bias_ma`
and `temperature_c`; unavailable individual fields are null. Conversion follows
the executed target relay's centi-unit formulas, including signed fixed-point
power/temperature. Native32-bit presentation avoids the original16-bit voltage
overflow. The original higher-level getter uses dummy sentinels after a failed
test and returns success; the native interface reports the failure instead.
Neither an OMCI result nor the two-message handshake proves physical sensor
accuracy or an optical alarm state.

`trace_stock_fttr_onu_optical.py` executes both target request encoders, result
parser/conversion and the actual ONU result generator with explicit sensor/MIB
boundaries. `test_fttr_onu_optical.py` runs112 original-wire, conversion, missing
sensor, message-order, malformed/stale/timeout/cancellation cases. Actual daemon/
CLI/AF_PACKET tests pass15 cases on each ASIC/FPGA1/FPGA2 backend: normal/reversed
responses, absent sensor, rejection, missing result, timeout, concurrent request,
replacement, other/same-ONU administration, shutdown, vanished client, missing ANI
and failed authentication. They verify that diagnostic requests preserve live
business port indices and do not toggle FE admission. Existing23 upgrade cases,
migration/admin regressions and host/aarch64-musl daemon/CLI builds pass. Evidence
is local `artifacts/fttr/onu-optical-*-20261109.*`.

The optical feature changes no kernel data path and no device was accessed.
During final alignment, mainfbbc857 added the PE acknowledged-grant fast path.
The combined FTTR PE was regenerated and passed acknowledged/fresh/rollover grant,
sleep-selection, PS/peer withdrawal, CPU stamps, FTTR/multicast prefix drain and
BCMC tests. Repeated output matches:32540 bytes, SHA256
`65a4c8639b8c1e16964b8b420aab78fb25a1feeddd3d646febcf5eda9090a7ac`,
conservative stack7304/8192; non-uplink remains14168 bytes. Image/modules and the
generated kernel input match. Alarm subscription, other optical capability layouts
and physical optical readings remain to be implemented or accepted within the full
FTTR scope.

### Resident ONU alarm snapshot and notification synchronization

After successful authentication and MIB inventory, the controller now obtains a
baseline Get All Alarms snapshot (class2/instance0, mode0) and reads its entries
sequentially. The target ONU emits28-byte alarm bitmaps identified by class/ME
instance, and resets its notification sequence to1. Native state is exposed in
each ONU's `status.json` row as `alarms`: validity, synchronization state, error,
check time, last notification sequence and raw class/instance/bitmap rows. An
invalid or incomplete state exposes no rows; it is not reported as zero alarms.
Raw bitmaps preserve unsupported/vendor classes without guessing alarm meanings.

The controller accepts ordered baseline0x10 notifications only for a currently
authenticated registration with the same serial. Sequence1..255 wraps to1. Exact
last-message duplicates are ignored; conflicting duplicates, gaps and malformed
messages invalidate the state and schedule a new full snapshot. Notifications
after the snapshot header are collected separately and overlaid after all snapshot
rows arrive, so older snapshot rows cannot overwrite a newer notification. A
notification before the header makes the reset/snapshot boundary uncertain and
forces a retry. Duplicate snapshot objects, oversized counts, allocation failure
and expiry also leave the state unknown. Requests are not individually retried
against an uncertain remote snapshot; a new snapshot starts after2s. A periodic
60s refresh bounds a lost final notification; each collection has a55s total
limit and the existing2s exchange deadline. At most4096 objects are retained.

Alarm observation is separate from business admission: it does not withdraw FE,
ports or DBA on a raw alarm bit or a diagnostic error. Configured, inventory-only
and authenticated service-failed ONUs can synchronize. Same-serial MIB failure,
registration replacement, administrative teardown and controller exit invalidate
old alarm state; subsequent fresh inventory enables collection again. The entire
raw table is reconstructed after restart, rather than trusting runtime files.
The wire format contains no serial, so registration pinning cannot prove that a
very late same-slot unsolicited packet came from the current physical ONU.

`trace_stock_fttr_alarm.py` executes target ONU snapshot/reply/notification and
serializer functions, notification sequence reset and both relay header encoders:
514 notifications prove the original1..255 wrap. `test_fttr_alarm.py` covers22
snapshot/race/error groups and2056 target-generated notifications plus duplicates;
ASan/UBSan fault tests cover snapshot/delta/merge allocation failures, total expiry,
4096-row overflow and clear/free. Actual daemon/AF_PACKET tests pass on ASIC,
FPGA1 and FPGA2 for initial state, raise/clear, gap recovery with interleaved update,
conflicting duplicate, timeout, oversize response, same-serial MIB rescan, restart,
replacement and failed authentication. Optical/upgrade/migration/rebuild/admin
regressions and host/aarch64-musl daemon builds pass. Local evidence is
`artifacts/fttr/alarm-*-20261109.*` and `alarms-resident-20261109.*`.

No device was accessed. These tests model alarm storage, timers and transport;
they do not verify physical alarm assertions, vendor alarm naming or a policy to
act on alarms. Those policies and full packet-retirement acceptance remain open.

### Native alarm names and active bit identities

Every valid alarm row now also exposes `active`, an array of zero-based bit
indices and names. Bits use the actual original MSB-first wire ordering; an
empty array means the valid bitmap is clear. Unknown classes and reserved bits
remain visible with `name:null`, including bit223. Invalid snapshots still expose
no rows. There is no separate cached interpretation which could survive a reset.

Names follow the retained relay's warning string table: ONU-G256 bits0..11
(equipment, powering, battery, intrusion, self-test, dying gasp, temperature and
voltage), ANI-G263 bits0..6 (receive/transmit optical power, SF/SD and laser bias)
and Ethernet UNI11 bit0 (LAN-LOS). Other ONU-G bits are not labeled from adjacent
strings. The target's ONU-G converter places its16 input bits at report bits1..16;
ANI-G preserves its seven bit indices. Both distinguish asserted and restored
bits. The inspected Ethernet UNI wrapper computes the bit but never calls the
report sender; native status still preserves the actual received bitmap.

`AlarmRelay` executes both original relay converters and the report serializer
with only the message queue modeled. `test_fttr_alarm.py` compares every one of
224 possible input bits, zero/all-set states and all20 names, alongside the
existing snapshot/race tests. Actual daemon tests on ASIC/FPGA1/FPGA2 verify named
raise/clear, unknown-class bit223, resynchronization, restart and replacement,
without changing business/FE admission. Host and aarch64-musl builds and allocation
fault tests pass. Reports live under `artifacts/fttr/alarm-names-*-20261109.*`.

This completes names for these verified classes, not the original warning
hierarchy/trap callbacks or a hardware action policy. Actual alarm assertions,
other/vendor class meanings and physical acceptance remain open.

### Main NI prefix and request-template integration

The branch includes main `632c19c`, including large NI frame prefixes and
copying only live peer-template fields. FTTR cookie admission can rewrite the
Ethernet destination, so its prepared prefix uses the admitted address after
that rewrite. BCMC remains on its distinct header path. Prefix metadata is
masked out of header offsets and lengths; source6 generation, immutable cookie,
shared sequence and key ownership remain attached through selection/publication.

The combined image exceeded the fixed32KiB IMEM under `-Os`; the production
build now uses `-Oz` and retains all admission and retirement checks. The linked
uplink image is31876 bytes, SHA256
`d23a06d0d2c9ed9da75c294feb9e4b9a1d8e160a89bec11b90d6f18946ed564b`,
with conservative stack bound7288/8192 bytes. The non-uplink image is13848 bytes.
This is a capacity fix; instruction-count/throughput equivalence is not claimed.

Actual linked tests cover12 ingress length/header combinations, eight mixed
gathers and24 multicast/source6/header/AP/STA combinations, exact emitted bytes,
preserved generations and final EQ return. A same-image comparison verifies
that prebuilt prefixes reduce payload accesses for large aggregates and leave
short-frame access counts unchanged; main's absolute counts do not include the
FTTR ownership checks. All64 TID/order request cases, publication/key PN,
PS/withdrawal, multicast/BCMC, CPU/EPP gates, source6 drain, STA TX/RX and RXQ
backpressure regressions pass. Image/modules build and generated/overlay/kernel
header identity pass. No physical device was accessed.

### Open-network NI peers and current Linux key ownership

The native NI path now admits an explicitly verified open AP/STA peer, alongside
the existing CCMP peer. The actual target `txsc_add_sc_cache_entry` accepts
unencrypted IPv4/IPv6/PPPoE data when QoS and AMPDU are enabled, with a unicast RA,
no software encryption and no fragmentation. Its64 admission cases are recorded
by `trace_stock_fttr_tx_admission.py`. This is the cache admission prefix; actual
radio transmission and the rest of original TXSC are not claimed by that oracle.

Before exposing a route, the offload worker obtains the actual mac80211-selected
PTK or default unicast key under wiphy. Missing/unauthorized stations, software
keys, tainted keys and a station marked as using encryption without a usable key
are rejected. An open peer must have neither a selected key nor a residual driver
PTK role, and its Ethernet template must have no encryption flags or CAM value.
An encrypted peer must match the exact current key pointer, CAM and key index.
Starting shared NI rings only admits the peer which passed this check; other
peers are checked separately.

Raw unprotected QoS traffic can seed an open Ethernet template; raw software-
encrypted traffic cannot. Open FE/PE recipes retain a zero key, omit Protected and
WD encryption/PN fields, and do not take a CAM reference. AP converted copies and
STA group copies keep their existing address/sequence/PS contracts. Firmware
capability is MCP6, preventing an older PE from accepting these new recipes.

This also fixes stale key admission. The previous worker could retain/reopen an
old NI route after Linux selected a software/different key, because it checked
only its cached driver role. Tests reproduce the failure on `b1c9c7a`. A new
non-sleeping mac80211 callback closes all PE peer route bits before PTK
installation/removal, TX-index selection or default-unicast publication; the
multicast writer closes the matching FE/PE generations before publication too.
The worker completes existing NI withdrawal before reevaluating current keys.
Failure retains the pending state and closed routes for retry. Unsupported
pairwise/default cipher installation also withdraws the old native route.
This closes admission but does not prove upstream MCE or already-published
wireless packet retirement, so the existing hardware-ID quarantine remains.

Validation includes263 current-key states,106 actual Linux key-publication
cases with native FE/PE gates,512 worker failure/retry combinations,16896 actual
ARM request cases, complete learner-produced open records consumed by MIPS,
24 full open AP/STA/source6/HeaderA/length combinations and279 MIPS admission/
revocation cases (280 with the exported learner record). Existing encrypted,
BCMC/GTK, PS, FTTR generations, EPP/RPQ/EQ, PN, sequence, prefix and policy
regressions pass. Full PE is31908 bytes, SHA256
`dbec1147fcd0b0733154857c803d141ea19a62f5d65d14bfeaa1d6c75b3dee3e`,
stack bound7288/8192; non-uplink13848 bytes. Image/modules,11 kernel inputs and
zero-fuzz patch reverse/replay pass. Local evidence is
`artifacts/fttr/open-peer-*-20261109.*`; no device was accessed.

The same research rules out treating current RPQ as TSPEC airtime: its4-byte
record contains ownership/status only. Although the reference CCX layout has an
8-bit accumulated-time field, the target `mac_ccxrpt_parsing` ignores it (four
distinct values produce identical output), and `OnAction_wmm` is a no-op. Unit,
granularity and complete report delivery are unproven. Native TSPEC accounting
remains outstanding; no guessed microsecond charge or new TSPEC admission is
introduced here. Non-QoS software forwarding/sequence support remains separate
from the original QoS/AMPDU TXSC gate.

### GCMP128 across native key and accelerated packet paths

The native driver now accepts GCMP128 as well as CCMP128. Executing the target
C/XB Security CAM and DATA descriptor encoders confirms GCMP type8 (CCMP type6),
one16-byte key entry, unchanged DCTL slot ownership and the same48-bit packet
number fields. The extended `trace_stock_security.py --module PATH` compares590
CAM/DCTL/TX/drop cases. The256-bit variants need separate extended-key ownership
and remain unsupported by native hardware offload.

Algorithm identity is part of each cached peer template and immutable FE/PE
recipe (key bit27); the worker checks it with the selected Linux key/CAM/index.
PTK and GTK ARM descriptors and PE WD builders select the actual cipher type.
Direct AP BCMC keeps HIQ, shared GTK PN and interface sequence handling; both
CCMP/GCMP transitions revoke old recipes even when their CAM index is unchanged.
MCP7 capability prevents mixing these recipes with older PE firmware.

Software GCMP's real `gcmp_encrypt_skb` now allocates from the existing shared
PN domain, including software management frames sharing a GTK with PE. The
mac80211 RX snapshot/sync path uses the correct GCMP PN member, and successful
protected GCMP QoS reception can establish shortcut state. Native RX publishes
the selected cipher with peer admission; PE validates the longer minimum frame
and removes16 MIC bytes for GCMP (eight for CCMP), preserving IV/PN and all
existing address, BA, replay, PS and error checks. Software fallback continues
to receive the original IV/MIC and verify receive PN through mac80211.

Validation covers519 selected-key cases,33792 ARM request combinations,1024
worker failure/retry combinations, both cipher CAM installation/cleanup faults,
60000 concurrent shared PN allocations and64 actual software-cipher/PE
alternations. Actual Linux RX snapshot/sync passes128 PN/BA/authorization cases
plus native cipher transitions. Actual linked PE tests exercise both radios,
AP/STA, IPv4/IPv6, single/A-MSDU, exact Ethernet bytes, replay/decrypt/address
rejection, GCMP M2U and BCMC, and complete learner records through real RPQ/EQ
recycling. Two full ARM/PE BCMC runs compare90 submissions and45 linked WD
publications per cipher, including all four GTK IDs, PN carry and PS transitions.
Existing open/CCMP, FTTR generation, PS/withdrawal, prefix, PN/sequence and
multicast policy tests pass. Hardware encryption/decryption effects remain
modeled in these offline tests; no physical cryptographic/airtime claim is made.

Image/modules build, nine changed overlay/kernel inputs, zero-fuzz patch
reverse/replay and repeat firmware output pass. Combined PE is31948 bytes,
SHA256 `32b0a853fbc990618c359326995fac4256d19b28060d6a9b99082ff31d944fff`,
conservative stack7288/8192; non-uplink13868 bytes. Local artifacts use
`artifacts/fttr/gcmp-*-20261109.*`. No device was accessed. Full hardware
retirement/ID reuse, multiple VIFs, TSPEC and remaining factory-management
capabilities stay within the unfinished full goal.

### Extended CCMP256 and GCMP256 key ownership

The native key path now supports CCMP256/type7 and GCMP256/type9. Actual target
`rtw_hal_set_key` sends two16-byte halves, both marked EXT_KEY, through independent
CAM allocation and two same-key-ID DCTL slots. The target first-half error is
overwritten by the second call's result; native code instead reserves both CAMs
before writing, checks each completion, and publishes the role only after both
halves and DCTL succeed. No adjacency assumption is made.

A primary CAM owns its optional second CAM until both erase acknowledgments
arrive. A failed second erase retains both reservations and is retried; station
release, key deletion and failed-install cleanup use that same ownership rule.
The software key pointer and PN domain belong to the primary only. Failed DCTL
publication retains unlink state so cleanup restores the current desired role
before either half can be reused. Existing key IDs are not silently overwritten.

DCTL slots are selected from the actual available class range, while preserving
the prior single-key placement where possible. The worker locates the selected
PTK by its current Linux key ID and primary CAM, rather than treating key ID as
a physical slot index. DCTL now uses the original mode3 contract (two PTK/four
GTK/one BIP slots); mode2 only covers three GTK slots and was inconsistent with
the native fourth GTK slot. An extended PTK consumes both PTK slots, and two
extended GTKs consume all four GTK slots. Exhaustion returns ENOSPC without a
partial reservation; removal frees the pair for a later key. Extended-key-ID
overlap requiring more than two PTK slots cannot be hardware-offloaded.

Key bit28 carries the256-bit identity beside GCMP bit27 throughout ARM requests,
FE immutable records, PE descriptors and current-key validation. BCMC continues
to use HIQ and one shared48-bit PN sequence. All three16-byte-MIC algorithms
(CCMP256/GCMP128/GCMP256) use the longer receive trailer; Linux RX snapshot/sync
uses the matching CCM/GCM PN member. MCP8 rejects older PE firmware.

`trace_stock_extended_keys.py` executes48 original half-producer/failure cases,
16 deletion pairs,12 nonadjacent DCTL placements and the mode2/3 slot boundaries.
The four-algorithm CAM/DCTL/TX comparison passes654 cases. Native tests cover72
dual-CAM fault/retry/teardown groups, CAM and DCTL exhaustion, duplicate IDs and
replacement preserving another GTK;1031 key-selection states,67584 actual ARM
requests,2048 worker combinations and256 Linux PN/BA cases pass. Complete FE
records reach actual MIPS M2U/BCMC and RPQ/EQ; both256-bit algorithms pass full
ARM-vs-PE WD comparisons and existing PS/FTTR/PN/sequence/receive regressions.
The shared-PN test includes128 software-CCMP/GCMP and PE alternations across
all four configurations. These tests model hardware cryptographic effects;
physical encryption and interoperability remain unverified.

Image/modules, ten changed kernel inputs, exact patch reverse/replay and repeated
firmware output pass. Combined PE is31960 bytes, SHA256
`50c9ba7e6d9ea24e29fc3b8609dece8eb0670caaadcfaf5d598bbd7b298fdb4d`,
stack bound7288/8192; non-uplink13880 bytes. Evidence is under
`artifacts/fttr/extended-key*-20261109.*`. Final alignment includes main
`bfaa446`; its iperf checksum/tail-publication regression and real-skb test module
build pass. That module was not loaded. No device was accessed. Full hardware
packet retirement/ID reuse, multi-VIF, TSPEC and remaining factory-management
capabilities remain unfinished.

### Open-network PE receive forwarding

Open AP/STA peers can now use PE→NI receive forwarding as well as NI→PE TX.
The target `core_rxsc_apply_shortcut` privacy/template branch was executed for
20 combinations: an open template accepts an unprotected frame, and mismatched
protection is rejected. Native admission still requires the current Linux open
peer check described above; an absent key alone is not authorization.

Open receive sequence state belongs to `sta_info`, with separate per-TID valid
and next-sequence values. A QoS, unprotected, unfragmented frame reaching the
normal Linux RX chain after decryption checks establishes the state. Snapshot
requires authorization, no selected PTK/default unicast key, no historical
USES_ENCRYPTION flag, and, when BA exists, an empty reorder queue at the same
next sequence. It never initializes from zero-filled key storage. ARM delivery
and BAR recovery query that state before re-enabling the per-TID PE shortcut.

The radio records explicit open-peer admission after the worker checks current
keys, and clears it on key change/station teardown. PE config bit25 selects the
open format: no IV or PN reads, no MIC, four-byte FCS removal. Protected or
hardware-decrypted packets cannot use the open recipe. Existing MAC addresses,
DS direction, QoS, PS/U-APSD, fragment, protocol and NI-credit checks remain.
Contiguous sequences advance; future BA frames use the existing bounded search
and ARM fallback. Old Retry frames return to Linux instead of using encrypted
PN-based duplicate suppression. PN words remain zero; sequence and traffic
counters still synchronize before ARM receives exceptions. MCP9 distinguishes
the new receive contract from older firmware.

Tests execute the actual Linux mark/snapshot/sync code for open and four AES
configurations, including112 open authorization/key/BA cases and12 mark cases;
radio tests cover key transitions, open packet/BAR seeding and failed snapshot
retry. Actual MIPS tests cover both radios, AP/STA, IPv4/IPv6, single/A-MSDU,
exact Ethernet output and privacy/address/replay errors. Twelve complete RXQ
cases add sequence wrap, reorder gaps, no BA, Retry, key closure and preceding
ARM barriers, retaining the existing RXBD inventory and NI-prefetch ownership
checks. Encrypted, multicast/BCMC, PS, FTTR-generation and withdrawal regressions
pass. These checks do not verify physical RX descriptors or hardware timing.

Image/modules, six changed overlay/kernel inputs, zero-fuzz patch replay and
repeat firmware output pass. Full PE is32112 bytes, SHA256
`4c98aad7e73b5f16907d9847cea9b99c579813bc72552801e4ba2ee086a4aad0`,
conservative stack7296/8192; non-uplink13880 bytes. Local artifacts are
`artifacts/fttr/open-rx-*-20261109.*`. No device was accessed. Full upstream/
downstream retirement and ID reuse, multi-VIF, TSPEC and remaining factory
management still belong to the unfinished goal.

### Negotiated SPP A-MSDU in Security CAM

The native installer now passes mac80211's `IEEE80211_KEY_FLAG_SPP_AMSDU` to
Security CAM bit5, alongside type[3:0] and extended-key bit4. Both halves of a
256-bit key carry the same negotiated SPP value. The target C/XB encoder and
HAL producer confirm this layout and propagation. The previous installer
silently omitted SPP even when Linux had negotiated it, causing the hardware
authentication mode to differ from the software key.

`test_spp_amsdu.py` executes Linux's actual shared CCM/GCM AAD builder in384
QoS/TID/address/SPP/A-MSDU combinations. With SPP the A-MSDU-present bit is
included; unrelated Retry/PS flags stay masked. The original CAM/DCTL/TX
comparison passes782 cases, and the extended-key producer passes96 cases plus
32 deletion pairs. Native tests reproduce the old omission, then pass144
dual-CAM fault/cleanup groups and eight ordinary↔SPP replacement cases, including
erase failure followed by same-CAM reuse. Existing key tests, Image/modules and
the two changed kernel inputs pass. PE binary remains unchanged; no additional
descriptor bit is invented because SPP is stored in CAM. Local artifacts are
`artifacts/fttr/spp-*-20261109.*`. Physical SPP interoperability and cryptographic
hardware behavior remain unverified; no device was accessed.

### Original EasyMesh Flag2 configuration transaction

The syncserv client now supports the original EasyMesh enable/disable request:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa easymesh 1
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa easymesh 0
```

Use the interface/EtherType for the actual downstream syncserv topology.
`auto` discovers the peer's current local slot; an explicit0..15 slot is also
accepted. The native frame retains the existing slave multicast destination,
with the target MAC pinned in JSON `DMAC`. `SetAllSetting`, Flag2 and
`content:{"act":"config","EnableEasymesh":"0"|"1"}` match the executed
`fttrManage:set_fu_easymesh_info` and `sync_controllerd` producer. There is one
send and a nine-second total completion budget. A re-registration message from
the peer cancels the explicit transaction.

The actual original reply handler confirms `Result:1` only acknowledges the
request. A separate `RPCMethod:SetAllSetting`, `Flag:2`, integer `STATUS:0`
report completes it, including when received before the ACK. Other flags/methods
are ignored; nonzero STATUS remains pending within the fixed budget. Negative
Result, malformed messages and timeout are explicit errors. Output distinguishes
`ack_seen` from `completion_reported`; `configuration_verified` remains false.
The original GetONUInfo readback interpretation has since been established as
described below. A reported completion alone does not prove actual Mesh
enablement, roaming, wireless restart, credentials or interoperation. Late identical same-source
reports remain indistinguishable because this original envelope has no
transaction ID.

Resident `serve` policies accept optional `"EasyMesh":true` or `false` per peer,
alongside its existing MAC/Registration/Revision/WiFi entries. After a matched
registration with Ready1, all requested Wi-Fi rows must finish before Flag2 is
sent. Ready0 does not send it. Independent peers continue while one waits. The
resident event `sync_resident_easymesh` reports the requested value and observed
completion; re-registration/offline/replacement cancels outstanding state.

With `--state`, a version2 record stores the exact EasyMesh value and reserves
attempt bit16 after file/directory fsync and before transmission. Existing
Wi-Fi-only records remain version1. Startup refuses a changed same-Revision
policy. An attempted Flag2 operation recovered after restart is never replayed;
the independent state query described below now checks it against the saved
target. A new explicit Revision permits a new attempt after registration.
Without `--state`, suppression only lasts for the process lifetime.

`trace_stock_fttr_easymesh.py` executes both toggle values through the original
manager, controller builder, L2 encoder and ACK/completion branches. Actual
AF_PACKET/veth tests pass14 CLI and17 resident/persistence cases, including
fragments, incorrect source/method/flag, ACK-only timeout, registration change,
Wi-Fi-before-Mesh ordering, Ready0, fsync failure, SIGKILL/restart, policy drift
and revision changes. Existing37 resident,38 durable-recovery and30 Wi-Fi CLI
cases pass. Host and aarch64-musl builds pass. Evidence is under
`artifacts/fttr/easymesh-*-20261109.*`; kernel/PE are unchanged and no device was
accessed. This implements the verified host configuration transaction, while
actual subordinate application/readback and full roaming coordination remain
unfinished.

### GetONUInfo and independent EasyMesh state readback

The native client now exposes the original device-information query:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa onu-info
```

The request is the original `GetONUInfo` plus the master's MAC. Its result
contains `reported` fields using the original names, covering device versions,
supported wireless modes/bands/SSID counts, uplink/UNI descriptions, working
state and resource/radio statistics. Unknown fields are discarded; missing
fields stay absent. Known values accept strings or integers, preserve their
reported representation, and reject embedded NULs or strings exceeding1024
bytes. Enum meanings and units are not invented. Ethernet source and JSON MAC
must match the target. An explicit other RPC method cannot complete the query.
The target's pending reply dispatcher also accepts a response without RPCMethod;
native accepts that only with success Result, matching MAC and known data.
A bare ACK remains pending.

The original report parser copies `easymeshCapability` to byte1127 of the
subdevice MIB row. The retained province30 `fttrManage:get_sfu_info` block reads
that same byte into `Enableeasymesh`, while emitting `Supporteasymesh` separately.
Executing both stages for0/1 establishes the reported-state interpretation;
the misleading wire name must not be used as a generic support boolean.
`trace_stock_fttr_info.py` executes the actual query builder, full report parser,
pending Result dispatcher and bounded manager-output block.

EasyMesh CLI and resident transactions now query that field before writing and
again after the Flag2 completion exchange. Only canonical0/1 values permit a
write; absent state returns ENODATA, unsupported values EPROTO. Readback runs
even after a lost ACK/completion, but stops on observed registration replacement.
Output separates `completion_reported`, write/readback results and
`reported_state_matches`; a successful query cannot erase an earlier write error.
The resident state machine first completes requested Wi-Fi recovery/configuration,
then handles EasyMesh. Restart with a durable attempted bit performs only the
state query; matching state reports `recovered_read_only`, mismatch returns
EUCLEAN, and neither case replays the write. Subsequent registration/offline
events still invalidate the in-memory session.

Offline tests pass19 EasyMesh CLI cases,12 standalone GetONUInfo cases and20
resident/persistence cases: all56 known report fields, partial reports,
fragments, missing/invalid/mismatched state, bad source/method/MAC, timeout,
registration changes, SIGKILL recovery, policy revision and fsync failures.
Existing37 resident and38 durable Wi-Fi recovery cases pass. Host and
aarch64-musl builds pass; artifacts are `artifacts/fttr/onu-info-*-20261109.*`.
These are correlated peer-reported values, not authenticated identity or physical
Mesh/roaming validation. No slave application code or physical device was run;
kernel/PE code is unchanged. Full hardware retirement/ID reuse, multi-VIF, TSPEC
and remaining factory-management behavior remain open.

### Original Radio RPC configuration and durable recovery

The native syncserv client implements the original `GetWifiRadio` and
`SetONURadioInfo` methods:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa radio-info
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa radio-apply /root/radio.json
```

The private input file contains exactly six string fields, for example:

```json
{"RadioType":"2.4G","Enable":"1","TransmitPower":"80","Standard":"11bgn","Channel":"6","FrequencyWidth":"40M"}
```

`RadioType` is `2.4G` or `5G`; `Enable` is `0` or `1`. The original power
converter maps `100/80/60/40/20` to codes0/1/2/3/4. Supported width strings are
`20M`, `40M`, `Auto20M40M`, and, on5G, `Auto20M40M80M` and
`Auto20M40M80M160M`. Standards use the existing band-specific Wi-Fi strings.
Channel0 requests automatic selection. Fixed channel input is canonical decimal
within1..14 on2.4G and32..255 on5G; these wire bounds do not establish actual
regulatory or hardware channel support.

Queries and writes use the selected peer's Ethernet unicast address: the
original Radio JSON builders have no DMAC field to restrict a multicast write.
Query results retain the18 known RadioInfo fields as bounded strings, reject
more than two rows, duplicate bands and a mismatched OnuMAC, and discard unknown
fields. Field order does not affect native parsing. The original consumer is
order-dependent (its MAC branch reloads the MIB row), which native does not copy.
A bare ACK cannot complete a query. Re-registration cancels the exchange.

Apply first requires the selected band and all six readable fields, sends once,
and independently reads again even after a lost ACK. A successful read cannot
erase a write error. Fixed-channel comparison also requires AutoChannelEnable0;
Channel0 requires AutoChannelEnable1 and permits the selected actual channel.
Other fields compare exactly, including the configured FrequencyWidth rather
than CurrentChannelWidth. This verifies reported fields only; it does not prove
physical frequency, power, regulatory acceptance or subordinate installation.
Preflight checks presence/type, not a complete capability profile.

Resident peer policies accept an optional `Radios` array containing up to two
such objects, with one object per band. Execution order is WiFi, Radios, then
EasyMesh. Version3 durable records preserve the exact Radio policy and reserve
attempted bits17/18 (EasyMesh remains16). The send intent must be fsynced before
a write. Restart only reads previously attempted rows, writes unattempted rows,
and rejects changed policy under the same Revision. Policies without Radios
retain version1/2 compatibility. Radio events expose radio_type and separate
write/readback outcomes; configuration_verified remains false.

`trace_stock_fttr_radio.py` executes both original builders, the full two-band
reply consumer and libmib width/power converters. Another32 executions of the
actual manager width setter/getter blocks establish that MIB1319 distinguishes
40M from Auto20M40M even though both use width code1. Exact comparison is
therefore retained. `test_fttr_radio.py` passes100
input-enum cases,19 actual CLI/veth cases, three invalid-policy cases and seven
dual-band durable runs, including SIGKILL, fsync failure, drift and read-only
recovery. The combined WiFi→Radio→EasyMesh scenario and existing37 controller
and38 persistent Wi-Fi tests pass. Host and aarch64-musl builds pass; reports
are `artifacts/fttr/radio-*-20261109.*`. Kernel/PE code is unchanged and no
physical device was accessed. Full hardware retirement/ID reuse, multi-VIF,
TSPEC and other factory-management gaps remain open.

### Original WAN address notification

The syncserv CLI now sends the original `WanIPChange` notification to a selected
peer, for example:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa wan-ip 192.0.2.1
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa wan-ip 2001:db8::1
```

The JSON contains RPCMethod, the master's uppercase12-digit MAC, and exactly one
of WanIP/WanIPv6. Input must be a numeric address accepted by inet_pton, with no
prefix, zone, hostname or embedded suffix. Unspecified addresses0.0.0.0/:: are
encodable as in the original producer; their downstream effects are unverified.
The selected Ethernet unicast target, local interface lease, bounded fragment
receiver and registration cancellation are reused. The notification is sent
once with a3-second ACK deadline. Result1 is an ACK; rejection, malformed result
or timeout remains an error. An explicit unrelated method cannot complete it.

Output includes address, ack_seen and error, and always leaves
configuration_verified false. No route/table readback exists in the established
protocol evidence. A late identical bare ACK is not distinguishable by this
wire. This is an explicit notification API; automatic netifd WAN-change events,
resident delivery after re-registration and complete subordinate gateway setup
remain to be integrated with the original lifecycle semantics.

`trace_stock_fttr_wan.py` executes six IPv4/IPv6 producer/envelope cases and three
actual method12 pending reply paths. `test_fttr_wan.py` passes26 CLI/veth cases,
including automatic discovery, withdrawal-address encoding, fragments, incorrect
source/method, malformed replies, re-registration, no-result and timeout. The
Radio suite,32 original width roundtrips and host/aarch64-musl builds pass after
this addition. Artifacts are `artifacts/fttr/wan-*-20261109.*`. No kernel/PE change
or physical device access occurred. The branch includes main80b82e6; its iperf
feed regression passes.

### HE BSS Color in shared BSSID CAM lifecycle

The native driver previously updated the port0 BSS Color register while every
address/BSSID CAM command encoded color0. Creating, associating or removing a
station therefore rewrote the shared BSSID CAM with a color different from the
active BSS. The target factory `_hal_stainfo_to_macrinfo` copies the negotiated
six-bit color for a connected HE role; `role_info_init` and
`fill_bssid_cam_info` place it in byte53 of the60-byte H2C payload, while
`set_role_bss_clr` independently updates the port register. Both locations are
part of the hardware BSS identity.

All native CAM writes now carry the current single-interface BSS Color,
including legacy peers sharing that BSSID CAM. AP/mesh startup and STA
association establish it; disconnect/disabled-color uses0. Dynamic color
changes update the owning CAM and wait for its ACK before committing the saved
color and port register. Successful AP-self/STA-owner CAM writes refresh the
saved command, so a later color update retains the latest AID, HE and address
fields. Failed writes do not replace that saved owner command. On a dynamic
update failure, hardware scheduling is blocked immediately, then PE/RX pause
is attempted and cfg80211 is asked to stop the interface. A pause timeout cannot
leave the scheduler running while asynchronous teardown is pending.

`test_radio_bss_color.py` executes256 target HAL→BSSID-CAM and port-register
cases across AP/STA, HE/legacy and all64 colors. Actual native command helpers
pass128 success/ACK-loss/retry transitions,32768 peer write permutations and384
production role/peer/client CAM recipe combinations, with non-color fields
preserved. It explicitly reproduces the old encoders' zero-color overwrite and
checks current-owner AID/HE preservation. The STA lifecycle test executes128
dynamic AP/STA failure/recovery/disable scenarios, including a failed pause,
and verifies initial association color. Existing STA/BA release, extended-key
cleanup, AP/mesh and FE transaction regressions pass under their existing
sanitizers. Kernel Image/modules build succeeds and all three changed kernel
inputs match the worktree. Artifacts are `artifacts/fttr/bss-color-*-20261109.*`.
PE firmware is unchanged; no physical color/airtime/traffic acceptance is claimed.

This fixes an existing hardware identity mismatch found while reviewing
multi-VIF requirements. Multiple data interfaces remain unsupported: each
still needs independent role/CAM allocation, beacon/MBID, GTK, sequence and
BCMC ownership, with matching FE/PE metadata. The single-interface limit was
not relaxed. Complete hardware retirement/ID reuse and TSPEC remain open.

### Independent address and BSSID CAM ownership

The radio no longer assumes address-CAM index equals MACID or that every role
owns BSSID-CAM0. `cam-owner.h` now reserves those resources independently in the
actual CAM writer, using the target C/XB hardware capacities:128 address entries
and20 BSSID entries. Each MACID retains its allocated address and BSSID indices;
roles with the same full BSSID share one reference-counted BSSID entry. The
current native radio uses MAC band0; concurrent multi-band/port selection is not
implied by this allocator.

Reservation precedes the firmware command and survives a lost ACK. Retries
reuse the reservation without adding references. Address and BSSID exhaustion
return ENOSPC before issuing CAM IO. Updating an identity shared by other peers
returns EBUSY; an exclusively owned STA BSSID can change for association. Peer
deletion targets its retained indices, keeps the BSSID valid for remaining
users and releases software ownership only after the erase ACK. A repeated
cleanup with no reservation performs no hardware write. An uncertain last-user
erase followed by a new same-BSSID reservation is retried with valid1, preserving
the new owner. This is CAM command ownership, not proof of complete packet
retirement elsewhere in the forwarding pipeline.

`trace_stock_radio_cam_owners.py` executes the target HAL/MAC allocator and
removal path for three BSSIDs, arbitrary nonsequential MACIDs, shared peers and
reuse on both C and XB:26 transitions. The target `_remove_role` calls
`_change_role`, which runs BSSID allocation again on removal. When the last
matching role has been made invalid, that path can select a different free
index and retain valid1. Six last-user deletion commands in the trace differ
from the required retained-index cleanup. Native explicitly corrects this
observed factory error instead of applying that allocator to removal.

AP→STA transition also keeps a pending CAM erase independent of successful
firmware role removal. Clearing the role stage no longer skips CAM cleanup on
retry. AP self-role construction uses a local candidate; a failed CAM write
cannot replace its last acknowledged command. A first-create timeout without a
confirmed command still produces a correctly sized32-byte address/8-byte BSSID
erase using the retained indices and BSSID. The HE color update uses the latest
acknowledged owner command, preserving its newly allocated indices.

`test_radio_cam_owners.py` compares all26 original transitions, checks the six
corrected deletions, both capacity limits, timeout retries, identity changes,
new shared references during cleanup and first-create failure under ASan/UBSan.
Actual STA/BA cleanup verifies non-MACID address slots stay reserved across RPQ
and firmware failure. AP→STA tests require the pending erase before recreation;
AP/mesh tests check failed self-role replacement retains its snapshot. Existing
BSS Color, extended-key and FE transaction regressions pass. Image/modules build
and three changed-input identities pass; artifacts are
`artifacts/fttr/cam-owner*-20261109.*`. PE firmware is unchanged, with no physical
association or traffic validation.

This is an implemented prerequisite for multi-VIF isolation. The driver still
exposes one data interface: per-VIF role/MBID/beacon allocation, GTK, shared
sequence and BCMC/FE/PE identity are not yet complete. NI group/action/cookie/GEM
retirement restrictions remain; this change does not remove their quarantine.

### Role identity through firmware tables and beacon upload

A native interface now has a private hardware identity (port, MBID, self MACID
and role type), and each station copies it into a retained per-MACID record.
The current interface allocator still creates one AP/STA/mesh at port0/MBID0.
JOIN, address CAM, station CCTL updates and beacon uploads consume the identity
instead of silently resetting these fields to the root AP values. Removal uses
the retained station record after mac80211 can free the station/interface data.
This does not yet advertise multiple interfaces.

The target HAL establishes distinct fields: role type AP2/VAP3/STA1/mesh6;
JOIN port[23:21]; CAM port[10:8] and TSF port[13:11]; C/XB CCTL word5 port[2:0]
and MBID[7:4]. The original VAP allocator requires an existing port0 and assigns
MBID1..7 while sharing WMM0. Beacon HAL copies role hardware band, port, MBID
and self MACID into its hardware entry, then the actual MAC encoder writes
port/MBID/band into header bytes0/1/2 and MACID into byte4. Physical RF band
must not be substituted for MAC band0.

Native initial station tables, BA operational/finish updates and rate-update
JOINs preserve the station identity. Partial CCTL writes alter only the
identity bits selected by the command's mask, retaining unrelated HE/rate state.
Self-role construction derives AP/VAP/mesh type from the interface identity.
Normal beacon upload receives that same identity along with mac80211's template;
allocation or H2C failure leaves previously recorded template state unchanged.
The separate explicit probe beacon interface retains its root-role contract.

`trace_stock_radio_role_identity.py` executes36 target HAL→JOIN/CAM cases,
72 actual C/XB CCTL encodings, and24 complete HAL role→beacon entry→MAC payload
chains. `test_radio_role_identity.py` matches these outputs and checks160
partial CCTL masks plus24 real native beacon upload/error cases and input/allocation
bounds. AP/mesh lifecycle tests inject seven VAP identities into actual self-role
commands. STA/BA cleanup tests use nonzero MBID5 and require it across BA updates
and deferred station removal. Existing STA, CAM-color, extended-key, authorization
and FE transaction tests pass. Kernel Image/modules build passes and all four
changed inputs match the worktree. Reports/build data are under
`artifacts/fttr/role-identity*-20261109.*`; PE firmware is unchanged.

Per-VIF resource allocation, MBID enable/disable and beacon scheduling, per-VIF
GTK/non-QoS sequence/BCMC state and CPU/NI→PE descriptor identity still require
integration before the single-data-interface restriction can be removed. These
encoded multi-role tests do not demonstrate physical multi-SSID operation.
Complete upstream/downstream packet retirement and other hardware-ID quarantine
remain unchanged. No physical device was accessed.

### MBID register scheduling and independent VAP preparation

Port0 startup now programs the previously omitted MBID control register0xc568.
The actual C/XB `mac_port_init` ends with `port0_mbid_set`: it sets the additional
slot count to7 in bits19:16 and clears all15 hardware VAP-enable bits1:15,
retaining bit0 and unrelated fields. The hardware supports16 total slots while
the target PHL allocator permits VAP MBID1..7. Earlier fixed register snapshots
did not include0xc568; the diagnostic list now includes it. A failed reinitialize
also clears software ready state instead of retaining an earlier success.

Root beacon TX continues to use0xc400 bit12. VAP beacon switches use only their
own0xc568 bit, with bounds and the programmed slot count checked. VAP preparation
uses the existing root interval/TSF schedule, requires a matching interval and
writes only its own HIQ window50 and DTIM byte, plus the shared TIM update bits.
It does not reinitialize the whole port or other MBID timers. The actual
self-role preparation and beacon start/change/stop paths select those operations
from the stored interface identity. Starting only releases the scheduler after
the MBID switch succeeds. This is the per-MBID register part; the remaining
single-interface software state must still be split before enabling multiple
interfaces concurrently.

On disable, C/XB uses the original selector8 HIQ-drop command for that MBID;
enable only changes the register (the factory REL_HIQ_MBSSID path has no H2C on
these chips). The command has no receive/done ACK. Native retains a pending bit
when command publication fails, schedules the existing cleanup worker to retry,
and refuses re-enable while that bit remains. Successful publication clears
the pending bit; it does not prove downstream packet retirement or authorize
hardware-ID reuse. Root/GTK/BCMC drain remains governed by its existing separate
barriers. No VAP resources are currently allocated by the interface API.

`trace_stock_radio_mbid.py` captures162 target configurations, including both
chips, MBID switches, HIQ/DTIM and full startup from zero and nonzero registers.
`test_radio_mbid.py` compares64 complete relevant MMIO/selector/MBID operation
traces. The one intentional startup difference is retaining root beacon TX
disabled until a template exists. Unused factory drop-structure bytes are not
claimed as defined wire state. Native tests cover VAP-only register effects,
invalid/mismatched intervals, allocation bounds, frozen timer, every startup
HIQ publication failure, seven independent drop retries and sanitizer checks.
Actual lifecycle tests verify VAP disable retains the root beacon switch,
failed re-enable/stop reports errors, and pending-drop cleanup retries. AP/mesh,
STA/BA, FE transactions and target DFS/CAC regressions pass. Image/modules build
and three changed-source identities pass; artifacts are
`artifacts/fttr/mbid-*-20261109.*`. PE firmware is unchanged; no physical device
was accessed. Per-VIF GTK/sequence/BCMC, full interface allocation and PE identity
still remain, along with end-to-end packet retirement and physical acceptance.

### CPU and NI PE transmit-role snapshots

CPU submissions now encode the interface port/MBID in request word3 bits24..30;
the direct ARM WD path and PE builder both emit it in WD word8 bits6..0. Target
C/XB DATA and management encoders agree on this layout across160 cases.
Per-station rate templates retain these identity bits across firmware rate
updates and the non-uplink probe-rate path. The producer uses the actual
interface private identity, while per-peer NI templates are initialized after
peer reset and before admission. Existing single-interface allocation is unchanged.

A peer's local interface address is stored in the TX context at0x68400 (128
8-byte rows, immediately after the CPU multicast stamps). Request word3 bit23
marks the address as explicitly supplied. CPU encapsulation copies it into
bytes96..103 of the software WP header allocation, which remains owned until
completion. NI selection snapshots it into private request words28/29: NI uses
at most nine address pairs, and those two words carry FTTR generations only for
CPU requests. Requests queued for hardware credit retain the captured address
when global/peer templates change. AP encapsulation and its A-MSDU outer header
use this per-interface address; RX receiver matching uses the same per-peer
address rather than the global radio address. Unmarked diagnostic/BCMC templates
retain the existing root address behavior. The live producer marks supported
peer and CPU encapsulation templates explicitly.

STA three-address TX retains the documented Linux Ethernet-SA decision; changing
only the PE address would diverge from the Linux path. This remains an explicit
factory difference requiring coordinated Linux/forwarding policy work. Multicast
BCMC still has its single-root GTK/sequence contract; it is not made multi-VIF by
this change. Non-QoS sequence and full per-VIF key/BCMC isolation remain open.

New capability TXR1 at mailbox215 makes ARM reject older firmware which lacks
these request semantics. The generated PE image is32280 bytes, sha256
`fa72ae956d130a691cba93ef689643732fbfa3c59f588c0805f8dab6c384c212`, with a
conservative7312-byte static-stack sum within8192 bytes. Repeat production build
is byte-identical; the non-uplink variant builds at13992 bytes.

`test_pe_tx_role.py` passes49 linked CPU/NI/AP/STA/multicast/A-MSDU cases,
including CPU credit stalls and changing the NI address table after staging,
plus32 RX per-peer/foreign-address/admission checks. Actual ARM submission tests
cover direct and PE port/MBID emission, CPU address snapshots and40 per-peer
rate-update combinations. The67584-request construction comparison, linked
publication,282 multicast+12 full TX/RPQ,103 CPU multicast,245 BCMC,251 FTTR
NI/PE drain cases, RX crypto/address and PS budget/generation regressions pass.
An older whole-memory-poison comparison fixture hit an unmapped read and is not
used as evidence; its temporary edits were removed. Image/modules build and
five source/generated-input identities pass. Local reports are
`artifacts/fttr/tx-role-*-20261109.*`. No physical device was accessed; these
results do not prove air-interface behavior, complete packet retirement or
hardware-ID reuse.

### Per-interface non-QoS sequence domains

The native ARM/mac80211/PE sequence binding now selects twelve independent
interface domains: port0 MBID0..7 and port1..4 with MBID0. The root retains
its0x1fd00 shared record; the other eleven records occupy0x68c00 onward in the
TX radio context. Invalid port/MBID combinations are rejected rather than
aliasing another interface. The interface allocator still exposes a single
data VIF, but the actual binding and NI allocation paths now support distinct
domains. Linux retains its existing per-interface sequence counter and the
same ARM/PE mutual exclusion protocol.

Queued NI frames carry a domain-plus-generation token in their existing
headroom word7. Selection and final publication compare both the token's domain
and generation with the request role. A domain reassignment is rejected even if
the old domain remains alive or another domain has the same generation value.
PE allocates only after TXBD/WP credit and never allocates twice after a PN
stall. Domain-specific closure cannot consume or close another domain's counter.
Generation values use27 bits, with explicit exhaustion; high bits carry the
domain and bit31 remains the assigned-sequence marker.

CPU software WPs now have a separate interface token at0x68800, outside the ten
address pairs and FTTR/GTK metadata. All bound-interface CPU requests snapshot it
before publication, including ones whose non-QoS sequence Linux already assigned.
The final PE publisher rejects a stale token before PCIe ownership transfer.
Previously preassigned CPU requests did not have this interface-generation
check. Requests already handed to PCIe still require the existing completion
barriers. The root BCMC template is explicitly restricted to port0/MBID0 until
its separate per-VIF GTK/template implementation is complete. Sequence capability
SEQ2 makes the new driver reject incompatible PE firmware.

`test_shared_sequence.py` executes240 target factory counter cases,24576
concurrent allocations and the actual Linux TX/fast-path sequence handlers. It
also binds all12 domains simultaneously, performs98400 Linux/PE alternations,
then checks independent close/rebind, generation exhaustion and invalid domains.
Linked-MIPS tests pass24 existing non-QoS EPP/header/RPQ/EQ/stall cases,24 domain
selection/withdrawal cases,24 stale CPU queued/selected cases and two live-domain
reassignment cases. Actual ARM submission checks token capture. Existing BCMC,
multicast, CPU cancellation, FTTR NI/PE drain, TX/RX identity, publication and
main RX address-store regressions pass.

Image/modules build and five changed/generated-source identities pass. The PE
image is32476 bytes, sha256
`5a649bbe7ef17344df2c889e279752a63af4f95d9a84db49b31274b031e70453`, with
conservative stack7328/8192 bytes. Repeat production build is identical;
non-uplink builds at13992 bytes. Local reports are
`artifacts/fttr/vif-seq-*-20261109.*`. This branch includes main3db3da5 and its
RX address-store optimization. No physical device was accessed. Per-VIF
GTK/BCMC, full interface allocation and complete hardware packet retirement
remain unfinished; software domain isolation does not remove hardware-ID
quarantine.

### GTK ownership follows the interface self role

GTK installation now selects the interface's stored self-MACID rather than
implicitly using MACID0 (or a global STA sentinel). Every primary security CAM
retains its owning MACID, port, MBID and whether it uses AP HIQ. Disable callbacks
and deferred cleanup use that retained ownership even if the later interface
identity has changed or become invalid. The current desired DCTL for the owner
is restored on failed installation, preserving newer keys and other VIFs' keys.
Owner metadata is cleared only after both CAM halves erase successfully.

Group retirement is independent of a zero/nonzero MACID test. Root0 delegates
to the existing direct-BCMC fence. Non-root CPU GTK retirement pauses PE, sends
the factory HIQ_PORT7 or HIQ_MBSSID8 command for the saved port/MBID, resumes
recycling and waits for the saved self-MACID's outstanding WPs. A failed pause,
command or wait remains an error; CAM/DCTL cleanup retains ownership and retries.
These no-ACK HIQ commands plus WP completion do not establish the outstanding
upstream/downstream hardware retirement required for ID reuse.

CPU group/management TX now takes its MACID from the interface when no unicast
station is supplied, and AP DCTL restoration uses that self role. A nonzero
self-MACID group QoS frame must retain Linux's per-interface sequence; it no
longer qualifies for the PE unicast per-TID sequence rewrite. The direct NI→PE
BCMC template remains root-only and does not yet support multiple VIFs.

This review also fixed the ARM completion side of the previous interface-token
change. PE-canceled ordinary CPU requests had no FTTR/GTK/multicast stamp and
were skipped by the release worker. A per-WP interface-reference flag now starts
and maintains release polling for these requests, so cancellation unmaps/frees
them and restores software credits exactly once. The normal RPQ path remains
unchanged.

`trace_stock_group_owner.py` executes64 target HAL GTK owner calls and24 HIQ
port/MBID encodings. `test_group_stop.py` compares96 command/failure cases.
Production key lifecycle tests pass28 two-VIF cases using identical key IDs,
including four ciphers, stale callbacks, installation failure, deletion failure
and cleanup with a newer key on the other VIF. Existing144 extended-CAM and
key/STA/Mesh/FE regressions pass. Actual CPU submission verifies nonzero group
MACID with preserved sequence. ARM completion passes771 partial/full batches
across all WP IDs/rings, including ordinary interface-only cancellations;
16448 RPQ comparisons pass. Image/modules build and five input identities pass.
Artifacts are `artifacts/fttr/group-owner-*-20261109.*`. PE firmware is unchanged;
no physical device was accessed. Full multi-VIF allocation/direct BCMC and
complete hardware packet retirement remain unfinished.

### Multiple VIF bindings to one PE multicast table

The FE/radio binding layer now permits up to12 logical VIFs to share the same
physical-radio PE multicast table. Each FE binding holds its own netdev and
BCMC policy/generation. Unicast policy publication also checks that VIF's
binding instead of requiring the physical slot's first netdev. A different
table for the same physical destination, one table on two radios, or a VIF
bound to two destinations is rejected. Adding a VIF preserves the existing
immutable records and cookie tombstones.

Every staged cookie records the FE-assigned wireless ID of its owning VIF.
Unbinding closes the table epoch and permanently clears only that VIF's record
epochs, retaining control tombstones; other VIF records and policies remain
owned. Namespace-local ifindex is not used as identity. The last binding retires
the complete table; a new first binding also retires prior FE/driver incarnations
before publication. Normal multicast generation invalidation still closes all
readers while FE revalidates complete recipient sets.

The radio retains one reference per bound VIF, can detach a single interface
during remove_interface, and releases every binding before NI/context/DMA
teardown. An individual or partial detach failure retains the remaining netdev
references for retry; already detached entries are not released twice. A
NETDEV_DOWN followed by rebind still reaches FE, so the radio's retained local
reference cannot incorrectly imply that FE owns the table.

The production binding test checks same-radio multiple VIFs with different GTK
policies,12-binding exhaustion, foreign-table/radio conflicts, identical ifindex
in distinct logical devices, cookie revocation, rejoin without resurrection,
partial failure and detach-before-PE-join-before-DMA-free. The actual complete
multicast learner stages two VIF recipients with distinct GTK keys/sequence
policies into the same PE table in an isolated test run. The separate run avoids
exhausting the deliberately quarantined cookie pool of the existing long suite;
production limits are unchanged. Existing full learner, AP/Mesh, NI retirement
and FE transaction regressions pass. Image/modules build and input identity are
checked; reports are `artifacts/fttr/multivif-bind-*-20261109.*`.

This removes the single-VIF binding bottleneck. It does not yet make the direct
BCMC PE template multi-VIF: its producer, admission, queue and GTK/sequence
state still need per-interface storage. The interface API still creates one
data VIF. No physical device was accessed and no packet-retirement/ID-reuse
claim is made.

The final multi-VIF binding build also includes main `b28f290` (aligned RX
reference reads). Merge resolution retains the per-peer receiver-address
selection before the optimized comparison. RX backpressure/admission,49 TX/32
RX identity, per-VIF sequence,245 BCMC and publication regressions pass.
The generated PE image is32440 bytes, sha256
`fa1fd99f936e4023b262ddc15800e30202df5cebca7c6865f7120e80c7cf3ac1`, with
conservative stack7296/8192. Repeat build is identical; non-uplink13992 bytes.
The combined Image/modules build passes with eight source/generated inputs
identical to the worktree. No physical device was accessed.

### Per-interface direct BCMC templates and PE ownership

Direct BCMC now has twelve templates matching the sequence domains (port0
MBID0..7, ports1..4). Root storage remains0x1fd40; eleven additional28-byte
records start at0x69000. Each template carries its immutable GTK/key generation,
sequence-domain token, port/MBID, self-MACID and transmitter address. Radio
publication and FE policy validation preserve those fields; generation closes
before changing a template and publishes last. A conflicting netdev in a
reserved domain, shared self-MACID across domains, or changed self-MACID in an
owned domain returns EBUSY. Interface close checks the bound netdev before
invalidating a domain; failed teardown retains reservation until full NI drain.

The existing software BCMC FIFO remains shared at MACID0. Each queued BID keeps
its cookie and domain token; selection builds from that cookie's VIF template,
not the global root template. The final request includes the VIF's WD MACID and
MBID and snapshots its address in words28/29. The full PE main-loop staging path
preserves these words instead of fetching the unicast address table. Final
publication verifies template/key/sequence ownership again. A distinct BCMC bit
in NI ownership metadata permits matching nonzero self-MACID RPQ reports while
excluding these requests from ordinary station PS and AC accounting. EQ return
still follows RPQ/software completion, with no premature BID reuse.

Closing or failing one template leaves other VIF templates and sequence counters
intact. Group cleanup closes the table epoch through the existing global policy
gate, pauses the final publisher, drops the selected hardware HIQ and waits for
the shared software BCMC prefix and owning self-MACID's WPs. Whole-radio stop
iterates all reserved domains. Because the FIFO and policy gate remain shared,
this can temporarily delay or invalidate other interfaces' traffic; it is not
per-VIF scheduling isolation or a complete upstream/downstream retirement fence.
Domain/ID reuse after detached ownership remains restricted. The public interface
allocator still permits one data VIF until its full multi-interface lifecycle
is implemented.

`test_pe_bcmc.py` passes245 existing root cases and72 complete multi-domain,
self-MACID0/41/127, open/GTK EPP→HIQ→RPQ→EQ cases. A shared two-VIF ingress batch
selectively rejects one closed template and publishes the other. Two binary
records from the actual complete bridge/FE learner reach the linked PE and
complete RPQ/EQ. Three full PE main-loop runs verify snapshot staging with
poisoned global/unicast addresses. Native template tests cover independent
close, publication failure, key change, domain/MACID conflicts and retry. FE,
GTK, group-stop, NI retirement,771 CPU completion batches, ordinary multicast,
per-VIF sequences, FTTR drain, TX/RX identity, RXQ and publication regressions pass.

The multicast PE capability advances to MCP: (`0x4d43503a`). Production firmware
is32680 bytes, sha256
`53e5c1689e6bea4aaaad53da6837eb954261bd90562faedd6e0326b09db57651`; conservative
stack is7336/8192. Repeat build is identical, non-uplink builds at13992 bytes.
Image/modules build and nine source/generated-input identities pass. Local
reports are `artifacts/fttr/bcmc-vif-*-20261109.*`. No physical device was accessed;
DTIM timing, actual wireless encryption/airtime, full VIF operation and safe
hardware-ID reuse remain unverified or incomplete.

### Retained self-role state by MACID

Self-role creation now accepts the explicit interface identity all the way
through firmware CREATE/JOIN, address CAM and initial station tables. Previously
JOIN/beacon fields were prepared for a nonzero self-MACID while the CAM/table
and lifecycle stages still used root0. The radio now retains stage, type, color,
result and acknowledged CAM independently for every self-MACID. Each peer stores
its owning self-MACID so its CAM updates inherit the correct BSSID color. STA1
uses a distinct record from the probed root0 role.

Self MACIDs share the same reservation bitmap as peer MACIDs, preventing peer
allocation from taking an active self slot. An existing peer cannot be converted
or deleted through the self-role path. Uncertain CREATE completion retains the
reservation and original role type; a retry first removes that uncertain role
before creating it again. Removal waits for associated peers, keys/pending key
unlinks and group WPs, then separately records firmware removal and CAM erase.
A successful role removal followed by failed CAM erase retries only the erase.
The first CAM timeout can have no saved payload; cleanup still supplies the
reserved MACID and the CAM allocator's retained indices/table geometry.

remove_interface starts this teardown and leaves a pointer-free retained record
for the existing cleanup worker. A later add_interface finishes any pending
self-role teardown before reusing IDs. AP/mesh creation requests preserve their
color across a failed-create cleanup/rebuild. AP→STA handoff clears the probed
root reservation only after its CAM cleanup and retains STA ownership separately.

The actual self-role functions are exercised with root0, VAP41 and VAP63 together
on both chip configurations, including each CREATE/JOIN/CAM error, peer/key
holds, CAM-only retry and independent snapshots. Additional first-CAM-failure
and removal-failure tests cover absent snapshots and retries after VIF memory
is no longer needed. BSSID color tests verify peer7 inherits owner41 while
owner63 retains its distinct color and CAM. Existing STA/BA, key cleanup,
AP/Mesh, FE and factory-backed DFS regressions pass. Image/modules build succeeds
and four changed-source identities match; reports are
`artifacts/fttr/self-role-*-20261109.*`. PE firmware is unchanged.

The public interface count remains one. AP active/beacon state, asynchronous
work routing and actual interface allocation are still partly global; port0
color-register sharing and full multi-VIF scheduling require further work.
This change implements independent firmware-role ownership, not a claim of
complete multi-SSID operation or physical packet retirement. No physical device
was accessed.

### Peer work and completion ownership across interfaces

The target `rtw_get_stainfo@0xccae8` searches and caches inside the supplied
adapter's `sta_priv`. Executing its actual hash and cache paths against two
constructed adapters with the same peer MAC returns distinct station objects;
an inactive entry never falls back to the other adapter. The native driver now
preserves that interface boundary in asynchronous unicast offload, BA completion,
rate updates and traffic policy. A wiphy-owned self-MACID-to-VIF table is published
only after successful interface creation and cleared before removal. Each peer
retains the nonwrapping interface instance number and full role identity. Workers
reject removed/recreated owners, mismatched identities and mac80211 stations with
a different MACID. Hardware cleanup still runs when no live VIF remains, while
its BA callback is suppressed. The atomic rate-change callback uses only its
supplied live VIF and the published instance number; it never dereferences the
wiphy-owned registry.

Non-wiphy NI activity, TIM/PS completion, RPQ feedback and RX PN/SN synchronization
now match both local and peer MAC addresses and verify the returned MACID. RX
accepted-frame/BAR reseeding also checks the receiver address before choosing a
peer, so two BSSes with the same transmitter cannot copy one another's sequence
or replay state. CPU Ethernet requests retain local and wireless-peer address
snapshots until completion, with no borrowed VIF pointer. In STA mode that peer
is the associated AP, not an Ethernet destination beyond it, consistent with the
original `update_attrib`/`rtw_make_wlanhdr` execution. Direct BCMC RPQ feedback is
excluded from ordinary station rate reporting.

`test_wifi_peer_vif.py` exercises the actual offload/lookup/BA/atomic-RC functions
with the same station MAC on root0 and self41, different keys and devices, removed
and recreated VIFs, wrong returned MACIDs and role mutations. RX synchronization
tests include equal transmitter addresses, distinct receivers/keys and stale
MACID reseeding. Interface startup failure and generation overflow do not publish
an owner. Existing TIM, PS generation, BA/key teardown, AP/mesh/STA lifecycle,
FE failure/retry, 16448 RPQ completion vectors, 67584 PE request vectors and the
factory RA comparisons pass. Actual Image/modules builds and eight input hashes
match; evidence is `artifacts/fttr/vif-*-20261109.*`, with the original lookup
oracle in `tools/trace_stock_peer_lookup.py`. The old standalone
`test_wifi_cleanup_nonblocking.py` fixture currently fails to load its updated
key-test setup; delayed RPQ/nonblocking station cleanup is covered by the actual
station-release and key-cleanup tests, not claimed from that broken fixture.

The branch includes local main `b28f290`; PE firmware/ABI are unchanged. Public
interface count remains one: AP/beacon lifecycle, BCMC scheduling, global policy
and interface allocation still require integration before enabling multiple
SSIDs. This work fixes peer task/completion ownership and does not establish a
full hardware retirement barrier or safe resource reuse. No physical device was
accessed.

### Independent BSS lifecycle and shared port0 scheduling

Each retained self-MACID now owns its BSS started/beacon/result state and pending
BCMC stop. AP start/change/stop and TIM template refresh use that owner. Aggregate
radio flags describe whether any BSS is active. Stopping or removing one BSS
leaves the other BSSes' receive mode, TIM work, rate work and offload work active;
only the last BSS stop returns DFS to CAC and cancels the shared workers. BCMC
publication iterates the live interface owners, so a failed GTK unlink or template
on one interface does not suppress the others. A failed group stop retains the
identity for the cleanup worker even after the VIF is removed. Successful restart
must finish that stop before clearing its retry state. Scan completion detects
active BSSes even when the original root VIF has been removed.

Root restart while VAPs remain active preserves the shared TSF, interval, MBID
switches and other BSSes' DTIM/HIQ windows. It only disables the root switch and
sets the root's own HIQ window/DTIM. A conflicting interval is rejected before
firmware role writes. VAP setup retains the existing independent register path.
The port0 root group drop now uses original `HIQ_MBSSID8, MBID0`; the previous
`HIQ_PORT7` selector would drop other BSSes sharing port0. Ports1..4 still use
the port selector. `trace_stock_group_owner.py` now executes that explicit root
MBID recipe in both target chip encoders, and `test_group_stop.py` compares all
24 payloads with pause/drop/wait failure ownership (96 vectors).

Radar reception disables root and all seven VAP beacon switches under the
command lock, clears their retained beacon state and blocks TX. BSS lifecycle
state survives for orderly stop; offload cannot reopen BCMC after radar/CAC.
The actual radar mutation block is exercised with multiple active BSSes in
`test_radio_dfs.py`, alongside 589 target classifier vectors and 66 dynamic
vectors. This report records the actual retained factory module hash used here.

The actual AP functions are tested with root0, self41/MBID1 and self63/MBID2:
stop each one first, refresh surviving TIM templates, restart root without port
reset, reject an interval change without touching another owner, remove root,
and stop the last VAP. BCMC tests cover independent publication, GTK unlink
failure and radar. MMIO tests cover all255 root DTIM values while preserving VAP
fields, in addition to the64 original MBID/port traces. Stop retry after a freed
VIF, key cleanup, AP/mesh/STA and FE failure/retry regressions pass. Image/modules
builds and all four changed kernel inputs match;
`artifacts/fttr/bss-*-20261109.*` contains the reports. The older broad
`test_radio_regulatory.py` fixture lacks several existing driver fields/APIs and
cannot compile; radar and radio-stop coverage above comes from the maintained
DFS/stop-route tests, not from that fixture.

Public interface count remains one. Interface allocation, NI unicast's singleton
route/device assumptions, shared BSSID color/EDCA policy and scheduling are still
incomplete. The software BCMC FIFO and some gates remain shared, so per-domain
close may temporarily pause unrelated traffic. PE firmware is unchanged; no
hardware retirement/reuse claim is added and no physical device was accessed.

### NI peer interface ownership and per-peer RX source identity

NI now retains a referenced netdev and peer-address snapshot for each MACID
before its first FE publication. Partial publication failures retain that exact
owner. Per-peer and whole-radio withdrawal use those recorded identities instead
of the first shared `route_dev`. A different device/address cannot silently take
an existing MACID binding. TX BA withdrawal keeps the reference while RX remains
active; full retirement releases it after the existing FE/EPP/RPQ fence. Global
release and forced joined teardown release remaining references only after their
owned resources are relinquished. Failed uplink preparation without any published
route is also explicitly retireable.

The shared uplink ring no longer sets one Wi-Fi ID for every peer. Startup clears
admission and publishes the state-table pointer; only a peer's successful FE
transaction enables its own six-bit source ID. Re-enabling the same peer preserves
accepted PN/SN, and replacing a live source identity is rejected. This uses the
existing per-peer RX descriptor field consumed by PE, without changing its layout.
PE fallback lookup now compares both receiver and transmitter addresses before
selecting a MACID, so an earlier peer with the same transmitter MAC cannot hide
the correct BSS. Hardware CAM hits receive the same full address validation.

`test_ni_peer_owner.py` executes production FE flow mutation, NI publication and
withdrawal with two devices sharing one peer MAC. It covers partial FE failures,
precise reference counts, wrong-device/address rejection, failed uplink allocation,
BA-preserved RX and global teardown. Native RX tests cover separate Wi-Fi IDs,
repeat admission without PN/SN reset, invalid/replaced IDs and no bulk enable at
startup. The linked PE tests include80 RX identity vectors with/without CAM and
49 CPU/NI address snapshot cases, plus AP/STA CCMP/GCMP/open IPv4/IPv6 acceptance
and rejection. Existing multicast, BCMC, FTTR drain, NI restore and FE failure
regressions pass. Evidence is `artifacts/fttr/ni-vif-*-20261109.*`.

The generated combined PE is32764 bytes, SHA256
`70e43f8144dff6fe571a5d185abdab8ffcd1695318d8802ef9e8e139d5af4ca8`.
It retains the fixed32768-byte IMEM limit; removing duplicate address checks by
sharing the matcher made the change fit. Conservative stack bound is7360/8192,
and the configuration without uplink builds to13992 bytes. Actual Image/modules
builds and six kernel source/header hashes match, including the generated image.

Public multi-interface operation is still disabled. NI downlink packets still
need an unambiguous BSS identity at PE ingress: ordinary destination-only lookup
is insufficient when two interfaces share a peer MAC. The first `route_dev` also
remains as the shared-ring lifetime reference. Full interface allocation,
shared-policy arbitration and upstream/downstream hardware retirement remain
open. Existing EPP/packet fences do not establish full MCE/AFT retirement or safe
hardware-ID reuse. No physical device was accessed.

### NI downlink EPP interface selection

The normal FE action already writes the output Wi-Fi interface to `mdata_byte`.
The target `ca_ni_rx_napi_get_header_from_64bit_epp` extracts EPP bits[19:14]
(second word) as that interface's software ID, and nonzero IDs consume16 bytes
of internal headers. Executing the target decoder covers384 ID/length cases;
the existing FE action converter comparison covers330 vectors and the native
Wi-Fi DMA contract covers300 original instruction cases. These establish the
software format contract; physical FE-to-EPP delivery remains untested.

NI peer admission now publishes its recorded Wi-Fi ID instead of boolean1.
Ordinary PE downlink lookup and the batch destination cache both require that
ID to match the EPP before choosing a station, including STA catch-all routes.
Authorization restores the retained ID; a netdev whose registered ID changed
cannot silently reuse an existing binding. ID0, reserved and unmatched inputs
cannot reach an ordinary station. POL multicast cookies retain their separate
complete-recipient admission and do not depend on unicast EPP identity.

The linked two-BSS test uses identical destination MACs with alternating and
repeated IDs in one EPP batch, checks exact BID chains/selected peer and role,
and injects revocation on a cache hit. Unowned/zero/reserved IDs recycle each BID
once without TXBD publication. Existing batching, PS, A-MSDU/prefix, multicast,
BCMC, FTTR drain and selected-peer withdrawal tests pass. Fixtures for ordinary
radio packets now supply the real nonzero-ID EPP format; multicast still tests
both8- and16-byte HeaderA forms. The pre-main-alignment image is32704 bytes with
stack bound7360/8192; artifacts are `artifacts/fttr/downlink-*-20261109.*` and
`artifacts/fttr/ni-downlink-id-pe-20261109/`.

The subsequent merge includes main `76ad78c` (one NI descriptor publication per
RX batch). FTTR address matching and the batch publisher are both retained;
`ni_tsf` was moved between the existing linker-checked code banks to accommodate
the larger RX wrapper. Combined PE is32764 bytes, SHA256
`e68c616b56ef35dd0d10d1afdda2af217b27efdd0239a2c56238d4d1eb5d1e08`,
with conservative stack bound7368/8192; non-uplink remains13992 bytes.
Image/modules and four source/generated-input hashes match. Eight linked
identity/STA-RX/multicast/BCMC/drain/withdrawal/batch regressions pass against the
combined image. The original full RX-backpressure run trapped in the new
pause-ACK scenario before descriptor preparation. That was subsequently traced
to Unicorn's global memory-write hook interacting with a MIPS branch-delay
stack store. The corrected, narrowly scoped observer now executes the actual
full main loop and all original assertions; the pause-ACK gap is resolved by
[the full-main validation below](#full-pe-pause-ack-and-resume-validation).
Historical reports remain `artifacts/fttr/downlink-main-*-20261109.*`.

This supplies BSS selection, not an end-to-end generation fence. Wi-Fi ID reuse
and late packets still depend on incomplete MCE/AFT retirement proof, and actual
interface allocation/shared policies remain unfinished. The developer-only NI
bypass traffic generator also needs its raw metadata contract checked before
using it with this strict admission. No physical device was accessed.

### Public port0 AP/VAP allocation and interface teardown

Both radios now advertise up to eight AP interfaces on one channel. STA and Mesh
retain their separate single-interface modes; DFS combinations on C preserve
the supported widths/regions. Linux's actual combination verifier accepts these
declarations. A contiguous MAC-address mask lets mac80211 assign distinct VIF
addresses while retaining the base first octet. The target
`rtw_phl_wifi_role_alloc@0x13a684` was executed for root plus seven VAPs: all use
port0/WMM0, MBID1..7 are distinct, and duplicate-address lookup returns the existing
role. The native driver rejects duplicate VIF addresses rather than giving two
Linux interfaces ownership of one role.

Interface addition reserves self-MACID/MBID before peers can be allocated, even
if the BSS has not started. VAP self IDs skip occupied station IDs. Reservation
survives BSS stop and uncertain CREATE cleanup while its VIF remains live;
failed sequence binding releases the unexposed reservation. Hardware cleanup
must finish before retired identities can be reused. Root removal and recreation
can coexist with live VAPs; their shared port timing remains protected by the
previous BSS implementation. Monitor removal never releases root AP state.

NI VIF release retires its peers and table binding, then transfers the shared
ring's initial netdev reference to a surviving bound interface. The last
interface tears down the ring. A failed detach retains one netdev reference in
the existing cleanup lifecycle and marks its self role as NI-retiring. A new
interface cannot claim that self-MACID until NI withdrawal succeeds. Group-stop
success also releases the old BCMC domain/device reservation, allowing a rebuilt
interface with a different self-MACID to publish a fresh template in that domain.

Actual add/start/stop/remove tests create all eight interfaces on both chip
configurations, include an ordinary peer occupying MACID1, exercise all BSSes,
remove/recreate a VAP and root, and inject binding, firmware teardown and NI
detach failures. Additional tests cover netdev reference transfer, failed-detach
retry and BCMC domain reconstruction. Twelve key/BA/NI/FE/DFS/binding regressions,
Linux combination validation and Image/modules builds pass; seven changed kernel
inputs match. Artifacts are `artifacts/fttr/vap-*-20261109.*`. PE firmware is
unchanged and no physical device was accessed.

This opens the factory port0 AP/VAP topology in code; it is not physical
multi-SSID acceptance. WMM0/EDCA, channel and port color remain shared hardware
state, as distinct from the per-BSSID CAM colors. Independent per-VIF airtime,
TSPEC policy and mixed AP/STA/multi-channel operation are not implemented.
Wi-Fi ID reuse still lacks a proven full upstream packet-retirement barrier;
existing quarantines remain. Full PE pause-ACK simulation is now verified below;
that does not prove upstream packet retirement.

### Per-VIF EDCA requests on shared WMM0

The target VAP topology shares WMM0; its role EDCA command writes that shared
bank. Native mac80211 callbacks previously retained only one global parameter
set, so a slot-time notification on another VIF could combine the wrong AIFSN/
contention window with that VIF's slot duration. Each VIF now retains its four
requests, computed words and nonwrapping request order. The shared AC selects
the most recent request from an active VIF. Slot-time changes recompute only
that VIF's own request without replacing a newer owner's request. Stop/removal
restores the most recent surviving request; restart restores that VIF's saved
request subject to the same ordering. This request ownership policy is the
native mac80211 adaptation, not a claim that the factory implements a Linux
VIF queue.

Dynamic WMM's temporary BE override keeps the latest requested BE/BK restore
values behind it. A config update or interface removal while WMM is enabled
therefore cannot be undone by restoring a stale snapshot when WMM exits. The
last active VIF restores the baseline and clears temporary WMM/quota state;
the next activation captures a fresh baseline. Unsupported contention-window,
TXOP/AIFS encodings and sequence exhaustion return explicit errors.

Actual callback tests cover both chips and all four ACs, different VIF requests,
slot changes, stop/restart/removal, first/last-owner baseline handling, dynamic
WMM enter/exit and overflow. Eight actual target wrapper/address/write results
match native callbacks. AP/mesh, STA/BA, BSSID color and DFS regressions pass;
Image/modules builds and three changed source hashes match. Reports are
`artifacts/fttr/edca-*-20261109.*`; PE code is unchanged. Per-BSSID CAM colors and
the shared port-color register retain their existing factory-backed behavior.
This does not implement per-VIF hardware WMM queues, airtime fairness or TSPEC;
full physical acceptance and hardware retirement remain outstanding.

### Ordinary NI unicast uses Linux QoS and admission policy

Ordinary NI unicast previously reclassified DSCP with a fixed priority mapping,
while multicast already used the current mac80211 classifier. The radio now
compiles the same Linux policy for each unicast peer before FE admission. A
128-byte per-peer DDR slot at0x69400 holds64 DSCP TIDs, the inline-tagged/default
TID and no-ACK bitmap. PE consumes that snapshot for IPv4/IPv6 and tagged frames;
no-ACK also clears aggregation in the selected request. Multicast retains its
immutable per-copy policy. The table fits within the existing512KiB TX context.

All existing mac80211 QoS/ACM/TSPEC/no-ACK/reserved-TID policy publication sites
now close ordinary NI admission before changing Linux state. The existing
non-sleeping key-refresh gate and deferred withdrawal worker are reused; a
reauthorization cannot reopen it while refresh is pending. The radio retires
old queued work before replacing a policy. Compilation or withdrawal failure
keeps the former snapshot and closed route for retry. A STA with admitted TSPEC,
unsupported fragmentation/control-port policy or another unsupported classifier
configuration is withdrawn to Linux, where the required per-packet accounting
runs. This corrects bypass of Linux policy; it does not invent hardware airtime
measurements or claim native TSPEC offload. The target OnAction_wmm remains a
no-op and target RPQ does not supply proven airtime data, as recorded earlier.

The end-to-end offline test executes the actual Linux classifier, native radio
snapshot publisher and linked PE for384 IPv4/IPv6 DSCP/no-ACK selections, tagged
frames, unchanged-policy idempotence, failed withdrawal and TSPEC rejection.
The complete Linux policy matrix covers1179648 comparisons and18 publication
sites. Additional actual publication tests verify unicast closure before the
policy changes. Existing FE retry, per-VIF identity, multicast, BCMC, FTTR drain,
EPP batching and selected-peer withdrawal regressions pass. Firmware fixtures
that isolate batching initialize an explicit synthetic policy; they are not
counted as Linux-classifier proofs.

PE is32448 bytes, SHA256
`f6d8b207b42d0f9f9cce54bc16616a524742c8752f0647d12b11e9a42c4a71f6`,
with conservative stack7360/8192; non-uplink builds to13992 bytes. Image/modules
builds, seven changed kernel inputs and patch238's zero-fuzz apply/reverse round
trip pass. Evidence is `artifacts/fttr/unicast-policy-*-20261109.*`. No physical
device was accessed; full upstream packet retirement and ID reuse remain
outstanding. The full-main pause-ACK simulation gap is now resolved below.

### Retired Wi-Fi FE rules gate final NI release

Wi-Fi route withdrawal now handles retired FE port generations. Normal port
lookup deliberately excludes them, but failed Hash/cache deletion can retain
rules which still target the radio's NI destination. Withdrawal retries actual
`fe_delete` for every old generation of the same netdev, including input and
multicast-recipient references, before reporting success. An active port's
already-deleting rules are also finished rather than skipped or re-enabled.
An absent port is idempotent only for withdrawal; route publication still
returns ENODEV. Unrelated ports and their published rules are left intact.

Whole-radio withdrawal closes every PE peer admission before attempting any
fallible FE operation. The PCI-removal path no longer continues into NI resource
restoration after an unacknowledged producer withdrawal. If graceful teardown
fails, it checks FE withdrawal again immediately before the forced DMA path;
a previous successful attempt cannot justify ignoring a later failure. This
preserves the existing handling of retained EPP/WP/BID resources after upstream
rules have been withdrawn, including partial EQ-return retries.

`test_fe_retired_wifi.py` executes the actual FE deletion/table helpers across
18 active/retired/input/output/replica cases with Hash/cache failure and a new
port generation. It verifies retained retry ownership, no re-publication,
unaffected foreign rules and idempotent absent-port withdrawal. NI tests inject
errors at either peer and repeat both direct/failed-graceful teardown attempts:
all PE gates close, while no NI DMA stop/restore/free or interface release runs
before successful FE withdrawal. Existing peer-route, FE multicast, NI restore,
partial BID teardown, inflight release and binding regressions pass (ten test
entries); Image/modules and both changed source hashes match. Artifacts are
`artifacts/fttr/retired-route-*-20261109.*`; PE is unchanged.

This is an ownership-ordering fix, not a complete hardware producer fence.
MCE/AFT or downstream packets already beyond FE still require independent
retirement evidence; no hardware-ID reuse is authorized by this check. Supplier
removal and failure recovery continue to have the previously documented physical
acceptance limits. No physical device was accessed.

### Original downstream time settings and durable synchronization

The syncserv client implements the retained `GetTimeSetting` and `SetTimeSetting`
transactions. `trace_stock_fttr_time.py` executes the actual controller builders,
Ethernet encoder and pending reply handler. Requests carry the master's `MAC`;
Set adds integer `TimeEnable`, string `NTPServer1`/`NTPServer2` and integer
`TimeZone`. These are the original MIB128/133/134/129 fields. Servers fit the
original46-byte buffers (at most45 bytes plus NUL). `TimeZone` is the raw signed32
JSON encoding of the MIB word, not an IANA name or an inferred UTC offset.

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa time-settings
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa time-apply /etc/fttr-time.json
```

The private input file contains exactly four fields, for example the following
wire-format example; select the actual peer's raw zone code from its reported
configuration rather than assuming what24 means:

```json
{"TimeEnable":1,"NTPServer1":"192.0.2.123","NTPServer2":"2001:db8::123","TimeZone":24}
```

The explicit write first reads and validates a complete peer reply, sends one
Set request, then independently queries again even after a lost or rejected ACK.
A positive Result alone is not a completed read: source/JSON MAC and all four
fields must match the expected peer and schema. Supplied RPCMethod is checked;
re-registration cancels the transaction. Reports distinguish `write_result`,
`readback_result` and `readable_fields_match`. `clock_synchronized` and
`configuration_verified` remain false: field equality is not evidence that NTP
ran or the clock is synchronized. The original envelope has no transaction ID,
so late identical same-source replies cannot be fully distinguished.

Resident policy peers accept optional `Time` with the same four fields. Under
an identity-matched Ready1 registration it runs after WiFi and Radios, before
EasyMesh. Ready0 observes without writing. With `--state`, record version4 pins
Time to the Revision and fsyncs attempted bit19 before transmission. Recovery
queries and compares the recorded target; it does not replay an attempted write.
A changed Time under the same Revision is refused. Policies without Time retain
their previous record versions. Automatic UCI extraction of local time settings
is not part of this change; Time is supplied explicitly in the peer policy.

Validation includes eight original enable/raw-zone encodings and ACK branches,
27 real AF_PACKET/veth CLI/resident cases,14 schema cases and ASan/UBSan reply
parsing checks. The network cases cover fragments, bare ACK/partial data, wrong
source/MAC/method, readback mismatch/timeouts, re-registration, Ready0, fsync
failure, SIGKILL after send, read-only restart, revision drift and ordered mixed
WiFi/Radio/Time policies. Existing Wi-Fi CLI, resident/store, Radio and EasyMesh
regressions pass. Host fttr-sync and aarch64-musl fttr-sync/fttr-sync-policy builds
pass; evidence is `artifacts/fttr/time-*-20261109.*`. Kernel/PE were unchanged and
no physical device, real time service or downstream gateway was accessed.

### Original downstream IPTV settings and durable readback

The syncserv client now implements `GetIPTVSetting` / `SetIPTVSetting`:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa iptv-settings
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa iptv-apply /etc/fttr-iptv.json
```

An enabled private configuration contains exactly the original three fields:

```json
{"IPTVEnable":1,"IPTVVlan":100,"IPTVLanPort":"LAN1,LAN3"}
```

VID0 represents the original untagged recipe; other accepted VIDs are1..4094.
The port list is the original ascending, nonduplicated LAN1..LAN4 string. Empty
lists are representable: the target builder emits one when the selected WAN has
only port-mask bits outside its four LAN positions. Disabling sends only
`{"IPTVEnable":0}`; VLAN/port fields are deliberately omitted as in the target
builder. Replies reporting disabled state are compared on that switch, without
claiming that previous inactive VLAN values have been erased.

`trace_stock_fttr_iptv.py` executes the actual controller builder over128
port-mask/tag/VID cases. It confirms the first enabled mode0 WAN with service
bit2 and a nonzero port mask supplies the profile; failed/ineligible MIB rows
are skipped. The original independent request and reply methods, source MAC and
slave multicast envelope are retained. Its Result response only acknowledges
the request. Native application first queries a complete matching peer response,
sends one write, then independently reads back, including after timeout/NACK.
A re-registration cancels the transaction. `readable_fields_match` is separate
from `traffic_verified:false`: no IPTV stream, forwarding or remote switch table
has been verified by a matching JSON response.

Resident peers accept optional `IPTV` with the same object, after WiFi, Radios
and Time and before EasyMesh. Ready0 never writes. Record version5 pins the
configuration to its Revision and fsyncs attempted bit20 before sending. Restart
uses read-only reconciliation, including after SIGKILL immediately following
Set; same-Revision configuration drift is refused. Policies without IPTV retain
their existing record versions. This does not automatically import local WAN
service policy or implement the separate Get/SetIPTVPortInfo methods.

Validation includes the original128 builder cases,30 CLI/resident AF_PACKET
network-namespace cases,40 schema checks and ASan/UBSan malformed-response tests.
These cover enable/disable, untagged/all/empty port sets, MAC/method/source
mismatch, partial replies, fragments, ACK/readback failures, Ready0, fsync failure,
crash recovery and combined WiFi/Radio/Time/IPTV ordering. Time's27 cases, Radio,
EasyMesh and existing persistent-store regressions pass. Host fttr-sync and
musl fttr-sync/fttr-sync-policy compile successfully; reports are
`artifacts/fttr/iptv-*-20261109.*`. Kernel and PE are unchanged; no physical device
or actual IPTV forwarding path was accessed.

### Separate IPTV logical-port RPCs

The original `GetIPTVPortInfo` and `SetIPTVPortInfo` methods are now available:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa iptv-ports
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa iptv-ports-apply /etc/fttr-iptv-ports.json
```

The private write document uses two strings, for example:

```json
{"IPTVLanPort":"1,3","IPTVVlanId":"100"}
```

This protocol uses numeric logical port IDs, unlike `SetIPTVSetting`'s LAN1..4
labels. The original manager converts interface names through its port mapping
before saving this numeric list; native input therefore requires the explicit
peer's logical IDs. It does not assume that logical ID1 is a particular physical
socket. Empty lists are accepted, duplicate IDs and malformed/noncanonical
numbers are refused, and the original32-byte storage limits the string to31
bytes. VLAN is a canonical decimal string0..4094. The native validator bounds
logical IDs to positive signed32 values; this is a representation bound, not a
claim that the subordinate implements every such port.

`trace_stock_fttr_iptv_port.py` executes the actual controller builders, Ethernet
encoder and pending25 reply-to-MIB parser across24 port/VLAN combinations. The
reader saves only `IPTVLanPort` at the original MIB row offset723; it does not
consume `IPTVVlanId`. Native application consequently queries before writing,
sends once and independently compares the returned ports, with
`ports_match`/`readable_fields_match` separate from `vlan_verified:false` and
`traffic_verified:false`. Even a successful Set ACK plus matching ports does
not verify the requested VLAN or actual IPTV delivery. The original envelope
has no transaction ID, so same-source delayed replies retain the known limit.

Resident policy uses `IPTVPorts` and executes after optional Time, before
EasyMesh. It is mutually exclusive with `IPTV` on the same peer to avoid two
protocols overwriting its IPTV configuration. Record version6 pins both strings
to the Revision and fsyncs attempted bit21 before transmission. Recovery reads
ports without replaying Set; it explicitly leaves VLAN unverified. Same-Revision
VLAN drift is still rejected even though the remote query cannot verify it.
Policies without IPTVPorts keep their prior record version.

Validation includes the24 original builder/parser cases,30 actual CLI/resident
AF_PACKET/veth cases,26 field checks and ASan/UBSan. Cases include cleared lists,
nontrivial numeric ports, wrong replies, timeout/NACK/readback mismatch,
re-registration, fsync failure, SIGKILL/restart, Ready0, conflicting policies,
Revision drift and WiFi/Radio/Time ordering. Existing Time, IPTV, Radio, EasyMesh
and durable-store regressions pass. Host fttr-sync and musl fttr-sync/
fttr-sync-policy build successfully; reports are
`artifacts/fttr/iptv-port-*-20261109.*`. Kernel/PE and physical devices were not
changed or exercised.

### Original web-password synchronization with redacted output

The native client implements the retained `GetWebPassword` / `SetWebPassword`
RPCs. The target controller's builders read MIB175 into a30-byte string buffer
and emit `WebAdminPassword`, alongside method and source MAC. The offline oracle
executes these builders, their nested form, Ethernet encoder and pending ACK
branches using synthetic values only; no real saved password is read.

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa web-password
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa web-password-apply /etc/fttr-web-password.json
```

The private file contains exactly `WebAdminPassword` as a string of0..29 UTF-8
bytes, without embedded NUL. This preserves the original field capacity; it does
not infer the subordinate's login acceptance rules. The value is supplied in a
file, not a command-line argument. Query output reports only that a valid field
was returned, never the password. Apply performs a complete identity-correlated
preflight, one Set and independent readback, including after a lost/rejected ACK.
It reports `password_matches` separately from `credentials_verified:false`:
matching reported text does not prove that an HTTP login succeeds. The inherited
syncserv Ethernet protocol carries the value as JSON without encryption; output
redaction does not change its wire format.

Resident peers accept `WebPassword` with the same one-field object, after WiFi,
Radios, Time and any IPTV configuration, before EasyMesh. Ready0 does not write.
With persistent state, version7 includes the exact desired value in the existing
private mode0600 record and commits attempted bit22 before transmission. This
record contains credentials and must retain its private directory permissions.
Recovery reads and compares without replaying Set; same-Revision changes are
rejected. Other policies retain their existing version. Password strings and
field values are omitted from CLI/event/test reports; private desired state is
not mislabeled as an encrypted secret store.

Validation covers five original synthetic length/escaping recipes,31 actual
AF_PACKET CLI/resident cases and15 schema cases, including29-byte/UTF-8 bounds,
empty passwords, escaping, output redaction, mode0600 state, errors/timeout,
re-registration, fsync failure, SIGKILL/restart, Ready0, revision drift and ordered
mixed policies. ASan/UBSan reply parsing and existing Time/IPTV/port/store
regressions pass. Host fttr-sync and musl fttr-sync/fttr-sync-policy compile;
artifacts are `artifacts/fttr/web-password-*-20261109.*`. No physical device,
credential installation or login was tested. Kernel/PE are unchanged.

### Full PE pause ACK and resume validation

The previous full-main pause test was blocked by its observation hook rather
than by the PE implementation. A minimal execution of the actual MIPS pattern
(conditional branch, delay-slot stack store, following absolute J) succeeds
without a global write hook and traps with one in Unicorn2.1.4. The test now
observes only the NI write cursor, NI doorbell and command-ACK mailbox words.
No production instruction, firmware feature, acknowledgement assertion or DMA
completion model was weakened to make it pass.

The complete RX backpressure suite now passes without skipping the full-main
case. Its pause matrix has36 cases:1/4/17 captured packets; normal and wrapped
NI descriptor positions; pause requested at first/last descriptor preparation
or at doorbell publication; ordinary and near-wrap command numbers. Every ACK
follows the completed batch doorbell and exact descriptor payload checks. While
paused, further main-loop execution neither publishes the seventeenth packet
nor releases payloads still owned by NI. Resume acknowledges the new command,
processes the remaining prefix once and preserves PN/SN and the514-buffer
ownership inventory. The two-descriptor prefetch retention remains intact.

Related linked PE1 TX staging/drain tests confirm no pause ACK while TXBD credit
prevents pending submissions, independent PE0 pause, and eventual ACK after
publication. ARM pause/open, NI queue restoration, inflight release and RX
handoff regressions pass. Rebuilding PE from current sources produces exactly
the committed32448-byte header, SHA256
`f6d8b207b42d0f9f9cce54bc16616a524742c8752f0647d12b11e9a42c4a71f6`;
stack bound7360/8192 and IMEM-entry checks pass. Reports are
`artifacts/fttr/pe-pause-matrix-20261109.log` and
`artifacts/fttr/pause-*-20261109.*`.

This closes the specific full-main pause-ACK offline validation gap. It is not
a new hardware retirement fence: ACK means PE has completed its local batch;
NI DMA consumption, two prefetched payloads, MCE/AFT upstream work and ES/MiniOLT
downstream traffic still have distinct ownership requirements. Safe hardware-ID
reuse and physical timing/traffic acceptance remain incomplete. Production
kernel/PE sources are unchanged in this increment and no physical device was
accessed.

### Original QosSync transport and durable send ownership

The retained controller's `build_QosSyncSetting` at `0x4128a0` obtains
`ahsapd.qos` from the output of `ubus call ahsapd.qos getQosConfig`, preserves
the object's fields and overwrites `SyncFromMaster` with the **string** `"1"`.
Its envelope has `RPCMethod: "QosSync"`, the master's MAC and that object.
Pending method18 consumes Result replies without an independent configuration
readback. `tools/trace_stock_fttr_qos.py` executes those target instructions,
the original Ethernet encoder and reply dispatcher. Ubus/file/JSON boundaries
are modeled; the synthetic inner fields are not an asserted vendor QoS schema.

Native explicit synchronization accepts a private JSON file containing exactly
the `ahsapd.qos` object exported by the matching implementation:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa qos-sync /etc/fttr-qos.json
```

The target receives one unicast request. The object is copied before imposing
`SyncFromMaster`, so the caller's configuration and durable record remain
unchanged. Missing/non-object payloads fail before transmission. The final
encoded JSON must fit the existing1976-byte single-frame payload limit; a
larger policy fails with EMSGSIZE, without truncation or partial transmission.
Original request fragmentation and arbitrary QoS field semantics have not
been established. Allocation/copy/serialization failures also prevent sending.
The exchange has the existing three-second limit, filters Ethernet source and
explicit RPCMethod, supports existing response reassembly, and aborts on a new
registration. A successful ACK reports `ack_seen`; `scheduler_verified` and
`configuration_verified` remain false. Output contains no raw policy values.

Resident peers can add `"QoS": {"ahsapd.qos": {...}}` to their private policy.
Scheduling is WiFi, Radios, Time, IPTV or IPTVPorts, WebPassword, QoS, EasyMesh.
QoS requires matching pinned registration and Ready1. Version8 records retain
the requested object and bit23 write intent, synced before transmission. A
timeout, rejection or re-registration cannot trigger automatic retransmission.
On restart an existing intent emits `sync_resident_qos` with EINPROGRESS and
`prior_write_outcome_unknown: true`, sends neither a second write nor an
invented query, and blocks later settings in that policy. Even an ACK observed
before restart is not proof of installed QoS. A changed revision explicitly
permits a new transaction; changed configuration under the same revision is
rejected. Without `--state`, suppression lasts only for the process lifetime.

Validation: five original builders including nested/escaped fields and the
forced marker, missing source/parse/object failures, original wire bytes and
pending18 ACK/rejection branches;38 actual glibc CLI/resident/veth cases and37
equivalent packaged musl cases (glibc fsync injection is separate), including
the exact envelope size limit, wrong source/method, fragmented ACK, failed
identity, Ready0, crash/restart, fsync failure, revision drift and all seven
configuration stages. ASan/UBSan checks exercise malformed replies, immutable
input and each native JSON allocation/copy/serialization failure. Existing
WAN26, Time27, WebPassword31, resident37, store38 and EasyMesh19 CLI/12 info/20
resident cases pass. The packaged musl UCI converter/resident test also passes.

The FTTR package and retained FPGA companion rebuild and stage successfully;
67 package source/header inputs match current source, and all four installed
programs match their package staging files. Reports are
`artifacts/fttr/qos-{original,native,musl,regressions,policy,package-identity}-20261109.json`
and `qos-package-20261109.log`. This refreshes the userspace packages, not the
complete kernel/rootfs/FIT image. No kernel/PE production code changed and no
physical device was accessed. Actual subordinate policy installation, local
configuration event import and native hardware scheduler mapping remain open.

### Failed Wi-Fi withdrawal stays closed across interface reopen

The previous notifier retained an endpoint when FE withdrawal or the shared
CPU DMA cursor wait failed, but a later AP/STA UP event immediately registered
its ingress hook again. The current running netdev also made that endpoint
eligible for CPU EPP delivery. A retained UNREGISTER generation could meanwhile
be missed by `rtl_fe_flush_port`, which looked up only the active port. The
actual old notifier reproduces the premature reopen in
`scripts/test_wifi_notifier.py --before` (the fixture reads the known
pre-fix source at `e109480`).

The endpoint now enters closing before fallible withdrawal, followed by a
network grace period joining admitted CPU submissions and RX callbacks. Closing
endpoints are excluded from NI ID lookup, new CPU NI submissions, ordinary
Wi-Fi EPP hits/misses and the Wi-Fi portions of multicast host-copy delivery.
Failures retain the ID, netdev reference and closed state. Reopening completes
the previous detach before creating a new endpoint and registering its ingress
hook. Invalid detach IDs fail explicitly and an already absent ID is idempotent.

FE port flush now deletes matching input/output/replica flows across every
retained generation of the netdev. An active Wi-Fi port is also marked closing
under the FE mutex, rejecting new peer-route publication and new unicast or
multicast rules until the next successful NI attach. This prevents another
producer from republishing rules while failed cleanup is retained. Existing
flow-block ownership across DOWN/UP remains intact; ordinary LAN/PON port
flushes do not acquire the Wi-Fi gate.

Target FC disassembly at `rtk_fc_eventHandler_netDev+0x2a0` (`0x91f20`) confirms
the wireless branch calls flow deletion by wireless index before the helper
that removes its mapping; the original branch ignores the deletion result.
Native preserves that ordering while retaining failed cleanup ownership.
The existing target CPU receive/registration oracle also verifies that a
wireless copy requires the current active mapping and a running device.
Neither observation supplies a hardware drain fence. An attempted full event
oracle failed to reach this branch and was removed, not counted as validation.

Validation includes old-notifier reproduction, actual notifier add/delete
retry across AP/STA/mesh/monitor transitions,60-ID endpoint capacity/lifetime
checks with ASan/UBSan,36 active/retired FE Hash/cache fault combinations and
new-rule rejection for closed inputs/outputs/replicas. Actual QM polling covers
672 host/ordinary-copy cases including closing endpoints, both HeaderA forms,
VLAN/COS and EPP credit/return ownership. Bridge host completion, complete
multicast learning/M2U, peer-route publication, radio withdrawal, NI restore,
multicast binding and retained Ethernet/VLAN/PPPoE frame regressions pass.
Image/modules compile and all five changed kernel source inputs match the
compiled tree. Reports are `artifacts/fttr/wifi-withdraw-*-20261109.*` and
`wifi-reopen-before-20261109.log`. PE is unchanged; no device was accessed.

This closes a failed-withdrawal admission bug. Successful detach still uses
the existing CPU cursor/EPP drain, which does not prove MCE/AFT or downstream
retirement. Wi-Fi ID reuse after successful detach, including a delayed packet
arriving after a new endpoint is attached, remains an explicit unresolved
requirement. No additional reuse permission or full-drain claim follows from
the closing flag.

### Original downstream wireless station information

The explicit syncserv client now queries the connected subordinate's station
table using the retained `GetWifiStaInfo` request:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa wifi-stations
```

The request has only `RPCMethod` and uses the target's Ethernet address. The
response must name `GetWifiStaInfo` and contain a `StaInfo` array, including an
empty array for an empty table. A bare ACK or missing list does not complete
the query. The existing interface lease, source pin, three-second deadline
and independent type6/type7 fragment assembly apply; a new registration
cancels the query. After validating the whole report, native sends the same
empty JSON response object that the target controller emits. Failed response
transmission returns an error without exposing a successful snapshot.

Each row retains the original `OnuMAC`, `OnuIntf`, `BandType`, `StaMAC`,
`StaRssi`, `StaNegoUpRate` and `StaNegoDownRate` fields. OnuMAC and StaMAC must
be valid unicast MAC strings; every OnuMAC must match the queried device.
Other known fields accept the original JSON string or integer form, with
bounded strings and no embedded NUL. Missing measurement fields stay absent,
and unknown fields are not copied into output. The same terminal MAC on
different BSS interfaces is retained as separate records. Original names and
reported values are preserved; no inferred rate units or RSSI calibration
are added. `report_complete` means the complete valid report was received;
`identity_authenticated` remains false.

The original consumer clears its table and consumes at most64 records. It
narrows RSSI to8 bits and negotiated rates to16 bits, and serializes OnuIntf
with JSON quotes before copying into a fixed buffer. Native retains the
received values, rejects more than64 records rather than silently truncating,
and never exposes partial rows if validation or allocation fails.
`tools/trace_stock_fttr_stations.py` executes the actual request builder,
consumer, empty response and Ethernet encoder. All seven fields are checked
in complete one/two-record reports;0/63/64/65-record cases independently check
clear, capacity and truncation behavior using compact records. The file-save
boundary and remote data are explicitly modeled.

Validation comprises33 glibc CLI/veth cases (including response-send failure),
32 equivalent packaged musl cases, full64-record out-of-order reassembly,
empty/missing/oversized tables, source/method/record identity checks,
re-registration, malformed/NUL/type cases and same-terminal separate BSS rows.
ASan/UBSan passes22 native JSON allocation/copy failure points plus malformed
and partially built reports. Radio19 CLI/7 resident, QosSync38 and resident
controller37 regressions pass. The FTTR and retained FPGA packages rebuild;
67 sources/headers and four installed programs match the package staging.
Reports are `artifacts/fttr/stations-*-20261109.*`. Kernel/PE are unchanged and
no device was accessed.

This supplies station-report input for later gateway/roaming coordination; it
does not implement a roaming decision or prove association/RF behavior. The
separate `GetWifiNeighbor` report uses `scanResult` text and NEW/REMAINING file
append semantics. The retained manager waits two seconds after requesting it
before reading the file (`getWifiNeighbor+0x2e4`, `0x450818`); this is not proof
that all segments arrived. Complete neighbor scan termination/loss handling,
automatic resident station collection and physical acceptance remain open.

### Original neighbor report collection without a false completion claim

The explicit syncserv client now sends the original `GetWifiNeighbor` query:

```sh
fttr-sync fttr0 02:00:00:00:00:01 auto 0x88aa wifi-neighbors
```

`tools/trace_stock_fttr_neighbors.py` executes the retained builder at
`0x412198`, the complete controller report branch, its file replacement/append
decisions and reply encoder. A report carries `MAC`, `RECVFLAG` and the string
`scanResult`. NEW replaces stored text (including an empty string); REMAINING
appends verbatim. A repeated REMAINING is appended again by the original
receiver. It replies with `{"Result":0}` after each report. No total count,
final flag or snapshot sequence is consumed by this branch. The retained
manager's fixed two-second delay before reading the file remains insufficient
to prove completeness; no subordinate producer was found in the retained
inputs to establish a stronger completion contract.

Native preserves these verified query/report/reply operations and the raw
text, including records split between reports. It requires a NEW for this
query before accepting continuation data, verifies the reported MAC and
Ethernet source, and rejects unknown flags, invalid UTF-8/types and embedded
NUL. Another NEW replaces the current text and increments `restarts`.
Automatic discovery's diagnostic summary is cleared before beginning report
accumulation, so it cannot make an orphan continuation appear valid.

Collection lasts the existing fixed three-second exchange window. A query
with accepted data and no outstanding transport fragment returns observed
`scanResult`, `reports`, `restarts`, `scan_complete:false` and
`identity_authenticated:false`. This is a successful observation window,
**not** a complete scan or authenticated neighbor inventory. The window is
not extended by reports. No data, an unfinished transport fragment, malformed
continuation, new registration, allocation failure or failed per-report reply
returns an error and discards the output. Cumulative reception is bounded to
64 reports and64KiB of current text; replacement still consumes report budget.
Duplicate application reports are not guessed away, since their wire format
has no identity to distinguish duplicates from equal data. Missing whole
reports and delayed previous-scan data cannot be reliably detected with the
verified fields. No scan-result text is used for automatic roaming decisions.

Validation passes32 glibc and31 packaged musl CLI/veth scenarios, including
NEW/append/replace/empty/duplicate, split text, report fragments, wrong source
or method, orphan continuation before/after automatic discovery, late data,
registration change, incomplete fragments after valid data,64-report/64KiB
bounds, bad data after a valid prefix and reply-send failure. ASan/UBSan covers
42 allocation/copy failures across first report, replacement and continuation;
the old internal value remains owned until a replacement is fully built.
Station33, QosSync38 and EasyMesh19 CLI/12 info/20 resident regressions pass;
existing station and settings allocation/parser checks also pass.
The FTTR/FPGA packages rebuild and67 source/header inputs plus four installed
programs match package staging. Evidence is
`artifacts/fttr/neighbors-*-20261109.*`. Kernel/PE are unchanged, no device was
accessed, and full image/physical acceptance is not implied.

Complete neighbor scan semantics, structured neighbor field conversion,
resident collection and roaming coordination remain incomplete. This addition
does not resolve the hardware retirement and ID-reuse requirements below.

### Full offline image refresh after the accumulated rewrite

The complete OpenWrt `world` build has been refreshed from branch `5b24120`,
which contains local main `76ad78c`, plus the AP capability description fix
in this increment. The kernel directory was cleaned and prepared again from
the pinned Linux6.18.55 source, current overlay and the complete patch series.
All91 saved patch targets match the previously validated prepared tree byte
for byte, and all196 overlay files match the newly prepared inputs. This
rules out an uncommitted manual kernel edit being required for this build.
Image, modules, userspace, rootfs, target images and package indexes complete.
The main checkout was mounted read-only inside the build mount namespace.

Build preparation encountered older LuCI patch output in the isolated feed.
The affected files were saved, compared with pristine feed HEAD plus the
current complete patch series, and updated only after confirming the old
AP-only restrictions explained the differences. Current source also still
described a single AP despite the implemented eight-AP combination. The
description now states eight APs on one channel or a single STA/mesh role;
the existing STA/mesh replacement guard remains. Full LuCI patch replay twice,
JavaScript/ucode behavior checks, LAN address UI tests and the actual Linux
interface-combination verifier pass. The new description is present in the
final rootfs.

Rebuilt PE is32448 bytes with SHA256
`f6d8b207b42d0f9f9cce54bc16616a524742c8752f0647d12b11e9a42c4a71f6`;
it matches the committed header and the actual `rtl_ax_ap.ko` inside initramfs.
Linked-entry validation, fixed32768-byte IMEM and conservative7360/8192 stack
checks pass. The four native FTTR programs in the image exactly match the
current package builds after the same OpenWrt `sstrip -z` step. The four
FTTR/BOSA/FPGA/radio modules retain identical allocated code/data sections
to the compiled modules; OpenWrt deliberately removes their build-id notes.
Comparing a stripped ELF by missing section headers initially gave an invalid
mismatch; exact strip output and module code/data checks resolved it.

The final23461896-byte initramfs Image has SHA256
`a8a7d1fc8c6d734e597536ef5e12a07545582d2093c5fb02a7d59bbdbd564371`.
Its1905 archive entries agree with every1469 regular files and all symlink
destinations in the external rootfs tar. Current FTTR startup scripts,
default-disabled syncserv configuration, retained FPGA firmware and calibration
links are present; vendor NI/FC/relay/ASIC host binaries are absent. No private
SSH host key was embedded. The image's actual stripped `fttr-sync` passes32
station-report network-namespace cases. Its actual UCI converter passes51
conversion cases plus the resident read/write/readback path.

Separate local FTTR RAM FITs were rebuilt and their kernel, FDT and script
payload hashes/RSA signatures independently verified:

| Variant | Bytes | SHA256 |
| --- | ---: | --- |
| ASIC |15297952|`ec92a4e03d788ae08e5c590c909d7d1c1d7583cc0875b3ed73a20f4b98f3f18b`|
| FPGA |15298236|`24652a0efd22a839b50df76297a0b25579c2dd45ce626c801e2c229e2cd7ec3b`|

Both selected DTs retain the P6/reorder resources. The FPGA MiniOLT points to
the correct FPGA manager and its NI phandle; the fixed unconfirmed optical
binding remains disabled. The ordinary sysupgrade image still uses the
ordinary board DT and is not promoted to an FTTR release by this verification.
Neither RAM FIT was booted or published. This is full offline build/artifact
acceptance, not physical traffic, cryptography, optical or interoperability
acceptance, and it does not establish a new packet-retirement barrier.

Artifacts and receipts are under `artifacts/fttr/world-refresh-20261109/`,
including `build-verification.json`, `overlay-identity.json`,
`fresh-preparation-differences.json`, `dt-verification.json`, the two `ram-*`
directories and actual-image program tests. The full log is
`artifacts/fttr/world-refresh-20261109.log`. These generated/private-input
artifacts remain local; public Git contains the code and verification scope.

### Structured neighbor fields from the retained manager

`wifi-neighbors` now adds a `neighbors` array after collecting its report text.
The eight fields after the original line prefix/colon are index, radio band,
SSID, BSSID, network type, channel, RSSI and standard. Native emits the same
manager field names: `MAC` (the queried subordinate), `RadioType`, `SSIDName`,
`BSSID`, `NetworkType`, `Channel`, `RSSI` and `Standard`. The raw `scanResult`
remains available and `scan_complete` remains false.

`tools/trace_stock_fttr_neighbor_fields.py` executes the retained manager's
actual JSON-output block at `0x450a24..0x450b7c`, with explicit values in the
destinations of its preceding file scanner. Both bands and five RSSI forms
match: a positive magnitude is prefixed with minus, while negative or
zero-prefixed strings retain their form. The native parser restricts RSSI to
an optional leading minus followed by digits before applying this conversion;
it does not reproduce the original's permissive treatment of a minus anywhere
in arbitrary text. The original scanner itself is represented by its known
eight-field layout, not claimed as executed by this oracle.

Conversion occurs only after accepted NEW/REMAINING reports are joined, so a
line may span reports. LF, CRLF and a final syntactically complete row without
newline are accepted. SSID spaces/UTF8 are preserved up to32 bytes; BSSID must
be valid unicast in compact or colon form. All eight fields are required,
control characters and extra commas are rejected, and fixed field/line bounds
prevent the original unbounded `%[^,]` copies. The original format has no
verified quoting convention for commas or empty SSIDs; those inputs return an
error rather than guessing a replacement. Malformed trailing rows or allocation
failure discard the entire structured output and fail the query. Syntactic row
validity still cannot prove that all reports from a scan were received.

Validation passes77 direct original/native and malformed-row comparisons,
39 actual glibc CLI/veth cases and38 packaged musl cases, including cross-report
rows, replacement/duplicates, newline forms, malformed/BSSID/empty/extra fields
and positive RSSI. ASan/UBSan now covers62 allocation/copy failure points across
report accumulation and field conversion. Station33 and QosSync38 regressions
pass. FTTR packages rebuild; all67 source/header inputs and four installed
programs match their package staging. Reports are
`artifacts/fttr/neighbor-fields-*-20261109.*`. No kernel/PE change or device
access occurred; this increment was package-tested, not included in the prior
full RAM FIT receipt. Complete scan identity/termination, resident neighbor
collection, roaming decisions and physical RF verification remain open.

### Restarting a recorded download before End Download

A new explicit command retries an interrupted download without removing its
durable ownership or re-enabling the ONU's service first:

```sh
fttrctl /var/run/tyfza1-fttr onu-upgrade 7 5445535400000007 retry-download /path/to/image.bin
```

The record must belong to the current controller/backend/SN, be a pending
download and still record the pre-End START stage. The supplied regular file
is copied into an immutable buffer and its length and original OMCI CRC must
match the retained record before any new datapath or OMCI operation. This is
accidental-change/transport consistency checking, not a cryptographic identity,
signature or model-compatibility check. No file path from a prior process is
implicitly reopened, and no automatic retry is started after a crash.

Both Software Image slots are read again. The recorded slot and14-byte old
version must still match, and the slot must remain inactive and uncommitted;
another eligible slot cannot replace it. New START intent is fsynced before
sending. The transfer restarts at byte zero with the normal negotiated window
and existing retry rules. After End succeeds, independent readback accepts
the new version and saves that identity for later activate/commit. The old
version pin authorizes replacement of the selected slot, not a requirement
that the downloaded version remain old. Download retry never activates or
commits an image.

`tools/trace_stock_fttr_download_restart.py` executes the original ONU
normalizer, Start handler and response serializer. Four window sizes and
inactive/valid/active/committed flags confirm that a new Start removes the
partial file, frees the old window and clears partial receive state; active or
committed slots refuse it. MIB, file/allocation/timer boundaries and the
flash-validity feature are modeled. This establishes restart semantics, not
arbitrary offset resume. Once End intent has been stored, retry-download
returns EBUSY: flash programming may already be in progress and no proven
cancel/remote-offset API is available. Confirmed downloads and pending
activate/commit records are also not accepted by this retry entry.

Validation:102 native state-machine/original relay wire scenarios,16 actual
ONU Start/reset cases, and28 real daemon/CLI/FD/veth scenarios per ASIC9690,
FPGA1 and FPGA2. New cases cover SIGKILL during download, changed size/content,
target flag drift, a second crash during retry, End already sent, and failed
retry intent persistence before START. They verify complete retransmitted
bytes, preserved original record on rejection/failure and the other ONU's
datapath during each live retry. Existing upgrade-record corruption/fsync and
admin-release checks pass. Shutdown testing now waits for the actual process
instead of replying to queued packets after the test has requested termination;
runtime packet failures remain errors. Host daemon/control and full musl
packages build;67 source/header inputs and four installed programs match
package staging. Artifacts are `artifacts/fttr/download-retry-*-20261109.*` and
`download-restart-original-20261109.json`.

No kernel/PE code changed and no device was accessed. Resume at a confirmed
remote byte offset, recovery after End with unknown outcome, cryptographic
image/model verification and physical flashing/boot acceptance remain open.

### NI retirement requests close admission before fallible setup

The previous radio path recorded `retire_quiescing` only after FE withdrawal,
PE pause and all SCC packet-drop commands succeeded. If any setup step failed,
the retained route still looked eligible for a later authorization callback or
offload worker. The actual old production authorization function reproduces a
nonzero PE interface ID after failed retirement in
`test_radio_ni_retire_flush.py --revision e8f49cc`.

The driver now records global/individual retirement requests independently of
an established EPP/RPQ drain fence. A global request closes every PE peer ID
before even multicast unbinding; an individual request blocks that peer's
re-admission. Authorization callbacks, NI peer publication and BCMC startup
honor these pending requests. Failed FE withdrawal, PE pause or partial DROP
submission retains the request and retries the existing sequence. It does not
claim a completed fence or skip the remaining cleanup. Already established
EPP/RPQ waits retain the prior behavior without repeating completed DROP setup.

Background cleanup also owns pending per-peer requests, including authorization
withdrawal that has no removed VIF or deleting key. An authorization-withdrawal
failure schedules cleanup. Each request remembers whether it began as TX-BA-only
retirement, preserving RX in that case; escalation to key/STA retirement is
monotonic until completion. Successful full cleanup clears the request before
normal publication can resume. The existing status line includes global
`requested` alongside `NI_retire_quiescing` and MACID.

Target `ca_ni_virtual_instance_close` disassembly and its matching reference
body confirm that virtual-port closure clears the selected ES CPU enable bit,
disables interrupts/TX/NAPI and releases the IRQ. Its actual
`aal_l3qm_enable_tx_cpu` implementation changes only the selected low8 ES bit;
no additional queue-idle or upstream retirement acknowledgement was found in
this path. Those observations do not authorize ID reuse. Existing original
Security CAM/DCTL/TX/SCC DROP execution still matches all782 cases.

Validation includes direct old-code reauthorization reproduction, each of four
per-peer AC DROP failures, each of eight DROP positions across two peers,
failed global unbinding/PE pause/FE withdrawal, subsequent cleanup, preserved
RX-only companions, BA-to-STA escalation and existing missing EPP/RPQ completion
cases. Ten focused suites pass: full NI retirement, authorization, FE transaction,
two-device ownership, BCMC template, NI restore, station release, key cleanup,
RX BA cleanup and STA lifecycle. ASan/UBSan cover the ownership fixtures.
Two older standalone fixtures could not run because of stale extraction/type
assumptions and are not counted as passing checks. Final Image/modules build
passes, and all four changed driver inputs match the compiled tree. PE firmware
is unchanged and no physical device was accessed.

Evidence is `artifacts/fttr/ni-retire-admission-*-20261109.*`,
`ni-retire-original-drop-20261109.log` and
`ni-retire-reopen-before-20261109.log`. This fixes failed-retirement admission
and retry ownership. MCE/AFT upstream delayed packets, full downstream retirement
and safe reuse after a successful detach remain separate incomplete requirements.

### Preserve the NI fence owner while DROP setup is incomplete

The retirement-request fix above closed admission during setup failures, but
the established EPP fence still acquired its software owner only after SCC
DROP succeeded. If peer1 published its cutoff/retirement mailbox and then DROP
failed, peer2 could overwrite that same cutoff and mailbox. Executing both
production retire calls from `907e5ed` reproduces this ownership loss in
`test_radio_ni_retire_flush.py --revision 907e5ed`.

The driver now records the fence owner and `retire_drop_pending` before
resuming PE after publishing the per-peer/global retirement mailbox. Another
departing peer may close and withdraw its own route, but it cannot replace
the retained owner's cutoff. Retrying the owner issues the incomplete DROP
setup against that same cutoff; it does not capture later EPP producer positions.
Only successful DROP setup clears `retire_drop_pending` and advances to the
existing EPP/RPQ/EQ wait. Completed DROP setup is not repeated during subsequent
completion polls. A BA-only request that escalates to full retirement also
retires RX before completing the retried DROP stage.

Global teardown uses the same separate DROP stage, retaining the global fence
through BCMC or per-peer command failures. Existing admission gates stay closed
throughout. After successful cleanup, explicit re-admission republishes the
correct Wi-Fi ID and RX route; the new flags do not permanently disable a peer.

Validation covers each of four AC failure positions followed by a second
peer's retirement, unchanged EPP cutoff bytes despite advancing producers,
global/multi-peer failures, BA escalation, withheld EPP/RPQ completion, eventual
cleanup and successful re-admission. Ten regression suites pass. These include
25 linked PE peer-withdrawal cases and251 linked FTTR EPP/BID/RPQ/EQ cases,
plus authorization, FE publication, exact device ownership, BCMC, restore,
key cleanup and RX BA cleanup. Final Image/modules build passes and both
changed kernel inputs match the compiled tree. PE firmware is unchanged.
Artifacts are `artifacts/fttr/ni-fence-drop-*-20261109.*`.

No physical device was accessed. This fixes ownership of an already established
software/hardware queue cutoff through command failures; it does not prove
complete MCE/AFT upstream or ES/MiniOLT downstream retirement, and does not
enable hardware-ID reuse.

### Wireless ES occupancy participates in NI retirement

Radio retirement previously checked EPP producer/consumer equality and PE
queued/owned BIDs, but did not inspect the preceding ES queues. An empty EPP
therefore allowed release even when ES retained a packet that had not been
published to PE. The old release function at `bc25b35` reproduces this with
`scripts/test_ni_es_retirement.py --revision bc25b35`.

The target `aal_l3_tm_es_voq_status_get` at `0x6f0a0` reads `0x6830` for CPU2
and CPU3. Bits16..23 and24..31 respectively indicate occupied COS queues;
the original API inverts the selected bit to return empty. Executing all256
occupancy masks, both CPU ports and all8 COS selectors confirms4096 results
and the exact register address. This is an ES occupancy observation, not an
upstream DMA or MCE/AFT completion acknowledgement.

The native shared queue-empty check now includes all8 ES queues for the
radio, in addition to EPP cursors. Global retirement keeps the owned PE/EPP
consumer running while ES drains and completes only after the existing
pending-WP checks also pass. The release path disables its ES CPU port and
verifies the write before inspecting the queues again. If data appeared
between the preceding empty sample and port closure, it retains all maps and
resources, restores the CPU enable bit only if it had been enabled on entry,
and returns EBUSY for a later drain retry. An unapplied enable/disable write
returns EIO. A pre-disabled owner is never implicitly reopened.

Forced PCI cleanup also checks ES after disabling the CPU port and refuses
to restore maps with pending ES BIDs. Those buffers have not reached PE owner
lists and cannot be reclaimed by walking those lists. No speculative flush
register write or global RMU drop selector was introduced: the original flush
helper's timeout-fabricated success, already documented above, is not a valid
replacement for this observation.

Validation passes554 native occupancy/arrival/pre-disabled/write-failure/forced
cleanup cases and16 global lifecycle cases where EPP/PE are empty but one ES
COS remains busy. Busy retries retain ownership without resending DROP; clearing
the modeled ES status permits completion. Ten related suites pass, including
NI register restore, inflight WPs, uplink cache mode, complete peer retirement,
authorization, FE publication, exact device ownership, BCMC and linked PE
peer/FTTR retirement. ASan/UBSan check the native fixtures. Image/modules build
passes and the changed input matches the compiled kernel tree. PE firmware is
unchanged; no physical device was accessed. Receipts are
`artifacts/fttr/ni-es-retirement-*-20261109.*` and
`ni-es-retirement-20261109.json`.

This adds the previously missing ES queue to release eligibility. Packets still
upstream of ES can arrive after the observation; complete MCE/AFT production
retirement, MiniOLT downstream retirement and safe hardware-ID reuse remain
unproven and are not enabled by this change.

### Verify TQM reset edges before republishing buffer ownership

QM startup closes CB/RMU/ES, pulses TQM reset and polls the initialization-done
bit before clearing refill state and reloading all BID pools. Previously it
did not verify the control or reset writes. If either reset edge did not reach
hardware, a still-set INIT_DONE from the previous run could authorize buffer
reinitialization while the old queue state remained. The old production start
prefix at `073132e` reproduces this for control close, reset assertion and reset
deassertion failures.

Startup now reads back CB/RMU/ES disable and both TQM reset edges. Any failed
write returns EIO before clearing refill ownership, resetting EPP pointers,
publishing cache lines or loading EQ/BID entries. The existing bounded ready
poll remains; timeout also stops before reinitialization. A subsequent start
repeats the complete gate and reset sequence, including recovery from a reset
left asserted. Read-modify-write preserves unrelated reset bits, including a
change observed during the reset interval.

The retained `aal_l3qm_check_init_done` at `0x5ad90` executes against the actual
`0x6850` bit30; immediate and delayed-read cases confirm this ready contract.
The reference/target startup reset sequence supplies assertion,1ms delay and
deassertion, but does not make a stale high ready bit evidence of a successful
new reset. Native adds the explicit write checks without changing the verified
EQ layout or reset timing.

`scripts/test_qm_reset_gate.py` executes the production startup prefix up to
the refill/BID reinitialization boundary. Its45 cases cover all five write
failures, ready timeout, retry, asserted-reset recovery and unrelated bits.
ASan/UBSan pass. Old-revision reproduction can isolate either reset edge with
`--revision 073132e --failure 4` or `--failure 5`. Six regressions pass: separated
pool allocation, cached EPP publication/lifecycle, original EQ/EPP attributes,
stop/poll interleavings, FTTR fragmented receive and multicast host copies.
The AXI-attribute script required its retained input path to be supplied from
the main checkout; missing default input was not counted as a product failure.
Image/modules build and final source identity checks pass. Artifacts are
`artifacts/fttr/qm-reset-*-20261109.*`. PE is unchanged; no device was accessed.

This guards the existing restart boundary before buffer ownership is rebuilt.
It does not establish stop/free bus-idle, downstream packet retirement or safe
hardware-ID reuse. Those remain independent incomplete lifecycle requirements.

### Failed shared QM closure retains published DMA pools

The shared QM previously issued stop writes without readback and freed every
allocation unconditionally. The actual old functions at `aea5f88` reproduce
freeing hardware-published pools even when one of those writes is rejected.
Allocation-only cleanup and pools from a partially failed start were also not
distinguished by an explicit ownership state.

QM now records `hardware_owned` before the startup sequence begins publishing
queue/pool state. Thus a later failed start is covered even if
`deepq_configured` was never set. Stop still attempts every CB, ingress,
RMU and ES close, then records EIO in `stop_error` if any corresponding enable
field remains set. Final free retries stop for a published allocation; if
closure remains unconfirmed it logs the error and retains the entire set of
DMA pools and EPP rings. Allocation failure before hardware publication does
not touch inherited registers and retains the existing normal cleanup.
State reporting includes both ownership and the last close error. A new start
continues to require the complete checked TQM reset sequence before replacing
BID ownership.

Validation executes production allocation/stop/free with all15 combinations
of failed close writes, both optional-FTTR pool layouts and complete/partial
startup ownership:62 cases include repeated free attempts, exact allocation
retention, successful retry and unchanged unrelated control bits. Old-revision
reproduction now reaches the actual free calls and confirms the premature
release. Six regressions pass: pool allocation,45 reset-gate cases,17024 cached
EPP cases plus reopen,100 stop/poll interleavings,768 fragmented FTTR receive
cases and multicast host-copy delivery. ASan/UBSan cover the lifetime fixtures.
Image/modules build and both final kernel inputs match. Artifacts are
`artifacts/fttr/qm-close-*-20261109.*`.

This fixes explicit close-failure ownership. Reading disabled controls is not
proof that all previously issued bus accesses have completed; successful
stop/free still lacks a complete bus-idle/producer/downstream retirement
contract. This change does not authorize ID reuse. PE firmware is unchanged
and no physical device was accessed.

### Forced radio teardown returns unconsumed EPP buffers

Forced radio cleanup previously walked the PE WP-owner and queued-BID lists,
then restored EPP read pointers to freshly sampled producers. EPP entries not
yet ingested by PE belonged to neither list, so this cursor update could skip
their buffer return. The original native cleanup at `00c5cf3` fails the new
retained-EPP release boundary in `test_ni_restore.py --revision 00c5cf3`.

Forced teardown now requests EPP retirement from the QM supplier that owns
the exact ring allocations and split buffer pools. The radio first withdraws
FE, disables its ES CPU port, verifies ES empty, joins PE/PCI DMA and finishes
the existing software owner walks. The QM routine then checks the current
eight ring bases, aligned in-range producer/consumer pointers and every BID
address against the selected radio's allocated2KiB buffers. Wrong-pool,
misaligned or out-of-range addresses are not returned to EQ. Packet fragment
flags do not prevent returning an otherwise owned buffer.

The routine advances and reads back the consumer before returning that buffer
to EQ. It retains the sampled address/COS/next cursor if the read-pointer write
fails or EQ return is blocked, so retries never reread a reused descriptor or
return the same BID twice. At most64 buffers return per invocation; pending
work returns EAGAIN. All COS pointers and ES occupancy are checked again after
the pass. The supplier call is serialized with supplier removal. A radio
`epp_retiring` flag keeps later retries on the forced path, preventing a now
empty EPP cursor from bypassing a pending EQ return. Raw EPP entries have not
acquired PE FTTR/multicast generation references; this path does not decrement
those counters.

Register restoration no longer writes any EPP consumer pointer. Only actual
PE/ARM consumption advances it; a later producer update remains visible instead
of being silently skipped during restoration. This does not settle how every
upstream late arrival retires, but removes the identified buffer-return loss.

Validation passes162 actual QM retirement cases: two radio pools, all COS,
ring wrap, fragment flags, split-pool ends, wrong addresses, producer geometry,
EQ timeout, failed read-pointer write, changed ring during retry,64-buffer
budget and late lower-COS publication. Native forced-cleanup tests verify that
EPP errors prevent map/resource restoration and remain retryable after cursor
advance. Ten suites pass, including existing partial aggregate/queued BID
walks, ES occupancy, pool allocation/close, supplier binding and linked PE
peer/FTTR retirement. ASan/UBSan cover the native fixtures. Final Image/modules
and eight changed source/header identities pass. Artifacts are
`artifacts/fttr/epp-retire-*-20261109.*`; PE firmware is unchanged and no device
was accessed.

AXI investigation found only return-status fields and EPP grant/response
counters in the reference map; no verified original stop-wait sequence uses
them as an end-to-end ownership barrier. They were not guessed into production.
Complete MCE/AFT/AXI quiescence, MiniOLT downstream retirement and safe hardware
ID reuse remain unproven. The new routine covers already-published EPP buffers,
not packets still upstream or an assertion that successful disable is bus-idle.

### Valid inactive-BID credit gates every buffer return

The target `aal_l3qm_get_inactive_bid_cntr` at `0x575c0` returns the low14
count only when bit31 validates it; otherwise it returns zero. The original
RX refill path obtains that credit before checking the separate CPU PUSH
FIFO-ready flag and submitting the address. `trace_stock_qm_return_credit.py`
executes56 validity/count combinations for EQ4..7,8 FIFO-ready states and12
actual PUSH encodings for CPU0/2/3. All match this independent two-condition
contract.

Forced PE owner/queued-BID cleanup and the new unconsumed-EPP cleanup previously
checked only FIFO readiness. They now wait for validated nonzero inactive-BID
credit first, retaining the current list head or pending EPP return on timeout.
CPU startup/refill and normal PE recycling also now reject nonzero low bits
when the valid bit is clear. PE/CPU normal refill continue consuming a bounded
local credit snapshot, refreshing it only when exhausted. Every accepted PUSH
still consumes one credit and preserves the existing post-PUSH ownership order.
No cached allowance survives a later refill call.

The old native forced paths reproduce PUSH while the modeled inactive pool
has no valid slot. Linked old PE likewise returns a BID with a stale nonzero
count and clears its pending reference. Fixed tests verify that ordinary,
source6 FTTR and multicast references remain held until valid credit returns.
Fixture credit values now include the original hardware valid bit explicitly;
no production condition was relaxed to preserve an inaccurate fixture.

Validation includes194 forced EPP cases, aggregate/queued-chain cleanup with
independent invalid-credit/zero-credit/FIFO stalls,729 dual-EQ normal refill
cases plus startup and invalid-bit cases,28 linked peer-withdrawal cases,251
linked FTTR drain cases,125 multicast drain cases and132 PS withdrawal records
consumed by ARM/mac80211. Sixteen current regression suites pass, including
actual RPQ ownership/recycle, BCMC, channel progress, downlink aggregation,
TX publication and full RXQ/pause tests. A non-QoS sequence fixture fails with
both old and new firmware; three other old standalone fixtures use obsolete
layout/setup assumptions. They were not counted as passing and their attempted
fixture changes were removed. The valid-credit edits needed by the exercised
fixtures are retained.

PE now builds to32456 bytes, SHA256
`3766ba833e734982d1aac72afc43010e1e9baaa45ac0ad6257faf3da1ed71bad`,
with unchanged7360/8192 conservative stack use and fixed32768-byte IMEM.
Repeated build output is identical; the non-uplink build is14000 bytes.
Image/modules and all three changed kernel inputs match the embedded PE/header
and current sources. Artifacts are `artifacts/fttr/eq-credit-*-20261109.*`.
No physical device was accessed. This corrects buffer-return admission; it
does not establish full upstream/downstream retirement or hardware-ID reuse.

### NI preparation retains every queue and failed rollback owner

Before binding a radio, the native preparation function previously checked
only COS0 EPP equality and then advanced all eight read pointers to their
producers. That could skip an older packet on another COS or one published
between the check and pointer programming. Preparation now requires the radio's
ES CPU enable to be clear, all ES queues empty and all eight EPPs consumed.
It refuses to replace already retained register mappings. EPP consumers are
copied into PE without writing hardware RD, so a subsequent producer update
remains visible. All eight original base/profile pairs are snapshotted before
any queue setup is marked active, supporting complete rollback after a later
failure.

The unicast startup caller now retains the supplying netdev before preparation,
as the BCMC path already did. Its preparation-failure marker is shared by both
callers. If preparation fails, rollback runs; a rollback error keeps the netdev,
register mappings and cleanup request and does not reopen PE through the old
unconditional call. Background cleanup follows the retained preparation branch
before another startup is permitted. Successful rollback releases the reference
and allows a new attempt. An original preparation error remains visible when
rollback succeeds; a failed rollback is reported when it owns the outstanding
work.

The previous `fc6c4b6` production preparation reproduces acceptance of a busy
nonzero COS/ES path. Its unicast caller also fails the new preparation-plus-
rollback ownership checks. `test_ni_prepare_ownership.py` executes the full
preparation function in96 cases covering both radios, all COS, active ES,
existing mappings, late publication, every snapshot failure and subsequent
attribute/arbitration/event/RPQ failure boundaries. Actual route/cleanup tests
verify failed rollback retains its interface and prevents a second prepare,
then cleanup and successful reinitialization balance references. Eleven related
suites pass, including FE transaction, device ownership, BCMC, authorization,
ES/EPP retirement and linked PE peer/FTTR drain. ASan/UBSan cover the native
fixtures. Final Image/modules and both changed source identities pass. Evidence
is `artifacts/fttr/ni-prepare-*-20261109.*`.

PE firmware is unchanged and no physical device was accessed. This preserves
existing queued data and partial-start ownership; it does not prove all upstream
producers stopped, safe ID reuse, or full physical startup/teardown acceptance.

### Uplink allocation is distinct from a successfully running NI queue

`ni_uplink_start` previously returned success whenever its descriptor memory
existed, including memory retained after a failed queue load or stop. That
allowed a later peer-enable attempt to treat a disabled/uninitialized queue
as usable. The old production functions from `6cd836b` reproduce the false
success after load failure in `test_ni_uplink_pattern.py --revision 6cd836b`.

The driver now tracks `uplink_ready` separately from allocation. Stop clears
readiness before any fallible operation. Start with retained, non-ready memory
must complete the previous stop/rollback before allocating and snapshotting a
new queue. Failed cleanup preserves the original allocation and saved registers
and returns its error. VP disable is read back before clearing the published
mailbox pointer or releasing descriptor memory; VP enable is read back before
declaring readiness. Failed enable enters the same retained cleanup path.
Successfully running starts remain idempotent.

Validation covers12 failures/retries over both VP9/10, including failed initial
load, PE pause, incomplete DMA cursor, rejected disable/enable and stuck base
access. Tests verify retained allocation/snapshot identity, no second allocation
or false success while cleanup fails, eventual restart and exact restoration of
unrelated selector bits. The actual ARM `uplink_counter_start` followed by linked
PE completion executes40 old/new cursor and one/two-tail cases; all514 RX buffers
remain accounted for and held payloads return only after the preserved two-slot
distance. Actual original DMA completion runs4096 cursor/reclaim pairs, retaining
the documented two descriptors and recording its four synthetic short-distance
wrap defects rather than treating those defects as native requirements.

Eleven related suites pass, including NI preparation/restore/retirement,
publication/ownership, BCMC, RX handoff, pause and the full linked RXQ test.
ASan/UBSan cover native start/stop. Final Image/modules build passes and both
changed driver inputs match the compiled tree. PE firmware is unchanged;
artifacts are `artifacts/fttr/uplink-ready-*-20261109.*`. No physical device was
accessed. This fixes retry readiness and checks the existing tail preservation;
it does not establish complete upstream/downstream packet retirement or safe
hardware-ID reuse.

### Rebase pending work-ring completions when restarting uplink DMA

Queue reload must translate all NI completion records into the new cursor
epoch. The previous `uplink_counter_start` translated only the two retained
tail buffers. A paused PE may still own completed payloads in its work ring;
lookahead can also submit NI frames beyond an ARM-blocked process cursor.
Keeping their old end sequences while resetting the read cursor could return
payloads before the original two-descriptor retention interval elapsed.

The paused restart now walks WORK_READ through WORK_WRITE and rebases every
NI-pending record by its signed31-bit distance from the old completion cursor.
It preserves the NI ownership bit, ARM completion and lookup hints, buffer IDs,
ring cursors and entries outside the captured interval. No payload is released
by rebasing; the existing PE completion/recycling path decides when to return it.

`test_ni_uplink_tail_restart.py` executes the native ARM helper followed by the
linked production PE. The old `a694877` helper reproduces an early buffer return
when restarting cursor0 at1. The fix passes84 work-ring cases covering wrap,
ARM blockage, submissions beyond WORK_PROCESS,31/32-bit old cursor boundaries,
and exact delayed return of all514 buffers, plus the previous40 tail cases.
The start/stop ASan/UBSan test and nine related suites pass, including full linked
RXQ/BA/pause and251 FTTR NI/PE drain cases. Image/modules and the changed source
identity pass; artifacts are `artifacts/fttr/uplink-work-*-20261109.*`.

PE firmware is unchanged; no physical device was accessed. This repairs the
native restart bookkeeping while retaining the original prefetch rule. Full
producer/downstream retirement and safe hardware-ID reuse remain unproven.

### Original BSSID and SSID conflict report queries

The independent syncserv client now exposes two original diagnostics:

```sh
fttr-sync fttr-mc 02:00:00:00:00:02 auto 0x88aa bssid-conflicts
fttr-sync fttr-mc 02:00:00:00:00:02 auto 0x88aa ssid-conflicts
```

The retained controller builders at `0x4120f0` and `0x412128` emit only
`RPCMethod:CheckBSSIDConflict` or `CheckSSIDNameConflict`. The executed report
consumer uses **lowercase `mac`** for the reporting device and an `ApInfo` array
with `onuMac`, `onuModel`, `rfband`, `wlanMac`, `ssidIndex`, `ssid`. The target
writes these fields to its per-device diagnostic file and sends an empty JSON
response. Empty, one/two-row, integer-valued field and trailing-field cases
are executed in `trace_stock_fttr_conflicts.py`; JSON/file operations and remote
reports are modeled. The original receiver loses AP rows when `ApInfo` is not
the last top-level field. Native lookup uses the field name and does not copy
that defect or the original lossy comma-separated intermediate file.

The native query binds both Ethernet source and report `mac` to the selected
peer, accepts only the matching method and a complete bounded `ApInfo`, and
responds with the original empty object. A row may identify another ONU's AP;
its addresses are validated but not restricted to the reporting peer. All six
fields must be present; string/integer values are preserved, without assigning
unproven band/index meanings. Embedded NUL, invalid addresses/types and malformed
tail rows fail the entire report. The local64-row bound rejects larger lists
without truncation. Fragment loss, re-registration, NACK, incomplete ACK-only
responses and reply-send failure produce no partial output. Unknown fields are
excluded, and JSON preserves punctuation/newlines in SSIDs.

Output includes `ApInfo`, `report_complete:true`, `scan_verified:false` and
`identity_authenticated:false`. Report completion means the full response
object was received; it does not establish physical scan coverage, diagnose
interference, authorize roaming or authenticate the subordinate. There is no
automatic remediation or periodic conflict policy in this change.

Validation covers37 host and36 packaged-musl CLI/veth scenarios for each method,
including discovery, both message directions, fragmented64-row reports, empty
reports, wrong source/method, lower-case identity, other-ONU rows, numeric values,
SSID punctuation and failure paths. ASan/UBSan exercise27 allocation/copy failures
and every missing/invalid row field. Existing station, neighbor allocation and
resident controller regressions pass. The FTTR and FPGA companion packages
rebuild; all67 source inputs and four stripped/staged programs match. Evidence
is `artifacts/fttr/*conflicts*-20261109.*`. Kernel/PE are unchanged by these
diagnostics, and no physical device was accessed. Full gateway/roaming policy,
subordinate capability validation and packet retirement remain incomplete.

### Preserve completed PE replay decisions during ARM ownership transfer

PE lookahead may discard a duplicate cross-TID retransmission while its
contiguous work cursor waits for an earlier missing sequence. That completed
entry carries exactly `ARM_DONE` (`BIT(30)`). The ARM stop path previously
skipped only NI-owned entries and delivered this already-completed payload
again. It now also skips exact `ARM_DONE`; ordinary lookup hints containing
bit30 still require delivery. The existing recycler returns each completed
buffer once, and the NI end-sequence/two-descriptor holdback is unchanged.

Both the extracted production stop routine and an actual linked PE-to-ARM
handoff reproduce the extra delivery with `f0dcea4`. The latter executes real
lookahead on mixed BA-gap, cross-TID duplicate and NI-submitted frames, then
passes its memory/ownership state to the native stop routine. Six cases cover
work-ring wrap, incomplete NI consumption, failed PE stop and retry. The fixed
path delivers only the two pending frames, retains the final NI payload until
two more completions, and accounts for all514 buffers. Native ASan/UBSan tests
also distinguish exact completion from ordinary bit30 hints. Seven related
suites, Image/modules and the changed source identity pass; evidence is
`artifacts/fttr/rx-handoff-*-20261109.*` and
`artifacts/fttr/rx-linked-handoff-before-20261109.log`.

This repairs native ownership transfer; no new factory replay or RF behavior
is inferred from the modeled ARM delivery callback. PE firmware is unchanged,
no physical device was accessed, and complete upstream/downstream retirement
and safe ID reuse remain open.

### Failed VP closure retains graceful and forced NI ownership

The ordinary test-VP release and forced PCI teardown paths both wrote zero to
VP CONTROL without checking that TXQ0 actually disabled. An ignored write could
therefore be followed by queue-base restoration and release. Both paths now
read back bit0 and return `EIO` while it remains set, before changing the base,
transferring RX ownership or releasing allocations. Failed retries preserve
the original mappings and snapshots. The regular uplink path already performed
this check; these two additional entry points now enforce it as well.

The reference `DMA_SEC_DMA_LSO_VP_CONTROL` identifies bit0 as `txq0_en`, matching
the native queue0 setup. Inspection of the target `ca_ni_dma_lso_reset` in its
actual `.text.unlikely` section shows global DMA/AFT reset toggles followed by
ring reset/reinitialization, not a separately proven downstream-idle handshake.
No global reset or additional idle inference is introduced here.

The old `0f16f7d` release functions reproduce continuing after a rejected disable.
Four both-radio/graceful/forced cases now verify error, unchanged ownership and
no base programming/PE handoff/free across retries, followed by successful
cleanup. All46 restore cases,16 original AXI attribute comparisons and six
related suites pass, including554 ES cases, partial BID returns, NI prepare,
uplink retries and FE transactions. Image/modules and changed source identity
pass. Evidence is `artifacts/fttr/vp-close-*-20261109.*`. PE firmware is unchanged;
no physical device was accessed. Successful enable-bit closure still does not
prove all in-flight bus accesses finished or permit safe hardware-ID reuse.

### Forced NI cleanup must finish a retained uplink stop

An unsuccessful graceful `ni_uplink_stop` retains its coherent descriptor
allocation and VP snapshot. The forced `ni_test_remove` path previously could
continue to restore/unmap NI registers without successfully stopping that
uplink, leaving its allocation and published mailbox reference behind. It now
retries the same uplink stop before any forced base replacement or RX handoff.
Failure returns with all retry resources retained; success uses the existing
ordered stop to withdraw mailbox pointers, restore the VP and free exactly once.
The forced downlink BID/EPP cleanup then proceeds independently.

The lifecycle fixture now executes the production uplink-stop function together
with production release/remove, instead of a success-only stop stub. Six new
both-radio cases cover outstanding DMA consumption, rejected disable and a busy
base transaction across repeated remove attempts, then recovery into forced
downlink cleanup. Old `1beaee0` reproduces bypassing the incomplete stop. All52
restore cases and six related suites pass, including554 ES scenarios and the
linked PE-to-ARM ownership transfer. ASan/UBSan, final Image/modules and both
changed radio source identities pass. Evidence is
`artifacts/fttr/uplink-forced-*-20261109.*`.

No physical device was accessed and PE firmware is unchanged. This establishes
the cleanup dependency and retains the existing two-descriptor prefetch rule;
it does not establish full MCE/AFT/AXI/MiniOLT retirement or safe ID reuse.

### NI setup and restoration verify their ES enable result

The final CPU ES enable in `ni_test_prepare` and the saved CPU enable restoration
in `ni_restore_registers` previously returned success even if the write did not
take effect. Setup now verifies the selected CPU bit is enabled; restoration
verifies it matches the saved state. Both return `EIO` on mismatch. Existing
partial-prepare and failed-cleanup ownership retains mappings, netdev references
and snapshots for retry; no successful FE/BCMC admission or release is reported
from these failed operations. Only the selected CPU bit is compared/restored,
preserving unrelated CPU/LAN/PON state.

The target `aal_l3qm_enable_tx_cpu` executes16 CPU2/3 enable/disable cases with
four initial register patterns; its actual instructions confirm the masked
`0x6108` update. The native fault tests reproduce both false-success cases with
`dd6d738`, then pass98 preparation and60 restoration cases, including both saved
bit values, normal/forced cleanup retries and retained snapshots. Existing554 ES
cases, BCMC partial setup, NI withdrawal and FE transaction regressions pass.
Image/modules and changed source identity pass; artifacts use
`artifacts/fttr/es-enable-*-20261109.*` and
`artifacts/fttr/es-restore-before-20261109.log`.

PE firmware is unchanged and no physical device was accessed. These are checked
control updates, not proof of complete packet retirement or safe ID reuse.

### Relay EEPROM diagnostics do not establish a selected SFP implementation

`trace_stock_fttr_module_optics.py` now executes the retained ASIC and FPGA1
relay module-information APIs and their four calibrated getters. Both always
use physical channel0 and address0x51: TX reads80/82/102, temperature84/86/96,
bias76/78/100 and voltage88/90/98. The first two values are calibration slope
and offset; the third is the sample. These are separate from the four
factory-file-selected `rtkoltbosa` recipes already implemented.

The actual public aggregate API reads all four values into a temporary record
and publishes only after success. Its RX power field is **constant zero**;
it does not call the separate RX polynomial getter. The six threshold/alarm
getters and `drv_gpon_polling_pon_module` return success without reading a sensor
or writing their output. These stubs must not become native zero-valued limits,
an absent-alarm claim, or a module-detection predicate. The converter's apparent
endianness helper is also a no-op in these retained builds. The oracle supplies
host integers at the original `read_word` API boundary and does not infer
EEPROM wire byte order from that helper.

Both binaries pass24 successful calibrated getter comparisons,144 injected
read-failure paths, unchanged-output checks for the no-op functions, and the
aggregate constant-zero RX check. The original temperature conversion truncates
through signed8-bit whole degrees; its result is recorded, not adopted as a
generic sensor rule. `log10`, read-word integers and logging are explicit model
boundaries. Evidence is `artifacts/fttr/module-optics-original-20261109.json`.

This is evidence narrowing an outstanding branch, not new SFP support. No
factory selection/identity predicate connecting these fixed EEPROM addresses
to the supported BOSA startup was established. Automatic probing, threshold
actions and SFP calibration remain unimplemented rather than being inferred
from exported getter names. No hardware was accessed.

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
   assignment are integrated. PLOAM credential acquisition and factory per-ONU
   policy import are implemented as described above; saved user-modified profiles,
   FPGA1 ordinary default grants and broader capability-based decisions remain work. Explicit T-CONT binding/readback/local DBA
   and native GEM CTP/IW lifecycle/readback/cleanup are implemented. Native GAL,
   bridge/initial Mapper and basic Ethernet/VEIP attachment are implemented.
   Mapper binding and ANI/GEM-side bridge ports/VLAN filtering are implemented.
   UNI VLAN operation rules, table readback and exact prior-rule restoration
   are implemented. Explicit fresh-MIB selection, direct-bridge service
   orchestration, shared Ethernet/VEIP attachment and local DBA readback/restoration are implemented. Resident
   provisioning from explicit policies and asynchronous per-ONU dispatch are
   implemented, with runtime journals and observed disconnect/replacement
   handling. MIB sync monitoring now also detects/reconciles same-serial MIB
   loss without an observed registration transition. Durable committed ownership, interrupted release and initial-write checked rollback are implemented as described above; legacy-record recovery and saved policy import remain work; state3 cleanup now rebuilds automatically after fresh authentication. The original
   ASIC/FPGA2 default recipe and card limits now generate shared multi-service
   collections as described above; broader factory
   capability/UNI policy combinations remain work. Explicit bridge attachment through native per-GEM
   VLAN ports, their lifecycle and kernel FDB removal are implemented.
   Native P6 CF/FE admission and synchronous Hash/cache withdrawal are
   implemented. Physical queued-packet retirement and hardware acceptance
   remain work.
   Current upstream ONU OMCI is a different role.
5. Original subordinate gateway configuration, Wi-Fi synchronization,
   multicast, reboot/upgrade and roaming coordination, using the connected
   ONU's actual capabilities rather than assuming one fixed subordinate model.
   The resident ONU-G reboot request is implemented as described above;
   the Software Image download/activation/commit protocol is also implemented.
   Resumable image transfer, image compatibility/boot verification,
   complete gateway/roaming coordination and physical reboot verification remain work.
   Original downstream NTP/time configuration now has explicit and durable resident
   Get/Set/readback transactions as described above; physical clock synchronization
   and automatic local-time policy extraction remain unverified or incomplete.
   Explicit WanIPChange notifications, Radio configuration and EasyMesh
   transactions with independent reported-state readback are implemented.
   QosSync's original opaque transport and durable resident send ownership are
   implemented above; its ACK does not establish scheduler installation.
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
