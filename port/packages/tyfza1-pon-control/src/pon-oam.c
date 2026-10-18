// SPDX-License-Identifier: GPL-2.0-or-later
/* Native IEEE802.3ah discovery and CTC LOID/identity codec.
 * Protocol layouts cross-checked with CTC interoperability traces and TYFZA1 OAM DAL.
 * Input/output are ordinary Ethernet frames; each Linux interface owns one LLID.
 */
#include <errno.h>
#include <string.h>
#include "pon-oam.h"

#define LOCAL_EVAL 0x0008
#define LOCAL_STABLE 0x0010
#define REMOTE_EVAL 0x0020
#define REMOTE_STABLE 0x0040

static uint16_t be16(const uint8_t *data)
{
	return (uint16_t)data[0] << 8 | data[1];
}

static void set_be16(uint8_t *data, uint16_t value)
{
	data[0] = value >> 8;
	data[1] = value;
}

static bool ctc_oui(const uint8_t *oui)
{
	return !memcmp(oui, "\x11\x11\x11", 3) || !memcmp(oui, "\x00\x0d\xb6", 3);
}

void pon_oam_reset(struct pon_oam_session *session)
{
	session->have_peer = false;
	session->have_sent = false;
	session->local_echoed = false;
	session->extension_selected = false;
	session->extension_advertised = false;
	session->extension_version = 0;
	memset(session->key, 0, sizeof(session->key));
	session->key_length = 0;
	memset(session->extension_oui, 0, sizeof(session->extension_oui));
	session->peer_flags = 0;
	session->auth = PON_AUTH_WAITING;
	session->auth_failure = 0;
	session->generation++;
}

uint16_t pon_oam_flags(const struct pon_oam_session *session)
{
	uint16_t flags = session->have_peer ? session->local_echoed ? LOCAL_STABLE : LOCAL_EVAL : 0;

	if (session->peer_flags & LOCAL_STABLE)
		flags |= REMOTE_STABLE;
	else if (session->peer_flags & LOCAL_EVAL)
		flags |= REMOTE_EVAL;
	return flags;
}

static void local_information(const struct pon_oam_session *session, uint8_t *local)
{
	/* OAM version1, revision0, active, unidirectional/variable-request capability. */
	static const uint8_t information[14] = {1, 0, 0, 6, 0x14, 5, 0xee, 0, 0x0d, 0xb6, 0x4d, 0x15, 1, 0};

	memcpy(local, information, sizeof(information));
	set_be16(local + 1, session->generation);
}

static void ethernet_header(const struct pon_oam_session *session, const struct pon_oam_identity *identity,
			    uint8_t *output, uint8_t code)
{
	static const uint8_t destination[6] = {1, 0x80, 0xc2, 0, 0, 2};

	memset(output, 0, PON_OAM_FRAME_MAX);
	memcpy(output, destination, 6);
	memcpy(output + 6, identity->mac, 6);
	output[12] = 0x88;
	output[13] = 9;
	output[14] = 3;
	set_be16(output + 15, pon_oam_flags(session));
	output[17] = code;
}

size_t pon_oam_information(struct pon_oam_session *session, const struct pon_oam_identity *identity,
			   uint8_t output[PON_OAM_FRAME_MAX])
{
	size_t offset = 18;

	ethernet_header(session, identity, output, 0);
	output[offset++] = 1;
	output[offset++] = 16;
	local_information(session, output + offset);
	offset += 14;
	if (session->have_peer) {
		output[offset++] = 2;
		output[offset++] = 16;
		memcpy(output + offset, session->peer_local, 14);
		offset += 14;
	}
	if (session->extension_advertised || session->extension_selected) {
		output[offset++] = 0xfe;
		output[offset++] = session->extension_selected ? 7 : 11;
		memcpy(output + offset, session->extension_oui, 3);
		offset += 3;
		output[offset++] = 1;
		output[offset++] = session->extension_selected ? session->extension_version : 0;
		if (!session->extension_selected) {
			memcpy(output + offset, session->extension_oui, 3);
			offset += 3;
			output[offset++] = session->extension_version;
		}
	}
	offset += 2; /* end TLV and zero Ethernet padding */
	return offset < 60 ? 60 : offset;
}

void pon_oam_information_sent(struct pon_oam_session *session, const uint8_t *frame)
{
	memcpy(session->last_local, frame + 20, 14);
	session->have_sent = true;
	session->tx_frames++;
}

static int receive_information(struct pon_oam_session *session, const uint8_t *input, size_t length)
{
	const uint8_t *local = NULL, *remote = NULL, *extension = NULL;
	size_t offset;
	bool echoed, revision_only;

	for (offset = 18; offset < length;) {
		uint8_t type = input[offset], size;

		if (!type)
			break;
		if (offset + 2 > length)
			return -EMSGSIZE;
		size = input[offset + 1];
		if (size < 2 || offset + size > length)
			return -EMSGSIZE;
		if (type == 1 || type == 2) {
			if (size != 16 || (type == 1 && local) || (type == 2 && remote))
				return -EPROTO;
			if (type == 1)
				local = input + offset + 2;
			else
				remote = input + offset + 2;
		} else if (type == 0xfe && size >= 7 && ctc_oui(input + offset + 2)) {
			if ((size - 7) % 4)
				return -EPROTO;
			extension = input + offset;
		}
		offset += size;
	}
	if (!local)
		return -EPROTO;
	/* A missing/mismatched Remote echo ends the established discovery session,
	 * even when the peer's Local TLV has not changed. Require a new revision
	 * echo before accepting authentication for the next session. */
	echoed = remote && session->have_sent && !memcmp(remote, session->last_local, 14);
	/* An OLT can change only its revision after provisioning while still
	 * advertising stable and echoing our current revision. That is an update
	 * within discovery, not a new discovery/authentication session. */
	revision_only = session->have_peer && echoed && (be16(input + 15) & LOCAL_STABLE) &&
		local[0] == session->peer_local[0] && !memcmp(local + 3, session->peer_local + 3, 11);
	if ((session->have_peer && memcmp(session->peer_local, local, 14) && !revision_only) ||
	    (session->local_echoed && (!remote || !session->have_sent ||
				     memcmp(remote, session->last_local, 14))))
		pon_oam_reset(session);
	memcpy(session->peer_local, local, 14);
	session->have_peer = true;
	session->local_echoed = remote && session->have_sent && !memcmp(remote, session->last_local, 14);
	session->peer_flags = be16(input + 15);
	if (extension && (extension[1] > 7 || session->local_echoed)) {
		unsigned int version = 0;
		/* OLT advertisements contain any number of OUI/version tuples. */
		for (offset = 7; offset + 4 <= extension[1]; offset += 4)
			if (ctc_oui(extension + offset) && extension[offset + 3] <= 0x30 &&
			    extension[offset + 3] > version)
				version = extension[offset + 3];
		if (extension[1] == 7)
			version = extension[6];
		if (version) {
			memcpy(session->extension_oui, extension + 2, 3);
			session->extension_selected = extension[1] == 7;
			session->extension_advertised = extension[1] > 7;
			session->extension_version = version;
		}
	}
	return 0;
}

static void identity_field(uint8_t *output, size_t width, const uint8_t *value, size_t capacity)
{
	size_t length = strnlen((const char *)value, capacity);

	if (length > width)
		length = width;
	memset(output, 0, width - length);
	memcpy(output + width - length, value, length);
}

static int receive_auth(struct pon_oam_session *session, const struct pon_oam_identity *identity,
			const uint8_t *input, size_t length, uint8_t *output)
{
	uint8_t code;
	uint8_t auth_type;
	uint16_t size;

	if (length < 25)
		return -EMSGSIZE;
	code = input[22];
	size = be16(input + 23);
	/* CTC field interoperability: 0101 encodes an implicit type1 challenge. */
	if (!(code == 1 && size == 0x0101) && size > length - 25)
		return -EMSGSIZE;
	if (code == 3) {
		if (size)
			return -EPROTO;
		session->auth = PON_AUTH_ACCEPTED;
		session->auth_results++;
		session->auth_successes++;
		return PON_OAM_HANDLED;
	}
	if (code == 4) {
		if (size != 1)
			return -EPROTO;
		session->auth = PON_AUTH_REJECTED;
		session->auth_failure = input[25];
		session->auth_results++;
		session->auth_failures++;
		return PON_OAM_HANDLED;
	}
	if (code != 1 || (size != 1 && size != 0x0101))
		return -EPROTO;
	auth_type = size == 0x0101 ? 1 : input[25];
	session->auth = PON_AUTH_CHALLENGE;
	session->auth_failure = 0;
	session->auth_challenges++;
	output[21] = 5;
	output[22] = 2;
	if (auth_type == 1) {
		if (!identity->loid_configured)
			return -ENOENT;
		set_be16(output + 23, 37);
		output[25] = 1;
		identity_field(output + 26, 24, identity->loid, sizeof(identity->loid));
		identity_field(output + 50, 12, identity->password, sizeof(identity->password));
		return 62;
	}
	set_be16(output + 23, 2);
	output[25] = 2;
	output[26] = 1; /* Request the LOID type supported by this ONU. */
	return 60;
}

/* A zero width encodes 128 data bytes. Longer values repeat the descriptor,
 * as in the board's eponoamd:0x4162ec; the high bit denotes an indication. */
static int variable_reply(uint8_t *output, size_t offset, uint8_t branch,
			 uint16_t leaf, const uint8_t *data, size_t size, uint8_t indication)
{
	size_t count;
	size_t position = 0;

	if (!data)
		size = 0;
	count = data && size ? (size + 127) / 128 : 1;
	if (offset + count * 4 + size > PON_OAM_FRAME_MAX)
		return -EMSGSIZE;
	do {
		size_t chunk = size - position > 128 ? 128 : size - position;

		output[offset++] = branch;
		set_be16(output + offset, leaf);
		offset += 2;
		output[offset++] = data ? (chunk == 128 ? 0 : chunk) : indication;
		if (data) {
			memcpy(output + offset, data + position, chunk);
			offset += chunk;
			position += chunk;
		}
	} while (position < size);
	return offset;
}

static int receive_variables(const struct pon_oam_session *session,
	const struct pon_oam_identity *identity, const uint8_t *input,
	size_t length, uint8_t *output, bool set)
{
	uint8_t onu[54] = {}, value[PON_OAM_FRAME_MAX];
	struct pon_oam_request request = {.version = session->extension_version};
	size_t position = 22, offset = 22;
	bool variables = false;

	memcpy(onu, identity->vendor, 4);
	memcpy(onu + 4, identity->model, 4);
	memcpy(onu + 8, identity->mac, 6);
	identity_field(onu + 14, 8, identity->hardware, sizeof(identity->hardware));
	identity_field(onu + 22, 16, identity->software, sizeof(identity->software));
	identity_field(onu + 38, 16, identity->equipment, sizeof(identity->equipment));
	output[21] = set ? 4 : 2;
	while (position < length && input[position]) {
		const uint8_t *data = NULL;
		size_t size = 0;
		uint8_t indication = 0x87;
		int result;

		if (position + 3 > length)
			return -EMSGSIZE;
		request.branch = input[position];
		request.leaf = be16(input + position + 1);
		if (request.branch == 0x36 || request.branch == 0x37) {
			size_t width = request.branch == 0x36 ? 5 : 8;

			if (position + width > length || input[position + 3] != width - 4)
				return -EPROTO;
			if (offset + width > PON_OAM_FRAME_MAX)
				return -EMSGSIZE;
			memcpy(output + offset, input + position, width);
			request.object = input + position;
			request.object_size = width;
			offset += width;
			position += width;
			continue;
		}
		position += 3;
		request.parameters = NULL;
		request.parameter_size = 0;
		if (set) {
			/* These two actions have no width or data in the stock table. */
			bool no_data = (request.branch == 0xc9 && request.leaf == 1) ||
				(request.branch == 9 && request.leaf == 0xb);
			if (!no_data) {
				for (;;) {
					size_t chunk;

					if (position == length || input[position] & 0x80)
						return -EPROTO;
					chunk = input[position++] ? input[position - 1] : 128;
					if (position + chunk > length || size + chunk > sizeof(value))
						return -EMSGSIZE;
					memcpy(value + size, input + position, chunk);
					size += chunk;
					position += chunk;
					if (chunk != 128 || position + 3 >= length ||
					    input[position] != request.branch ||
					    be16(input + position + 1) != request.leaf)
						break;
					position += 3;
				}
			}
			if (identity->set_parameter)
				indication = identity->set_parameter(identity->backend_context, &request, value, size);
		} else {
			/* AlarmThreshold Get carries its two-byte alarm ID, not another
			 * variable descriptor (stock eponoamd:0x417ba0). */
			if (request.branch == 0xc7 && request.leaf == 0x82) {
				if (position + 3 > length || input[position] != 2)
					return -EPROTO;
				request.parameters = input + position + 1;
				request.parameter_size = 2;
				position += 3;
			}
			if (request.branch == 0xc7 && request.leaf == 1) {
				data = onu;
				size = session->extension_version == 0x30 ? sizeof(onu) : 38;
			} else if (request.branch == 0xc7 && request.leaf == 2) {
				/* FirmwareVer is three version octets; the software identity
				 * string belongs to ONU SN (stock eponoamd:0x41e518). */
				data = identity->firmware;
				size = sizeof(identity->firmware);
			} else if (identity->get_parameter) {
				result = identity->get_parameter(identity->backend_context, &request, value, sizeof(value));
				if (result >= 0 && (size_t)result <= sizeof(value)) {
					data = value;
					size = result;
				} else if (result == -EOPNOTSUPP) {
					indication = 0x86;
				}
			}
		}
		result = variable_reply(output, offset, request.branch, request.leaf, data, size, indication);
		if (result < 0)
			return result;
		offset = result;
		variables = true;
	}
	return variables ? (offset < 60 ? 60 : offset) : PON_OAM_IGNORED;
}

static int receive_churning(struct pon_oam_session *session, const struct pon_oam_identity *identity,
	const uint8_t *input, size_t length, uint8_t *output)
{
	int size;

	if (length < 24 || input[22] != 0 || input[23] > 1)
		return -EPROTO;
	if (!identity->churning_key)
		return -EOPNOTSUPP;
	if (session->key_index != input[23])
		session->key_length = 0;
	size = identity->churning_key(identity->backend_context, input[23] ^ 1,
		session->key, session->key_length);
	if (size < 0)
		return size;
	session->key_index = input[23];
	session->key_length = size;
	output[21] = 9;
	output[22] = 1;
	output[23] = input[23] ^ 1;
	memcpy(output + 24, session->key, size);
	return 60;
}

int pon_oam_receive(struct pon_oam_session *session, const struct pon_oam_identity *identity,
		    const uint8_t *input, size_t length, uint8_t output[PON_OAM_FRAME_MAX])
{
	int ret;

	if (length < 18 || length > PON_OAM_FRAME_MAX || input[12] != 0x88 || input[13] != 9 || input[14] != 3)
		return -EPROTO;
	session->rx_frames++;
	if (!input[17]) {
		ret = receive_information(session, input, length);
		return ret < 0 ? ret : PON_OAM_HANDLED;
	}
	if (input[17] != 0xfe)
		return PON_OAM_IGNORED;
	if (length < 22)
		return -EMSGSIZE;
	if (!ctc_oui(input + 18) ||
	    (input[21] != 1 && input[21] != 3 && input[21] != 5 && input[21] != 9))
		return PON_OAM_IGNORED;
	if (!session->local_echoed)
		return -EAGAIN;
	ethernet_header(session, identity, output, 0xfe);
	memcpy(output + 18, input + 18, 3);
	if (input[21] == 5)
		ret = receive_auth(session, identity, input, length, output);
	else if (input[21] == 9)
		ret = receive_churning(session, identity, input, length, output);
	else if (input[21] == 1)
		ret = receive_variables(session, identity, input, length, output, false);
	else
		ret = receive_variables(session, identity, input, length, output, true);
	if (ret > PON_OAM_IGNORED) {
		session->extension_selected = true;
		memcpy(session->extension_oui, input + 18, 3);
	}
	return ret;
}
