# TYFZA1 panel LED status

Checked 2026-10-18 against original firmware, not a guessed GPIO scan.
All 12 four-GbE and 11 2.5G GPIO LED definitions match their selected original
FDT, including polarities. Both light buttons and the power/WPS aliases match.

| Function | Four-GbE GPIO | 2.5G GPIO | Native behavior / evidence |
| --- | --- | --- | --- |
| Power | 11 high | 70 low | Boot/failsafe/running/upgrade aliases; default on |
| Internet | 2 low | 71 low | Follows netifd `wan` up/down; not a reachability test |
| PON | 9 low | 9 low | Original spec-2 registration/authentication policy |
| LOS | 10 low | 10 low | Independent 120 ms loss-of-signal phases |
| EPHY0 | 4 low / lan1 | 11 high / lan5 | Corrected board-specific carrier/activity binding |
| EPHY1 | 3 low / lan2 | 12 high / lan3 | Corrected board-specific carrier/activity binding |
| EPHY2 | 1 low / lan3 | 3 low / lan2 | Corrected board-specific carrier/activity binding |
| EPHY3 | 0 low / lan4 | 4 low / lan1 | Corrected board-specific carrier/activity binding |
| USB socket | 7 high (USB0) | 2 low (USB1) | Presence plus original 50 ms URB-activity phases |
| Spare USB1 | 6 low | Not a second socket | No fabricated second-socket activity |
| Phone / FXS | 12 high | 139 low | Output exists; native telephony state integration is NOT implemented |
| WPS | 5 high | 140 high | Output/alias exists; automatic WPS event integration is NOT implemented |
| 2.4/5 GHz Wi-Fi | Radio LED0/GPIO8 | Radio LED0/GPIO8 | Original radio register vectors; native AP link/RX/TX trigger |
| RTL8221B LED0 | Not used | PHY, not SoC GPIO | Original automatic/off/on modes through MMD31 |

There is no independently named IPTV LED in either original FDT. IPTV's
physical Ethernet indicator must follow the actual jack's link/traffic, not
the PON or Internet LED. The software four-port mapping is established, but a
panel-label photograph or physical observation is still needed to assign the
user's printed IPTV legend to an EPHY output. No such assignment is guessed.

Focused checks cover both compiled LED DTs, both board defaults and retained
configuration migrations, the global LED override, original radio register
operations, PON/authentication patterns, PHY LED modes and original USB timer
phases. Evidence is in `artifacts/led-port-map-20261018/` and described in
`docs/leds-native.md`. No full test suite was run.

These checks are not physical acceptance. No corrected full image has been
built/flashed or tested for all jack insertions, USB2/USB3 storage activity,
optical registration, Wi-Fi traffic and front-panel switch operation on both
boards. The GPIO getter still reads the input register even for output pins;
its debugfs hi/lo is not proof of an LED output latch or voltage. This capture
does not silently turn that diagnostic mismatch into a claimed lighting fix.
Phone and WPS service-state gaps must not be presented as completed features.
