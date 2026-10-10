/* SPDX-License-Identifier: GPL-2.0-only */
/* Bind the Darwin TCP socket to en5 and fix real DNS addresses.
 * Run via an independent LaunchAgent: an application Network Extension can
 * otherwise divert a Codex child even when its socket is bound to en5.
 * Usage: mac_lan_speedtest host IPv4 path download|upload [range|file] */
#include <curl/curl.h>
#include <net/if.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double monotonic(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int bind_lan(void *data, curl_socket_t socket, curlsocktype purpose)
{
    unsigned int index = *(unsigned int *)data;
    if (setsockopt(socket, IPPROTO_IP, IP_BOUND_IF, &index, sizeof(index))) {
        perror("IP_BOUND_IF en5");
        return CURL_SOCKOPT_ERROR;
    }
    return CURL_SOCKOPT_OK;
}
static size_t discard(void *data, size_t size, size_t count, void *context)
{
    return size * count;
}
int main(int argc, char **argv)
{
    unsigned int index = if_nametoindex("en5");
    char url[512], resolve[256], error[CURL_ERROR_SIZE] = {};
    struct curl_slist *dns = NULL, *headers = NULL;
    CURL *curl;
    CURLcode result;
    FILE *input = NULL;
    curl_off_t downloaded = 0, uploaded = 0;
    double seconds = 0, download_speed = 0, upload_speed = 0;
    double started, finished;
    long code = 0;
    char *local = NULL, *remote = NULL;
    int upload;

    if (argc != 6 || !index) return 1;
    upload = !strcmp(argv[4], "upload");
    snprintf(url, sizeof(url), "https://%s%s", argv[1], argv[3]);
    snprintf(resolve, sizeof(resolve), "%s:443:%s", argv[1], argv[2]);
    curl_global_init(CURL_GLOBAL_DEFAULT);
    curl = curl_easy_init();
    dns = curl_slist_append(dns, resolve);
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "curl/8.7.1");
    curl_easy_setopt(curl, CURLOPT_INTERFACE, "en5");
    curl_easy_setopt(curl, CURLOPT_RESOLVE, dns);
    curl_easy_setopt(curl, CURLOPT_PROXY, "");
    curl_easy_setopt(curl, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);
    curl_easy_setopt(curl, CURLOPT_SOCKOPTFUNCTION, bind_lan);
    curl_easy_setopt(curl, CURLOPT_SOCKOPTDATA, &index);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 8L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 25L);
    curl_easy_setopt(curl, CURLOPT_ERRORBUFFER, error);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, discard);
    if (upload) {
        struct stat info;
        input = fopen(argv[5], "rb");
        if (!input || fstat(fileno(input), &info)) return 1;
        headers = curl_slist_append(headers, "Content-Type: application/octet-stream");
        headers = curl_slist_append(headers, "Expect:");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_READDATA, input);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE, (curl_off_t)info.st_size);
    } else {
        curl_easy_setopt(curl, CURLOPT_RANGE, argv[5]);
    }
    started = monotonic();
    result = curl_easy_perform(curl);
    finished = monotonic();
    curl_easy_getinfo(curl, CURLINFO_SIZE_DOWNLOAD_T, &downloaded);
    curl_easy_getinfo(curl, CURLINFO_SIZE_UPLOAD_T, &uploaded);
    curl_easy_getinfo(curl, CURLINFO_TOTAL_TIME, &seconds);
    curl_easy_getinfo(curl, CURLINFO_SPEED_DOWNLOAD, &download_speed);
    curl_easy_getinfo(curl, CURLINFO_SPEED_UPLOAD, &upload_speed);
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_getinfo(curl, CURLINFO_LOCAL_IP, &local);
    curl_easy_getinfo(curl, CURLINFO_PRIMARY_IP, &remote);
    printf("{\"direction\":\"%s\",\"IP_BOUND_IF\":%u,\"local\":\"%s\",\"remote\":\"%s\",\"bytes\":%lld,\"seconds\":%.6f,\"Mbps\":%.3f,\"http_code\":%ld,\"curl_result\":%d,\"started_monotonic\":%.6f,\"finished_monotonic\":%.6f}\n",
           upload ? "upload" : "download", index, local ? local : "", remote ? remote : "",
           (long long)(upload ? uploaded : downloaded), seconds,
           (upload ? upload_speed : download_speed) * 8 / 1000000, code, result, started, finished);
    if (result) fprintf(stderr, "%s\n", error);
    if (input) fclose(input);
    curl_slist_free_all(headers);
    curl_slist_free_all(dns);
    curl_easy_cleanup(curl);
    curl_global_cleanup();
    return result || code < 200 || code >= 300;
}
