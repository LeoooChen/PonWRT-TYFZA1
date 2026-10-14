'use strict';
'require baseclass';
'require rpc';
'require network';

var callStatus = rpc.declare({ object: 'luci.tyfza1-pon', method: 'status', expect: { '': {} }, reject: true });
var callInterfaces = rpc.declare({ object: 'network.interface', method: 'dump', reject: true });
var callWireless = rpc.declare({ object: 'luci-rpc', method: 'getWirelessDevices', reject: true });
var modes = {
	epon: 'EPON', 'epon-d10g': '10G EPON（非对称）', 'epon-bi10g': '10G EPON（对称）',
	gpon: 'GPON', xgpon: 'XG-PON', xgspon: 'XGS-PON'
};

function value(v) { return v == null || v === '' ? '—' : String(v); }
function flag(v) { return v === '1' || v === 1 || v === true; }
function badge(text, tone) { return E('span', { class: 'tyf-badge tyf-' + (tone || 'muted') }, text); }
function link(text, path) { return E('a', { href: L.url.apply(L, path.split('/')) }, text); }
function rows(items) {
	return E('dl', { class: 'tyf-facts' }, items.reduce(function(nodes, item) {
		return nodes.concat([E('dt', {}, item[0]), E('dd', {}, item[1] == null ? '—' : item[1])]);
	}, []));
}
function card(title, state, content, key) {
	return E('section', { class: 'tyf-card', 'data-key': key }, [E('div', { class: 'tyf-card-head' }, [E('h3', {}, title), state]), content]);
}
function bytes(v) {
	if (v == null || v === '' || !Number.isFinite(+v) || +v < 0) return '—';
	var n = +v, units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0;
	while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
	return n.toFixed(i ? 1 : 0) + ' ' + units[i];
}

// Keep unchanged text, links and open details in place between status polls.
function update(current, next) {
	if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) {
		current.replaceWith(next);
		return;
	}
	if (current.nodeType === 3) {
		if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
		return;
	}
	Array.from(current.attributes).forEach(function(attr) {
		if (current.nodeName === 'DETAILS' && attr.name === 'open') return;
		if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
	});
	Array.from(next.attributes).forEach(function(attr) {
		if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
	});
	var children = Array.from(next.childNodes);
	var keys = new Set(children.filter(function(child) { return child.nodeType === 1; })
		.map(function(child) { return child.getAttribute('data-key'); }));
	var keyed = new Map();
	Array.from(current.children).forEach(function(child) {
		if (!child.hasAttribute('data-key')) return;
		var key = child.getAttribute('data-key');
		if (keys.has(key)) keyed.set(key, child);
		else child.remove();
	});
	children.forEach(function(child, index) {
		var old = current.childNodes[index];
		var key = child.nodeType === 1 ? child.getAttribute('data-key') : null;
		if (key != null) {
			var match = keyed.get(key);
			if (!match) {
				current.insertBefore(child, old || null);
				return;
			}
			if (match !== old) current.insertBefore(match, old || null);
			old = match;
		}
		else if (!old || old.nodeType === 1 && old.hasAttribute('data-key')) {
			current.insertBefore(child, old || null);
			return;
		}
		update(old, child);
	});
	while (current.childNodes.length > children.length) current.lastChild.remove();
}

function ponState(data) {
	var serdes = data.serdes || {}, mac = data.mac || {}, mpcp = data.mpcp || {};
	if (data.service && data.service.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	if (!data.serdes) return { text: '状态不可用', tone: 'muted', help: '请检查系统日志。' };
	if ([data.service, data.activation, data.bosa, serdes, mac].some(function(s) { return s && s.error != null && +s.error !== 0; }))
		return { text: '初始化异常', tone: 'danger', help: '请查看高级诊断。' };
	if (flag((data.optical || {}).rx_los) || flag(serdes.rx_los))
		return { text: '光纤未连接', tone: 'danger', help: '请检查光纤连接。' };
	if (!flag(serdes.configured) || mac.enabled === '0' || /^epon/.test(serdes.mode) && mpcp.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	if (/^(gpon|xgpon|xgspon)$/.test(serdes.mode))
		return mac.activation === 'O5' && flag(mac.prepared)
			? flag((data.data_path || {}).configured)
				? { text: '已激活 · 业务已配置', tone: 'good' }
				: { text: '已激活 · 等待业务下发', tone: 'warn' }
			: { text: '等待激活', tone: 'warn', help: '激活状态：' + value(mac.activation) };
	if (!flag(mpcp.enabled) || !/^[0-9a-f]+$/i.test(mpcp.registered || '') || !parseInt(mpcp.registered, 16))
		return { text: '等待注册', tone: 'warn' };
	var ports = (data.oam_ports || []).filter(function(p) { return p.live && p.registered; });
	if (ports.some(function(p) { return p.auth_state === 'rejected'; }))
		return { text: '认证未通过', tone: 'danger', help: '请核对 LOID 和密码。' };
	if (ports.some(function(p) { return p.auth_state === 'accepted' && p.discovery_stable; }))
		return { text: '已注册 · 认证通过', tone: 'good' };
	return { text: '已注册 · 等待认证', tone: 'warn' };
}

function ponCard(data, showLink) {
	var state = ponState(data), optical = data.optical || {}, serdes = data.serdes || {};
	return card('PON 光链路', badge(state.text, state.tone), E('div', {}, [
		rows([['线路协议', modes[serdes.mode] || value(serdes.mode)],
			['接收光信号', serdes.rx_los == null ? '—' : (flag(serdes.rx_los) ? '失光' : '未失光')],
			['光模块告警', optical.tx_fault == null ? '—' : (flag(optical.tx_fault) ? '发射故障' : '无发射故障')]]),
		state.help ? E('p', { class: 'tyf-note' }, state.help) : null,
		showLink === false ? null : link('管理光链路 →', 'admin/network/pon')
	]), 'pon');
}

function offloadCard(data) {
	var fe = data.fe || {}, available = flag(fe.available), enabled = flag(fe.enabled);
	var error = available && fe.error != null && +fe.error !== 0;
	return card('硬件转发', badge(error ? '运行异常' : !available ? '不可用' : enabled ? '已启用' : '未启用',
		error ? 'danger' : available && enabled ? 'good' : 'muted'), E('div', {}, [
		rows([['当前硬件流', value(fe.flows)], ['累计安装 / 释放', value(fe.installs) + ' / ' + value(fe.deletes)]])
	]), 'offload');
}

function portCards(data) {
	return E('div', { class: 'tyf-grid tyf-ports' }, (data.ports || []).map(function(port) {
		var up = port.present && flag(port.carrier), stats = port.statistics || {};
		var pon = port.device === 'pon-data0';
		var speed = !up ? '—' : +port.speed > 0 ? (+port.speed >= 1000 ? (+port.speed / 1000) + ' Gbit/s' : port.speed + ' Mbit/s') : '—';
		return card(pon ? 'PON 业务口' : 'LAN · ' + port.device,
			badge(!port.present ? '未检测到' : port.carrier == null ? '状态未知' : up ? '链路已连接' : '链路未连接', up ? 'good' : 'muted'),
			E('div', {}, [
				rows([[pon ? '业务链路速率' : '协商速率', speed]]),
				E('details', { class: 'tyf-port-details' }, [E('summary', {}, '详情'),
					rows([['系统设备', port.device],
						['双工模式', !up ? '—' : port.duplex === 'full' ? '全双工' : port.duplex === 'half' ? '半双工' : '—'],
						['累计收 / 发', bytes(stats.rx_bytes) + ' / ' + bytes(stats.tx_bytes)],
						['收 / 发错误', value(stats.rx_errors) + ' / ' + value(stats.tx_errors)]])])
			]), 'port:' + port.device);
	}));
}

function interfaceCard(net, runtime) {
	var active = (runtime || []).find(function(entry) { return entry.interface === net.getName(); }) || {};
	var addresses = net.getIPAddrs().concat(net.getIP6Addrs());
	return card(net.getName().toUpperCase(), badge(active.up ? '接口已启动' : '未连接', active.up ? 'good' : 'muted'), E('div', {}, [
		rows([['运行协议 / 设备', value(active.proto) + ' / ' + (active.l3_device || active.device || '未分配')],
			['IP 地址', addresses.length ? addresses.join(' · ') : '尚未获取'],
			['网关', net.getGatewayAddr() || net.getGateway6Addr() || '—'],
			['DNS', net.getDNSAddrs().concat(net.getDNS6Addrs()).join(' · ') || '—']]),
		link('接口设置 →', 'admin/network/network')
	]), 'interface:' + net.getName());
}

function interfaceCards(nets, runtime) {
	return E('div', { class: 'tyf-grid' }, nets.filter(function(net) { return net.getName() !== 'loopback'; }).map(function(net) {
		return interfaceCard(net, runtime);
	}));
}

function wifiCards(wifis) {
	if (!wifis.length) return E('p', { class: 'tyf-note' }, '暂无无线网络');
	return E('div', { class: 'tyf-grid' }, wifis.map(function(wifi) {
		var disabled = wifi.isDisabled(), up = !disabled && wifi.isUp();
		var frequency = +wifi.getFrequency();
		var band = frequency >= 2.4 && frequency < 2.5 ? '2.4 GHz' : frequency >= 4.9 && frequency < 5.925 ? '5 GHz' :
			frequency >= 5.925 && frequency < 7.125 ? '6 GHz' :
			({ '2g': '2.4 GHz', '5g': '5 GHz', '6g': '6 GHz' }[wifi.ubus('dev', 'config', 'band')] || '无线网络');
		return card(E('span', { class: 'tyf-wifi-title' }, [E('span', { class: 'tyf-band' }, band),
			E('span', {}, wifi.getActiveSSID() || wifi.getName())]),
			badge(disabled ? '已关闭' : up ? '运行中' : '未就绪', up ? 'good' : 'muted'), E('div', {}, [
			rows([['信道', value(wifi.getChannel())],
				['加密方式', up ? value(wifi.getActiveEncryption()) : '—'],
				['无线设备', wifi.getWifiDeviceName()]]),
			link('无线设置 →', 'admin/network/wireless')
		]), 'wifi:' + wifi.getName());
	}));
}

return baseclass.extend({
	modes: modes, value: value, badge: badge, rows: rows, card: card, bytes: bytes, update: update,
	ponState: ponState, ponCard: ponCard, offloadCard: offloadCard, portCards: portCards,
	interfaceCards: interfaceCards, wifiCards: wifiCards,
	style: function() { return E('link', { rel: 'stylesheet', href: L.resource('tyfza1/status.css') }); },
	callStatus: callStatus,
	load: function() {
		return network.flushCache().catch(function() { return null; }).then(function() {
			return Promise.all([
				callStatus().then(function(data) { return { data: data }; }, function() { return { error: 'PON 和端口状态读取失败，请刷新页面。' }; }),
				// The LuCI network model substitutes empty data on RPC failure. Probe
				// availability so an RPC outage is not presented as disconnected ports.
				Promise.all([callInterfaces(), callWireless()]).then(function(state) {
					if (!state[0] || !Array.isArray(state[0].interface)) throw new Error('Missing interface status');
					return Promise.all([network.getNetworks(), network.getWifiNetworks(), state[0].interface]);
				}).then(function(data) {
					return { networks: data[0], wifis: data[1], runtime: data[2] };
				}, function() { return { error: '网络和无线状态读取失败，请刷新页面。' }; })
			]);
		});
	},
	renderOverview: function(data) {
		var pon = data[0], net = data[1];
		var uplinks = [], local = [];
		(net.networks || []).forEach(function(iface) {
			if (iface.getName() === 'loopback') return;
			var active = (net.runtime || []).find(function(entry) { return entry.interface === iface.getName(); }) || {};
			var upstream = /^wan\d*$/i.test(iface.getName()) || (active.route || []).some(function(route) {
				return +route.mask === 0 && (route.target === '0.0.0.0' || route.target === '::');
			});
			(upstream ? uplinks : local).push(iface);
		});
		return E('div', { class: 'tyf-dashboard' }, [
			E('div', { 'data-key': 'errors' }, [
				pon.error ? E('p', { class: 'alert-message warning', role: 'status', 'data-key': 'pon-error' }, pon.error) : null,
				net.error ? E('p', { class: 'alert-message warning', role: 'status', 'data-key': 'network-error' }, net.error) : null]),
			E('div', { class: 'tyf-grid', 'data-key': 'uplinks' }, (pon.error ? [] : [ponCard(pon.data)]).concat(
				uplinks.map(function(iface) { return interfaceCard(iface, net.runtime); }))),
			net.error ? null : E('section', { 'data-key': 'wireless' }, [
				E('h3', { class: 'tyf-section-title' }, '无线网络'), wifiCards(net.wifis)]),
			pon.error ? null : E('section', { 'data-key': 'ports' }, [
				E('h3', { class: 'tyf-section-title' }, '有线端口'), portCards(pon.data)]),
			!net.error && local.length ? E('section', { 'data-key': 'local' }, [
				E('h3', { class: 'tyf-section-title' }, '其他网络接口'), interfaceCards(local, net.runtime)]) : null,
			pon.error ? null : E('div', { class: 'tyf-grid', 'data-key': 'forwarding' }, [offloadCard(pon.data)]),
			E('p', { class: 'tyf-note', 'data-key': 'refresh' }, '每 5 秒自动刷新')
		]);
	}
});
