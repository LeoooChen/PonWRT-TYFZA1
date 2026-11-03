#!/usr/bin/env python3
"""Execute LuCI's actual IPv4 validators and address-change warning logic."""
from pathlib import Path
import argparse
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
feed = root / 'sources/openwrt/feeds/luci'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--before', action='store_true')
parser.add_argument('--feed', type=Path, default=feed, help='Read-only LuCI source checkout')
args = parser.parse_args()
feed = args.feed
with tempfile.TemporaryDirectory(prefix='lan-address-ui-') as directory:
    work = Path(directory)
    luci = work / 'feeds/luci'
    patches = sorted((root / 'port/luci-patches').glob('*.patch'))
    paths = {line[6:] for patch in patches for line in patch.read_text().splitlines()
             if line.startswith('--- a/')}
    paths.add('modules/luci-base/htdocs/luci-static/resources/validation.js')
    for path in paths:
        dest = luci / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(['git', '-C', str(feed), 'show', 'HEAD:' + path]))
    subprocess.run(['git', 'init', '-q', str(luci)], check=True)
    if not args.before:
        for _ in range(2):
            subprocess.run(['sh', str(root / 'port/scripts/apply-luci-patches.sh'), str(work)], check=True)
    if not args.before:
        header = (luci / 'modules/luci-base/ucode/template/header.ut').read_text()
        assert "pkgs_update_time += '-lan-cidr1';" in header
    resources = luci / 'modules/luci-base/htdocs/luci-static/resources'
    test = r'''
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const resources = process.argv[2];
String.prototype.format = function(...args) { let i=0; return this.replace(/%\w/g,()=>args[i++]); };
const _ = s => s;
const L = {bind:(f,ctx)=>f.bind(ctx), resolveDefault:p=>Promise.resolve(p),
 isObject:v=>v!==null && typeof v==='object', raise:(type,msg)=>{throw Error(msg)},
 dom:{parent:()=>({}),content:()=>{},append:()=>{}}};
const prototypes = [];
const validation = new Function('baseclass','L','_',fs.readFileSync(path.join(resources,'validation.js'),'utf8'))(
 {extend:p=>{prototypes.push(p);return p}},L,_);
function valid(datatype,value) {
 const ctx = {value,factory:validation,apply:prototypes[0].apply,assert:v=>!!v};
 const compiled = validation.compile(datatype);
 return compiled[0].apply(ctx,compiled[1]);
}
const form = {Value:{extend:p=>p,prototype:{renderWidget:()=>({})}},
 DynamicList:{prototype:{renderWidget:()=>({})}}};
const protocol = new Function('form','network','validation','L','E','_',fs.readFileSync(path.join(resources,'protocol/static.js'),'utf8'))(
 form,{registerProtocol:(name,proto)=>proto,maskToPrefix:()=>24},validation,L,()=>({}),_);
const addr = {...protocol.CBIIPValue};
addr.renderWidget('lan',0,['192.168.1.1/24']);
assert(valid(addr.datatype,'192.168.8.1/24'));
assert(valid(addr.datatype,'192.168.8.1/255.255.255.0'));
assert(valid(addr.datatype,'192.168.8.1/32')); // explicit host route remains supported
assert(!valid(addr.datatype,'192.168.8.1/33'));
assert(!valid(addr.datatype,'999.168.8.1/24'));
assert(!valid(addr.datatype,'192.168.8.1'),'CIDR list accepted a bare IPv4 and silently permits /32');
addr.renderWidget('lan',0,'192.168.8.1');
assert(valid(addr.datatype,'192.168.8.1')); // separate netmask form is unchanged
assert(!valid(addr.datatype,'192.168.8.1/24'));
addr.map={lookupOption:()=>[{formvalue:()=> '255.255.255.0',cbid:()=>''}],findElement:()=>null};
addr.isValid=()=>true;addr.formvalue=()=> '192.168.8.1';
addr.handleSwitch('lan',0,{target:{}});
assert(!valid(addr.datatype,'192.168.8.1'),'switching to list mode still accepts a bare address');
const ui = fs.readFileSync(path.join(resources,'ui.js'),'utf8');
const start = ui.indexOf('\t\tcheckConnectivityAffected() {');
const end = ui.indexOf('\n\t\t},',start)+5;
const checker = new Function('L','fs',`return {${ui.slice(start,end)}}`)(L,
 {exec_direct:()=>Promise.resolve({inbound_interfaces:['lan']})}).checkConnectivityAffected;
(async()=>{
 for(const change of [['set','lan','ipaddr','192.168.8.1'],['list-add','lan','ipaddr','192.168.8.1/24'],
  ['list-del','lan','ipaddr','192.168.1.1/24'],['remove','lan','ipaddr'],['set','lan','netmask','255.255.0.0']])
  assert.equal(await checker.call({changes:{network:[change]}}),'lan');
 for(const change of [['list-add','wan','ipaddr','192.0.2.1/24'],['set','lan','mtu','1500']])
  assert.equal(await checker.call({changes:{network:[change]}}),null);
 console.log('PASS: actual LuCI CIDR/dotted-mask validation, legacy form, mode switch and address-list connectivity warnings');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    script = work / 'check.js'
    script.write_text(test)
    subprocess.run(['node', str(script), str(resources)], check=True)
