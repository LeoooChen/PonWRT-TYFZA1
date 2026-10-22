# A53 package compatibility on TYFZA1

The firmware uses only OpenWrt's signed A53 snapshot userspace feeds: base,
luci, packages, routing, telephony and video. It keeps A55 code generation and
the `aarch64_cortex-a55` primary APK architecture, adding
`aarch64_cortex-a53` as a compatible architecture on first boot and upgrades.
No alternate target's kernel repository or project package repository is added.

This is a default feed selection, not a source whitelist. Cortex-A55 runs A53
userspace binaries directly. APK accepts `aarch64_cortex-a53` packages from
official feeds, third-party feeds and local files, as well as `noarch` packages.
There is no TYFZA1 patch to APK or the LuCI package manager restricting package
origins. A55 remains the compilation target for this board's own code; there is
no need to relabel A55 binaries as A53 to install A53 applications.

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

## Third-party packages

For a signed third-party APK repository, install the publisher's authenticated
APK public key under `/etc/apk/keys/` and put its repository URL in
`/etc/apk/repositories.d/customfeeds.list`. Then use normal `apk update` and
`apk add`. Do not replace the official key or the official feeds.

For a local package whose signing key is already trusted:

```sh
apk add /tmp/example.apk
```

An untrusted signature is rejected by upstream APK too. Adding another copy of
the OpenWrt key cannot authenticate a package signed by someone else. If the
publisher does not supply a verifiable public key, authenticate the specific
download separately and explicitly allow that local package as shown below.
This is a per-command exception, not a persistent change to signature policy.
The firmware uses APK, so use `.apk` releases rather than `.ipk` packages.

## OpenClash on 0.8.0-beta and 0.8.1-beta

OpenClash is not in the configured official feeds. The upstream
[v0.48.002 release](https://github.com/vernesong/OpenClash/releases/tag/v0.48.002)
provides a `noarch` APK. Its signature is not trusted by the official OpenWrt
keyring; the release assets do not include the signing public key. The upstream
[installation instructions](https://github.com/vernesong/OpenClash#readme)
also use `--allow-untrusted` for this package. This is not an A53/A55 mismatch
or a missing official feed key.

Run the following over SSH on an ONU with working Internet access. Install the
official dependencies with signature checking first, then authenticate the
fixed-version OpenClash download against its GitHub release asset SHA-256 and
install only that local package with a one-command trust exception:

```sh
(
    set -e
    apk update
    apk add bash ca-bundle curl dnsmasq-full ip-full luci-compat ruby ruby-yaml \
        unzip kmod-tun kmod-inet-diag kmod-nft-tproxy
    cd /tmp
    curl -fL -o luci-app-openclash-0.48.002.apk \
        https://github.com/vernesong/OpenClash/releases/download/v0.48.002/luci-app-openclash-0.48.002.apk
    echo 'f7ebbd43f9be15c5e6df26e5d0c03d50d0bb8f7fa4cc0d2c2bf048b08c0b155b  luci-app-openclash-0.48.002.apk' | sha256sum -c -
    apk add --no-network --allow-untrusted /tmp/luci-app-openclash-0.48.002.apk
    rm /tmp/luci-app-openclash-0.48.002.apk
)
```

The digest above applies only to that exact release asset. APK automatically
replaces `dnsmasq` with `dnsmasq-full` through its package metadata; do not remove
DNS service first or use `--force-overwrite` / `--force-broken-world`.
No firmware flash, Wi-Fi change or global `apk upgrade` is required.
LuCI's stock file-upload installer does not pass `--allow-untrusted`; use SSH
for this upstream package rather than disabling signature checks globally.

The firmware already includes TUN, nftables TPROXY and INET_DIAG. The OpenClash
APK does not include the Clash/Mihomo executable: select the ARM64 core in
OpenClash and supply your configuration separately. The package metadata alone
declares 28,863,438 installed bytes, before additional dependencies and the core;
actual flash usage depends on filesystem compression. Check `/overlay` capacity
before adding the core and rule databases.

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

The 2026-10-21 OpenClash check verified the fixed asset digest, rejection of its
untrusted signature, official dependency installation and local APK installation
in a copied ARM64 rootfs. Package scripts were disabled for that check. Bash,
curl, Lua, dnsmasq and Ruby YAML ran there; OpenClash service startup, core
operation and traffic forwarding were not validated.
