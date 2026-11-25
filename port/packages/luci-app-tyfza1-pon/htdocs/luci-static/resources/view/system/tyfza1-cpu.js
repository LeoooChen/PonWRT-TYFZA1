'use strict';
'require view';
'require form';
'require rpc';
'require poll';
'require uci';
'require tyfza1.status as status';

var callCpuStatus = rpc.declare({
	object: 'luci.tyfza1-system', method: 'status', expect: { '': {} }, reject: true
});

function frequency(value) {
	return value == null ? '未提供' : (value / 1000) + ' MHz';
}

function evaluateCpuState(next, configured) {
	var cpu = (next || {}).cpu || {};
	if (cpu.current_khz == null)
		return { badge: '驱动未就绪', tone: 'warn', detail: '调频驱动尚未提供当前频率读数' };
	if (cpu.transition_error == null)
		return { badge: '校验未知', tone: 'warn', detail: '调频硬件读回状态读取失败' };
	if (cpu.transition_error !== 0)
		return { badge: '调频失败', tone: 'danger', detail: '错误码 ' + cpu.transition_error + '，请检查系统日志' };
	if (!configured)
		return { badge: '默认运行', tone: 'muted', detail: '尚未在 UCI 中指定目标频率' };
	if (cpu.governor !== 'userspace')
		return { badge: '策略未接管', tone: 'warn', detail: '当前调频策略为 ' + (cpu.governor || '未知') + '，保存并应用后切换为 userspace' };
	if (Number(configured) !== cpu.current_khz)
		return { badge: '待应用 / 不一致', tone: 'warn', detail: '已配置 ' + frequency(Number(configured)) + '，与硬件读回不同' };
	return { badge: '已生效', tone: 'good', detail: '四核共享 CPLL 时钟已按配置运行（userspace）' };
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('system'), callCpuStatus().catch(function() { return null; })]);
	},

	render: function(data) {
		var initial = data[1], cpu = ((initial || {}).cpu) || { available_khz: [] };
		var last = initial == null ? null : new Date(), pending;
		var warning = E('p', { class: 'alert-message warning', role: 'status', hidden: initial != null },
			'状态读取失败，尚无实时数据。频率配置编辑不受影响。');
		var live = E('div', { class: 'tyf-live' });
		var updated = E('span', { class: 'tyf-updated' });
		var button = E('button', { type: 'button', class: 'cbi-button tyf-btn', click: refresh }, '刷新状态');

		function renderTelemetry(next) {
			var c = (next || {}).cpu || {};
			var tempText = next && next.temperature_mC != null ? (next.temperature_mC / 1000).toFixed(1) + ' °C' : '未提供';
			var tempC = next && next.temperature_mC != null ? next.temperature_mC / 1000 : null;
			var tempTone = tempC == null ? null : tempC >= 85 ? 'danger' : tempC >= 70 ? 'warn' : 'good';
			var configured = uci.get('system', 'cpu', 'frequency');
			var stateInfo = evaluateCpuState(next, configured);
			return E('div', { class: 'tyf-metrics' }, [
				E('div', { class: 'tyf-tile', 'data-tone': c.current_khz != null ? 'accent' : null }, [
					E('dt', {}, ['当前运行频率']),
					E('dd', {}, [frequency(c.current_khz)]),
					E('p', { class: 'tyf-note' }, ['硬件寄存器实时读回' + (c.governor ? ' · ' + c.governor : '')])
				]),
				E('div', { class: 'tyf-tile', 'data-tone': tempTone }, [
					E('dt', {}, ['SoC 实时温度']),
					E('dd', {}, [tempText]),
					E('p', { class: 'tyf-note' }, ['RTL9617C 片上传感器（cpu-thermal）'])
				]),
				E('div', { class: 'tyf-tile', 'data-tone': stateInfo.tone === 'muted' ? null : stateInfo.tone }, [
					E('dt', {}, ['调频运行状态']),
					E('dd', {}, [status.badge(stateInfo.badge, stateInfo.tone)]),
					E('p', { class: 'tyf-note' }, [stateInfo.detail])
				])
			]);
		}

		function showTime() {
			updated.textContent = last ? '更新于 ' + last.toLocaleTimeString() + ' · 5 秒刷新' : '等待首次读取';
		}

		function refresh() {
			if (pending) return pending;
			button.disabled = true;
			pending = callCpuStatus().then(function(next) {
				live.replaceChildren(renderTelemetry(next));
				warning.hidden = true;
				last = new Date();
			}).catch(function() {
				warning.hidden = false;
				warning.textContent = last
					? '刷新失败，以下状态为上次成功读取的数据，并非当前状态。配置编辑不受影响。'
					: '状态读取失败，尚无实时数据。';
			}).finally(function() {
				pending = null;
				button.disabled = false;
				showTime();
			});
			return pending;
		}

		if (initial != null) live.replaceChildren(renderTelemetry(initial));
		showTime();
		poll.add(refresh, 5);

		var m = new form.Map('system');
		m.readonly = !L.hasViewPermission();
		var s = m.section(form.NamedSection, 'cpu', 'cpu', 'CPU 运行频率设置',
			'四个 Cortex-A55 核心共享同一时钟源，默认 1200 MHz。切换频率不影响 PE / NI / PCIe 独立时钟域，保存并应用及重启后持续生效。'), o;
		s.addremove = false;
		o = s.option(form.RichListValue || form.ListValue, 'frequency', '目标运行频率');
		o.rmempty = false;
		o.description = '仅调节 CPU 分频与 CPLL 系数，不作无板级依据的调压写入。应用后驱动自动校验硬件读回。';
		(cpu.available_khz || []).forEach(function(khz) {
			o.value(String(khz), frequency(khz) + (khz === 1200000 ? '（默认）' : ''));
		});
		if (!(cpu.available_khz || []).length) {
			m.readonly = true;
			this.handleSaveApply = this.handleSave = this.handleReset = null;
		}

		return m.render().then(function(formNode) {
			return E('div', { class: 'tyf-pon cbi-map' }, [
				status.style(),
				E('div', { class: 'tyf-page-header' }, [
					E('div', { class: 'tyf-page-title' }, [
						E('h2', {}, ['CPU 频率']),
						E('p', { class: 'tyf-note' }, ['查看 RTL9617C 实时核心频率与温度，配置四核共享运行频率档位。'])
					]),
					E('div', { class: 'tyf-refresh-controls' }, [updated, button])
				]),
				warning,
				live,
				E('section', { class: 'tyf-config' }, [formNode])
			]);
		});
	}
});
