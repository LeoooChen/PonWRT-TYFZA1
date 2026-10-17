'use strict';
'require baseclass';
'require rpc';

var callStatus = rpc.declare({ object: 'luci.tyfza1-pon', method: 'status', expect: { '': {} }, reject: true });
var modes = {
	epon: 'EPON', 'epon-d10g': '10G EPON（非对称）', 'epon-bi10g': '10G EPON（对称）',
	gpon: 'GPON', xgpon: 'XG-PON', xgspon: 'XGS-PON'
};

function value(v) { return v == null || v === '' ? '—' : String(v); }
function flag(v) { return v === '1' || v === 1 || v === true; }
function badge(text, tone) { return E('span', { class: 'tyf-badge tyf-' + (tone || 'muted') }, [text]); }
function rows(items) {
	return E('dl', { class: 'tyf-facts' }, items.reduce(function(nodes, item) {
		return nodes.concat([E('dt', {}, [item[0]]), E('dd', {}, [item[1] == null ? '—' : item[1]])]);
	}, []));
}
function card(title, state, content) {
	return E('section', { class: 'tyf-card' }, [E('div', { class: 'tyf-card-head' }, [E('h3', {}, [title]), state])].concat(content == null ? [] : content));
}
function omciLive(data) {
	var omci = data.omci, path = data.data_path || {};
	return !!(omci && omci.live && (omci.session == null || path.session == null || String(omci.session) === String(path.session)));
}
function ponState(data) {
	var serdes = data.serdes || {}, mac = data.mac || {}, mpcp = data.mpcp || {};
	if (data.service && data.service.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	var failed = ['service', 'activation', 'bosa', 'serdes', 'mac', 'mpcp'].find(function(name) {
		return data[name] && data[name].error != null && +data[name].error !== 0;
	});
	if (failed)
		return { text: '初始化异常', tone: 'danger', help: failed + ' 错误码：' + data[failed].error + '。请查看高级诊断和系统日志。' };
	if (flag((data.optical || {}).tx_fault))
		return { text: '光模块发射故障', tone: 'danger', help: '光模块报告 TX Fault，请检查高级诊断。' };
	if (flag((data.optical || {}).rx_los) || flag(serdes.rx_los))
		return { text: '接收失光', tone: 'danger', help: '未检测到接收光信号，请检查光纤与上联线路。' };
	if (!modes[serdes.mode] || serdes.configured == null)
		return { text: '状态不可用', tone: 'muted', help: '驱动尚未提供有效线路状态，请查看系统日志。' };
	if (serdes.configured === '0' || mac.enabled === '0' || /^epon/.test(serdes.mode) && mpcp.enabled === '0')
		return { text: '未启用', tone: 'muted' };
	if (/^(gpon|xgpon|xgspon)$/.test(serdes.mode)) {
		var businessError = ['data_path', 'omci'].find(function(name) {
			return data[name] && (name !== 'omci' || omciLive(data)) && data[name].error != null && +data[name].error !== 0;
		});
		if (businessError)
			return { text: '业务配置异常', tone: 'danger', help: businessError + ' 错误码：' + data[businessError].error + '。请查看高级诊断。' };
		if (!omciLive(data))
			return { text: 'OMCI 状态不可用', tone: 'danger', help: '未取得存活控制进程的最新状态；历史数据保留在高级诊断中，请检查系统日志。' };
		if (+data.omci.auth > 1)
			return { text: '认证未通过', tone: 'danger', help: 'OLT 返回 LOID 认证状态码：' + data.omci.auth + '。请核对运营商认证参数。' };
		return mac.activation === 'O5' && flag(mac.prepared)
			? flag((data.data_path || {}).configured)
				? { text: '已激活 · 业务已配置', tone: 'good' }
				: { text: '已激活 · 等待业务下发', tone: 'warn' }
			: { text: '等待激活', tone: 'warn', help: '激活状态：' + value(mac.activation) };
	}
	if (!flag(mpcp.enabled) || !/^[0-9a-f]+$/i.test(mpcp.registered || '') || !parseInt(mpcp.registered, 16))
		return { text: '等待注册', tone: 'warn' };
	var ports = (data.oam_ports || []).filter(function(p) { return p.live && p.registered; });
	if (ports.some(function(p) { return p.auth_state === 'rejected'; }))
		return { text: '认证未通过', tone: 'danger', help: '请核对 LOID 和密码。' };
	if (ports.some(function(p) { return p.auth_state === 'accepted' && p.discovery_stable; }))
		return { text: '已注册 · 认证通过', tone: 'good' };
	return { text: '已注册 · 等待认证', tone: 'warn' };
}

function ponCard(data) {
	var state = ponState(data), optical = data.optical || {}, serdes = data.serdes || {};
	var los = flag(serdes.rx_los) || flag(optical.rx_los) ? '接收失光' :
		serdes.rx_los != null || optical.rx_los != null ? '未失光' : '未提供';
	return E('section', { class: 'tyf-link-panel', 'data-tone': state.tone }, [
		E('div', { class: 'tyf-link-state' }, [
			E('p', { class: 'tyf-eyebrow' }, 'PON / 实时光链路'),
			E('h3', {}, [E('span', { class: 'tyf-state-dot', 'aria-hidden': 'true' }), state.text]),
			E('p', { class: 'tyf-note' }, state.help || '线路注册与认证不等同于互联网连通，请在接口管理中确认 WAN 状态。')]),
		E('div', { class: 'tyf-link-facts' }, rows([
			['运行协议', modes[serdes.mode] || value(serdes.mode)],
			['接收光信号', los],
			['发射告警', optical.tx_fault == null ? '未提供' : flag(optical.tx_fault) ? '发射故障' : '无发射故障']
		]))
	]);
}

function offloadCard(data) {
	var fe = data.fe || {}, available = flag(fe.available), enabled = flag(fe.enabled);
	var error = fe.error != null && +fe.error !== 0;
	return card('硬件转发', badge(error ? '运行异常' : fe.available == null ? '状态未知' : !available ? '不可用' : fe.enabled == null ? '状态未知' : enabled ? '已启用' : '未启用',
		error ? 'danger' : available && enabled ? 'good' : 'muted'), E('div', {}, [
		rows([['当前硬件流', value(fe.flows)], ['累计安装 / 释放', value(fe.installs) + ' / ' + value(fe.deletes)]]),
		error ? E('p', { class: 'tyf-note' }, '错误码：' + fe.error) : null
	]));
}

return baseclass.extend({
	modes: modes, value: value, badge: badge, rows: rows, card: card,
	omciLive: omciLive, ponState: ponState, ponCard: ponCard, offloadCard: offloadCard,
	style: function() { return E('link', { rel: 'stylesheet', href: L.resource('tyfza1/status.css') }); },
	callStatus: callStatus
});
