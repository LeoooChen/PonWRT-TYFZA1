// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include "upgrade-store.h"
#include "omci-wire.h"

static void filename(const uint8_t *serial, char name[32])
{
	snprintf(name, 32, "%02x%02x%02x%02x%02x%02x%02x%02x.upgrade", serial[0], serial[1],
		serial[2], serial[3], serial[4], serial[5], serial[6], serial[7]);
}
static void put32(uint8_t *p, uint32_t v)
{
	p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}
static uint32_t get32(const uint8_t *p)
{
	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
int fttr_upgrade_store_open(const char *path)
{
	struct stat st;
	int fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW), ret;
	if (fd < 0) return -errno;
	if (fstat(fd, &st)) { ret = -errno; goto fail; }
	if (st.st_uid != geteuid() || (st.st_mode & 077)) { ret = -EACCES; goto fail; }
	if (flock(fd, LOCK_EX | LOCK_NB) || fsync(fd)) { ret = -errno; goto fail; }
	return fd;
fail:
	close(fd);
	return ret;
}
int fttr_upgrade_store_load(int directory, const uint8_t serial[8], struct fttr_upgrade_record *r)
{
	uint8_t data[64];
	char name[32];
	struct stat st;
	int fd, ret = 0;
	filename(serial, name);
	fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0) return -errno;
	if (fstat(fd, &st)) { ret = -errno; goto out; }
	if (!S_ISREG(st.st_mode) || st.st_size != sizeof(data) || st.st_uid != geteuid() || (st.st_mode & 077))
		{ ret = -EACCES; goto out; }
	if (read(fd, data, sizeof(data)) != sizeof(data)) { ret = -EIO; goto out; }
	if (memcmp(data, "FTU1", 4) || memcmp(data + 4, serial, 8) ||
	    get32(data + 60) != fttr_omci_crc(data, 60) || data[25] > FTTR_UPGRADE_COMMIT ||
	    (data[25] == FTTR_UPGRADE_DOWNLOAD && !data[42] && data[27] != FTTR_UPGRADE_END) ||
	    data[26] > 1 || data[42] > 1 || data[43] || memcmp(data + 52, (uint8_t[8]){0}, 8) ||
	    !((data[25] == FTTR_UPGRADE_DOWNLOAD && (data[27] == FTTR_UPGRADE_START || data[27] == FTTR_UPGRADE_END)) ||
	      (data[25] == FTTR_UPGRADE_ACTIVATE && data[27] == FTTR_UPGRADE_ACTIVATING) ||
	      (data[25] == FTTR_UPGRADE_COMMIT && data[27] == FTTR_UPGRADE_COMMITTING)))
		{ ret = -EBADMSG; goto out; }
	memset(r, 0, sizeof(*r));
	memcpy(r->serial, data + 4, 8); memcpy(r->host, data + 12, 6); memcpy(r->olt, data + 18, 6);
	r->backend = data[24]; r->action = data[25]; r->image = data[26]; r->stage = data[27];
	memcpy(r->version, data + 28, 14); r->pending = data[42];
	r->size = get32(data + 44); r->crc = get32(data + 48);
out:
	close(fd);
	return ret;
}
int fttr_upgrade_store_save(int directory, const struct fttr_upgrade_record *r)
{
	uint8_t data[64] = {0};
	char name[32], temporary[40];
	int fd, ret = 0;
	memcpy(data, "FTU1", 4); memcpy(data + 4, r->serial, 8);
	memcpy(data + 12, r->host, 6); memcpy(data + 18, r->olt, 6);
	data[24] = r->backend; data[25] = r->action; data[26] = r->image; data[27] = r->stage;
	memcpy(data + 28, r->version, 14); data[42] = r->pending;
	put32(data + 44, r->size); put32(data + 48, r->crc); put32(data + 60, fttr_omci_crc(data, 60));
	filename(r->serial, name); snprintf(temporary, sizeof(temporary), "%s.new", name);
	if (unlinkat(directory, temporary, 0) && errno != ENOENT) return -errno;
	fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
	if (fd < 0) return -errno;
	if (write(fd, data, sizeof(data)) != sizeof(data)) { ret = -EIO; goto out; }
	if (fsync(fd) || renameat(directory, temporary, directory, name) || fsync(directory)) ret = -errno;
out:
	close(fd);
	if (ret) unlinkat(directory, temporary, 0);
	return ret;
}
int fttr_upgrade_store_remove(int directory, const uint8_t serial[8])
{
	char name[32];
	filename(serial, name);
	if (unlinkat(directory, name, 0) && errno != ENOENT) return -errno;
	return fsync(directory) ? -errno : 0;
}
