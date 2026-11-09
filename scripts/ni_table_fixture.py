"""Compile the production NI transaction with host mutex/poll boundaries."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = (root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-ni-table.c').read_text()
source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
code = r'''
#include <pthread.h>
#include <errno.h>
#include <stdbool.h>
#ifndef BIT
#define BIT(n) (1U<<(n))
#endif
static pthread_mutex_t ni_fixture_lock=PTHREAD_MUTEX_INITIALIZER;
#define DEFINE_MUTEX(name) int name __attribute__((unused))
#define mutex_lock(p) ((void)(p),pthread_mutex_lock(&ni_fixture_lock))
#define mutex_unlock(p) ((void)(p),pthread_mutex_unlock(&ni_fixture_lock))
#define EXPORT_SYMBOL_GPL(name)
#define read_poll_timeout(op,v,c,d,t,s,...) ({(void)(d);(void)(t);(void)(s);int err=-ETIMEDOUT;for(unsigned poll=0;poll<16;poll++){v=op(__VA_ARGS__);if(c){err=0;break;}}err;})
''' + source + r'''
#undef DEFINE_MUTEX
#undef mutex_lock
#undef mutex_unlock
#undef EXPORT_SYMBOL_GPL
#undef read_poll_timeout
'''
