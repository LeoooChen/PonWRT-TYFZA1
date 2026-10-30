#!/usr/bin/env python3
"""Check read-only CTC port state and retained local control in a network namespace."""
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = root / "port/packages/tyfza1-pon-control/src"
wrapper = r'''
#include "pon-backend.h"
int native(const char *device, unsigned char branch, unsigned short leaf,
           unsigned char port, const unsigned char *value, unsigned int size,
           unsigned char *output)
{
    struct pon_backend b = {};
    struct pon_oam_identity id = {};
    unsigned char object[] = {0x37,0,1,4,1,0,0,port};
    struct pon_oam_request r = {.version=0x30, .branch=branch, .leaf=leaf,
        .object=object, .object_size=sizeof(object)};
    if (pon_backend_init(&b, device)) return -1;
    pon_backend_bind(&id, &b);
    return value ? id.set_parameter(&b, &r, value, size) : id.get_parameter(&b, &r, output, 1514);
}
'''


def link(name):
    return json.loads(subprocess.check_output(["ip", "-j", "link", "show", name]))[0]


with tempfile.TemporaryDirectory(prefix="epon-native-", dir=root / "artifacts") as directory:
    directory = Path(directory)
    (directory / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + str(source),
        str(directory / "native.c"), str(source / "pon-backend.c"),
        "-o", str(directory / "native.so")], check=True)
    library = ctypes.CDLL(str(directory / "native.so"))
    native = library.native
    native.argtypes = [ctypes.c_char_p, ctypes.c_ubyte, ctypes.c_ushort, ctypes.c_ubyte,
                       ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p]
    device = str(directory / "mpcp").encode()
    output = ctypes.create_string_buffer(1514)
    for port in (0, 3):
        subprocess.run(["ip", "link", "add", f"lan{port + 1}", "type", "veth", "peer", "name", f"peer{port}"], check=True)
        subprocess.run(["ip", "link", "set", f"lan{port + 1}", "up"], check=True)
        subprocess.run(["ip", "link", "set", f"peer{port}", "up"], check=True)
    for selector, interface in ((1, "lan1"), (4, "lan4")):
        assert native(device, 7, 0x25, selector, None, 0, output) == 4
        assert output.raw[:4] == struct.pack("!I", 2)
        assert native(device, 9, 5, selector, struct.pack("!I", 1), 4, output) == 0x87
        assert "UP" in link(interface)["flags"]
        subprocess.run(["ip", "link", "set", interface, "down"], check=True)
        assert "UP" not in link(interface)["flags"]
        assert native(device, 7, 0x25, selector, None, 0, output) == 4
        assert output.raw[:4] == struct.pack("!I", 1)
        assert native(device, 9, 5, selector, struct.pack("!I", 2), 4, output) == 0x87
        assert "UP" not in link(interface)["flags"]
        subprocess.run(["ip", "link", "set", interface, "up"], check=True)
        assert "UP" in link(interface)["flags"]
    # Remote autonegotiation and Pause writes are not part of this OpenWrt scope.
    assert native(device, 9, 0xc, 1, struct.pack("!I", 2), 4, output) == 0x87
    assert native(device, 9, 0xb, 1, b'', 0, output) == 0x87
    assert native(device, 0xc7, 0x12, 1, b'\1', 1, output) == 0x87
    assert native(device, 9, 5, 5, struct.pack("!I", 1), 4, output) == 0x87
    assert "UP" in link("lan1")["flags"] and "UP" in link("lan4")["flags"]
    (directory / "transceiver").write_text(
        "temperature_raw=-256 voltage_raw=33000 bias_raw=1234 tx_power_raw=5678 rx_power_raw=65535 control=00/00/00/00\n")
    assert native(device, 0xc7, 5, 1, None, 0, output) == 10
    assert output.raw[:10] == struct.pack("!5H", 0xff00, 33000, 1234, 5678, 65535)
    (directory / "transceiver").unlink()
    assert native(device, 0xc7, 5, 1, None, 0, output) < 0
print("Native CTC: port1/4 reads target lan1/lan4; OLT writes are refused; local link control and optical raw readings remain functional")
