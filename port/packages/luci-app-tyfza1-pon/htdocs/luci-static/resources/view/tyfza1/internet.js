'use strict';
'require view';
'require form';
'require uci';
'require tools.widgets as widgets';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('network'), ui.snapshot()]); },
	render: function(data) {
		var m = new form.Map('network', 'WAN 参数', '直接编辑原生 network.wan，不新建业务配置、不接管 LAN 端口或防火墙。应用沿用 LuCI 的连接确认与回滚流程。');
		if (!L.hasViewPermission()) m.readonly = true;
		var proto = uci.get('network', 'wan', 'proto'), ipaddr = uci.get('network', 'wan', 'ipaddr');
		var editable = uci.get('network', 'wan', '.type') === 'interface' && ['dhcp', 'pppoe', 'static'].indexOf(proto) !== -1 &&
			!!uci.get('network', 'wan', 'device') && !Array.isArray(ipaddr) && !/\//.test(ipaddr || '');
		if (editable) {
			var s = m.section(form.NamedSection, 'wan', 'interface'), o;
			s.tab('general', '上网方式'); s.tab('advanced', '高级参数');
			o = s.taboption('general', form.ListValue, 'proto', '协议');
			o.value('dhcp', 'DHCP / IPoE'); o.value('pppoe', 'PPPoE'); o.value('static', '静态地址'); o.rmempty = false;
			o = s.taboption('general', widgets.DeviceSelect, 'device', 'WAN 设备');
			o.nobridges = false; o.optional = false; o.rmempty = false; o.network = 'wan'; o.exclude = '@wan';
			o.description = '保留当前设备选择；如需创建 VLAN 或配置桥接，请使用下方原生接口管理。';
			[['username', 'PPPoE 用户名', 'pppoe'], ['password', 'PPPoE 密码', 'pppoe'],
				['ipaddr', 'IPv4 地址', 'static', 'ip4addr("nomask")'], ['netmask', '子网掩码', 'static', 'ip4addr("true")'], ['gateway', 'IPv4 网关', 'static', 'ip4addr("nomask")']].forEach(function(field) {
				o = s.taboption('general', form.Value, field[0], field[1]); o.depends('proto', field[2]); o.retain = true;
				if (field[3]) o.datatype = field[3];
				if (field[0] === 'ipaddr' || field[0] === 'netmask') o.rmempty = false;
				if (field[0] === 'password') o.password = true;
			});
			o = s.taboption('advanced', form.Value, 'mtu', 'MTU'); o.datatype = 'and(uinteger,range(68,9200))';
			o.placeholder = proto === 'pppoe' ? '1492' : '1500';
			o = s.taboption('advanced', form.ListValue, 'ipv6', 'PPPoE IPv6 协商');
			o.value('auto', '自动'); o.value('0', '关闭'); o.value('1', '手动'); o.default = 'auto'; o.depends('proto', 'pppoe'); o.retain = true;
			o.description = '只控制 PPP 会话的 IPv6 协商，不删除或重写 wan6；复杂双栈配置请使用原生接口管理。';
		}
		else {
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}
		return m.render().then(function(form) {
			return ui.page('上网配置', '查看接口实时地址，配置现有 WAN。桥接、VLAN、IPv6 与防火墙高级设置仍由原生 OpenWrt 管理。', data[1], ui.snapshot,
				function(next) { return ui.networks(next); }, E('div', {}, [E('section', { class: 'tyf-config' }, editable ? form :
					ui.note('当前 WAN 需要原生接口管理：未配置 WAN、自定义协议、旧式设备引用或多地址/CIDR 配置均保持原样。')),
					E('div', { class: 'tyf-actions' }, [ui.link('接口 / VLAN / 桥接', 'admin/network/network'), ui.link('防火墙', 'admin/network/firewall')]) ]));
		});
	}
});
