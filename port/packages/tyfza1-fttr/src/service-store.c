// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include "service-store.h"

#define RECORD_LIMIT (32U * 1024 * 1024)
struct record_io { FILE *file; bool read; int error; size_t size; };
static void bytes(struct record_io *io, void *p, size_t n)
{
	if (io->error || !n) return;
	if (n > RECORD_LIMIT - 4 - io->size) { io->error = -E2BIG; return; }
	io->size += n;
	if ((io->read ? fread(p, 1, n, io->file) : fwrite(p, 1, n, io->file)) != n)
		io->error = -EIO;
}
static unsigned int number(struct record_io *io, unsigned int v, unsigned int max)
{
	uint8_t b[4] = { v >> 24, v >> 16, v >> 8, v };
	bytes(io, b, 4);
	v = (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 | (uint32_t)b[2] << 8 | b[3];
	if (v > max) { io->error = -EBADMSG; return 0; }
	return v;
}
#define FIELD(io, p, f, max) ((p)->f = number(io, (p)->f, max))
static void values(struct record_io *io, struct fttr_omci_values *v)
{
	FIELD(io, v, mask, 65535); FIELD(io, v, size, sizeof(v->value));
	if (!io->error) bytes(io, v->value, v->size);
}
static void objects(struct record_io *io, struct fttr_objects_job *j,
	struct fttr_omci_object *o, unsigned int max, unsigned int onu, const uint8_t serial[8])
{
	FIELD(io, j, count, max);
	j->objects = o; j->me.onu = onu; memcpy(j->serial, serial, 8);
	j->phase = FTTR_OBJECT_CONFIGURED;
	for (int i = 0; !io->error && i < j->count; i++) {
		FIELD(io, &o[i], class_id, 65535); FIELD(io, &o[i], instance, 65535);
		values(io, &o[i].create); values(io, &o[i].set);
		FIELD(io, &o[i], dependencies, (1U << i) - 1);
		FIELD(io, &o[i], update_only, 1); FIELD(io, &o[i], touched, 1);
		FIELD(io, &o[i], have_previous, 1);
		bytes(io, o[i].previous, sizeof(o[i].previous));
		o[i].presence = FTTR_OBJECT_VERIFIED;
		if (!o[i].create.mask || !o[i].create.size ||
		    (o[i].touched && o[i].update_only && !o[i].have_previous)) io->error = -EBADMSG;
	}
}
static void table(struct record_io *io, struct fttr_vlan_table *t)
{
	FIELD(io, t, capacity, 65535); FIELD(io, t, length, t->capacity * 16U);
	if (t->length % 16) io->error = -EBADMSG;
	if (io->error) return;
	if (io->read && t->length) {
		t->data = malloc(t->length);
		if (!t->data) { io->error = -ENOMEM; return; }
	}
	bytes(io, t->data, t->length);
	t->received = t->length; t->complete = true;
}
static void recipe(struct record_io *io, struct fttr_services *s, unsigned int onu,
	const uint8_t serial[8], enum fttr_backend backend)
{
	FIELD(io, s, count, FTTR_SERVICES);
	if (!s->count) io->error = -EBADMSG;
	for (unsigned int n = 0; !io->error && n < s->count; n++) {
		struct fttr_service_job *j = &s->job[n];
		struct fttr_service_config *c = &j->config;
		FIELD(io,c,uni,65535); FIELD(io,c,slot,7); FIELD(io,c,priority,7);
		FIELD(io,c,vid,4094); FIELD(io,c,fixed,65535); FIELD(io,c,assured,65535); FIELD(io,c,maximum,65535);
		FIELD(io,c,veip,1); FIELD(io,c,tagged,1); FIELD(io,c,shared,1);
		FIELD(io,c,filter_only,1); FIELD(io,c,factory,1); FIELD(io,c,multicast,2);
		c->backend = backend;
		FIELD(io,j,tcont,65535); FIELD(io,j,queue,65535); FIELD(io,j,gem,4094);
		FIELD(io,j,uni_port,2048); FIELD(io,j,ani_port,2048); FIELD(io,j,previous_alloc,65535);
		FIELD(io,j,mcast_port,2048); FIELD(io,j,mcast_uni,253);
		FIELD(io,&j->previous,fixed,65535); FIELD(io,&j->previous,limits,UINT32_MAX);
		FIELD(io,j,grant_sent,1); FIELD(io,j,require_shared,1);
		objects(io, &j->transaction, j->objects, 7, onu, serial);
		FIELD(io,j,uni_count,254U - !!c->multicast);
		if (!c->slot || !j->uni_count || j->transaction.count != 7 ||
		    j->gem != fttr_miniolt_gem_id(onu,c->slot) || j->mcast_uni >= j->uni_count) io->error = -EBADMSG;
		if (io->read && !io->error) {
			j->unis = calloc(j->uni_count, sizeof(*j->unis));
			if (!j->unis) { io->error = -ENOMEM; return; }
		}
		for (unsigned int u = 0; !io->error && u < j->uni_count; u++) {
			struct fttr_service_uni *p = &j->unis[u];
			struct fttr_vlan_change *v = &p->vlan;
			FIELD(io,p,instance,65535); FIELD(io,p,port,2048); FIELD(io,p,number,255); FIELD(io,p,veip,1);
			objects(io, &p->transaction, p->objects, 4, onu, serial);
			FIELD(io,&v->plan,count,3); bytes(io,v->plan.rows,sizeof(v->plan.rows));
			FIELD(io,v,sent,(1U << v->plan.count) - 1);
			table(io,&v->before.table); table(io,&v->after.table);
			v->onu = onu; v->instance = p->port; v->stage = FTTR_VLAN_DONE;
			v->verified = true; v->phase = "recovered";
			if (!p->port || !p->number || !p->transaction.count || !v->plan.count) io->error = -EBADMSG;
		}
		if (c->multicast) {
			struct fttr_mcast_job *m = &j->mcast;
			objects(io,&m->transaction,m->objects,7,onu,serial);
			FIELD(io,m,prior_empty,1); FIELD(io,m,table_sent,1); FIELD(io,m,doubled,1);
			FIELD(io,m,table_length,24); bytes(io,m->table_row,12);
			if (m->transaction.count != 7 || m->table_length != (m->doubled ? 24U : 12U)) io->error = -EBADMSG;
			m->stage = FTTR_MCAST_READY; m->configured = true;
		}
		j->stage = FTTR_SERVICE_READY; j->phase = "recovered";
		/* Local grants were withdrawn during controller startup. */
		j->configured = false; j->grant_verified = false; j->grant_withdrawn = true;
	}
	s->stage = FTTR_SERVICES_READY; s->configured = false;
}
#undef FIELD

static void filename(const uint8_t serial[8], char name[32])
{
	snprintf(name,32,"%02x%02x%02x%02x%02x%02x%02x%02x.service",serial[0],serial[1],serial[2],serial[3],serial[4],serial[5],serial[6],serial[7]);
}
int fttr_service_store_open(const char *path)
{
	struct stat st;
	int fd = open(path,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC), ret;
	if (fd < 0) return -errno;
	if (fstat(fd,&st)) { ret = -errno; goto fail; }
	if (st.st_uid != geteuid() || (st.st_mode & 077)) { ret = -EACCES; goto fail; }
	if (flock(fd,LOCK_EX|LOCK_NB) || fsync(fd)) { ret = -errno; goto fail; }
	return fd;
fail:
	close(fd); return ret;
}
static int save(int directory, const struct fttr_omci_link *link, unsigned int onu,
	const uint8_t serial[8], enum fttr_backend backend, const struct fttr_services *set, bool pending)
{
	char name[32], temporary[40], *data = NULL;
	size_t size = 0;
	uint8_t header[32] = { 'F','T','S','1' }, crc[4];
	struct record_io io = {0};
	int fd = -1, ret = 0;
	memcpy(header+4,serial,8); memcpy(header+12,link->host_mac,6); memcpy(header+18,link->olt_mac,6);
	header[24] = backend; header[25] = onu; header[26] = set ? pending ? 2 : 1 : 0;
	header[27] = link->downstream_length; header[28] = link->ethertype >> 8; header[29] = link->ethertype;
	io.file = open_memstream(&data,&size);
	if (!io.file) return -errno;
	bytes(&io,header,sizeof(header));
	/* The encoder uses one field walk for both directions, on a private copy
	 * so that saving never changes live ownership or completion state. */
	if (set) {
		struct fttr_services copy = *set;
		for (unsigned int n=0;n<copy.count;n++) {
			copy.job[n].unis = malloc(set->job[n].uni_count * sizeof(*copy.job[n].unis));
			if (!copy.job[n].unis) { io.error = -ENOMEM; copy.count=n; break; }
			memcpy(copy.job[n].unis,set->job[n].unis,set->job[n].uni_count * sizeof(*copy.job[n].unis));
		}
		if (!io.error) recipe(&io,&copy,onu,serial,backend);
		for (unsigned int n=0;n<copy.count;n++) free(copy.job[n].unis);
	}
	if (fclose(io.file) && !io.error) io.error = -errno;
	if (io.error) { ret = io.error; goto out; }
	if (size > RECORD_LIMIT - 4) { ret = -E2BIG; goto out; }
	uint32_t sum = fttr_omci_crc((uint8_t *)data,size);
	crc[0]=sum>>24;crc[1]=sum>>16;crc[2]=sum>>8;crc[3]=sum;
	filename(serial,name); snprintf(temporary,sizeof(temporary),"%s.new",name);
	if (unlinkat(directory,temporary,0) && errno != ENOENT) { ret=-errno;goto out; }
	fd=openat(directory,temporary,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
	if (fd<0) { ret=-errno;goto out; }
	if (write(fd,data,size)!=(ssize_t)size || write(fd,crc,4)!=4) ret=-EIO;
	if (!ret && (fsync(fd) || renameat(directory,temporary,directory,name) || fsync(directory))) ret=-errno;
	if (ret) unlinkat(directory,temporary,0);
out:
	if (fd>=0) close(fd);
	free(data); return ret;
}
static bool ready(const struct fttr_services *s)
{
	if (!s->count || s->count > FTTR_SERVICES || s->stage != FTTR_SERVICES_READY || s->error || s->cleanup_error) return false;
	for (unsigned int n = 0; n < s->count; n++) {
		const struct fttr_service_job *j = &s->job[n];
		if (j->stage != FTTR_SERVICE_READY || j->error || j->cleanup_error || !j->unis ||
		    !j->uni_count || j->uni_count > 254U - !!j->config.multicast ||
		    j->transaction.phase != FTTR_OBJECT_CONFIGURED) return false;
		for (unsigned int u = 0; u < j->uni_count; u++) {
			const struct fttr_vlan_change *v = &j->unis[u].vlan;
			if (j->unis[u].transaction.phase != FTTR_OBJECT_CONFIGURED || v->stage != FTTR_VLAN_DONE ||
			    !v->verified || v->error || v->cleanup_error || !v->before.table.complete ||
			    !v->after.table.complete || v->before.table.received != v->before.table.length ||
			    v->after.table.received != v->after.table.length ||
			    (v->before.table.length && !v->before.table.data) || (v->after.table.length && !v->after.table.data)) return false;
		}
		if (j->config.multicast && (j->mcast.stage != FTTR_MCAST_READY || !j->mcast.configured ||
		    j->mcast.error || j->mcast.cleanup_error)) return false;
	}
	return true;
}
int fttr_service_store_save(int d,const struct fttr_omci_link *l,unsigned int onu,const uint8_t sn[8],enum fttr_backend b,const struct fttr_services *s)
{
	if (!ready(s)) return -EINVAL;
	return save(d,l,onu,sn,b,s,false);
}
int fttr_service_store_pending(int d,const struct fttr_omci_link *l,unsigned int onu,const uint8_t sn[8],enum fttr_backend b,const struct fttr_services *committed)
{
	if (committed && !ready(committed)) return -EINVAL;
	return save(d,l,onu,sn,b,committed,true);
}
int fttr_service_store_load(int directory,const struct fttr_omci_link *link,unsigned int onu,
	const uint8_t serial[8],enum fttr_backend backend,struct fttr_services *set)
{
	char name[32]; uint8_t *data=NULL;
	struct stat st; struct record_io io={.read=true}; int fd,ret=0;
	memset(set,0,sizeof(*set)); filename(serial,name);
	fd=openat(directory,name,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
	if(fd<0)return -errno;
	if(fstat(fd,&st)){ret=-errno;goto out;}
	if(!S_ISREG(st.st_mode)||st.st_uid!=geteuid()||(st.st_mode&077)){ret=-EACCES;goto out;}
	if(st.st_size<36||st.st_size>RECORD_LIMIT){ret=-EBADMSG;goto out;}
	data=malloc(st.st_size); if(!data){ret=-ENOMEM;goto out;}
	if(read(fd,data,st.st_size)!=st.st_size){ret=-EIO;goto out;}
	uint8_t *end=data+st.st_size-4;
	uint32_t sum=(uint32_t)end[0]<<24|(uint32_t)end[1]<<16|(uint32_t)end[2]<<8|end[3];
	if(memcmp(data,"FTS1",4)||sum!=fttr_omci_crc(data,st.st_size-4)||data[26]>2||data[30]||data[31]){ret=-EBADMSG;goto out;}
	if(memcmp(data+4,serial,8)||memcmp(data+12,link->host_mac,6)||memcmp(data+18,link->olt_mac,6)||
	   data[24]!=backend||data[25]!=onu||data[27]!=link->downstream_length||
	   data[28]!=(link->ethertype>>8)||data[29]!=(link->ethertype&255)){ret=-EXDEV;goto out;}
	if(data[26]!=1){ret=-ENOTRECOVERABLE;goto out;}
	io.file=fmemopen(data+32,st.st_size-36,"rb");if(!io.file){ret=-errno;goto out;}
	recipe(&io,set,onu,serial,backend);
	ret=io.error;
	if(!ret && ftell(io.file)!=st.st_size-36)ret=-EBADMSG;
	fclose(io.file);
out:
	close(fd);free(data);
	if(ret) {
		for(unsigned int n=0;n<FTTR_SERVICES;n++) {
			if(!set->job[n].unis) set->job[n].uni_count=0;
			fttr_service_clear(&set->job[n]);
		}
		memset(set,0,sizeof(*set));
	}
	return ret;
}
int fttr_service_store_remove(int directory,const uint8_t serial[8])
{
	char name[32];filename(serial,name);
	if(unlinkat(directory,name,0)&&errno!=ENOENT)return -errno;
	return fsync(directory)?-errno:0;
}

struct fttr_me_job *fttr_service_recovery_pending(struct fttr_service_recovery *r)
{
	return r->stage==FTTR_REC_VLAN?&r->vlan.me:&r->me;
}
void fttr_service_recovery_clear(struct fttr_service_recovery *r)
{
	free(r->vlan.table.data);memset(r,0,sizeof(*r));
}
int fttr_service_recovery_update(struct fttr_service_recovery *r,struct fttr_services *s,uint16_t *counter)
{
	for (;;) {
		if (r->stage==FTTR_REC_DONE) return 2;
		if (r->stage == FTTR_REC_SYNC_BEFORE || r->stage == FTTR_REC_SYNC_AFTER) {
			if (!r->started) {
				fttr_me_job_start(&r->me, s->job[0].transaction.me.onu, counter, FTTR_ME_GET, 2, 0, 0x8000, NULL, 0);
				r->started = true; return 1;
			}
			if (r->me.state == FTTR_ME_WAIT) return 0;
			if (r->me.error) return r->me.error;
			if (r->stage == FTTR_REC_SYNC_AFTER) {
				if (r->sync != r->me.reply[11]) return -ESTALE;
				r->stage = FTTR_REC_DONE; return 2;
			}
			r->sync = r->me.reply[11]; r->started = false; r->stage = FTTR_REC_OBJECTS;
		}
		struct fttr_service_job *j=&s->job[r->service];
		unsigned int onu=j->transaction.me.onu;
		if(r->stage==FTTR_REC_VLAN){
			const struct fttr_vlan_table *wanted=&j->unis[r->uni].vlan.after.table;
			if(!r->started){fttr_vlan_read_start(&r->vlan,onu,counter,j->unis[r->uni].port);r->started=true;return 1;}
			if(r->vlan.me.state==FTTR_ME_WAIT)return 0;
			int ret=fttr_vlan_read_update(&r->vlan,counter);
			if(ret)return ret;
			if(r->vlan.table.capacity!=wanted->capacity||r->vlan.table.length!=wanted->length||
			   (wanted->length&&memcmp(r->vlan.table.data,wanted->data,wanted->length)))return -EUCLEAN;
			free(r->vlan.table.data);memset(&r->vlan,0,sizeof(r->vlan));r->started=false;
			if(++r->uni<j->uni_count)continue;
			r->uni=0;r->stage=FTTR_REC_MCAST;
		}
		struct fttr_objects_job *o=r->stage==FTTR_REC_OBJECTS?&j->transaction:
			r->stage==FTTR_REC_UNIS?&j->unis[r->uni].transaction:
			r->stage==FTTR_REC_MCAST&&j->config.multicast?&j->mcast.transaction:NULL;
		if(o){
			const struct fttr_omci_object *obj=&o->objects[r->object];
			const struct fttr_omci_values *v=r->group?&obj->set:&obj->create;
			if(!r->started){
				fttr_me_job_start(&r->me,onu,counter,FTTR_ME_GET,obj->class_id,obj->instance,v->mask,NULL,0);
				r->started=true;return 1;
			}
			if(r->me.state==FTTR_ME_WAIT)return 0;
			if(r->me.error)return r->me.error;
			if(memcmp(r->me.reply+11,v->value,v->size))return -EUCLEAN;
			r->started=false;
			if(!r->group&&obj->set.mask){r->group=1;continue;}
			r->group=0;
			if(++r->object<(unsigned int)o->count)continue;
			r->object=0;
			if(r->stage==FTTR_REC_OBJECTS){r->stage=FTTR_REC_UNIS;continue;}
			if(r->stage==FTTR_REC_UNIS){
				if(++r->uni<j->uni_count)continue;
				r->uni=0;r->stage=FTTR_REC_VLAN;continue;
			}
			r->stage=FTTR_REC_TABLE;
		}
		if(j->config.multicast){
			if(!r->started){
				uint8_t seq[2]={0};bool row=r->stage==FTTR_REC_ROW;
				fttr_me_job_start(&r->me,onu,counter,row?FTTR_ME_GET_NEXT:FTTR_ME_GET,281,4095,0x0080,row?seq:NULL,row?2:0);
				r->started=true;return 1;
			}
			if(r->me.state==FTTR_ME_WAIT)return 0;
			if(r->me.error)return r->me.error;
			if(r->stage==FTTR_REC_TABLE){
				const uint8_t *p=r->me.reply+11;
				if(((uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3])!=j->mcast.table_length)return -EUCLEAN;
				r->started=false;r->stage=FTTR_REC_ROW;continue;
			}
			if(memcmp(r->me.reply+11,j->mcast.table_row,12))return -EUCLEAN;
		}
		r->started=false;
		r->stage=++r->service<s->count?FTTR_REC_OBJECTS:FTTR_REC_SYNC_AFTER;
	}
}

static bool same_objects(const struct fttr_objects_job *a, const struct fttr_objects_job *b)
{
	if (a->count != b->count) return false;
	for (int i = 0; i < a->count; i++) {
		const struct fttr_omci_object *x = &a->objects[i], *y = &b->objects[i];
		if (x->class_id != y->class_id || x->instance != y->instance ||
		    x->dependencies != y->dependencies || x->update_only != y->update_only ||
		    x->create.mask != y->create.mask || x->create.size != y->create.size ||
		    memcmp(x->create.value, y->create.value, x->create.size) ||
		    x->set.mask != y->set.mask || x->set.size != y->set.size ||
		    memcmp(x->set.value, y->set.value, x->set.size)) return false;
	}
	return true;
}
int fttr_service_store_match(const struct fttr_services *saved, const struct fttr_services *planned)
{
	if (saved->count != planned->count) return -ESTALE;
	for (unsigned int n = 0; n < saved->count; n++) {
		const struct fttr_service_job *a = &saved->job[n], *b = &planned->job[n];
		if (a->uni_count != b->uni_count || a->tcont != b->tcont || a->queue != b->queue ||
		    a->mcast_uni != b->mcast_uni || a->mcast_port != b->mcast_port ||
		    !same_objects(&a->transaction, &b->transaction)) return -ESTALE;
		for (unsigned int u = 0; u < a->uni_count; u++)
			if (a->unis[u].instance != b->unis[u].instance || a->unis[u].veip != b->unis[u].veip ||
			    a->unis[u].port != b->unis[u].port || a->unis[u].number != b->unis[u].number ||
			    !same_objects(&a->unis[u].transaction, &b->unis[u].transaction)) return -ESTALE;
		if (a->config.multicast && !same_objects(&a->mcast.transaction, &b->mcast.transaction)) return -ESTALE;
	}
	return 0;
}

static bool reset_objects(const struct fttr_objects_job *objects, const struct fttr_mib_model *model)
{
	for (int n = 0; n < objects->count; n++) {
		const struct fttr_omci_object *o = &objects->objects[n];
		const struct fttr_mib_entity *e;
		for (e = model->entities; e; e = e->next)
			if (e->class_id == o->class_id && e->instance == o->instance) break;
		if (!o->update_only) {
			/* Any surviving service ME makes this a partial change, not a
			 * proven reset. Do not erase the previous ownership recipe. */
			if (e) return false;
			continue;
		}
		if (!e || !o->have_previous) return false;
		unsigned int offset = 0;
		for (unsigned int bit = 0x8000; bit; bit >>= 1) {
			if (!(o->create.mask & bit)) continue;
			size_t size;
			const uint8_t *v = fttr_mib_entity_attr(e, bit, &size);
			if (!v || size > o->create.size - offset || memcmp(v, o->previous + offset, size)) return false;
			offset += size;
		}
		if (offset != o->create.size) return false;
	}
	return true;
}
bool fttr_service_store_reset(const struct fttr_services *saved, const struct fttr_mib_model *model)
{
	if (!saved->count) return false;
	for (unsigned int n = 0; n < saved->count; n++) {
		const struct fttr_service_job *j = &saved->job[n];
		if (!reset_objects(&j->transaction, model)) return false;
		for (unsigned int u = 0; u < j->uni_count; u++) {
			/* Later services borrowed UNI state from the first member. */
			if (n && !j->unis[u].objects[j->unis[u].transaction.count - 1].touched) continue;
			if (!reset_objects(&j->unis[u].transaction, model)) return false;
		}
		if (j->config.multicast && !reset_objects(&j->mcast.transaction, model)) return false;
	}
	return true;
}
