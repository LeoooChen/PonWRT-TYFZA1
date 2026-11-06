/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_SERVICE_H
#define RTL9617C_PON_SERVICE_H
/* CPU TX and learned replicas must enter the same OMCI VLAN stage. */
static bool rtl_pon_itu_service_entry(struct net_device *dev, const struct rtl_evto_packet *packet)
{
	if (!packet->count) return false;
	for (unsigned int i = 0; i < packet->count; i++) {
		struct net_device *vlan = __vlan_find_dev_deep_rcu(dev,
			htons(packet->tag[i].tpid), packet->tag[i].tci & 4095);

		/* A bridge port's VID filter can stop the deep lookup at the port. */
		if (!vlan && netif_is_bridge_port(dev)) {
			struct net_device *master = netdev_master_upper_dev_get_rcu(dev);

			if (master) vlan = __vlan_find_dev_deep_rcu(master,
				htons(packet->tag[i].tpid), packet->tag[i].tci & 4095);
		}
		dev = vlan;
		/* A VLAN bridge member still selects an explicit local service. */
		if (!dev) return false;
	}
	return true;
}

/* PON RX and FE learning must try the same service/UNI entry points. */
static const struct rtl_itu_path *rtl_pon_itu_receive_path(const struct rtl_itu_paths *paths,
 struct net_device *dev,unsigned int index,struct rtl_evto_packet *input,struct rtl_evto_packet *output)
{
 const struct rtl_itu_path *path;
 input->service_entry=1;
 path=rtl_itu_path_receive_vlan(paths,index,input,output);
 if(!path || !rtl_pon_itu_service_entry(dev,output)) {
  input->service_entry=0;
  path=rtl_itu_path_receive_vlan(paths,index,input,output);
 }
 return path;
}
#endif
