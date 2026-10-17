# PMF, 2.4 GHz bandwidth and factory identities

Reviewed on 2026-10-17 after the original implementation task stopped.
These changes address the reported missing WPA3/PMF capabilities, missing
2.4 GHz AX/40 MHz choices, and unstable LAN/Wi-Fi identities.

- Advertise `MFP_CAPABLE`. CCMP data continues to use the existing hardware
  path; protected management TX uses the existing `SW_MGMT_TX` path and BIP
  uses mac80211 software crypto. This preserves the PMF requirement.
- Connect XB HT/HE40 requests to the original MAC TXSC, baseband, RF18 and
  IQK bandwidth fields. Publish the matching HT/HE channel-width capabilities.
  Regulatory limits and 20/40 coexistence remain active.
- Restore LAN from `ELAN_MAC_ADDR`, 2.4 GHz from ELAN+1 and 5 GHz from ELAN+2,
  matching the original `setup_mac_addr` contract. Full-address addition handles
  carry. Remove the extra 5 GHz locally administered bit and byte offset.
- Set physical LAN identities before netifd starts. Seed the bridge default on
  new installs and migrate preserved configurations while retaining explicit
  user MAC overrides on the interface or bridge device.

Review validation reused the original implementation evidence in
`artifacts/user-reports-20261017/` and reran the actual ucode factory importer,
boot service boundary checks, real UCI MAC migration, and native-versus-original
XB baseband sequences for channel6 HT20 and HT40 above/below. The importer test
now explicitly models `ip link` and asserts the five requested MAC assignments,
so the isolated chroot never attempts to configure the host network.

The existing ARM64 module build covers these radio sources. The combined board
implementation also compiles their shared LED callback changes. No RF/SAE/PMF
exchange, HE40 over-air test, reboot, flash, or full firmware build was performed.
The code fixes have offline evidence; they do not establish hardware acceptance
or resolve the separate 530 Mbps throughput investigation.

The uncommitted patch210 LED initializer was reviewed against the original
RTL8221B module and consolidated into NI port4 initialization by the board
compatibility change. Its duplicate generic-PHY patch is not needed.
