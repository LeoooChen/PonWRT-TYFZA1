#!/usr/bin/env python3
"""Check native EPON crypto profiles/keys against actual stock DAL/AAL code."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X5

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--modules", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
ne = args.modules / "ca-ne.ko"
dal_path = args.modules / "ca-rtk.ko"
wrapper = r'''
#include <errno.h>
#include "rtl9617c-epon-crypto.h"
static unsigned int regs[0xc000/4];
static int fail;
static unsigned int rd(void *p, unsigned int r) { (void)p; return regs[r/4]; }
static void wr(void *p, unsigned int r, unsigned int v) { (void)p; regs[r/4]=v; }
static int indirect(void *p, unsigned int r, unsigned int v)
{
    (void)p;
    if (fail) return -ETIMEDOUT;
    regs[r/4] = v & ~(r==0x1438 || r==0x1578 ? 1U : 1U<<31);
    return 0;
}
static const struct rtl_dq_io io={0,rd,wr,indirect};
void profile(unsigned int mode)
{
    for (unsigned int i=0; i<sizeof(regs)/sizeof(regs[0]); i++) regs[i]=0xa5a5a5a5;
    fail=0;
    rtl_epon_crypto_profile(&io, mode);
}
int key(unsigned int mode, unsigned int slot, const unsigned char *data)
{ return rtl_epon_crypto_key(&io,mode,0,slot,data); }
unsigned int reg(unsigned int r) { return rd(0,r); }
int forget(unsigned int channel)
{ return rtl_epon_crypto_llid(&io,RTL_PON_EPON_1G,channel,0,0); }
int failure(void)
{ fail=1; return rtl_epon_crypto_llid(&io,RTL_PON_EPON_D10G,0,1,1); }
'''


def make_aal(names, handler):
    oracle = ModuleOracle(ne, names, handler)
    oracle.uc.mem_map(0x6000000, 0xc000)
    oracle.uc.mem_write(0x6000000, b"\xa5" * 0xc000)
    oracle.word(oracle.symbols["ca_venus_pon_reg_iobase"][0], 0x6000000)

    def table_completion(uc, access, address, size, value, context):
        if address in (0x6001438, 0x6001578):
            command = struct.unpack("<I", uc.mem_read(address, 4))[0]
            uc.mem_write(address, struct.pack("<I", command & ~1))
    oracle.uc.hook_add(UC_HOOK_MEM_READ, table_completion)
    return oracle


def quiet(name, values):
    if name in ("ca_printf", "printk"):
        return 0
    raise RuntimeError((name, values))


records = []
with tempfile.TemporaryDirectory(prefix="epon-crypto-", dir=root / "artifacts") as temporary:
    temporary = Path(temporary)
    (temporary / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + str(source),
                    str(temporary / "native.c"), "-o", str(temporary / "native.so")], check=True)
    native = ctypes.CDLL(str(temporary / "native.so"))
    native.reg.restype = ctypes.c_uint
    native.key.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]
    for mode in range(3):
        native.profile(mode)
        cfg = struct.pack("<6I", 0 if mode == 0 else 2, 0, 0 if mode == 0 else 2, 0, 0, 0)
        cfg_name = "aal_epon_crypto_1g_cfg_set" if mode == 0 else "aal_epon_crypto_xg_cfg_set"
        aal = make_aal([cfg_name], quiet)
        aal.uc.mem_write(aal.scratch, cfg)
        assert aal.call(cfg_name, 0, 0, 0xd, aal.scratch) == 0
        registers = (0x8b4, 0x93c, 0x8a8, 0x940) if mode == 0 else (0x20, 0x1250, 0x1600, 0x14c0)
        assert all(native.reg(r) == aal.unpack(0x6000000 + r, "I")[0] for r in registers)
        records.append({"mode": mode, "profile_matches_stock": True})
        for slot in (0, 1):
            captured = []

            def dal_callback(name, values):
                if name == "aal_pon_mac_mode_get":
                    return mode
                if name in ("ca_printf", "printk", "ca_spin_lock", "ca_spin_unlock"):
                    return 0
                if name.endswith("_cfg_get"):
                    dal.uc.mem_write(values[2], bytes(24))
                elif name in ("aal_epon_crypto_1g_key_set", "aal_epon_crypto_xg_sa_tbl_set"):
                    fifth = dal.uc.reg_read(UC_ARM64_REG_X4)
                    sixth = dal.uc.reg_read(UC_ARM64_REG_X5)
                    captured.append((name, values[:4], fifth, bytes(dal.uc.mem_read(sixth, 16 if mode == 0 else 80))))
                elif name not in ("aal_epon_crypto_1g_enable_set", "aal_epon_crypto_xg_enable_set",
                                  "dal_rt_rtl8277c_epon_mpcp_gate_timer_set"):
                    raise RuntimeError((name, values))
                return 0

            dal = ModuleOracle(dal_path, ["dal_rt_rtl8277c_epon_churningKey_set"], dal_callback,
                external_symbols={"ca_rtk_debug": 0x5007000, "g_pon_spin_lock": 0x5007100, "hal_ctrl": 0x5007200})
            dal.word(0x5007218, dal.scratch + 0x2000)
            dal.word(dal.scratch + 0x2018, dal.scratch + 0x3000)
            dal.uc.mem_write(dal.scratch + 0x3158, struct.pack("<H", 7))
            dal.uc.mem_write(dal.symbols["epon_init"][0], struct.pack("<I", 1))
            key = bytes(range(1, 10))
            dal.uc.mem_write(dal.scratch, bytes([0, slot]) + key)
            assert dal.call("dal_rt_rtl8277c_epon_churningKey_set", dal.scratch) == 0
            native.profile(mode)
            assert native.key(mode, slot, key) == 0
            if mode:
                def tx_callback(name, values):
                    if name == "aal_epon_crypto_macsec_sectag_type_get":
                        tx.uc.mem_write(values[3], b"\0")
                        return 0
                    return quiet(name, values)
                tx = make_aal(["aal_epon_crypto_macsec_enable_set"], tx_callback)
                assert tx.call("aal_epon_crypto_macsec_enable_set", 0, 0, 0, 0) == 0
                assert all(native.reg(reg) == tx.unpack(0x6000000 + reg, "I")[0]
                           for reg in (0x1010, 0x8054, 0x8050))
            for name, first, fifth, packed in captured:
                aal = make_aal([name], quiet)
                # The 1G key path selects the initialized churning algorithm.
                if mode == 0:
                    aal.uc.mem_write(0x60008b4, struct.pack("<I", native.reg(0x8b4)))
                aal.uc.mem_write(aal.scratch, packed)
                assert aal.call(name, *first, fifth, aal.scratch) == 0
                base = 0x8b8 + slot * 16 if mode == 0 else (0x1588 if first[3] else 0x1448) + slot * 32
                registers = [base + i * 4 for i in range(4)] if mode == 0 else [base - i * 4 for i in range(4)]
                assert all(native.reg(r) == aal.unpack(0x6000000 + r, "I")[0] for r in registers)
            records.append({"mode": mode, "slot": slot, "key_matches_stock_dal_and_aal": True})
    # Original channels4..7 retain bit19 on disable. Verify the native root fix.
    name = "aal_epon_crypto_1g_llid_set.part.0"
    for channel in (0, 4, 7):
        aal = make_aal([name], quiet)
        aal.uc.mem_write(aal.scratch, bytes(8))
        assert aal.call(name, channel, aal.scratch) == 0
        old = aal.unpack(0x600095c + channel * 4, "I")[0]
        assert bool(old & (1 << 19)) == (channel >= 4)
        native.profile(0)
        assert native.forget(channel) == 0 and native.reg(0x95c + channel * 4) == 0
    assert native.failure() == -110
    records.append({"original_disabled_entry_valid_bug": "channels4..7", "native_entries_invalid": True,
                    "native_indirect_timeout_propagates": True})
args.output.write_text(json.dumps({"stock_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ne, dal_path)},
    "scope": "Actual stock key packing/AAL register writes and native production helpers; API config/registration dependencies and table completion modeled; no optical traffic",
    "records": records}, indent=2) + "\n")
print("EPON crypto: three profiles and both key slots match original DAL/AAL; disabled-entry bug fixed; timeout propagates")
