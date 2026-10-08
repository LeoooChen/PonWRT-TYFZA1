'use strict';
'require view';
'require form';
'require rpc';
'require poll';
'require uci';

var callStatus = rpc.declare({object: 'luci.tyfza1-pon', method: 'status', expect: {}});
var modes = [
	['epon', 'EPON（1G）'],
	['epon-d10g', '10G EPON（下行 10G / 上行 1G）'],
	['epon-bi10g', '10G EPON（下行 10G / 上行 10G）'],
	['gpon', 'GPON（MAC 适配中）'],
	['xgpon', 'XG-PON（MAC 适配中）'],
	['xgspon', 'XGS-PON（MAC 适配中）']
];

function value(source, key) {
	return source && source[key] != null ? source[key] : '—';
}

function statusPanel(data) {
	var rows = [
		['当前模式', value(data.serdes, 'mode')],
		['接收失光（LOS）', value(data.serdes, 'rx_los')],
		['10G PCS 同步', value(data.state, 'pcs10g_sync')],
		['1G PCS 同步', value(data.state, 'pcs1g_sync')],
		['光前端型号', value(data.bosa, 'model')],
		['光前端校准', value(data.bosa, 'configured')],
		['光前端错误码', value(data.bosa, 'error')],
		['MAC 初始化 / 错误码', value(data.mac, 'prepared') + ' / ' + value(data.mac, 'error')],
		['已注册 LLID 位图', value(data.mpcp, 'registered')],
		['MPCP 注册尝试', value(data.mpcp, 'attempts')],
		['OAM 收发', value(data.traffic, 'rx') + ' / ' + value(data.traffic, 'tx')],
		['LAN 接收错误 / 丢弃', (data.lan_rx_errors ?? '—') + ' / ' + (data.lan_rx_dropped ?? '—')]
	];
	return E('div', {class: 'cbi-section'}, [
		E('h3', {}, '实时状态'),
		E('table', {class: 'table'}, rows.map(function(row) {
			return E('tr', {class: 'tr'}, [E('td', {class: 'td'}, row[0]), E('td', {class: 'td'}, row[1])]);
		})),
		E('details', {}, [E('summary', {}, '队列与 LLID 状态'),
			E('pre', {}, (data.puc ?? '') + '\n' + (data.pdc ?? '') + '\n' + (data.llids ?? ''))])
	]);
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('pon'), callStatus()]);
	},
	render: function(data) {
		var m = new form.Map('pon', 'PON 管理');
		var s = m.section(form.NamedSection, 'pon', 'pon');
		s.tab('link', '光链路');
		s.tab('identity', '认证与设备身份');
		s.tab('wan', '上网方式');
		var o = s.taboption('link', form.Flag, 'enabled', '启用 PON');
		o.rmempty = false;
		o = s.taboption('link', form.ListValue, 'mode', '协议（手动选择）');
		modes.forEach(function(mode) { o.value(mode[0], mode[1]); });
		o.rmempty = false;
		o = s.taboption('link', form.Value, 'llid_count', 'LLID 数量');
		o.datatype = 'range(1,32)';
		o.default = '1';
		['epon', 'epon-d10g', 'epon-bi10g'].forEach(function(mode) { o.depends('mode', mode); });
		o = s.taboption('identity', form.Value, 'onu_mac', 'ONU MAC');
		o.datatype = 'macaddr';
		[
			['loid', 'LOID', 24, false],
			['loid_password', 'LOID 密码', 12, true],
			['serial_number', 'GPON 序列号', 16, false],
			['registration_id', 'Registration ID', 36, true],
			['ploam_password', 'PLOAM 密码', 10, true],
			['vendor_id', '厂商 ID', 4, false],
			['equipment_id', '设备型号', 20, false],
			['hardware_version', '硬件版本', 16, false],
			['software_version', '软件版本', 16, false]
		].forEach(function(field) {
			o = s.taboption('identity', form.Value, field[0], field[1]);
			o.datatype = 'maxlength(' + field[2] + ')';
			o.password = field[3];
		});
		o = s.taboption('wan', form.ListValue, 'wan_protocol', '上游地址获取');
		o.value('dhcp', 'DHCP');
		o.value('pppoe', 'PPPoE');
		o.value('bridge', '桥接');
		o.default = 'dhcp';
		o = s.taboption('wan', form.Value, 'wan_vlan', '业务 VLAN ID');
		o.datatype = 'range(1,4094)';
		o.placeholder = '由 OLT 业务配置确定';
		var status = E('div', {}, statusPanel(data[1]));
		poll.add(function() {
			return callStatus().then(function(next) { status.replaceChildren(statusPanel(next)); });
		}, 5);
		return m.render().then(function(form) { return E('div', {}, [status, form]); });
	}
});
