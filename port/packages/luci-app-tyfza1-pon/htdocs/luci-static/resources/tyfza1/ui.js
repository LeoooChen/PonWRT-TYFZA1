'use strict';
'require baseclass';
'require poll';
'require rpc';
'require tyfza1.status as status';

var board = rpc.declare({ object: 'system', method: 'board', expect: { '': {} }, reject: true });
var interfaces = rpc.declare({ object: 'network.interface', method: 'dump', expect: { 'interface': [] }, reject: true });
var radios = rpc.declare({ object: 'luci-rpc', method: 'getWirelessDevices', expect: { '': {} }, reject: true });

var portNames = { lan1: 'LAN 1', lan2: 'LAN 2', lan3: 'LAN 3', lan4: 'LAN 4', pon0: 'PON 光口' };
var protoNames = { dhcp: 'DHCP', dhcpv6: 'DHCPv6', pppoe: 'PPPoE', static: '静态地址' };
var wifiModes = { ap: 'AP 接入点', sta: 'STA 客户端', mesh: '802.11s Mesh', monitor: '监听' };

function note(text) { return E('p', { class: 'tyf-note' }, [text]); }

function link(label, path, primary) {
	return E('a', {
		class: 'cbi-button tyf-btn' + (primary ? ' tyf-btn-primary' : ''),
		href: L.url.apply(L, path.split('/'))
	}, [label]);
}

function tile(label, val, hint, tone) {
	return E('div', { class: 'tyf-tile', 'data-tone': tone || null }, [
		E('dt', {}, [label]),
		E('dd', {}, [status.value(val)]),
		hint ? note(hint) : null
	]);
}

function grid(items) { return E('div', { class: 'tyf-grid' }, items.filter(Boolean)); }
function grid2(items) { return E('div', { class: 'tyf-grid-2' }, items.filter(Boolean)); }
function metrics(items) { return E('div', { class: 'tyf-metrics' }, items.filter(Boolean)); }

function subcard(title, tagText, stateBadge, body) {
	var bodyNodes = body == null ? [] : Array.isArray(body) ? body.filter(Boolean) : [body];
	return E('div', { class: 'tyf-subcard' }, [
		E('div', { class: 'tyf-subcard-head' }, [
			E('div', { class: 'tyf-subcard-title' }, [
				E('span', {}, [title]),
				tagText ? E('span', { class: 'tyf-tag' }, [tagText]) : null
			]),
			stateBadge
		])
	].concat(bodyNodes));
}

function fold(title, key, contents) {
	var body = Array.isArray(contents) ? contents.filter(Boolean) : [contents];
	return E('details', { class: 'tyf-fold', 'data-tyf-fold': key }, [
		E('summary', {}, [title]),
		E('div', {}, body)
	]);
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

function formatGemVlan(vlan) {
	if (vlan === '65533') return '带标签（任意 VID）';
	if (vlan === '65534') return '无标签（Untagged）';
	if (vlan === '65535') return '透传（任意）';
	return status.value(vlan);
}

function services(data, multicastOnly) {
	var available = paths(data).filter(function(path) { return !multicastOnly || path.multicast === '1'; });
	return available.length ? E('div', { class: 'tyf-subgrid' }, available.map(function(path) {
		var isMcast = path.multicast === '1';
		var upstream = isMcast || (path.tcont === '0' && path.alloc === '65535')
			? '仅下行' : status.value(path.alloc) + ' / ' + status.value(path.tcont);
		return subcard('GEM ' + path.gem, path.index != null ? '槽位 ' + path.index : null,
			status.badge(isMcast ? '组播' : '单播', isMcast ? 'info' : 'good'),
			status.rows([
				['匹配 VLAN', formatGemVlan(path.vlan)],
				['Alloc-ID / T-CONT', upstream],
				['硬件队列', status.value(path.queue)],
				['下行加密 / 上行 AES', path.upstream_aes == null ? '未提供' : path.upstream_aes === '1' ? '已启用' : '未启用']
			]));
	})) : note(multicastOnly
		? '尚未取得 OMCI 组播通道。不代表线路没有 IPTV；EPON 业务与本地 VLAN 请在原生接口管理中核对。'
		: '尚无可展示的 OMCI GEM 业务通道。EPON 使用逻辑链路（LLID），不使用 GEM。');
}

function authentication(data) {
	var mode = (data.serdes || {}).mode;
	if (!status.modes[mode]) return note('线路协议尚未就绪，暂无注册与认证状态。');
	if (['gpon', 'xgpon', 'xgspon'].indexOf(mode) !== -1) {
		var mac = data.mac || {}, activation = data.activation || {}, path = data.data_path || {};
		var live = status.omciLive(data), omci = live ? data.omci : {};
		var auth = omci.auth == null ? '未提供' : +omci.auth === 1 ? '认证通过'
			: +omci.auth > 1 ? '认证未通过（状态码 ' + omci.auth + '）' : '等待 OLT 认证结果';
		var securityRow = mode === 'gpon'
			? (activation.ds_fec != null ? ['下行 FEC', status.flag(activation.ds_fec) ? '已启用' : '未启用'] : null)
			: (activation.key_ready != null ? ['下行会话密钥', status.flag(activation.key_ready) ? '已就绪' : '协商中'] : null);
		return E('div', { class: 'tyf-stack' }, [
			status.rows([
				['激活状态', status.value(mac.activation)],
				['ONU-ID', status.value(mode === 'gpon' ? mac.onu_id : activation.omcc_onu_id)],
				['OMCI 状态', status.badge(live ? '有效' : '不可用', live ? 'good' : 'danger')],
				['OLT LOID 认证', auth],
				['业务通道数', status.value(path.paths)],
				['OMCI 收 / 发', status.value(omci.rx) + ' / ' + status.value(omci.tx)],
				mac.rx != null || mac.tx != null ? ['PLOAM 收 / 发', status.value(mac.rx) + ' / ' + status.value(mac.tx)] : null,
				securityRow,
				['OMCI / 业务错误码', status.value(omci.error) + ' / ' + status.value(path.error)]
			], true),
			note('O5 表示光链路已激活；互联网与业务是否可达仍取决于 OLT 通道下发及 WAN 配置。')
		]);
	}
	var labels = { waiting: '等待认证', challenge: '正在认证', accepted: '认证通过', rejected: '认证拒绝' };
	var mpcp = data.mpcp || {}, fec = data.fec || {};
	var summaryRows = [
		mpcp.llids != null || mpcp.attempts != null
			? ['逻辑链路 / 注册尝试', status.value(mpcp.llids) + ' / ' + status.value(mpcp.attempts)] : null,
		fec.upstream != null ? ['上行 FEC', status.flag(fec.upstream) ? '已启用' : '未启用'] : null
	].filter(Boolean);
	var sessionNodes = (data.oam_ports || []).length ? E('div', { class: 'tyf-subgrid' }, data.oam_ports.map(function(port) {
		var accepted = port.live && port.registered && port.discovery_stable && port.auth_state === 'accepted';
		var rejected = port.live && port.registered && port.auth_state === 'rejected';
		var text = !port.live ? '会话已失效' : !port.registered ? '等待注册' :
			port.auth_state === 'accepted' && !accepted ? '等待 OAM 发现' : labels[port.auth_state] || '等待状态';
		var discovery = !port.live || !port.discovery_stable ? '未完成'
			: port.extension_selected ? 'CTC 扩展就绪' : '基础发现完成';
		return subcard('LLID ' + port.llid_index, port.loid_configured ? '已填 LOID' : null,
			status.badge(text, accepted ? 'good' : rejected ? 'danger' : port.live && port.registered ? 'warn' : 'muted'),
			status.rows([
				['MPCP 注册', port.live && port.registered ? '已注册' : '未注册'],
				['OAM 发现', discovery],
				['接收 / 发送帧', status.value(port.rx_frames) + ' / ' + status.value(port.tx_frames)],
				port.management_error ? ['管理错误码', String(port.management_error)] : null
			]));
	})) : note('暂无 OAM 认证会话，正在等待 MPCP 逻辑链路注册。');
	return summaryRows.length ? E('div', { class: 'tyf-stack' }, [status.rows(summaryRows, true), sessionNodes]) : sessionNodes;
}

function diagnostics(data) {
	return ['service', 'state', 'serdes', 'optical', 'transceiver', 'bosa', 'mac', 'activation',
		'mpcp', 'oam_ports', 'omci', 'data_path', 'fec', 'traffic', 'fe', 'deepq', 'puc', 'pdc', 'llids'].map(function(name) {
		var entry = name === 'data_path' ? data.data_path_raw : data[name];
		return '[' + name + ']\n' + (entry == null ? '未提供' : Array.isArray(entry) ? JSON.stringify(entry, null, 2)
			: typeof entry === 'object' ? Object.keys(entry).map(function(key) { return key + ': ' + entry[key]; }).join('\n') : entry);
	}).join('\n\n');
}

function formatPortSpeed(port, data) {
	var up = port.present && port.carrier === '1';
	if (!port.present) return '设备未就绪';
	if (!up) return '未插线 / 无载波';
	if (port.device === 'pon0') return status.modes[((data || {}).serdes || {}).mode] || '光口数据通道';
	var speed = Number(port.speed);
	if (!(speed > 0)) return '已连接';
	var rate = speed >= 1000 && speed % 100 === 0 ? (speed / 1000) + ' Gbps' : speed + ' Mbps';
	var duplex = port.duplex === 'full' ? ' · 全双工' : port.duplex === 'half' ? ' · 半双工' : '';
	return rate + duplex;
}

function ports(data) {
	var list = (data || {}).ports || [];
	if (!list.length) return note('尚未取得物理端口统计数据。');
	return E('div', { class: 'tyf-port-grid' }, list.map(function(port) {
		var up = port.present && port.carrier === '1', stats = port.statistics || {};
		var issues = (Number(stats.rx_dropped) || 0) + (Number(stats.tx_dropped) || 0) +
			(Number(stats.rx_errors) || 0) + (Number(stats.tx_errors) || 0);
		return E('div', { class: 'tyf-port', 'data-up': up ? 'true' : 'false' }, [
			E('div', { class: 'tyf-port-top' }, [
				E('div', { class: 'tyf-port-name' }, [
					E('span', {}, [portNames[port.device] || port.device]),
					portNames[port.device] ? E('span', { class: 'tyf-port-dev' }, [port.device]) : null
				]),
				status.badge(!port.present ? '不存在' : up ? '已连接' : '未连接', up ? 'good' : 'muted')
			]),
			E('div', { class: 'tyf-port-rate' }, [formatPortSpeed(port, data)]),
			E('div', { class: 'tyf-port-traffic' }, [
				E('div', { class: 'tyf-port-row' }, [E('span', {}, ['▼ 接收']), E('span', {}, [bytes(stats.rx_bytes)])]),
				E('div', { class: 'tyf-port-row' }, [E('span', {}, ['▲ 发送']), E('span', {}, [bytes(stats.tx_bytes)])]),
				E('div', { class: 'tyf-port-row' + (issues > 0 ? ' tyf-port-warn' : '') }, [
					E('span', {}, ['丢包 RX / TX']),
					E('span', {}, [status.value(stats.rx_dropped) + ' / ' + status.value(stats.tx_dropped)])
				])
			])
		]);
	}));
}

function snapshot() {
	return Promise.all([
		status.callStatus().catch(function() { return null; }),
		board().catch(function() { return {}; }),
		interfaces().catch(function() { return null; }),
		radios().catch(function() { return null; })
	]).then(function(data) {
		var wifi = [];
		Object.keys(data[3] || {}).forEach(function(name) {
			var radio = data[3][name], rcfg = radio.config || {};
			(radio.interfaces || []).forEach(function(iface) {
				var info = iface.iwinfo || {}, icfg = iface.config || {};
				var band = rcfg.band === '5g' ? '5 GHz' : rcfg.band === '2g' ? '2.4 GHz'
					: info.frequency >= 4900 ? '5 GHz' : info.frequency > 0 ? '2.4 GHz' : null;
				wifi.push({
					name: iface.ifname || iface.section || name,
					ssid: info.ssid || icfg.ssid || icfg.mesh_id,
					band: band,
					mode: wifiModes[icfg.mode] || info.mode || null,
					htmode: info.htmode || rcfg.htmode || null,
					txpower: typeof info.txpower === 'number' ? info.txpower : null,
					signal: typeof info.signal === 'number' && info.signal > -256 && info.signal < 0 ? info.signal : null,
					up: radio.up && !radio.disabled && !!iface.ifname,
					channel: info.channel || rcfg.channel,
					frequency: info.frequency > 0 ? (info.frequency / 1000).toFixed(3) : null,
					bitrate: info.bitrate > 0 ? info.bitrate / 1000 : null
				});
			});
		});
		return {
			pon: data[0], board: data[1], wifi: wifi, networkError: !Array.isArray(data[2]), wifiError: data[3] == null,
			networks: (Array.isArray(data[2]) ? data[2] : []).map(function(net) {
				function addresses(key) { return (net[key] || []).map(function(addr) { return addr.address + '/' + addr.mask; }); }
				var ipv6 = addresses('ipv6-address');
				(net['ipv6-prefix-assignment'] || []).forEach(function(prefix) {
					var local = prefix && prefix['local-address'];
					if (local) ipv6.push(local.address + '/' + local.mask);
				});
				return {
					name: net.interface, proto: net.proto, up: net.up, device: net.l3_device || net.device,
					ipv4: addresses('ipv4-address'), ipv6: Array.from(new Set(ipv6))
				};
			})
		};
	});
}

function networks(data) {
	if (data.networkError) return note('网络状态读取失败；未将历史数据作为实时连接状态。');
	var items = (data.networks || []).filter(function(net) { return net.name !== 'loopback'; });
	if (!items.length) return note('当前无可展示的业务网络接口。');
	return E('div', { class: 'tyf-subgrid' }, items.map(function(net) {
		var protoLabel = protoNames[net.proto] || status.value(net.proto);
		return subcard(net.name.toUpperCase(), protoLabel,
			status.badge(net.up ? '接口已连接' : '未连接', net.up ? 'good' : 'muted'),
			status.rows([
				['承载设备', status.mono(net.device)],
				['IPv4 地址', net.ipv4.length ? status.mono(net.ipv4.join(' · ')) : '—'],
				['IPv6 地址', net.ipv6.length ? status.mono(net.ipv6.join(' · ')) : '—']
			]));
	}));
}

function wireless(data) {
	if (data.wifiError) return note('无线状态读取失败。');
	if (!(data.wifi || []).length) return note('未发现无线接口；请检查无线配置与原厂校准加载状态。');
	return E('div', { class: 'tyf-subgrid' }, data.wifi.map(function(wifi) {
		var chanText = status.value(wifi.channel) +
			(wifi.frequency ? ' · ' + wifi.frequency + ' GHz' : '') +
			(wifi.htmode ? '（' + wifi.htmode + '）' : '');
		return subcard(wifi.ssid || wifi.name, wifi.band,
			status.badge(wifi.up ? '运行中' : '未运行', wifi.up ? 'good' : 'muted'),
			status.rows([
				['模式 / 接口', (wifi.mode ? wifi.mode + ' · ' : '') + wifi.name],
				['信道 / 频率', chanText],
				['功率上限 / 速率', (wifi.txpower != null ? wifi.txpower + ' dBm' : '自动') + ' · ' + (wifi.bitrate ? wifi.bitrate + ' Mbit/s' : '—')],
				wifi.signal != null ? ['上级信号', wifi.signal + ' dBm'] : null
			]));
	}));
}

function page(title, description, initial, read, render, form) {
	var last = initial == null ? null : new Date(), pending;
	var warning = E('p', { class: 'alert-message warning', role: 'status', hidden: initial != null },
		'状态读取失败，尚无实时数据。配置编辑不受影响。');
	var live = E('div', { class: 'tyf-live' }, initial == null ? [] : [render(initial)]);
	var updated = E('span', { class: 'tyf-updated' });
	var button = E('button', { type: 'button', class: 'cbi-button tyf-btn', click: refresh }, '刷新状态');
	function showTime() {
		updated.textContent = last ? '更新于 ' + last.toLocaleTimeString() + ' · 5 秒刷新' : '等待首次读取';
	}
	function refresh() {
		if (pending) return pending;
		button.disabled = true;
		pending = read().then(function(next) {
			var opened = {};
			live.querySelectorAll('details[data-tyf-fold]').forEach(function(node) {
				opened[node.getAttribute('data-tyf-fold')] = node.open;
			});
			live.replaceChildren(render(next));
			live.querySelectorAll('details[data-tyf-fold]').forEach(function(node) {
				node.open = !!opened[node.getAttribute('data-tyf-fold')];
			});
			warning.hidden = true;
			last = new Date();
		}).catch(function() {
			warning.hidden = false;
			warning.textContent = last
				? '刷新失败，以下状态为上次成功读取的数据，并非当前状态。配置编辑不受影响。'
				: '状态读取失败，尚无实时数据。';
		}).finally(function() {
			pending = null;
			button.disabled = false;
			showTime();
		});
		return pending;
	}
	showTime();
	poll.add(refresh, 5);
	return E('div', { class: 'tyf-pon cbi-map' }, [
		status.style(),
		E('div', { class: 'tyf-page-header' }, [
			E('div', { class: 'tyf-page-title' }, [E('h2', {}, [title]), description ? note(description) : null]),
			E('div', { class: 'tyf-refresh-controls' }, [updated, button])
		]),
		warning,
		live,
		form || null
	]);
}

return baseclass.extend({
	note: note, link: link, tile: tile, grid: grid, grid2: grid2, metrics: metrics, subcard: subcard, fold: fold,
	bytes: bytes, paths: paths, services: services, authentication: authentication, diagnostics: diagnostics,
	ports: ports, snapshot: snapshot, networks: networks, wireless: wireless, page: page
});
