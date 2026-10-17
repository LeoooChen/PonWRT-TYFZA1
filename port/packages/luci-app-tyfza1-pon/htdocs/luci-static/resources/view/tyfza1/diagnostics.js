'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return status.callStatus().catch(function() { return null; }); },
	render: function(initial) {
		return ui.page('诊断', '保留原始错误码、会话存活状态和完整多 GEM 数据，便于定位线路问题。', initial, status.callStatus, function(data) {
			var download = E('button', { type: 'button', class: 'cbi-button', click: function() {
				var url = URL.createObjectURL(new Blob([ui.diagnostics(data)], { type: 'text/plain;charset=utf-8' }));
				var anchor = E('a', { href: url, download: 'tyfza1-pon-status.txt' });
				anchor.click();
				setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
			} }, '下载当前快照');
			return E('div', {}, [status.ponCard(data), status.card('诊断工具', null, [E('div', { class: 'tyf-actions' }, [download,
				ui.link('系统日志', 'admin/status/logs'), ui.link('Ping / 路由 / DNS', 'admin/network/diagnostics')]),
				ui.note('快照只包含本页读取的运行状态，不导出 LOID、密码、PSK 或原厂镜像；仍可能包含线路与业务标识。')]),
				status.card('驱动与协议状态', null, E('pre', { class: 'tyf-raw' }, [ui.diagnostics(data)]))]);
		});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
