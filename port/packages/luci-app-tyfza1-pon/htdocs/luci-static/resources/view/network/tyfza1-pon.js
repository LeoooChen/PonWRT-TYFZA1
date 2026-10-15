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

function authentication(data) {
	var mode = (data.serdes || {}).mode;
	if (!status.modes[mode]) return E('p', { class: 'tyf-note' }, '线路协议尚未就绪，暂无认证状态。');
	var labels = { waiting: '等待认证', challenge: '正在认证', accepted: '认证通过', rejected: '认证拒绝' };
	var ports = data.oam_ports || [];
	if (ituModes.indexOf(mode) !== -1) {
		var mac = data.mac || {}, activation = data.activation || {}, path = data.data_path || {};
		var live = data.omci && data.omci.live, omci = live ? data.omci : {};
		return E('div', {}, [status.rows([
			['激活状态', status.value(mac.activation)], ['ONU-ID', status.value(mode === 'gpon' ? mac.onu_id : activation.omcc_onu_id)],
			['OMCI 状态', status.badge(live ? '有效' : '不可用', live ? 'good' : 'danger')],
			['业务通道数', status.value(path.paths)], ['OMCI 收 / 发', status.value(omci.rx) + ' / ' + status.value(omci.tx)],
			['OMCI 错误码', status.value(omci.error)], ['业务错误码', status.value(path.error)]
		]), E('p', { class: 'tyf-note' }, 'O5 表示光链路已激活；业务是否可用仍取决于 OLT 下发和 WAN 配置。')]);
	}
	if (!ports.length) return E('p', { class: 'tyf-note' }, '暂无 OAM 认证会话，等待逻辑链路注册。');
	return E('div', { class: 'tyf-sessions' }, ports.map(function(port) {
		var accepted = port.live && port.registered && port.discovery_stable && port.auth_state === 'accepted';
		var text = !port.live ? '会话已失效' : !port.registered ? '等待注册' :
			port.auth_state === 'accepted' && !accepted ? '等待 OAM 发现' : labels[port.auth_state] || '等待状态';
		return E('section', { class: 'tyf-session' }, [
			E('div', { class: 'tyf-session-head' }, [E('strong', {}, 'LLID ' + port.llid_index), status.badge(text,
				accepted ? 'good' : port.live && port.registered && port.auth_state === 'rejected' ? 'danger' : 'muted')]),
			status.rows([['注册', port.live && port.registered ? '已注册' : '未注册'],
				['OAM 发现', port.live && port.discovery_stable ? '已完成' : '未完成'],
				['接收 / 发送帧', status.value(port.rx_frames) + ' / ' + status.value(port.tx_frames)]])]);
	}));
}

function diagnostics(data) {
	var sections = ['service', 'state', 'serdes', 'optical', 'bosa', 'mac', 'activation', 'mpcp', 'oam_ports', 'omci', 'data_path', 'fec', 'traffic', 'fe', 'deepq', 'puc', 'pdc', 'llids'];
	return sections.map(function(name) {
		var entry = name === 'data_path' ? data.data_path_raw : data[name];
		return '[' + name + ']\n' + (entry == null ? '未提供' : Array.isArray(entry) ? JSON.stringify(entry, null, 2) : typeof entry === 'object'
			? Object.keys(entry).map(function(key) { return key + ': ' + entry[key]; }).join('\n') : entry);
	}).join('\n\n');
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('pon'), status.callStatus().catch(function() { return null; }), uci.load('firewall'), uci.load('network')]);
	},
	render: function(data) {
		var m = new form.Map('pon', '线路配置', '仅填写运营商要求的参数。保存并应用后生效，光链路可能短暂中断。');
		m.chain('firewall');
		var s = m.section(form.NamedSection, 'pon', 'pon');
		s.tab('link', '光链路');
		s.tab('identity', '运营商认证', '认证参数均可选填。切换协议只改变显示的字段，不会清除其他协议的认证信息。');
		s.tab('wan', '上网接口');
		s.tab('advanced', '高级参数');
		var o = s.taboption('link', form.Flag, 'enabled', '启用光链路');
		o.rmempty = false;
		o = s.taboption('link', form.ListValue, 'mode', '线路协议');
		Object.keys(status.modes).forEach(function(mode) { o.value(mode, status.modes[mode]); });
		o.rmempty = false;
		o.description = '按运营商线路选择；顶部显示设备实际运行的协议，不随未应用的选择改变。';
		o = s.taboption('advanced', form.Value, 'llid_count', 'EPON 逻辑链路数');
		o.datatype = 'and(uinteger,range(1,32))';
		o.write = function(section, value) { uci.set('pon', section, 'llid_count', String(Number(value))); };
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
		o.description = '使用本机原厂身份时无需填写；清空自定义值后，应用即可恢复原厂身份。';
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
			o = s.taboption('wan', form.DummyValue, '_wan_protocol', 'WAN 配置协议');
			o.cfgvalue = function() { return { dhcp: 'DHCP', pppoe: 'PPPoE' }[protocol] || protocol || '未配置'; };
			var device = uci.get('network', 'wan', 'device') || '';
			o = s.taboption('wan', form.DummyValue, '_wan_device', 'WAN 配置设备');
			o.cfgvalue = function() { return device || '未配置'; };
		}
		o = s.taboption('wan', form.DummyValue, '_network_settings', '配置上网接口');
		o.rawhtml = true;
		o.description = '这里仅展示配置值，不代表实时连接状态。拨号、IPv6、VLAN 和桥接统一在原生接口管理中设置。';
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
		var error = E('p', { class: 'alert-message warning', role: 'status', hidden: !!data[1] }, '状态读取失败，尚无实时数据。可重试读取，配置编辑不受影响。');
		var live = E('div', {}, data[1] ? status.ponCard(data[1]) : null);
		var auth = E('div', {}, data[1] ? authentication(data[1]) : '暂无实时数据');
		var offload = E('div', {}, data[1] ? status.offloadCard(data[1]) : null);
		var raw = E('pre', {}, data[1] ? diagnostics(data[1]) : '暂无数据');
		var lastUpdated = data[1] ? new Date() : null, pending = null;
		var updated = E('span', { class: 'tyf-updated' });
		function showTime() {
			updated.textContent = lastUpdated ? '更新于 ' + lastUpdated.toLocaleTimeString() + ' · 每 5 秒刷新' : '等待首次读取';
		}
		function refresh() {
			if (pending) return pending;
			refreshButton.disabled = true;
			refreshButton.textContent = '读取中…';
			pending = status.callStatus().then(function(next) {
				error.hidden = true;
				live.replaceChildren(status.ponCard(next));
				auth.replaceChildren(authentication(next));
				offload.replaceChildren(status.offloadCard(next));
				raw.textContent = diagnostics(next);
				lastUpdated = new Date();
			}, function() {
				error.hidden = false;
				error.textContent = lastUpdated ? '刷新失败，以下状态为上次成功读取的数据，并非当前状态。配置编辑不受影响。' :
					'状态读取失败，尚无实时数据。可重试读取，配置编辑不受影响。';
			}).finally(function() {
				pending = null;
				refreshButton.disabled = false;
				refreshButton.textContent = '刷新状态';
				showTime();
			});
			return pending;
		}
		var refreshButton = E('button', { type: 'button', class: 'cbi-button', click: refresh }, '刷新状态');
		showTime();
		poll.add(refresh, 5);
		return m.render().then(function(form) {
			return E('div', { class: 'tyf-pon' }, [status.style(),
				E('div', { class: 'tyf-page-header' }, [
					E('div', {}, [E('p', { class: 'tyf-eyebrow' }, 'TYFZA1 / 光网络'), E('h2', {}, 'PON 管理')]),
					E('div', { class: 'tyf-refresh-controls' }, [updated, refreshButton])]),
				error, live,
				E('div', { class: 'tyf-workspace' }, [E('section', { class: 'tyf-config' }, form),
					E('aside', { class: 'tyf-runtime', 'aria-label': '实时注册与转发状态' }, [
						status.card('注册与认证', null, auth), offload,
						E('p', { class: 'tyf-note tyf-optical-note' }, '当前驱动未提供收发光功率、温度和电压读数；此页不显示推算值。')])]),
				E('details', { class: 'tyf-diagnostics' }, [E('summary', {}, '高级诊断 · 驱动原始状态'),
					E('p', { class: 'tyf-note' }, ['保留原始错误码，便于定位驱动和业务下发问题。',
						E('a', { href: L.url('admin/status/logs') }, '打开系统日志 →')]), raw])]);
		});
	}
});
