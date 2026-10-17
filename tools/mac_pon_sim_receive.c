/* SPDX-License-Identifier: GPL-2.0-only */
/* Receive the PON-source DMA probe and verify every payload byte. */
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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
    const char *interface = argc > 3 ? argv[3] : "en0";
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(55001)};
    struct timeval timeout = {.tv_sec = 1};
    unsigned int index = if_nametoindex(interface), unique = 0, duplicates = 0, bad = 0;
    unsigned int unexpected = 0, received = 0;
    uint64_t bytes = 0;
    unsigned char packet[2048];
    double started, finished, first_packet = 0, last_packet = 0;
    int fd = socket(AF_INET, SOCK_DGRAM, 0), buffer = 4 * 1024 * 1024;

    if (fd < 0 || !index || !expected || expected > sizeof(seen) * 8) return 1;
    if (setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &index, sizeof(index)) ||
        bind(fd, (void *)&local, sizeof(local))) { perror("UDP bind"); return 1; }
    int buffer_result = setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer));
    socklen_t buffer_size = sizeof(buffer);
    getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, &buffer_size);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    started = monotonic();
    printf("PON_SIM receiver %s UDP55001 ready\n", interface);
    printf("Socket receive buffer: result=%d bytes=%d\n", buffer_result, buffer);
    fflush(stdout);
    while (monotonic() - started < duration && unique < expected) {
        struct sockaddr_in source;
        socklen_t length = sizeof(source);
        ssize_t n = recvfrom(fd, packet, sizeof(packet), 0, (void *)&source, &length);
        unsigned int sequence;
        int corrupt = 0;

        if (n < 14 || source.sin_addr.s_addr != htonl(0xc6120001) ||
            source.sin_port != htons(55000) || memcmp(packet, "TYFPON-SIM", 10)) continue;
        last_packet = monotonic();
        if (!received) first_packet = last_packet;
        received++; bytes += n;
        memcpy(&sequence, packet + 10, 4); sequence = ntohl(sequence);
        corrupt = n != 1472;
        for (ssize_t i = 14; i < n; i++)
            corrupt |= packet[i] != (unsigned char)((i + 42) * 31 + 7);
        bad += corrupt;
        if (sequence >= expected) { unexpected++; continue; }
        unsigned int bit = 1U << (sequence & 7);
        if (seen[sequence >> 3] & bit) duplicates++;
        else { seen[sequence >> 3] |= bit; unique++; }
    }
    finished = monotonic();
    printf("{\"interface\":\"%s\",\"expected\":%u,\"received\":%u,\"unique\":%u,"
           "\"missing\":%u,\"duplicates\":%u,\"payload_errors\":%u,\"unexpected_sequences\":%u,"
           "\"UDP_payload_bytes\":%llu,\"receiver_window_seconds\":%.6f,"
           "\"active_packet_seconds\":%.6f,\"UDP_payload_Mbps\":%.3f,\"missing_ranges\":[",
           interface, expected, received, unique, expected - unique, duplicates, bad, unexpected,
           (unsigned long long)bytes, finished - started,
           last_packet - first_packet,
           received > 1 ? bytes * 8 / (last_packet - first_packet) / 1000000 : 0);
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
