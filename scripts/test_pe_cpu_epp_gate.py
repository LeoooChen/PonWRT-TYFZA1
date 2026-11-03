from pathlib import Path
root=Path(__file__).resolve().parents[1]
fixture=(root/"scripts/test_pe_rxq_backpressure.py").read_text()
exec(compile(fixture, str(root/"scripts/test_pe_rxq_backpressure.py"), "exec"))
# Actual linked CPU EPP gate: congestion must not mutate payload, PN/SN or NI.
for cos in range(8):
 for used in (0,64,95,96,127):
  for pending in (0,1,8,80):
   r=reorder_peer();r.put(MB+90*4,pending)
   r.put(0x10007000+cos*4,(1000+used*8)&1023);r.put(0x10007100+cos*4,1000)
   at,raw=r.arrive(data_frame(0x123,2),True);r.capture();r.process()
   blocked=used+pending+1>96
   assert r.get(MB+90*4)==pending+(not blocked)
   assert r.get(MB+175*4)==(0 if blocked else 1)
   assert not r.get(MB+94*4)
   if blocked:
    assert bytes(r.u.mem_read(at,len(raw)))==raw
    assert r.get(ST+1024)==1<<31|0x123
    r.put(0x10007000+cos*4,1000);r.put(VP+0x1c,pending);r.capture();r.process()
    assert r.get(MB+90*4)==pending+1 and r.get(MB+175*4)==1
print('CPU EPP gate: 160 occupancy/pending/COS/wrap cases retain untouched MPDU and resume after drain')
