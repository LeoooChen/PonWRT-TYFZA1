'use strict';
'require view';
'require form';
'require rpc';
'require poll';
'require uci';

var callStatus = rpc.declare({
	object: 'luci.tyfza1-system', method: 'status', expect: { '': {} }, reject: true
});

function frequency(value) {
	return value == null ? '未提供' : (value / 1000) + ' MHz';
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('system'), callStatus()]);
	},

	render: function(data) {
		var state = data[1], cpu = state.cpu;
		var current = E('span'), temperature = E('span'), applied = E('span');
		function update(next) {
			current.textContent = frequency(next.cpu.current_khz);
			temperature.textContent = next.temperature_mC == null ? '未提供' : (next.temperature_mC / 1000).toFixed(1) + '°C';
			var configured = uci.get('system', 'cpu', 'frequency');
			applied.textContent = next.cpu.current_khz == null ? '调频驱动未就绪' :
				next.cpu.transition_error == null ? '调频结果读取失败' :
				next.cpu.transition_error !== 0 ? '调频失败（错误码 ' + next.cpu.transition_error + '），请查看系统日志。' :
				!configured ? '尚未配置运行频率' :
				next.cpu.governor !== 'userspace' ? '当前调频策略与配置不同' :
				Number(configured) !== next.cpu.current_khz ? '当前频率与配置不同，请检查应用结果和系统日志。' : '已生效';
		}
		update(state);
		poll.add(function() {
			return callStatus().then(update).catch(function(error) {
				current.textContent = temperature.textContent = '读取失败';
				applied.textContent = error.message;
			});
		}, 5);
		var m = new form.Map('system', 'CPU 频率', '四个核心共享同一频率，默认 1000 MHz。');
		m.readonly = !L.hasViewPermission();
		var s = m.section(form.NamedSection, 'cpu', 'cpu'), o;
		s.addremove = false;
		o = s.option(form.DummyValue, '_current', '当前频率');
		o.renderWidget = function() { return current; };
		o = s.option(form.DummyValue, '_temperature', 'CPU 温度');
		o.renderWidget = function() { return temperature; };
		o = s.option(form.ListValue, 'frequency', '运行频率');
		o.rmempty = false;
		cpu.available_khz.forEach(function(khz) { o.value(String(khz), frequency(khz)); });
		o = s.option(form.DummyValue, '_applied', '运行状态');
		o.renderWidget = function() { return applied; };
		if (!cpu.available_khz.length) {
			m.readonly = true;
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}
		return m.render();
	}
});
