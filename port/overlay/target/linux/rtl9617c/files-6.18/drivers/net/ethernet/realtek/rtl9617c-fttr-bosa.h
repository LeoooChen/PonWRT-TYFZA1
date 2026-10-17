/* SPDX-License-Identifier: GPL-2.0-only */
/* GN25L99 calibration file and register sequence from TYFZA1 rtkoltbosa. */
#ifndef RTL9617C_FTTR_BOSA_H
#define RTL9617C_FTTR_BOSA_H

#define RTL_FTTR_GN25L99_CAL_SIZE 661

static inline int rtl_fttr_gn25l99_validate(const unsigned char *cal,
					 unsigned int size, unsigned int slave)
{
	unsigned int i;
	unsigned char checksum = 0;

	if (size != RTL_FTTR_GN25L99_CAL_SIZE)
		return -EMSGSIZE;
	if (((unsigned int)cal[0] << 8 | cal[1]) != size ||
	    cal[3] != 10 || cal[4] != slave << 1)
		return -EINVAL;
	for (i = 0; i < size; i++)
		checksum ^= cal[i];
	return checksum ? -EBADMSG : 0;
}

static inline int rtl_fttr_gn25l99_calibrate(void *context,
		int (*write)(void *context, unsigned int reg, unsigned char value),
		const unsigned char *cal)
{
	unsigned int reg;
	int ret;

	for (reg = 0; reg < 128; reg++) {
		ret = write(context, reg, cal[0x1c + reg]);
		if (ret)
			return ret;
	}
	return 0;
}

#endif
