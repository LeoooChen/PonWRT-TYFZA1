/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_MCAST_GATE_H
#define RTL9617C_FE_MCAST_GATE_H
#include <linux/errno.h>
#include "rtl9617c-qm-deepq.h"

/* The owner holds its short IRQ-safe gate lock. Only the tuple count is
 * touched here: explicit TC tuples0..2 remain available, learned tuple3
 * is hidden before bridge state changes. No indirect engine or FE mutex. */
struct rtl_fe_mc_gate {
 unsigned long long epoch;
 unsigned int writers;
 unsigned char active;
 int error;
};
static inline void rtl_fe_mc_gate_close(const struct rtl_dq_io *io,struct rtl_fe_mc_gate *gate)
{
 if(!gate->active)return;
 io->write(io->context,0x372c,(io->read(io->context,0x372c)&~15U)|3);
 if((io->read(io->context,0x372c)&15)!=3)gate->error=-EIO;
 else gate->active=0;
}
static inline void rtl_fe_mc_gate_begin(const struct rtl_dq_io *io,struct rtl_fe_mc_gate *gate)
{
 gate->epoch++;
 gate->writers++;
 rtl_fe_mc_gate_close(io,gate);
}
static inline void rtl_fe_mc_gate_end(struct rtl_fe_mc_gate *gate)
{
 gate->epoch++;
 if(gate->writers)gate->writers--;
 else gate->error=-EUCLEAN;
}
/* Called only after all older learned Hashes have withdrawn and the complete
 * new recipient set is installed. Publication and writers share this lock. */
static inline int rtl_fe_mc_gate_open(const struct rtl_dq_io *io,struct rtl_fe_mc_gate *gate,
 unsigned long long epoch)
{
 if(gate->error)return gate->error;
 if(gate->writers || gate->epoch!=epoch)return -ESTALE;
 if((io->read(io->context,0x372c)&15)!=3 && !gate->active)return -EUCLEAN;
 /* Mark before IO so even uncertain completion is retried by close. */
 gate->active=1;
 io->write(io->context,0x372c,(io->read(io->context,0x372c)&~15U)|4);
 if((io->read(io->context,0x372c)&15)!=4) {
  gate->error=-EIO;
  rtl_fe_mc_gate_close(io,gate);
  return -EIO;
 }
 return 0;
}
#endif
