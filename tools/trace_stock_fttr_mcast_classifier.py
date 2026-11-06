#!/usr/bin/env python3
"""Execute original multicast Hash profiles and L3 classifier key conversion."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import random
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X5

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'


def profile_recipes(fc_path, ni_path):
    events = []
    def ni_boundary(name, args):
        raise AssertionError(('unmodeled original mask call', name, args))
    ni = ModuleOracle(ni_path, ['aal_hash_mask_unmask'], ni_boundary)
    def boundary(name, args):
        if name == 'aal_hash_profile_init':
            events.append(dict(function=name, arguments=args+[fc.uc.reg_read(UC_ARM64_REG_X4)]))
            return 0
        if name == 'aal_hash_mask_unmask':
            ni.call(name, ni.scratch)
            fc.uc.mem_write(args[0], bytes(ni.uc.mem_read(ni.scratch, 32)))
            return 0
        if name == 'aal_hash_profile_tuple_add':
            events.append(dict(function=name, profile=args[0], priority=args[2],
                mask=list(fc.unpack(args[3], '8I'))))
            fc.uc.mem_write(fc.uc.reg_read(UC_ARM64_REG_X4), struct.pack('<I', len(events)))
            fc.uc.mem_write(fc.uc.reg_read(UC_ARM64_REG_X5), b'\0')
            return 0
        if name == 'assert_eq':
            assert args[0] == args[1]
            return 0
        if name == 'sprintf':
            fc.uc.mem_write(args[0], b'log\0')
            return 3
        if name == 'printk':
            return 0
        raise AssertionError(('unmodeled original profile call', name, args))
    fc = ModuleOracle(fc_path, ['rtk_8277c_flow_hash_profile_init'], boundary)
    assert fc.call('rtk_8277c_flow_hash_profile_init', 0x140000, 3, 0, 0) == 0
    assert [x['profile'] for x in events if x['function']=='aal_hash_profile_tuple_add']==[0,1,2,2,3,4]
    multicast=[x for x in events if x.get('profile')==2]
    for item in multicast:
        mask=sum(word<<(32*i) for i,word in enumerate(item['mask']))
        item['destination_prefix']=(mask>>58)&511
        item['source_prefix']=(mask>>67)&511
    assert [(x['destination_prefix'],x['source_prefix']) for x in multicast]==[(128,0),(128,128)]
    return events


def profile_withdrawal(ni_path):
    """Execute original last-tuple removal, including its bounded direct IO."""
    writes=[]
    def boundary(name,args):
        raise AssertionError(('unmodeled tuple withdrawal',name,args))
    ni=ModuleOracle(ni_path,['aal_hash_profile_tuple_del'],boundary)
    base=0x6000000
    ni.uc.mem_map(base,0x10000)
    ni.word(ni.symbols['g_ne_reg_iobase'][0],base)
    ni.uc.hook_add(UC_HOOK_MEM_WRITE,lambda uc,access,address,size,value,data:
        writes.append((address-base,value)),begin=base,end=base+65535)
    cases=[]
    for count in range(1,9):
        writes.clear()
        seed=0x12345670|count
        ni.uc.mem_write(base+0x372c,struct.pack('<I',seed))
        for slot in range(count):
            ni.uc.mem_write(base+0x3730+slot*4,struct.pack('<I',54+slot))
        assert ni.call('aal_hash_profile_tuple_del',1,count-1)==0
        assert writes==[(0x3730+(count-1)*4,0),(0x372c,(seed&~15)|(count-1))]
        for slot in range(count-1):
            assert ni.unpack(base+0x3730+slot*4,'I')[0]==54+slot
        cases.append(dict(count=count,writes=list(writes)))
    return dict(cases=cases,scope='Original removes the last tuple using direct tuple clear and count reduction only. This validates register ownership, not physical cache/in-flight retirement.')


def multicast_mtu_path(ni_path):
    """Check the original PE MTU register setters used by the native owner."""
    def boundary(name,args):
        raise AssertionError(('unmodeled PE MTU call',name,args))
    ni=ModuleOracle(ni_path,['aal_l3pe_mtu_size_set','aal_l3pe_mtu_size_get',
        'aal_l3pe_pe_mtu_failed_ldpid_set','aal_l3pe_pe_mtu_failed_cos_set'],boundary)
    base=0x6000000
    ni.uc.mem_map(base,0x10000)
    ni.word(ni.symbols['g_ne_reg_iobase'][0],base)
    count=0
    for index in range(16):
        for mtu in (68,1280,1400,1500):
            address=base+0x3520+(index//2)*4
            seed=0xabcd4567
            ni.uc.mem_write(address,struct.pack('<I',seed))
            assert ni.call('aal_l3pe_mtu_size_set',index,mtu)==0
            shift=index%2*16
            assert ni.unpack(address,'I')[0]==(seed&~(0x3fff<<shift))|(mtu<<shift)
            assert ni.call('aal_l3pe_mtu_size_get',index,ni.scratch)==0
            assert ni.unpack(ni.scratch,'I')[0]==mtu
            count+=1
    seed=0x8fffffff
    ni.uc.mem_write(base+0x351c,struct.pack('<I',seed))
    assert ni.call('aal_l3pe_pe_mtu_failed_ldpid_set',16)==0
    assert ni.call('aal_l3pe_pe_mtu_failed_cos_set',0)==0
    assert ni.unpack(base+0x351c,'I')[0]==(seed&~((63<<16)|(7<<24)))|16<<16
    return dict(vectors=count,cpu_destination=16,scope='Actual original setters/getters confirm MTU field widths, index mapping and CPU16 failure destination. Physical over-MTU packet contents and multicast suppression remain untested.')


def classifier_recipes(ni_path, reference):
    text = reference.read_text()
    declarations = '#include <stdint.h>\ntypedef uint32_t ca_uint32_t;typedef uint8_t ca_mac_addr_t[6];\n'
    for name in ('cls_ip_addr', 'cls_mdata', 'cls_key', 'cls_action'):
        declarations += re.search(r'typedef struct '+name+r'_s \{.*?\}\s*__attribute__\(\(packed\)\)\s*'+name+r'_t;', text, re.S)[0]+'\n'
    declarations += '#include "rtl9617c-fe-mcast-profile.h"\n'
    declarations += r'''
#include <string.h>
void input(unsigned int slot,unsigned int profile,unsigned char *rule,unsigned char *action) {
 cls_key_t k; cls_action_t a={0};
 memset(&k,255,sizeof(k));
 k.msk_mac_da_mc=0;k.mac_da_mc=1;k.msk_mac_da_bc=0;k.mac_da_bc=0;
 memset(rule,0,8);memcpy(rule+8,&k,sizeof(k));
 a.dpid_vld=1;a.dpid_pri=1;a.permit=1;a.mcgid=16;
 a.stage2_ctrl_vld=1;a.t2_ctrl_vld=1;a.t2_ctrl=profile;
 memcpy(action,&a,sizeof(a));(void)slot;
}
void native(unsigned int profile,unsigned int *key,unsigned int *action) {
 rtl_fe_mc_classifier_key(key);
 rtl_fe_classify_hash(action,profile,0);rtl_fe_bits_set(action,208,1,0);
}
'''
    def boundary(name, args):
        raise AssertionError(('unmodeled original classifier call', name, args))
    ni = ModuleOracle(ni_path, ['__l3_cls_key_entry_update.constprop.0', '__l3_cls_fib_entry_update'], boundary)
    cases = []
    with tempfile.TemporaryDirectory(prefix='fttr-cls-') as directory:
        root = Path(directory)
        (root/'input.c').write_text(declarations)
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC','-I'+str(DRIVERS),str(root/'input.c'),'-o',str(root/'input.so')],check=True)
        lib = ctypes.CDLL(str(root/'input.so'))
        for slot in range(4):
            for profile in (0,1,6):
                rule,action=(ctypes.c_ubyte*512)(),(ctypes.c_ubyte*512)()
                lib.input(slot,profile,rule,action)
                ni.uc.mem_write(ni.scratch,bytes(rule))
                ni.uc.mem_write(ni.scratch+0x1000,bytes(88))
                assert ni.call('__l3_cls_key_entry_update.constprop.0',ni.scratch,0,slot,ni.scratch+0x1000)==0
                ni.uc.mem_write(ni.scratch+0x2000,bytes(action))
                ni.uc.mem_write(ni.scratch+0x3000,bytes(56))
                assert ni.call('__l3_cls_fib_entry_update',ni.scratch+0x2000,0,ni.scratch+0x3000)==0
                key_words=list(ni.unpack(ni.scratch+0x1000,'11I'))
                action_words=list(ni.unpack(ni.scratch+0x3000,'7I'))
                nk,na=(ctypes.c_uint*11)(),(ctypes.c_uint*7)()
                lib.native(profile,nk,na)
                assert action_words==list(na)
                if slot==0:assert key_words==list(nk)
                cases.append(dict(slot=slot,profile=profile,key=key_words,action=action_words))
    return cases


def native_ip_keys(ni_path):
    def boundary(name, args):
        if name=='aal_entry_get_by_idx':
            assert args[0] in (1,7) and args[1] in (54,55)
            ni.uc.mem_write(args[2],bytes(ni.uc.mem_read(ni.scratch+0x1000,32)))
            return 0
        raise AssertionError(('unmodeled original CRC call', name, args))
    ni=ModuleOracle(ni_path,['hash_value_calculate','reverse_n'],boundary)
    code=r'''
#include "rtl9617c-fe-mcast-profile.h"
void key(unsigned int ip6,unsigned int source,unsigned int group,unsigned int learned,unsigned int ingress,unsigned int *words,unsigned int *mask) {
 const unsigned char sa[6]={2,1,2,3,4,5},da[6]={1,0,0x5e,1,2,3};
 const unsigned int ips[4]={0x20010db8,0,0,source},ipd[4]={0xff3e0000,group,0,0x10203};
 const struct rtl_fe_vlan vlan[2]={{.vid=100,.tpid=1},{.vid=200,.tpid=2}};
 rtl_fe_bridge_key(words,sa,da,ip6?0x86dd:0x800,ingress,0,RTL_FE_MC_IP_MASK);
 rtl_fe_mc_ip_key(words,ip6?ips:&source,ip6?ipd:&group,ip6,17);
 rtl_fe_vlan_key(words,vlan,2);
 rtl_fe_bits_set(words,426,4,11);rtl_fe_bits_set(words,445,4,7);
 rtl_fe_key_set(words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_mc_ip_mask(mask);
 if(learned) {
  rtl_fe_mc_learned_mask(mask);
  rtl_fe_key_set(words,RTL_FE_KEY_HKEY_ID,RTL_FE_MC_LEARNED_MASK);
  rtl_fe_key_set(words,RTL_FE_KEY_L4_SP_EXACT_RANGE,1234);
  rtl_fe_key_set(words,RTL_FE_KEY_L4_DP_EXACT_RANGE,5000);
 }
}
void crc(unsigned int *key,unsigned int *c32,unsigned short *c16){rtl_fe_key_crc(key,c32,c16);}
'''
    signatures=[]
    with tempfile.TemporaryDirectory(prefix='fttr-mc-key-') as name:
        root=Path(name);(root/'key.c').write_text(code)
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC','-I'+str(DRIVERS),str(root/'key.c'),'-o',str(root/'key.so')],check=True)
        lib=ctypes.CDLL(str(root/'key.so'))
        rng=random.Random(0x9617c)
        for ingress in (4,6):
            for ip6 in (0,1):
                for case in range(128):
                    learned=case//64
                    source=0x0a000001+case//32
                    group=0xe0010203+((case%32)<<23) if not ip6 else case%32+1
                    words,mask=(ctypes.c_uint*23)(),(ctypes.c_uint*8)()
                    lib.key(ip6,source,group,learned,ingress,words,mask)
                    # Header fields ignored by this mask must not change the key.
                    raw=int.from_bytes(words,'little')
                    for bit,width in ((10,16),(26,16),(43,32),(78,8),(95,8),(103,20),(475,6),(614,64),(706,6)):
                        if learned and bit in (10,26):continue
                        select=((1<<width)-1)<<bit
                        raw=(raw&~select)|(rng.getrandbits(width)<<bit)
                    # Original API selects main-table CRC from the incoming
                    # table/profile before masking both out of the hashed key.
                    raw|=1<<728|1<<724
                    ni.uc.mem_write(ni.scratch,raw.to_bytes(92,'little'))
                    ni.uc.mem_write(ni.scratch+0x1000,bytes(mask))
                    assert ni.call('hash_value_calculate',ni.scratch,ni.scratch+0x1000,ni.scratch+0x2000)==0
                    assert bytes(ni.uc.mem_read(ni.scratch,92))==bytes(words), (ip6,case)
                    c32,c16=ctypes.c_uint(),ctypes.c_ushort()
                    lib.crc(words,ctypes.byref(c32),ctypes.byref(c16))
                    assert (c32.value,c16.value)==ni.unpack(ni.scratch+0x2000,'IH')
                    signatures.append((c32.value,c16.value))
    assert len(set(signatures))==len(signatures)
    return dict(vectors=len(signatures),original_mask_and_crc_match=True,
        fields='full IPv4/IPv6 source and group, protocol, LAN4/P6 ingress, SA/DA, two exact tags, template; random ignored fields')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fc',type=Path,required=True)
    parser.add_argument('--ni',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=dict(profiles=profile_recipes(args.fc,args.ni),profile_withdrawal=profile_withdrawal(args.ni),mtu_path=multicast_mtu_path(args.ni),classifiers=classifier_recipes(args.ni,args.reference),native_ip_keys=native_ip_keys(args.ni),
        sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.fc,args.ni,args.reference)},
        scope='Original ARM64 profile producer, default mask, classifier key/action converters and masked-key/CRC calculation. Profile allocator, table lookup responses and hardware IO are modeled; no physical multicast forwarding.')
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: original multicast profiles,8 tuple withdrawals,64 MTU vectors,12 classifier conversions and256 native source/group-IP masked keys and CRCs')


if __name__=='__main__':main()
