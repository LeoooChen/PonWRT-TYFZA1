'use strict';
'require view';
'require poll';
'require tyfza1.status as status';

return view.extend({
	load: function() { return status.load(); },
	render: function(data) {
		var panel = status.renderOverview(data);
		var paused = false, pending = null;
		var lastUpdated = data.every(function(part) { return !part.error; }) ? new Date() : null;
		var timestamp = E('span', { class: 'tyf-note tyf-updated' });
		var error = E('p', { class: 'alert-message warning', role: 'status', hidden: true });
		function showTime() {
			timestamp.textContent = (paused ? '自动刷新已暂停' : '每 5 秒自动刷新') +
				(lastUpdated ? ' · 更新于 ' + lastUpdated.toLocaleTimeString() : '');
		}
		function refresh() {
			if (pending) return pending;
			refreshButton.setAttribute('aria-disabled', 'true');
			refreshButton.textContent = '刷新中…';
			pending = Promise.resolve().then(function() { return status.load(); }).then(function(next) {
				status.update(panel, status.renderOverview(next));
				error.hidden = true;
				if (next.every(function(part) { return !part.error; })) lastUpdated = new Date();
			}, function() {
				error.textContent = '刷新失败，显示上次数据。';
				error.hidden = false;
			}).finally(function() {
				pending = null;
				refreshButton.setAttribute('aria-disabled', 'false');
				refreshButton.textContent = '立即刷新';
				showTime();
			});
			return pending;
		}
		var refreshButton = E('button', { type: 'button', class: 'cbi-button tyf-refresh', click: refresh }, '立即刷新');
		var pauseButton = E('button', { type: 'button', class: 'cbi-button tyf-pause', 'aria-pressed': 'false', click: function() {
			paused = !paused;
			pauseButton.setAttribute('aria-pressed', String(paused));
			pauseButton.textContent = paused ? '继续自动刷新' : '暂停自动刷新';
			showTime();
			if (!paused) return refresh();
		} }, '暂停自动刷新');
		showTime();
		poll.add(function() {
			return paused ? Promise.resolve() : refresh();
		}, 5);
		return E('div', { class: 'tyf-dashboard tyf-overview' }, [status.style(),
			E('div', { class: 'tyf-page-header' }, [E('h2', {}, 'TYFZA1 设备总览'),
				E('div', { class: 'tyf-refresh-controls' }, [refreshButton, pauseButton])]),
			timestamp, error,
			E('nav', { class: 'tyf-actions', 'aria-label': '常用管理入口' }, [
				E('a', { href: L.url('admin/network/pon') }, 'PON 管理'),
				E('a', { href: L.url('admin/network/network') }, '接口与 VLAN'),
				E('a', { href: L.url('admin/network/wireless') }, '无线设置'),
				E('a', { href: L.url('admin/network/dhcp') }, 'DHCP'),
				E('a', { href: L.url('admin/network/dns') }, 'DNS'),
				E('a', { href: L.url('admin/status/overview') }, '系统与终端'),
				E('a', { href: L.url('admin/status/logs') }, '系统日志')
			]), panel]);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
