'use strict';
'require view';
'require form';
'require uci';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return Promise.all([uci.load('firewall'), status.callStatus().catch(function() { return null; })]); },
	render: function(data) {
		var m = new form.Map('firewall', '流量卸载', '使用现有 firewall4 设置及 TYFZA1 FE 后端，不改变 WAN、桥接或 Wi-Fi 拓扑。');
		if (!L.hasViewPermission()) m.readonly = true;
		var defaults = uci.sections('firewall', 'defaults')[0];
		if (defaults) {
			var s = m.section(form.NamedSection, defaults['.name'], 'defaults'), o;
			o = s.option(form.Flag, 'flow_offloading_hw', '启用硬件流量卸载');
			o.rmempty = false;
			o.description = '启用时同时打开软件流量卸载；关闭硬件卸载不会关闭已有的软件卸载。';
			o.write = function(section, enabled) {
				uci.set('firewall', section, 'flow_offloading_hw', enabled);
				if (enabled === '1') uci.set('firewall', section, 'flow_offloading', '1');
			};
		}
		else {
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}
		return m.render().then(function(form) {
			return ui.page('硬件加速', '配置开关与驱动实际状态分开显示。已启用不代表每条连接都进入硬件。', data[1], status.callStatus, function(next) {
				var fe = next.fe || {};
				return E('div', {}, [status.offloadCard(next), ui.grid([
					ui.tile('当前硬件流', fe.flows), ui.tile('累计安装', fe.installs), ui.tile('累计释放', fe.deletes)
				]), ui.fold('FE 原始状态', 'fe', E('pre', {}, [Object.keys(fe).map(function(key) { return key + ': ' + fe[key]; }).join('\n') || '未提供']))]);
			}, E('section', { class: 'tyf-config' }, defaults ? form : ui.note('未找到防火墙 defaults 配置；请先检查原生防火墙设置。')));
		});
	}
});
