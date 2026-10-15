// SPDX-License-Identifier: GPL-2.0-only

use std::collections::{BTreeMap, BTreeSet};

use super::config::{fixed_width, IdentityConfig};
use super::protocol::{
    attribute_bit, Request, Response, ACTION_CREATE, ACTION_DELETE, ACTION_GET,
    ACTION_GET_ALL_ALARMS, ACTION_GET_ALL_ALARMS_NEXT, ACTION_GET_CURRENT_DATA, ACTION_GET_NEXT,
    ACTION_MIB_RESET, ACTION_MIB_UPLOAD, ACTION_MIB_UPLOAD_NEXT, ACTION_SET, ACTION_SET_TABLE,
    ACTION_SYNCHRONIZE_TIME, RESULT_ATTRIBUTE_FAILED, RESULT_COMMAND_NOT_SUPPORTED,
    RESULT_INSTANCE_EXISTS, RESULT_PARAMETER_ERROR, RESULT_SUCCESS, RESULT_UNKNOWN_INSTANCE,
    RESULT_UNKNOWN_ME,
};
use super::provisioning::{
    DataPathAttribute, DataPathCandidate, DataPathEntity, DataPathGraph, DataPathTableRow,
    ProvisioningSnapshot, DATA_PATH_VLAN_ANY, DATA_PATH_VLAN_TAGGED, DATA_PATH_VLAN_UNTAGGED,
};
use super::schema::{self, AttributeAccess, AttributeKind, CreateSource, ManagedEntityOrigin};
use super::security;

#[allow(unused_imports)]
pub use super::schema::{
    CLASS_ANI_G, CLASS_CARDHOLDER, CLASS_CIRCUIT_PACK, CLASS_CTC_LOID_AUTH,
    CLASS_ENHANCED_SECURITY_CONTROL, CLASS_ETHERNET_FRAME_EXTENDED_PM,
    CLASS_ETHERNET_PM_HISTORY_DATA, CLASS_ETHERNET_PM_HISTORY_DATA_2,
    CLASS_ETHERNET_PM_HISTORY_DATA_3, CLASS_EXTENDED_VLAN_TAGGING, CLASS_FEC_PM_HISTORY_DATA,
    CLASS_GAL_ETHERNET_PROFILE, CLASS_GEM_INTERWORKING_TP, CLASS_GEM_PORT_NETWORK_CTP,
    CLASS_GEM_PORT_PM_HISTORY_DATA, CLASS_IEEE_8021P_MAPPER, CLASS_MAC_BRIDGE_PORT_CONFIG_DATA,
    CLASS_MAC_BRIDGE_PORT_FILTER_PREASSIGN_DATA, CLASS_MAC_BRIDGE_PORT_PM_HISTORY_DATA,
    CLASS_MAC_BRIDGE_SERVICE_PROFILE, CLASS_MULTICAST_GEM_INTERWORKING_TP,
    CLASS_MULTICAST_OPERATIONS_PROFILE, CLASS_MULTICAST_SUBSCRIBER_CONFIG,
    CLASS_MULTICAST_SUBSCRIBER_MONITOR, CLASS_OLT_G, CLASS_OMCI, CLASS_ONU2_G, CLASS_ONU_DATA,
    CLASS_ONU_G, CLASS_ONU_POWER_SHEDDING, CLASS_PPTP_ETHERNET_UNI, CLASS_PRIORITY_QUEUE,
    CLASS_SOFTWARE_IMAGE, CLASS_TCONT, CLASS_THRESHOLD_DATA_1, CLASS_THRESHOLD_DATA_2,
    CLASS_TRAFFIC_DESCRIPTOR, CLASS_TRAFFIC_SCHEDULER, CLASS_UNI_G, CLASS_VEIP, CLASS_VENDOR_247,
    CLASS_VENDOR_351, CLASS_VLAN_TAGGING_FILTER,
};

const TCONT_COUNT: u16 = 31;
const TCONT_FIRST_ENTITY: u16 = 0x8000;
const ETHERNET_UNI_COUNT: u16 = 4;
const QUEUES_PER_TCONT: u16 = 8;
const QUEUES_PER_UNI: u16 = 8;
const UPSTREAM_PRIORITY_QUEUE_COUNT: u16 = TCONT_COUNT * QUEUES_PER_TCONT;
const DOWNSTREAM_PRIORITY_QUEUE_COUNT: u16 = ETHERNET_UNI_COUNT * QUEUES_PER_UNI;
const TOTAL_PRIORITY_QUEUE_COUNT: u16 =
    UPSTREAM_PRIORITY_QUEUE_COUNT + DOWNSTREAM_PRIORITY_QUEUE_COUNT;
const TOTAL_GEM_PORT_COUNT: u16 = 248;
const VEIP_ENTITY: u16 = 0x0a01;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AttributeValueChange {
    pub class_id: u16,
    pub entity_id: u16,
    pub attribute_mask: u16,
    pub value: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Attribute {
    value: Vec<u8>,
    writable: bool,
    table_row_len: Option<usize>,
    table_rows: BTreeMap<Vec<u8>, Vec<u8>>,
    upload: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct ManagedEntity {
    attributes: BTreeMap<u8, Attribute>,
}

impl ManagedEntity {
    fn read_only(mut self, index: u8, value: Vec<u8>) -> Self {
        self.attributes.insert(
            index,
            Attribute {
                value,
                writable: false,
                table_row_len: None,
                table_rows: BTreeMap::new(),
                upload: true,
            },
        );
        self
    }

    fn read_write(mut self, index: u8, value: Vec<u8>) -> Self {
        self.attributes.insert(
            index,
            Attribute {
                value,
                writable: true,
                table_row_len: None,
                table_rows: BTreeMap::new(),
                upload: true,
            },
        );
        self
    }

    fn table(mut self, index: u8, row_len: usize) -> Self {
        self.attributes.insert(
            index,
            Attribute {
                value: Vec::new(),
                writable: true,
                table_row_len: Some(row_len),
                table_rows: BTreeMap::new(),
                upload: false,
            },
        );
        self
    }

    fn read_only_table(mut self, index: u8, row_len: usize) -> Self {
        self.attributes.insert(
            index,
            Attribute {
                value: Vec::new(),
                writable: false,
                table_row_len: Some(row_len),
                table_rows: BTreeMap::new(),
                upload: false,
            },
        );
        self
    }
}

fn entity_from_definition(class_id: u16, payload: Option<&[u8]>) -> Option<ManagedEntity> {
    let definition = schema::managed_entity(class_id)?;
    let mut entity = ManagedEntity::default();

    for attribute in definition.attributes {
        let table_row_len = match attribute.kind {
            AttributeKind::Scalar { .. } => None,
            AttributeKind::Table { row_length } => Some(row_length),
        };
        let value = match attribute.kind {
            AttributeKind::Table { .. } => Vec::new(),
            AttributeKind::Scalar { length } => {
                let configured = match (payload, attribute.create) {
                    (Some(payload), CreateSource::Required { offset }) => {
                        Some(payload.get(offset..offset + length)?.to_vec())
                    }
                    (Some(payload), CreateSource::Optional { offset }) => {
                        payload.get(offset..offset + length).map(ToOwned::to_owned)
                    }
                    _ => None,
                };
                configured.unwrap_or_else(|| {
                    if attribute.default.is_empty() {
                        vec![0; length]
                    } else {
                        attribute.default.to_vec()
                    }
                })
            }
        };
        entity.attributes.insert(
            attribute.index,
            Attribute {
                value,
                writable: attribute.access == AttributeAccess::ReadWrite,
                table_row_len,
                table_rows: BTreeMap::new(),
                upload: attribute.upload,
            },
        );
    }
    Some(entity)
}

fn priority_queue_entity(related_port: u16, priority: u16) -> ManagedEntity {
    let mut related = Vec::with_capacity(4);
    related.extend_from_slice(&related_port.to_be_bytes());
    related.extend_from_slice(&priority.to_be_bytes());

    ManagedEntity::default()
        .read_only(1, vec![1])
        .read_only(2, 0xffffu16.to_be_bytes().to_vec())
        .read_write(3, 4u16.to_be_bytes().to_vec())
        .read_write(6, related)
        .read_write(7, 0u16.to_be_bytes().to_vec())
        .read_write(8, vec![1])
        .read_write(9, 0u16.to_be_bytes().to_vec())
        .read_write(10, 0u32.to_be_bytes().to_vec())
        .read_write(11, 0xffffu16.to_be_bytes().to_vec())
        .read_write(12, 0u16.to_be_bytes().to_vec())
}

fn mac_bridge_port_filter_preassign_entity() -> ManagedEntity {
    entity_from_definition(CLASS_MAC_BRIDGE_PORT_FILTER_PREASSIGN_DATA, None)
        .expect("filter preassign schema is registered")
}

fn omci_capability_entity(enhanced_security: bool) -> ManagedEntity {
    let mut entity = entity_from_definition(CLASS_OMCI, None).expect("OMCI schema is registered");
    let class_table = entity.attributes.get_mut(&1).expect("OMCI class table");
    /*
     * Class 351 lies in the vendor-specific range 350..399. Huawei OLTs create it without
     * checking this table; advertising it could invite another vendor's layout.
     */
    for definition in schema::MANAGED_ENTITIES.iter().filter(|definition| {
        (enhanced_security || definition.class_id != CLASS_ENHANCED_SECURITY_CONTROL)
            && definition.class_id != CLASS_VENDOR_351
    }) {
        let row = definition.class_id.to_be_bytes().to_vec();
        class_table.table_rows.insert(row.clone(), row);
    }

    let message_table = entity.attributes.get_mut(&2).expect("OMCI message table");
    for action in 0..32u8 {
        if schema::MANAGED_ENTITIES
            .iter()
            .any(|definition| definition.supports_action(action))
        {
            message_table.table_rows.insert(vec![action], vec![action]);
        }
    }
    entity
}

#[derive(Clone, Debug)]
pub struct Mib {
    entities: BTreeMap<(u16, u16), ManagedEntity>,
    autonomous_defaults: BTreeMap<(u16, u16), ManagedEntity>,
    known_classes: BTreeSet<u16>,
    olt_created: BTreeSet<(u16, u16)>,
    configured_priority_queues: BTreeSet<u16>,
    gem_queue_bindings: BTreeMap<u16, (u16, u16)>,
    dynamic_classes: BTreeSet<u16>,
    table_snapshot: Option<TableSnapshot>,
    upload_snapshot: Vec<UploadRecord>,
    onu_serial: [u8; 8],
    pending_master_session_key: Option<[u8; 16]>,
    attribute_value_changes: Vec<AttributeValueChange>,
    enhanced_security: bool,
    psk: Option<[u8; 16]>,
}

#[derive(Clone, Debug)]
struct TableSnapshot {
    class_id: u16,
    entity_id: u16,
    attribute_mask: u16,
    bytes: Vec<u8>,
}

#[derive(Clone, Debug)]
struct UploadRecord {
    class_id: u16,
    entity_id: u16,
    attribute_mask: u16,
    values: Vec<u8>,
}

impl Mib {
    pub fn from_identity(identity: &IdentityConfig) -> Self {
        let onu_serial: [u8; 8] = identity
            .onu_g_serial()
            .try_into()
            .expect("onu_g_serial always returns eight bytes");
        let mut mib = Self {
            entities: BTreeMap::new(),
            autonomous_defaults: BTreeMap::new(),
            known_classes: BTreeSet::new(),
            olt_created: BTreeSet::new(),
            configured_priority_queues: BTreeSet::new(),
            gem_queue_bindings: BTreeMap::new(),
            dynamic_classes: BTreeSet::new(),
            table_snapshot: None,
            upload_snapshot: Vec::new(),
            onu_serial,
            pending_master_session_key: None,
            attribute_value_changes: Vec::new(),
            enhanced_security: !identity.disable_enhanced_security,
            psk: identity.psk,
        };

        let onu_data = ManagedEntity::default().read_write(1, vec![0]);
        mib.insert(CLASS_ONU_DATA, 0, onu_data);

        // Image0 is running; no alternate OMCI-managed image is provisioned.
        let image_version = fixed_width(&identity.software_version, 14);
        mib.insert(
            CLASS_SOFTWARE_IMAGE,
            0,
            ManagedEntity::default()
                .read_only(1, image_version.clone())
                .read_only(2, vec![1])
                .read_only(3, vec![1])
                .read_only(4, vec![1]),
        );
        mib.insert(
            CLASS_SOFTWARE_IMAGE,
            1,
            ManagedEntity::default()
                .read_only(1, image_version)
                .read_only(2, vec![0])
                .read_only(3, vec![0])
                .read_only(4, vec![0]),
        );

        let onu_g = ManagedEntity::default()
            .read_only(1, fixed_width(&identity.vendor_id, 4))
            .read_only(2, fixed_width(&identity.hardware_version, 14))
            .read_only(3, onu_serial.to_vec())
            // Option 2 advertises priority-controlled scheduling with rate-controlled shaping.
            .read_only(4, vec![0x02])
            .read_only(5, vec![0])
            .read_write(6, vec![0])
            .read_write(7, vec![0])
            .read_only(8, vec![0])
            .read_only(9, vec![0])
            .read_only(10, fixed_width(&identity.loid, 24))
            .read_only(11, fixed_width(&identity.loid_password, 12))
            .read_write(12, vec![0])
            .read_only(13, vec![0]);
        mib.insert(CLASS_ONU_G, 0, onu_g);

        // ONU2-G capabilities mirror the autonomous T-CONT, queue, and GEM capacity below.
        let onu2_g = ManagedEntity::default()
            .read_only(1, fixed_width(&identity.equipment_id, 20))
            .read_only(2, vec![identity.omcc_version])
            .read_only(3, vec![0, 0])
            .read_only(4, vec![1])
            .read_write(5, vec![1])
            .read_only(6, TOTAL_PRIORITY_QUEUE_COUNT.to_be_bytes().to_vec())
            .read_only(7, vec![TCONT_COUNT as u8])
            .read_only(8, vec![1])
            .read_only(9, TOTAL_GEM_PORT_COUNT.to_be_bytes().to_vec())
            .read_only(10, vec![0; 4])
            .read_only(11, vec![0, 0])
            .read_write(12, vec![0])
            // The native backend provides fixed T-CONT queue associations.
            .read_only(13, 0u16.to_be_bytes().to_vec())
            .read_write(14, 1u16.to_be_bytes().to_vec());
        mib.insert(CLASS_ONU2_G, 0, onu2_g);

        // OpenWrt owns the physical LAN bridge; OMCI presents the PON-facing VEIP.

        let ani = ManagedEntity::default()
            .read_only(1, vec![1])
            .read_only(2, TCONT_COUNT.to_be_bytes().to_vec());
        mib.insert(CLASS_ANI_G, 0x8001, ani);

        // ONU power shedding is an autonomous singleton with zeroed timers.
        mib.insert(
            CLASS_ONU_POWER_SHEDDING,
            0,
            ManagedEntity::default()
                .read_write(1, 0u16.to_be_bytes().to_vec())
                .read_write(2, 0u16.to_be_bytes().to_vec())
                .read_write(4, 0u16.to_be_bytes().to_vec())
                .read_write(5, 0u16.to_be_bytes().to_vec())
                .read_write(6, 0u16.to_be_bytes().to_vec())
                .read_write(7, 0u16.to_be_bytes().to_vec())
                .read_write(8, 0u16.to_be_bytes().to_vec())
                .read_write(9, 0u16.to_be_bytes().to_vec())
                .read_write(10, 0u16.to_be_bytes().to_vec())
                .read_write(11, 0u16.to_be_bytes().to_vec())
                .read_only(12, vec![0]),
        );

        let ctc_auth = ManagedEntity::default()
            .read_only(1, fixed_width(&identity.operator_id, 4))
            .read_only(2, fixed_width(&identity.loid, 24))
            .read_only(3, fixed_width(&identity.loid_password, 12))
            .read_write(4, vec![0]);
        mib.insert(CLASS_CTC_LOID_AUTH, 0, ctc_auth);

        if !identity.disable_enhanced_security {
            /* Class 332 negotiates cryptographic capabilities and key length for each O5 epoch. */
            mib.insert(
                CLASS_ENHANCED_SECURITY_CONTROL,
                0,
                ManagedEntity::default()
                    .read_write(1, vec![0; 16])
                    .table(2, 17)
                    .read_write(3, vec![0])
                    .read_only(4, vec![1])
                    .read_only_table(5, 16)
                    .read_only_table(6, 16)
                    .table(7, 17)
                    .read_write(8, vec![0])
                    .read_only(9, vec![0])
                    .read_only(10, vec![0; 16])
                    .table(11, 18)
                    .read_only(12, 128u16.to_be_bytes().to_vec()),
            );
        }

        // Business T-CONT entities map to hardware slots1..31; slot0 serves OMCC.
        // XG-PON represents an unassigned Alloc-ID as 0xffff.
        for offset in 0..TCONT_COUNT {
            mib.insert(
                CLASS_TCONT,
                TCONT_FIRST_ENTITY + offset,
                ManagedEntity::default()
                    .read_write(1, 0xffffu16.to_be_bytes().to_vec())
                    .read_only(2, vec![1])
                    .read_write(3, vec![0]),
            );
        }

        // Each T-CONT and Ethernet UNI owns eight priority queues.
        for offset in 0..UPSTREAM_PRIORITY_QUEUE_COUNT {
            let entity_id = TCONT_FIRST_ENTITY + offset;
            let tcont_id = TCONT_FIRST_ENTITY + offset / QUEUES_PER_TCONT;
            let priority = 7 - (offset % QUEUES_PER_TCONT);
            mib.insert(
                CLASS_PRIORITY_QUEUE,
                entity_id,
                priority_queue_entity(tcont_id, priority),
            );
        }
        for offset in 0..TCONT_COUNT {
            let entity_id = TCONT_FIRST_ENTITY + offset;
            mib.insert(
                CLASS_TRAFFIC_SCHEDULER,
                entity_id,
                ManagedEntity::default()
                    .read_write(1, entity_id.to_be_bytes().to_vec())
                    .read_only(2, 0u16.to_be_bytes().to_vec())
                    .read_write(3, vec![1])
                    .read_write(4, vec![0]),
            );
        }

        // G.988 HGU presentation: one PON-facing VEIP.
        mib.insert(
            CLASS_VEIP,
            VEIP_ENTITY,
            ManagedEntity::default()
                .read_write(1, vec![0])
                .read_only(2, vec![0])
                .read_write(3, vec![0; 25])
                .read_write(4, 0xffffu16.to_be_bytes().to_vec())
                .read_only(5, vec![0, 0]),
        );

        mib.insert(
            CLASS_OLT_G,
            0,
            entity_from_definition(CLASS_OLT_G, None).expect("OLT-G schema is registered"),
        );

        mib.insert(
            CLASS_OMCI,
            0,
            omci_capability_entity(!identity.disable_enhanced_security),
        );

        for definition in schema::MANAGED_ENTITIES {
            mib.known_classes.insert(definition.class_id);
            if definition.origin == ManagedEntityOrigin::Olt {
                mib.dynamic_classes.insert(definition.class_id);
            }
        }

        // The MIB Reset template preserves initial attributes of autonomous entities.
        mib.autonomous_defaults = mib.entities.clone();
        mib
    }

    fn insert(&mut self, class_id: u16, entity_id: u16, entity: ManagedEntity) {
        self.known_classes.insert(class_id);
        self.entities.insert((class_id, entity_id), entity);
    }

    pub fn dispatch(&mut self, request: &Request<'_>) -> Response {
        if request.class_id == CLASS_ENHANCED_SECURITY_CONTROL && !self.enhanced_security {
            /* The disabled class is absent from the MIB and the OMCI class table, so it is an unknown ME. */
            return Response::new(request, RESULT_UNKNOWN_ME);
        }
        let Some(definition) = schema::managed_entity(request.class_id) else {
            return Response::new(request, RESULT_UNKNOWN_ME);
        };
        debug_assert!(!definition.name.is_empty());
        if !definition.supports_action(request.action) {
            return Response::new(request, RESULT_COMMAND_NOT_SUPPORTED);
        }

        match request.action {
            ACTION_CREATE => self.handle_create(request),
            ACTION_DELETE => self.handle_delete(request),
            ACTION_GET | ACTION_GET_CURRENT_DATA => self.handle_get(request),
            ACTION_GET_ALL_ALARMS => self.handle_get_all_alarms(request),
            ACTION_GET_ALL_ALARMS_NEXT => self.handle_get_all_alarms_next(request),
            ACTION_GET_NEXT => self.handle_get_next(request),
            ACTION_SET => self.handle_set(request),
            ACTION_SET_TABLE => self.handle_set_table(request),
            ACTION_MIB_UPLOAD => self.handle_mib_upload(request),
            ACTION_MIB_UPLOAD_NEXT => self.handle_mib_upload_next(request),
            ACTION_MIB_RESET => self.handle_mib_reset(request),
            ACTION_SYNCHRONIZE_TIME => self.handle_synchronize_time(request),
            _ => Response::new(request, RESULT_COMMAND_NOT_SUPPORTED),
        }
    }

    fn entity_error(&self, request: &Request<'_>) -> Response {
        let result = if self.known_classes.contains(&request.class_id) {
            RESULT_UNKNOWN_INSTANCE
        } else {
            RESULT_UNKNOWN_ME
        };
        Response::new(request, result)
    }

    fn handle_create(&mut self, request: &Request<'_>) -> Response {
        if !self.dynamic_classes.contains(&request.class_id) {
            return if self.known_classes.contains(&request.class_id) {
                Response::new(request, RESULT_COMMAND_NOT_SUPPORTED)
            } else {
                Response::new(request, RESULT_UNKNOWN_ME)
            };
        }
        let Some(mut entity) = Self::dynamic_entity(request.class_id, request.payload) else {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        };
        if request.class_id == CLASS_EXTENDED_VLAN_TAGGING {
            if !matches!(entity.attributes[&1].value[0], 0..=7 | 9..=12) {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            let table = entity.attributes.get_mut(&6).unwrap();
            for (outer, inner) in [(0xf8000000u32, 0xf8000000u32),
                                   (0xf8000000, 0xe8000000), (0xe8000000, 0xe8000000)] {
                let mut row = Vec::with_capacity(16);
                for word in [outer, inner, 0x000f0000, 0x000f0000] {
                    row.extend_from_slice(&word.to_be_bytes());
                }
                table.table_rows.insert(row[..8].to_vec(), row);
            }
        }
        if request.class_id == CLASS_IEEE_8021P_MAPPER && entity.attributes[&10].value[0] > 1 {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }
        if let Some(existing) = self.entities.get(&(request.class_id, request.entity_id)) {
            /* Repeated Create requests recover a lost response; identical content reuses the instance and MIB Data Sync value. */
            let result = if existing == &entity {
                RESULT_SUCCESS
            } else {
                RESULT_INSTANCE_EXISTS
            };
            return Response::new(request, result);
        }
        self.insert(request.class_id, request.entity_id, entity);
        if request.class_id == CLASS_MAC_BRIDGE_PORT_CONFIG_DATA {
            /* Creating Class 47 also creates the same Class 79 entity used by the following filter policy Set. */
            self.insert(
                CLASS_MAC_BRIDGE_PORT_FILTER_PREASSIGN_DATA,
                request.entity_id,
                mac_bridge_port_filter_preassign_entity(),
            );
        }
        self.olt_created
            .insert((request.class_id, request.entity_id));
        self.increment_mib_sync();
        Response::new(request, RESULT_SUCCESS)
    }

    fn handle_delete(&mut self, request: &Request<'_>) -> Response {
        let key = (request.class_id, request.entity_id);
        if !self.entities.contains_key(&key) {
            return self.entity_error(request);
        }
        if !self.olt_created.remove(&key) {
            return Response::new(request, RESULT_COMMAND_NOT_SUPPORTED);
        }
        self.entities.remove(&key);
        if request.class_id == CLASS_MAC_BRIDGE_PORT_CONFIG_DATA {
            self.entities.remove(&(
                CLASS_MAC_BRIDGE_PORT_FILTER_PREASSIGN_DATA,
                request.entity_id,
            ));
        }
        self.increment_mib_sync();
        Response::new(request, RESULT_SUCCESS)
    }

    fn dynamic_entity(class_id: u16, payload: &[u8]) -> Option<ManagedEntity> {
        let definition = schema::managed_entity(class_id)?;
        (definition.origin == ManagedEntityOrigin::Olt)
            .then(|| entity_from_definition(class_id, Some(payload)))?
    }

    fn handle_get_next(&self, request: &Request<'_>) -> Response {
        let Some(snapshot) = self.table_snapshot.as_ref() else {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        };
        if snapshot.class_id != request.class_id
            || snapshot.entity_id != request.entity_id
            || snapshot.attribute_mask != request.attribute_mask
        {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }

        let sequence = u16::from_be_bytes([request.content[0], request.content[1]]) as usize;
        let capacity = request.get_next_value_capacity();
        let start = sequence * capacity;
        if start >= snapshot.bytes.len() && !snapshot.bytes.is_empty() {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }
        let end = (start + capacity).min(snapshot.bytes.len());
        Response::get_next_success(
            request,
            snapshot.attribute_mask,
            &snapshot.bytes[start..end],
        )
        .expect("table chunk is bounded by baseline capacity")
    }

    fn handle_mib_upload(&mut self, request: &Request<'_>) -> Response {
        if request.class_id != CLASS_ONU_DATA || request.entity_id != 0 {
            return self.entity_error(request);
        }
        self.rebuild_upload_snapshot();
        Response::mib_upload(request, self.upload_snapshot.len() as u16)
    }

    fn handle_get_all_alarms(&self, request: &Request<'_>) -> Response {
        Response::get_all_alarms(request, 0)
    }

    fn handle_get_all_alarms_next(&self, request: &Request<'_>) -> Response {
        Response::get_all_alarms_next_empty(request)
    }

    fn handle_mib_upload_next(&self, request: &Request<'_>) -> Response {
        if request.class_id != CLASS_ONU_DATA || request.entity_id != 0 {
            return self.entity_error(request);
        }
        let sequence = request.attribute_mask as usize;
        let Some(record) = self.upload_snapshot.get(sequence) else {
            return Response::mib_upload_next(request, 0, 0, 0, &[])
                .expect("empty upload record always fits");
        };
        Response::mib_upload_next(
            request,
            record.class_id,
            record.entity_id,
            record.attribute_mask,
            &record.values,
        )
        .expect("upload records are split to baseline capacity")
    }

    fn rebuild_upload_snapshot(&mut self) {
        let mut records = Vec::new();
        for (&(class_id, entity_id), entity) in &self.entities {
            // CTC requires LOID authentication attributes to be read with GET, not MIB Upload.
            if class_id == CLASS_CTC_LOID_AUTH {
                continue;
            }
            let mut mask = 0u16;
            let mut values = Vec::new();
            for (&index, attribute) in &entity.attributes {
                // MIB Upload carries persistent scalar state; PM counters and tables use GET.
                if !attribute.upload || attribute.table_row_len.is_some() {
                    continue;
                }
                let bit = attribute_bit(index).expect("stored attribute index is valid");
                if !values.is_empty() && values.len() + attribute.value.len() > 26 {
                    records.push(UploadRecord {
                        class_id,
                        entity_id,
                        attribute_mask: mask,
                        values,
                    });
                    mask = 0;
                    values = Vec::new();
                }
                if attribute.value.len() <= 26 {
                    mask |= bit;
                    values.extend_from_slice(&attribute.value);
                }
            }
            if mask != 0 {
                records.push(UploadRecord {
                    class_id,
                    entity_id,
                    attribute_mask: mask,
                    values,
                });
            }
        }
        self.upload_snapshot = records;
    }

    fn handle_get(&mut self, request: &Request<'_>) -> Response {
        let Some(entity) = self.entities.get(&(request.class_id, request.entity_id)) else {
            return self.entity_error(request);
        };
        if request.attribute_mask == 0 {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }

        let mut returned_mask = 0u16;
        let mut values = Vec::new();
        let mut table = None;
        for index in 1..=16 {
            let bit = attribute_bit(index).expect("1..=16 always has an attribute bit");
            if request.attribute_mask & bit == 0 {
                continue;
            }
            let Some(attribute) = entity.attributes.get(&index) else {
                return Response::new(request, RESULT_ATTRIBUTE_FAILED);
            };
            if !attribute.upload && attribute.table_row_len.is_none() {
                return Response::new(request, RESULT_ATTRIBUTE_FAILED);
            }
            if attribute.table_row_len.is_some() {
                if request.attribute_mask.count_ones() != 1 {
                    return Response::new(request, RESULT_PARAMETER_ERROR);
                }
                let bytes = attribute
                    .table_rows
                    .values()
                    .flat_map(|row| row.iter().copied())
                    .collect::<Vec<_>>();
                values.extend_from_slice(&(bytes.len() as u32).to_be_bytes());
                table = Some(TableSnapshot {
                    class_id: request.class_id,
                    entity_id: request.entity_id,
                    attribute_mask: bit,
                    bytes,
                });
                returned_mask |= bit;
                continue;
            }
            if values.len() + attribute.value.len() > request.get_value_capacity() {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            returned_mask |= bit;
            values.extend_from_slice(&attribute.value);
        }

        self.table_snapshot = table;
        Response::get_success(request, returned_mask, &values)
            .expect("capacity was checked before response construction")
    }

    fn handle_set(&mut self, request: &Request<'_>) -> Response {
        let class_known = self.known_classes.contains(&request.class_id);
        let Some(entity) = self
            .entities
            .get_mut(&(request.class_id, request.entity_id))
        else {
            return Response::new(
                request,
                if class_known {
                    RESULT_UNKNOWN_INSTANCE
                } else {
                    RESULT_UNKNOWN_ME
                },
            );
        };
        if request.attribute_mask == 0 {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }

        // Validate every attribute and length before applying the Set atomically.
        let mut cursor = 0usize;
        let mut updates = Vec::<(u8, Vec<u8>, bool)>::new();
        for index in 1..=16 {
            let bit = attribute_bit(index).expect("1..=16 always has an attribute bit");
            if request.attribute_mask & bit == 0 {
                continue;
            }
            let Some(attribute) = entity.attributes.get(&index) else {
                return Response::new(request, RESULT_ATTRIBUTE_FAILED);
            };
            if !attribute.writable {
                return Response::new(request, RESULT_ATTRIBUTE_FAILED);
            }
            let length = attribute.table_row_len.unwrap_or(attribute.value.len());
            if attribute.table_row_len.is_some() && request.attribute_mask.count_ones() != 1 {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            let end = cursor + length;
            if end > request.content.len() {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            if request.class_id == CLASS_EXTENDED_VLAN_TAGGING
                && ((index == 1 && !matches!(request.content[cursor], 0..=7 | 9..=12))
                    || (index == 5 && request.content[cursor] > 8)
                    || (index == 6 && !valid_extended_vlan_row(&request.content[cursor..end]))) {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            if request.class_id == CLASS_IEEE_8021P_MAPPER
                && index == 10 && request.content[cursor] > 1 {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            updates.push((
                index,
                request.content[cursor..end].to_vec(),
                attribute.table_row_len.is_some(),
            ));
            cursor = end;
        }

        if request.class_id == CLASS_ENHANCED_SECURITY_CONTROL && request.entity_id == 0 {
            if self.apply_enhanced_security_updates(&updates).is_err() {
                return Response::new(request, RESULT_ATTRIBUTE_FAILED);
            }
            self.increment_mib_sync();
            return Response::set_success(request);
        }

        for (index, value, is_table) in updates {
            let attribute = entity
                .attributes
                .get_mut(&index)
                .expect("attribute was validated above");
            if is_table {
                let key = value[..8].to_vec();
                if value[8..].iter().all(|byte| *byte == 0xff) {
                    attribute.table_rows.remove(&key);
                } else {
                    if request.class_id == CLASS_EXTENDED_VLAN_TAGGING && index == 6
                        && !attribute.table_rows.contains_key(&key) && attribute.table_rows.len() == 32 {
                        return Response::new(request, 1);
                    }
                    attribute.table_rows.insert(key, value);
                }
            } else {
                attribute.value = value;
            }
        }
        if request.class_id == CLASS_PRIORITY_QUEUE && request.entity_id & 0x8000 != 0 {
            self.configured_priority_queues.insert(request.entity_id);
        }
        self.increment_mib_sync();
        Response::set_success(request)
    }

    fn apply_enhanced_security_updates(
        &mut self,
        updates: &[(u8, Vec<u8>, bool)],
    ) -> Result<(), ()> {
        let mut run_step1 = false;
        let mut run_step2 = false;
        let entity = self
            .entities
            .get_mut(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .ok_or(())?;

        for (index, value, is_table) in updates {
            match (*index, *is_table) {
                (2 | 7, true) => {
                    /*
                     * Class 332 challenge rows use the first byte as their key, and
                     * row zero clears the table. Rewriting a key replaces its row.
                     */
                    let attribute = entity.attributes.get_mut(index).ok_or(())?;
                    if value[0] == 0 {
                        attribute.table_rows.clear();
                    } else {
                        attribute.table_rows.insert(vec![value[0]], value.clone());
                    }
                    let status_index = if *index == 2 { 3 } else { 8 };
                    entity.attributes.get_mut(&status_index).ok_or(())?.value[0] = 0;
                }
                (1, false) => {
                    entity.attributes.get_mut(index).ok_or(())?.value = value.clone();
                    entity.attributes.get_mut(&3).ok_or(())?.value[0] = 0;
                }
                (11, true) => {
                    if value[0] > 2 || (value[0] != 2 && ((value[1] & 63) != 0 || !(1..=2).contains(&(value[1] >> 6)))) { return Err(()); }
                    /*
                     * Broadcast key rows are keyed by the row identifier (key index
                     * and fragment number). Row control bits 2..1 select set row,
                     * clear row, or clear table.
                     */
                    let attribute = entity.attributes.get_mut(index).ok_or(())?;
                    match value[0] & 0x03 {
                        0 => {
                            attribute.table_rows.insert(vec![value[1]], value.clone());
                        }
                        1 => {
                            attribute.table_rows.remove(&[value[1]][..]);
                        }
                        2 => attribute.table_rows.clear(),
                        _ => return Err(()),
                    }
                }
                (3 | 8, false) if value[0] <= 1 => {
                    let attribute = entity.attributes.get_mut(index).ok_or(())?;
                    let rising_edge = attribute.value[0] == 0 && value[0] == 1;
                    attribute.value[0] = value[0];
                    run_step1 |= *index == 3 && rising_edge;
                    run_step2 |= *index == 8 && rising_edge;
                }
                _ => return Err(()),
            }
        }

        if run_step1 {
            self.enhanced_security_step1()?;
        }
        if run_step2 {
            self.enhanced_security_step2()?;
        }
        Ok(())
    }

    fn enhanced_security_step1(&mut self) -> Result<(), ()> {
        let psk = self.psk.ok_or(())?;
        let olt_challenges = self.enhanced_security_challenges(2, true)?;
        let mut random = [0u8; 16];
        security::fill_random(&mut random).map_err(|_| ())?;
        let onu_challenges = vec![random];
        let authentication_result =
            security::authentication_result(&psk, &olt_challenges, &onu_challenges, &[0; 8], true)
                .ok_or(())?;

        let entity = self
            .entities
            .get_mut(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .ok_or(())?;
        let onu_table = entity.attributes.get_mut(&5).ok_or(())?;
        onu_table.table_rows.clear();
        for (index, challenge) in onu_challenges.iter().enumerate() {
            onu_table
                .table_rows
                .insert(vec![(index + 1) as u8], challenge.to_vec());
        }
        let result_table = entity.attributes.get_mut(&6).ok_or(())?;
        result_table.table_rows.clear();
        result_table
            .table_rows
            .insert(vec![1], authentication_result.to_vec());

        self.queue_table_avc(5, onu_challenges.len() * 16);
        self.queue_table_avc(6, 16);
        Ok(())
    }

    fn enhanced_security_step2(&mut self) -> Result<(), ()> {
        let psk = self.psk.ok_or(())?;
        let olt_challenges = self.enhanced_security_challenges(2, true)?;
        let onu_challenges = self.enhanced_security_challenges(5, false)?;
        let expected = security::authentication_result(
            &psk,
            &olt_challenges,
            &onu_challenges,
            &self.onu_serial,
            false,
        )
        .ok_or(())?;
        let received = self
            .entities
            .get(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .and_then(|entity| entity.attributes.get(&7))
            .and_then(|attribute| attribute.table_rows.values().next())
            .and_then(|row| row.get(1..17));

        if received != Some(expected.as_slice()) {
            self.set_enhanced_authentication_state(4);
            return Ok(());
        }

        let name = security::master_session_key_name(&psk, &olt_challenges, &onu_challenges).ok_or(())?;
        let msk = security::master_session_key(&psk, &olt_challenges, &onu_challenges).ok_or(())?;
        self.entities
            .get_mut(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .and_then(|entity| entity.attributes.get_mut(&10))
            .ok_or(())?
            .value = name.to_vec();
        /* State 3 is published after the driver installs the OIK. */
        self.pending_master_session_key = Some(msk);
        Ok(())
    }

    fn enhanced_security_challenges(
        &self,
        attribute_index: u8,
        indexed: bool,
    ) -> Result<Vec<[u8; 16]>, ()> {
        let attribute = self
            .entities
            .get(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .and_then(|entity| entity.attributes.get(&attribute_index))
            .ok_or(())?;
        let mut challenges = Vec::with_capacity(attribute.table_rows.len());
        for (position, (index, row)) in attribute.table_rows.iter().enumerate() {
            if indexed && index[0] as usize != position + 1 { return Err(()); }
            let bytes = if indexed {
                row.get(1..17)
            } else {
                row.get(0..16)
            }
            .ok_or(())?;
            challenges.push(bytes.try_into().map_err(|_| ())?);
        }
        if challenges.is_empty() {
            return Err(());
        }
        Ok(challenges)
    }

    fn queue_table_avc(&mut self, attribute_index: u8, byte_length: usize) {
        self.queue_avc(attribute_index, (byte_length as u32).to_be_bytes().to_vec());
    }

    fn queue_avc(&mut self, attribute_index: u8, value: Vec<u8>) {
        self.attribute_value_changes.push(AttributeValueChange {
            class_id: CLASS_ENHANCED_SECURITY_CONTROL,
            entity_id: 0,
            attribute_mask: attribute_bit(attribute_index)
                .expect("Class 332 attribute index is valid"),
            value,
        });
    }

    fn set_enhanced_authentication_state(&mut self, state: u8) {
        if let Some(attribute) = self
            .entities
            .get_mut(&(CLASS_ENHANCED_SECURITY_CONTROL, 0))
            .and_then(|entity| entity.attributes.get_mut(&9))
        {
            attribute.value[0] = state;
        }
        self.queue_avc(9, vec![state]);
    }

    pub fn reset_security_session(&mut self) {
        let key = (CLASS_ENHANCED_SECURITY_CONTROL, 0);
        if let Some(initial) = self.autonomous_defaults.get(&key) {
            self.entities.insert(key, initial.clone());
        }
        self.pending_master_session_key = None;
        self.attribute_value_changes.clear();
    }

    pub fn take_pending_master_session_key(&mut self) -> Option<[u8; 16]> {
        self.pending_master_session_key.take()
    }

    pub fn set_onu_serial(&mut self, serial: [u8; 8]) {
        self.onu_serial = serial;
        /* ONU-G attribute 3 and Class 332 use the same active PLOAM serial number. */
        for entities in [&mut self.entities, &mut self.autonomous_defaults] {
            if let Some(attribute) = entities
                .get_mut(&(CLASS_ONU_G, 0))
                .and_then(|entity| entity.attributes.get_mut(&3))
            {
                attribute.value = serial.to_vec();
            }
        }
    }

    pub fn complete_master_session_key_install(&mut self, installed: bool) {
        self.set_enhanced_authentication_state(if installed { 3 } else { 4 });
    }

    pub fn take_attribute_value_changes(&mut self) -> Vec<AttributeValueChange> {
        std::mem::take(&mut self.attribute_value_changes)
    }

    fn handle_set_table(&mut self, request: &Request<'_>) -> Response {
        let class_known = self.known_classes.contains(&request.class_id);
        let Some(entity) = self
            .entities
            .get_mut(&(request.class_id, request.entity_id))
        else {
            return Response::new(
                request,
                if class_known {
                    RESULT_UNKNOWN_INSTANCE
                } else {
                    RESULT_UNKNOWN_ME
                },
            );
        };
        if request.attribute_mask.count_ones() != 1 {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }

        let index = request.attribute_mask.leading_zeros() as u8 + 1;
        let Some(attribute) = entity.attributes.get_mut(&index) else {
            return Response::new(request, RESULT_ATTRIBUTE_FAILED);
        };
        let Some(row_len) = attribute.table_row_len else {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        };
        if !attribute.writable
            || row_len < 8
            || request.content.is_empty()
            || request.content.len() % row_len != 0
        {
            return Response::new(request, RESULT_PARAMETER_ERROR);
        }

        // Set-table applies rows in wire order using each table's add/delete control field.
        let mut rows = attribute.table_rows.clone();
        for row in request.content.chunks_exact(row_len) {
            if request.class_id == CLASS_EXTENDED_VLAN_TAGGING && index == 6 && !valid_extended_vlan_row(row) {
                return Response::new(request, RESULT_PARAMETER_ERROR);
            }
            let key = row[..8].to_vec();
            if row[8..].iter().all(|byte| *byte == 0xff) {
                rows.remove(&key);
            } else {
                rows.insert(key, row.to_vec());
            }
        }
        if request.class_id == CLASS_EXTENDED_VLAN_TAGGING && index == 6 && rows.len() > 32 {
            return Response::new(request, 1);
        }
        attribute.table_rows = rows;
        self.increment_mib_sync();
        Response::set_success(request)
    }

    fn handle_mib_reset(&mut self, request: &Request<'_>) -> Response {
        if request.class_id != CLASS_ONU_DATA || request.entity_id != 0 {
            return self.entity_error(request);
        }
        let loid_authentication = self.entities.get(&(CLASS_CTC_LOID_AUTH, 0)).cloned();
        self.entities = self.autonomous_defaults.clone();
        // The CTC LOID Authentication ME survives MIB Reset with every attribute unchanged.
        if let Some(entity) = loid_authentication {
            self.entities.insert((CLASS_CTC_LOID_AUTH, 0), entity);
        }
        self.olt_created.clear();
        self.configured_priority_queues.clear();
        self.gem_queue_bindings.clear();
        self.table_snapshot = None;
        self.upload_snapshot.clear();
        self.pending_master_session_key = None;
        self.attribute_value_changes.clear();
        Response::new(request, 0)
    }

    fn handle_synchronize_time(&self, request: &Request<'_>) -> Response {
        if request.class_id != CLASS_ONU_G || request.entity_id != 0 {
            return self.entity_error(request);
        }
        Response::synchronize_time_success(request)
    }

    fn increment_mib_sync(&mut self) {
        if let Some(sync) = self
            .entities
            .get_mut(&(CLASS_ONU_DATA, 0))
            .and_then(|entity| entity.attributes.get_mut(&1))
        {
            // G.988 MIB Data Sync cycles through 1..255; zero represents reset state.
            sync.value[0] = if sync.value[0] == 255 {
                1
            } else {
                sync.value[0] + 1
            };
        }
    }

    pub fn provisioning_snapshot(&self) -> ProvisioningSnapshot {
        let mut snapshot = ProvisioningSnapshot::default();
        if self.entities.get(&(CLASS_VEIP, VEIP_ENTITY))
            .and_then(|entity| entity.attributes.get(&1))
            .map(|attribute| attribute.value.as_slice()) == Some(&[1]) {
            return snapshot;
        }
        let mut tcont_alloc = BTreeMap::<u16, u16>::new();
        let mut gem_id_by_entity = BTreeMap::<u16, u16>::new();
        let mut unicast_by_entity = BTreeMap::<u16, DataPathCandidate>::new();
        let mut multicast_by_iwtp = BTreeMap::<u16, DataPathCandidate>::new();
        let mut mapper_paths = BTreeMap::<(u16, u16, Option<u16>), DataPathCandidate>::new();
        let mut bridge_paths = BTreeMap::<u16, Vec<DataPathCandidate>>::new();
        use super::provisioning::VlanProfile;
        let mut profiles = BTreeMap::<(u16, u16), u16>::new();
        for (&(class, id), entity) in &self.entities {
            if class != CLASS_EXTENDED_VLAN_TAGGING { continue; }
            let attr = |i| entity.attributes.get(&i).map(|a| a.value.as_slice()).unwrap();
            let class = match attr(1)[0] {
                0 => CLASS_MAC_BRIDGE_PORT_CONFIG_DATA,
                1 => CLASS_IEEE_8021P_MAPPER,
                2 => CLASS_PPTP_ETHERNET_UNI,
                5 => CLASS_GEM_INTERWORKING_TP,
                6 => CLASS_MULTICAST_GEM_INTERWORKING_TP,
                10 => CLASS_VEIP,
                _ => continue,
            };
            profiles.insert((class, be_u16(attr(7)).unwrap()), id);
            snapshot.vlan_profiles.push(VlanProfile {
                entity_id: id, input_tpid: be_u16(attr(3)).unwrap(),
                output_tpid: be_u16(attr(4)).unwrap(), downstream_mode: attr(5)[0],
                dscp: attr(8).try_into().unwrap(),
                rows: entity.attributes[&6].table_rows.values()
                    .map(|row| row.as_slice().try_into().unwrap()).collect(),
            });
        }
        let profile = |class, id| profiles.get(&(class, id)).copied();
        let mut uni_profiles = BTreeMap::<u16, [Option<u16>; 2]>::new();
        for (&(class, id), entity) in &self.entities {
            if class != CLASS_MAC_BRIDGE_PORT_CONFIG_DATA { continue; }
            let attr = |i| entity.attributes.get(&i).map(|a| a.value.as_slice()).unwrap();
            let tp_class = match attr(3)[0] { 1 => CLASS_PPTP_ETHERNET_UNI, 11 => CLASS_VEIP, _ => continue };
            uni_profiles.insert(be_u16(attr(1)).unwrap(),
                [profile(tp_class, be_u16(attr(4)).unwrap()), profile(class, id)]);
        }

        if let Some(olt) = self.entities.get(&(CLASS_OLT_G, 0)) {
            snapshot.olt_vendor_id = olt
                .attributes
                .get(&1)
                .map(|attribute| omci_text(&attribute.value))
                .unwrap_or_default();
            snapshot.olt_equipment_id = olt
                .attributes
                .get(&2)
                .map(|attribute| omci_text(&attribute.value))
                .unwrap_or_default();
            snapshot.olt_version = olt
                .attributes
                .get(&3)
                .map(|attribute| omci_text(&attribute.value))
                .unwrap_or_default();
        }

        if let Some(security) = self.entities.get(&(CLASS_ENHANCED_SECURITY_CONTROL, 0)) {
            snapshot.enhanced_security = true;
            /* Row identifier bits 7..6 carry the key index; bits 3..0 the fragment number. */
            snapshot.broadcast_key_indexes = security
                .attributes
                .get(&11)
                .map(|table| table.table_rows.keys().map(|row| row[0] >> 6).collect())
                .unwrap_or_default();
        }

        for (&(class_id, entity_id), entity) in &self.entities {
            if class_id != CLASS_TCONT {
                continue;
            }
            let Some(alloc) = entity
                .attributes
                .get(&1)
                .and_then(|attr| be_u16(&attr.value))
            else {
                continue;
            };
            if alloc != 0xffff {
                tcont_alloc.insert(entity_id, alloc);
            }
        }
        snapshot.configured_tconts = tcont_alloc.len();

        /* The GEM CTP entity ID is the pointer stored by 802.1p mapper and GEM IWTP entities. */
        for (&(class_id, entity_id), entity) in &self.entities {
            if class_id != CLASS_GEM_PORT_NETWORK_CTP {
                continue;
            }
            let gem_id = entity
                .attributes
                .get(&1)
                .and_then(|attr| be_u16(&attr.value));
            let tcont = entity
                .attributes
                .get(&2)
                .and_then(|attr| be_u16(&attr.value));
            let direction = entity
                .attributes
                .get(&3)
                .and_then(|attr| attr.value.first())
                .copied();
            if let Some(gem_id) = gem_id {
                gem_id_by_entity.insert(entity_id, gem_id);
            }
            if let (Some(gem_id), Some(direction @ 1..=3)) = (gem_id, direction) {
                let alloc_id = if direction & 1 != 0 {
                    tcont.and_then(|id| tcont_alloc.get(&id).copied())
                } else {
                    Some(u16::MAX)
                };
                if let Some(alloc_id) = alloc_id {
                    unicast_by_entity.insert(
                        entity_id,
                        DataPathCandidate {
                            alloc_id,
                            gem_id,
                            vlan_id: DATA_PATH_VLAN_ANY,
                            pbit_mask: if direction & 1 != 0 { u8::MAX } else { 0 },
                            downstream_pbits: if direction & 2 != 0 { u8::MAX } else { 0 },
                            multicast: false,
                            vlan_profiles: [None; 5],
                            vlan_exclude: 0, unmarked_mode: 0, default_priority: 0, mapper_dscp: [0; 24],
                        },
                    );
                }
            }
        }

        /*
         * Class 281 attribute 1 points to a GEM Port Network CTP. That GEM uses
         * multicast type and the downstream channel, deriving membership directly
         * from the CTP.
         */
        for (&(class_id, iwtp_id), iwtp) in &self.entities {
            if class_id != CLASS_MULTICAST_GEM_INTERWORKING_TP {
                continue;
            }
            let Some(ctp_entity) = iwtp.attributes.get(&1).and_then(|attr| be_u16(&attr.value))
            else {
                continue;
            };
            let Some(&gem_id) = gem_id_by_entity.get(&ctp_entity) else {
                continue;
            };
            multicast_by_iwtp.insert(
                iwtp_id,
                DataPathCandidate {
                    alloc_id: u16::MAX,
                    gem_id,
                    vlan_id: DATA_PATH_VLAN_ANY,
                    pbit_mask: u8::MAX,
                    downstream_pbits: u8::MAX,
                    multicast: true,
                    vlan_profiles: [None, None, None,
                        None, profile(CLASS_MULTICAST_GEM_INTERWORKING_TP, iwtp_id)],
                    vlan_exclude: 0, unmarked_mode: 0, default_priority: 0, mapper_dscp: [0; 24],
                },
            );
        }

        /*
         * 802.1p mapper attributes 2..9 point to GEM IWTPs for P-bits 0..7.
         * A direct GEM CTP entity ID is also a valid mapping form.
         */
        for (&(class_id, mapper_id), mapper) in &self.entities {
            if class_id != CLASS_IEEE_8021P_MAPPER {
                continue;
            }
            for pbit in 0..8u8 {
                let Some(pointer) = mapper
                    .attributes
                    .get(&(pbit + 2))
                    .and_then(|attr| be_u16(&attr.value))
                else {
                    continue;
                };
                if pointer == 0xffff {
                    continue;
                }
                let ctp_entity = self
                    .entities
                    .get(&(CLASS_GEM_INTERWORKING_TP, pointer))
                    .and_then(|entity| entity.attributes.get(&1))
                    .and_then(|attr| be_u16(&attr.value))
                    .unwrap_or(pointer);
                if let Some(path) = unicast_by_entity.get(&ctp_entity) {
                    let iwtp_profile = profile(CLASS_GEM_INTERWORKING_TP, pointer);
                    let mapped = mapper_paths
                        .entry((mapper_id, path.gem_id, iwtp_profile))
                        .or_insert_with(|| {
                            let mut mapped = path.clone();
                            mapped.pbit_mask = 0;
                            mapped.vlan_profiles[3] = profile(CLASS_IEEE_8021P_MAPPER, mapper_id);
                            mapped.vlan_profiles[4] = iwtp_profile;
                            mapped.unmarked_mode = if mapper.attributes[&10].value[0] == 0 { 1 } else { 2 };
                            mapped.default_priority = mapper.attributes[&12].value[0] & 7;
                            mapped.mapper_dscp = mapper.attributes[&11].value.as_slice().try_into().unwrap();
                            mapped
                        });
                    mapped.pbit_mask |= path.pbit_mask & (1 << pbit);
                }
            }
            if mapper.attributes[&13].value[0] == 7
                && be_u16(&mapper.attributes[&1].value) == Some(VEIP_ENTITY) {
                snapshot.data_paths.extend(mapper_paths.iter()
                    .filter(|((id, _, _), _)| *id == mapper_id)
                    .map(|(_, path)| {
                        let mut direct = path.clone();
                        direct.vlan_profiles[0] = profile(CLASS_VEIP, VEIP_ENTITY);
                        direct
                    }));
            }
        }

        /*
         * MAC bridge TP type 3 resolves through an 802.1p mapper, type 5 through
         * GEM IWTP, and type 6 through Multicast GEM IWTP. Each path ends at a GEM CTP.
         */
        for (&(class_id, entity_id), bridge) in &self.entities {
            if class_id != CLASS_MAC_BRIDGE_PORT_CONFIG_DATA {
                continue;
            }
            let tp_type = bridge
                .attributes
                .get(&3)
                .and_then(|attr| attr.value.first())
                .copied();
            let pointer = bridge
                .attributes
                .get(&4)
                .and_then(|attr| be_u16(&attr.value));
            let Some(pointer) = pointer else {
                continue;
            };

            let mut paths = match tp_type {
                Some(3) => mapper_paths
                    .iter()
                    .filter(|((mapper_id, _, _), _)| *mapper_id == pointer)
                    .map(|(_, path)| path.clone())
                    .collect::<Vec<_>>(),
                Some(5) => self
                    .entities
                    .get(&(CLASS_GEM_INTERWORKING_TP, pointer))
                    .and_then(|entity| entity.attributes.get(&1))
                    .and_then(|attr| be_u16(&attr.value))
                    .and_then(|ctp_entity| unicast_by_entity.get(&ctp_entity))
                    .cloned()
                    .into_iter()
                    .collect::<Vec<_>>(),
                Some(6) => multicast_by_iwtp
                    .get(&pointer)
                    .cloned()
                    .into_iter()
                    .collect::<Vec<_>>(),
                _ => Vec::new(),
            };
            let bridge = bridge.attributes.get(&1).and_then(|a| be_u16(&a.value)).unwrap();
            let uni = uni_profiles.get(&bridge).copied().unwrap_or([
                profile(CLASS_VEIP, VEIP_ENTITY), None]);
            for path in &mut paths {
                path.vlan_profiles[..2].copy_from_slice(&uni);
                path.vlan_profiles[2] = profile(CLASS_MAC_BRIDGE_PORT_CONFIG_DATA, entity_id);
                if tp_type == Some(5) {
                    path.vlan_profiles[4] = profile(CLASS_GEM_INTERWORKING_TP, pointer);
                }
            }
            if !paths.is_empty() {
                bridge_paths.insert(entity_id, paths);
            }
        }

        let bridged_multicast = bridge_paths.values().flatten()
            .filter(|path| path.multicast).map(|path| path.gem_id).collect::<BTreeSet<_>>();
        /* A VLAN filter and the same-entity ANI bridge port select the GEM path together. */
        for (entity_id, paths) in bridge_paths {
            let Some(filter) = self.entities.get(&(CLASS_VLAN_TAGGING_FILTER, entity_id)) else {
                snapshot.data_paths.extend(paths);
                continue;
            };
            let operation = filter
                .attributes
                .get(&2)
                .and_then(|attr| attr.value.first())
                .copied()
                .unwrap_or(0);
            /* The original VLAN rule generator normalizes Forward Operation first. */
            let mode = match operation {
                0x00 | 0x05 | 0x09 | 0x0d => 0,
                0x01 => 1,
                0x02 | 0x06 | 0x0a | 0x0e => 2,
                0x03 | 0x0f | 0x1c => 3,
                0x07 | 0x11 | 0x1e => 4,
                0x0b | 0x13 | 0x20 => 5,
                0x04 | 0x10 | 0x1d => 6,
                0x08 | 0x12 | 0x1f => 7,
                0x0c | 0x14 | 0x21 => 8,
                _ => continue,
            };
            if mode <= 2 {
                snapshot.data_paths.extend(paths.into_iter().map(|mut path| {
                    path.vlan_id = match mode {
                        1 => DATA_PATH_VLAN_UNTAGGED,
                        2 => DATA_PATH_VLAN_TAGGED,
                        _ => DATA_PATH_VLAN_ANY,
                    };
                    path
                }));
                continue;
            }
            if mode <= 5 {
                snapshot.data_paths.extend(paths.iter().cloned().map(|mut path| {
                    path.vlan_id = DATA_PATH_VLAN_UNTAGGED;
                    path
                }));
            }
            let count = filter
                .attributes
                .get(&3)
                .and_then(|attr| attr.value.first())
                .copied()
                .unwrap_or(0)
                .min(12) as usize;
            let Some(list) = filter.attributes.get(&1) else {
                continue;
            };
            for entry in list.value.chunks_exact(2).take(count) {
                let tci = u16::from_be_bytes([entry[0], entry[1]]);
                for path in &paths {
                    let mut path = path.clone();
                    if matches!(mode, 3 | 5 | 6 | 8) {
                        path.vlan_id = tci & 0x0fff;
                        if path.vlan_id == 4095 { continue; }
                    } else {
                        path.vlan_id = DATA_PATH_VLAN_TAGGED;
                    }
                    if matches!(mode, 4 | 5 | 7 | 8) {
                        path.vlan_exclude |= !(1 << ((tci >> 13) & 7));
                        path.downstream_pbits &= 1 << ((tci >> 13) & 7);
                    }
                    snapshot.data_paths.push(path);
                }
            }
        }

        /*
         * Class 281 activates its downstream GEM at creation time. Existing bridge
         * paths preserve concrete VIDs; DATA_PATH_VLAN_ANY represents GEM-wide delivery.
         */
        for path in multicast_by_iwtp.into_values() {
            if !bridged_multicast.contains(&path.gem_id) && !snapshot
                .data_paths
                .iter()
                .any(|current| current.multicast && current.gem_id == path.gem_id)
            {
                snapshot.data_paths.push(path);
            }
        }

        for (&(class_id, _), entity) in &self.entities {
            match class_id {
                CLASS_GEM_PORT_NETWORK_CTP => {
                    snapshot.gem_ports += 1;
                }
                CLASS_GEM_INTERWORKING_TP | CLASS_MULTICAST_GEM_INTERWORKING_TP => {
                    snapshot.gem_interworking_tps += 1;
                }
                CLASS_EXTENDED_VLAN_TAGGING => {
                    if let Some(table) = entity.attributes.get(&6) {
                        snapshot.vlan_rules += table.table_rows.len();
                        for row in table.table_rows.values() {
                            collect_extended_vlan_ids(row, &mut snapshot.vlan_ids);
                        }
                    }
                }
                CLASS_VLAN_TAGGING_FILTER => {
                    let count = entity
                        .attributes
                        .get(&3)
                        .and_then(|attr| attr.value.first())
                        .copied()
                        .unwrap_or(0)
                        .min(12) as usize;
                    snapshot.vlan_rules += count;
                }
                _ => {}
            }
        }
        snapshot.data_paths.retain(|path| path.downstream_pbits != 0 ||
            (if path.vlan_profiles[2].is_none() { path.pbit_mask & !path.vlan_exclude != 0 }
             else { path.pbit_mask != 0 && path.vlan_exclude != 255 }));
        snapshot.data_paths.sort_by_key(|path| {
            (
                path.multicast,
                path.vlan_id,
                path.alloc_id,
                path.gem_id,
                path.pbit_mask,
                path.downstream_pbits,
            )
        });
        snapshot.data_paths.dedup();
        /*
         * A VLAN Tagging Filter gains its role from the associated MAC bridge port.
         * The resolved GEM or Class 281 reference classifies unicast and multicast VLANs.
         */
        for path in &snapshot.data_paths {
            if !(1..=4094).contains(&path.vlan_id) {
                continue;
            }
            if path.multicast {
                snapshot.multicast_vlan_ids.insert(path.vlan_id);
            } else {
                snapshot.vlan_ids.insert(path.vlan_id);
            }
        }

        /*
         * Class 309 becomes active for a UNI through a Class 310 reference. ACL rows
         * describe downstream GEM, VLAN, and group ranges; upstream TCI and tag control
         * describe Report/Leave tagging on the unicast GEM.
         */
        for (&(class_id, _), subscriber) in &self.entities {
            if class_id != CLASS_MULTICAST_SUBSCRIBER_CONFIG {
                continue;
            }
            let Some(profile_id) = subscriber
                .attributes
                .get(&2)
                .and_then(|attr| be_u16(&attr.value))
            else {
                continue;
            };
            let Some(profile) = self
                .entities
                .get(&(CLASS_MULTICAST_OPERATIONS_PROFILE, profile_id))
            else {
                continue;
            };
            for index in [7u8, 8] {
                let Some(table) = profile.attributes.get(&index) else {
                    continue;
                };
                for row in table.table_rows.values() {
                    collect_multicast_acl_vlan_id(row, &mut snapshot.multicast_vlan_ids);
                }
            }
            let Some(tag_control) = profile
                .attributes
                .get(&5)
                .and_then(|attr| attr.value.first())
                .copied()
            else {
                continue;
            };
            if tag_control > 3 {
                continue;
            }
            snapshot.igmp_upstream_tag_controls.insert(tag_control);
            if tag_control == 0 {
                continue;
            }
            let Some(tci) = profile
                .attributes
                .get(&4)
                .and_then(|attr| be_u16(&attr.value))
            else {
                continue;
            };
            let vlan_id = tci & 0x0fff;
            if (1..=4094).contains(&vlan_id) {
                snapshot.igmp_upstream_vlan_ids.insert(vlan_id);
            }
        }
        snapshot
    }

    pub fn update_queue_bindings(&mut self, request: &Request<'_>) -> Result<(), i32> {
        let owner = |id| -> Result<u16, i32> {
            let queue = self.entities.get(&(CLASS_PRIORITY_QUEUE, id)).ok_or(-22)?;
            let related = &queue.attributes[&6].value;
            let port = u16::from_be_bytes([related[0], related[1]]);
            let scheduler_id = be_u16(&queue.attributes[&7].value).unwrap();
            if let Some(scheduler) = self.entities.get(&(CLASS_TRAFFIC_SCHEDULER, scheduler_id)) {
                if scheduler.attributes[&3].value[0] == 2 {
                    return Ok(be_u16(&scheduler.attributes[&1].value).unwrap());
                }
            }
            Ok(port)
        };
        // A live queue Set updates every flow using that queue. A later CTP Set
        // establishes its own T-CONT again, as in the original cfgGemFlow path.
        for (_, (queue, port)) in &mut self.gem_queue_bindings {
            if (request.class_id == CLASS_PRIORITY_QUEUE && request.entity_id == *queue)
                || (request.class_id == CLASS_TRAFFIC_SCHEDULER
                    && self.entities[&(CLASS_PRIORITY_QUEUE, *queue)].attributes[&7].value
                        == request.entity_id.to_be_bytes()) {
                *port = owner(*queue)?;
            }
        }
        let mut desired = BTreeMap::new();
        for path in self.provisioning_snapshot().data_paths {
            if path.multicast || path.pbit_mask == 0 { continue; }
            if let Some((&(_, id), ctp)) = self.entities.iter().find(|(&(class, _), entity)|
                class == CLASS_GEM_PORT_NETWORK_CTP && be_u16(&entity.attributes[&1].value) == Some(path.gem_id)) {
                desired.insert(id, (be_u16(&ctp.attributes[&4].value).unwrap(),
                                    be_u16(&ctp.attributes[&2].value).unwrap()));
            }
        }
        self.gem_queue_bindings.retain(|id, _| desired.contains_key(id));
        for (id, (requested, port)) in desired {
            if self.gem_queue_bindings.contains_key(&id)
                && !(request.class_id == CLASS_GEM_PORT_NETWORK_CTP && request.entity_id == id) {
                continue;
            }
            let queue = if owner(requested)? == port { requested } else {
                // Original 0x1e8b8..0x1e8ec selects the first active queue on
                // the CTP T-CONT; with no match it retains the requested queue.
                self.configured_priority_queues.iter().copied()
                    .find(|&queue| owner(queue) == Ok(port)).unwrap_or(requested)
            };
            self.gem_queue_bindings.insert(id, (queue, port));
            self.configured_priority_queues.insert(queue);
        }
        Ok(())
    }

    /// Export OMCI entities that determine GEM classification in MIB wire byte order.
    pub fn data_path_graph(&self) -> DataPathGraph {
        const GRAPH_CLASSES: [u16; 13] = [
            CLASS_MAC_BRIDGE_PORT_CONFIG_DATA,
            CLASS_VLAN_TAGGING_FILTER,
            CLASS_IEEE_8021P_MAPPER,
            CLASS_EXTENDED_VLAN_TAGGING,
            CLASS_GEM_INTERWORKING_TP,
            CLASS_MULTICAST_GEM_INTERWORKING_TP,
            CLASS_MULTICAST_OPERATIONS_PROFILE,
            CLASS_MULTICAST_SUBSCRIBER_CONFIG,
            CLASS_GEM_PORT_NETWORK_CTP,
            CLASS_TRAFFIC_DESCRIPTOR,
            CLASS_PRIORITY_QUEUE,
            CLASS_TRAFFIC_SCHEDULER,
            CLASS_TCONT,
        ];

        let mut graph = DataPathGraph {
            candidates: self.provisioning_snapshot().data_paths,
            configured_queues: self.configured_priority_queues.clone(),
            gem_queue_bindings: self.gem_queue_bindings.clone(),
            entities: Vec::new(),
        };
        for (&(class_id, entity_id), entity) in &self.entities {
            if !GRAPH_CLASSES.contains(&class_id) {
                continue;
            }
            let mut attributes = Vec::with_capacity(entity.attributes.len());
            for (&index, attribute) in &entity.attributes {
                let table_rows = attribute
                    .table_rows
                    .iter()
                    .map(|(key, value)| DataPathTableRow {
                        key: key.clone(),
                        value: value.clone(),
                    })
                    .collect();
                attributes.push(DataPathAttribute {
                    index,
                    value: attribute.value.clone(),
                    table_rows,
                });
            }
            graph.entities.push(DataPathEntity {
                class_id,
                entity_id,
                attributes,
            });
        }
        graph
    }
}

fn be_u16(value: &[u8]) -> Option<u16> {
    Some(u16::from_be_bytes([*value.first()?, *value.get(1)?]))
}

fn omci_text(value: &[u8]) -> String {
    let end = value
        .iter()
        .position(|byte| *byte == 0 || *byte == 0xff)
        .unwrap_or(value.len());
    String::from_utf8_lossy(&value[..end]).trim_end().to_owned()
}

fn valid_extended_vlan_row(row: &[u8]) -> bool {
    if row.len() != 16 { return false; }
    if row[8..].iter().all(|byte| *byte == 0xff) { return true; }
    let word = |offset| u32::from_be_bytes(row[offset..offset + 4].try_into().unwrap());
    let input = if row[4] >> 4 == 15 { 0 } else if row[0] >> 4 == 15 { 1 } else { 2 };
    if input == 0 && row[0] >> 4 != 15 { return false; }
    if word(4) & 15 > 5 || (word(4) >> 4) & 255 > 2 { return false; }
    for i in 0..input {
        let filter = word(if input == 1 { 4 } else { i * 4 });
        let priority = filter >> 28;
        let vid = (filter >> 15) & 8191;
        let tpid = (filter >> 12) & 7;
        if (priority > 8 && priority != 14) || vid == 4095 || vid > 4096 || (tpid != 0 && tpid < 4) { return false; }
    }
    let remove = row[8] >> 6;
    if remove == 3 { return true; }
    if remove as usize > input || (row[9] & 15 != 15 && row[13] & 15 == 15) { return false; }
    for offset in [8, 12] {
        let treatment = word(offset);
        let priority = (treatment >> 16) & 15;
        if priority == 15 { continue; }
        let vid = (treatment >> 3) & 8191;
        let tpid = treatment & 7;
        if priority > 10 || vid == 4095 || vid > 4097 || tpid == 5
            || (input == 0 && (priority == 8 || vid == 4096 || tpid == 0 || tpid == 2))
            || (input < 2 && (priority == 9 || vid == 4097 || tpid == 1 || tpid == 3)) { return false; }
    }
    true
}

fn collect_extended_vlan_ids(row: &[u8], output: &mut BTreeSet<u16>) {
    if row.len() != 16 {
        return;
    }
    for offset in [0usize, 4, 8, 12] {
        let word = u32::from_be_bytes([
            row[offset],
            row[offset + 1],
            row[offset + 2],
            row[offset + 3],
        ]);
        let vid = ((word >> 3) & 0x1fff) as u16;
        // Filter fields place VID in bits 27..15; treatment fields use bits 15..3.
        // Values 4096 and 4097 encode copy and don't-care operations.
        let vid = if offset < 8 {
            ((word >> 15) & 0x1fff) as u16
        } else {
            vid
        };
        if (1..=4094).contains(&vid) {
            output.insert(vid);
        }
    }
}

fn collect_multicast_acl_vlan_id(row: &[u8], output: &mut BTreeSet<u16>) {
    if row.len() != 24 {
        return;
    }
    /*
     * G.988 Class 309 static and dynamic ACL rows carry the two-byte VLAN TCI
     * at offset 4. The low 12 bits contain VID, with 0 and 4095 as reserved values.
     */
    let vlan_id = u16::from_be_bytes([row[4], row[5]]) & 0x0fff;
    if (1..=4094).contains(&vlan_id) {
        output.insert(vlan_id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_mib() -> Mib {
        Mib {
            entities: BTreeMap::new(),
            autonomous_defaults: BTreeMap::new(),
            known_classes: BTreeSet::new(),
            olt_created: BTreeSet::new(),
            configured_priority_queues: BTreeSet::new(),
            gem_queue_bindings: BTreeMap::new(),
            dynamic_classes: BTreeSet::new(),
            table_snapshot: None,
            upload_snapshot: Vec::new(),
            onu_serial: [0; 8],
            pending_master_session_key: None,
            attribute_value_changes: Vec::new(),
            enhanced_security: true,
            psk: Some([1; 16]),
        }
    }

    #[test]
    fn create_schema_preserves_wire_offsets_and_access() {
        let payload = (0u8..32).collect::<Vec<_>>();
        let bridge = Mib::dynamic_entity(CLASS_MAC_BRIDGE_SERVICE_PROFILE, &payload).unwrap();
        assert_eq!(bridge.attributes[&4].value, payload[3..5]);
        assert!(bridge.attributes[&4].writable);

        let gem = Mib::dynamic_entity(CLASS_GEM_PORT_NETWORK_CTP, &payload).unwrap();
        assert_eq!(gem.attributes[&9].value, payload[11..13]);
        assert!(!gem.attributes[&8].writable);

        let descriptor = Mib::dynamic_entity(CLASS_TRAFFIC_DESCRIPTOR, &payload).unwrap();
        assert_eq!(descriptor.attributes[&8].value, payload[19..20]);
        assert!(!descriptor.attributes[&8].writable);
    }

    #[test]
    fn create_schema_requires_its_complete_payload() {
        for definition in schema::MANAGED_ENTITIES
            .iter()
            .filter(|definition| definition.origin == ManagedEntityOrigin::Olt)
        {
            let length = definition
                .attributes
                .iter()
                .filter_map(|attribute| match (attribute.create, attribute.kind) {
                    (CreateSource::Required { offset }, AttributeKind::Scalar { length }) => {
                        Some(offset + length)
                    }
                    _ => None,
                })
                .max()
                .expect("OLT-created ME has a required create field");
            assert!(Mib::dynamic_entity(definition.class_id, &vec![0; length]).is_some());
            assert!(Mib::dynamic_entity(definition.class_id, &vec![0; length - 1]).is_none());
        }
    }

    #[test]
    fn pm_schema_keeps_threshold_state_and_runtime_counters_separate() {
        let mut mib = Mib::from_identity(&IdentityConfig::default());
        let payload = 0x1234u16.to_be_bytes();
        let create = Request {
            encoding: super::super::protocol::Encoding::Baseline,
            tci: 1,
            message_type: 0x44,
            action: ACTION_CREATE,
            class_id: CLASS_ETHERNET_PM_HISTORY_DATA,
            entity_id: 1,
            attribute_mask: 0,
            payload: &payload,
            content: &payload,
        };

        assert_eq!(mib.dispatch(&create).result(), Some(RESULT_SUCCESS));
        let entity = &mib.entities[&(CLASS_ETHERNET_PM_HISTORY_DATA, 1)];
        assert_eq!(entity.attributes[&2].value, payload);
        assert_eq!(entity.attributes[&3].value, vec![0; 4]);
        assert!(!entity.attributes[&3].writable);
        assert!(!entity.attributes[&3].upload);
    }

    #[test]
    fn duplicate_create_is_idempotent_when_the_instance_matches() {
        let mut mib = Mib::from_identity(&IdentityConfig::default());
        let payload = 0x1234u16.to_be_bytes();
        let create = Request {
            encoding: super::super::protocol::Encoding::Baseline,
            tci: 1,
            message_type: 0x44,
            action: ACTION_CREATE,
            class_id: CLASS_ETHERNET_PM_HISTORY_DATA,
            entity_id: 1,
            attribute_mask: 0,
            payload: &payload,
            content: &payload,
        };

        assert_eq!(mib.dispatch(&create).result(), Some(RESULT_SUCCESS));
        assert_eq!(mib.dispatch(&create).result(), Some(RESULT_SUCCESS));

        let changed_payload = 0x5678u16.to_be_bytes();
        let changed = Request {
            payload: &changed_payload,
            content: &changed_payload,
            ..create
        };
        assert_eq!(
            mib.dispatch(&changed).result(),
            Some(RESULT_INSTANCE_EXISTS)
        );
        assert_eq!(
            mib.entities[&(CLASS_ETHERNET_PM_HISTORY_DATA, 1)].attributes[&2].value,
            payload
        );
    }

    #[test]
    fn compatibility_profile_disables_enhanced_security() {
        let identity = IdentityConfig {
            omcc_version: 0x86,
            disable_enhanced_security: true,
            ..IdentityConfig::default()
        };
        let mut mib = Mib::from_identity(&identity);

        assert_eq!(
            mib.entities[&(CLASS_ONU2_G, 0)].attributes[&2].value,
            [0x86]
        );
        assert!(!mib
            .entities
            .contains_key(&(CLASS_ENHANCED_SECURITY_CONTROL, 0)));
        assert!(!mib.entities[&(CLASS_OMCI, 0)].attributes[&1]
            .table_rows
            .contains_key(&CLASS_ENHANCED_SECURITY_CONTROL.to_be_bytes().to_vec()));

        let content = [1u8];
        let set = Request {
            encoding: super::super::protocol::Encoding::Baseline,
            tci: 1,
            message_type: 0x48,
            action: ACTION_SET,
            class_id: CLASS_ENHANCED_SECURITY_CONTROL,
            entity_id: 0,
            attribute_mask: 0x8000,
            payload: &content,
            content: &content,
        };
        assert_eq!(mib.dispatch(&set).result(), Some(RESULT_UNKNOWN_ME));
    }

    #[test]
    fn huawei_pm_creates_are_accepted() {
        let mut mib = Mib::from_identity(&IdentityConfig::default());
        /* Payloads captured from a Huawei XGS-PON OLT: PPTP 1 downstream control block and threshold data 4. */
        let mut control_block = [0u8; 32];
        control_block[..12].copy_from_slice(&[0, 0x0b, 0, 0x0b, 0, 0x01, 0, 0, 0, 0, 0, 0x02]);
        let mut threshold = [0u8; 32];
        threshold[1] = 0x04;

        let class_rows = &mib.entities[&(CLASS_OMCI, 0)].attributes[&1].table_rows;
        assert!(
            class_rows.contains_key(&CLASS_MAC_BRIDGE_PORT_PM_HISTORY_DATA.to_be_bytes().to_vec())
        );
        assert!(class_rows.contains_key(&CLASS_ETHERNET_FRAME_EXTENDED_PM.to_be_bytes().to_vec()));
        assert!(!class_rows.contains_key(&CLASS_VENDOR_351.to_be_bytes().to_vec()));

        for (class_id, payload) in [
            (CLASS_ETHERNET_FRAME_EXTENDED_PM, &control_block),
            (CLASS_MAC_BRIDGE_PORT_PM_HISTORY_DATA, &threshold),
            (CLASS_VENDOR_351, &threshold),
        ] {
            let create = Request {
                encoding: super::super::protocol::Encoding::Baseline,
                tci: 1,
                message_type: 0x44,
                action: ACTION_CREATE,
                class_id,
                entity_id: 2,
                attribute_mask: 0,
                payload,
                content: payload,
            };
            assert_eq!(mib.dispatch(&create).result(), Some(RESULT_SUCCESS));
            let get = Request {
                message_type: 0x49,
                action: ACTION_GET,
                attribute_mask: 0xc000,
                ..create
            };
            assert_eq!(mib.dispatch(&get).result(), Some(RESULT_SUCCESS));
            let delete = Request {
                message_type: 0x46,
                action: ACTION_DELETE,
                ..create
            };
            assert_eq!(mib.dispatch(&delete).result(), Some(RESULT_SUCCESS));
        }

        let entity = entity_from_definition(CLASS_ETHERNET_FRAME_EXTENDED_PM, Some(&control_block))
            .expect("extended PM schema is registered");
        assert_eq!(entity.attributes[&2].value, control_block[..16]);
        assert!(!entity.attributes[&16].upload);
    }

    #[test]
    fn enhanced_security_accepts_broadcast_key_rows() {
        let mut mib = Mib::from_identity(&IdentityConfig::default());
        let set = |mib: &mut Mib, row: &[u8]| {
            mib.dispatch(&Request {
                encoding: super::super::protocol::Encoding::Baseline,
                tci: 1,
                message_type: 0x48,
                action: ACTION_SET,
                class_id: CLASS_ENHANCED_SECURITY_CONTROL,
                entity_id: 0,
                attribute_mask: 0x0020,
                payload: row,
                content: row,
            })
            .result()
        };
        let rows = |mib: &Mib| {
            mib.entities[&(CLASS_ENHANCED_SECURITY_CONTROL, 0)].attributes[&11]
                .table_rows
                .len()
        };

        /* Key indexes 1 and 2, fragment 0, as distributed by a Huawei XGS-PON OLT. */
        let mut key1 = vec![0x00, 0x40];
        key1.extend_from_slice(&[0x50; 16]);
        let mut key2 = vec![0x00, 0x80];
        key2.extend_from_slice(&[0xf5; 16]);
        assert_eq!(set(&mut mib, &key1), Some(RESULT_SUCCESS));
        assert_eq!(set(&mut mib, &key2), Some(RESULT_SUCCESS));
        assert_eq!(rows(&mib), 2);
        let snapshot = mib.provisioning_snapshot();
        assert!(snapshot.enhanced_security);
        assert_eq!(
            snapshot
                .broadcast_key_indexes
                .into_iter()
                .collect::<Vec<_>>(),
            [1, 2]
        );

        let mut clear_row = vec![0x01, 0x40];
        clear_row.extend_from_slice(&[0; 16]);
        assert_eq!(set(&mut mib, &clear_row), Some(RESULT_SUCCESS));
        assert_eq!(rows(&mib), 1);

        let mut clear_table = vec![0x02, 0x00];
        clear_table.extend_from_slice(&[0; 16]);
        assert_eq!(set(&mut mib, &clear_table), Some(RESULT_SUCCESS));
        assert_eq!(rows(&mib), 0);

        let mut reserved = vec![0x03, 0x40];
        reserved.extend_from_slice(&[0; 16]);
        assert_eq!(set(&mut mib, &reserved), Some(RESULT_ATTRIBUTE_FAILED));
    }

    #[test]
    fn omci_capability_table_tracks_the_registered_schema() {
        let mib = Mib::from_identity(&IdentityConfig::default());
        let entity = &mib.entities[&(CLASS_OMCI, 0)];
        let class_rows = &entity.attributes[&1].table_rows;
        let message_rows = &entity.attributes[&2].table_rows;

        for class_id in [
            CLASS_ETHERNET_PM_HISTORY_DATA,
            CLASS_GEM_PORT_PM_HISTORY_DATA,
            CLASS_FEC_PM_HISTORY_DATA,
            CLASS_THRESHOLD_DATA_1,
            CLASS_THRESHOLD_DATA_2,
        ] {
            assert!(class_rows.contains_key(&class_id.to_be_bytes().to_vec()));
        }
        assert!(message_rows.contains_key(&vec![ACTION_GET_CURRENT_DATA]));
    }

    #[test]
    fn multicast_iwtp_uses_downstream_gem_and_bridge_vlan() {
        let mut mib = empty_mib();

        /* The Class 281 reference assigns multicast type to its downstream GEM. */
        mib.insert(
            CLASS_GEM_PORT_NETWORK_CTP,
            0x120,
            ManagedEntity::default()
                .read_write(1, 4000u16.to_be_bytes().to_vec())
                .read_write(2, 0xffffu16.to_be_bytes().to_vec())
                .read_write(3, vec![2]),
        );
        mib.insert(
            CLASS_MULTICAST_GEM_INTERWORKING_TP,
            0x220,
            ManagedEntity::default().read_write(1, 0x120u16.to_be_bytes().to_vec()),
        );
        mib.insert(
            CLASS_MAC_BRIDGE_PORT_CONFIG_DATA,
            0x320,
            ManagedEntity::default()
                .read_write(3, vec![6])
                .read_write(4, 0x220u16.to_be_bytes().to_vec()),
        );
        let mut vlan_list = vec![0; 24];
        vlan_list[..2].copy_from_slice(&85u16.to_be_bytes());
        mib.insert(
            CLASS_VLAN_TAGGING_FILTER,
            0x320,
            ManagedEntity::default()
                .read_write(1, vlan_list)
                .read_write(2, vec![0x03])
                .read_write(3, vec![1]),
        );

        assert_eq!(
            mib.provisioning_snapshot().data_paths,
            vec![DataPathCandidate {
                alloc_id: u16::MAX,
                gem_id: 4000,
                vlan_id: 85,
                pbit_mask: u8::MAX,
                downstream_pbits: u8::MAX,
                multicast: true,
            }, DataPathCandidate {
                alloc_id: u16::MAX,
                gem_id: 4000,
                vlan_id: DATA_PATH_VLAN_UNTAGGED,
                pbit_mask: u8::MAX,
                downstream_pbits: u8::MAX,
                multicast: true,
            }]
        );
        let snapshot = mib.provisioning_snapshot();
        assert!(snapshot.vlan_ids.is_empty());
        assert_eq!(snapshot.multicast_vlan_ids, BTreeSet::from([85]));
    }

    #[test]
    fn subscriber_profile_reports_igmp_upstream_vlan() {
        let mut mib = empty_mib();
        let mut profile_payload = vec![0; 26];

        /* Class 309 attribute 4 starts at offset 3 and attribute 5 follows it. */
        profile_payload[3..5].copy_from_slice(&85u16.to_be_bytes());
        profile_payload[5] = 1; // add TCI
        mib.insert(
            CLASS_MULTICAST_OPERATIONS_PROFILE,
            0x400,
            Mib::dynamic_entity(CLASS_MULTICAST_OPERATIONS_PROFILE, &profile_payload)
                .expect("valid multicast profile"),
        );

        let mut subscriber_payload = vec![0; 10];
        subscriber_payload[1..3].copy_from_slice(&0x400u16.to_be_bytes());
        mib.insert(
            CLASS_MULTICAST_SUBSCRIBER_CONFIG,
            0x100,
            Mib::dynamic_entity(CLASS_MULTICAST_SUBSCRIBER_CONFIG, &subscriber_payload)
                .expect("valid subscriber config"),
        );

        let snapshot = mib.provisioning_snapshot();
        assert_eq!(snapshot.igmp_upstream_vlan_ids, BTreeSet::from([85]));
        assert_eq!(snapshot.igmp_upstream_tag_controls, BTreeSet::from([1]));
    }

    #[test]
    fn subscriber_profile_reports_multicast_acl_vlan() {
        let mut mib = empty_mib();
        let profile_payload = vec![0; 26];
        let mut profile = Mib::dynamic_entity(CLASS_MULTICAST_OPERATIONS_PROFILE, &profile_payload)
            .expect("valid multicast profile");
        let mut acl = vec![0; 24];

        /* Class 309 attribute 7 carries GEM 0xfffe, VLAN 80, and the multicast address range. */
        acl[0..2].copy_from_slice(&0x5388u16.to_be_bytes());
        acl[2..4].copy_from_slice(&0xfffeu16.to_be_bytes());
        acl[4..6].copy_from_slice(&80u16.to_be_bytes());
        acl[10..14].copy_from_slice(&[224, 0, 1, 0]);
        acl[14..18].copy_from_slice(&[239, 255, 255, 255]);
        profile
            .attributes
            .get_mut(&7)
            .expect("static ACL table")
            .table_rows
            .insert(acl[..8].to_vec(), acl);
        mib.insert(CLASS_MULTICAST_OPERATIONS_PROFILE, 5, profile);

        let mut subscriber_payload = vec![0; 10];
        subscriber_payload[1..3].copy_from_slice(&5u16.to_be_bytes());
        mib.insert(
            CLASS_MULTICAST_SUBSCRIBER_CONFIG,
            5,
            Mib::dynamic_entity(CLASS_MULTICAST_SUBSCRIBER_CONFIG, &subscriber_payload)
                .expect("valid subscriber config"),
        );

        assert_eq!(
            mib.provisioning_snapshot().multicast_vlan_ids,
            BTreeSet::from([80])
        );
    }

    #[test]
    fn bridge_port_create_provides_filter_preassign_instance() {
        let mut mib = Mib::from_identity(&IdentityConfig::default());
        let bridge_port_payload = [0u8; 14];
        let create = Request {
            encoding: super::super::protocol::Encoding::Extended,
            tci: 1,
            message_type: 0x44,
            action: ACTION_CREATE,
            class_id: CLASS_MAC_BRIDGE_PORT_CONFIG_DATA,
            entity_id: 1,
            attribute_mask: 0,
            payload: &bridge_port_payload,
            content: &bridge_port_payload,
        };

        assert_eq!(mib.dispatch(&create).result(), Some(RESULT_SUCCESS));
        let filter = (CLASS_MAC_BRIDGE_PORT_FILTER_PREASSIGN_DATA, 1);
        assert!(mib.entities.contains_key(&filter));

        let value = [1u8];
        let set = Request {
            encoding: super::super::protocol::Encoding::Extended,
            tci: 2,
            message_type: 0x48,
            action: ACTION_SET,
            class_id: filter.0,
            entity_id: filter.1,
            attribute_mask: 0x8000,
            payload: &value,
            content: &value,
        };

        assert_eq!(mib.dispatch(&set).result(), Some(RESULT_SUCCESS));
        assert_eq!(mib.entities[&filter].attributes[&1].value, value);
    }
}
