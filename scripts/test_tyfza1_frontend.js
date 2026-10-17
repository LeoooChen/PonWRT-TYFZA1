#!/usr/bin/env node
// Execute production view callbacks with the upstream LuCI UCI model.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const resources = path.join(root, 'port/packages/luci-app-tyfza1-pon/htdocs/luci-static/resources');
const baseclass = { extend: object => object };
const rpc = { declare: () => () => Promise.resolve({}) };
const L = { url: (...p) => '/' + p.join('/'), resource: p => '/luci-static/resources/' + p, bind: (f, ctx) => f.bind(ctx) };
function E(tag, attrs, children) {
	return { tag, attrs: attrs || {}, children, replaceChildren(...nodes) { this.children = nodes; } };
}
function load(file, context) {
	return new Function(...Object.keys(context), fs.readFileSync(file, 'utf8'))(...Object.values(context));
}
const uci = load(path.join(root, 'sources/openwrt/feeds/luci/modules/luci-base/htdocs/luci-static/resources/uci.js'), { baseclass, rpc, L });
const validation = load(path.join(root, 'sources/openwrt/feeds/luci/modules/luci-base/htdocs/luci-static/resources/validation.js'), {
	baseclass, _: text => ({ format: () => text })
});
const status = load(path.join(resources, 'tyfza1/status.js'), { baseclass, rpc, L, E });
let options;
class Map {
	constructor() { options = {}; }
	chain() {}
	section() { return { tab() {}, taboption(tab, type, key) {
		const option = { tab, type, values: {}, dependencies: [], value(k, v) { this.values[k] = v; }, depends(k, v) { this.dependencies.push([k, v]); } };
		options[key] = option;
		return option;
	} }; }
	render() { return Promise.resolve(E('form')); }
}
const form = { Map, NamedSection: 'section', Value: 'value', DummyValue: 'dummy', Flag: 'flag', ListValue: 'list' };
let polling;
const view = load(path.join(resources, 'view/network/tyfza1-pon.js'), {
	view: baseclass, form, poll: { add: f => { polling = f; } }, uci, status, L, E
});
function config(device = 'pon0', protocol = 'dhcp') {
	uci.state = { values: {
		pon: { pon: { '.type': 'pon', mode: 'epon-d10g', enabled: '1', loid: 'lab-only' } },
		network: { wan: { '.type': 'interface', device, proto: protocol },
			wan6: { '.type': 'interface', device: '@wan', proto: 'dhcpv6' },
			lan: { '.type': 'interface', device: 'br-lan', proto: 'static' } },
		firewall: { defaults: { '.name': 'defaults', '.type': 'defaults', flow_offloading_hw: '1' } },
		wireless: { radio0: { '.type': 'wifi-device', disabled: '0' } }
	}, creates: {}, changes: {}, deletes: {}, reorder: {} };
}
async function render(data = {}) { return view.render([null, data, null, null]); }
function text(node) {
	if (Array.isArray(node)) return node.map(text).join(' ');
	return node && typeof node === 'object' ? node.textContent ?? text(node.children) : node == null ? '' : String(node);
}
async function main() {
	const menu = JSON.parse(fs.readFileSync(path.join(resources, '../../../root/usr/share/luci/menu.d/luci-app-tyfza1-pon.json')));
	assert.deepEqual(Object.keys(menu), ['admin/network/pon']);
	for (const file of ['view/status/tyfza1.js', 'view/status/include/05_tyfza1.js'])
		assert.equal(fs.existsSync(path.join(resources, file)), false);
	const online = { serdes: { mode: 'epon-d10g', configured: '1', rx_los: '0', error: '0' },
		mac: { prepared: '1', error: '0' }, mpcp: { enabled: '1', registered: '00000001' },
		oam_ports: [{ live: true, registered: true, discovery_stable: true, auth_state: 'accepted' }] };
	assert.equal(status.ponState(online).tone, 'good');
	assert.equal(status.ponState({}).tone, 'muted');
	assert.equal(status.ponState({ ...online, service: { enabled: '0', error: '0' } }).text, '未启用');
	assert.equal(status.ponState({ ...online, service: { enabled: '1', error: '1' } }).tone, 'danger');
	assert.match(status.ponState({ service: { enabled: '1', error: '-5' } }).help, /service.*-5/);
	assert.equal(status.ponState({ serdes: { mode: 'epon-d10g' } }).text, '状态不可用');
	assert.equal(status.ponState({ ...online, optical: { tx_fault: '1' } }).text, '光模块发射故障');
	assert.match(JSON.stringify(status.ponCard({ ...online, optical: { rx_los: '1' } })), /接收失光/);
	assert.match(JSON.stringify(status.offloadCard({})), /状态未知/);
	assert.equal(status.ponState({ ...online, serdes: { ...online.serdes, rx_los: '1' } }).tone, 'danger');
	assert.equal(status.ponState({ ...online, mac: { error: '-95' } }).tone, 'danger');
	assert.equal(status.ponState({ ...online, mpcp: { enabled: '0', registered: '00000001' } }).tone, 'muted');
	assert.equal(status.ponState({ ...online, mpcp: { enabled: '1', registered: 'garbage' } }).text, '等待注册');
	assert.equal(status.ponState({ ...online, oam_ports: [{ live: false, auth_state: 'accepted' }] }).tone, 'warn');
	assert.equal(status.ponState({ ...online, oam_ports: [{ live: true, registered: true, auth_state: 'rejected' }] }).tone, 'danger');
	for (const mode of ['gpon', 'xgpon', 'xgspon']) {
		const active = { serdes: { mode, configured: '1' }, mac: { prepared: '1', activation: 'O5', onu_id: '17' },
			activation: { omcc_onu_id: '37' }, omci: { live: true, error: '0' }, mpcp: { enabled: '0' } };
		assert.equal(status.ponState(active).tone, 'warn');
		assert.equal(status.ponState({ ...active, data_path: { configured: '1' } }).tone, 'good');
		assert.equal(status.ponState({ ...active, data_path: { configured: '1', error: '-5' } }).text, '业务配置异常');
		assert.match(status.ponState({ ...active, omci: { live: true, error: '-22' } }).help, /omci.*-22/);
		assert.equal(status.ponState({ ...active, mac: { activation: 'O4' } }).tone, 'warn');
		config();
		assert.ok(text(await render(active)).includes('ONU-ID ' + (mode === 'gpon' ? '17' : '37')));
		const stale = { ...active, data_path: { configured: '1' }, omci: { live: false, error: '-22', rx: '456', tx: '789' } };
		assert.equal(status.ponState(stale).text, 'OMCI 状态不可用');
		assert.ok(text(await render(stale)).includes('OMCI 收 / 发 — / —'));
		assert.ok(text(await render(stale)).includes('error: -22'));
	}
	config();
	const page = await render();
	assert.equal(options.wan_protocol, undefined);
	assert.equal(options.wan_username, undefined);
	assert.equal(options.wan_password, undefined);
	assert.equal(options._wan_vlan, undefined);
	assert.notEqual(options.onu_mac.rmempty, false);
	assert.equal(options.onu_mac.validate('pon', ''), true);
	assert.notEqual(options.onu_mac.validate('pon', 'ff:ff:ff:ff:ff:ff'), true);
	assert.notEqual(options.onu_mac.validate('pon', '00:00:00:00:00:00'), true);
	assert.equal(options.onu_mac.validate('pon', '02:00:00:00:00:01'), true);
	assert.notEqual(options.serial_number.rmempty, false);
	assert.equal(options.serial_number.validate('pon', ''), true);
	assert.equal(options._network_settings.cfgvalue().attrs.href, '/admin/network/network');
	assert.equal(Object.keys(options.mode.values).length, 6);
	assert.equal(options.llid_count.datatype, 'and(uinteger,range(1,32))');
	for (const [value, expected] of [['1', true], ['32', true], ['08', true], ['1.5', false], ['0', false], ['33', false]]) {
		const validator = { value, factory: validation, assert: condition => !!condition };
		assert.equal(validation.types.uinteger.call(validator) && validation.types.range.call(validator, 1, 32), expected);
	}
	assert.equal(options.loid.validate('pon', '中'.repeat(8)), true);
	assert.notEqual(options.loid.validate('pon', '中'.repeat(9)), true);
	assert.notEqual(options.loid.validate('pon', 'a\0b'), true);
	assert.equal(options.serial_number.validate('pon', 'ABCD1234abcd'), true);
	assert.equal(options.serial_number.validate('pon', '414243441234abcd'), true);
	assert.notEqual(options.serial_number.validate('pon', 'ABCD1234567z'), true);
	assert.notEqual(options.serial_number.validate('pon', '中文12345678'), true);
	options.hardware_offload.write('pon', '1');
	assert.equal(uci.get('firewall', 'defaults', 'flow_offloading'), '1');
	options.hardware_offload.write('pon', '0');
	assert.equal(uci.get('firewall', 'defaults', 'flow_offloading_hw'), '0');
	assert.equal(uci.get('firewall', 'defaults', 'flow_offloading'), '1');
	assert.equal(uci.state.changes.wireless, undefined);
	assert.equal(uci.state.changes.network, undefined);
	assert.equal(uci.state.changes.pon, undefined);
	await polling();
	const initialOptions = options;
	const callStatus = status.callStatus;
	status.callStatus = () => Promise.reject(new Error('RPC unavailable'));
	await polling();
	assert.match(JSON.stringify(page), /上次成功读取的数据/);
	assert.equal(options, initialOptions);
	const rawPath = 'configured=0 error=-5 paths=2\ngem=100 vlan=100\ngem=200 vlan=200\n';
	status.callStatus = () => Promise.resolve({ ...online, omci: { live: false, error: '-22' }, data_path: { error: '-5' }, data_path_raw: rawPath });
	await polling();
	assert.ok(text(page).includes('[omci]\nlive: false\nerror: -22'));
	assert.ok(text(page).includes('[data_path]\n' + rawPath));
	assert.equal(options, initialOptions);
	status.callStatus = callStatus;
	options.llid_count.write('pon', '08');
	assert.equal(uci.get('pon', 'pon', 'llid_count'), '8');
	for (const device of ['br-wan', 'pon0.100.200', 'custom-vlan', 'lan4']) {
		config(device, 'static');
		await render();
		assert.equal(options._wan_vlan, undefined);
		assert.equal(options._wan_protocol.cfgvalue(), 'static');
		assert.equal(uci.get('network', 'wan', 'device'), device);
	}
	config();
	delete uci.state.values.network.wan;
	delete uci.state.values.firewall.defaults;
	await render();
	assert.equal(options.wan_protocol, undefined);
	assert.equal(options.hardware_offload, undefined);
	console.log('PON frontend: native homepage isolation, truthful status/errors, polling, identity validation and native UCI writes passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
