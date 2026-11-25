'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

var portLabels = { lan1: 'LAN 1（lan1）', lan2: 'LAN 2（lan2）', lan3: 'LAN 3（lan3）', lan4: 'LAN 4（lan4）', pon0: 'PON 业务口（pon0）' };

return view.extend({
	load: function() { return ui.snapshot(); },
	render: function(initial) {
		return ui.page('硬件身份', '只读展示设备当前实际生效的硬件标识与光模块校准状态，不写入原厂分区，不导出认证密码。', initial, ui.snapshot, function(data) {
			var pon = data.pon || {}, identity = pon.identity || {}, bosa = pon.bosa || {}, ddm = pon.transceiver || {};
			var mode = (pon.serdes || {}).mode;
			var isEpon = /^epon/.test(mode || '');
			var temp = status.opticalTemp(ddm.temperature_raw), volt = status.opticalVoltage(ddm.voltage_raw);
			var calState = bosa.configured == null ? '未提供' : status.flag(bosa.configured) ? '已配置' : '未配置';
			var calTone = bosa.configured == null ? 'muted' : status.flag(bosa.configured) ? 'good' : 'warn';
			return E('div', { class: 'tyf-stack' }, [
				data.pon ? null : E('p', { class: 'alert-message warning' }, 'PON 身份读取失败。'),
				ui.grid([
					status.card('设备与系统', null, status.rows([
						['设备型号', data.board.model || '未提供'],
						['处理器架构', data.board.system || '未提供'],
						['内核版本', status.value(data.board.kernel)],
						['固件版本', (data.board.release || {}).description || '未提供']
					], true)),
					status.card('生效中的 PON 身份', identity.onu_mac ? status.badge('实时读取', 'good') : null, [
						status.rows([
							['运行协议', status.modes[mode] || '未提供'],
							['ONU MAC', status.mono(identity.onu_mac)],
							['ITU-T 序列号', isEpon ? 'EPON 模式不使用' : status.mono(status.formatSerial(identity.serial_number))]
						], true)
					], [
						ui.note('覆盖项留空时自动使用本机原厂值'),
						ui.link('配置身份与认证', 'admin/onu/config', true)
					]),
					status.card('光模块与校准', status.badge(calState, calTone), status.rows([
						['BOSA 芯片', status.bosaName(bosa)],
						['校准来源', bosa.eeprom == null ? '—' : status.flag(bosa.eeprom) ? '模块内置 EEPROM' : '原厂校准文件'],
						['寄存器写入 / 错误码', status.value(bosa.writes) + ' / ' + status.value(bosa.error)],
						['实时温度 / 供电', temp || volt ? [temp, volt].filter(Boolean).join(' · ') : '—']
					], true))
				]),
				status.card('物理端口 MAC 地址', null,
					status.rows((pon.ports || []).map(function(port) {
						return [portLabels[port.device] || port.device, status.mono(port.address)];
					}), true),
					ui.note('保留 TYFZA1 的原厂身份与无线/光学校准导入机制。此页不提供 MTD/UBI 分区写入，也不读取其他设备的数据。'))
			]);
		});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
