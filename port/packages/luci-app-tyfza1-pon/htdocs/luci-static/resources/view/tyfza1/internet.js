'use strict';
'require view';
'require form';
'require uci';
'require tools.widgets as widgets';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('network'), ui.snapshot()]); },
	render: function(data) {
		var m = new form.Map('network');
		if (!L.hasViewPermission()) m.readonly = true;
		var proto = uci.get('network', 'wan', 'proto'), ipaddr = uci.get('network', 'wan', 'ipaddr');
		var editable = uci.get('network', 'wan', '.type') === 'interface' && ['dhcp', 'pppoe', 'static'].indexOf(proto) !== -1 &&
			!!uci.get('network', 'wan', 'device') && !Array.isArray(ipaddr) && !/\//.test(ipaddr || '');
		if (editable) {
			var SelectValue = form.RichListValue || form.ListValue;
			var s = m.section(form.NamedSection, 'wan', 'interface', 'WAN 上网参数',
				'直接编辑原生 network.wan，不新建独立配置、不接管 LAN 端口或防火墙。保存并应用时沿用 LuCI 的连接确认与自动回滚流程。'), o;
			s.tab('general', '上网方式');
			s.tab('advanced', '高级参数');
			o = s.taboption('general', SelectValue, 'proto', '连接协议');
			o.value('dhcp', 'DHCP / IPoE 自动获取', '无需宽带账号密码，由上级网关或光网自动分配地址');
			o.value('pppoe', 'PPPoE 宽带拨号', '使用运营商宽带账号与密码由本机直接拨号认证');
			o.value('static', '静态 IPv4 地址', '手动指定固定 IPv4 地址、子网掩码与默认网关');
			o.rmempty = false;
			o = s.taboption('general', widgets.DeviceSelect, 'device', 'WAN 承载设备');
			o.nobridges = false;
			o.optional = false;
			o.rmempty = false;
			o.network = 'wan';
			o.exclude = '@wan';
			o.description = '默认光口业务设备为 pon0（带 VLAN 时如 pon0.VID）。如需新建 VLAN 子设备或桥接，请使用原生接口管理。';
			[
				['username', 'PPPoE 宽带账号', 'pppoe'],
				['password', 'PPPoE 宽带密码', 'pppoe'],
				['ipaddr', 'IPv4 地址', 'static', 'ip4addr("nomask")'],
				['netmask', '子网掩码', 'static', 'ip4addr("true")'],
				['gateway', 'IPv4 默认网关', 'static', 'ip4addr("nomask")']
			].forEach(function(field) {
				o = s.taboption('general', form.Value, field[0], field[1]);
				o.depends('proto', field[2]);
				o.retain = true;
				if (field[3]) o.datatype = field[3];
				if (field[0] === 'ipaddr' || field[0] === 'netmask') o.rmempty = false;
				if (field[0] === 'password') o.password = true;
			});
			o = s.taboption('advanced', form.Value, 'mtu', '自定义 MTU');
			o.datatype = 'and(uinteger,range(68,9200))';
			o.placeholder = proto === 'pppoe' ? '1492' : '1500';
			o = s.taboption('advanced', SelectValue, 'ipv6', 'PPPoE IPv6 协商');
			o.value('auto', '自动（配合 wan6）', '协商 IPv6CP 并自动派生 DHCPv6 下游接口');
			o.value('0', '关闭', '禁用此 PPPoE 会话的 IPv6 协商');
			o.value('1', '手动', '仅开启链路层 IPv6CP，自行维护接口配置');
			o.default = 'auto';
			o.depends('proto', 'pppoe');
			o.retain = true;
			o.description = '仅控制 PPP 会话内的 IPv6CP 协商，不会删除或重写 wan6；复杂双栈拓扑请使用原生接口管理。';
		}
		else {
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}
		return m.render().then(function(formNode) {
			return ui.page('上网配置', '查看业务接口实时地址，快捷配置现有 WAN。桥接、VLAN、多 WAN 与防火墙高级规则仍由原生 OpenWrt 管理。',
				data[1], ui.snapshot,
				function(next) {
					return status.card('业务网络实时状态', null, ui.networks(next), [
						ui.note('如需创建业务 VLAN（如 pon0.41）、配置桥接光猫模式或调整防火墙区域，请前往原生页面'),
						E('div', { class: 'tyf-actions' }, [
							ui.link('接口 / VLAN / 桥接', 'admin/network/network', !editable),
							ui.link('防火墙设置', 'admin/network/firewall')
						])
					]);
				},
				editable
					? E('section', { class: 'tyf-config' }, [formNode])
					: status.card('WAN 参数由原生接口管理维护', status.badge('保持原样', 'info'),
						ui.note('当前未配置 network.wan，或使用了自定义协议、旧式设备引用、多地址 / CIDR 列表配置。为避免覆盖复杂网络拓扑，请直接在原生接口管理中编辑。'),
						ui.link('打开原生接口管理', 'admin/network/network', true)));
		});
	}
});
