'use strict';
'require view';
'require form';
'require uci';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('firewall'), status.callStatus().catch(function() { return null; })]); },
	render: function(data) {
		var m = new form.Map('firewall');
		if (!L.hasViewPermission()) m.readonly = true;
		var defaults = uci.sections('firewall', 'defaults')[0];
		if (defaults) {
			var s = m.section(form.NamedSection, defaults['.name'], 'defaults', '流量卸载开关',
				'联动原生 firewall4 与 RTL9617C FE / 双核 PE 硬件数据通路，覆盖有线及 Wi-Fi（AP / STA）转发，不改变接口或网桥拓扑。'), o;
			o = s.option(form.DummyValue, '_sw_offload', '软件流量卸载状态');
			o.cfgvalue = function(section) {
				return uci.get('firewall', section, 'flow_offloading') === '1'
					? '已启用' : '未启用（开启下方硬件卸载时将自动同步启用）';
			};
			o = s.option(form.Flag, 'flow_offloading_hw', '启用硬件流量卸载');
			o.rmempty = false;
			o.description = '开启时同步启用软件流量卸载；关闭硬件卸载不会主动关闭已开启的软件卸载。';
			o.write = function(section, enabled) {
				uci.set('firewall', section, 'flow_offloading_hw', enabled);
				if (enabled === '1') uci.set('firewall', section, 'flow_offloading', '1');
			};
			o = s.option(form.DummyValue, '_firewall_settings', '完整防火墙设置');
			o.cfgvalue = function() { return ui.link('打开原生防火墙设置', 'admin/network/firewall'); };
		}
		else {
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}
		return m.render().then(function(formNode) {
			return ui.page('硬件加速', '将配置开关与驱动实时状态分开核对。开启卸载后，仅满足加速条件的连接进入硬件流表。',
				data[1], status.callStatus, function(next) {
					var fe = next.fe || {}, deepq = next.deepq || {};
					var rawLines = Object.keys(fe).map(function(key) { return 'fe.' + key + ': ' + fe[key]; })
						.concat(Object.keys(deepq).map(function(key) { return 'deepq.' + key + ': ' + deepq[key]; }));
					return E('div', { class: 'tyf-stack' }, [
						status.offloadCard(next),
						ui.metrics([
							ui.tile('当前硬件流', fe.flows,
								fe.hash_entries ? '流表容量 ' + fe.hash_entries + ' 项' : '已发布硬件流表项',
								status.flag(fe.enabled) ? 'good' : null),
							ui.tile('二层桥接规则', fe.bridge_rules,
								fe.learned_bridge_rules != null ? '动态学习条目 ' + fe.learned_bridge_rules : 'FE 桥接转发表'),
							ui.tile('累计安装 / 释放', status.value(fe.installs) + ' / ' + status.value(fe.deletes),
								fe.rejected != null ? '慢路回退 / 未命中 ' + fe.rejected : '流表生命周期计数'),
							ui.tile('PON 深度缓冲', deepq.configured == null ? '—' : status.flag(deepq.configured) ? '已启用' : '未就绪',
								deepq.buffers_per_pool ? '每池 ' + deepq.buffers_per_pool + ' 个缓冲块' : 'QM DeepQ 状态',
								status.flag(deepq.configured) ? 'accent' : null)
						]),
						ui.fold('查看 FE 与 DeepQ 原始状态', 'fe', E('pre', {}, [rawLines.join('\n') || '未提供']))
					]);
				}, defaults
					? E('section', { class: 'tyf-config' }, [formNode])
					: status.card('未找到防火墙默认配置', status.badge('配置缺失', 'warn'),
						ui.note('未检测到 firewall defaults 配置段，请先前往原生防火墙页面检查。'),
						ui.link('打开原生防火墙设置', 'admin/network/firewall', true)));
		});
	}
});
