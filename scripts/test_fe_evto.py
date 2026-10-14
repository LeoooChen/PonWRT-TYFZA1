#!/usr/bin/env python3
"""Feed the OLT snapshot through production FE conversion and bridge hooks."""
from pathlib import Path
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture = (root / 'scripts/test_bridge_learning.py').read_text().split('functions =')[0]
exec(compile(fixture, str(root / 'scripts/test_bridge_learning.py'), 'exec'))

wire = (root / 'artifacts/goal-evto/veip-ani.bin').read_bytes()
count, profiles = struct.unpack_from('<HH', wire, 2)
lines = [f'static struct rtl_itu_paths *config(void) {{ struct rtl_itu_paths *p=calloc(1,sizeof(*p)+{profiles}*sizeof(p->profile[0]));',
         f'assert(p); p->count={count}; p->profile_count={profiles}; p->generation=77;']
for n in range(count):
    fields = struct.unpack_from('<HHHBBBBHB5HBBB24s', wire, 12 + n * 50)
    alloc, gem, vid, bits, mc, queue, aes, tcont, down = fields[:9]
    ids = fields[9:14]
    exclude, mode, priority, dscp_map = fields[14:]
    lines.append(f'p->path[{n}]=(struct rtl_itu_path){{.alloc_id={alloc},.gem_id={gem},.vlan={vid},.pbits={bits},.multicast={mc},.queue={queue},.encrypted={aes},.tcont={tcont},.downstream_pbits={down},.index={8+n},.vlan_profile={{{",".join(map(str,ids))}}},.vlan_exclude={exclude},.unmarked_mode={mode},.default_priority={priority},.mapper_dscp={{{",".join(map(str,dscp_map))}}}}};')
cursor = 12 + count * 50
for n in range(profiles):
    entity, input_tpid, output_tpid, mode, rows = struct.unpack_from('<HHHBB', wire, cursor)
    dscp = ','.join(map(str, wire[cursor+8:cursor+32]))
    row_data = ','.join('{' + ','.join(map(str, wire[at:at+16])) + '}'
                        for at in range(cursor+32, cursor+32+rows*16, 16))
    lines.append(f'p->profile[{n}]=(struct rtl_evto_profile){{.entity={entity},.input_tpid={input_tpid},.output_tpid={output_tpid},.downstream={mode},.count={rows},.dscp={{{dscp}}},.row={{{row_data}}}}};')
    cursor += 32 + rows * 16
assert cursor == len(wire)
lines.append('return p;}')
test = r'''
int main(void) {
 struct net net={};
 struct net_device br={.ifindex=10,.bridge=true,.protocol=0x8100,.net=&net};
 struct net_device lan={.ifindex=1,.running=true,.carrier=true,.state=3,.master=&br,.net=&net};
 struct net_device pon={.ifindex=7,.running=true,.carrier=true,.state=3,.master=&br,.net=&net};
 devices[1]=&lan;devices[7]=&pon;devices[10]=&br;
 struct rtl_fe_port lp={.netdev=&lan,.source=0,.destination=0},pp={.netdev=&pon,.source=7,.destination=32};
 struct rtl_fe fe={.ports={&lp,&pp},.itu_netdev=&pon,.itu_paths=config(),.ppp_protocol_ids={4,9},.enabled=true};
 lp.fe=pp.fe=&fe;init_list(&fe.flows);
 struct rtl_fe_rule routed={.input=&lp,.output=&pp,.key={.protocol=17}};
 assert(!fe_itu_rule(&routed));
 assert(routed.action.destination==33 && routed.action.gem_index==8 && routed.action.gem_set);
 assert(routed.action.vlan_set && routed.action.vlan_count==1 && routed.action.vlan[0].vid==200 &&
        routed.action.vlan[0].priority==5 && routed.action.vlan[0].tpid==1);
 unsigned int words[8];rtl_fe_ipv4_action(words,&routed.action);
 assert((words[0]>>8&1023)==33 && (words[4]>>19&4095)==200 && (words[5]>>2&3)==1 && (words[5]>>7&255)==8);
 /* A wildcard PCP TC rule cannot collapse both priority5 and other frames:
  * their inverse operations produce different logical VLAN keys. */
 routed=(struct rtl_fe_rule){.input=&pp,.output=&lp,.key={.protocol=17}};
 assert(fe_itu_rule(&routed)==-EOPNOTSUPP);
 struct rtl_fe_bridge bridge={.fe=&fe};
 struct sk_buff skb={.len=60};
 memcpy(skb.frame,"\x02\0\0\0\0\x02\x02\0\0\0\0\x01\x88\x64\x11\0\x12\x34\0\x26\0\x21",22);
 struct nf_hook_state wire={.net=&net,.in=&lan},input=wire,output={.net=&net,.out=&pon};
 fdb_source=&lan;fdb_destination=&pon;
 fe_bridge_wire(&bridge,&skb,&wire);fe_bridge_capture(&bridge,&skb,&input);
 fe_bridge_forwarded(&bridge,&skb,&output);fe_bridge_work(&bridge.work.work);
 assert(installed==1 && last.action.gem_index==8 && last.action.destination==33);
 assert(last.action.vlan_count==1 && last.action.vlan[0].vid==200 && last.action.vlan[0].priority==5);
 /* PON driver delivered an untagged logical frame, with original wire metadata. */
 skb.extension=true;skb.saved=(struct rtl9617c_bridge_frame){.pon_generation=77,.pon_gem=8,.tags=1,
  .tag={{5<<13|200,0x8100}}};
 wire.in=input.in=&pon;output.out=&lan;fdb_source=&pon;fdb_destination=&lan;
 fe_bridge_wire(&bridge,&skb,&wire);
 assert(skb.saved.tags==1 && skb.saved.tag[0].tci==(5<<13|200) && skb.saved.pon_gem==8);
 fe_bridge_capture(&bridge,&skb,&input);
 fe_bridge_forwarded(&bridge,&skb,&output);fe_bridge_work(&bridge.work.work);
 assert(installed==2 && last.key.llid==8 && last.vlan_count==1 && last.ingress_tci[0]==(5<<13|200));
 assert(last.action.vlan_set && last.action.vlan_count==0 && last.action.destination==0);
 skb.saved.pon_generation=76;
 fe_bridge_forwarded(&bridge,&skb,&output);fe_bridge_work(&bridge.work.work);
 assert(installed==2); /* Queued old configuration must not seed a new cache. */
 lan.state=0;fe_bridge_work(&bridge.work.work);
 assert(deleted==2 && !net.references);free(fe.itu_paths);
 puts("EVTO FE: routed upstream editor words, both learned bridge directions, original wire GEM/TCI and configuration generation passed");
}
'''
functions = ''.join(function(name) for name in ['fe_bridge_physical','fe_bridge_frame',
    'fe_bridge_wire','fe_bridge_capture','fe_bridge_forwarded','fe_bridge_ports','fe_bridge_learn','fe_bridge_work'])
with tempfile.TemporaryDirectory(prefix='fe-evto-') as temp:
    work = Path(temp)
    (work / 'test.c').write_text(common + structs + backend +
        (drivers / 'rtl9617c-fe-vlan.h').read_text() + '\n'.join(lines) + functions + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
        '-fsanitize=address,undefined', '-I' + str(drivers),
        '-I' + str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/include'),
        str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
