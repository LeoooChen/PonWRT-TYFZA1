/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_PEER_LOOKUP_H
#define RTL_AX_PEER_LOOKUP_H
#include "role.h"

/* Caller holds RCU. Retained local/peer addresses distinguish the same STA
 * MAC on different BSSes; MACID rejects a replaced mac80211 station. */
static inline struct ieee80211_sta *rtl_ax_lookup_peer(struct ieee80211_hw *hw,
 const u8 *peer, const u8 *self, unsigned int macid)
{
 struct ieee80211_sta *sta;
 if(!macid || macid>=128)return NULL;
 sta=ieee80211_find_sta_by_ifaddr(hw,peer,self);
 return sta && ((struct rtl_ax_station *)sta->drv_priv)->macid==macid ? sta : NULL;
}
#endif
