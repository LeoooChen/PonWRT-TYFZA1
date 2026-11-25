'use strict';
'require view';
'require form';
'require uci';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('network'), status.callStatus().catch(function() { return null; })]); },
	render: function(data) {
		var m = new form.Map('network');
		if (!L.hasViewPermission()) m.readonly = true;
		var bridges = uci.sections('network', 'device').filter(function(device) { return device.type === 'bridge'; });
		if (!bridges.length) this.handleSaveApply = this.handleSave = this.handleReset = null;
		bridges.forEach(function(bridge, index) {
			var s = m.section(form.NamedSection, bridge['.name'], 'device',
				'网桥组播参数 · ' + (bridge.name || bridge['.name']),
				index === 0 ? '直接配置 Linux bridge / netifd 的 IGMP snooping 与组播查询器，不会自动摘除 LAN 口、创建业务 VLAN 或改动防火墙。' : null), o;
			o = s.option(form.DummyValue, '_ports', '当前桥成员');
			o.cfgvalue = function() { return L.toArray(bridge.ports).join(' · ') || '未配置'; };
			o = s.option(form.Flag, 'igmp_snooping', 'IGMP 侦听（Snooping）');
			o.default = '0';
			o.description = '按组成员关系按需转发组播流，避免在网桥内向所有有线与无线端口泛洪。';
			o = s.option(form.Flag, 'multicast_querier', '组播查询器（Querier）');
			o.rmempty = false;
			// netifd inherits snooping when the explicit querier option is absent.
			o.cfgvalue = function(section) {
				return uci.get('network', section, 'multicast_querier') || uci.get('network', section, 'igmp_snooping') || '0';
			};
			o.description = '仅当上联网络无组播查询器、需要由本网桥定期发送成员查询时开启。';
		});
		return m.render().then(function(formNode) {
			return ui.page('IPTV 与组播', '先核对 OLT 下发的组播通道，再按拓扑配置本地业务 VLAN、桥接与机顶盒端口。',
				data[1], status.callStatus,
				function(next) { return status.card('OLT 组播业务通道', null, ui.services(next, true)); },
				E('div', { class: 'tyf-stack' }, [
					bridges.length
						? E('section', { class: 'tyf-config' }, [formNode])
						: status.card('未发现原生网桥设备', status.badge('使用原生管理', 'info'),
							ui.note('尚未发现 device 类型的网桥定义；旧式接口网桥请在原生接口管理中查看与编辑。')),
					status.card('IPTV 业务拓扑与后端边界', null,
						ui.note('专用口透传桥接与单线复用均使用原生 802.1Q VLAN 与网桥配置。本包不包含无后端支撑的 SIP / H.248 语音开关、IGMP proxy 或组播转 HTTP 占位项；如需组播转单播可从软件源安装对应软件包。'),
						ui.link('配置 IPTV VLAN / 专用口 / 单线复用', 'admin/network/network', true))
				]));
		});
	}
});
