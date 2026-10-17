'use strict';
'require baseclass';
'require poll';
'require rpc';
'require tyfza1.status as status';

var board = rpc.declare({ object: 'system', method: 'board', expect: { '': {} }, reject: true });
var interfaces = rpc.declare({ object: 'network.interface', method: 'dump', expect: { 'interface': [] }, reject: true });
var radios = rpc.declare({ object: 'luci-rpc', method: 'getWirelessDevices', expect: { '': {} }, reject: true });

function note(text) { return E('p', { class: 'tyf-note' }, [text]); }
function link(label, path) { return E('a', { class: 'cbi-button tyf-link', href: L.url.apply(L, path.split('/')) }, [label]); }
function tile(label, value, hint) {
	return E('div', { class: 'tyf-tile' }, [E('dt', {}, [label]), E('dd', {}, [status.value(value)]), hint ? note(hint) : null]);
}
function grid(items) { return E('div', { class: 'tyf-grid' }, items); }
function fold(title, key, contents) {
	return E('details', { class: 'tyf-fold', 'data-tyf-fold': key }, [E('summary', {}, title), E('div', {}, contents)]);
}
function bytes(value) {
	if (value == null || value === '' || !isFinite(Number(value))) return '—';
	var n = Number(value), units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0;
	while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
	return n.toFixed(i ? 1 : 0) + ' ' + units[i];
}
function fields(text) {
	var result = {};
	String(text || '').trim().split(/\s+/).forEach(function(token) {
		var index = token.indexOf('=');
		if (index > 0) result[token.slice(0, index)] = token.slice(index + 1);
	});
	return result;
}
function paths(data) {
	return String(data.data_path_raw || '').split('\n').map(fields).filter(function(path) { return path.gem != null; });
}
function services(data, multicastOnly) {
	var available = paths(data).filter(function(path) { return !multicastOnly || path.multicast === '1'; });
	return available.length ? grid(available.map(function(path) {
		return status.card('GEM ' + path.gem, status.badge(path.multicast === '1' ? '组播' : '单播'), status.rows([
			['VLAN', status.value(path.vlan)], ['Alloc-ID / T-CONT', status.value(path.alloc) + ' / ' + status.value(path.tcont)],
			['队列', status.value(path.queue)], ['上行加密', path.upstream_aes == null ? '未提供' : path.upstream_aes === '1' ? '启用' : '未启用']
		]));
	})) : note(multicastOnly ? '尚未取得 OMCI 组播通道。不代表线路没有 IPTV；EPON 业务和本地 VLAN 请在接口管理中核对。' : '尚无可展示的 OMCI GEM 业务通道。EPON 使用 LLID，不使用 GEM。');
}
function authentication(data) {
	var mode = (data.serdes || {}).mode;
	if (!status.modes[mode]) return note('线路协议尚未就绪，暂无认证状态。');
	if (['gpon', 'xgpon', 'xgspon'].indexOf(mode) !== -1) {
		var mac = data.mac || {}, activation = data.activation || {}, path = data.data_path || {};
		var live = status.omciLive(data), omci = live ? data.omci : {};
		var auth = omci.auth == null ? '未提供' : +omci.auth === 1 ? '认证通过' : +omci.auth > 1 ? '认证未通过（状态码 ' + omci.auth + '）' : '未收到 LOID 认证结果';
		return E('div', {}, [status.rows([
			['激活状态', status.value(mac.activation)], ['ONU-ID', status.value(mode === 'gpon' ? mac.onu_id : activation.omcc_onu_id)],
			['OMCI 状态', status.badge(live ? '有效' : '不可用', live ? 'good' : 'danger')],
			['OLT LOID 认证', auth],
			['业务通道数', status.value(path.paths)], ['OMCI 收 / 发', status.value(omci.rx) + ' / ' + status.value(omci.tx)],
			['OMCI 错误码', status.value(omci.error)], ['业务错误码', status.value(path.error)]
		]), note('O5 表示光链路已激活；业务是否可用仍取决于 OLT 下发和 WAN 配置。')]);
	}
	var labels = { waiting: '等待认证', challenge: '正在认证', accepted: '认证通过', rejected: '认证拒绝' };
	return (data.oam_ports || []).length ? E('div', { class: 'tyf-sessions' }, data.oam_ports.map(function(port) {
		var accepted = port.live && port.registered && port.discovery_stable && port.auth_state === 'accepted';
		var text = !port.live ? '会话已失效' : !port.registered ? '等待注册' :
			port.auth_state === 'accepted' && !accepted ? '等待 OAM 发现' : labels[port.auth_state] || '等待状态';
		return E('section', { class: 'tyf-session' }, [
			E('div', { class: 'tyf-card-head' }, [E('strong', {}, ['LLID ' + port.llid_index]), status.badge(text,
				accepted ? 'good' : port.live && port.registered && port.auth_state === 'rejected' ? 'danger' : 'muted')]),
			status.rows([['注册', port.live && port.registered ? '已注册' : '未注册'],
				['OAM 发现', port.live && port.discovery_stable ? '已完成' : '未完成'],
				['接收 / 发送帧', status.value(port.rx_frames) + ' / ' + status.value(port.tx_frames)]])
		]);
	})) : note('暂无 OAM 认证会话，等待逻辑链路注册。');
}
function diagnostics(data) {
	return ['service', 'state', 'serdes', 'optical', 'bosa', 'mac', 'activation', 'mpcp', 'oam_ports', 'omci', 'data_path', 'fec', 'traffic', 'fe', 'deepq', 'puc', 'pdc', 'llids'].map(function(name) {
		var entry = name === 'data_path' ? data.data_path_raw : data[name];
		return '[' + name + ']\n' + (entry == null ? '未提供' : Array.isArray(entry) ? JSON.stringify(entry, null, 2) : typeof entry === 'object'
			? Object.keys(entry).map(function(key) { return key + ': ' + entry[key]; }).join('\n') : entry);
	}).join('\n\n');
}
function ports(data) {
	return grid((data.ports || []).map(function(port) {
		var up = port.present && port.carrier === '1', stats = port.statistics || {};
		return status.card(port.device, status.badge(!port.present ? '不存在' : up ? '已连接' : '未连接', up ? 'good' : 'muted'), status.rows([
			['速率', up && Number(port.speed) > 0 ? port.speed + ' Mbps' : '—'],
			['接收 / 发送', bytes(stats.rx_bytes) + ' / ' + bytes(stats.tx_bytes)],
			['丢包 RX / TX', status.value(stats.rx_dropped) + ' / ' + status.value(stats.tx_dropped)]
		]));
	}));
}
function snapshot() {
	return Promise.all([status.callStatus().catch(function() { return null; }), board().catch(function() { return {}; }),
		interfaces().catch(function() { return null; }), radios().catch(function() { return null; })]).then(function(data) {
		var wifi = [];
		Object.keys(data[3] || {}).forEach(function(name) {
			var radio = data[3][name];
			(radio.interfaces || []).forEach(function(iface) {
				var info = iface.iwinfo || {};
				wifi.push({ name: iface.ifname || iface.section || name, ssid: info.ssid || (iface.config || {}).ssid,
					up: radio.up && !radio.disabled && !!iface.ifname, channel: info.channel,
					frequency: info.frequency > 0 ? (info.frequency / 1000).toFixed(3) : null,
					bitrate: info.bitrate > 0 ? info.bitrate / 1000 : null });
			});
		});
		return { pon: data[0], board: data[1], wifi: wifi, networkError: data[2] == null, wifiError: data[3] == null,
			networks: (data[2] || []).map(function(net) {
				function addresses(key) { return (net[key] || []).map(function(addr) { return addr.address + '/' + addr.mask; }); }
				var ipv6 = addresses('ipv6-address');
				(net['ipv6-prefix-assignment'] || []).forEach(function(prefix) {
					var local = prefix && prefix['local-address'];
					if (local) ipv6.push(local.address + '/' + local.mask);
				});
				return { name: net.interface, proto: net.proto, up: net.up, device: net.l3_device || net.device,
					ipv4: addresses('ipv4-address'), ipv6: Array.from(new Set(ipv6)) };
			}) };
	});
}
function networks(data) {
	if (data.networkError) return note('网络状态读取失败；未将历史数据作为实时连接状态。');
	return grid((data.networks || []).filter(function(net) { return net.name !== 'loopback'; }).map(function(net) {
		return status.card(net.name, status.badge(net.up ? '接口已连接' : '未连接', net.up ? 'good' : 'muted'), status.rows([
			['协议 / 设备', net.proto + ' / ' + status.value(net.device)],
			['IPv4', net.ipv4.length ? net.ipv4.join(' · ') : '—'], ['IPv6', net.ipv6.length ? net.ipv6.join(' · ') : '—']
		]));
	}));
}
function page(title, description, initial, read, render, form) {
	var last = initial == null ? null : new Date(), pending;
	var warning = E('p', { class: 'alert-message warning', role: 'status', hidden: initial != null }, '状态读取失败，尚无实时数据。配置编辑不受影响。');
	var live = E('div', { class: 'tyf-live' }, initial == null ? null : render(initial));
	var updated = E('span', { class: 'tyf-updated' });
	var button = E('button', { type: 'button', class: 'cbi-button', click: refresh }, '刷新状态');
	function showTime() { updated.textContent = last ? '更新于 ' + last.toLocaleTimeString() + ' · 5 秒刷新' : '等待首次读取'; }
	function refresh() {
		if (pending) return pending;
		button.disabled = true;
		pending = read().then(function(next) {
			var opened = {};
			live.querySelectorAll('details[data-tyf-fold]').forEach(function(node) { opened[node.getAttribute('data-tyf-fold')] = node.open; });
			live.replaceChildren(render(next));
			live.querySelectorAll('details[data-tyf-fold]').forEach(function(node) { node.open = !!opened[node.getAttribute('data-tyf-fold')]; });
			warning.hidden = true;
			last = new Date();
		}).catch(function() {
			warning.hidden = false;
			warning.textContent = last ? '刷新失败，以下状态为上次成功读取的数据，并非当前状态。配置编辑不受影响。' : '状态读取失败，尚无实时数据。';
		}).finally(function() { pending = null; button.disabled = false; showTime(); });
		return pending;
	}
	showTime();
	poll.add(refresh, 5);
	return E('div', { class: 'tyf-pon cbi-map' }, [status.style(),
		E('div', { class: 'tyf-page-header' }, [E('div', {}, [E('p', { class: 'tyf-eyebrow' }, 'TYFZA1 / ONU'), E('h2', {}, title), note(description)]),
			E('div', { class: 'tyf-refresh-controls' }, [updated, button])]), warning, live, form || null]);
}

return baseclass.extend({ note: note, link: link, tile: tile, grid: grid, fold: fold, bytes: bytes,
	paths: paths, services: services, authentication: authentication, diagnostics: diagnostics, ports: ports,
	snapshot: snapshot, networks: networks, page: page });
