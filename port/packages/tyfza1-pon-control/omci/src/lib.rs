// SPDX-License-Identifier: GPL-2.0-only
// Standard OMCI engine from PonWrt; RTL9617C integration is kept at this boundary.
#![allow(dead_code)]

mod config;
mod mib;
mod protocol;
mod provisioning;
mod schema;
mod security;
mod wire;

use std::collections::VecDeque;
use std::slice;
use config::IdentityConfig;
use mib::Mib;
use protocol::{Request, Response};

#[repr(C)]
pub struct Identity {
    serial: [u8; 8],
    equipment: [u8; 20],
    hardware: [u8; 14],
    software: [u8; 14],
    loid: [u8; 24],
    password: [u8; 12],
    operator: [u8; 4],
    psk: [u8; 16],
    has_psk: u8,
}

pub struct Agent {
    mib: Mib,
    cache: VecDeque<(Vec<u8>, Response)>,
    notifications: VecDeque<Vec<u8>>,
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_new(identity: &Identity) -> *mut Agent {
    let identity = IdentityConfig {
        omcc_version: 0xb0,
        disable_enhanced_security: false,
        vendor_id: identity.serial[..4].to_vec(),
        equipment_id: identity.equipment.to_vec(),
        hardware_version: identity.hardware.to_vec(),
        software_version: identity.software.to_vec(),
        loid: identity.loid.to_vec(),
        loid_password: identity.password.to_vec(),
        operator_id: identity.operator.to_vec(),
        serial_number: identity.serial.to_vec(),
        psk: (identity.has_psk != 0).then_some(identity.psk),
    };
    Box::into_raw(Box::new(Agent {
        mib: Mib::from_identity(&identity),
        cache: VecDeque::new(),
        notifications: VecDeque::new(),
    }))
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_free(agent: *mut Agent) {
    drop(Box::from_raw(agent));
}

#[no_mangle]
pub extern "C" fn tyfza1_omci_epoch(agent: &mut Agent) {
    agent.cache.clear();
    agent.notifications.clear();
    agent.mib.reset_security_session();
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_broadcast(agent: &Agent, data: *const u8, length: usize, row: *mut u8) -> i32 {
    let data = slice::from_raw_parts(data, length);
    // The daemon programs the key before dispatch; a cached reply must also skip that write.
    if agent.cache.iter().any(|(key, _)| key == data) { return 0; }
    let Ok(request) = Request::parse(data) else { return 0; };
    if request.action == protocol::ACTION_SET && request.class_id == schema::CLASS_ENHANCED_SECURITY_CONTROL
        && request.entity_id == 0 && request.attribute_mask == 0x0020 && request.content.len() >= 18 {
        std::ptr::copy_nonoverlapping(request.content.as_ptr(), row, 18);
        return 1;
    }
    0
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_dispatch(
    agent: &mut Agent, data: *const u8, length: usize, output: *mut u8, backend_result: i32,
) -> i32 {
    let data = slice::from_raw_parts(data, length);
    let request = match Request::parse(data) {
        Ok(request) => request,
        Err(_) => return -74,
    };
    if backend_result < 0 {
        let response = Response::new(&request, 1);
        let bytes = response.as_bytes();
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
        return bytes.len() as i32;
    }
    let response = if let Some((_, response)) = agent.cache.iter().find(|(key, _)| key == data) {
        response.clone()
    } else {
        let response = agent.mib.dispatch(&request);
        if request.action == protocol::ACTION_MIB_RESET {
            agent.cache.clear();
            agent.notifications.clear();
        }
        if agent.cache.len() == 32 { agent.cache.pop_front(); }
        agent.cache.push_back((data.to_vec(), response.clone()));
        response
    };
    let bytes = response.as_bytes();
    std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
    bytes.len() as i32
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_session_key(agent: &mut Agent, output: *mut u8) -> i32 {
    match agent.mib.take_pending_master_session_key() {
        Some(key) => { std::ptr::copy_nonoverlapping(key.as_ptr(), output, 16); 1 }
        None => 0,
    }
}

#[no_mangle]
pub extern "C" fn tyfza1_omci_key_complete(agent: &mut Agent, installed: bool) {
    agent.mib.complete_master_session_key_install(installed);
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_notification(agent: &mut Agent, output: *mut u8) -> i32 {
    for change in agent.mib.take_attribute_value_changes() {
        if let Ok(response) = Response::attribute_value_change(0, change.class_id,
            change.entity_id, change.attribute_mask, &change.value) {
            agent.notifications.push_back(response.as_bytes().to_vec());
        }
    }
    match agent.notifications.pop_front() {
        Some(bytes) => {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
            bytes.len() as i32
        }
        None => 0,
    }
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_paths(agent: &Agent, output: *mut u8) -> i32 {
    let snapshot = agent.mib.provisioning_snapshot();
    let paths = snapshot.data_paths;
    if paths.len() > 248 { return -28; }
    let profiles: Vec<_> = snapshot.vlan_profiles.into_iter().filter(|profile|
        paths.iter().any(|path| path.vlan_profiles.contains(&Some(profile.entity_id)))).collect();
    if profiles.iter().any(|profile| profile.rows.len() > 32) { return -28; }
    let graph = agent.mib.data_path_graph();
    let mut bytes = Vec::with_capacity(12 + paths.len() * 50 + profiles.len() * 544);
    bytes.extend_from_slice(&3u16.to_le_bytes());
    bytes.extend_from_slice(&(paths.len() as u16).to_le_bytes());
    bytes.extend_from_slice(&(profiles.len() as u16).to_le_bytes());
    bytes.extend_from_slice(&[0; 6]);
    for path in paths {
        let mut tcont = 0;
        let mut queue = 0;
        let mut encrypted = 0;
        if let Some(ctp) = graph.entities.iter().find(|e| e.class_id == schema::CLASS_GEM_PORT_NETWORK_CTP
            && e.attributes.iter().any(|a| a.index == 1 && a.value == path.gem_id.to_be_bytes())) {
            let attr = |index| ctp.attributes.iter().find(|a| a.index == index).map(|a| a.value.as_slice());
            if let Some(value) = attr(2).filter(|_| path.alloc_id != u16::MAX) {
                let entity = u16::from_be_bytes([value[0], value[1]]);
                if entity >= 0x8000 && entity < 0x801f { tcont = entity - 0x8000 + 1; }
            }
            if let Some(value) = attr(4) {
                let entity = u16::from_be_bytes([value[0], value[1]]);
                queue = (7 - (entity & 7)) as u8;
            }
            if let Some(value) = attr(10) { encrypted = u8::from(value[0] == 1); }
        }
        if !path.multicast && path.pbit_mask != 0 && tcont == 0 { return -22; }
        bytes.extend_from_slice(&path.alloc_id.to_le_bytes());
        bytes.extend_from_slice(&path.gem_id.to_le_bytes());
        bytes.extend_from_slice(&path.vlan_id.to_le_bytes());
        bytes.push(path.pbit_mask);
        bytes.push(u8::from(path.multicast));
        bytes.push(queue);
        bytes.push(encrypted);
        bytes.extend_from_slice(&tcont.to_le_bytes());
        bytes.push(path.downstream_pbits);
        for id in path.vlan_profiles {
            let index = id.and_then(|id| profiles.iter().position(|profile| profile.entity_id == id))
                .map(|index| index as u16).unwrap_or(u16::MAX);
            bytes.extend_from_slice(&index.to_le_bytes());
        }
        bytes.push(path.vlan_exclude);
        bytes.push(path.unmarked_mode);
        bytes.push(path.default_priority);
        bytes.extend_from_slice(&path.mapper_dscp);
    }
    for profile in profiles {
        bytes.extend_from_slice(&profile.entity_id.to_le_bytes());
        bytes.extend_from_slice(&profile.input_tpid.to_le_bytes());
        bytes.extend_from_slice(&profile.output_tpid.to_le_bytes());
        bytes.push(profile.downstream_mode);
        bytes.push(profile.rows.len() as u8);
        bytes.extend_from_slice(&profile.dscp);
        for row in profile.rows { bytes.extend_from_slice(&row); }
    }
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
    bytes.len() as i32
}
