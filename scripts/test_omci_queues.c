/* SPDX-License-Identifier: GPL-2.0-only */
/* OLT queue mutations must reach the PUC payload before their successful ACK. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"

static uint8_t frame[44], reply[2048], applied[65536];
static unsigned int transaction, calls;
static int fail, applied_length;
static void be16(uint8_t *p, unsigned int value) { p[0] = value >> 8; p[1] = value; }
static unsigned int le16(const uint8_t *p) { return p[0] | p[1] << 8; }
static int apply(void *context, const uint8_t *paths, size_t length)
{
 (void)context;
 assert(reply[8] == 0xa5 && le16(paths) == 4);
 calls++;
 if (fail) return fail;
 memcpy(applied, paths, length); applied_length = length;
 return 0;
}
static unsigned int request(struct omci_agent *agent, unsigned int action, unsigned int cls,
 unsigned int entity, const uint8_t *body, unsigned int length)
{
 memset(frame, 0, sizeof(frame)); memset(reply, 0xa5, sizeof(reply));
 be16(frame, ++transaction); frame[2] = 0x40 | action; frame[3] = 10;
 be16(frame + 4, cls); be16(frame + 6, entity); frame[43] = 0x28;
 memcpy(frame + 8, body, length);
 int error = 0;
 assert(tyfza1_omci_dispatch_apply(agent, frame, sizeof(frame), reply, &error, apply, NULL) == 44);
 return reply[8];
}
static unsigned int set(struct omci_agent *agent, unsigned int cls, unsigned int entity,
 unsigned int index, const uint8_t *value, unsigned int length)
{
 uint8_t body[8] = {};
 be16(body, 0x8000U >> (index - 1)); memcpy(body + 2, value, length);
 return request(agent, 8, cls, entity, body, length + 2);
}
static void path(struct omci_agent *agent, unsigned int n)
{
 uint8_t body[32] = {};
 be16(body, 4000 + n); be16(body + 2, 0x8000); body[4] = 3;
 be16(body + 5, 0x8000 + n); be16(body + 7, 0xffff); be16(body + 9, 0xffff); be16(body + 11, 0xffff);
 assert(!request(agent, 4, 268, 0x100 + n, body, 13));
 memset(body, 0, sizeof(body)); be16(body, 0x100 + n); body[2] = 5;
 be16(body + 3, 0xffff); be16(body + 5, 0xffff);
 assert(!request(agent, 4, 266, 0x200 + n, body, 10));
 memset(body, 0, sizeof(body)); be16(body, 100 + n); body[24] = 0x04; body[25] = 1;
 assert(!request(agent, 4, 84, 0x300 + n, body, 26));
 memset(body, 0, sizeof(body)); be16(body, 1); body[2] = n + 1; body[3] = 5; be16(body + 4, 0x200 + n);
 assert(!request(agent, 4, 47, 0x300 + n, body, 14));
}
int main(int argc, char **argv)
{
 struct omci_identity identity = {.serial = {84, 69, 83, 84, 1, 2, 3, 4}};
 struct omci_agent *agent = tyfza1_omci_new(&identity);
 uint8_t value[4], before[65536];
 be16(value, 1063); assert(!set(agent, 262, 0x8000, 1, value, 2));
 be16(value, 2000); assert(!set(agent, 262, 0x8001, 1, value, 2));
 path(agent, 0); path(agent, 1);
 assert(applied_length == 112 && applied[20] == 0 && applied[70] == 1);
 unsigned int previous_calls = calls;
 be16(value, 0x8000); be16(value + 2, 5);
 assert(!set(agent, 277, 0x8000, 6, value, 4));
 assert(calls == previous_calls + 1 && applied[20] == 1 && applied[70] == 0);
 be16(value, 0x8001);
 assert(!set(agent, 277, 0x8000, 6, value, 4));
 assert(le16(applied + 12) == 2000 && le16(applied + 22) == 2 && applied[20] == 0);
 /* A later CTP T-CONT Set selects the existing queue on its target port. */
 be16(value, 0x8000); assert(!set(agent, 268, 0x100, 2, value, 2));
 assert(le16(applied + 12) == 1063 && le16(applied + 22) == 1 && applied[20] == applied[70]);
 be16(value, 0x8001); assert(!set(agent, 268, 0x100, 2, value, 2));
 assert(le16(applied + 12) == 2000 && le16(applied + 22) == 2);
 value[0] = 2; assert(!set(agent, 278, 0x8001, 3, value, 1));
 be16(value, 0x8001); assert(!set(agent, 277, 0x8000, 7, value, 2));
 assert(applied_length == 124 && le16(applied + 6) == 1 && le16(applied + 112) == 2);
 assert(applied[116] == 1 && applied[117] == 1);
 value[0] = 9; previous_calls = calls;
 assert(!set(agent, 277, 0x8000, 8, value, 1));
 assert(calls == previous_calls + 1 && applied[116] == 9 && applied[117] == 9);
 if (argc > 1) {
  FILE *file = fopen(argv[1], "wb");
  assert(file && fwrite(applied, 1, applied_length, file) == (size_t)applied_length);
  assert(!fclose(file));
 }
 memcpy(before, applied, applied_length);
 fail = -EIO; value[0] = 11;
 assert(set(agent, 277, 0x8000, 8, value, 1) == 1);
 assert(!memcmp(before, applied, applied_length));
 fail = 0;
 uint8_t get[] = {1, 0};
 assert(!request(agent, 9, 277, 0x8000, get, sizeof(get)) && reply[11] == 9);
 value[0] = 0; assert(set(agent, 277, 0x8000, 8, value, 1) == 1);
 assert(!request(agent, 9, 277, 0x8000, get, sizeof(get)) && reply[11] == 9);
 value[0] = 1; assert(!set(agent, 278, 0x8001, 3, value, 1));
 assert(applied_length == 112 && le16(applied + 6) == 0);
 be16(value, 0); assert(!set(agent, 277, 0x8000, 7, value, 2));
 be16(value, 0x8000); be16(value + 2, 5); assert(!set(agent, 277, 0x8000, 6, value, 4));
 value[0] = 2; assert(!set(agent, 278, 0x8000, 3, value, 1));
 be16(value, 0x8000); assert(!set(agent, 277, 0x8000, 7, value, 2));
 assert(applied_length == 124 && applied[20] == 0 && applied[70] == 2);
 assert(applied[116] == 9 && applied[117] == 9 && applied[118] == 0);
 /* Same-value SET must still consult a backend that may have lost readiness. */
 previous_calls = calls; fail = -EIO; value[0] = 9;
 assert(set(agent, 277, 0x8000, 8, value, 1) == 1 && calls == previous_calls + 1);
 fail = 0;
 /* Invalid CTP queue pointers fail atomically through both entry points. */
 for (unsigned int direct = 0; direct < 2; direct++) {
  uint8_t body[] = {0x10, 0, 0xff, 0xff};
  if (direct) {
   be16(frame, ++transaction); frame[2] = 0x48; frame[3] = 10;
   be16(frame + 4, 268); be16(frame + 6, 0x100);
   memcpy(frame + 8, body, sizeof(body));
   assert(tyfza1_omci_dispatch(agent, frame, sizeof(frame), reply, 0) == 44 && reply[8] == 1);
  } else assert(request(agent, 8, 268, 0x100, body, sizeof(body)) == 1);
  body[0] = 0x10; body[1] = 0;
  assert(!request(agent, 9, 268, 0x100, body, 2));
  assert(reply[11] == 0x80 && reply[12] == 0);
 }
 assert(!request(agent, 15, 2, 0, value, 0));
 assert(applied_length == 12 && le16(applied + 2) == 0 && le16(applied + 6) == 0);
 tyfza1_omci_free(agent);
 puts("OMCI queue SET: priority reorders GEM queues, RelatedPort moves T-CONT/Alloc-ID, scheduler policy and weight reach PUC before ACK, failed writes roll back");
}
