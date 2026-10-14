/* SPDX-License-Identifier: GPL-2.0-only */
/* Emit actual Ethernet frames through en0, avoiding local IP delivery to en5. */
#include <arpa/inet.h>
#include <fcntl.h>
#include <net/bpf.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static uint16_t checksum(const unsigned char *data, unsigned int bytes)
{
    uint32_t sum = 0;
    for (unsigned int i = 0; i < bytes; i += 2) sum += data[i] * 256U + data[i + 1];
    while (sum >> 16) sum = (sum & 65535) + (sum >> 16);
    return ~sum;
}
static void put16(unsigned char *out, uint16_t value)
{ out[0] = value >> 8; out[1] = value; }
static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
int main(int argc, char **argv)
{
    unsigned int rate = strtoul(argv[1], NULL, 10), packets = strtoul(argv[2], NULL, 10);
    int reply = argc > 3;
    unsigned char frame[1514] = {0}, pseudo[1492] = {0};
    char path[32]; int fd = -1;
    struct ifreq interface = {0}; unsigned int one = 1;
    for (unsigned int i = 0; i < 256; i++) {
        snprintf(path, sizeof(path), "/dev/bpf%u", i);
        fd = open(path, O_RDWR); if (fd >= 0) break;
    }
    if (fd < 0) { perror("BPF"); return 1; }
    strlcpy(interface.ifr_name, reply ? "en5" : "en0", sizeof(interface.ifr_name));
    if (ioctl(fd, BIOCSETIF, &interface) || ioctl(fd, BIOCSHDRCMPLT, &one)) return 1;
    memcpy(frame, "\x00\x50\xc2\x01\x02\x00\x06\x7a\xfb\x78\xb2\x87\x08\x00", 14);
    if (reply) memcpy(frame + 6, "\x00\xe0\x4c\x16\x74\xb0", 6);
    unsigned char *ip = frame + 14, *udp = frame + 34;
    ip[0] = 0x45; put16(ip + 2, 1500); ip[8] = 64; ip[9] = 17;
    memcpy(ip + 12, "\xc0\xa8\x01\xd4\xc6\x12\x00\x02", 8);
    if (reply) memcpy(ip + 12, "\xc0\xa8\x01\xac\xc6\x12\x00\x01", 8);
    put16(ip + 10, checksum(ip, 20));
    put16(udp, 55000); put16(udp + 2, 55001); put16(udp + 4, 1480);
    if (reply) { put16(udp, 55001); put16(udp + 2, 55000); }
    memcpy(frame + 42, "TYFPON-SIM", 10);
    for (unsigned int i = 56; i < sizeof(frame); i++) frame[i] = (i * 31 + 7) & 255;
    memcpy(pseudo, ip + 12, 8); pseudo[9] = 17; put16(pseudo + 10, 1480);
    memcpy(pseudo + 12, udp, 1480);
    uint16_t check = checksum(pseudo, sizeof(pseudo));
    put16(udp + 6, check ? check : 65535);
    double started = now(); unsigned int sent = 0, previous = 0;
    for (; sent < packets; sent++) {
        uint32_t sum = (uint16_t)~check + (uint16_t)~(previous >> 16) +
            (sent >> 16) + (uint16_t)~previous + (uint16_t)sent;
        while (sum >> 16) sum = (sum & 65535) + (sum >> 16);
        check = ~sum; put16(udp + 6, check ? check : 65535);
        uint32_t sequence = htonl(sent); memcpy(frame + 52, &sequence, 4); previous = sent;
        if (write(fd, frame, sizeof(frame)) != sizeof(frame)) { perror("en0 write"); break; }
        double due = started + (sent + 1) * 1514.0 * 8 / (rate * 1000000.0);
        while (now() < due) {}
    }
    double elapsed = now() - started;
    printf("{\"interface\":\"%s\",\"requested\":%u,\"sent\":%u,\"target_Mbps\":%u,\"seconds\":%.6f,\"offered_Mbps\":%.3f}\n",
           interface.ifr_name, packets, sent, rate, elapsed, sent * 1514.0 * 8 / elapsed / 1000000);
    close(fd); return sent == packets ? 0 : 1;
}
