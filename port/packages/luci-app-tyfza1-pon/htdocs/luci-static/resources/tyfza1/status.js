'use strict';
'require baseclass';
'require rpc';

var callStatus = rpc.declare({ object: 'luci.tyfza1-pon', method: 'status', expect: { '': {} }, reject: true });

var modes = {
	epon: 'EPON', 'epon-d10g': '10G EPON（非对称）', 'epon-bi10g': '10G EPON（对称）',
	gpon: 'GPON', xgpon: 'XG-PON', xgspon: 'XGS-PON'
};

var bosaModels = {
	'201': 'Semtech GN25L95', '202': 'Semtech GN25L98', '203': 'Semtech GN28L95',
	'204': 'Semtech GN28L96', '205': 'Semtech GN28L97'
};

function value(v) { return v == null || v === '' ? '—' : String(v); }
function flag(v) { return v === '1' || v === 1 || v === true; }
function mono(v) { return v == null || v === '' ? '—' : E('span', { class: 'tyf-mono' }, [String(v)]); }
function badge(text, tone) { return E('span', { class: 'tyf-badge tyf-' + (tone || 'muted') }, [text]); }

function bosaName(bosa) {
	var m = (bosa || {}).model;
	if (m == null || m === '' || m === '0' || m === 0) return '未识别';
	return bosaModels[String(m)] || ('型号 ' + m);
}

function opticalPower(raw, zeroText) {
	if (raw == null || !/^\d+$/.test(String(raw))) return null;
	var n = Number(raw);
	if (n === 0) return zeroText || '无光';
	var dbm = 10 * Math.log10(n / 10000);
	return (dbm >= 0 ? '+' : '') + dbm.toFixed(2) + ' dBm';
}

function opticalTemp(raw) {
	return raw != null && /^-?\d+$/.test(String(raw)) ? (Number(raw) / 256).toFixed(1) + ' °C' : null;
}

function opticalVoltage(raw) {
	return raw != null && /^\d+$/.test(String(raw)) ? (Number(raw) / 10000).toFixed(2) + ' V' : null;
}

function opticalBias(raw) {
	return raw != null && /^\d+$/.test(String(raw)) ? (Number(raw) * 0.002).toFixed(1) + ' mA' : null;
}

function formatSerial(hex) {
	if (!/^[0-9a-f]{16}$/i.test(hex || '')) return value(hex);
	var vendor = '', printable = true;
	for (var i = 0; i < 8; i += 2) {
		var code = parseInt(hex.slice(i, i + 2), 16);
		if (code < 0x20 || code > 0x7e) { printable = false; break; }
		vendor += String.fromCharCode(code);
	}
	return printable ? vendor + hex.slice(8).toUpperCase() + '（' + hex.toLowerCase() + '）' : hex.toLowerCase();
}

function rows(items, divided) {
	return E('dl', { class: 'tyf-facts' + (divided ? ' tyf-facts-divided' : '') }, items.filter(Boolean).reduce(function(nodes, item) {
		return nodes.concat([E('dt', {}, [item[0]]), E('dd', {}, [item[1] == null ? '—' : item[1]])]);
	}, []));
}

function card(title, state, content, footer) {
	var body = content == null ? [] : Array.isArray(content) ? content.filter(Boolean) : [content];
	var foot = footer == null ? [] : Array.isArray(footer) ? footer.filter(Boolean) : [footer];
	return E('section', { class: 'tyf-card' }, [
		E('div', { class: 'tyf-card-head' }, [E('h3', {}, [title]), state]),
		E('div', { class: 'tyf-card-body' }, body),
		foot.length ? E('div', { class: 'tyf-card-foot' }, foot) : null
	]);
}

function omciLive(data) {
	var omci = data.omci, path = data.data_path || {};
	return !!(omci && omci.live && (omci.session == null || path.session == null || String(omci.session) === String(path.session)));
}

function ponState(data) {
	var serdes = data.serdes || {}, mac = data.mac || {}, mpcp = data.mpcp || {}, optical = data.optical || {};
	if (data.service && data.service.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	var failed = ['service', 'activation', 'bosa', 'serdes', 'mac', 'mpcp'].find(function(name) {
		return data[name] && data[name].error != null && +data[name].error !== 0;
	});
	if (failed) {
		var stage = failed === 'service' && data.service.stage ? '（阶段 ' + data.service.stage + '）' : '';
		return { text: '初始化异常', tone: 'danger', help: failed + stage + ' 错误码：' + data[failed].error + '。请查看诊断页与系统日志。' };
	}
	if (flag(optical.module_absent))
		return { text: '光模块不在位', tone: 'danger', help: '驱动检测到光模块缺席标志，请检查硬件状态。' };
	if (flag(optical.tx_fault))
		return { text: '光模块发射故障', tone: 'danger', help: '光模块报告 TX Fault，请检查诊断页与硬件校准状态。' };
	if (flag(optical.rx_los) || flag(serdes.rx_los))
		return { text: '接收失光', tone: 'danger', help: '未检测到接收光信号，请检查光纤连接、尾纤弯曲与上联线路。' };
	if (!modes[serdes.mode] || serdes.configured == null)
		return { text: '状态不可用', tone: 'muted', help: '驱动尚未提供有效线路状态，请查看系统日志。' };
	if (serdes.configured === '0' || mac.enabled === '0' || /^epon/.test(serdes.mode) && mpcp.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	if (/^(gpon|xgpon|xgspon)$/.test(serdes.mode)) {
		var businessError = ['data_path', 'omci'].find(function(name) {
			return data[name] && (name !== 'omci' || omciLive(data)) && data[name].error != null && +data[name].error !== 0;
		});
		if (businessError)
			return { text: '业务配置异常', tone: 'danger', help: businessError + ' 错误码：' + data[businessError].error + '。请查看诊断页。' };
		if (!omciLive(data))
			return { text: 'OMCI 状态不可用', tone: 'danger', help: '未取得存活控制进程的最新状态；历史数据保留在诊断页中，请检查系统日志。' };
		if (+data.omci.auth > 1)
			return { text: '认证未通过', tone: 'danger', help: 'OLT 返回 LOID 认证状态码：' + data.omci.auth + '。请核对运营商认证参数。' };
		return mac.activation === 'O5' && flag(mac.prepared)
			? flag((data.data_path || {}).configured)
				? { text: '已激活 · 业务已配置', tone: 'good' }
				: { text: '已激活 · 等待业务下发', tone: 'warn' }
			: { text: '等待激活', tone: 'warn', help: '当前激活阶段：' + value(mac.activation) };
	}
	if (!flag(mpcp.enabled) || !/^[0-9a-f]+$/i.test(mpcp.registered || '') || !parseInt(mpcp.registered, 16))
		return { text: '等待注册', tone: 'warn' };
	var ports = (data.oam_ports || []).filter(function(p) { return p.live && p.registered; });
	if (ports.some(function(p) { return p.auth_state === 'rejected'; }))
		return { text: '认证未通过', tone: 'danger', help: 'OLT 拒绝了 OAM 认证，请核对 LOID 和密码。' };
	if (ports.some(function(p) { return p.auth_state === 'accepted' && p.discovery_stable; }))
		return { text: '已注册 · 认证通过', tone: 'good' };
	return { text: '已注册 · 等待认证', tone: 'warn' };
}

function ponCard(data) {
	var state = ponState(data), optical = data.optical || {}, serdes = data.serdes || {}, ddm = data.transceiver || {};
	var isLos = flag(serdes.rx_los) || flag(optical.rx_los);
	var los = isLos ? '接收失光' : (serdes.rx_los != null || optical.rx_los != null ? '未失光' : '未提供');
	var rxPower = opticalPower(ddm.rx_power_raw, '无光');
	var txPower = opticalPower(ddm.tx_power_raw, flag(optical.tx_disabled) ? '已关断' : '待突发');
	var temp = opticalTemp(ddm.temperature_raw);
	var moduleState = flag(optical.module_absent) ? '模块不在位' :
		flag(optical.tx_fault) ? '发射故障' :
		flag(optical.tx_disabled) ? '发射已关闭' :
		optical.tx_fault != null ? '无发射故障' + (temp ? ' · ' + temp : '') : (temp || '未提供');
	return E('section', { class: 'tyf-link-panel', 'data-tone': state.tone }, [
		E('div', { class: 'tyf-link-state' }, [
			E('p', { class: 'tyf-kicker' }, ['PON 实时光链路', modes[serdes.mode] ? ' · ' + modes[serdes.mode] : '']),
			E('h3', {}, [E('span', { class: 'tyf-state-dot', 'aria-hidden': 'true' }), state.text]),
			E('p', { class: 'tyf-note' }, [state.help || '光路注册与认证不等同于互联网连通，请在接口管理或上网配置中确认 WAN 地址状态。'])
		]),
		E('div', { class: 'tyf-link-facts' }, [rows([
			['运行协议', modes[serdes.mode] || value(serdes.mode)],
			['接收光信号', los],
			rxPower || txPower ? ['收 / 发光功率', (rxPower || '—') + ' / ' + (txPower || '—')] : null,
			['发射与模块', moduleState]
		])])
	]);
}

function offloadCard(data, footer) {
	var fe = data.fe || {}, available = flag(fe.available), enabled = flag(fe.enabled);
	var error = fe.error != null && +fe.error !== 0;
	var label = error ? '运行异常' : fe.available == null ? '状态未知' : !available ? '不可用' :
		fe.enabled == null ? '状态未知' : enabled ? '已启用' : '未启用';
	var tone = error ? 'danger' : available && enabled ? 'good' : 'muted';
	var flowDetail = value(fe.flows) + (fe.counted_flows != null && fe.counter_slots != null
		? '（计费槽 ' + fe.counted_flows + ' / ' + fe.counter_slots + '）' : '');
	var bridgeDetail = fe.bridge_rules != null
		? fe.bridge_rules + (fe.learned_bridge_rules != null ? '（学习 ' + fe.learned_bridge_rules + '）' : '') : null;
	return card('硬件流量卸载', badge(label, tone), [
		rows([
			['活跃硬件流', flowDetail],
			bridgeDetail != null ? ['二层桥接规则', bridgeDetail] : null,
			['累计安装 / 释放', value(fe.installs) + ' / ' + value(fe.deletes)]
		], true),
		error ? E('p', { class: 'tyf-note' }, ['FE 错误码：' + fe.error]) : null
	], footer);
}

function style() {
	var href = L.resource('tyfza1/status.css') + '?v=16b53a533329';
	if (typeof document !== 'undefined' && document.head) {
		var headLink = document.head.querySelector('link[data-tyfza1-style]');
		if (!headLink)
			document.head.appendChild(E('link', { rel: 'stylesheet', href: href, 'data-tyfza1-style': '1' }));
		else if (headLink.getAttribute('href') !== href)
			headLink.setAttribute('href', href);
	}
	return E('link', { rel: 'stylesheet', href: href });
}

return baseclass.extend({
	modes: modes, bosaModels: bosaModels, bosaName: bosaName,
	value: value, flag: flag, mono: mono, badge: badge,
	opticalPower: opticalPower, opticalTemp: opticalTemp, opticalVoltage: opticalVoltage, opticalBias: opticalBias,
	formatSerial: formatSerial, rows: rows, card: card,
	omciLive: omciLive, ponState: ponState, ponCard: ponCard, offloadCard: offloadCard,
	// Track the CSS content independently of LuCI's version across sysupgrades.
	style: style,
	callStatus: callStatus
});
