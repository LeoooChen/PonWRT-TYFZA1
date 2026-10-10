import CoreWLAN
import Foundation
let radio = CWWiFiClient.shared().interface()!
print("interface=\(radio.interfaceName ?? "unavailable") power_on=\(radio.powerOn())")
for ssid in CommandLine.arguments.count > 1 ? Array(CommandLine.arguments.dropFirst()) : ["PonWRT-Test-2G", "PonWRT-Test-5G"] {
    do {
        let networks = try radio.scanForNetworks(withName: ssid)
        let matches = networks.filter { $0.ssid == ssid || $0.ssid == nil }
        print("SSID=\(ssid) query_results=\(networks.count) named_matches=\(networks.filter { $0.ssid == ssid }.count)")
        for ap in matches {
            print("SSID_readable=\(ap.ssid != nil) BSSID=\(ap.bssid ?? "unavailable") channel=\(ap.wlanChannel?.channelNumber ?? 0) RSSI=\(ap.rssiValue) noise=\(ap.noiseMeasurement)")
        }
    } catch { print("SSID=\(ssid) scan_error=\(error)") }
}
