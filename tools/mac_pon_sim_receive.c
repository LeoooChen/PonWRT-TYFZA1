/* SPDX-License-Identifier: GPL-2.0-only */
/* Receive the PON-source DMA probe and verify every payload byte. */
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/uio.h>
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

#ifdef __APPLE__
/* Apple XNU bsd/sys/socket_private.h: recvmsg_x() datagram batch ABI. */
struct msghdr_x {
    void *msg_name;
    socklen_t msg_namelen;
    struct iovec *msg_iov;
    int msg_iovlen;
    void *msg_control;
    socklen_t msg_controllen;
    int msg_flags;
    size_t msg_datalen;
};
extern ssize_t recvmsg_x(int, const struct msghdr_x *, unsigned int, int);
#endif

static unsigned char seen[1250000];
static double monotonic(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
    unsigned int expected = argc > 1 ? strtoul(argv[1], NULL, 10) : 8;
    double duration = argc > 2 ? atof(argv[2]) : 15;
    const char *binding = argc > 3 ? argv[3] :
#ifdef __APPLE__
        "192.168.1.212";
#else
        "0.0.0.0";
#endif
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(55001)};
    struct timeval timeout = {.tv_sec = 1};
    unsigned int unique = 0, duplicates = 0, bad = 0;
    unsigned int unexpected = 0, received = 0;
    unsigned int previous_sequence = 0, jumps = 0;
#ifdef __linux__
    unsigned int socket_drops = 0;
#endif
    uint64_t bytes = 0, good_bytes = 0;
#ifdef __APPLE__
    unsigned char packet_batch[64][2048];
    struct sockaddr_in sources[64];
    struct iovec buffers[64];
    struct msghdr_x messages[64];
    ssize_t batch_count = 0, batch_cursor = 0;
#else
    unsigned char packet_buffer[2048];
#endif
    double started, finished, first_packet = 0, last_packet = 0, reported;
    uint64_t reported_bytes = 0;
    int fd = socket(AF_INET, SOCK_DGRAM, 0), buffer = 4 * 1024 * 1024;

    if (fd < 0 || !expected || expected > sizeof(seen) * 8) return 1;
    if (inet_pton(AF_INET, binding, &local.sin_addr) != 1) return 1;
    if (bind(fd, (void *)&local, sizeof(local))) { perror("UDP bind"); return 1; }
    struct sockaddr_in peer = {.sin_family = AF_INET, .sin_port = htons(55000),
                               .sin_addr.s_addr = htonl(0xc6120001)};
    if (connect(fd, (void *)&peer, sizeof(peer))) { perror("UDP peer"); return 1; }
    int buffer_result = setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer));
    socklen_t buffer_size = sizeof(buffer);
    getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, &buffer_size);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
#ifdef __linux__
    int report_drops = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_RXQ_OVFL, &report_drops, sizeof(report_drops))) {
        perror("UDP drop accounting"); return 1;
    }
#endif
    started = monotonic(); reported = started;
    printf("PON_SIM receiver %s UDP55001 ready\n", binding);
    printf("Socket receive buffer: result=%d bytes=%d\n", buffer_result, buffer);
    fflush(stdout);
    while (monotonic() - started < duration && unique < expected) {
        struct sockaddr_in source;
#ifdef __APPLE__
        if (batch_cursor == batch_count) {
            for (unsigned int i = 0; i < 64; i++) {
                buffers[i] = (struct iovec){.iov_base = packet_batch[i], .iov_len = sizeof(packet_batch[i])};
                messages[i] = (struct msghdr_x){.msg_name = &sources[i], .msg_namelen = sizeof(sources[i]),
                                               .msg_iov = &buffers[i], .msg_iovlen = 1};
            }
            batch_count = recvmsg_x(fd, messages, 64, 0);
            batch_cursor = 0;
            if (batch_count < 0) {
                if (errno == EAGAIN || errno == EINTR) { batch_count = 0; continue; }
                perror("recvmsg_x"); return 1;
            }
            if (!batch_count) continue;
        }
        unsigned char *packet = packet_batch[batch_cursor];
        source = sources[batch_cursor];
        ssize_t n = messages[batch_cursor++].msg_datalen;
#else
        unsigned char *packet = packet_buffer;
        struct iovec io = {.iov_base = packet, .iov_len = sizeof(packet_buffer)};
        struct msghdr message = {.msg_name = &source, .msg_namelen = sizeof(source),
                                 .msg_iov = &io, .msg_iovlen = 1};
#ifdef __linux__
        char control[CMSG_SPACE(sizeof(socket_drops))];
        message.msg_control = control; message.msg_controllen = sizeof(control);
#endif
        ssize_t n = recvmsg(fd, &message, 0);
#endif
        unsigned int sequence;
        int corrupt = 0;

        if (n < 14 || source.sin_addr.s_addr != htonl(0xc6120001) ||
            source.sin_port != htons(55000) || memcmp(packet, "TYFPPP-SIM", 10)) continue;
#ifdef __linux__
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&message); c; c = CMSG_NXTHDR(&message, c))
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SO_RXQ_OVFL)
                memcpy(&socket_drops, CMSG_DATA(c), sizeof(socket_drops));
#endif
        last_packet = monotonic();
        if (!received) first_packet = last_packet;
        received++; bytes += n;
        memcpy(&sequence, packet + 10, 4); sequence = ntohl(sequence);
        if (received > 1 && sequence != previous_sequence + 1 && jumps++ < 16)
            printf("SEQUENCE_JUMP previous=%u current=%u receiver_seconds=%.6f\n",
                   previous_sequence, sequence, last_packet - started);
        previous_sequence = sequence;
        corrupt = n != 1464;
        for (ssize_t i = 14; i < n; i++)
            corrupt |= packet[i] != (unsigned char)((i + 50) * 31 + 7);
        bad += corrupt;
        if (sequence >= expected) { unexpected++; continue; }
        unsigned int bit = 1U << (sequence & 7);
        if (seen[sequence >> 3] & bit) duplicates++;
        else { seen[sequence >> 3] |= bit; unique++; if (!corrupt) good_bytes += n; }
        if (last_packet - reported >= 1) {
            printf("INTERVAL seconds=%.6f valid_unique_payload_Mbps=%.3f\n",
                   last_packet - reported, (good_bytes - reported_bytes) * 8 / (last_packet - reported) / 1000000);
            fflush(stdout); reported = last_packet; reported_bytes = good_bytes;
        }
    }
    finished = monotonic();
    printf("{\"binding\":\"%s\",\"expected\":%u,\"received\":%u,\"unique\":%u,"
           "\"missing\":%u,\"duplicates\":%u,\"payload_errors\":%u,\"unexpected_sequences\":%u,"
#ifdef __linux__
           "\"socket_drops_seen\":%u,"
#else
           "\"socket_drops_seen\":null,"
#endif
           "\"UDP_payload_bytes\":%llu,\"valid_unique_payload_bytes\":%llu,\"receiver_window_seconds\":%.6f,"
           "\"active_packet_seconds\":%.6f,\"missing_ranges\":[",
           binding, expected, received, unique, expected - unique, duplicates, bad, unexpected,
#ifdef __linux__
           socket_drops,
#endif
           (unsigned long long)bytes, (unsigned long long)good_bytes, finished - started,
           last_packet - first_packet);
    unsigned int ranges = 0;
    for (unsigned int i = 0; i < expected && ranges < 16; i++) {
        if (seen[i >> 3] & (1U << (i & 7))) continue;
        unsigned int first = i;
        while (i + 1 < expected && !(seen[(i + 1) >> 3] & (1U << ((i + 1) & 7)))) i++;
        printf("%s[%u,%u]", ranges++ ? "," : "", first, i);
    }
    puts("]}");
    close(fd);
    return unique == expected && !bad && !duplicates && !unexpected ? 0 : 2;
}
