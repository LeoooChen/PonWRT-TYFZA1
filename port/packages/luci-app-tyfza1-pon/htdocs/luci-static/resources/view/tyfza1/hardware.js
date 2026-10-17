'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return ui.snapshot(); },
	render: function(initial) {
		return ui.page('硬件身份', '读取设备实际生效的身份，不写入原厂分区，不导出认证密码。', initial, ui.snapshot, function(data) {
			var pon = data.pon || {}, identity = pon.identity || {}, bosa = pon.bosa || {};
			return E('div', {}, [data.pon ? null : E('p', { class: 'alert-message warning' }, 'PON 身份读取失败。'), ui.grid([
				status.card('设备信息', null, status.rows([['型号', data.board.model || '未提供'], ['系统', data.board.system || '未提供'],
					['内核', status.value(data.board.kernel)], ['固件', (data.board.release || {}).description || '未提供']])),
				status.card('生效中的 PON 身份', null, [status.rows([['ONU MAC', status.value(identity.onu_mac)],
					['ITU-T SN（十六进制）', status.value(identity.serial_number)], ['运行协议', status.modes[(pon.serdes || {}).mode] || '未提供']]),
					ui.note('EPON 不使用 ITU-T SN。身份覆盖值可在线路与认证中设置；留空恢复原厂值。'), ui.link('配置身份与认证', 'admin/onu/config')]),
				status.card('光学校准', null, status.rows([['配置状态', bosa.configured == null ? '未提供' : bosa.configured === '1' ? '已配置' : '未配置'],
					['BOSA 型号', status.value(bosa.model)], ['初始化错误码', status.value(bosa.error)]]))
			]), status.card('端口 MAC 地址', null, status.rows((pon.ports || []).map(function(port) { return [port.device, status.value(port.address)]; }))),
			ui.note('保留 TYFZA1 的原厂身份与校准导入流程。此页不提供 MTD/UBI 写入，也不读取其他设备的身份数据。')]);
		});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
