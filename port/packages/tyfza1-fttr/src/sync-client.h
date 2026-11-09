/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_CLIENT_H
#define TYFZA1_SYNC_CLIENT_H

#include <stdbool.h>
#include <json-c/json.h>
#include "sync-wire.h"

int fttr_sync_wan_address(const char *text);
int fttr_sync_mac_parse(const char *text, uint8_t mac[6]);
/* settings: 0 GetWifi, 1 Wi-Fi Flag1, 2 EasyMesh Flag2, 3 GetONUInfo,
 * 4 GetWifiRadio, 5 SetONURadioInfo, 6 WanIPChange,
 * 7 GetTimeSetting, 8 SetTimeSetting, 9 GetIPTVSetting, 10 SetIPTVSetting,
 * 11 GetIPTVPortInfo, 12 SetIPTVPortInfo. Flag2 requires
 * a separate STATUS0 completion report; Result alone is only an ACK. */
int fttr_sync_reply_decode(const struct fttr_sync_message *message, const uint8_t peer[6],
		unsigned int settings, bool writing, bool *ack_seen,
		struct json_object **summary, struct json_object **observed);
int fttr_sync_request(int fd, const uint8_t source[6], const uint8_t peer[6], uint16_t ethertype,
		uint8_t slot, unsigned int settings, struct json_object *configuration);
int fttr_sync_iptv_ports_validate(struct json_object *value);
int fttr_sync_iptv_ports_check(struct json_object *observed,struct json_object *desired,bool compare);
int fttr_sync_iptv_validate(struct json_object *value);
int fttr_sync_iptv_check(struct json_object *observed,struct json_object *desired,bool compare);
int fttr_sync_time_validate(struct json_object *value);
int fttr_sync_time_check(struct json_object *observed,struct json_object *desired,bool compare);
int fttr_sync_info_mesh(struct json_object *info, bool *enabled);

#endif
