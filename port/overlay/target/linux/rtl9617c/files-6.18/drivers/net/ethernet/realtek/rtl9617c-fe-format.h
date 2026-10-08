/* SPDX-License-Identifier: GPL-2.0-or-later */
/* RTL9617C L3FE native Hash type0, normal action groups18+20.
 * Hardware format: Cortina GPL aal-77c/include/aal_common.h.
 * This format is 224 bits within a 256-bit DRAM/cache action slot. */
#ifndef RTL9617C_FE_FORMAT_H
#define RTL9617C_FE_FORMAT_H

#define RTL_FE_ACTION_WORDS 8
#define RTL_FE_ACTION_GROUPS 0x00140000U

enum rtl_fe_action_field {
	RTL_FE_MRR_VLD,
	RTL_FE_MRR_EN,
	RTL_FE_NO_DROP_VLD,
	RTL_FE_NO_DROP,
	RTL_FE_DPID_VLD,
	RTL_FE_DPID_PRI,
	RTL_FE_PERMIT,
	RTL_FE_DEEPQ,
	RTL_FE_MCGID,
	RTL_FE_MC,
	RTL_FE_MDATA_BYTE_VLD,
	RTL_FE_MDATA_BYTE,
	RTL_FE_L3_IF_VLD1,
	RTL_FE_SMAC_TRANS,
	RTL_FE_IGR_L3_IF_IDX1,
	RTL_FE_EGR_L3_IF_IDX1,
	RTL_FE_L3_IF_COUNTER_EN,
	RTL_FE_IP_TTL_DEC1,
	RTL_FE_IP_TTL_ZERO_DISCARD_EN1,
	RTL_FE_IP_ADDR_VLD,
	RTL_FE_IP_TYPE,
	RTL_FE_IP_ADDR,
	RTL_FE_IP_ADDR_NAPT6,
	RTL_FE_MAC_DA_IDX_VLD,
	RTL_FE_MAC_DA_IDX,
	RTL_FE_CHK_MSK_PTR1,
	RTL_FE_CACHE_CTRL1,
	RTL_FE_POP_L3_VLD1,
	RTL_FE_POP_L3_CHK_ECN_EN1,
	RTL_FE_POP_L3_EN1,
	RTL_FE_T2_CTRL_VLD1,
	RTL_FE_T2_CTRL1,
	RTL_FE_LDPID_OFFSET_MSB,
	RTL_FE_IP_DSCP_UPDATE_EN1,
	RTL_FE_IP_DSCP1,
	RTL_FE_COS_UPDATE_EN,
	RTL_FE_COS1,
	RTL_FE_INNER_802_1P_UPDATE_EN,
	RTL_FE_INNER_802_1P1,
	RTL_FE_TOP_802_1P_UPDATE_EN,
	RTL_FE_TOP_802_1P1,
	RTL_FE_INNER_DEI1,
	RTL_FE_INNER_VID1,
	RTL_FE_INNER_TPID_ENC1,
	RTL_FE_TOP_DEI1,
	RTL_FE_TOP_VID1,
	RTL_FE_TOP_TPID_ENC1,
	RTL_FE_VLAN_CNT1,
	RTL_FE_VLAN_VLD1,
	RTL_FE_POL_VLD1,
	RTL_FE_POL_EN1,
	RTL_FE_POL_ID1,
	RTL_FE_POL2_ID_ENABLE1,
	RTL_FE_POL2_ID1,
	RTL_FE_POL3_ID_ENABLE1,
	RTL_FE_POL3_ID1,
	RTL_FE_PPPOE_VLD1,
	RTL_FE_PPPOE_SET1,
	RTL_FE_L4_PORT,
	RTL_FE_IP_MTU_ENC_VLD1,
	RTL_FE_IP_MTU_ENC1,
	RTL_FE_MODIFY_VLAN_ONLY_VLD,
	RTL_FE_MODIFY_VLAN_ONLY,
	RTL_FE_SIX_RD_DIP_REMAP_OR_FMR_IDX_VLD,
	RTL_FE_SIX_RD_DIP_REMAP_OR_FMR_IDX,
	RTL_FE_VXLAN_SPORT_MSB15,
	RTL_FE_VXLAN_SPORT_UPDATE_EN,
	RTL_FE_FIELD_COUNT,
};

struct rtl_fe_field_layout { unsigned short bit; unsigned char width; };
static const struct rtl_fe_field_layout rtl_fe_action_fields[RTL_FE_FIELD_COUNT] = {
	[RTL_FE_MRR_VLD] = {0, 1},
	[RTL_FE_MRR_EN] = {1, 1},
	[RTL_FE_NO_DROP_VLD] = {2, 1},
	[RTL_FE_NO_DROP] = {3, 1},
	[RTL_FE_DPID_VLD] = {4, 1},
	[RTL_FE_DPID_PRI] = {5, 1},
	[RTL_FE_PERMIT] = {6, 1},
	[RTL_FE_DEEPQ] = {7, 1},
	[RTL_FE_MCGID] = {8, 10},
	[RTL_FE_MC] = {18, 1},
	[RTL_FE_MDATA_BYTE_VLD] = {19, 1},
	[RTL_FE_MDATA_BYTE] = {20, 8},
	[RTL_FE_L3_IF_VLD1] = {28, 1},
	[RTL_FE_SMAC_TRANS] = {29, 1},
	[RTL_FE_IGR_L3_IF_IDX1] = {30, 6},
	[RTL_FE_EGR_L3_IF_IDX1] = {36, 6},
	[RTL_FE_L3_IF_COUNTER_EN] = {42, 1},
	[RTL_FE_IP_TTL_DEC1] = {43, 1},
	[RTL_FE_IP_TTL_ZERO_DISCARD_EN1] = {44, 1},
	[RTL_FE_IP_ADDR_VLD] = {45, 1},
	[RTL_FE_IP_TYPE] = {46, 1},
	[RTL_FE_IP_ADDR] = {47, 32},
	[RTL_FE_IP_ADDR_NAPT6] = {79, 1},
	[RTL_FE_MAC_DA_IDX_VLD] = {80, 1},
	[RTL_FE_MAC_DA_IDX] = {81, 13},
	[RTL_FE_CHK_MSK_PTR1] = {94, 6},
	[RTL_FE_CACHE_CTRL1] = {100, 2},
	[RTL_FE_POP_L3_VLD1] = {102, 1},
	[RTL_FE_POP_L3_CHK_ECN_EN1] = {103, 1},
	[RTL_FE_POP_L3_EN1] = {104, 1},
	[RTL_FE_T2_CTRL_VLD1] = {105, 1},
	[RTL_FE_T2_CTRL1] = {106, 4},
	[RTL_FE_LDPID_OFFSET_MSB] = {110, 1},
	[RTL_FE_IP_DSCP_UPDATE_EN1] = {111, 1},
	[RTL_FE_IP_DSCP1] = {112, 6},
	[RTL_FE_COS_UPDATE_EN] = {118, 1},
	[RTL_FE_COS1] = {119, 3},
	[RTL_FE_INNER_802_1P_UPDATE_EN] = {122, 1},
	[RTL_FE_INNER_802_1P1] = {123, 3},
	[RTL_FE_TOP_802_1P_UPDATE_EN] = {126, 1},
	[RTL_FE_TOP_802_1P1] = {127, 3},
	[RTL_FE_INNER_DEI1] = {130, 1},
	[RTL_FE_INNER_VID1] = {131, 12},
	[RTL_FE_INNER_TPID_ENC1] = {143, 3},
	[RTL_FE_TOP_DEI1] = {146, 1},
	[RTL_FE_TOP_VID1] = {147, 12},
	[RTL_FE_TOP_TPID_ENC1] = {159, 3},
	[RTL_FE_VLAN_CNT1] = {162, 2},
	[RTL_FE_VLAN_VLD1] = {164, 1},
	[RTL_FE_POL_VLD1] = {165, 1},
	[RTL_FE_POL_EN1] = {166, 1},
	[RTL_FE_POL_ID1] = {167, 8},
	[RTL_FE_POL2_ID_ENABLE1] = {175, 1},
	[RTL_FE_POL2_ID1] = {176, 6},
	[RTL_FE_POL3_ID_ENABLE1] = {182, 1},
	[RTL_FE_POL3_ID1] = {183, 6},
	[RTL_FE_PPPOE_VLD1] = {189, 1},
	[RTL_FE_PPPOE_SET1] = {190, 1},
	[RTL_FE_L4_PORT] = {191, 16},
	[RTL_FE_IP_MTU_ENC_VLD1] = {207, 1},
	[RTL_FE_IP_MTU_ENC1] = {208, 4},
	[RTL_FE_MODIFY_VLAN_ONLY_VLD] = {212, 1},
	[RTL_FE_MODIFY_VLAN_ONLY] = {213, 1},
	[RTL_FE_SIX_RD_DIP_REMAP_OR_FMR_IDX_VLD] = {214, 1},
	[RTL_FE_SIX_RD_DIP_REMAP_OR_FMR_IDX] = {215, 2},
	[RTL_FE_VXLAN_SPORT_MSB15] = {217, 6},
	[RTL_FE_VXLAN_SPORT_UPDATE_EN] = {223, 1},
};

static inline void rtl_fe_bits_set(unsigned int *words, unsigned int bit,
 unsigned int width, unsigned int value)
{
 unsigned int word = bit / 32, shift = bit % 32;
 unsigned int mask = width == 32 ? ~0U : (1U << width) - 1;

 value &= mask;
 words[word] = (words[word] & ~(mask << shift)) | (value << shift);
 if (shift + width > 32)
  words[word + 1] = (words[word + 1] & ~(mask >> (32 - shift))) | (value >> (32 - shift));
}

/* The fixed field layout guarantees each value spans at most two words. */
static inline void rtl_fe_action_set(unsigned int words[RTL_FE_ACTION_WORDS],
 enum rtl_fe_action_field field, unsigned int value)
{
 const struct rtl_fe_field_layout *layout = &rtl_fe_action_fields[field];
 rtl_fe_bits_set(words, layout->bit, layout->width, value);
}

/* Source/destination IPv4 NAT share one address/port rewrite selector per
 * directional flow. Indexed DA and egress interface supply Ethernet rewrite. */
struct rtl_fe_ipv4_action {
 unsigned int address;
 unsigned short port, destination, mac_da_index;
 unsigned char ingress_interface, egress_interface, cos;
 unsigned char translate, translate_destination, deep_queue;
};

static inline void rtl_fe_ipv4_action(unsigned int words[RTL_FE_ACTION_WORDS],
 const struct rtl_fe_ipv4_action *action)
{
 unsigned int i;

 for (i = 0; i < RTL_FE_ACTION_WORDS; i++)
  words[i] = 0;
 rtl_fe_action_set(words, RTL_FE_DPID_VLD, 1);
 rtl_fe_action_set(words, RTL_FE_DPID_PRI, 1);
 rtl_fe_action_set(words, RTL_FE_PERMIT, 1);
 rtl_fe_action_set(words, RTL_FE_DEEPQ, action->deep_queue);
 rtl_fe_action_set(words, RTL_FE_MCGID, action->destination);
 rtl_fe_action_set(words, RTL_FE_L3_IF_VLD1, 1);
 rtl_fe_action_set(words, RTL_FE_SMAC_TRANS, 1);
 rtl_fe_action_set(words, RTL_FE_IGR_L3_IF_IDX1, action->ingress_interface);
 rtl_fe_action_set(words, RTL_FE_EGR_L3_IF_IDX1, action->egress_interface);
 rtl_fe_action_set(words, RTL_FE_IP_TTL_DEC1, 1);
 rtl_fe_action_set(words, RTL_FE_IP_TTL_ZERO_DISCARD_EN1, 1);
 rtl_fe_action_set(words, RTL_FE_MAC_DA_IDX_VLD, 1);
 rtl_fe_action_set(words, RTL_FE_MAC_DA_IDX, action->mac_da_index);
 rtl_fe_action_set(words, RTL_FE_COS_UPDATE_EN, 1);
 rtl_fe_action_set(words, RTL_FE_COS1, action->cos);
 rtl_fe_action_set(words, RTL_FE_IP_ADDR_VLD, action->translate);
 rtl_fe_action_set(words, RTL_FE_IP_TYPE, action->translate_destination);
 rtl_fe_action_set(words, RTL_FE_IP_ADDR, action->address);
 rtl_fe_action_set(words, RTL_FE_L4_PORT, action->port);
}

#define RTL_FE_KEY_WORDS 23
enum rtl_fe_key_field {
	RTL_FE_KEY_L4_CHKSUM_ZERO,
	RTL_FE_KEY_TCP_RDP_CTRL,
	RTL_FE_KEY_L4_DP_EXACT_RANGE,
	RTL_FE_KEY_L4_SP_EXACT_RANGE,
	RTL_FE_KEY_L3_CHKSUM_ERR,
	RTL_FE_KEY_IP_FRAGMENT_FLAG,
	RTL_FE_KEY_IP_OPTIONS,
	RTL_FE_KEY_IP_DA_0,
	RTL_FE_KEY_IP_SA_0,
	RTL_FE_KEY_IP_PROTOCOL,
	RTL_FE_KEY_IP_VER,
	RTL_FE_KEY_IP_VLD,
	RTL_FE_KEY_PPPOE_SESSION_ID,
	RTL_FE_KEY_INNER_VID,
	RTL_FE_KEY_INNER_TPID_ENC,
	RTL_FE_KEY_TOP_VID,
	RTL_FE_KEY_TOP_TPID_ENC,
	RTL_FE_KEY_VLAN_CNT,
	RTL_FE_KEY_POL_ID,
	RTL_FE_KEY_ORIG_LSPID,
	RTL_FE_KEY_LSPID,
	RTL_FE_KEY_HKEY_ID,
	RTL_FE_KEY_CTRL_SET_ID,
	RTL_FE_KEY_TABLE_ID,
	RTL_FE_KEY_FIELD_COUNT,
};
static const struct rtl_fe_field_layout rtl_fe_key_fields[RTL_FE_KEY_FIELD_COUNT] = {
	[RTL_FE_KEY_L4_CHKSUM_ZERO] = {0, 1},
	[RTL_FE_KEY_TCP_RDP_CTRL] = {1, 9},
	[RTL_FE_KEY_L4_DP_EXACT_RANGE] = {10, 16},
	[RTL_FE_KEY_L4_SP_EXACT_RANGE] = {26, 16},
	[RTL_FE_KEY_L3_CHKSUM_ERR] = {42, 1},
	[RTL_FE_KEY_IP_FRAGMENT_FLAG] = {92, 1},
	[RTL_FE_KEY_IP_OPTIONS] = {94, 1},
	[RTL_FE_KEY_IP_DA_0] = {123, 32},
	[RTL_FE_KEY_IP_SA_0] = {251, 32},
	[RTL_FE_KEY_IP_PROTOCOL] = {382, 8},
	[RTL_FE_KEY_IP_VER] = {398, 1},
	[RTL_FE_KEY_IP_VLD] = {399, 1},
	[RTL_FE_KEY_PPPOE_SESSION_ID] = {404, 16},
	[RTL_FE_KEY_INNER_VID] = {430, 12},
	[RTL_FE_KEY_INNER_TPID_ENC] = {442, 3},
	[RTL_FE_KEY_TOP_VID] = {449, 12},
	[RTL_FE_KEY_TOP_TPID_ENC] = {461, 3},
	[RTL_FE_KEY_VLAN_CNT] = {464, 2},
	[RTL_FE_KEY_POL_ID] = {682, 9},
	[RTL_FE_KEY_ORIG_LSPID] = {706, 6},
	[RTL_FE_KEY_LSPID] = {712, 6},
	[RTL_FE_KEY_HKEY_ID] = {718, 6},
	[RTL_FE_KEY_CTRL_SET_ID] = {724, 4},
	[RTL_FE_KEY_TABLE_ID] = {728, 4},
};

static inline void rtl_fe_key_set(unsigned int words[RTL_FE_KEY_WORDS],
 enum rtl_fe_key_field field, unsigned int value)
{
 const struct rtl_fe_field_layout *layout = &rtl_fe_key_fields[field];
 rtl_fe_bits_set(words, layout->bit, layout->width, value);
}

/* Addresses and ports are numerical host-order values. This representation
 * matches the parser's complete 736-bit key; unselected fields stay zero. */
struct rtl_fe_ipv4_key {
 unsigned int source, destination;
 unsigned short source_port, destination_port, llid;
 unsigned char protocol, ingress, profile, mask;
};

static inline void rtl_fe_ipv4_key(unsigned int words[RTL_FE_KEY_WORDS],
 const struct rtl_fe_ipv4_key *key)
{
 unsigned int i;

 for (i = 0; i < RTL_FE_KEY_WORDS; i++)
  words[i] = 0;
 rtl_fe_key_set(words, RTL_FE_KEY_L4_SP_EXACT_RANGE, key->source_port);
 rtl_fe_key_set(words, RTL_FE_KEY_L4_DP_EXACT_RANGE, key->destination_port);
 rtl_fe_key_set(words, RTL_FE_KEY_IP_SA_0, key->source);
 rtl_fe_key_set(words, RTL_FE_KEY_IP_DA_0, key->destination);
 rtl_fe_key_set(words, RTL_FE_KEY_IP_PROTOCOL, key->protocol);
 rtl_fe_key_set(words, RTL_FE_KEY_IP_VLD, 1);
 rtl_fe_key_set(words, RTL_FE_KEY_POL_ID, key->llid);
 rtl_fe_key_set(words, RTL_FE_KEY_ORIG_LSPID, key->ingress);
 rtl_fe_key_set(words, RTL_FE_KEY_LSPID, key->ingress);
 rtl_fe_key_set(words, RTL_FE_KEY_HKEY_ID, key->mask);
 rtl_fe_key_set(words, RTL_FE_KEY_CTRL_SET_ID, key->profile);
 rtl_fe_key_set(words, RTL_FE_KEY_TABLE_ID, 1);
}

static inline unsigned int rtl_fe_reverse(unsigned int value, unsigned int bits)
{
 unsigned int result = 0, i;

 for (i = 0; i < bits; i++) {
  result = (result << 1) | (value & 1);
  value >>= 1;
 }
 return result;
}

/* Cortina hash_value_calculate reverses the complete masked key, runs
 * reflected CCITT16/IEEE32 from all-one seeds, then reverses each result.
 * Neither CRC uses a final XOR. Tuple rotations are configured separately. */
static inline void rtl_fe_key_crc(const unsigned int words[RTL_FE_KEY_WORDS],
 unsigned int *crc32, unsigned short *crc16)
{
 unsigned int c32 = ~0U, c16 = 0xffffU, byte, bit;

 for (byte = RTL_FE_KEY_WORDS * 4; byte; byte--) {
  unsigned int index = byte - 1;
  unsigned int value = rtl_fe_reverse((words[index / 4] >> ((index % 4) * 8)) & 255, 8);

  c32 ^= value;
  c16 ^= value;
  for (bit = 0; bit < 8; bit++) {
   c32 = (c32 >> 1) ^ ((c32 & 1) ? 0xedb88320U : 0);
   c16 = (c16 >> 1) ^ ((c16 & 1) ? 0x8408U : 0);
  }
 }
 *crc32 = rtl_fe_reverse(c32, 32);
 *crc16 = rtl_fe_reverse(c16, 16);
}

#define RTL_FE_MASK_WORDS 8
/* All unselected parser fields are masked. IPv4 /32 uses the prefix-length
 * encoding; protocol/ports/ingress/LLID/profile distinguish directional flows.
 * Zero in error/fragment/options flags makes those packets miss the flow. */
static inline void rtl_fe_ipv4_mask(unsigned int words[RTL_FE_MASK_WORDS])
{
 unsigned int i;
 for (i = 0; i < 7; i++)
  words[i] = ~0U;
 words[7] = 0;
 words[0] = (words[0] & ~0x07fffc00U) | 0x00000000U; /* l4_dp_exact_range */
 words[0] = (words[0] & ~0xf8000000U) | 0x00000000U; /* l4_sp_exact_range */
 words[1] = (words[1] & ~0x00000fffU) | 0x00000000U;
 words[1] = (words[1] & ~0x00001000U) | 0x00000000U; /* l3_chksum_err */
 words[1] = (words[1] & ~0x00100000U) | 0x00000000U; /* ip_fragment_flag */
 words[1] = (words[1] & ~0x00400000U) | 0x00000000U; /* ip_options */
 words[2] = (words[2] & ~0x00000ff8U) | 0x00000100U; /* ip_sa */
 words[1] = (words[1] & ~0xfc000000U) | 0x80000000U; /* ip_da */
 words[2] = (words[2] & ~0x00000007U) | 0x00000000U;
 words[2] = (words[2] & ~0x00002000U) | 0x00000000U; /* ip_protocol */
 words[2] = (words[2] & ~0x00800000U) | 0x00000000U; /* ip_vld */
 words[2] = (words[2] & ~0x00400000U) | 0x00000000U; /* ip_ver */
 words[6] = (words[6] & ~0x00800000U) | 0x00000000U; /* orig_lspid */
 words[6] = (words[6] & ~0x01000000U) | 0x00000000U; /* lspid */
 words[6] = (words[6] & ~0x00000200U) | 0x00000000U; /* pol_id */
 words[6] = (words[6] & ~0x04000000U) | 0x00000000U; /* ctrl_set_id */
}

#define RTL_FE_HEADER_WORDS 32
/* Header-I is the hardware parser's 1024-bit representation. The SWO CRC
 * port takes this header, rather than the 736-bit software hash key. */
static inline void rtl_fe_header_ipv4(unsigned int words[RTL_FE_HEADER_WORDS],
 const struct rtl_fe_ipv4_key *key)
{
 unsigned int i;
 for (i = 0; i < RTL_FE_HEADER_WORDS; i++)
  words[i] = 0;
 rtl_fe_bits_set(words, 74, 16, key->destination_port); /* l4_dp */
 rtl_fe_bits_set(words, 90, 16, key->source_port); /* l4_sp */
 rtl_fe_bits_set(words, 233, 32, key->destination); /* ip_da_0 */
 rtl_fe_bits_set(words, 361, 32, key->source); /* ip_sa_0 */
 rtl_fe_bits_set(words, 492, 8, key->protocol); /* ip_protocol */
 rtl_fe_bits_set(words, 510, 1, 1); /* ip_vld */
 rtl_fe_bits_set(words, 802, 8, key->llid); /* pol_id */
 rtl_fe_bits_set(words, 831, 6, key->ingress); /* o_lspid */
 rtl_fe_bits_set(words, 837, 6, key->ingress); /* lspid */
 rtl_fe_bits_set(words, 978, 4, key->profile); /* t2_ctrl */
}
#endif
