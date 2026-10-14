// SPDX-License-Identifier: GPL-2.0-or-later
/* Raw OMCI transport through the board's NI/PUC management container. */
#include <linux/if_arp.h>
#include <linux/netdevice.h>
#include <linux/unaligned.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/random.h>
#include <crypto/aes.h>
#include <crypto/hash.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-pon-omci-format.h"
#include "rtl9617c-xgpon-hw.h"

static unsigned int key_read(void *context, unsigned int address)
{
	return readl(((struct rtl9617c_pon *)context)->mac + address);
}

static void key_write(void *context, unsigned int address, unsigned int value)
{
	writel(value, ((struct rtl9617c_pon *)context)->mac + address);
}

static int rtl_omci_cmac(void *context, const u8 *key, const u8 *data, unsigned int bytes, u8 *result)
{
	struct crypto_shash *tfm = context;
	SHASH_DESC_ON_STACK(desc, tfm);
	int ret = crypto_shash_setkey(tfm, key, 16);

	desc->tfm = tfm;
	if (!ret) ret = crypto_shash_digest(desc, data, bytes, result);
	shash_desc_zero(desc);
	return ret;
}

int rtl_pon_xgpon_ack_update(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, key_read, key_write, NULL};

	lockdep_assert_held(&pon->lock);
	return rtl_xgpon_ack_no_message(&io, pon->omci_kdf, rtl_omci_cmac,
		pon->xgpon_ploam_ik, readl(pon->mac + 0x412c) & 0xffff);
}

int rtl_pon_xgpon_omci_key(struct rtl9617c_pon *pon, const u8 tag[8])
{
	const struct rtl_dq_io io = {pon, key_read, key_write, NULL};
	static const u8 omci_label[16] = "OMCIIntegrityKey";
	static const u8 kek_label[16] = "KeyEncryptionKey";
	/* Exact16-byte label used by this board's ca_generate_ploam_ik. */
	static const u8 ploam_label[16] = "PLOAMIntegrtyKey";
	u8 key[16], session[16], integrity[16];
	int ret;

	lockdep_assert_held(&pon->lock);
	if (pon->xgpon_key_ready && !memcmp(pon->xgpon_tag, tag, 8)) return 0;
	spin_lock_bh(&pon->omci_key_lock);
	pon->xgpon_key_ready = false;
	spin_unlock_bh(&pon->omci_key_lock);
	ret = rtl_omci_session_key(pon->omci_kdf, rtl_omci_cmac, pon->xgpon_serial,
		pon->xgpon_registration, tag, pon->omci_master_key_valid ? pon->omci_master_key : NULL, session);
	if (!ret) ret = rtl_omci_cmac(pon->omci_kdf, session, omci_label, 16, key);
	if (!ret) ret = rtl_omci_cmac(pon->omci_kdf, session, kek_label, 16, pon->xgpon_kek);
	if (!ret) ret = rtl_omci_cmac(pon->omci_kdf, session, ploam_label, 16, integrity);
	if (!ret && pon->omci_master_key_valid) {
		rtl_pon_update(pon->mac + 0x4e00, BIT(6), 0);
		rtl_xgpon_key_words(&io, 0x4e14, pon->omci_master_key);
		rtl_xgpon_key_words(&io, 0x4e24, session);
		rtl_pon_update(pon->mac + 0x4e04, BIT(2), BIT(2));
		rtl_xgpon_key_words(&io, 0x4e34, integrity);
		rtl_pon_update(pon->mac + 0x4e04, BIT(3), BIT(3));
	}
	if (!ret) {
		memcpy(pon->xgpon_ploam_ik, integrity, 16);
		if (pon->xgpon_omcc_ready)
			ret = rtl_pon_xgpon_ack_update(pon);
	}
	if (!ret) {
		spin_lock_bh(&pon->omci_key_lock);
		ret = crypto_shash_setkey(pon->omci_cmac, key, 16);
		pon->xgpon_key_ready = !ret;
		if (!ret) memcpy(pon->xgpon_tag, tag, 8);
		spin_unlock_bh(&pon->omci_key_lock);
	}
	memzero_explicit(key, sizeof(key));
	memzero_explicit(session, sizeof(session));
	memzero_explicit(integrity, sizeof(integrity));
	return ret;
}

ssize_t rtl_pon_omci_master_key(struct rtl9617c_pon *pon, const char *buf, size_t count)
{
	u8 master[16], tag[8];
	int ret;

	if (count != 32) return -EMSGSIZE;
	ret = hex2bin(master, buf, 16);
	if (ret) return ret;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	memcpy(pon->omci_master_key, master, 16);
	pon->omci_master_key_valid = true;
	ret = 0;
	if (pon->mode == RTL_PON_XGPON || pon->mode == RTL_PON_XGSPON) {
		memcpy(tag, pon->xgpon_tag, 8);
		spin_lock_bh(&pon->omci_key_lock);
		pon->xgpon_key_ready = false;
		spin_unlock_bh(&pon->omci_key_lock);
		ret = rtl_pon_xgpon_omci_key(pon, tag);
	}
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	memzero_explicit(master, sizeof(master));
	return ret ? ret : count;
}

void rtl_pon_xgpon_key_irq(struct rtl9617c_pon *pon, u32 interrupts, u32 control)
{
	const struct rtl_dq_io io = {pon, key_read, key_write, NULL};
	static const u8 name_suffix[16] = "3141592653589793"; /* Original ca_generate_kek_name constant. */
	u8 message[40] = {}, key[16], name[32];
	struct crypto_aes_ctx aes;
	u32 status;
	unsigned int flag = (control >> 8) & 255, index = (control >> 16) & 255;
	int ret = 0;

	lockdep_assert_held(&pon->lock);
	if (interrupts & BIT(7)) {
		if (!pon->xgpon_key_ready || index < 1 || index > 2 || flag > 1) { ret = -EPROTO; goto error; }
		index--;
		if (!flag) {
			get_random_bytes(key, 16);
			ret = aes_expandkey(&aes, pon->xgpon_kek, 16);
			if (ret) goto error;
			aes_encrypt(&aes, pon->xgpon_encrypted_key[index], key);
			memcpy(name, key, 16); memcpy(name + 16, name_suffix, 16);
			ret = rtl_omci_cmac(pon->omci_kdf, pon->xgpon_kek, name, 32, pon->xgpon_key_name[index]);
			if (ret) goto error;
			rtl_xgpon_data_key(&io, index, key);
			rtl_pon_update(pon->mac + 0x4124, BIT(index), BIT(index));
			pon->xgpon_key_valid |= BIT(index);
			if (pon->xgpon_key_state != 4 || pon->xgpon_active_key != index)
				pon->xgpon_key_state = 2;
		} else {
			if (!(pon->xgpon_key_valid & BIT(index))) { ret = -ENOKEY; goto error; }
			rtl_xgpon_key_confirm(&io, index);
			pon->xgpon_active_key = index;
			if (pon->xgpon_key_state == 2) pon->xgpon_key_state = 4;
		}
		rtl_xgpon_key_report(message, pon->xgpon_omcc_id, control,
			flag ? pon->xgpon_key_name[index] : pon->xgpon_encrypted_key[index]);
		ret = readl_poll_timeout(pon->mac + 0x4afc, status, !(status & BIT(31)), 1, 10000);
		if (!ret) {
			rtl_xgpon_ploam_transmit(&io, message, false);
			pon->xgpon_key_reports++; pon->ploam_tx++;
		}
	}
error:
	if (interrupts & BIT(5)) {
		pon->xgpon_key_tk4++;
		if (pon->xgpon_key_state == 2) pon->xgpon_key_state = 0;
	}
	if (interrupts & BIT(6)) pon->xgpon_key_tk5++;
	if (ret) pon->xgpon_activation_error = ret;
	memzero_explicit(key, sizeof(key));
	memzero_explicit(name, sizeof(name));
	memzero_explicit(&aes, sizeof(aes));
}

ssize_t rtl_pon_omci_broadcast_key(struct rtl9617c_pon *pon, const char *buf, size_t count)
{
	const struct rtl_dq_io io = {pon, key_read, key_write, NULL};
	const u8 *row = (const u8 *)buf;
	struct crypto_aes_ctx aes;
	u8 key[16] = {};
	unsigned int operation, slot;
	int ret = 0;

	if (count != 18) return -EMSGSIZE;
	operation = row[0]; slot = row[1] >> 6;
	if (operation > 2 || (operation != 2 && ((row[1] & 63) || slot < 1 || slot > 2))) return -EINVAL;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_XGPON && pon->mode != RTL_PON_XGSPON) { ret = -EOPNOTSUPP; goto out; }
	if (!operation) {
		if (!pon->xgpon_key_ready) { ret = -ENOKEY; goto out; }
		ret = aes_expandkey(&aes, pon->xgpon_kek, 16);
		if (ret) goto out;
		aes_decrypt(&aes, key, row + 2);
		rtl_xgpon_multicast_key(&io, slot - 1, key, true);
	} else if (operation == 1) {
		rtl_xgpon_multicast_key(&io, slot - 1, key, false);
	} else {
		rtl_xgpon_multicast_key(&io, 0, key, false);
		rtl_xgpon_multicast_key(&io, 1, key, false);
	}
out:
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	memzero_explicit(key, sizeof(key));
	memzero_explicit(&aes, sizeof(aes));
	return ret ? ret : count;
}

static int rtl_omci_mic(struct rtl9617c_pon *pon, u8 direction, const u8 *data, unsigned int bytes, u8 mic[4])
{
	SHASH_DESC_ON_STACK(desc, pon->omci_cmac);
	u8 result[16];
	int ret = -ENOKEY;

	desc->tfm = pon->omci_cmac;
	spin_lock_bh(&pon->omci_key_lock);
	if (pon->xgpon_key_ready) {
		ret = crypto_shash_init(desc);
		if (!ret) ret = crypto_shash_update(desc, &direction, 1);
		if (!ret) ret = crypto_shash_finup(desc, data, bytes, result);
		if (!ret) memcpy(mic, result, 4);
	}
	spin_unlock_bh(&pon->omci_key_lock);
	shash_desc_zero(desc);
	return ret;
}

static int rtl_omci_open(struct net_device *dev)
{
	struct rtl9617c_pon *pon = *(struct rtl9617c_pon **)netdev_priv(dev);
	int ret = rtl9617c_ni_open(pon->ni_dev);

	if (!ret) {
		ret = rtl9617c_ni_oam_configure(pon->ni_dev);
		if (ret) rtl9617c_ni_close(pon->ni_dev, dev);
	}
	if (!ret)
		netif_start_queue(dev);
	return ret;
}

static int rtl_omci_stop(struct net_device *dev)
{
	struct rtl9617c_pon *pon = *(struct rtl9617c_pon **)netdev_priv(dev);

	netif_stop_queue(dev);
	rtl9617c_ni_close(pon->ni_dev, dev);
	return 0;
}

static netdev_tx_t rtl_omci_transmit(struct sk_buff *skb, struct net_device *dev)
{
	struct rtl9617c_pon *pon = *(struct rtl9617c_pon **)netdev_priv(dev);
	u8 header[10], prefix[RTL_OMCI_PREFIX_BYTES];
	unsigned int body, wire, padding;
	struct sk_buff *packet;
	netdev_tx_t ret;
	struct rtl9617c_ni_frame frame = {
		.header_high = BIT(13),
		.header_low = 7 | (15 << 3) | (17 << 9) | BIT(29),
		.prefix = prefix,
		.prefix_length = RTL_OMCI_PREFIX_BYTES,
		.raw_payload = true,
	};

	if (rtl_pon_mode_is_epon(READ_ONCE(pon->mode)) || !netif_carrier_ok(dev) ||
	    skb->len < 8 || skb_copy_bits(skb, 0, header, min(skb->len, 10U)))
		goto drop;
	body = rtl_omci_body_length(header, min(skb->len, 10U));
	if (!body || body != skb->len || body > RTL_OMCI_MAX_BODY)
		goto drop;
	wire = body + 4;
	padding = wire < 48 ? 48 - wire : 0;
	packet = skb_copy_expand(skb, 0, 4 + padding, GFP_ATOMIC);
	if (!packet)
		goto drop;
	if (READ_ONCE(pon->mode) == RTL_PON_GPON)
		put_unaligned_be32(rtl_omci_gpon_crc(packet->data, body), skb_put(packet, 4));
	else if (rtl_omci_mic(pon, 2, packet->data, body, skb_put(packet, 4))) {
		dev_kfree_skb_any(packet);
		goto drop;
	}
	if (padding)
		memset(skb_put(packet, padding), 0, padding);
	rtl_omci_prefix(prefix, wire, packet->data[0]);
	ret = rtl9617c_ni_transmit(pon->ni_dev, dev, packet, &frame);
	if (ret == NETDEV_TX_BUSY) {
		dev_kfree_skb_any(packet);
		return ret;
	}
	dev_consume_skb_any(skb);
	return NETDEV_TX_OK;
drop:
	dev->stats.tx_dropped++;
	dev_kfree_skb_any(skb);
	return NETDEV_TX_OK;
}

void rtl_pon_omci_receive(struct rtl9617c_pon *pon, struct napi_struct *napi,
			  struct sk_buff *skb, unsigned int source)
{
	struct net_device *dev = pon->omci_device;
	unsigned int offset, body;

	if (!dev || !netif_running(dev) || rtl_pon_mode_is_epon(READ_ONCE(pon->mode)) ||
	    (source & 255) != 7)
		goto drop;
	offset = rtl_omci_receive_offset(skb->data, skb->len);
	if (!offset)
		goto drop;
	skb_pull(skb, offset);
	body = rtl_omci_body_length(skb->data, skb->len);
	if (!body || body > RTL_OMCI_MAX_BODY || skb->len < body + 4)
		goto drop;
	if (READ_ONCE(pon->mode) == RTL_PON_GPON) {
		if (get_unaligned_be32(skb->data + body) != rtl_omci_gpon_crc(skb->data, body)) {
			dev->stats.rx_crc_errors++;
			goto drop;
		}
	} else {
		u8 mic[4];

		if (rtl_omci_mic(pon, 1, skb->data, body, mic) || memcmp(mic, skb->data + body, 4)) {
			dev->stats.rx_crc_errors++;
			goto drop;
		}
	}
	skb_trim(skb, body);
	skb->dev = dev;
	skb->protocol = htons(ETH_P_802_2);
	skb->pkt_type = PACKET_HOST;
	skb_reset_mac_header(skb);
	skb_reset_network_header(skb);
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += body;
	netif_receive_skb(skb);
	return;
drop:
	if (dev)
		dev->stats.rx_dropped++;
	dev_kfree_skb_any(skb);
}

static const struct net_device_ops rtl_omci_ops = {
	.ndo_open = rtl_omci_open,
	.ndo_stop = rtl_omci_stop,
	.ndo_start_xmit = rtl_omci_transmit,
};

static void rtl_omci_setup(struct net_device *dev)
{
	dev->type = ARPHRD_NONE;
	dev->flags = IFF_NOARP | IFF_POINTOPOINT;
	dev->mtu = RTL_OMCI_MAX_BODY;
	dev->min_mtu = 44;
	dev->max_mtu = RTL_OMCI_MAX_BODY;
	dev->tx_queue_len = 64;
	dev->netdev_ops = &rtl_omci_ops;
}

int rtl_pon_omci_init(struct rtl9617c_pon *pon)
{
	struct net_device *dev = alloc_netdev(sizeof(pon), "pon-omci", NET_NAME_UNKNOWN, rtl_omci_setup);
	int ret;

	if (!dev)
		return -ENOMEM;
	pon->omci_kdf = crypto_alloc_shash("cmac(aes)", 0, 0);
	if (IS_ERR(pon->omci_kdf)) {
		ret = PTR_ERR(pon->omci_kdf);
		goto free_netdev;
	}
	pon->omci_cmac = crypto_alloc_shash("cmac(aes)", 0, 0);
	if (IS_ERR(pon->omci_cmac)) {
		ret = PTR_ERR(pon->omci_cmac);
		goto free_kdf;
	}
	spin_lock_init(&pon->omci_key_lock);
	*(struct rtl9617c_pon **)netdev_priv(dev) = pon;
	SET_NETDEV_DEV(dev, pon->dev);
	netif_carrier_off(dev);
	ret = register_netdev(dev);
	if (ret) {
		crypto_free_shash(pon->omci_cmac);
		goto free_kdf;
	}
	pon->omci_device = dev;
	return 0;
free_kdf:
	crypto_free_shash(pon->omci_kdf);
free_netdev:
	free_netdev(dev);
	return ret;
}

void rtl_pon_omci_remove(struct rtl9617c_pon *pon)
{
	unregister_netdev(pon->omci_device);
	free_netdev(pon->omci_device);
	pon->omci_device = NULL;
	crypto_free_shash(pon->omci_cmac);
	crypto_free_shash(pon->omci_kdf);
}
