// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include "onu-policy.h"
#include "sync-json.h"

int fttr_onu_compare(unsigned int method, const struct fttr_onu_credentials *expected,
		const struct fttr_onu_credentials *observed)
{
	if (method < 1 || method > 5)
		return -EINVAL;
	if ((method == 2 || method == 3) && !observed->ploam_valid)
		return -ENODATA;
	if (method >= 4 && !observed->loid_valid)
		return -ENODATA;
	if ((method == 2 || method == 3) && memcmp(expected->ploam, observed->ploam, 10))
		return 2;
	if (method >= 4 && strcmp(expected->loid, observed->loid))
		return 2;
	if (method == 5 && strcmp(expected->key, observed->key))
		return 2;
	if ((method == 2 || method >= 4) && !expected->serial[0])
		return 1;
	return memcmp(expected->serial, observed->serial, 8) ? 2 : 0;
}

static int hex(const char *text, size_t length, uint8_t *out)
{
	if (strlen(text) != length * 2)
		return -EINVAL;
	for (size_t i = 0; i < length; i++) {
		unsigned int v;
		if (strspn(text + i * 2, "0123456789abcdefABCDEF") < 2 || sscanf(text + i * 2, "%2x", &v) != 1)
			return -EINVAL;
		out[i] = v;
	}
	return 0;
}

static struct json_object *field(struct json_object *object, const char *key, enum json_type type)
{
	struct json_object *value;
	return json_object_object_get_ex(object, key, &value) && json_object_is_type(value, type) ? value : NULL;
}

static const char *string(struct json_object *object, const char *key)
{
	struct json_object *value = field(object, key, json_type_string);
	const char *text = value ? json_object_get_string(value) : NULL;
	return text && strlen(text) == (size_t)json_object_get_string_len(value) ? text : NULL;
}

static bool keys(struct json_object *object, const char *allowed)
{
	json_object_object_foreach(object, key, value) {
		char entry[64];
		(void)value;
		if (strchr(key, '|') || snprintf(entry, sizeof(entry), "|%s|", key) >= (int)sizeof(entry) || !strstr(allowed, entry))
			return false;
	}
	return true;
}

static int parse(struct fttr_onu_policy *p)
{
	struct json_object *d = p->document, *rows, *vendors, *v;
	const char *revision;

	if (!keys(d, "|version|revision|state_directory|auto_learn|auto_find|vendors|onus|") ||
	    !(v = field(d, "version", json_type_int)) || json_object_get_int64(v) != 1 ||
	    !(revision = string(d, "revision")) || !*revision || strlen(revision) > 32 ||
	    strspn(revision, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != strlen(revision) ||
	    !(v = field(d, "auto_learn", json_type_boolean)))
		return -EINVAL;
	p->auto_learn = json_object_get_boolean(v);
	if (!(v = field(d, "auto_find", json_type_boolean)))
		return -EINVAL;
	p->auto_find = json_object_get_boolean(v);
	snprintf(p->name, sizeof(p->name), "%s.json", revision);
	rows = field(d, "onus", json_type_array);
	vendors = field(d, "vendors", json_type_array);
	if (!rows || !vendors || json_object_array_length(rows) > FTTR_ONUS || json_object_array_length(vendors) > 32)
		return -EINVAL;
	p->vendor_count = json_object_array_length(vendors);
	for (unsigned int i = 0; i < p->vendor_count; i++) {
		v = json_object_array_get_idx(vendors, i);
		if (!json_object_is_type(v, json_type_string) || json_object_get_string_len(v) != 8 ||
		    hex(json_object_get_string(v), 4, p->vendors[i]))
			return -EINVAL;
	}
	for (unsigned int i = 0; i < json_object_array_length(rows); i++) {
		struct json_object *row = json_object_array_get_idx(rows, i);
		struct fttr_onu_policy_row *r;
		const char *sn, *ploam, *loid, *key;
		int64_t id, method;

		if (!json_object_is_type(row, json_type_object) ||
		    !keys(row, "|id|method|active|serial|ploam|loid|key|") || !(v = field(row, "id", json_type_int)))
			return -EINVAL;
		id = json_object_get_int64(v);
		if (id < 1 || id > FTTR_ONUS || p->rows[id - 1].present || !(v = field(row, "method", json_type_int)))
			return -EINVAL;
		method = json_object_get_int64(v);
		if (method < 1 || method > 5 || !(v = field(row, "active", json_type_boolean)))
			return -EINVAL;
		r = &p->rows[id - 1];
		r->active = json_object_get_boolean(v);
		r->method = method;
		if (!(sn = string(row, "serial")) || hex(sn, 8, r->expected.serial) ||
		    !(ploam = string(row, "ploam")) || hex(ploam, 10, r->expected.ploam) ||
		    !(loid = string(row, "loid")) || strlen(loid) > 24 ||
		    !(key = string(row, "key")) || strlen(key) > 12)
			return -EINVAL;
		strcpy(r->expected.loid, loid);
		strcpy(r->expected.key, key);
		r->present = true;
	}
	return 0;
}

static int persist(struct fttr_onu_policy *p, struct json_object *bindings)
{
	struct json_object *document = json_object_new_object();
	char temporary[48];
	const char *data;
	size_t length, done = 0;
	int fd, ret = 0;

	if (!document)
		return -ENOMEM;
	if (json_object_object_add(document, "policy", json_object_get(p->document)) ||
	    json_object_object_add(document, "bindings", json_object_get(bindings))) { ret = -ENOMEM; goto out; }
	data = json_object_to_json_string_ext(document, JSON_C_TO_STRING_PLAIN);
	if (!data) { ret = -ENOMEM; goto out; }
	length = strlen(data);
	snprintf(temporary, sizeof(temporary), "%s.new", p->name);
	if (unlinkat(p->directory, temporary, 0) && errno != ENOENT) { ret = -errno; goto out; }
	fd = openat(p->directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
	if (fd < 0) { ret = -errno; goto out; }
	while (done < length) {
		ssize_t n = write(fd, data + done, length - done);
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0) { ret = n < 0 ? -errno : -EIO; break; }
		done += n;
	}
	if (!ret && fsync(fd))
		ret = -errno;
	if (close(fd) && !ret)
		ret = -errno;
	if (!ret && (renameat(p->directory, temporary, p->directory, p->name) || fsync(p->directory)))
		ret = -errno;
	if (ret)
		unlinkat(p->directory, temporary, 0);
out:
	json_object_put(document);
	return ret;
}

int fttr_onu_policy_load(const char *path, struct fttr_onu_policy *p)
{
	struct json_object *stored = NULL, *policy, *bindings;
	const char *directory;
	struct stat st;
	int fd, ret;

	memset(p, 0, sizeof(*p));
	p->directory = -1;
	ret = fttr_sync_json_load_bounded(path, 65536, &p->document);
	if (ret || (ret = parse(p)))
		goto failed;
	directory = string(p->document, "state_directory");
	if (!directory || directory[0] != '/') { ret = -EINVAL; goto failed; }
	p->directory = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (p->directory < 0) { ret = -errno; goto failed; }
	if (fstat(p->directory, &st)) { ret = -errno; goto failed; }
	if (st.st_uid != geteuid() || (st.st_mode & 077)) { ret = -EACCES; goto failed; }
	if (flock(p->directory, LOCK_EX | LOCK_NB) || fsync(p->directory)) { ret = -errno; goto failed; }
	fd = openat(p->directory, p->name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0) {
		if (errno != ENOENT) { ret = -errno; goto failed; }
		p->bindings = json_object_new_array_ext(FTTR_ONUS);
		if (!p->bindings) { ret = -ENOMEM; goto failed; }
		for (unsigned int i = 0; i < FTTR_ONUS; i++)
			if (json_object_array_add(p->bindings, NULL)) { ret = -ENOMEM; goto failed; }
		ret = persist(p, p->bindings);
		if (ret)
			goto failed;
	} else {
		ret = fttr_sync_json_read_private(fd, 131072, &stored);
		close(fd);
		if (ret)
			goto failed;
		policy = field(stored, "policy", json_type_object);
		bindings = field(stored, "bindings", json_type_array);
		if (!keys(stored, "|policy|bindings|") || !policy || !json_object_equal(policy, p->document)) { ret = -ESTALE; goto failed; }
		if (!bindings || json_object_array_length(bindings) != FTTR_ONUS) { ret = -EBADMSG; goto failed; }
		for (unsigned int i = 0; i < FTTR_ONUS; i++) {
			struct json_object *v = json_object_array_get_idx(bindings, i);
			struct fttr_onu_policy_row *r = &p->rows[i];
			uint8_t sn[8];

			if (!v)
				continue;
			if (!json_object_is_type(v, json_type_string) || json_object_get_string_len(v) != 16 ||
			    hex(json_object_get_string(v), 8, sn) || !sn[0] ||
			    (r->present && (r->expected.serial[0] || (r->method != 2 && r->method < 4))) ||
			    (!r->present && (!p->auto_learn || !p->auto_find))) { ret = -EBADMSG; goto failed; }
			if (!r->present) {
				r->present = r->active = r->learned = true;
				r->method = 1;
			} else
				r->bound = true;
			memcpy(r->expected.serial, sn, 8);
		}
		p->bindings = json_object_get(bindings);
	}
	json_object_put(stored);
	p->enabled = true;
	return 0;
failed:
	json_object_put(stored);
	fttr_onu_policy_close(p);
	return ret;
}

void fttr_onu_policy_close(struct fttr_onu_policy *p)
{
	if (p->directory >= 0)
		close(p->directory);
	json_object_put(p->document);
	json_object_put(p->bindings);
	memset(p, 0, sizeof(*p));
	p->directory = -1;
}

bool fttr_onu_policy_needs_loid(const struct fttr_onu_policy *p)
{
	for (unsigned int i = 0; i < FTTR_ONUS; i++)
		if (p->rows[i].present && p->rows[i].method >= 4)
			return true;
	return false;
}

struct fttr_onu_selection fttr_onu_policy_select(const struct fttr_onu_policy *p,
		unsigned int hardware, const struct fttr_onu_credentials *observed, const bool live[FTTR_ONUS])
{
	struct fttr_onu_selection result = { .action = FTTR_ONU_REJECT, .error = -EACCES };
	unsigned int free_id = FTTR_ONUS, vendor;
	bool missing = false;

	if (!p->enabled || hardware >= FTTR_ONUS || !observed->serial[0]) { result.error = -EINVAL; return result; }
	for (vendor = 0; vendor < p->vendor_count; vendor++)
		if (!memcmp(p->vendors[vendor], observed->serial, 4))
			break;
	if (p->vendor_count && vendor == p->vendor_count)
		return result;
	for (unsigned int i = 0; i < FTTR_ONUS; i++) {
		const struct fttr_onu_policy_row *r = &p->rows[i];
		int match;

		if (!r->present) {
			if (free_id == FTTR_ONUS)
				free_id = i;
			continue;
		}
		if (live[i] && i != hardware)
			continue;
		match = fttr_onu_compare(r->method, &r->expected, observed);
		if (match < 0) { missing = true; continue; }
		if (match == 2)
			continue;
		result.id = i;
		result.method = r->method;
		result.error = 0;
		result.action = i != hardware ? FTTR_ONU_MOVE : match == 1 ? FTTR_ONU_BIND :
			r->active ? FTTR_ONU_MATCH : FTTR_ONU_WAIT;
		return result;
	}
	/* Missing credentials cannot establish that an unconfigured identity is
	 * new: auto-learning it here would bypass the configured credential rule.
	 */
	if (missing) { result.error = -ENODATA; return result; }
	if (p->auto_find && p->auto_learn && free_id < FTTR_ONUS) {
		result.action = FTTR_ONU_LEARN;
		result.id = p->rows[hardware].present ? free_id : hardware;
		result.method = 1;
		result.error = 0;
	}
	return result;
}

int fttr_onu_policy_bind(struct fttr_onu_policy *p, const struct fttr_onu_selection *selection,
		const uint8_t serial[8])
{
	struct fttr_onu_policy_row *r;
	struct json_object *bindings = NULL, *value;
	char text[17];
	int ret;

	if (!p->enabled || p->directory < 0 || selection->id >= FTTR_ONUS || !serial[0])
		return -EINVAL;
	r = &p->rows[selection->id];
	if ((selection->action != FTTR_ONU_BIND && selection->action != FTTR_ONU_LEARN) ||
	    (selection->action == FTTR_ONU_BIND && (!r->present || r->expected.serial[0] ||
	     (r->method != 2 && r->method != 4 && r->method != 5) || selection->method != r->method)) ||
	    (selection->action == FTTR_ONU_LEARN && (r->present || !p->auto_learn || !p->auto_find || selection->method != 1)))
		return -ESTALE;
	if (json_object_deep_copy(p->bindings, &bindings, NULL))
		return -ENOMEM;
	for (unsigned int i = 0; i < 8; i++)
		snprintf(text + i * 2, 3, "%02x", serial[i]);
	value = json_object_new_string(text);
	if (!value || json_object_array_put_idx(bindings, selection->id, value)) {
		json_object_put(value);
		json_object_put(bindings);
		return -ENOMEM;
	}
	ret = persist(p, bindings);
	if (!ret) {
		if (!r->present) {
			r->present = r->active = r->learned = true;
			r->method = 1;
		} else
			r->bound = true;
		memcpy(r->expected.serial, serial, 8);
		json_object_put(p->bindings);
		p->bindings = bindings;
	} else
		json_object_put(bindings);
	return ret;
}
