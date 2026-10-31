// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define VERSION "1.0.0"
#define RESERVE (16ULL * 1024 * 1024)
#define UBIFS_MAGIC 0x24051905
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

struct stream {
	const char *name;
	size_t limit, budget, used, size;
	unsigned long dropped;
	int fd;
	bool dirty;
};
static struct stream streams[] = {
	{.name = "kernel.log", .limit = 1024 * 1024, .budget = 65536, .fd = -1},
	{.name = "system.log", .limit = 1024 * 1024, .budget = 65536, .fd = -1},
	{.name = "health.log", .limit = 512 * 1024, .budget = 65536, .fd = -1},
	{.name = "events.log", .limit = 128 * 1024, .budget = 16384, .fd = -1},
};
static int dirfd = -1;
static bool writable = true;
static volatile sig_atomic_t stopping;
static double last_urgent, urgent_until;

static double monotime(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void fail(const char *what)
{
	perror(what);
	exit(1);
}

static void flush_logs(void)
{
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) {
		struct stream *s = &streams[i];
		if (s->fd >= 0 && s->dirty) {
			if (fdatasync(s->fd)) fail("fdatasync");
			s->dirty = false;
		}
	}
}

static void open_stream(struct stream *s)
{
	struct stat st;
	s->fd = openat(dirfd, s->name, O_WRONLY | O_APPEND | O_CREAT |
		       O_CLOEXEC | O_NOFOLLOW, 0600);
	if (s->fd < 0 || fstat(s->fd, &st)) fail(s->name);
	if (!S_ISREG(st.st_mode) || st.st_nlink != 1) {
		errno = EINVAL;
		fail("unsafe log file");
	}
	s->size = st.st_size;
}

static void rotate(struct stream *s)
{
	char old[80], newer[80];
	if (s->dirty && fdatasync(s->fd)) fail("rotate fdatasync");
	close(s->fd);
	snprintf(old, sizeof(old), "%s.1", s->name);
	snprintf(newer, sizeof(newer), "%s.2", s->name);
	if (renameat(dirfd, old, dirfd, newer) && errno != ENOENT) fail("rotate .2");
	if (renameat(dirfd, s->name, dirfd, old)) fail("rotate .1");
	s->dirty = false;
	open_stream(s);
	if (fsync(dirfd)) fail("rotate directory fsync");
}

static void append(struct stream *s, const char *data, size_t len)
{
	if (!len) return;
	if (!writable || len > s->budget - s->used) {
		s->dropped += len;
		return;
	}
	if (s->size + len > s->limit) rotate(s);
	while (len) {
		ssize_t n = write(s->fd, data, len);
		if (n < 0 && errno == EINTR) continue;
		if (n <= 0) fail("log write");
		s->size += n;
		s->used += n;
		s->dirty = true;
		data += n;
		len -= n;
	}
}

static void note(const char *message)
{
	char buf[1024];
	int n = snprintf(buf, sizeof(buf), "wall=%lld mono=%.3f %s\n",
			 (long long)time(NULL), monotime(), message);
	append(&streams[3], buf, (size_t)n < sizeof(buf) ? (size_t)n : sizeof(buf) - 1);
}

static bool important(const char *text)
{
	static const char *words[] = {"panic", "Oops:", "BUG:", "Call trace:",
		"watchdog", "lockup", "hung task", "out of memory", "oom-kill",
		"killed process", "reboot", "shutdown", "restarting system",
		"thermal", "I/O error", "UBIFS error", "uncorrectable",
		".err ", ".crit ", ".alert ", ".emerg "};
	for (size_t i = 0; i < ARRAY_SIZE(words); i++)
		if (strcasestr(text, words[i])) return true;
	return false;
}

static void record(unsigned int which, const char *text, size_t len, bool urgent)
{
	char header[100];
	int n = snprintf(header, sizeof(header), "\nwall=%lld mono=%.3f source=%s bytes=%zu\n",
			 (long long)time(NULL), monotime(), streams[which].name, len);
	/* Reserve the entire record, not just its prefix, before spending the quota. */
	struct stream *s = &streams[which];
	if (len + n <= s->budget - s->used) {
		append(s, header, n);
		append(s, text, len);
	} else s->dropped += len + n;
	if (urgent) {
		s = &streams[3];
		if (len + n <= s->budget - s->used) {
			append(s, header, n);
			append(s, text, len);
		} else s->dropped += len + n;
		/* Bound flash syncs even during an error storm. */
		double now = monotime();
		urgent_until = now + 3;
		if (now - last_urgent >= 1) {
			flush_logs();
			last_urgent = now;
		}
	}
}

static void copy_file(const char *path, size_t cap)
{
	char buf[4096], header[512];
	int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
	if (fd < 0) return;
	int n = snprintf(header, sizeof(header), "\n--- %s ---\n", path);
	append(&streams[2], header, n);
	while (cap) {
		ssize_t got = read(fd, buf, cap < sizeof(buf) ? cap : sizeof(buf));
		if (got < 0 && errno == EINTR) continue;
		if (got <= 0) break;
		append(&streams[2], buf, got);
		cap -= got;
	}
	close(fd);
}

static void copy_glob(const char *pattern)
{
	glob_t g = {0};
	if (!glob(pattern, 0, NULL, &g))
		for (size_t i = 0; i < g.gl_pathc; i++) copy_file(g.gl_pathv[i], 1024);
	globfree(&g);
}

static void health(bool full)
{
	char buf[180];
	struct statvfs v;
	int n = snprintf(buf, sizeof(buf), "\n=== wall=%lld mono=%.3f heartbeat full=%d ===\n",
			 (long long)time(NULL), monotime(), full);
	append(&streams[2], buf, n);
	const char *files[] = {"/proc/uptime", "/proc/loadavg", "/proc/meminfo",
		"/proc/net/dev", "/proc/pressure/cpu", "/proc/pressure/memory",
		"/proc/pressure/io", "/proc/sys/kernel/tainted"};
	for (size_t i = 0; i < ARRAY_SIZE(files); i++) copy_file(files[i], 8192);
	copy_glob("/sys/class/watchdog/watchdog*/state");
	copy_glob("/sys/class/watchdog/watchdog*/timeleft");
	copy_glob("/sys/class/watchdog/watchdog*/timeout");
	copy_glob("/sys/class/thermal/thermal_zone*/temp");
	copy_glob("/sys/class/ubi/ubi*/bad_peb_count");
	copy_glob("/sys/class/ubi/ubi*/max_ec");
	if (!fstatvfs(dirfd, &v)) {
		n = snprintf(buf, sizeof(buf), "\nflash_available=%llu reserve=%llu\n",
			     (unsigned long long)v.f_bavail * v.f_frsize, RESERVE);
		append(&streams[2], buf, n);
	}
	if (full) {
		copy_file("/proc/stat", 8192);
		copy_file("/proc/interrupts", 16384);
		copy_file("/proc/softirqs", 8192);
		copy_file("/proc/vmstat", 8192);
		copy_file("/proc/modules", 8192);
		/* stat includes task state, CPU time, RSS and last CPU, but no credentials. */
		copy_glob("/proc/[0-9]*/stat");
	}
}

static void space_check(void)
{
	struct statvfs v;
	bool before = writable;
	writable = !fstatvfs(dirfd, &v) && (uint64_t)v.f_bavail * v.f_frsize >= RESERVE;
	if (before != writable) {
		fprintf(stderr, "recorder: persistent writes %s (16 MiB reserve)\n",
			writable ? "resumed" : "suspended");
		if (writable) note("SPACE_RECOVERED: see dropped-byte counters");
	}
}

static void reset_budgets(void)
{
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) streams[i].used = 0;
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) {
		char buf[180];
		struct stream *s = &streams[i];
		if (!s->dropped || !writable) continue;
		snprintf(buf, sizeof(buf), "GAP stream=%s dropped_bytes=%lu (quota or low space)",
			 s->name, s->dropped);
		s->dropped = 0;
		note(buf);
	}
}

static pid_t logreader(int *fd)
{
	int p[2];
	if (pipe2(p, O_CLOEXEC)) fail("pipe2");
	pid_t pid = fork();
	if (pid < 0) fail("fork");
	if (!pid) {
		dup2(p[1], STDOUT_FILENO);
		dup2(p[1], STDERR_FILENO);
		close(p[0]);
		close(p[1]);
		/* -f alone asks OpenWrt for zero backlog; request existing evidence too. */
		execl("/sbin/logread", "logread", "-f", "-l", "4096", "-Z", "0", (char *)NULL);
		_exit(127);
	}
	close(p[1]);
	if (fcntl(p[0], F_SETFL, O_NONBLOCK)) fail("fcntl pipe");
	*fd = p[0];
	return pid;
}

static void stop_signal(int sig)
{
	stopping = sig;
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--version")) {
		puts("tyfza1-recorder " VERSION);
		return 0;
	}
	if (argc != 2) {
		fprintf(stderr, "Usage: %s EXISTING-UBIFS-BOOT-DIRECTORY\n", argv[0]);
		return 2;
	}
	umask(077);
	dirfd = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
	if (dirfd < 0) fail("boot directory");
	struct statfs fs;
	if (fstatfs(dirfd, &fs)) fail("fstatfs");
	/* Use the direct /overlay path: overlayfs itself reports a different magic. */
	if (fs.f_type != UBIFS_MAGIC) {
		fprintf(stderr, "Refusing non-UBIFS storage; use the direct /overlay directory\n");
		return 1;
	}
	int lock = openat(dirfd, "collector.lock", O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
	if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) fail("recorder already running / lock");
	space_check();
	if (!writable) return 1;
	for (size_t i = 0; i < ARRAY_SIZE(streams); i++) open_stream(&streams[i]);
	if (fsync(dirfd)) fail("initial directory fsync");
	struct sigaction sa = {.sa_handler = stop_signal};
	sigemptyset(&sa.sa_mask);
	sigaction(SIGTERM, &sa, NULL);
	sigaction(SIGINT, &sa, NULL);
	nice(5);
	note("COLLECTOR_START version=" VERSION "; not a boot/reset reason; no UART capture");
	int kmsg = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
	if (kmsg < 0) note("KMSG_UNAVAILABLE: kernel capture disabled");
	int sysfd;
	pid_t child = logreader(&sysfd);
	double now = monotime(), sync_at = now + 5, health_at = now, full_at = now;
	double quota_at = now + 60, retry_at = 0;
	unsigned long long seq = 0;
	bool have_seq = false;
	/* One-time allowance for the 128 KiB boot ring and existing syslog backlog. */
	streams[0].budget = streams[1].budget = 256 * 1024;
	char buf[16385], line[16385];
	size_t line_len = 0;
	while (!stopping) {
		struct pollfd fds[] = {{.fd = kmsg, .events = POLLIN}, {.fd = sysfd, .events = POLLIN}};
		if (poll(fds, ARRAY_SIZE(fds), 500) < 0 && errno != EINTR) fail("poll");
		/* Bounded batches keep health/sync deadlines alive under a printk flood. */
		for (int batch = 0; kmsg >= 0 && batch < 64; batch++) {
			ssize_t n = read(kmsg, buf, sizeof(buf) - 1);
			if (n < 0 && errno == EPIPE) {
				note("KMSG_OVERRUN: kernel ring records were overwritten");
				continue;
			}
			if (n < 0 && (errno == EAGAIN || errno == EINTR)) break;
			if (n <= 0) {
				note("KMSG_READ_FAILED: collector exits for procd recovery");
				stopping = SIGIO;
				break;
			}
			buf[n] = 0;
			unsigned int pri = 6;
			unsigned long long current;
			if (sscanf(buf, "%u,%llu,", &pri, &current) == 2) {
				if (have_seq && current != seq + 1) note("KMSG_SEQUENCE_GAP");
				seq = current;
				have_seq = true;
			}
			record(0, buf, n, (pri & 7) <= 3 || important(buf));
		}
		for (int batch = 0; sysfd >= 0 && batch < 32; batch++) {
			ssize_t n = read(sysfd, buf, sizeof(buf) - 1);
			if (n < 0 && (errno == EAGAIN || errno == EINTR)) break;
			if (n <= 0) {
				close(sysfd);
				sysfd = -1;
				retry_at = monotime() + 5;
				note("LOGREAD_DISCONNECTED: retry in 5 seconds; backlog may repeat");
				break;
			}
			/* Reassemble pipe fragments so split panic/reboot words are not missed. */
			for (ssize_t j = 0; j < n; j++) {
				line[line_len++] = buf[j];
				if (buf[j] == '\n' || line_len == sizeof(line) - 1) {
					line[line_len] = 0;
					record(1, line, line_len, important(line));
					line_len = 0;
				}
			}
		}
		now = monotime();
		/* Also flush the following stack frames, not only the panic/Oops header. */
		if (now <= urgent_until && now - last_urgent >= 1) {
			flush_logs();
			last_urgent = now;
		}
		if (sysfd < 0 && now >= retry_at) {
			if (line_len) {
				line[line_len] = 0;
				record(1, line, line_len, important(line));
				line_len = 0;
			}
			if (waitpid(child, NULL, WNOHANG) == child) child = logreader(&sysfd);
			retry_at = now + 5;
		}
		if (now >= sync_at) {
			flush_logs();
			space_check();
			sync_at = now + 5;
		}
		if (now >= quota_at) {
			streams[0].budget = streams[1].budget = 65536;
			reset_budgets();
			quota_at = now + 60;
		}
		if (now >= health_at) {
			health(now >= full_at);
			health_at = now + 30;
			if (now >= full_at) full_at = now + 300;
			flush_logs();
		}
	}
	if (line_len) {
		line[line_len] = 0;
		record(1, line, line_len, important(line));
	}
	note("COLLECTOR_STOP: signal/stop is NOT proof of a clean machine shutdown");
	flush_logs();
	kill(child, SIGTERM);
	/* Do not let a stuck logread delay OpenWrt shutdown. */
	if (waitpid(child, NULL, WNOHANG) == 0) kill(child, SIGKILL);
	return stopping == SIGIO ? 1 : 0;
}
