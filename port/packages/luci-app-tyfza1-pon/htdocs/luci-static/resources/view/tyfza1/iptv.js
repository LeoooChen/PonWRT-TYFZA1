'use strict';
'require view';
'require form';
'require uci';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('network'), status.callStatus().catch(function() { return null; })]); },
	render: function(data) {
		var m = new form.Map('network', '现有网桥的组播设置', '直接使用 Linux bridge / netifd 的 IGMP snooping 与 querier 设置。不会自动摘除 LAN 口、创建业务 VLAN 或修改防火墙。');
		if (!L.hasViewPermission()) m.readonly = true;
		var bridges = uci.sections('network', 'device').filter(function(device) { return device.type === 'bridge'; });
		if (!bridges.length) this.handleSaveApply = this.handleSave = this.handleReset = null;
		bridges.forEach(function(bridge) {
			var s = m.section(form.NamedSection, bridge['.name'], 'device', bridge.name), o;
			o = s.option(form.DummyValue, '_ports', '桥成员');
			o.cfgvalue = function() { return L.toArray(bridge.ports).join(' · ') || '未配置'; };
			o = s.option(form.Flag, 'igmp_snooping', 'IGMP snooping'); o.default = '0';
			o.description = '根据组成员关系转发组播，避免向所有端口泛洪。';
			o = s.option(form.Flag, 'multicast_querier', '组播查询器'); o.rmempty = false;
			// netifd inherits snooping when the explicit querier option is absent.
			o.cfgvalue = function(section) {
				return uci.get('network', section, 'multicast_querier') || uci.get('network', section, 'igmp_snooping') || '0';
			};
			o.description = '仅在此网桥确实需要本机发送组播查询时启用。';
		});
		return m.render().then(function(form) {
			return ui.page('IPTV 与组播', '先核对 OLT 下发的组播通道，再配置本地业务 VLAN 和机顶盒端口。', data[1], status.callStatus,
				function(next) { return status.card('OLT 组播通道', null, ui.services(next, true)); }, E('div', {}, [
					E('section', { class: 'tyf-config' }, bridges.length ? form : ui.note('尚未发现原生 device 类型的网桥。旧式接口配置请在原生接口管理中编辑。')),
					ui.link('配置 IPTV VLAN / 专用口 / 单线复用', 'admin/network/network'),
					ui.note('专用口桥接与单线复用使用原生 VLAN/网桥配置。本包未安装 IGMP proxy、HTTP 组播中继或 SIP/H.248 语音服务，因此不提供无后端的开关。')
				]));
		});
	}
});
