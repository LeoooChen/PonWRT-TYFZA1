// SPDX-License-Identifier: GPL-2.0-only
#define main recorder_main
#include "recorder.c"
#undef main
#include <assert.h>

static void expect_file(const char *name, const char *expected)
{
	char buf[4096] = {0};
	int fd = openat(dirfd, name, O_RDONLY);
	assert(fd >= 0);
	ssize_t n = read(fd, buf, sizeof(buf) - 1);
	assert(n >= 0);
	assert(!strcmp(buf, expected));
	close(fd);
}

int main(void)
{
	char path[] = "/tmp/tyfza1-recorder-test-XXXXXX";
	assert(mkdtemp(path));
	dirfd = open(path, O_RDONLY | O_DIRECTORY);
	assert(dirfd >= 0);
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) open_stream(&streams[i]);
	struct stream *s = &streams[0];
	s->limit = 4;
	append(s, "aaaa", 4);
	append(s, "bbbb", 4);
	append(s, "cccc", 4);
	append(s, "dddd", 4);
	flush_logs();
	expect_file("kernel.log", "dddd");
	expect_file("kernel.log.1", "cccc");
	expect_file("kernel.log.2", "bbbb");
	assert(!s->dirty);
	struct stat st;
	assert(!fstat(s->fd, &st) && (st.st_mode & 0777) == 0600);
	puts("PASS rotation, retention bound, permissions, sync");

	s->budget = s->used + 2;
	append(s, "xxx", 3);
	assert(s->dropped == 3);
	expect_file("kernel.log", "dddd");
	writable = false;
	append(s, "xx", 2);
	assert(s->dropped == 5);
	expect_file("kernel.log", "dddd");
	writable = true;
	reset_budgets();
	assert(!s->used && !s->dropped);
	puts("PASS byte quota, low-space suspension, drop accounting");

	s->limit = 1024;
	s->budget = 1;
	s->used = 0;
	unsigned int events_used = streams[3].used;
	record(0, "Kernel panic - test\n", 20, true);
	assert(!s->used && s->dropped > 20);
	assert(streams[3].used > events_used);
	assert(important("Kernel panic - not syncing"));
	assert(important("reBOOT requested"));
	assert(important("Out of memory: Killed process 9"));
	assert(!important("ordinary packet statistics"));
	puts("PASS independent critical-event quota and signatures");

	s = &streams[1];
	append(s, "survives restart\n", 17);
	flush_logs();
	close(s->fd);
	open_stream(s);
	append(s, "appended\n", 9);
	expect_file("system.log", "survives restart\nappended\n");
	puts("PASS append on collector restart");

	char *args[] = {"recorder", path, NULL};
	int original_dir = dirfd;
	assert(recorder_main(2, args) == 1);
	close(original_dir);
	puts("PASS refuse non-UBIFS storage");
	/* recorder_main opened another directory descriptor before rejecting the fs. */
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) {
		close(streams[i].fd);
		for (int j = 0; j < 3; j++) {
			char name[80];
			if (j) snprintf(name, sizeof(name), "%s.%d", streams[i].name, j);
			else snprintf(name, sizeof(name), "%s", streams[i].name);
			unlinkat(dirfd, name, 0);
		}
	}
	close(dirfd);
	assert(!rmdir(path));
	return 0;
}
