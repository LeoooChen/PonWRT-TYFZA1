'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return ui.snapshot(); },
	render: function(initial) {
		return ui.page('运行状态', '集中查看光链路、物理端口、业务接口与双频无线实时状态。接口连接不等于互联网可达。', initial, ui.snapshot, function(data) {
			var pon = data.pon || {}, optical = pon.optical || {}, bosa = pon.bosa || {}, ddm = pon.transceiver || {}, fe = pon.fe || {};
			var serdes = pon.serdes || {};
			var isLos = status.flag(serdes.rx_los) || status.flag(optical.rx_los);
			var txOff = status.flag(optical.tx_disabled);
			var rxPower = status.opticalPower(ddm.rx_power_raw, '无光');
			var txPower = status.opticalPower(ddm.tx_power_raw, txOff ? '已关断' : '待突发');
			var temp = status.opticalTemp(ddm.temperature_raw), volt = status.opticalVoltage(ddm.voltage_raw);
			var bias = status.opticalBias(ddm.bias_raw);
			var isEpon = /^epon/.test(serdes.mode || '');
			return E('div', { class: 'tyf-stack' }, [
				data.pon ? status.ponCard(pon) : E('p', { class: 'alert-message warning' }, 'PON 状态读取失败。'),
				ui.metrics([
					ui.tile('接收光功率', rxPower || (isLos ? '接收失光' : '未提供'),
						rxPower ? (isLos ? '检测到失光告警' : 'BOSA 实时收光读数') : (optical.rx_los != null ? (isLos ? '未检测到接收光信号' : '光信号正常') : '等待光模块读数'),
						isLos ? 'danger' : rxPower && rxPower !== '无光' ? 'good' : null),
					ui.tile('发射光功率', txPower || (optical.tx_disabled == null ? '未提供' : txOff ? '已关闭' : '已允许'),
						txOff ? '本地光口发射已关断' : (txPower ? '突发发射实时功率' : '光口发射处于允许状态'),
						txOff ? 'warn' : txPower && txPower !== '待突发' ? 'good' : null),
					ui.tile('光模块温度 / 供电', temp || volt ? [temp, volt].filter(Boolean).join(' · ') : (bosa.configured == null ? '未提供' : status.flag(bosa.configured) ? '校准已加载' : '未就绪'),
						bias ? '偏置电流 ' + bias + ' · ' + status.bosaName(bosa) : status.bosaName(bosa),
						status.flag(bosa.configured) ? 'accent' : null),
					ui.tile('活跃硬件流', fe.flows != null ? fe.flows + ' 条' : '—',
						fe.enabled == null ? 'FE 状态未提供' : status.flag(fe.enabled) ? 'FE 硬件卸载运行中' : 'FE 硬件卸载未启用',
						status.flag(fe.enabled) ? 'good' : null)
				]),
				ui.grid2([
					status.card('注册与认证', null, ui.authentication(pon), [
						ui.note('按运营商线路配置协议、SN 与认证凭据'),
						ui.link('线路与认证', 'admin/onu/config')
					]),
					status.offloadCard(pon, [
						ui.note('配置有线与 Wi-Fi 硬件流量卸载'),
						ui.link('硬件加速', 'admin/onu/acceleration')
					])
				]),
				status.card('物理端口与累计流量', null, ui.ports(pon)),
				status.card('业务网络接口', null, ui.networks(data), [
					ui.note('查看 WAN / LAN 实时地址，管理拨号、VLAN 与防火墙'),
					E('div', { class: 'tyf-actions' }, [
						ui.link('上网配置', 'admin/onu/internet', true),
						ui.link('接口管理', 'admin/network/network')
					])
				]),
				status.card('无线网络', null, ui.wireless(data), [
					ui.note('每个射频支持独立 AP、STA 客户端或 802.11s Mesh 接口'),
					ui.link('管理无线网络', 'admin/network/wireless', true)
				]),
				isEpon && pon.llids != null
					? ui.fold('EPON 硬件逻辑链路表（LLID）', 'paths', E('pre', {}, [String(pon.llids).trim() || '暂无已分配表项']))
					: ui.fold('OLT 下发的 GEM 业务通道', 'paths', ui.services(pon)),
				E('div', { class: 'tyf-meta-bar' }, [
					E('span', {}, [(data.board.model || 'TYFZA1') + (data.board.system ? ' · ' + data.board.system : '')]),
					E('span', {}, [((data.board.release || {}).description || '固件信息未提供')])
				])
			]);
		});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
