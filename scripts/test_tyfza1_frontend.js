#!/usr/bin/env node
// Execute production view callbacks with the upstream LuCI UCI model.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const resources = path.join(root, 'port/packages/luci-app-tyfza1-pon/htdocs/luci-static/resources');
const baseclass = { extend: object => object };
let rpcResults = {};
const rpc = { declare: spec => () => {
	const result = rpcResults[spec.object + '.' + spec.method];
	return result instanceof Error ? Promise.reject(result) : Promise.resolve(result ?? {});
} };
let canWrite = true;
const L = { url: (...p) => '/' + p.join('/'), resource: p => '/luci-static/resources/' + p, bind: (f, ctx) => f.bind(ctx),
	hasViewPermission: () => canWrite, toArray: value => value == null ? [] : Array.isArray(value) ? value : String(value).split(/\s+/) };
function E(tag, attrs, children) {
	// LuCI DOM.create only expands the outer children array, not nested arrays.
	if (Array.isArray(children)) children = children.map(child => Array.isArray(child) ? String(child) : child);
	return { tag, attrs: attrs || {}, children, replaceChildren(...nodes) { this.children = nodes; },
		getAttribute(name) { return this.attrs[name]; },
		querySelectorAll() {
			const result = [];
			function walk(node) {
				if (Array.isArray(node)) return node.forEach(walk);
				if (!node || typeof node !== 'object') return;
				if (node.tag === 'details' && node.attrs['data-tyf-fold']) result.push(node);
				walk(node.children);
			}
			walk(this.children); return result;
		} };
}
function load(file, context) {
	return new Function(...Object.keys(context), fs.readFileSync(file, 'utf8'))(...Object.values(context));
}
const uci = load(path.join(root, 'sources/openwrt/feeds/luci/modules/luci-base/htdocs/luci-static/resources/uci.js'), { baseclass, rpc, L });
const validation = load(path.join(root, 'sources/openwrt/feeds/luci/modules/luci-base/htdocs/luci-static/resources/validation.js'), {
	baseclass, _: text => ({ format: () => text })
});
const status = load(path.join(resources, 'tyfza1/status.js'), { baseclass, rpc, L, E });
let options, lastMap;
class Map {
	constructor() { options = {}; lastMap = this; }
	chain() {}
	section() { return { tab() {}, option(type, key) { return this.taboption('', type, key); }, taboption(tab, type, key) {
		const option = { tab, type, values: {}, dependencies: [], value(k, v) { this.values[k] = v; }, depends(k, v) { this.dependencies.push([k, v]); } };
		options[key] = option;
		return option;
	} }; }
	render() { return Promise.resolve(E('form')); }
}
const form = { Map, NamedSection: 'section', Value: 'value', DummyValue: 'dummy', Flag: 'flag', ListValue: 'list' };
let polling;
const ui = load(path.join(resources, 'tyfza1/ui.js'), { baseclass, rpc, poll: { add: f => { polling = f; } }, status, L, E });
const view = load(path.join(resources, 'view/network/tyfza1-pon.js'), {
	view: baseclass, form, ui, uci, status, L, E
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
	const cssRevision = require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(resources, 'tyfza1/status.css'))).digest('hex').slice(0, 12);
	assert.equal(status.style().attrs.href, '/luci-static/resources/tyfza1/status.css?v=' + cssRevision,
		'CSS changes must change the URL so browser caches cannot retain an older ONU layout');
	assert.match(text(status.card('card', null, [E('p', {}, 'first child'), E('p', {}, 'second child')])), /first child.*second child/);
	const menu = JSON.parse(fs.readFileSync(path.join(resources, '../../../root/usr/share/luci/menu.d/luci-app-tyfza1-pon.json')));
	assert.equal(Object.keys(menu).filter(key => key.startsWith('admin/onu/')).length, 7);
	assert.equal(menu['admin/network/pon'].action.path, 'admin/onu/config');
	for (const item of Object.values(menu)) {
		if (item.action.type === 'view') assert.ok(fs.existsSync(path.join(resources, 'view', item.action.path + '.js')));
	}
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
		assert.ok(ui.diagnostics(stale).includes('error: -22'));
		const current = { ...active, data_path: { configured: '1', session: '7' }, omci: { live: true, session: '7', auth: '1', rx: '456' } };
		assert.equal(status.omciLive(current), true);
		assert.match(text(ui.authentication(current)), /OLT LOID 认证 认证通过/);
		const rejected = { ...current, omci: { ...current.omci, auth: '2' } };
		assert.equal(status.ponState(rejected).text, '认证未通过');
		const previousSession = { ...current, omci: { ...current.omci, session: '6' } };
		assert.equal(status.omciLive(previousSession), false);
		assert.equal(status.ponState(previousSession).text, 'OMCI 状态不可用');
		assert.match(text(ui.authentication(previousSession)), /OMCI 收 \/ 发 — \/ —/);
		assert.doesNotMatch(text(ui.authentication(previousSession)), /认证通过/);
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
	for (const key of ['hardware_version', 'software_version']) {
		const ituField = { section: { formvalue: () => 'xgpon' } }, eponField = { section: { formvalue: () => 'epon-d10g' } };
		assert.equal(options[key].validate.call(ituField, 'pon', 'a'.repeat(14)), true);
		assert.notEqual(options[key].validate.call(ituField, 'pon', 'a'.repeat(15)), true);
		assert.equal(options[key].validate.call(eponField, 'pon', 'a'.repeat(16)), true);
	}
	assert.equal(options.operator_id.validate('pon', 'CTC'), true);
	assert.notEqual(options.operator_id.validate('pon', '12345'), true);
	assert.equal(options.hardware_offload, undefined);
	assert.equal(options._acceleration.cfgvalue().attrs.href, '/admin/onu/acceleration');
	assert.equal(uci.state.changes.firewall, undefined);
	assert.equal(options.llid_count.rmempty, false);
	assert.equal(uci.state.changes.wireless, undefined);
	assert.equal(uci.state.changes.network, undefined);
	assert.equal(uci.state.changes.pon, undefined);
	await polling();
	const initialOptions = options;
	rpcResults['luci.tyfza1-pon.status'] = new Error('RPC unavailable');
	await polling();
	assert.match(JSON.stringify(page), /上次成功读取的数据/);
	assert.equal(options, initialOptions);
	const rawPath = 'configured=0 error=-5 paths=2\ngem=100 vlan=100\ngem=200 vlan=200\n';
	rpcResults['luci.tyfza1-pon.status'] = { ...online, omci: { live: false, error: '-22' }, data_path: { error: '-5' }, data_path_raw: rawPath };
	await polling();
	assert.ok(ui.diagnostics({ omci: { live: false, error: '-22' }, data_path_raw: rawPath }).includes('[omci]\nlive: false\nerror: -22'));
	assert.ok(ui.diagnostics({ data_path_raw: rawPath }).includes('[data_path]\n' + rawPath));
	assert.equal(options, initialOptions);
	rpcResults['luci.tyfza1-pon.status'] = {};
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
	const fold = page.querySelectorAll()[0];
	fold.open = true;
	await polling();
	assert.equal(page.querySelectorAll()[0].open, true);
	for (const filename of ['overview', 'hardware', 'acceleration', 'diagnostics', 'internet', 'iptv']) {
		const module = load(path.join(resources, 'view/tyfza1', filename + '.js'), {
			view: baseclass, form, uci, status, ui, L, E, widgets: { DeviceSelect: 'device' }
		});
		config();
		const snapshot = { pon: online, board: {}, networks: [], wifi: [] };
		const args = { overview: snapshot, hardware: snapshot, acceleration: [null, online], diagnostics: online,
			internet: [null, snapshot], iptv: [null, online] };
		const rendered = await module.render(args[filename]);
		assert.ok(rendered);
		assert.doesNotMatch(text(rendered), /\[object Object\]/, filename + ' must render card children as nodes');
		if (filename === 'internet') {
			assert.equal(options.device.rmempty, false);
			assert.equal(lastMap.readonly, undefined, 'native Map.load must still check per-config write permission');
			assert.equal(options.password.password, true);
			assert.equal(options.password.retain, true);
			assert.equal(options.ipv6.retain, true);
			assert.equal(uci.state.changes.network, undefined);
			for (const [dev, proto] of [['pon0.100.200', 'static'], ['custom-wan', 'custom']]) {
				config(dev, proto); await module.render([null, snapshot]);
				assert.equal(uci.get('network', 'wan', 'device'), dev);
				assert.equal(uci.get('network', 'wan', 'proto'), proto);
				assert.equal(uci.state.changes.network, undefined);
				if (proto === 'custom') assert.equal(options.proto, undefined);
			}
			config(); delete uci.state.values.network.wan;
			await module.render([null, snapshot]); assert.equal(options.proto, undefined);
			assert.equal(module.handleSaveApply, null, 'no editable WAN must not offer Apply');
			config(); uci.state.values.network.wan.ipaddr = ['192.0.2.2/24', '192.0.2.3/24'];
			await module.render([null, snapshot]); assert.equal(options.proto, undefined);
		}
		if (filename === 'iptv') {
			uci.state.values.network.br = { '.name': 'br', '.type': 'device', type: 'bridge', name: 'br-lan', ports: ['lan1', 'lan2'] };
			await module.render([null, online]);
			assert.equal(options._ports.cfgvalue(), 'lan1 · lan2');
			assert.ok(options.igmp_snooping); assert.ok(options.multicast_querier);
			uci.state.values.network.br.igmp_snooping = '1';
			assert.equal(options.multicast_querier.cfgvalue('br'), '1');
			uci.state.values.network.br.multicast_querier = '0';
			assert.equal(options.multicast_querier.cfgvalue('br'), '0');
			assert.equal(uci.state.changes.network, undefined);
		}
		if (filename === 'acceleration') {
			options.flow_offloading_hw.write('defaults', '1');
			assert.equal(uci.get('firewall', 'defaults', 'flow_offloading'), '1');
			options.flow_offloading_hw.write('defaults', '0');
			assert.equal(uci.get('firewall', 'defaults', 'flow_offloading'), '1');
		}
		if (['internet', 'iptv', 'acceleration'].includes(filename)) {
			canWrite = false; await module.render(args[filename]); assert.equal(lastMap.readonly, true); canWrite = true;
		}
	}
	config(); canWrite = false; await render(); assert.equal(lastMap.readonly, true); canWrite = true;
	assert.equal(ui.paths({ data_path_raw: rawPath }).length, 2);
	assert.equal(ui.bytes(0), '0 B'); assert.equal(ui.bytes(null), '—');
	rpcResults = {
		'network.interface.dump': [{ interface: 'wan', proto: 'pppoe', up: true, l3_device: 'pppoe-wan', 'ipv4-address': [{address: '192.0.2.1', mask: 32}],
			'ipv6-address': [{address: '2001:db8::1', mask: 64}],
			'ipv6-prefix-assignment': [{'local-address': {address: '2001:db8::1', mask: 64}}, {'local-address': {address: '2001:db8:1::1', mask: 64}}] }],
		'luci-rpc.getWirelessDevices': { radio0: { up: true, interfaces: [{ ifname: 'wlan0', iwinfo: { ssid: 'Fixture', frequency: 5180, bitrate: 1201000 } }] } }
	};
	let snapshot = await ui.snapshot();
	assert.equal(snapshot.networks[0].ipv4[0], '192.0.2.1/32');
	assert.deepEqual(snapshot.networks[0].ipv6, ['2001:db8::1/64', '2001:db8:1::1/64']);
	assert.equal(snapshot.wifi[0].frequency, '5.180'); assert.equal(snapshot.wifi[0].bitrate, 1201);
	rpcResults['network.interface.dump'] = new Error('unavailable');
	snapshot = await ui.snapshot(); assert.equal(snapshot.networkError, true); assert.equal(snapshot.wifiError, false);
	rpcResults['luci-rpc.getWirelessDevices'] = new Error('unavailable');
	snapshot = await ui.snapshot(); assert.equal(snapshot.wifiError, true);
	const acl = JSON.parse(fs.readFileSync(path.join(resources, '../../../root/usr/share/rpcd/acl.d/luci-app-tyfza1-pon.json')));
	assert.deepEqual(acl['luci-app-tyfza1-pon'].write.uci, ['pon', 'firewall', 'network']);
	assert.equal(acl['luci-app-tyfza1-pon'].write.ubus, undefined);
	let renderingFails = false;
	const guardedPage = ui.page('test', '', {}, () => Promise.resolve({}), () => {
		if (renderingFails) throw new Error('fixture rendering failure');
		return E('div', {}, 'last successful snapshot');
	});
	renderingFails = true;
	await polling();
	assert.match(text(guardedPage), /上次成功读取的数据/);
	assert.match(text(guardedPage), /last successful snapshot/);
	console.log('ONU frontend: 7 views, legacy URL, state semantics, polling/folds, native UCI isolation, read-only forms, identity validation and independent RPC failures passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
