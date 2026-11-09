// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#include "sync-store.h"
#include "sync-json.h"

int fttr_sync_store_revision(struct json_object *peer)
{
	struct json_object *value;
	const char *revision;
	size_t i, length;

	if (!json_object_object_get_ex(peer, "Revision", &value) || !json_object_is_type(value, json_type_string))
		return -EINVAL;
	revision = json_object_get_string(value);
	length = json_object_get_string_len(value);
	if (!length || length > 32)
		return -EINVAL;
	for (i = 0; i < length; i++)
		if (!((revision[i] >= 'a' && revision[i] <= 'z') || (revision[i] >= 'A' && revision[i] <= 'Z') ||
		      (revision[i] >= '0' && revision[i] <= '9') || revision[i] == '-' || revision[i] == '_'))
			return -EINVAL;
	return 0;
}

int fttr_sync_store_open(const char *directory)
{
	struct stat st;
	int fd, ret;

	fd = open(directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0)
		return -errno;
	if (fstat(fd, &st)) { ret = -errno; goto failed; }
	if ((st.st_mode & 077) || st.st_uid != geteuid()) { ret = -EACCES; goto failed; }
	if (flock(fd, LOCK_EX | LOCK_NB)) { ret = -errno; goto failed; }
	/* Refuse filesystems that cannot sync directory metadata. */
	if (fsync(fd)) { ret = -errno; goto failed; }
	return fd;
failed:
	close(fd);
	return ret;
}

static void filename(const uint8_t peer[6], struct json_object *record, char name[64])
{
	struct json_object *revision;

	json_object_object_get_ex(record, "revision", &revision);
	snprintf(name, 64, "%02x%02x%02x%02x%02x%02x-%s.json", peer[0], peer[1], peer[2],
		peer[3], peer[4], peer[5], json_object_get_string(revision));
}

int fttr_sync_store_load(int directory, const uint8_t source[6], const uint8_t peer[6],
	uint16_t ethertype, struct json_object *policy, struct json_object **record, unsigned int *attempted)
{
	struct json_object *value, *registration, *copy, *wifi = NULL, *mesh = NULL, *radios = NULL, *time = NULL, *iptv = NULL, *ports = NULL, *password = NULL, *qos = NULL, *revision, *stored = NULL, *mask;
	unsigned int allowed;
	char name[64], mac[13];
	int fd, ret;

	*record = NULL;
	*attempted = 0;
	if (fttr_sync_store_revision(policy))
		return -EINVAL;
	json_object_object_get_ex(policy, "Registration", &registration);
	json_object_object_get_ex(policy, "WiFi", &wifi);
	json_object_object_get_ex(policy, "EasyMesh", &mesh);
	json_object_object_get_ex(policy,"Radios",&radios);
 json_object_object_get_ex(policy,"Time",&time);
 json_object_object_get_ex(policy,"IPTV",&iptv);
 json_object_object_get_ex(policy,"IPTVPorts",&ports);
 json_object_object_get_ex(policy,"WebPassword",&password);
 json_object_object_get_ex(policy,"QoS",&qos);
	allowed = (1U << (wifi ? json_object_array_length(wifi) : 0)) - 1;
	if(qos)allowed|=1U<<23;
	if(password)allowed|=1U<<22;
	if(ports)allowed|=1U<<21;
	if(iptv)allowed|=1U<<20;
	if(time)allowed|=1U<<19;
	if (mesh) allowed |= 1U << 16;
	if (radios) allowed |= ((1U << json_object_array_length(radios))-1)<<17;
	json_object_object_get_ex(policy, "Revision", &revision);
	copy = json_object_new_object();
	/* Ready can change with operator-supplied WAN/CWMP state. Identity and
	 * desired Wi-Fi/EasyMesh fields are immutable within a revision. */
	json_object_object_foreach(registration, key, field) {
		if (strcmp(key, "Ready"))
			json_object_object_add(copy, key, json_object_get(field));
	}
	value = json_object_new_object();
	snprintf(mac, sizeof(mac), "%02X%02X%02X%02X%02X%02X", source[0], source[1], source[2], source[3], source[4], source[5]);
	json_object_object_add(value, "version", json_object_new_int(qos ? 8 : password ? 7 : ports ? 6 : iptv ? 5 : time ? 4 : radios ? 3 : mesh ? 2 : 1));
	json_object_object_add(value, "source", json_object_new_string(mac));
	snprintf(mac, sizeof(mac), "%02X%02X%02X%02X%02X%02X", peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
	json_object_object_add(value, "peer", json_object_new_string(mac));
	json_object_object_add(value, "ethertype", json_object_new_int(ethertype));
	json_object_object_add(value, "revision", json_object_get(revision));
	json_object_object_add(value, "registration", copy);
	json_object_object_add(value, "wifi", json_object_get(wifi));
	if (mesh) json_object_object_add(value, "easymesh", json_object_get(mesh));
	if (radios) json_object_object_add(value,"radios",json_object_get(radios));
 if(time)json_object_object_add(value,"time",json_object_get(time));
 if(iptv)json_object_object_add(value,"iptv",json_object_get(iptv));
 if(ports)json_object_object_add(value,"iptvports",json_object_get(ports));
 if(password)json_object_object_add(value,"webpassword",json_object_get(password));
	if(qos)json_object_object_add(value,"qos",json_object_get(qos));
	json_object_object_add(value, "attempted", json_object_new_int(0));
	filename(peer, value, name);
	fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0) {
		ret = errno == ENOENT ? 0 : -errno;
		goto out;
	}
	ret = fttr_sync_json_read_private(fd, 131072, &stored);
	close(fd);
	if (ret)
		goto out;
	if (!json_object_object_get_ex(stored, "attempted", &mask) || !json_object_is_type(mask, json_type_int) ||
	    json_object_get_int64(mask) < 0 ||
	    json_object_get_int64(mask) > allowed || (json_object_get_int64(mask) & ~allowed)) {
		ret = -EBADMSG;
		goto out;
	}
	json_object_object_add(value, "attempted", json_object_get(mask));
	if (!json_object_equal(stored, value)) { ret = -ESTALE; goto out; }
	*attempted = json_object_get_int(mask);
out:
	json_object_put(stored);
	if (!ret)
		*record = value;
	else
		json_object_put(value);
	return ret;
}

int fttr_sync_store_mark(int directory, const uint8_t peer[6], struct json_object *record, unsigned int attempted)
{
	char name[64], temporary[72];
	const char *data;
	size_t length, written = 0;
	ssize_t n;
	int fd, ret = 0;

	json_object_object_add(record, "attempted", json_object_new_int(attempted));
	data = json_object_to_json_string_ext(record, JSON_C_TO_STRING_PLAIN);
	length = strlen(data);
	if (length >= 131072)
		return -E2BIG;
	filename(peer, record, name);
	snprintf(temporary, sizeof(temporary), "%s.new", name);
	/* An interrupted staging file is never a committed record. The directory
	 * lease prevents another controller from using this reserved name. */
	if (unlinkat(directory, temporary, 0) && errno != ENOENT)
		return -errno;
	fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
	if (fd < 0)
		return -errno;
	while (written < length) {
		n = write(fd, data + written, length - written);
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0) { ret = n < 0 ? -errno : -EIO; goto out; }
		written += n;
	}
	if (fsync(fd)) { ret = -errno; goto out; }
	if (renameat(directory, temporary, directory, name) || fsync(directory))
		ret = -errno;
out:
	close(fd);
	if (ret)
		unlinkat(directory, temporary, 0);
	return ret;
}
