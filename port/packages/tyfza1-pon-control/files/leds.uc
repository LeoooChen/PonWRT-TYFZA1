#!/usr/bin/ucode
// SPDX-License-Identifier: GPL-2.0-only
// Stock TYFZA1 PON LED spec 2, using native PON and management-session state.
'use strict';
import { readfile, writefile, glob } from 'fs';

const pon = '/sys/bus/platform/devices/4f5500000.pon/';
let patterns = {};
let wifi_bound = {};

function fields(text) {
	let result = {};
	for (let token in split(trim(text), /\s+/)) {
		let pair = split(token, '=');
		if (length(pair) == 2) result[pair[0]] = pair[1];
	}
	return result;
}

function required(path) {
	let text = readfile(path);
	if (text == null) die('LED state read failed: ' + path + '\n');
	return text;
}

function put(path, value) {
	let text = '' + value + '\n';
	if (writefile(path, text) != length(text)) die('LED write failed: ' + path + '\n');
}

function pattern(name, value) {
	if (patterns[name] == value) return;
	let path = '/sys/class/leds/' + name + '/';
	put(path + 'trigger', value > 1 ? 'timer' : 'none');
	if (value > 1) {
		put(path + 'delay_on', value);
		put(path + 'delay_off', value);
	} else {
		put(path + 'brightness', value);
	}
	patterns[name] = value;
}

function live(status, now) {
	if (status == null) return false;
	let process = readfile('/proc/' + status.pid + '/stat');
	if (process == null) return false;
	let state = split(substr(process, index(process, ') ') + 2), ' ');
	let updated = +status.updated_ms - (+status.updated_ms % 10);
	return state[0] != 'Z' && state[0] != 'X' && state[19] == status.process_start &&
		now >= updated && now - updated < 5000;
}

function wifi_leds() {
	for (let path in glob('/sys/class/net/*/phy80211/device/device')) {
		let id = readfile(path);
		let name = id != null && trim(id) == '0xc852' ? 'LED_WLAN_5G' :
			id != null && trim(id) == '0x0192' ? 'LED_WLAN_2G' : null;
		let iface = split(path, '/')[4];
		let net = '/sys/class/net/' + iface + '/';
		let type = readfile(net + 'type');
		if (name == null || type == null || trim(type) != '1') continue;
		let index = readfile(net + 'ifindex');
		if (index == null || wifi_bound[name] == index) continue;
		let led = '/sys/class/leds/' + name + '/';
		// A netdev can appear while its PCI probe is still registering the LED.
		if (readfile(led + 'max_brightness') == null) continue;
		put(led + 'trigger', 'netdev');
		put(led + 'device_name', iface);
		put(led + 'link', 1);
		put(led + 'rx', 1);
		put(led + 'tx', 1);
		wifi_bound[name] = index;
	}
}

// A null entry retains the previous pattern, as the stock event handler does.
const itu_patterns = { O1: 0, O2: 250, O23: 250, O4: 250, O5: 250, O7: 0 };
const gpon_states = ['O1', 'O2', 'O3', 'O4', 'O5', 'O6', 'O7'];
const xgtc_states = ['O1', 'O23', 'O4', 'O5', 'O6', 'O7'];
let line_key, auth_key, context_key, current = 0;
pattern('LED_PON', 0);

while (true) {
	wifi_leds();
	let now = +split(required('/proc/uptime'), ' ')[0] * 1000;
	let link = fields(required(pon + 'led_state'));
	let mode = link.mode, session = link.session;
	let context = mode + ':' + session;
	if (context != context_key) {
		current = 0;
		line_key = auth_key = null;
		context_key = context;
	}
	// Original blink-rate 256 converts to 12 ticks at HZ=100 (120 ms).
	pattern('LED_LOS', link.los == '1' ? 120 : 0);
	if (index(mode, 'epon') == 0) {
		let registered = link.enabled == '1' && link.registered == '1';
		let key = mode + ':' + registered;
		if (key != line_key) {
			current = registered ? 1 : 0;
			line_key = key;
			auth_key = null;
		}
		if (registered) {
			let text = readfile('/var/run/pon-oam/llid-0.json');
			let oam = text == null ? null : json(text);
			if (live(oam, now) && oam.registered) {
				current = oam.auth_state == 'accepted' ? 1 :
					oam.auth_state == 'rejected' ? 250 : oam.discovery_stable ? 1 : 250;
			} else {
				current = 250;
			}
		}
	} else {
		let state = (mode == 'gpon' ? gpon_states : xgtc_states)[+link.state];
		let key = mode + ':' + link.enabled + ':' + session + ':' + state;
		if (key != line_key) {
			if (link.enabled != '1') current = 0;
			else if (itu_patterns[state] != null) current = itu_patterns[state];
			line_key = key;
		}
		let text = readfile('/var/run/pon-omci');
		let omci = text == null ? null : fields(text);
		let auth = live(omci, now) && omci.session == session ? +omci.auth : 0;
		key = mode + ':' + session + ':' + auth;
		if (key != auth_key) {
			if (link.enabled == '1' && state == 'O5')
				current = auth == 1 ? 1 : auth > 1 ? 0 : 250;
			auth_key = key;
		}
	}
	pattern('LED_PON', current);
	sleep(1000);
}
