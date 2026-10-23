/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_QM_LAYOUT_H
#define RTL9617C_QM_LAYOUT_H

/* TYFZA1 ca-ne.ko: aal_l3qm_init_empty_buffer and the EPP64 RX decoder. */
#define RTL_QM_BUFFER_BYTES 2048
#define RTL_QM_HEADROOM 64
#define RTL_QM_TAILROOM 384
#define RTL_QM_DATA_BYTES (RTL_QM_BUFFER_BYTES - RTL_QM_HEADROOM - RTL_QM_TAILROOM)
#define RTL_QM_PACKET_LAYOUT 0x18041804

#endif
