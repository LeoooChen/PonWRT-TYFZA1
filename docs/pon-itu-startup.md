# ITU startup blocked by EPON identity

2026-10-15

The reported XG-PON snapshot has `service error=1`, synchronized SerDes,
`mac prepared=0 error=0`, unconfigured PUC/PDC, and `activation enabled=0`.
The startup script can fail at the ONU MAC or ITU identity operation before
calling MAC preparation. Reading ACTP as O4 in this condition does not establish
that this startup reached ranging. The snapshot alone cannot identify the exact
failed operation on the reporter's device.

Two startup defects were reproduced:

1. The factory importer required a valid EPON LLID MAC before saving any PON
   identity. A missing EPON table or all-zero EPON MAC discarded a valid GPON SN
   and Registration-ID as well.
2. The service unconditionally wrote the EPON ONU MAC before initializing any
   ITU protocol. An ITU configuration without an EPON identity therefore stopped
   before MAC preparation.

The importer now saves the ITU identity independently of the EPON-specific
fields. EPON import failures still produce their original diagnostic and a
nonzero importer exit. The service requires the LLID MAC for EPON; ITU can start
from SN and its protocol-specific authentication value. A supplied MAC continues
to be applied, and invalid supplied identity still stops startup.

The board's original `ca-ne.ko` dispatches GPON, XG-PON and XGS-PON through
`aal_gpon_init` / `aal_xgpon_init`, separately from EPON initialization. The local
original-code records are `logs/stock-pon-mode-dispatch.json` and
`logs/itu47-stock-initialization-contract.json`. Their modeled dependency calls
are not evidence of successful optical registration. This correction changes
identity import and service ordering; it does not alter the MAC register sequence.

Startup now records the last attempted step in `/var/run/pon-service`, retains
stderr in `/var/run/pon-service-error`, and forwards diagnostics to the system
log and caller. A configuration-load failure in `pon-identity` also emits an
error instead of silently returning 1.

Validation used the actual ucode importer, ARM64/musl `pon-identity`, and BusyBox
startup script in an isolated root. `scripts/test_pon_itu_startup.py` covers the
two bad EPON inputs across the three ITU modes, explicit MAC application,
invalid-SN failure reporting, and the mandatory EPON MAC. Driver interfaces,
`ip` and procd are modeled. The identity tool compiles with
`-Wall -Wextra -Werror`; shell syntax and the focused regression pass.

The connected device is operating on EPON. No live PON configuration or firmware
was changed. The reporter is currently unreachable: real XG-PON O5, OMCI
provisioning and service traffic remain unverified. These defects can explain
the reported initialization failure, but are not confirmed as its unique cause.
