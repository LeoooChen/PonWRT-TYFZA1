'use strict';
'require view';
'require tyfza1.status as status';
'require tyfza1.ui as ui';

return view.extend({
	load: function() { return status.callStatus().catch(function() { return null; }); },
	render: function(initial) {
		return ui.page('诊断', '完整保留底层寄存器摘要、错误码、控制进程存活校验与多 GEM 业务映射，便于定位光路故障。',
			initial, status.callStatus, function(data) {
				var download = E('button', { type: 'button', class: 'cbi-button tyf-btn tyf-btn-primary', click: function() {
					var url = URL.createObjectURL(new Blob([ui.diagnostics(data)], { type: 'text/plain;charset=utf-8' }));
					var anchor = E('a', { href: url, download: 'tyfza1-pon-status.txt' });
					anchor.click();
					setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
				} }, '下载当前状态快照');
				return E('div', { class: 'tyf-stack' }, [
					status.ponCard(data),
					status.card('诊断工具与快照导出', null,
						ui.note('状态快照仅包含本页采集的运行状态，不导出 LOID、密码、PSK 或原厂分区镜像；公开提交问题前请自行脱敏 MAC 与序列号。'),
						E('div', { class: 'tyf-actions' }, [
							download,
							ui.link('系统日志', 'admin/status/logs'),
							ui.link('Ping / 路由 / DNS', 'admin/network/diagnostics')
						])),
					status.card('驱动与协议原始状态', null, E('pre', { class: 'tyf-raw' }, [ui.diagnostics(data)]))
				]);
			});
	},
	handleSaveApply: null, handleSave: null, handleReset: null
});
