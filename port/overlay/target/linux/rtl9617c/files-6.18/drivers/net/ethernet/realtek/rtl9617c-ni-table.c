// SPDX-License-Identifier: GPL-2.0-only
#include <linux/errno.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/rtl9617c-ni-table.h>

static DEFINE_MUTEX(ni_table_lock);

int rtl9617c_ni_table(void *context,
	unsigned int (*read)(void *, unsigned int),
	void (*write)(void *, unsigned int, unsigned int),
	int (*indirect)(void *, unsigned int, unsigned int),
	unsigned int access, unsigned int index, unsigned int *result,
	const unsigned int *value, unsigned int count)
{
	unsigned int status, data, actual[2], i;
	int ret;

	if ((!result && !value) || !count || count > 2 ||
	    (access != 0x1654 && access != 0x66a4) ||
	    (access == 0x1654 && (count != 1 || index >= 512)) ||
	    (access == 0x66a4 && index >= 64)) return -EINVAL;
	data = access == 0x1654 ? 0x1658 : 0x66ac;
	mutex_lock(&ni_table_lock);
	/* A preceding timeout retains the hardware command even after its
	 * caller drops the mutex. Never overwrite its staging data or index. */
	ret = read_poll_timeout(read, status, !(status & BIT(31)), 1, 100000,
		false, context, access);
	if (ret) goto out;
	if (value) {
		for (i = 0; i < count; i++) write(context, data - i * 4, value[i]);
		ret = indirect(context, access, BIT(31) | BIT(30) | index);
		if (ret) goto out;
	}
	ret = indirect(context, access, BIT(31) | index);
	if (ret) goto out;
	for (i = 0; i < count; i++) actual[i] = read(context, data - i * 4);
	if (value)
		for (i = 0; i < count; i++)
			if (actual[i] != value[i]) { ret = -EIO; goto out; }
	if (result)
		for (i = 0; i < count; i++) result[i] = actual[i];
out:
	mutex_unlock(&ni_table_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_table);
