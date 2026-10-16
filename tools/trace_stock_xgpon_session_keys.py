#!/usr/bin/env python3
"""Compare successive XGTC session data keys with the board's original generator."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
source = (drivers / 'rtl9617c-pon-omci.c').read_text()


def function(name):
    start = re.search(r'^(?:static )?(?:int|void|unsigned int) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <openssl/evp.h>
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-omci-format.h"
typedef uint8_t u8;
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-ETIMEDOUT;})
#define lockdep_assert_held(p) ((void)(p))
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
struct crypto_shash {u8 key[16];};
struct crypto_aes_ctx {u8 key[16];};
struct rtl9617c_pon {
 int lock,omci_key_lock,xgpon_activation_error;
 unsigned char *mac;
 bool xgpon_key_ready,omci_master_key_valid,xgpon_omcc_ready;
 struct crypto_shash *omci_kdf,*omci_cmac;
 u8 xgpon_serial[8],xgpon_registration[36],omci_master_key[16],xgpon_tag[8];
 u8 xgpon_kek[16],xgpon_ploam_ik[16],xgpon_encrypted_key[2][16],xgpon_key_name[2][16];
 u8 xgpon_key_valid,xgpon_key_state,xgpon_active_key;
 unsigned int xgpon_omcc_id;
 unsigned long long xgpon_key_reports,ploam_tx,xgpon_key_tk4,xgpon_key_tk5;
};
unsigned int memory[0x10000/4],random_calls;
static struct crypto_shash kdf,cmac;
struct rtl9617c_pon pon={.mac=(void*)memory,.omci_kdf=&kdf,.omci_cmac=&cmac,
 .xgpon_serial={'T','E','S','T',1,2,3,4}};
static unsigned int readl(const void *p) {return *(const unsigned int*)p;}
static void writel(unsigned int value,void *p) {*(unsigned int*)p=value;}
static void rtl_pon_update(void *p,unsigned int mask,unsigned int value) {writel((readl(p)&~mask)|value,p);}
static void memzero_explicit(void *p,unsigned int bytes) {memset(p,0,bytes);}
static void get_random_bytes(void *p,unsigned int bytes) {
 assert(bytes==16);u8 *key=p;
 for(unsigned int i=0;i<bytes;i++)key[i]=17*random_calls+i+1;
 random_calls++;
}
int test_cmac(const u8 *key,const u8 *data,unsigned int bytes,u8 *result) {
 size_t written=0;
 return EVP_Q_mac(NULL,"CMAC",NULL,"AES-128-CBC",NULL,key,16,data,bytes,result,16,&written)
  && written==16?0:-1;
}
int test_aes(const u8 *key,const u8 *data,u8 *result) {
 EVP_CIPHER_CTX *ctx=EVP_CIPHER_CTX_new();int written=0;
 int ok=EVP_EncryptInit_ex(ctx,EVP_aes_128_ecb(),NULL,key,NULL)
  && EVP_CIPHER_CTX_set_padding(ctx,0) && EVP_EncryptUpdate(ctx,result,&written,data,16);
 EVP_CIPHER_CTX_free(ctx);return ok && written==16?0:-1;
}
static int aes_expandkey(struct crypto_aes_ctx *aes,const u8 *key,unsigned int bytes) {
 assert(bytes==16);memcpy(aes->key,key,16);return 0;
}
static void aes_encrypt(const struct crypto_aes_ctx *aes,u8 *out,const u8 *in) {
 assert(!test_aes(aes->key,in,out));
}
static int crypto_shash_setkey(struct crypto_shash *tfm,const u8 *key,unsigned int bytes) {
 assert(bytes==16);memcpy(tfm->key,key,16);return 0;
}
static int rtl_omci_cmac(void *context,const u8 *key,const u8 *data,unsigned int bytes,u8 *out) {
 (void)context;return test_cmac(key,data,bytes,out);
}
static int rtl_pon_xgpon_ack_update(struct rtl9617c_pon *p) {(void)p;return 0;}
'''
wrapper = r'''
int derive(const u8 *tag,const u8 *msk) {
 if(msk) {memcpy(pon.omci_master_key,msk,16);pon.omci_master_key_valid=true;pon.xgpon_key_ready=false;}
 return rtl_pon_xgpon_omci_key(&pon,tag);
}
void init(void) {
 for(unsigned int i=0;i<36;i++)pon.xgpon_registration[i]=i+32;
 memory[0x4e00/4]=0x50;
}
void previous_exchange(void) {
 memset(pon.xgpon_encrypted_key,0xa5,sizeof(pon.xgpon_encrypted_key));
 memset(pon.xgpon_key_name,0x5a,sizeof(pon.xgpon_key_name));
 memset(memory+0x4f00/4,0xa5,32);pon.xgpon_key_valid=3;
}
void key_control(unsigned int flag) {
 memory[0x4afc/4]=0;pon.xgpon_omcc_id=17;
 rtl_pon_xgpon_key_irq(&pon,BIT(7),0x00010021|(flag<<8));
 assert(!pon.xgpon_activation_error);
 assert(pon.xgpon_key_state==(flag?4:2));
}
const u8 *field(unsigned int index) {
 switch(index) {
 case 0:return pon.xgpon_kek;
 case 1:return pon.xgpon_ploam_ik;
 case 2:return cmac.key;
 case 3:return pon.xgpon_encrypted_key[0];
 case 4:return pon.xgpon_encrypted_key[1];
 case 5:return pon.xgpon_key_name[0];
 case 6:return pon.xgpon_key_name[1];
 default:assert(0);return NULL;
 }
}
'''
records = []
with tempfile.TemporaryDirectory(prefix='xgpon-session-keys-') as directory:
    work = Path(directory)
    # The helper is present only after the fix; derive always executes production code.
    helper = function('rtl_xgpon_generate_data_key') if 'static int rtl_xgpon_generate_data_key(' in source else ''
    (work / 'native.c').write_text(fixture + function('key_read') + function('key_write') + helper +
                                  function('rtl_pon_xgpon_omci_key') + function('rtl_pon_xgpon_key_irq') + wrapper)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-shared', '-fPIC',
                    '-I', str(drivers), str(work / 'native.c'), '-lcrypto', '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.derive.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    native.test_cmac.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    native.test_aes.argtypes = [ctypes.c_void_p] * 3
    native.field.argtypes = [ctypes.c_uint]
    native.field.restype = ctypes.c_void_p
    memory = (ctypes.c_uint * (0x10000 // 4)).in_dll(native, 'memory')
    random_calls = ctypes.c_uint.in_dll(native, 'random_calls')
    native.init()
    stock_random = 0

    def callback(name, args):
        global stock_random
        if name in ('ca_printf', 'printk', 'ca_spin_lock', 'ca_spin_unlock'):
            return 0
        if name == 'scfg_read':
            oracle.uc.mem_write(args[3], b'GENERIC\0')
        elif name == 'aal_ploam_rgst_id_cfg_get':
            oracle.uc.mem_write(args[1], bytes(range(32, 68)))
        elif name in ('aal_ploam_vendor_id_cfg_get', 'aal_ploam_vssn_cfg_get'):
            value = 0x54455354 if name == 'aal_ploam_vendor_id_cfg_get' else 0x01020304
            oracle.uc.mem_write(args[1], struct.pack('<I', value))
        elif name == 'random_key_generate':
            oracle.uc.mem_write(args[0], bytes((17 * stock_random + i + 1) & 255 for i in range(16)))
            stock_random += 1
        elif name == 'strcmp':
            left, right = (bytes(oracle.uc.mem_read(p, 8)).split(b'\0')[0] for p in args[:2])
            return int(left > right) - int(left < right)
        elif name in ('aes128_cmac_compute', 'aes128_ecb_encrypt'):
            result = ctypes.create_string_buffer(16)
            key = bytes(oracle.uc.mem_read(args[0], 16))
            data = bytes(oracle.uc.mem_read(args[1], args[2] if name == 'aes128_cmac_compute' else 16))
            if name == 'aes128_cmac_compute':
                assert args[3] == 16
                assert native.test_cmac(key, data, len(data), result) == 0
                destination = oracle.uc.reg_read(UC_ARM64_REG_X4)
            else:
                assert native.test_aes(key, data, result) == 0
                destination = args[2]
            oracle.uc.mem_write(destination, result.raw)
        else:
            raise RuntimeError((name, args))
        return 0

    names = ['xgpn_aes_key_generate', 'xgpn_aes_key_generate_by_msk', 'ca_generate_msk',
             'ca_generate_sk', 'ca_generate_ploam_ik', 'ca_generate_def_kek_and_kek_name',
             'ca_generate_kek_name.part.0', 'aal_xgpon_aes_uc_enc_key_set',
             'ca_generate_new_kek_and_kek_name']
    oracle = ModuleOracle(module, names, callback)
    base = 0x6000000
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)
    oracle.uc.mem_write(oracle.symbols['aal_xgpon_aes_key_hw_mode'][0], struct.pack('<I', 1))
    symbols = ['kek', 'ploam_ik', 'omci_ik', 'g_encryped_key0', 'g_encryped_key1', 'g_kek_name0', 'g_kek_name1']
    for label, tag, master in [('first-tag', b'PONTAG01', None), ('changed-tag', b'PONTAG02', None),
                                ('new-msk', b'PONTAG02', bytes(range(128, 144)))]:
        if label != 'first-tag':
            native.previous_exchange()
        oracle.uc.mem_write(oracle.symbols['g_pon_tag'][0], tag)
        if master:
            oracle.uc.mem_write(oracle.scratch, master)
            ret = oracle.call('xgpn_aes_key_generate_by_msk', 0, oracle.scratch)
        else:
            ret = oracle.call('xgpn_aes_key_generate', 0)
        assert ret == 0
        assert native.derive(tag, master) == 0
        expected = [bytes(oracle.uc.mem_read(oracle.symbols[s][0], 16)) for s in symbols]
        actual = [ctypes.string_at(native.field(i), 16) for i in range(len(symbols))]
        expected_words = bytes(oracle.uc.mem_read(base + 0x4f00, 32))
        actual_words = bytes(memory)[0x4f00:0x4f20]
        records.append(dict(case=label, stock_random_calls=stock_random, native_random_calls=random_calls.value,
                            mismatch=[s for s, e, a in zip(symbols, expected, actual) if e != a],
                            data_key_registers_match=expected_words == actual_words,
                            downstream_valid=memory[0x4124 // 4],
                            stale_wrapped_key_retained=actual[3:5] == [bytes([0xa5]) * 16] * 2,
                            omci_and_ploam_keys_match=actual[:3] == expected[:3]))
    count = random_calls.value
    assert native.derive(b'PONTAG02', None) == 0 and random_calls.value == count
    assert oracle.call('ca_generate_new_kek_and_kek_name', 0) == 0
    native.key_control(0)
    assert ctypes.string_at(native.field(3), 16) == bytes(oracle.uc.mem_read(oracle.symbols['g_encryped_key0'][0], 16))
    assert ctypes.string_at(native.field(5), 16) == bytes(oracle.uc.mem_read(oracle.symbols['g_kek_name0'][0], 16))
    assert memory[0x4124 // 4] == 1 and random_calls.value == count + 1
    new_message = b''.join(v.to_bytes(4, 'big') for v in memory[0x4b00 // 4:0x4b28 // 4])
    assert new_message[8:24] == ctypes.string_at(native.field(3), 16)
    native.key_control(1)
    confirm_message = b''.join(v.to_bytes(4, 'big') for v in memory[0x4b00 // 4:0x4b28 // 4])
    assert confirm_message[8:24] == ctypes.string_at(native.field(5), 16)
    assert memory[0x4600 // 4] & 128 and random_calls.value == count + 1

result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Actual stock key generators and native session-key function. Fixed synthetic RNG, '
                    'generic OLT, synthetic registration/SN/TAG/MSK, real OpenSSL AES/CMAC; '
                    'MMIO, locks and ACK update modeled. No live keys or optical execution.')
(root / 'logs/stock-xgpon-session-keys.json').write_text(json.dumps(result, indent=2) + '\n')
for record in records:
    print(record)
assert all(not r['mismatch'] and r['data_key_registers_match'] and not r['downstream_valid'] for r in records)
print('XGTC first session, changed PON-TAG and MSK: both slots match stock; unchanged TAG retains keys; Key-Control request/confirm uses the current slot')
