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
const status = load(path.join(resources, 'tyfza1/status.js'), { baseclass, rpc, network: {}, L, E });
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
function config(device = 'pon-data0', protocol = 'dhcp') {
	uci.state = { values: {
		pon: { pon: { '.type': 'pon', mode: 'epon-d10g', enabled: '1', loid: 'lab-only' } },
		network: { wan: { '.type': 'interface', device, proto: protocol },
			wan6: { '.type': 'interface', device: '@wan', proto: 'dhcpv6' },
			lan: { '.type': 'interface', device: 'br-lan', proto: 'static' } },
		firewall: { defaults: { '.name': 'defaults', '.type': 'defaults', flow_offloading_hw: '1' } },
		wireless: { radio0: { '.type': 'wifi-device', disabled: '0' } }
	}, creates: {}, changes: {}, deletes: {}, reorder: {} };
}
async function render() { return view.render([null, {}, null, null]); }
async function main() {
	const online = { serdes: { mode: 'epon-d10g', configured: '1', rx_los: '0', error: '0' },
		mac: { prepared: '1', error: '0' }, mpcp: { enabled: '1', registered: '00000001' },
		oam_ports: [{ live: true, registered: true, discovery_stable: true, auth_state: 'accepted' }] };
	assert.equal(status.ponState(online).tone, 'good');
	assert.equal(status.ponState({}).tone, 'muted');
	assert.equal(status.ponState({ ...online, service: { enabled: '0', error: '0' } }).text, '未启用');
	assert.equal(status.ponState({ ...online, service: { enabled: '1', error: '1' } }).tone, 'danger');
	assert.equal(status.ponState({ ...online, serdes: { ...online.serdes, rx_los: '1' } }).tone, 'danger');
	assert.equal(status.ponState({ ...online, mac: { error: '-95' } }).tone, 'danger');
	assert.equal(status.ponState({ ...online, mpcp: { enabled: '0', registered: '00000001' } }).tone, 'muted');
	assert.equal(status.ponState({ ...online, mpcp: { enabled: '1', registered: 'garbage' } }).text, '等待注册');
	assert.equal(status.ponState({ ...online, oam_ports: [{ live: false, auth_state: 'accepted' }] }).tone, 'warn');
	assert.equal(status.ponState({ ...online, oam_ports: [{ live: true, registered: true, auth_state: 'rejected' }] }).tone, 'danger');
	for (const mode of ['gpon', 'xgpon', 'xgspon']) {
		assert.equal(status.ponState({ serdes: { mode, configured: '1' }, mac: { prepared: '1', activation: 'O5' }, mpcp: { enabled: '0' } }).text, '已激活 · O5');
		assert.equal(status.ponState({ serdes: { mode, configured: '1' }, mac: { activation: 'O4' } }).tone, 'warn');
	}
	assert.equal(status.bytes(null), '—');
	assert.equal(status.bytes('0'), '0 B');
	assert.equal(status.bytes('1048576'), '1.0 MiB');
	config();
	await render();
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
	for (const device of ['br-wan', 'pon-data0.100.200', 'custom-vlan', 'eth3']) {
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
	console.log('Frontend: status, stale auth, byte/SN validation, optional identity, native WAN navigation, real LuCI UCI writes and isolation checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
