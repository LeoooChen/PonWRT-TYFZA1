/* SPDX-License-Identifier: GPL-2.0-only */
/* OLT wire requests must preserve each GEM CTP's direction through classification. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"
#include "rtl9617c-pon-path.h"

static unsigned int transaction;
static void be16(uint8_t *p, unsigned int value) { p[0] = value >> 8; p[1] = value; }
static unsigned int le16(const uint8_t *p) { return p[0] | p[1] << 8; }
static void request(struct omci_agent *agent, unsigned int action, unsigned int cls,
                    unsigned int entity, const uint8_t *body, unsigned int length)
{
 uint8_t frame[44] = {}, reply[2048];
 be16(frame, ++transaction); frame[2] = 0x40 | action; frame[3] = 10;
 be16(frame + 4, cls); be16(frame + 6, entity); frame[43] = 0x28;
 memcpy(frame + 8, body, length);
 assert(tyfza1_omci_dispatch(agent, frame, sizeof(frame), reply, 0) == 44 && reply[8] == 0);
}

int main(void)
{
 for (unsigned int direction = 1; direction <= 3; direction++)
 for (unsigned int mapper = 0; mapper < 3; mapper++)
 for (unsigned int filtered = 0; filtered < 2; filtered++) {
  struct omci_identity identity = {.serial = {'T','E','S','T',1,2,3,4}};
  struct omci_agent *agent = tyfza1_omci_new(&identity);
  uint8_t body[32] = {}, wire[4096];
  if (direction & 1) {
   body[0] = 0x80; be16(body + 2, 1063); request(agent, 8, 262, 0x8000, body, 4);
  }
  memset(body, 0, sizeof(body)); be16(body, 4000);
  be16(body + 2, direction & 1 ? 0x8000 : 0xffff); body[4] = direction;
  be16(body + 5, direction & 1 ? 0x8007 : 0xffff);
  be16(body + 7, 0xffff); be16(body + 9, 0xffff); be16(body + 11, 0xffff);
  request(agent, 4, 268, 0x100, body, 13);
  memset(body, 0, sizeof(body)); be16(body, 0x100); body[2] = 5;
  be16(body + 3, 0xffff); be16(body + 5, 0xffff);
  request(agent, 4, 266, 0x300, body, 10);
  if (mapper) {
   memset(body, 0, sizeof(body)); be16(body, 0xffff);
   for (unsigned int i = 0; i < 8; i++)
    be16(body + 2 + i * 2, mapper == 2 && i ? 0xffff : 0x300);
   request(agent, 4, 130, 0x200, body, 24);
  }
  memset(body, 0, sizeof(body)); be16(body, 1); body[2] = 1;
  body[3] = mapper ? 3 : 5; be16(body + 4, mapper ? 0x200 : 0x300);
  request(agent, 4, 47, 0x400, body, 14);
  if (filtered) {
   memset(body, 0, sizeof(body)); be16(body, (5 << 13) | 100);
   body[24] = 0x0c; body[25] = 1; /* VID + PCP match, tagged only. */
   request(agent, 4, 84, 0x400, body, 26);
  }
  int length = tyfza1_omci_paths(agent, wire);
  unsigned int mask = filtered ? 1 << 5 : 255;
  unsigned int upstream = direction & 1 ? mask & (mapper == 2 ? 1 : 255) : 0;
  unsigned int downstream = direction & 2 ? mask : 0;
  if (!(upstream | downstream)) {
   assert(length == 4);
   tyfza1_omci_free(agent);
   continue;
  }
  if (length != 17) fprintf(stderr, "direction=%u mapper=%u filtered=%u paths_bytes=%d\n",
                           direction, mapper, filtered, length);
  assert(length == 17);
  struct rtl_itu_paths paths = {.count = 1};
  struct rtl_itu_path *path = &paths.path[0];
  *path = (struct rtl_itu_path){.alloc_id = le16(wire + 4), .gem_id = le16(wire + 6),
   .vlan = le16(wire + 8), .pbits = wire[10], .multicast = wire[11],
   .queue = wire[12], .encrypted = wire[13], .tcont = le16(wire + 14),
   .downstream_pbits = wire[16], .index = 8};
  assert(path->pbits == upstream);
  assert(path->downstream_pbits == downstream);
  assert(path->tcont == (direction & 1 ? 1 : 0));
  assert(path->alloc_id == (direction & 1 ? 1063 : 65535));
  for (unsigned int pcp = 0; pcp < 8; pcp++) {
   assert(!!rtl_itu_path_select(&paths, filtered ? 100 : 65535, pcp) ==
          !!(upstream & (1 << pcp)));
   assert(!!rtl_itu_path_downstream(&paths, filtered ? 100 : 65535, 1 << pcp) ==
          !!(downstream & (1 << pcp)));
  }
  assert(!!rtl_itu_path_flow(&paths, filtered ? 100 : 65535) == (upstream == 255));
  tyfza1_omci_free(agent);
 }
 puts("GEM directions: upstream/downstream/bidirectional, direct/mapper, VLAN+PCP filters and FE classification passed");
}
