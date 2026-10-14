/* SPDX-License-Identifier: GPL-2.0-only */
/* Real UDP sockets; the ONU DNATs non-local destinations to the other NIC. */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

struct direction {
	int send_fd, receive_fd;
	struct sockaddr_in destination;
	unsigned int count, sent, received, unique, duplicate, bad, send_errors;
	int send_errno;
	unsigned int last_sequence, duplicate_sequence[16], duplicate_previous[16];
	unsigned char *seen, *emitted;
	atomic_int sending;
	double finished;
	const char *input, *output;
};
static double now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec / 1e9;
}
static int socket_on(const char *interface, unsigned int port)
{
	struct ifaddrs *all, *entry;
	struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(port)};
	unsigned int index = if_nametoindex(interface);
	int fd = socket(AF_INET, SOCK_DGRAM, 0), buffer = 4 * 1024 * 1024;
	struct timeval timeout = {.tv_usec = 200000};

	getifaddrs(&all);
	for (entry = all; entry; entry = entry->ifa_next)
		if (entry->ifa_addr && entry->ifa_addr->sa_family == AF_INET && !strcmp(entry->ifa_name, interface))
			local.sin_addr = ((struct sockaddr_in *)entry->ifa_addr)->sin_addr;
	freeifaddrs(all);
	if (fd < 0 || !index || !local.sin_addr.s_addr ||
	    setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &index, sizeof(index)) ||
	    bind(fd, (void *)&local, sizeof(local))) { perror("bound UDP socket"); exit(1); }
	setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer));
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
	return fd;
}
static void *receive(void *argument)
{
	struct direction *path = argument;
	unsigned char packet[2048];
	double last = now();

	while (atomic_load(&path->sending) || now() - last < 1) {
		ssize_t bytes = recv(path->receive_fd, packet, sizeof(packet), 0);
		uint32_t sequence;
		unsigned int i, bad = 0;

		if (bytes < 12 || memcmp(packet, "TYFUDP01", 8)) continue;
		memcpy(&sequence, packet + 8, 4);
		sequence = ntohl(sequence);
		bad = bytes != 1472;
		for (i = 12; i < (unsigned int)bytes; i++) bad |= packet[i] != (unsigned char)(i * 31 + 7);
		path->bad += bad || sequence >= path->count;
		path->received++;
		if (sequence < path->count) {
			if (path->seen[sequence]) {
				if (path->duplicate < 16) {
					path->duplicate_sequence[path->duplicate] = sequence;
					path->duplicate_previous[path->duplicate] = path->last_sequence;
				}
				path->duplicate++;
			}
			else { path->seen[sequence] = 1; path->unique++; }
		}
		path->last_sequence = sequence;
		last = now();
	}
	return NULL;
}
int main(int argc, char **argv)
{
	struct direction paths[2] = {{.input = "en5", .output = "en0"}, {.input = "en0", .output = "en5"}};
	pthread_t receiver[2];
	unsigned int rate, count, i, failed = 0, active = 3;
	double seconds, started;
	unsigned char packet[1472];

	if (argc == 4) {
		if (!strcmp(argv[3], "down")) active = 1;
		else if (!strcmp(argv[3], "up")) active = 2;
		else if (strcmp(argv[3], "duplex")) return 1;
	}
	if (argc != 3 && argc != 4) { fprintf(stderr, "usage: mac_wifi_socket_probe wire_Mbps seconds [down|up|duplex]\n"); return 1; }
	rate = strtoul(argv[1], NULL, 10); seconds = atof(argv[2]);
	count = rate * 1000000.0 * seconds / (1538 * 8);
	memcpy(packet, "TYFUDP01", 8);
	for (i = 12; i < sizeof(packet); i++) packet[i] = i * 31 + 7;
	for (i = 0; i < 2; i++) {
		struct direction *path = &paths[i];
		if (!(active & (1U << i))) continue;
		path->count = count;
		path->seen = calloc(count, 1);
		path->emitted = calloc(count, 1);
		path->send_fd = socket_on(path->input, 55000 + i * 2);
		fcntl(path->send_fd, F_SETFL, fcntl(path->send_fd, F_GETFL) | O_NONBLOCK);
		path->receive_fd = socket_on(path->output, 55001 + i * 2);
		path->destination = (struct sockaddr_in){.sin_family = AF_INET, .sin_port = htons(55001 + i * 2)};
		inet_pton(AF_INET, i ? "198.19.0.2" : "198.18.0.2", &path->destination.sin_addr);
		atomic_store(&path->sending, 1);
		pthread_create(&receiver[i], NULL, receive, path);
	}
	puts("real UDP sockets ready on en5/en0; destinations 198.18.0.2 and198.19.0.2"); fflush(stdout);
	started = now();
	for (unsigned int sequence = 0; sequence < count && now() - started < seconds; sequence++) {
		uint32_t wire_sequence = htonl(sequence);
		memcpy(packet + 8, &wire_sequence, 4);
		for (i = 0; i < 2; i++) {
			struct direction *path = &paths[i];
			if (!(active & (1U << i))) continue;
			if (sendto(path->send_fd, packet, sizeof(packet), 0,
			    (void *)&path->destination, sizeof(path->destination)) != sizeof(packet)) {
				path->send_errors++; path->send_errno = errno;
			}
			else { path->emitted[sequence] = 1; path->sent++; }
		}
		double due = started + (sequence + 1) * 1538.0 * 8 / (rate * 1000000.0);
		while (now() < due) {}
	}
	for (i = 0; i < 2; i++) { paths[i].finished = now(); atomic_store(&paths[i].sending, 0); }
	for (i = 0; i < 2; i++) {
		struct direction *path = &paths[i];
		if (!(active & (1U << i))) continue;
		pthread_join(receiver[i], NULL);
		printf("{\"source\":\"%s\",\"receive\":\"%s\",\"sent\":%u,\"received\":%u,"
		       "\"unique\":%u,\"missing\":%u,\"duplicates\":%u,\"payload_errors\":%u,"
		       "\"send_errors\":%u,\"send_errno\":%d,\"seconds\":%.6f,\"UDP_payload_Mbps\":%.3f,\"duplicate_examples\":[",
		       path->input, path->output, path->sent, path->received, path->unique, path->sent - path->unique,
		       path->duplicate, path->bad, path->send_errors, path->send_errno, path->finished - started,
		       path->unique * 1472.0 * 8 / (path->finished - started) / 1000000);
		for (unsigned int j = 0; j < path->duplicate && j < 16; j++)
			printf("%s{\"sequence\":%u,\"previous\":%u}", j ? "," : "",
				path->duplicate_sequence[j], path->duplicate_previous[j]);
		printf("],\"missing_examples\":[");
		for (unsigned int j = 0, examples = 0; j < path->count && examples < 16; j++)
			if (path->emitted[j] && !path->seen[j])
				printf("%s%u", examples++ ? "," : "", j);
		puts("]}");
		failed |= path->sent != count || path->unique != count || path->duplicate || path->bad || path->send_errors;
		close(path->send_fd); close(path->receive_fd); free(path->seen); free(path->emitted);
	}
	return failed ? 2 : 0;
}
