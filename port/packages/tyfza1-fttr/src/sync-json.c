// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "sync-json.h"

int fttr_sync_json_load_private(const char *path, struct json_object **desired)
{
	struct json_tokener *parser = NULL;
	struct json_object *value = NULL;
	struct stat st;
	char data[1536];
	size_t used = 0;
	ssize_t n;
	int fd, ret = -EINVAL;

	*desired = NULL;
	fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0)
		return -errno;
	if (fstat(fd, &st)) { ret = -errno; goto out; }
	if (!S_ISREG(st.st_mode) || (st.st_mode & 077) || st.st_uid != geteuid()) { ret = -EACCES; goto out; }
	for (;;) {
		n = read(fd, data + used, sizeof(data) - used);
		if (n < 0) { if (errno == EINTR) continue; ret = -errno; goto out; }
		if (!n)
			break;
		used += n;
		if (used == sizeof(data)) { ret = -E2BIG; goto out; }
	}
	parser = json_tokener_new();
	if (!parser) { ret = -ENOMEM; goto out; }
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	value = json_tokener_parse_ex(parser, data, used);
	if (json_tokener_get_error(parser) != json_tokener_success || json_tokener_get_parse_end(parser) != used)
		goto out;
	ret = json_object_is_type(value, json_type_object) ? 0 : -EINVAL;
	if (!ret) { *desired = value; value = NULL; }
out:
	json_object_put(value);
	if (parser)
		json_tokener_free(parser);
	close(fd);
	return ret;
}

