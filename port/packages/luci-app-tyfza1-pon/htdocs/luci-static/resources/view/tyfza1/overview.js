'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return ui.snapshot(); },
	render: function(initial) {
		return ui.page('运行状态', '光链路、业务网络与无线，集中查看。接口连接不等于互联网可达。', initial, ui.snapshot, function(data) {
			var pon = data.pon || {}, optical = pon.optical || {}, bosa = pon.bosa || {};
			return E('div', {}, [data.pon ? status.ponCard(pon) : E('p', { class: 'alert-message warning' }, 'PON 状态读取失败。'),
				ui.grid([
					ui.tile('运行协议', status.modes[(pon.serdes || {}).mode] || '未提供', '来自驱动实际运行模式'),
					ui.tile('光口发射', optical.tx_disabled == null ? '未提供' : optical.tx_disabled === '1' ? '已关闭' : '已允许', '光功率 / 温度尚无读取接口'),
					ui.tile('光学校准', bosa.configured == null ? '未提供' : bosa.configured === '1' ? '已配置' : '未就绪', '沿用原厂数据加载机制')
				]),
				ui.grid([status.card('注册与认证', null, ui.authentication(pon)), status.offloadCard(pon)]),
				status.card('业务网络', null, [ui.networks(data), ui.link('管理上网', 'admin/onu/internet')]),
				status.card('无线网络', null, [data.wifiError ? ui.note('无线状态读取失败。') : data.wifi.length ? ui.grid(data.wifi.map(function(wifi) {
					return status.card(wifi.ssid || wifi.name, status.badge(wifi.up ? '运行中' : '未运行', wifi.up ? 'good' : 'muted'), status.rows([
						['接口', wifi.name], ['信道', status.value(wifi.channel)], ['频率', wifi.frequency ? wifi.frequency + ' GHz' : '—'],
						['当前 PHY 速率', wifi.bitrate ? wifi.bitrate + ' Mbit/s' : '—']
					]));
				})) : ui.note('未发现无线接口；请检查无线配置与校准加载状态。'), ui.link('管理 Wi-Fi 6', 'admin/network/wireless')]),
				ui.fold('物理端口与累计流量', 'ports', ui.ports(pon)),
				ui.fold('OLT 下发的 GEM 业务通道', 'paths', ui.services(pon)),
				ui.note((data.board.model || 'TYFZA1') + ' · ' + ((data.board.release || {}).description || '固件信息未提供'))
			]);
		});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
