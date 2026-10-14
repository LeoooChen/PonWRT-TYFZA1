'use strict';
'require view';
'require form';
'require poll';
'require uci';
'require tyfza1.status as status';

var eponModes = ['epon', 'epon-d10g', 'epon-bi10g'];
var ituModes = ['gpon', 'xgpon', 'xgspon'];

function byteLimit(limit) {
	return function(section, text) {
		if (/[\x00-\x1f\x7f]/.test(text)) return '不能包含控制字符。';
		return new TextEncoder().encode(text).length <= limit || '最多 ' + limit + ' 字节。';
	};
}

function livePanel(data) {
	return E('div', { class: 'tyf-grid' }, [status.ponCard(data, false), status.offloadCard(data)]);
}

function authentication(data) {
	var labels = { waiting: '等待认证', challenge: '正在认证', accepted: '认证通过', rejected: '认证拒绝' };
	var ports = data.oam_ports || [];
	if (!/^epon/.test((data.serdes || {}).mode || ''))
		return status.rows([['激活状态', status.value((data.mac || {}).activation)], ['ONU-ID', status.value((data.mac || {}).onu_id)]]);
	if (!ports.length) return E('p', { class: 'tyf-note' }, '暂无认证会话');
	return E('div', { class: 'tyf-grid' }, ports.map(function(port) {
		var accepted = port.live && port.registered && port.discovery_stable && port.auth_state === 'accepted';
		var text = !port.live ? '会话已失效' : !port.registered ? '等待注册' :
			port.auth_state === 'accepted' && !accepted ? '等待 OAM 发现' : labels[port.auth_state] || '等待状态';
		return status.card('LLID ' + port.llid_index, status.badge(text,
			accepted ? 'good' : port.live && port.registered && port.auth_state === 'rejected' ? 'danger' : 'muted'),
			status.rows([['注册', port.live && port.registered ? '已注册' : '未注册'],
				['OAM 发现', port.live && port.discovery_stable ? '已完成' : '未完成'],
				['接收 / 发送帧', status.value(port.rx_frames) + ' / ' + status.value(port.tx_frames)]]));
	}));
}

function diagnostics(data) {
	var sections = ['service', 'state', 'serdes', 'optical', 'bosa', 'mac', 'activation', 'mpcp', 'fec', 'traffic', 'fe', 'deepq', 'puc', 'pdc', 'llids'];
	return sections.map(function(name) {
		var entry = data[name];
		return '[' + name + ']\n' + (entry == null ? '未提供' : typeof entry === 'object'
			? Object.keys(entry).map(function(key) { return key + ': ' + entry[key]; }).join('\n') : entry);
	}).join('\n\n');
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('pon'), status.callStatus().catch(function() { return null; }), uci.load('firewall'), uci.load('network')]);
	},
	render: function(data) {
		var m = new form.Map('pon', 'PON 配置', '应用光链路配置可能短暂中断上网。');
		m.chain('firewall');
		var s = m.section(form.NamedSection, 'pon', 'pon');
		s.tab('link', '光链路');
		s.tab('identity', '运营商认证');
		s.tab('wan', '上网设置');
		s.tab('advanced', '高级设置');
		var o = s.taboption('link', form.Flag, 'enabled', '启用光链路');
		o.rmempty = false;
		o = s.taboption('link', form.ListValue, 'mode', '线路协议');
		Object.keys(status.modes).forEach(function(mode) { o.value(mode, status.modes[mode]); });
		o.rmempty = false;
		o.description = '选择运营商使用的线路协议。';
		o = s.taboption('advanced', form.Value, 'llid_count', 'EPON 逻辑链路数');
		o.datatype = 'range(1,32)';
		o.default = '1';
		o.retain = true;
		o.description = '通常为 1。';
		eponModes.forEach(function(mode) { o.depends('mode', mode); });
		o = s.taboption('identity', form.Value, 'onu_mac', 'ONU MAC 地址');
		o.datatype = 'macaddr';
		o.validate = function(section, text) {
		return text === '' || /^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$/i.test(text) &&
			!(parseInt(text.slice(0, 2), 16) & 1) && !/^00(?::00){5}$/.test(text) || '请输入非零的单播 MAC 地址，或留空使用原厂身份。';
	};
		o.retain = true;
		o.placeholder = '留空使用原厂 MAC';
		[
			['loid', 'LOID', 24, false, eponModes.concat(ituModes)],
			['loid_password', 'LOID 密码', 12, true, eponModes.concat(ituModes)],
			['serial_number', 'ONU 序列号（SN）', 16, false, ituModes],
			['registration_id', 'Registration ID', 36, true, ['xgpon', 'xgspon']],
			['ploam_password', 'PLOAM 密码', 10, true, ['gpon']],
			['vendor_id', '厂商 ID', 4, false, eponModes],
			['oam_model', 'OAM 型号标识', 4, false, eponModes],
			['equipment_id', '设备型号', 20, false, eponModes.concat(ituModes)],
			['hardware_version', '硬件版本', 16, false, eponModes.concat(ituModes)],
			['software_version', '软件版本', 16, false, eponModes.concat(ituModes)]
		].forEach(function(field, index) {
			o = s.taboption(index < 5 ? 'identity' : 'advanced', form.Value, field[0], field[1]);
			o.validate = byteLimit(field[2]);
			o.password = field[3];
			o.retain = true;
			o.placeholder = index < 5 ? '选填' : '留空使用原厂值';
			field[4].forEach(function(mode) { o.depends('mode', mode); });
			if (field[0] === 'serial_number') {
				o.placeholder = 'ABCD12345678';
				o.description = '留空使用原厂 SN；支持 12 字符 SN 或 16 位十六进制。';
				o.validate = function(section, text) { return text === '' || /^(?:[\x20-\x7e]{4}[0-9a-fA-F]{8}|[0-9a-fA-F]{16})$/.test(text) || '请输入有效的 12 字符 SN 或 16 位十六进制 SN。'; };
			}
		});
		o = s.taboption('identity', form.Value, 'omci_psk', 'OMCI 增强认证 PSK');
		o.password = true;
		o.retain = true;
		o.placeholder = '选填';
		o.description = '运营商要求增强认证时填写 32 位十六进制密钥。';
		o.validate = function(section, text) { return text === '' || /^[0-9a-f]{32}$/i.test(text) || '请输入 32 位十六进制密钥。'; };
		ituModes.forEach(function(mode) { o.depends('mode', mode); });
		if (uci.get('network', 'wan', '.type') === 'interface') {
			var protocol = uci.get('network', 'wan', 'proto');
			o = s.taboption('wan', form.DummyValue, '_wan_protocol', '当前连接方式');
			o.cfgvalue = function() { return { dhcp: 'DHCP', pppoe: 'PPPoE' }[protocol] || protocol || '未配置'; };
			var device = uci.get('network', 'wan', 'device') || '';
			o = s.taboption('wan', form.DummyValue, '_wan_device', '当前业务设备');
			o.cfgvalue = function() { return device || '未配置'; };
		}
		o = s.taboption('wan', form.DummyValue, '_network_settings', '配置上网接口');
		o.rawhtml = true;
		o.description = '在接口管理中配置拨号、IPv6 和 VLAN。';
		o.cfgvalue = function() { return E('a', { href: L.url('admin/network/network') }, '打开接口管理 →'); };
		var defaults = uci.sections('firewall', 'defaults')[0];
		if (defaults) {
			o = s.taboption('advanced', form.Flag, 'hardware_offload', '启用硬件流量卸载');
			o.description = '同时启用软件流量卸载。';
			o.uciconfig = 'firewall';
			o.ucisection = defaults['.name'];
			o.ucioption = 'flow_offloading_hw';
			o.rmempty = false;
			o.write = function(section, enabled) {
				uci.set('firewall', defaults['.name'], 'flow_offloading_hw', enabled);
				if (enabled === '1') uci.set('firewall', defaults['.name'], 'flow_offloading', '1');
			};
		}
		var error = E('p', { class: 'alert-message warning', role: 'status', hidden: !!data[1] }, '状态读取失败，请刷新页面。');
		var live = E('div', {}, data[1] ? livePanel(data[1]) : null);
		var auth = E('div', {}, data[1] ? authentication(data[1]) : null);
		var raw = E('pre', {}, data[1] ? diagnostics(data[1]) : '暂无数据');
		var updated = E('p', { class: 'tyf-note' }, '每 5 秒自动刷新');
		poll.add(function() {
			return status.callStatus().then(function(next) {
				error.hidden = true;
				live.replaceChildren(livePanel(next));
				auth.replaceChildren(authentication(next));
				raw.textContent = diagnostics(next);
				updated.textContent = '更新于 ' + new Date().toLocaleTimeString();
			}, function() {
				error.hidden = false;
				error.textContent = '刷新失败，显示上次数据。';
			});
		}, 5);
		return m.render().then(function(form) {
			return E('div', { class: 'tyf-dashboard' }, [status.style(), E('h2', {}, 'PON 管理'), error, live, updated, form,
				E('h3', { class: 'tyf-section-title' }, '认证状态'), auth,
				E('details', { class: 'tyf-diagnostics' }, [E('summary', {}, '高级诊断'), raw])]);
		});
	}
});
