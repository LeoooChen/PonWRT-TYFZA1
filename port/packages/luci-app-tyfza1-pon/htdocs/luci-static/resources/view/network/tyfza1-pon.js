'use strict';
'require view';
'require form';
'require tyfza1.ui as ui';
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

return view.extend({
	load: function() {
		return Promise.all([uci.load('pon'), status.callStatus().catch(function() { return null; }), uci.load('network')]);
	},
	render: function(data) {
		var m = new form.Map('pon', '线路配置', '仅填写运营商要求的参数。保存并应用后生效，光链路可能短暂中断。');
		if (!L.hasViewPermission()) m.readonly = true;
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
		o = s.taboption('advanced', form.ListValue, 'omcc_version', 'OMCC 版本');
		o.value('0xb0', '0xb0（默认）');
		o.value('0x86', '0x86（兼容模式）');
		o.default = '0xb0';
		o.rmempty = false;
		o.retain = true;
		o.description = '通常保持默认。OLT 在 OMCI 配置阶段不兼容时，可尝试 0x86。';
		ituModes.forEach(function(mode) { o.depends('mode', mode); });
		o = s.taboption('advanced', form.Flag, 'disable_enhanced_security', '禁用 OMCI 增强认证（Class 332）');
		o.default = '0';
		o.rmempty = false;
		o.retain = true;
		o.description = '默认不禁用。仅在线路不支持增强认证时开启，与 OMCC 版本独立设置。';
		ituModes.forEach(function(mode) { o.depends('mode', mode); });
		o = s.taboption('advanced', form.Value, 'llid_count', 'EPON 逻辑链路数');
		o.datatype = 'and(uinteger,range(1,32))';
		o.write = function(section, value) { uci.set('pon', section, 'llid_count', String(Number(value))); };
		o.default = '1';
		o.rmempty = false;
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
		o.description = '留空时优先使用原厂 ONU MAC；若原厂未提供，则使用本机基础 MAC。';
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
			['software_version', '软件版本', 16, false, eponModes.concat(ituModes)],
			['operator_id', 'OMCI 运营商 ID', 4, false, ituModes]
		].forEach(function(field, index) {
			o = s.taboption(index < 5 ? 'identity' : 'advanced', form.Value, field[0], field[1]);
			o.validate = byteLimit(field[2]);
			o.password = field[3];
			o.retain = true;
			o.placeholder = index < 5 ? '选填' : '留空使用原厂值';
			field[4].forEach(function(mode) { o.depends('mode', mode); });
			if (field[0] === 'hardware_version' || field[0] === 'software_version') {
				o.validate = function(section, text) {
					var mode = this.section.formvalue(section, 'mode') || uci.get('pon', section, 'mode');
					return byteLimit(ituModes.indexOf(mode) !== -1 ? 14 : 16)(section, text);
				};
				o.description = 'ITU-T 模式最多 14 字节，EPON 模式最多 16 字节。';
			}
			if (field[0] === 'operator_id') {
				o.placeholder = '留空使用默认值';
				o.description = '运营商有要求时填写，最多 4 字节。';
			}
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
		o = s.taboption('advanced', form.DummyValue, '_acceleration', '硬件流量卸载');
		o.cfgvalue = function() { return ui.link('打开硬件加速设置', 'admin/onu/acceleration'); };
		return m.render().then(function(form) {
			return ui.page('线路与认证', '使用 TYFZA1 原生配置。只修改运营商要求的参数，应用时光链路可能短暂中断。',
				data[1], status.callStatus, function(next) {
					return E('div', {}, [status.ponCard(next), ui.fold('查看实时注册与认证', 'auth', ui.authentication(next))]);
				}, E('section', { class: 'tyf-config' }, form));
		});
	}
});
