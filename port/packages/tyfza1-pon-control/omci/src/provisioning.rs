// SPDX-License-Identifier: GPL-2.0-only

//! Service configuration model derived from the OMCI MIB.
//!
//! `mib` stores OLT-provisioned managed entities. This snapshot forms the
//! boundary between the MIB and the RTL9617C business-path backend.

use std::collections::BTreeSet;

/// Match any VLAN; the adjacent values distinguish tagged and untagged frames.
pub const DATA_PATH_VLAN_ANY: u16 = u16::MAX;
pub const DATA_PATH_VLAN_UNTAGGED: u16 = u16::MAX - 1;
pub const DATA_PATH_VLAN_TAGGED: u16 = u16::MAX - 2;

#[derive(Clone, Debug)]
pub struct VlanProfile {
    pub entity_id: u16,
    pub input_tpid: u16,
    pub output_tpid: u16,
    pub downstream_mode: u8,
    pub dscp: [u8; 24],
    pub rows: Vec<[u8; 16]>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DataPathCandidate {
    pub alloc_id: u16,
    pub gem_id: u16,
    pub vlan_id: u16,
    pub pbit_mask: u8,
    /// Downstream filtering is independent of upstream 802.1p GEM mapping.
    pub downstream_pbits: u8,
    /// A Class 281 GEM carries downstream multicast traffic.
    pub multicast: bool,
    /// VEIP, UNI bridge port, ANI bridge port, mapper, and GEM IWTP, in wire order.
    pub vlan_profiles: [Option<u16>; 5],
    pub vlan_exclude: u8,
    pub unmarked_mode: u8,
    pub default_priority: u8,
    pub mapper_dscp: [u8; 24],
}

#[derive(Clone, Debug, Default)]
pub struct ProvisioningSnapshot {
    /// OLT-G text fields after fixed-width wire padding is removed.
    pub olt_vendor_id: String,
    pub olt_equipment_id: String,
    pub olt_version: String,
    pub configured_tconts: usize,
    pub gem_ports: usize,
    pub gem_interworking_tps: usize,
    pub vlan_rules: usize,
    /// VLANs used by unicast service data paths.
    pub vlan_ids: BTreeSet<u16>,
    /// VLANs mapped to Class 281 downstream multicast GEMs.
    pub multicast_vlan_ids: BTreeSet<u16>,
    /// IGMP upstream VLANs selected by Class 309 profiles referenced from Class 310.
    pub igmp_upstream_vlan_ids: BTreeSet<u16>,
    /// Class 309 attribute 5: 0 transparent, 1 add, 2 replace TCI, 3 replace VID.
    pub igmp_upstream_tag_controls: BTreeSet<u8>,
    /// Class 332 is present in the MIB.
    pub enhanced_security: bool,
    /// Key indexes with at least one Class 332 attribute 11 broadcast key fragment.
    pub broadcast_key_indexes: BTreeSet<u8>,
    pub data_paths: Vec<DataPathCandidate>,
    pub vlan_profiles: Vec<VlanProfile>,
}

#[derive(Clone, Debug)]
pub struct DataPathTableRow {
    pub key: Vec<u8>,
    pub value: Vec<u8>,
}

#[derive(Clone, Debug)]
pub struct DataPathAttribute {
    pub index: u8,
    pub value: Vec<u8>,
    pub table_rows: Vec<DataPathTableRow>,
}

#[derive(Clone, Debug)]
pub struct DataPathEntity {
    pub class_id: u16,
    pub entity_id: u16,
    pub attributes: Vec<DataPathAttribute>,
}

#[derive(Clone, Debug, Default)]
pub struct DataPathGraph {
    pub candidates: Vec<DataPathCandidate>,
    pub entities: Vec<DataPathEntity>,
}
