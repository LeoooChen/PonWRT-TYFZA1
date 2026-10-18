# Official package compatibility on TYFZA1

The firmware uses only OpenWrt's signed A53 snapshot userspace feeds: base,
luci, packages, routing, telephony and video. It keeps A55 code generation and
the `aarch64_cortex-a55` primary APK architecture, adding
`aarch64_cortex-a53` as a compatible architecture on first boot and upgrades.
No alternate target's kernel repository or project package repository is added.

`openwrt-keyring` supplies the official snapshot signing key, and `ca-bundle`
supplies HTTPS trust. The first-boot migration checks that the signing key is
installed before updating `/etc/apk/arch`. This happens after APK's rootfs
assembly transaction, which otherwise writes only the primary architecture.

## Preinstalled dependency selection

`port/configs/tyfza1-compat.config` is the authoritative selection. The sync
script applies it to retained and custom configurations, and the build script
checks every selection after `defconfig`.

| Use | Dependencies included in the firmware |
| --- | --- |
| General C/C++ applications | libatomic, libstdcpp, libpthread, librt, existing libc/libgcc |
| WireGuard and OpenVPN | TUN, WireGuard, OpenVPN DCO/backports; selected crypto and UDP tunnel dependencies |
| SQM | IFB, scheduler core, CAKE, connmark and ctinfo |
| Traffic accounting and diagnostics | conntrack netlink, INET_DIAG |
| Proxy and policy routing | nftables socket, TPROXY, compat and queue; ipset and matching iptables extensions |
| Multi-WAN | IPv4/IPv6 iptables using the nft backend, conntrack-extra, ipopt and NAT modules |

This selection follows actual official package dependencies checked on
2026-10-18. For example, `sqm-scripts` requires IFB and CAKE,
`wireguard-tools` requires its kernel module, `openvpn-openssl` requires both
TUN and `kmod-ovpn-backports`, and `nlbwmon` requires conntrack netlink.
`mwan3` also requires userspace iptables commands and extensions, so installing
kernel modules alone is insufficient.

The toolchain packages require particular attention. They are not in the
official generic userspace feeds. `libiperf3` requires `libatomic1`, while btop
and ICU require `libstdcpp6`. SmartDNS, collectd and several other libraries
declare `libpthread`, and libuuid declares `librt`. On musl, the latter two
package records are needed by APK even though their functions live in libc.
These libraries and package records are therefore included in the image.

## Installation

After flashing a firmware built with these changes:

```sh
apk update
apk add htop nano iperf3
apk add luci-app-sqm luci-proto-wireguard luci-app-nlbwmon
```

Application installation uses normal dependency resolution and signature
verification. Additional kernel modules must be built for the same TYFZA1
kernel ABI and included in the firmware. A plugin requiring a different
hardware driver or an unselected kernel subsystem may still need a new image.
Future snapshot packages can also introduce new runtime requirements. Firmware
upgrades use this project's sysupgrade images.

## Verification scope

The checks execute the production feed-generation macro, configuration sync
and first-boot migration. Official indexes and application packages are
downloaded over HTTPS and verified using the official key. Native ARM64 APK
and binaries are exercised in a copied root filesystem in the Ubuntu VM;
this validates userspace package handling, not physical VPN, SQM or forwarding
performance on the ONU.

The 2026-10-18 full build passed with 83 preinstalled kmod packages (170 module
files). The generated rootfs is 8,067,088 bytes, below the 33,648,640-byte
limit. A fresh copy of that rootfs passed first-boot architecture setup,
official index refresh, dependency resolution for 21 representative packages,
and actual installation of htop, nano, iperf3, curl, btop and luci-app-commands.
The five command-line programs ran successfully, curl accessed the official
HTTPS server, and removing trust in the official key correctly rejected an
index signature. All installed kmod package dependencies matched the image's
kernel ABI. Evidence is recorded in
`manifests/apk-official-compatibility-20261018.json`.
