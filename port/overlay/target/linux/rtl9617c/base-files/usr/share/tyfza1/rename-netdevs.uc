#!/usr/bin/ucode
// SPDX-License-Identifier: GPL-2.0-only
'use strict';
import { cursor } from 'uci';

const config = cursor();
let system_section;
config.foreach('system', 'system', section => {
	if (system_section == null) system_section = section['.name'];
});
if (system_section == null) die('Missing system configuration for device-name migration\n');
if (config.get('system', system_section, 'tyfza1_netdev_names') == '1') exit(0);

function rename_device(value) {
	let negation = substr(value, 0, 1) == '!' ? '!' : '';
	let name = negation ? substr(value, 1) : value;
	let lan = match(name, /^eth([0-4])($|[.:+*])/);
	if (lan) return negation + 'lan' + (+lan[1] + 1) + substr(name, 4);
	let pon = match(name, /^pon-data([0-9]+)($|[.:+*])/);
	if (pon) return negation + 'pon' + pon[1] + substr(name, 8 + length(pon[1]));
	return value;
}

function rename_value(value) {
	if (type(value) == 'array') return map(value, rename_device);
	if (type(value) != 'string') return value;
	let words = split(value, /\s+/);
	let renamed = map(words, rename_device);
	return sprintf('%J', words) == sprintf('%J', renamed) ? value : join(' ', renamed);
}

const references = {
	network: ['name', 'device', 'ifname', 'ports', 'bridge', 'master', 'parent', 'tunlink'],
	firewall: ['device', 'src_device', 'dest_device'],
	system: ['dev'],
	wireless: ['ifname'],
	sqm: ['interface'],
	qos: ['interface'],
	vnstat: ['interface'],
	statistics: ['Interface', 'Interfaces']
};

const installed = config.configs();
for (let package, options in references) {
	if (index(installed, package) < 0) continue;
	if (!config.load(package)) die('Cannot load device references: ' + package + '\n');
	let changed = false;
	config.foreach(package, null, section => {
		if (package == 'system' && section['.type'] != 'led') return;
		for (let option in options) {
			if (option == 'name' && section['.type'] != 'device') continue;
			let value = section[option];
			let renamed = rename_value(value);
			if (sprintf('%J', value) == sprintf('%J', renamed)) continue;
			if (!config.set(package, section['.name'], option, renamed))
				die('Cannot migrate device reference: ' + package + '.' + section['.name'] + '.' + option + '\n');
			changed = true;
		}
	});
	if (changed && !config.commit(package)) die('Cannot commit device names: ' + package + '\n');
}

// A future USB Ethernet adapter may legitimately acquire an ethN name.
config.set('system', system_section, 'tyfza1_netdev_names', '1');
if (!config.commit('system')) die('Cannot commit device-name migration marker\n');
