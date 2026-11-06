/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_MCAST_OWNER_H
#define RTL9617C_FE_MCAST_OWNER_H
#include "rtl9617c-fe-mcast.h"

/* All operations run under the existing FE mutex. A group owns action
 * references before touching hardware, including an uncertain failed write.
 * REFERENCED must be set BEFORE publishing a classifier/Hash that names it.
 * Such indices cannot be reused until a separately proven NI retirement.
 */
enum rtl_fe_mc_state { RTL_FE_MC_FREE, RTL_FE_MC_STAGED, RTL_FE_MC_REFERENCED,
 RTL_FE_MC_RETIRED };
struct rtl_fe_mc_action_owner {
 unsigned int words[3],users;
 unsigned char ready,exposed;
};
struct rtl_fe_mc_group_owner {
 unsigned int bitmap[2],held[2];
 enum rtl_fe_mc_state state;
 unsigned char ready,withdrawing;
};
struct rtl_fe_mc_owner {
 struct rtl_fe_mc_action_owner actions[RTL_FE_MC_ACTIONS];
 struct rtl_fe_mc_group_owner groups[RTL_FE_MC_GROUPS];
 unsigned char initialized;
};

static inline int rtl_fe_mc_equal(const unsigned int *a,const unsigned int *b,unsigned int count)
{
 unsigned int i;
 for(i=0;i<count;i++)if(a[i]!=b[i])return 0;
 return 1;
}

/* Do not overwrite an inherited factory/Linux group or silently adopt its
 * ownership. Nonzero actions also prevent warm reattachment from forgetting
 * the quarantine left by a prior native owner. No hardware is cleared here.
 */
static inline int rtl_fe_mc_owner_start(const struct rtl_dq_io *io,struct rtl_fe_mc_owner *owner)
{
 unsigned int words[3],i;
 int ret;

 if(owner->initialized)return 0;
 for(i=0;i<RTL_FE_MC_GROUPS;i++) {
  ret=rtl_fe_mc_group_read(io,i,words);
  if(ret)return ret;
  if(words[0] || words[1])return -EBUSY;
 }
 for(i=0;i<RTL_FE_MC_ACTIONS;i++) {
  ret=rtl_fe_mc_action_read(io,i,words);
  if(ret)return ret;
  if(words[0] || words[1] || words[2])return -EBUSY;
 }
 owner->initialized=1;
 return 0;
}

/* Stage one complete recipient set; duplicates cannot be silently collapsed
 * because two Linux mirror actions would send two packets. On failure *index
 * remains nonzero whenever the caller must retry withdrawal of owned state.
 */
static inline int rtl_fe_mc_stage(const struct rtl_dq_io *io,struct rtl_fe_mc_owner *owner,
 const struct rtl_fe_mc_replica *replicas,unsigned int count,unsigned int *index)
{
 unsigned int encoded[RTL_FE_MC_ACTIONS][3],actual[3],g,i,j,a,free;
 struct rtl_fe_mc_group_owner *group;
 struct rtl_fe_mc_action_owner *action;
 int ret;

 *index=0;
 if(!count || count>RTL_FE_MC_ACTIONS)return -EINVAL;
 for(i=0;i<count;i++) {
  ret=rtl_fe_mc_replica_encode(io,replicas+i,encoded[i]);
  if(ret)return ret;
  for(j=0;j<i;j++)if(rtl_fe_mc_equal(encoded[i],encoded[j],3))return -EEXIST;
 }
 ret=rtl_fe_mc_owner_start(io,owner);
 if(ret)return ret;
 for(g=1;g<RTL_FE_MC_GROUPS;g++)if(owner->groups[g].state==RTL_FE_MC_FREE)break;
 if(g==RTL_FE_MC_GROUPS)return -ENOSPC;
 ret=rtl_fe_mc_group_read(io,g,actual);
 if(ret)return ret;
 if(actual[0] || actual[1])return -EUCLEAN;
 group=&owner->groups[g];
 group->state=RTL_FE_MC_STAGED;*index=g;
 for(i=0;i<count;i++) {
  free=RTL_FE_MC_ACTIONS;
  for(a=0;a<RTL_FE_MC_ACTIONS;a++) {
   action=&owner->actions[a];
   if((action->users || action->exposed) && action->ready &&
      rtl_fe_mc_equal(action->words,encoded[i],3))break;
   if(!action->users && !action->exposed && free==RTL_FE_MC_ACTIONS)free=a;
  }
  if(a==RTL_FE_MC_ACTIONS)a=free;
  if(a==RTL_FE_MC_ACTIONS)return -ENOSPC;
  action=&owner->actions[a];
  ret=rtl_fe_mc_action_read(io,a,actual);
  if(ret)return ret;
  if(action->users || action->exposed) {
   if(!rtl_fe_mc_equal(actual,action->words,3))return -EUCLEAN;
  } else {
   if(actual[0] || actual[1] || actual[2])return -EUCLEAN;
   for(j=0;j<3;j++)action->words[j]=encoded[i][j];
   action->ready=0;
  }
  action->users++;
  group->held[a/32]|=1U<<(a%32);
  group->bitmap[a/32]|=1U<<(a%32);
  if(!action->ready) {
   ret=rtl_fe_mc_action_write(io,a,action->words);
   if(ret)return ret;
   action->ready=1;
  }
 }
 ret=rtl_fe_mc_group_write(io,g,group->bitmap);
 if(!ret)group->ready=1;
 return ret;
}

static inline int rtl_fe_mc_verify(const struct rtl_dq_io *io,const struct rtl_fe_mc_owner *owner,
 unsigned int index)
{
 const struct rtl_fe_mc_group_owner *group;
 unsigned int actual[3],i;
 int ret;

 if(!index || index>=RTL_FE_MC_GROUPS)return -EINVAL;
 group=&owner->groups[index];
 if(!group->ready || group->withdrawing ||
    (group->state!=RTL_FE_MC_STAGED && group->state!=RTL_FE_MC_REFERENCED))return -EINVAL;
 ret=rtl_fe_mc_group_read(io,index,actual);
 if(ret)return ret;
 if(!rtl_fe_mc_equal(actual,group->bitmap,2))return -EUCLEAN;
 for(i=0;i<RTL_FE_MC_ACTIONS;i++)if(group->bitmap[i/32]&(1U<<(i%32))) {
  if(!owner->actions[i].users || !owner->actions[i].ready)return -EUCLEAN;
  ret=rtl_fe_mc_action_read(io,i,actual);
  if(ret)return ret;
  if(!rtl_fe_mc_equal(actual,owner->actions[i].words,3))return -EUCLEAN;
 }
 return 0;
}

static inline int rtl_fe_mc_reference(const struct rtl_dq_io *io,struct rtl_fe_mc_owner *owner,
 unsigned int index)
{
 unsigned int i;
 int ret=rtl_fe_mc_verify(io,owner,index);
 if(ret)return ret;
 owner->groups[index].state=RTL_FE_MC_REFERENCED;
 for(i=0;i<RTL_FE_MC_ACTIONS;i++)
  if(owner->groups[index].bitmap[i/32]&(1U<<(i%32)))owner->actions[i].exposed=1;
 return 0;
}

/* Caller first invalidates Hash/classifier and cache references. Withdraw NI
 * before releasing ANY action; preserve all remaining ownership on failure.
 * Published indices stay quarantined. Immutable exposed actions can be shared
 * by a new group, but never changed into a different forwarding destination.
 */
static inline int rtl_fe_mc_withdraw(const struct rtl_dq_io *io,struct rtl_fe_mc_owner *owner,
 unsigned int index)
{
 const unsigned int empty[3]={0,0,0};
 struct rtl_fe_mc_group_owner *group;
 unsigned int i;
 int ret;

 if(!index || index>=RTL_FE_MC_GROUPS)return -EINVAL;
 group=&owner->groups[index];
 if(group->state==RTL_FE_MC_FREE || group->state==RTL_FE_MC_RETIRED)return 0;
 group->withdrawing=1;
 ret=rtl_fe_mc_group_clear(io,index);
 if(ret)return ret;
 group->ready=0;
 for(i=0;i<RTL_FE_MC_ACTIONS;i++)if(group->held[i/32]&(1U<<(i%32))) {
  struct rtl_fe_mc_action_owner *action=&owner->actions[i];
  if(!action->users)return -EUCLEAN;
  if(action->users==1 && !action->exposed) {
   action->ready=0;
   ret=rtl_fe_mc_action_write(io,i,empty);
   if(ret)return ret;
   for(unsigned int j=0;j<3;j++)action->words[j]=0;
  }
  action->users--;
  group->held[i/32]&=~(1U<<(i%32));
 }
 if(group->state==RTL_FE_MC_REFERENCED)group->state=RTL_FE_MC_RETIRED;
 else *group=(struct rtl_fe_mc_group_owner){0};
 return 0;
}
#endif
