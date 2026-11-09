/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_CAM_OWNER_H
#define RTL_AX_CAM_OWNER_H

/* C and XB hw_info: 128 address CAM entries, 20 BSSID CAM entries. These
 * resources are independent of MACID. Serialized by wiphy (or pre-register
 * probe); reservations survive an uncertain firmware command. */
#define RTL_AX_ADDR_CAMS 128
#define RTL_AX_BSSID_CAMS 20
struct rtl_ax_cam_owners {
	struct { u8 address, bssid; bool reserved; } role[128];
	struct { u8 address[6]; unsigned int references; } bssid[RTL_AX_BSSID_CAMS];
};

static int rtl_ax_cam_prepare(struct rtl_ax_cam_owners *owners, u8 cam[60])
{
	unsigned int macid = cam[32] & 127, address, bssid, i;
	bool valid = cam[8] & 1;

	if (!owners->role[macid].reserved) {
		if (!valid) return -ENOENT;
		for (address = 0; address < RTL_AX_ADDR_CAMS; address++) {
			for (i = 0; i < 128; i++)
				if (owners->role[i].reserved && owners->role[i].address == address)
					break;
			if (i == 128) break;
		}
		if (address == RTL_AX_ADDR_CAMS) return -ENOSPC;
		for (bssid = 0; bssid < RTL_AX_BSSID_CAMS; bssid++)
			if (owners->bssid[bssid].references &&
			    !memcmp(owners->bssid[bssid].address, cam + 54, 6)) break;
		if (bssid == RTL_AX_BSSID_CAMS) {
			for (bssid = 0; bssid < RTL_AX_BSSID_CAMS; bssid++)
				if (!owners->bssid[bssid].references) break;
			if (bssid == RTL_AX_BSSID_CAMS) return -ENOSPC;
			memcpy(owners->bssid[bssid].address, cam + 54, 6);
		}
		owners->role[macid].address = address;
		owners->role[macid].bssid = bssid;
		owners->role[macid].reserved = true;
		owners->bssid[bssid].references++;
	} else {
		address = owners->role[macid].address;
		bssid = owners->role[macid].bssid;
		if (valid && memcmp(owners->bssid[bssid].address, cam + 54, 6)) {
			/* A single STA can change its association in its own BSSID slot.
			 * Changing a shared identity requires first retiring its peers. */
			if (owners->bssid[bssid].references != 1) return -EBUSY;
			memcpy(owners->bssid[bssid].address, cam + 54, 6);
		}
	}
	/* Even the first create can time out before a confirmed owner snapshot
	 * exists. Erasure still needs the hardware table lengths. */
	cam[4] = address;
	cam[5] = 0;
	cam[6] = 32;
	cam[49] = 0;
	cam[50] = 8;
	cam[12] = (cam[12] & ~63U) | bssid;
	cam[48] = bssid;
	/* A deletion must address the owned slot even after other slots became
	 * free. Keep the BSSID valid until its last address owner retires. */
	cam[52] = (cam[52] & ~1U) | (valid || owners->bssid[bssid].references > 1);
	memcpy(cam + 54, owners->bssid[bssid].address, 6);
	return 0;
}

static void rtl_ax_cam_complete(struct rtl_ax_cam_owners *owners, const u8 cam[60])
{
	unsigned int macid = cam[32] & 127;

	if (!(cam[8] & 1) && owners->role[macid].reserved) {
		unsigned int bssid = owners->role[macid].bssid;

		owners->role[macid].reserved = false;
		if (!--owners->bssid[bssid].references)
			memset(owners->bssid[bssid].address, 0, 6);
	}
}
#endif
