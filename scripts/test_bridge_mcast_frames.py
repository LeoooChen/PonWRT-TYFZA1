#!/usr/bin/env python3
"""Exercise the production bridge parser using actual IPv4/IPv6/VLAN bytes."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DRIVERS=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
scope={'__file__':str(ROOT/'scripts/test_bridge_learning.py')}
setup=(ROOT/'scripts/test_bridge_learning.py').read_text().split("structs =",1)[0]
exec(compile(setup,'bridge-parser-fixture','exec'),scope)
code=scope['common'].replace('offset+14+length<=s->len','offset+length<=s->len')
code+=scope['function']('fe_bridge_frame')
code+=r'''
static void be16(u8 *p,unsigned v){p[0]=v>>8;p[1]=v;}
static unsigned vectors;
static void check(struct sk_buff *s,int expected,unsigned tags){
 struct rtl9617c_bridge_frame f={};int result=fe_bridge_frame(s,&f);vectors++;
 assert(result==expected);
 if(!result){assert(f.tags==tags&&f.ip_protocol==17&&f.source_port==1234&&f.destination_port==5678);}
}
int main(void){
 for(unsigned ipv6=0;ipv6<2;ipv6++)for(unsigned tags=0;tags<3;tags++)for(unsigned metadata=0;metadata<=!!tags;metadata++) {
  struct sk_buff s={};unsigned wire=tags-metadata,offset=14+4*wire,iplen=ipv6?40:20;
  memcpy(s.frame,"\1\0\x5e\1\2\3\2\0\0\0\0\1",12);
  be16(s.frame+12,wire?0x8100:ipv6?0x86dd:0x800);
  s.tagged=metadata;s.vlan_proto=htons(0x8100);s.vlan=0xb123;
  for(unsigned tag=0;tag<wire;tag++){be16(s.frame+14+4*tag,0xa123+tag);be16(s.frame+16+4*tag,tag+1<wire?0x8100:ipv6?0x86dd:0x800);}
  u8 *ip=s.frame+offset,*udp=ip+iplen;
  ip[0]=ipv6?0x60:0x45;
  if(ipv6){be16(ip+4,12);ip[6]=17;ip[8]=0x20;ip[24]=0xff;}
  else{be16(ip+2,32);ip[9]=17;ip[12]=10;ip[16]=239;}
  be16(udp,1234);be16(udp+2,5678);be16(udp+4,12);s.len=offset-14+iplen+12;
  check(&s,0,tags);
  struct rtl9617c_bridge_frame f={};assert(!fe_bridge_frame(&s,&f));
  assert(f.protocol==(ipv6?0x86dd:0x800)&&f.ip_source[0]==(ipv6?0x20000000:0x0a000000));
  if(metadata)assert(f.tag[0].tci==0xb123);
  struct sk_buff valid=s;
  memset(s.frame,255,6);check(&s,0,tags);s=valid;
  /* MC-to-UC output requests full datagram validation after DA replacement. */
  s.frame[0]=2;struct rtl9617c_bridge_frame unicast={.ip_protocol=17};vectors++;
  assert(!fe_bridge_frame(&s,&unicast)&&unicast.destination[0]==2&&unicast.source_port==1234&&unicast.destination_port==5678);
  ip[ipv6?6:9]=6;unicast=(struct rtl9617c_bridge_frame){.ip_protocol=17};vectors++;
  assert(fe_bridge_frame(&s,&unicast)==-EOPNOTSUPP);s=valid;
  /* Ethernet padding cannot disguise UDP length beyond its IP datagram. */
  s.len+=6;be16(udp+4,18);check(&s,-EINVAL,tags);s=valid;
  be16(udp+4,7);check(&s,-EINVAL,tags);s=valid;
  be16(udp+4,8);check(&s,-EINVAL,tags);s=valid;
  s.len--;check(&s,-EINVAL,tags);s=valid;
  if(ipv6){
   ip[6]=0;check(&s,-EOPNOTSUPP,tags);s=valid;
   ip[6]=58;check(&s,-EOPNOTSUPP,tags);s=valid;
   be16(ip+4,0);check(&s,-EINVAL,tags);s=valid;
   be16(ip+4,7);check(&s,-EINVAL,tags);s=valid;
  } else {
   ip[0]=0x46;check(&s,-EOPNOTSUPP,tags);s=valid;
   ip[9]=2;check(&s,-EOPNOTSUPP,tags);s=valid;
   be16(ip+6,0x2000);check(&s,-EOPNOTSUPP,tags);s=valid;
   be16(ip+6,1);check(&s,-EOPNOTSUPP,tags);s=valid;
   be16(ip+2,27);check(&s,-EINVAL,tags);s=valid;
  }
  s.len=offset-14+iplen-1;check(&s,-EOPNOTSUPP,tags);s=valid;
  if(tags==2){s.tagged=true;if(metadata)be16(s.frame+16,0x8100);check(&s,-E2BIG,tags);}
 }
 printf("Native multicast wire parser: %u byte vectors (VLAN, IPv4/IPv6 UDP, padding, truncation, fragments, options, IGMP/MLD) passed\n",vectors);
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-frames-') as name:
 p=Path(name);(p/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-fsanitize=address,undefined',
  '-I'+str(DRIVERS),'-I'+str(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
