#!/usr/bin/ucode
// SPDX-License-Identifier: GPL-2.0-only
'use strict';
import { cursor } from 'uci';
import { readfile } from 'fs';

const config = cursor();
let system_section;
config.foreach('system', 'system', section => {
	if (system_section == null) system_section = section['.name'];
});
if (system_section == null) die('Missing system configuration for device-name migration\n');
const version = config.get('system', system_section, 'tyfza1_netdev_names');
if (version == '2') exit(0);
const board_2p5g = trim(readfile('/tmp/sysinfo/board_name')) == 'tianyi,tyfza1-2p5g';

function rename_device(value) {
	let negation = substr(value, 0, 1) == '!' ? '!' : '';
	let name = negation ? substr(value, 1) : value;
	// Only NI4 used lan5; existing lan1-lan4 keep their enclosure numbering.
	if (board_2p5g && match(name, /^lan5($|[.:+*])/))
		return negation + 'lan1' + substr(name, 4);
	// Version 1 already migrated native ethN; those names may now be USB NICs.
	if (version == '1') return value;
	let lan = match(name, /^eth([0-4])($|[.:+*])/);
	if (lan) {
		let port = +lan[1];
		if (port < 3 || port == (board_2p5g ? 4 : 3))
			return negation + 'lan' + (port == 4 ? 1 : 4 - port) + substr(name, 4);
	}
	let pon = match(name, /^pon-data([0-9]+)($|[.:+*])/);
	if (pon) return negation + 'pon' + pon[1] + substr(name, 8 + length(pon[1]));
	return value;
}

function rename_value(value) {
	if (type(value) != 'array' && type(value) != 'string') return value;
	let words = type(value) == 'array' ? value : split(value, /\s+/);
	let renamed = map(words, rename_device);
	if (sprintf('%J', words) == sprintf('%J', renamed)) return value;
	return type(value) == 'array' ? uniq(renamed) : join(' ', uniq(renamed));
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
config.set('system', system_section, 'tyfza1_netdev_names', '2');
if (!config.commit('system')) die('Cannot commit device-name migration marker\n');
