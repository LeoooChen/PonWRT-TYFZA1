// SPDX-License-Identifier: GPL-2.0-only
#include <ctype.h>
#include <errno.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#include "sync-client.h"
#include "sync-peer.h"

int fttr_sync_mac_parse(const char *text, uint8_t mac[6])
{
	unsigned int i, nonzero = 0;

	if (strlen(text) != 17)
		return -EINVAL;
	for (i = 0; i < 6; i++) {
		char byte[3] = { text[i * 3], text[i * 3 + 1], 0 }, *end;
		unsigned long value = strtoul(byte, &end, 16);

		if (end != byte + 2 || value > 255 || (i < 5 && text[i * 3 + 2] != ':') ||
		    !isxdigit((unsigned char)byte[0]) || !isxdigit((unsigned char)byte[1]))
			return -EINVAL;
		mac[i] = value;
		nonzero |= value;
	}
	return !nonzero || (mac[0] & 1) ? -EINVAL : 0;
}

int fttr_sync_info_mesh(struct json_object *info, bool *enabled)
{
	struct json_object *field;
	if (!info || !json_object_object_get_ex(info, "easymeshCapability", &field)) return -ENODATA;
	if (!json_object_is_type(field, json_type_string) && !json_object_is_type(field, json_type_int)) return -EPROTO;
	const char *text = json_object_get_string(field);
	if (!text || (json_object_is_type(field, json_type_string) && json_object_get_string_len(field) != 1) ||
	    (strcmp(text, "0") && strcmp(text, "1"))) return -EPROTO;
	*enabled = text[0] == '1';
	return 0;
}

static int info_fields(struct json_object *value, struct json_object **observed)
{
	/* Retained GetONUInfo parser field names. Values remain as reported;
	 * no unproven enums or units are assigned by this reader. */
	static const char *const names[] = {
		"actualTime", "gatewayMac", "devType", "hdVersion", "swVersion", "sysUpTime",
		"wifiBand", "standardCapability", "supportedWorkingMode", "supportedRfBand",
		"supportedSsidNumber", "transmitPowerSupported", "supportedUplinkType", "supportedUniPorts",
		"deviceStatus", "workingMode", "uplinkType", "vxd_addr", "noise", "idlePercent",
		"InterferencePercent", "uplinkrate", "downlinkrate", "sendPackets", "sendDroppedPackets",
		"sendFailedPackets", "recvPackets", "recvDroppedPackets", "recvErrorPackets",
		"macFilterEnable", "macFilterPolicy", "macFilterEntries", "roamingSwitch", "ledStatus",
		"macAddress", "accessPoint", "internetAccessMode", "wlanConfigurationEntryNum", "band",
		"channel", "channelWidth", "standard", "antennaNumber2G4", "mimoCapability2G4",
		"mimoParameter2G4", "antennaNumber5G", "mimoCapability5G", "mimoParameter5G",
		"cpuUsage", "memUsage", "cpuTemp", "radarCounter", "lastRadarChannel", "lastRadarTime",
		"swTime", "easymeshCapability"
	};
	struct json_object *result = json_object_new_object(), *field;
	if (!result) return -ENOMEM;
	for (unsigned int i = 0; i < sizeof(names) / sizeof(*names); i++) {
		if (!json_object_object_get_ex(value, names[i], &field)) continue;
		if ((!json_object_is_type(field, json_type_string) && !json_object_is_type(field, json_type_int)) ||
		    (json_object_is_type(field, json_type_string) &&
		     (json_object_get_string_len(field) > 1024 || (size_t)json_object_get_string_len(field) != strlen(json_object_get_string(field))))) {
			json_object_put(result); return -EBADMSG;
		}
		json_object_object_add(result, names[i], json_object_get(field));
	}
	if (!json_object_object_length(result)) { json_object_put(result); return -EAGAIN; }
	*observed = result;
	return 0;
}

static int radio_reply(struct json_object *value, const uint8_t peer[6], bool writing,
	bool *ack_seen, struct json_object **summary, struct json_object **observed)
{
	const char *expected=writing?"SetONURadioInfo":"GetWifiRadio";
	struct json_object *rpc=NULL,*rows,*field,*filtered=NULL;
	bool success=false;
	int ret=-EBADMSG;
	if (json_object_object_get_ex(value,"RPCMethod",&rpc)) {
		if (!json_object_is_type(rpc,json_type_string) || (size_t)json_object_get_string_len(rpc)!=strlen(json_object_get_string(rpc))) return ret;
		if (!strcmp(json_object_get_string(rpc),"Register")) return -ESTALE;
		if (strcmp(json_object_get_string(rpc),expected)) return -EAGAIN;
	}
	if (json_object_object_get_ex(value,"Result",&field)) {
		if (!json_object_is_type(field,json_type_int)) return ret;
		if (json_object_get_int64(field)!=1) return -EREMOTEIO;
		*ack_seen=true;success=true;
	}
	if (writing) return success?0:-EAGAIN;
	if (!rpc && !success) return -EAGAIN;
	if (!json_object_object_get_ex(value,"RadioInfo",&rows)) return -EAGAIN;
	if (!json_object_is_type(rows,json_type_array) || json_object_array_length(rows)>2) return ret;
	filtered=json_object_new_array();
	if (!filtered) return -ENOMEM;
	unsigned seen=0;
	static const char *const names[]={"OnuMAC","MAC","RadioType","Enable","TransmitPower","Standard","Channel",
		"StaNum","AutoChannelEnable","ChannelsInUse","FrequencyWidth","CurrentChannelWidth","Noise",
		"IdlePercent","RecvPackets","SendPackets","BeaconFails","CountryCode"};
	for (size_t n=0;n<json_object_array_length(rows);n++) {
		struct json_object *row=json_object_array_get_idx(rows,n),*copy;
		char mac[18];uint8_t address[6];
		if (!json_object_is_type(row,json_type_object)) goto out;
		copy=json_object_new_object();
		if (!copy) {ret=-ENOMEM;goto out;}
		json_object_array_add(filtered,copy);
		for (unsigned j=0;j<sizeof(names)/sizeof(*names);j++) {
			if (!json_object_object_get_ex(row,names[j],&field)) continue;
			if (!json_object_is_type(field,json_type_string) || json_object_get_string_len(field)>256 ||
			    (size_t)json_object_get_string_len(field)!=strlen(json_object_get_string(field))) goto out;
			json_object_object_add(copy,names[j],json_object_get(field));
		}
		if (!json_object_object_get_ex(copy,"OnuMAC",&field) || json_object_get_string_len(field)!=12) goto out;
		for (unsigned i=0;i<6;i++) {mac[3*i]=json_object_get_string(field)[2*i];mac[3*i+1]=json_object_get_string(field)[2*i+1];mac[3*i+2]=i==5?0:':';}
		if (fttr_sync_mac_parse(mac,address) || memcmp(address,peer,6)) {ret=-ESTALE;goto out;}
		const char *type=json_object_object_get_ex(copy,"RadioType",&field)?json_object_get_string(field):"";
		unsigned bit=!strcmp(type,"2.4G")?1:!strcmp(type,"5G")?2:0;
		if (!bit || (seen&bit)) goto out;
		seen|=bit;
	}
	struct json_object *result=json_object_new_object();
	if (!result) {ret=-ENOMEM;goto out;}
	json_object_object_add(result,"event",json_object_new_string("sync_radio_info"));
	json_object_object_add(result,"RadioInfo",json_object_get(filtered));
	json_object_object_add(result,"identity_authenticated",json_object_new_boolean(0));
	if (observed) *observed=json_object_get(filtered);
	*summary=result;ret=0;
out:
	json_object_put(filtered);return ret;
}

static int wan_reply(struct json_object *value, bool *ack_seen)
{
	struct json_object *field;

	if (json_object_object_get_ex(value, "RPCMethod", &field)) {
		if (!json_object_is_type(field, json_type_string) ||
		    (size_t)json_object_get_string_len(field) != strlen(json_object_get_string(field)))
			return -EBADMSG;
		if (!strcmp(json_object_get_string(field), "Register"))
			return -ESTALE;
		if (strcmp(json_object_get_string(field), "WanIPChange"))
			return -EAGAIN;
	}
	if (!json_object_object_get_ex(value, "Result", &field))
		return -EAGAIN;
	if (!json_object_is_type(field, json_type_int))
		return -EBADMSG;
	if (json_object_get_int64(field) != 1)
		return -EREMOTEIO;
	*ack_seen = true;
	return 0;
}

/* This notification reports an address, including an unspecified address on
 * WAN withdrawal. It does not configure or validate a downstream route. */
int fttr_sync_wan_address(const char *text)
{
	uint8_t address[16];

	if (!text || strlen(text) >= INET6_ADDRSTRLEN)
		return -EINVAL;
	if (inet_pton(AF_INET, text, address) == 1)
		return AF_INET;
	return inet_pton(AF_INET6, text, address) == 1 ? AF_INET6 : -EINVAL;
}

/* Raw factory NTP configuration. TimeZone is the signed32 JSON encoding
 * of the MIB word, not an IANA zone or a guessed UTC offset. */
int fttr_sync_time_validate(struct json_object *value)
{
 struct json_object *field;
 if(!value || !json_object_is_type(value,json_type_object) || json_object_object_length(value)!=4)return -EINVAL;
 if(!json_object_object_get_ex(value,"TimeEnable",&field) || !json_object_is_type(field,json_type_int) ||
    (json_object_get_int64(field)!=0 && json_object_get_int64(field)!=1))return -EINVAL;
 if(!json_object_object_get_ex(value,"TimeZone",&field) || !json_object_is_type(field,json_type_int) ||
    json_object_get_int64(field)<INT32_MIN || json_object_get_int64(field)>INT32_MAX)return -EINVAL;
 const char *servers[]={"NTPServer1","NTPServer2"};
 for(unsigned int i=0;i<2;i++) {
  if(!json_object_object_get_ex(value,servers[i],&field) || !json_object_is_type(field,json_type_string) ||
     json_object_get_string_len(field)>45 ||
     (size_t)json_object_get_string_len(field)!=strlen(json_object_get_string(field)))return -EINVAL;
 }
 return 0;
}

int fttr_sync_time_check(struct json_object *observed,struct json_object *desired,bool compare)
{
 if(fttr_sync_time_validate(desired))return -EINVAL;
 if(fttr_sync_time_validate(observed))return -EBADMSG;
 return compare && !json_object_equal(observed,desired)?-EUCLEAN:0;
}

/* Original port list is the ascending comma-separated low four WAN bits.
 * Disabled IPTV has no VLAN/port write fields; VID0 denotes untagged. */
int fttr_sync_iptv_validate(struct json_object *value)
{
 struct json_object *field;
 if(!value || !json_object_is_type(value,json_type_object) ||
    !json_object_object_get_ex(value,"IPTVEnable",&field) || !json_object_is_type(field,json_type_int))return -EINVAL;
 int64_t enabled=json_object_get_int64(field);
 if(enabled==0)return json_object_object_length(value)==1?0:-EINVAL;
 if(enabled!=1 || json_object_object_length(value)!=3 ||
    !json_object_object_get_ex(value,"IPTVVlan",&field) || !json_object_is_type(field,json_type_int) ||
    json_object_get_int64(field)<0 || json_object_get_int64(field)>4094)return -EINVAL;
 if(!json_object_object_get_ex(value,"IPTVLanPort",&field) || !json_object_is_type(field,json_type_string) ||
    json_object_get_string_len(field)>19 || (size_t)json_object_get_string_len(field)!=strlen(json_object_get_string(field)))return -EINVAL;
 const char *ports=json_object_get_string(field);unsigned int last=0;
 while(*ports) {
  if(strlen(ports)<4 || strncmp(ports,"LAN",3) || ports[3]<'1' || ports[3]>'4' || (unsigned int)(ports[3]-'0')<=last)return -EINVAL;
  last=ports[3]-'0';ports+=4;
  if(!*ports)break;
  if(*ports++!=',' || !*ports)return -EINVAL;
 }
 return 0;
}

int fttr_sync_iptv_check(struct json_object *observed,struct json_object *desired,bool compare)
{
 if(fttr_sync_iptv_validate(desired))return -EINVAL;
 if(fttr_sync_iptv_validate(observed))return -EBADMSG;
 return compare && !json_object_equal(observed,desired)?-EUCLEAN:0;
}

static int settings_reply(struct json_object *value,const uint8_t peer[6],unsigned int settings,
 bool *ack_seen,struct json_object **summary,struct json_object **observed)
{
 struct json_object *field,*result=NULL,*fields=NULL;
 bool iptv=settings==9 || settings==10,writing=settings==8 || settings==10;
 const char *expected=iptv?(writing?"SetIPTVSetting":"GetIPTVSetting"):(writing?"SetTimeSetting":"GetTimeSetting");
 int ret=-EBADMSG;
 if(json_object_object_get_ex(value,"RPCMethod",&field)) {
  if(!json_object_is_type(field,json_type_string) || (size_t)json_object_get_string_len(field)!=strlen(json_object_get_string(field)))return ret;
  const char *method=json_object_get_string(field);
  if(!strcmp(method,"Register"))return -ESTALE;
  if(strcmp(method,expected))return -EAGAIN;
 }
 if(!json_object_object_get_ex(value,"Result",&field))return -EAGAIN;
 if(!json_object_is_type(field,json_type_int))return ret;
 if(json_object_get_int64(field)!=1)return -EREMOTEIO;
 *ack_seen=true;
 if(writing)return 0;
 /* The factory accepts bare ACKs. Native reads require this reply's data
  * and peer identity; a previous ACK cannot complete a later partial row. */
 if(!json_object_object_get_ex(value,"MAC",&field))return -EAGAIN;
 if(!json_object_is_type(field,json_type_string) || json_object_get_string_len(field)!=12)return ret;
 char mac[18];uint8_t decoded[6];const char *text=json_object_get_string(field);
 for(unsigned int i=0;i<6;i++){mac[i*3]=text[i*2];mac[i*3+1]=text[i*2+1];mac[i*3+2]=i==5?0:':';}
 if(fttr_sync_mac_parse(mac,decoded) || memcmp(decoded,peer,6))return -ESTALE;
 fields=json_object_new_object();if(!fields)return -ENOMEM;
 const char *time_names[]={"TimeEnable","NTPServer1","NTPServer2","TimeZone"};
 const char *iptv_names[]={"IPTVEnable","IPTVVlan","IPTVLanPort"};
 const char *const *names=iptv?iptv_names:time_names;
 unsigned int count=iptv?3:4;
 if(iptv) {
  if(!json_object_object_get_ex(value,"IPTVEnable",&field)) {ret=-EAGAIN;goto out;}
  if(!json_object_is_type(field,json_type_int))goto out;
  if(json_object_get_int64(field)==0)count=1;
 }
 for(unsigned int i=0;i<count;i++) {
  if(!json_object_object_get_ex(value,names[i],&field)){ret=-EAGAIN;goto out;}
  json_object_object_add(fields,names[i],json_object_get(field));
 }
 if(iptv?fttr_sync_iptv_validate(fields):fttr_sync_time_validate(fields))goto out;
 result=json_object_new_object();if(!result){ret=-ENOMEM;goto out;}
 json_object_object_add(result,"event",json_object_new_string(iptv?"sync_iptv_settings":"sync_time_settings"));
 json_object_object_add(result,"reported",json_object_get(fields));
 json_object_object_add(result,iptv?"traffic_verified":"clock_synchronized",json_object_new_boolean(0));
 json_object_object_add(result,"identity_authenticated",json_object_new_boolean(0));
 if(observed)*observed=json_object_get(fields);
 *summary=result;ret=0;
out:
 json_object_put(fields);return ret;
}

int fttr_sync_reply_decode(const struct fttr_sync_message *message, const uint8_t peer[6],
			unsigned int settings, bool writing, bool *ack_seen,
			struct json_object **summary, struct json_object **observed)
{
	struct json_tokener *parser = json_tokener_new();
	struct json_object *value = NULL, *field, *result = NULL, *entries = NULL;
	const char *method = settings ? "SetAllSetting" : "GetWifi";
	uint8_t address[6];
	char mac[18];
	int ret = -EBADMSG;
	size_t i;
	bool acknowledgement = false;

	if (!parser)
		return -ENOMEM;
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	value = json_tokener_parse_ex(parser, (const char *)message->payload, message->length);
	if (json_tokener_get_error(parser) != json_tokener_success ||
	    json_tokener_get_parse_end(parser) != message->length ||
	    !json_object_is_type(value, json_type_object))
		goto out;
	if(settings>=7 && settings<=10) {ret=settings_reply(value,peer,settings,ack_seen,summary,observed);goto out;}
	if (settings == 6) {
		ret = wan_reply(value, ack_seen);
		goto out;
	}
	if (settings==4 || settings==5) {
		ret=radio_reply(value,peer,settings==5,ack_seen,summary,observed);goto out;
	}
	if (settings == 3) {
		struct json_object *rpc, *fields = NULL;
		bool result_present = false;
		if (json_object_object_get_ex(value, "RPCMethod", &rpc)) {
			if (!json_object_is_type(rpc, json_type_string) ||
			    (size_t)json_object_get_string_len(rpc) != strlen(json_object_get_string(rpc))) goto out;
			if (!strcmp(json_object_get_string(rpc), "Register")) { ret = -ESTALE; goto out; }
			if (strcmp(json_object_get_string(rpc), "GetONUInfo")) { ret = -EAGAIN; goto out; }
		} else rpc = NULL;
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int)) goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
			result_present = true;
		}
		/* The original pending GetONUInfo response can omit RPCMethod.
		 * A bare ACK is insufficient: require this message's success plus
		 * target MAC and at least one understood data field. */
		if (!rpc && !result_present) { ret = -EAGAIN; goto out; }
		if (!rpc && !json_object_object_get_ex(value, "MAC", &field)) { ret = -EAGAIN; goto out; }
		if (!json_object_object_get_ex(value, "MAC", &field) || !json_object_is_type(field, json_type_string) ||
		    json_object_get_string_len(field) != 12) goto out;
		for (i = 0; i < 6; i++) {
			mac[3*i] = json_object_get_string(field)[2*i]; mac[3*i+1] = json_object_get_string(field)[2*i+1];
			mac[3*i+2] = i == 5 ? 0 : ':';
		}
		if (fttr_sync_mac_parse(mac, address) || memcmp(address, peer, 6)) { ret = -ESTALE; goto out; }
		snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
		ret = info_fields(value, &fields);
		if (ret) goto out;
		result = json_object_new_object();
		if (!result) { json_object_put(fields); ret = -ENOMEM; goto out; }
		json_object_object_add(result, "event", json_object_new_string("sync_onu_info"));
		json_object_object_add(result, "peer", json_object_new_string(mac));
		json_object_object_add(result, "slot", json_object_new_int(message->slot));
		json_object_object_add(result, "identity_authenticated", json_object_new_boolean(0));
		json_object_object_add(result, "reported", json_object_get(fields));
		if (observed) *observed = json_object_get(fields);
		json_object_put(fields); *summary = result; ret = 0; goto out;
	}
	if (settings == 2) {
		struct json_object *rpc, *flag, *status;
		bool has_rpc = json_object_object_get_ex(value, "RPCMethod", &rpc);
		bool has_flag = json_object_object_get_ex(value, "Flag", &flag);
		if (has_rpc && (!json_object_is_type(rpc, json_type_string) ||
		    json_object_get_string_len(rpc) != (int)strlen(json_object_get_string(rpc)))) goto out;
		if (has_rpc && !strcmp(json_object_get_string(rpc), "Register")) { ret = -ESTALE; goto out; }
		if (has_rpc && strcmp(json_object_get_string(rpc), "SetAllSetting")) { ret = -EAGAIN; goto out; }
		if (has_flag && !json_object_is_type(flag, json_type_int)) goto out;
		if (has_flag && json_object_get_int64(flag) != 2) { ret = -EAGAIN; goto out; }
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int)) goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
		}
		/* Result acknowledges transport. Only the separately identified
		 * Flag2 STATUS0 report completes the original setting operation. */
		if (!has_rpc || !has_flag || !json_object_object_get_ex(value, "STATUS", &status)) {
			ret = -EAGAIN; goto out;
		}
		if (!json_object_is_type(status, json_type_int)) goto out;
		ret = json_object_get_int64(status) == 0 ? 0 : -EAGAIN;
		goto out;
	}
	if (json_object_object_get_ex(value, "RPCMethod", &field) &&
	    (!json_object_is_type(field, json_type_string) ||
	     (size_t)json_object_get_string_len(field) != strlen(method) || strcmp(json_object_get_string(field), method))) {
		ret = -EAGAIN;
		goto out;
	}
	if (settings) {
		struct json_object *content, *settings_content, *status;
		bool report;

		if (json_object_object_get_ex(value, "Flag", &field)) {
			if (!json_object_is_type(field, json_type_int))
				goto out;
			if (json_object_get_int64(field) != 1) { ret = -EAGAIN; goto out; }
		}
		report = json_object_object_get_ex(value, "content", &content);
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int))
				goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
			if (writing) { ret = 0; goto out; }
		}
		if (writing && json_object_object_get_ex(value, "RPCMethod", &field) &&
		    json_object_object_get_ex(value, "Flag", &field) &&
		    json_object_object_get_ex(value, "STATUS", &status) && json_object_is_type(status, json_type_int)) {
			ret = json_object_get_int64(status) == 0 ? 0 : -EAGAIN;
			goto out;
		}
		if (!report) {
			if (json_object_object_get_ex(value, "STATUS", &status)) {
				if (!json_object_is_type(status, json_type_int))
					goto out;
				if (json_object_get_int64(status) != 0) { ret = -EAGAIN; goto out; }
				goto out;
			}
			ret = -EAGAIN;
			goto out;
		}
		/* Native completion requires the separately identified Flag1 report.
		 * A positive Result by itself is only an ACK, even with a method. */
		if (!json_object_object_get_ex(value, "RPCMethod", &field) ||
		    !json_object_object_get_ex(value, "Flag", &field) ||
		    !json_object_object_get_ex(value, "STATUS", &status) ||
		    !json_object_is_type(status, json_type_int))
			goto out;
		/* Original leaves the operation pending on nonzero STATUS. Its
		 * precise progress/error meanings are not established here. */
		if (json_object_get_int64(status) != 0) { ret = -EAGAIN; goto out; }
		if (!json_object_is_type(content, json_type_object) ||
		    !json_object_object_get_ex(content, "content", &settings_content) ||
		    !json_object_is_type(settings_content, json_type_object) ||
		    !json_object_object_get_ex(settings_content, "List", &entries) ||
		    !json_object_is_type(entries, json_type_array))
			goto out;
		for (i = 0; i < json_object_array_length(entries); i++)
			if (!json_object_is_type(json_object_array_get_idx(entries, i), json_type_object))
				goto out;
		/* Original report ownership is the Ethernet source resolved at ingress;
		 * it does not require a redundant JSON MAC or a remote slot echo. */
		snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
		goto summarize;
	}
	if (json_object_object_get_ex(value, "Result", &field)) {
		if (!json_object_is_type(field, json_type_int))
			goto out;
		if (json_object_get_int64(field) != 1) {
			ret = -EREMOTEIO;
			goto out;
		}
		acknowledgement = !json_object_object_get_ex(value, "RPCMethod", &field);
		if (acknowledgement) {
			snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
				peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
			goto summarize;
		}
	}
	/* No transaction ID exists in this original envelope. Restrict the
	 * read-only exchange to one peer, slot and method under an interface lease. */
	if (!json_object_object_get_ex(value, "RPCMethod", &field) ||
	    !json_object_is_type(field, json_type_string) ||
	    json_object_get_string_len(field) != 7 || strcmp(json_object_get_string(field), "GetWifi")) {
		ret = -EAGAIN;
		goto out;
	}
	if (!json_object_object_get_ex(value, "MAC", &field) ||
	    !json_object_is_type(field, json_type_string) || json_object_get_string_len(field) != 12)
		goto out;
	for (i = 0; i < 6; i++) {
		mac[i * 3] = json_object_get_string(field)[i * 2];
		mac[i * 3 + 1] = json_object_get_string(field)[i * 2 + 1];
		mac[i * 3 + 2] = i == 5 ? 0 : ':';
	}
	if (fttr_sync_mac_parse(mac, address) || memcmp(address, peer, 6)) {
		ret = -EAGAIN;
		goto out;
	}
summarize:
	result = json_object_new_object();
	json_object_object_add(result, "event", json_object_new_string(settings ? "sync_wifi_settings" :
		acknowledgement ? "sync_ack" : "sync_wifi_reply"));
	json_object_object_add(result, "configuration_verified", json_object_new_boolean(0));
	json_object_object_add(result, "peer", json_object_new_string(mac));
	json_object_object_add(result, "slot", json_object_new_int(message->slot));
	if (settings) {
		json_object_object_add(result, "ack_seen", json_object_new_boolean(*ack_seen));
		json_object_object_add(result, "report_complete", json_object_new_boolean(1));
		json_object_object_add(result, "settings_entries", json_object_new_int(json_object_array_length(entries)));
		if (observed)
			*observed = json_object_get(entries);
	}
	/* Summarize shape only. GetWifi can contain SSIDs and keys, including
	 * fields not implemented here. Never print raw JSON or arbitrary values. */
	json_object_object_add(result, "attributes", json_object_new_int(json_object_object_length(value)));
	if (json_object_object_get_ex(value, "SSID", &field) && json_object_is_type(field, json_type_array))
		json_object_object_add(result, "ssid_entries", json_object_new_int(json_object_array_length(field)));
	json_object_object_add(result, "values_redacted", json_object_new_boolean(1));
	*summary = result;
	ret = 0;
out:
	json_object_put(value);
	json_tokener_free(parser);
	return ret;
}

int fttr_sync_request(int fd, const uint8_t source[6], const uint8_t peer[6], uint16_t ethertype,
		uint8_t slot, unsigned int settings, struct json_object *configuration)
{
	uint8_t frame[FTTR_SYNC_FRAME_MAX];
	char payload[FTTR_SYNC_FRAME_MAX];
	const char *content = configuration ? json_object_to_json_string_ext(configuration, JSON_C_TO_STRING_PLAIN) : "{\"act\":\"get\"}";
	int ret, length;
	if (settings > 10 || ((settings == 2 || settings == 5 || settings == 6 || settings == 8 || settings == 10) && !configuration)) return -EINVAL;

	length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"GetWifi\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\"}",
		source[0], source[1], source[2], source[3], source[4], source[5]);
 if(settings>=7 && settings<=10) {
  bool iptv=settings>=9,writing=settings==8 || settings==10;
  if(writing && (iptv?fttr_sync_iptv_validate(configuration):fttr_sync_time_validate(configuration)))return -EINVAL;
  struct json_object *request=json_object_new_object();if(!request)return -ENOMEM;
  char mac[13];snprintf(mac,sizeof(mac),"%02X%02X%02X%02X%02X%02X",source[0],source[1],source[2],source[3],source[4],source[5]);
  json_object_object_add(request,"RPCMethod",json_object_new_string(iptv?(writing?"SetIPTVSetting":"GetIPTVSetting"):(writing?"SetTimeSetting":"GetTimeSetting")));
  json_object_object_add(request,"MAC",json_object_new_string(mac));
  if(writing) {
   json_object_object_foreach(configuration,key,field) {json_object_object_add(request,key,json_object_get(field));}
  }
  length=snprintf(payload,sizeof(payload),"%s",json_object_to_json_string_ext(request,JSON_C_TO_STRING_PLAIN));
  json_object_put(request);
	} else if (settings == 6) {
		if (!json_object_is_type(configuration, json_type_string) ||
		    (size_t)json_object_get_string_len(configuration) != strlen(json_object_get_string(configuration)))
			return -EINVAL;
		const char *address = json_object_get_string(configuration);
		int family = fttr_sync_wan_address(address);
		if (family < 0)
			return family;
		length = snprintf(payload, sizeof(payload),
			"{\"RPCMethod\":\"WanIPChange\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\",\"%s\":\"%s\"}",
			source[0], source[1], source[2], source[3], source[4], source[5],
			family == AF_INET ? "WanIP" : "WanIPv6", address);
	} else if (settings == 4) length=snprintf(payload,sizeof(payload),"{\"RPCMethod\":\"GetWifiRadio\"}");
	else if (settings == 5) {
		struct json_object *request=json_object_new_object();
		if (!request) return -ENOMEM;
		json_object_object_add(request,"RPCMethod",json_object_new_string("SetONURadioInfo"));
		json_object_object_foreach(configuration,key,field) json_object_object_add(request,key,json_object_get(field));
		length=snprintf(payload,sizeof(payload),"%s",json_object_to_json_string_ext(request,JSON_C_TO_STRING_PLAIN));
		json_object_put(request);
	} else if (settings == 3)
		length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"GetONUInfo\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\"}",
			source[0], source[1], source[2], source[3], source[4], source[5]);
	else if (settings)
		length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"SetAllSetting\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\","
			"\"DMAC\":\"%02X%02X%02X%02X%02X%02X\",\"Flag\":%u,\"content\":%s}",
			source[0], source[1], source[2], source[3], source[4], source[5],
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5], settings, content);
	if (length < 0 || (size_t)length >= sizeof(payload))
		return -E2BIG;
	ret = fttr_sync_pack(source, settings>=4 && settings<=6?peer:fttr_sync_slave_group, ethertype, slot, FTTR_SYNC_REQUEST,
		payload, length, frame, sizeof(frame));
	if (ret < 0)
		return ret;
	length = ret;
	{
		ssize_t sent = send(fd, frame, length, 0);

		if (sent != length)
			return sent < 0 ? -errno : -EIO;
	}
	return 0;
}
