#!/usr/bin/env python3
"""Pass real linked PE lookahead ownership to the production ARM stop routine."""
import ast
import ctypes as C
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
fixture=root/'scripts/test_pe_rxq_backpressure.py'
ns={'__file__':str(fixture)}
exec(compile(fixture.read_text().split('m=Machine();retained=[]',1)[0],str(fixture),'exec'),ns)
for node in ast.parse(fixture.read_text()).body:
    if isinstance(node,ast.FunctionDef) and node.name in ('data_frame','reorder_peer'):
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixture),'exec'),ns)
ns.update(peer=bytes.fromhex('067afb78b287'),ap=bytes.fromhex('02e04c076805'),dst=bytes.fromhex('0050c2010200'))
reorder_peer,data_frame,B,MB,ST,VP,STATUS,IDS,TAIL=(ns[n] for n in ('reorder_peer','data_frame','B','MB','ST','VP','STATUS','IDS','TAIL'))
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(driver/'rx.c').read_text()
if len(sys.argv)>2:
    source=subprocess.check_output(['git','show',sys.argv[2]+':'+str((driver/'rx.c').relative_to(root))],cwd=root,text=True)
def function(name):
    a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start();b=source.index('{',a)+1;depth=1
    while depth:depth+=(source[b]=='{')-(source[b]=='}');b+=1
    return source[a:b]+'\n'
common=next(ast.literal_eval(n.value) for n in ast.parse((root/'scripts/test_radio_rx_handoff.py').read_text()).body
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='common' for t in n.targets))
common=common.replace('static unsigned syncs,','static u32 delivered_slots[256];\nstatic unsigned syncs,')
common=common.replace('delivered++;}', 'delivered_slots[delivered++]=slot;}')
common=common.replace('u8 *m=rx->rpq_memory+0x2000;put_unaligned_le32(get_unaligned_le32(m+376),m+380);',
                      'u8 *m=rx->pe.memory+0x2000;assert(get_unaligned_le32(m+376)==get_unaligned_le32(m+380));')
code=common+''.join(function(n) for n in ('rtl_ax_rx_idle_put','rtl_ax_rx_tail_recycle','rtl_ax_rx_work_recycle','rtl_ax_rx_pe_rpq_stop'))+r'''
static struct rtl_ax_rx rx;
static u8 tx_memory[131072];
static struct rtl_ax_io io={.read32=read32,.write32=write32,.write16=write16};
void setup(void *memory,const u32 *input_tags){
 memset(&rx,0,sizeof(rx));memset(tx_memory,0,sizeof(tx_memory));delivered=0;
 rx.pe.memory=memory;rx.pe.attached=true;rx.io=&io;rx.rpq_memory=tx_memory;rx.rpq_owned=true;
 rx.queues[0].buffer_bytes=12288;rx.queues[1].buffer_dma=0x06000000;
 memcpy(tags,input_tags,sizeof(tags));
 for(unsigned i=0;i<514;i++){rx.queues[0].payloads[i].data=(u8 *)&tags[i];rx.queues[0].payloads[i].dma=0x08000000+i*16384;}
}
int handoff(unsigned fail,u32 *output_tags,u32 *slots){
 stop_fail=fail;int ret=rtl_ax_rx_pe_rpq_stop(&rx);
 memcpy(output_tags,tags,sizeof(tags));memcpy(slots,delivered_slots,delivered*4);return ret;
}
unsigned delivery_count(void){return delivered;}
'''
with tempfile.TemporaryDirectory(prefix='pe-arm-stop-') as tmp:
    d=Path(tmp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-shared','-fPIC','-I',str(driver),str(d/'test.c'),'-o',str(d/'test.so')],check=True)
    lib=C.CDLL(str(d/'test.so'));lib.setup.argtypes=[C.c_void_p,C.c_void_p];lib.handoff.argtypes=[C.c_uint,C.c_void_p,C.c_void_p]
    cases=0
    for head in (0,128,254):
        for failure in (0,1):
            r=reorder_peer()
            for word in (174,175,176):r.put(MB+word*4,head)
            other=ST+1056;r.put(other,1<<31|0x345);r.put(other+4,6);r.put(other+28,64)
            duplicate=bytearray(data_frame(0x344,6,1));duplicate[1]|=8
            for frame in (data_frame(0x125,4),bytes(duplicate),data_frame(0x126,5),data_frame(0x123,2)):
                r.arrive(frame,True);r.capture()
            r.process();r.inventory()
            assert r.get(B+STATUS+((head+1)&255)*4)==1<<30
            assert r.get(MB+175*4)==head and r.get(MB+90*4)==1
            ids=[r.get(B+IDS+((head+i)&255)*4)&0x7fffffff for i in range(4)]
            memory=C.create_string_buffer(bytes(r.u.mem_read(B,0x20000)),0x20000)
            tags=(C.c_uint32*514)(*[r.get(r.payload_dma(i)) for i in range(514)]);slots=(C.c_uint32*256)()
            lib.setup(memory,tags)
            assert lib.handoff(0,tags,slots)==-16 and lib.delivery_count()==0 # NI not consumed
            C.c_uint32.from_buffer(memory,MB-B+364).value=1
            assert lib.handoff(failure,tags,slots)==(-110 if failure else 0)
            assert list(slots[:lib.delivery_count()])==[head,(head+2)&255],('replayed completed RX',head,list(slots[:lib.delivery_count()]))
            if failure:
                assert lib.handoff(0,tags,slots)==0 and lib.delivery_count()==2
            r.u.mem_write(B,memory.raw)
            for i,value in enumerate(tags):r.put(r.payload_dma(i),value)
            r.inventory()
            assert all(not r.get(r.payload_dma(i)) for i in ids[:3])
            assert r.get(B+TAIL)==ids[3]+1 and r.get(r.payload_dma(ids[3]))
            # The already-consumed NI payload still obeys original two-tail retention.
            # Stop clears PE's directory pointer; the existing start publishes it
            # again before polling. Model that pointer publication here.
            r.put(MB+320,ns['TABLE'])
            r.put(MB+352,0);r.put(MB+364,2);r.execute('rx_completion_poll');r.inventory()
            assert r.get(B+TAIL)==ids[3]+1
            r.put(MB+364,3);r.execute('rx_completion_poll');r.inventory()
            assert not r.get(B+TAIL) and not r.get(r.payload_dma(ids[3]))
            cases+=1
print(f'Linked PE -> native ARM stop: {cases} cross-TID replay/lookahead/wrap/stop-retry cases; no repeated delivery and exact514-buffer return PASS')
