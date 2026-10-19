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

use std::collections::{BTreeMap, BTreeSet, VecDeque};
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
    omcc_version: u8,
    disable_enhanced_security: u8,
}

pub struct Agent {
    mib: Mib,
    led_auth: u8,
    cache: VecDeque<(Vec<u8>, Response)>,
    notifications: VecDeque<Vec<u8>>,
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_new(identity: &Identity) -> *mut Agent {
    let identity = IdentityConfig {
        omcc_version: if identity.omcc_version == 0 { 0xb0 } else { identity.omcc_version },
        disable_enhanced_security: identity.disable_enhanced_security != 0,
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
        led_auth: 0,
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
    agent.led_auth = 0;
    agent.cache.clear();
    agent.notifications.clear();
    agent.mib.reset_security_session();
}

#[no_mangle]
pub extern "C" fn tyfza1_omci_led_auth(agent: &Agent) -> u8 {
    agent.led_auth
}

fn update_led_auth(agent: &mut Agent, request: &Request<'_>) {
    if request.action == protocol::ACTION_SET && request.class_id == schema::CLASS_CTC_LOID_AUTH
        && request.entity_id == 0 && request.attribute_mask & 0x1000 != 0 {
        agent.led_auth = agent.mib.loid_auth_status();
    }
}

#[no_mangle]
pub unsafe extern "C" fn tyfza1_omci_broadcast(agent: &Agent, data: *const u8, length: usize, row: *mut u8) -> i32 {
    let data = slice::from_raw_parts(data, length);
    // The daemon programs the key before dispatch; a cached reply must also skip that write.
    if agent.cache.iter().any(|(key, _)| key == data) { return 0; }
    let Ok(request) = Request::parse(data) else { return 0; };
    if agent.mib.enhanced_security_enabled() && request.action == protocol::ACTION_SET
        && request.class_id == schema::CLASS_ENHANCED_SECURITY_CONTROL
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
        let response = if matches!(request.action, protocol::ACTION_CREATE | protocol::ACTION_DELETE |
            protocol::ACTION_SET | protocol::ACTION_SET_TABLE | protocol::ACTION_MIB_RESET) {
            let mut candidate = agent.mib.clone();
            let response = candidate.dispatch(&request);
            if response.result() == Some(protocol::RESULT_SUCCESS) {
                if candidate.update_queue_bindings(&request).is_err() || paths_bytes(&candidate).is_err() {
                    let response = Response::new(&request, 1);
                    let bytes = response.as_bytes();
                    std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
                    return bytes.len() as i32;
                }
                agent.mib = candidate;
                update_led_auth(agent, &request);
            }
            response
        } else {
            agent.mib.dispatch(&request)
        };
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
pub unsafe extern "C" fn tyfza1_omci_dispatch_apply(
    agent: &mut Agent, data: *const u8, length: usize, output: *mut u8, backend_result: &mut i32,
    apply: unsafe extern "C" fn(*mut std::ffi::c_void, *const u8, usize) -> i32,
    context: *mut std::ffi::c_void,
) -> i32 {
    let frame = slice::from_raw_parts(data, length);
    let request = match Request::parse(frame) {
        Ok(request) => request,
        Err(_) => return -74,
    };
    if *backend_result < 0 || agent.cache.iter().any(|(key, _)| key == frame)
        || !matches!(request.action, protocol::ACTION_CREATE | protocol::ACTION_DELETE |
            protocol::ACTION_SET | protocol::ACTION_SET_TABLE | protocol::ACTION_MIB_RESET) {
        return tyfza1_omci_dispatch(agent, data, length, output, *backend_result);
    }
    let mut candidate = agent.mib.clone();
    let response = candidate.dispatch(&request);
    if response.result() == Some(protocol::RESULT_SUCCESS) {
        *backend_result = match candidate.update_queue_bindings(&request).and_then(|_| paths_bytes(&candidate)) {
            Ok(paths) => apply(context, paths.as_ptr(), paths.len()),
            Err(error) => error,
        };
        if *backend_result < 0 {
            let response = Response::new(&request, 1);
            let bytes = response.as_bytes();
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
            return bytes.len() as i32;
        }
        // Commit the MIB, reply cache and notifications only after hardware accepts it.
        agent.mib = candidate;
        update_led_auth(agent, &request);
    }
    // Dispatch against the committed MIB would execute the mutation twice.
    if request.action == protocol::ACTION_MIB_RESET {
        agent.cache.clear();
        agent.notifications.clear();
    }
    if agent.cache.len() == 32 { agent.cache.pop_front(); }
    agent.cache.push_back((frame.to_vec(), response.clone()));
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
    let bytes = match paths_bytes(&agent.mib) {
        Ok(bytes) => bytes,
        Err(error) => return error,
    };
    std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
    bytes.len() as i32
}

fn paths_bytes(mib: &Mib) -> Result<Vec<u8>, i32> {
    let snapshot = mib.provisioning_snapshot();
    let paths = snapshot.data_paths;
    if paths.len() > 248 { return Err(-28); }
    let profiles: Vec<_> = snapshot.vlan_profiles.into_iter().filter(|profile|
        paths.iter().any(|path| path.vlan_profiles.contains(&Some(profile.entity_id)))).collect();
    if profiles.iter().any(|profile| profile.rows.len() > 32) { return Err(-28); }
    let graph = mib.data_path_graph();
    let entity = |class, id| graph.entities.iter().find(|e| e.class_id == class && e.entity_id == id);
    fn attribute(e: &provisioning::DataPathEntity, index: u8) -> Option<&[u8]> {
        e.attributes.iter().find(|a| a.index == index).map(|a| a.value.as_slice())
    }
    let word = |e: &provisioning::DataPathEntity, index| attribute(e, index)
        .map(|value| u16::from_be_bytes([value[0], value[1]]));
    let ctp = |gem: u16| graph.entities.iter().find(|e| e.class_id == schema::CLASS_GEM_PORT_NETWORK_CTP
        && word(e, 1) == Some(gem));
    let mut active = BTreeSet::new();
    for &binding in graph.gem_queue_bindings.values() { active.insert(binding); }
    for &id in &graph.configured_queues {
        if graph.gem_queue_bindings.values().any(|binding| binding.0 == id) { continue; }
        let queue = entity(schema::CLASS_PRIORITY_QUEUE, id).ok_or(-22)?;
        let related = attribute(queue, 6).ok_or(-22)?;
        active.insert((id, u16::from_be_bytes([related[0], related[1]])));
    }
    // The original wrapper activates only queues referenced by GEMs or explicitly set by the OLT.
    let mut groups = BTreeMap::<u16, Vec<(u16, u16, u8)>>::new();
    for (id, owner) in active {
        let queue = entity(schema::CLASS_PRIORITY_QUEUE, id).ok_or(-22)?;
        let related = attribute(queue, 6).ok_or(-22)?;
        let priority = u16::from_be_bytes([related[2], related[3]]);
        let scheduler = word(queue, 7).ok_or(-22)?;
        let policy = if scheduler & 0x8000 != 0 {
            let scheduler = entity(schema::CLASS_TRAFFIC_SCHEDULER, scheduler).ok_or(-22)?;
            attribute(scheduler, 3).ok_or(-22)?[0]
        } else {
            attribute(entity(schema::CLASS_TCONT, owner).ok_or(-22)?, 3).ok_or(-22)?[0]
        };
        if !(0x8000..0x801f).contains(&owner) { return Err(-22); }
        let weight = if policy == 2 {
            let weight = attribute(queue, 8).ok_or(-22)?[0];
            if weight == 0 { return Err(-22); }
            weight
        } else { 0 };
        groups.entry(owner).or_default().push((id, priority, weight));
    }
    let mut mapping = BTreeMap::<(u16, u16), u8>::new();
    let mut weights = BTreeMap::<u16, [u8; 8]>::new();
    for (owner, mut queues) in groups {
        // libomci_mib.so:0x19aa8: WRR first; SP priority descending; equal SP priority by ME ID.
        queues.sort_by(|a, b| match (a.2 != 0, b.2 != 0) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            (false, false) => b.1.cmp(&a.1).then(a.0.cmp(&b.0)),
            (true, true) => std::cmp::Ordering::Equal,
        });
        let weighted = queues.iter().filter(|queue| queue.2 != 0).count();
        // PVT queues 0 and 1 share their SP/WRR selection; reserve an unused WRR partner.
        let partner = usize::from(weighted == 1);
        if queues.len() + partner > 8 { return Err(-28); }
        let mut table = [0; 8];
        for (rank, (id, _, weight)) in queues.into_iter().enumerate() {
            let slot = rank + if rank >= weighted { partner } else { 0 };
            mapping.insert((id, owner), slot as u8);
            table[slot] = weight;
        }
        if partner != 0 { table[1] = table[0]; }
        weights.insert(owner, table);
    }
    let mut active_tconts = BTreeSet::new();
    let mut bytes = Vec::with_capacity(12 + paths.len() * 50 + profiles.len() * 544 + 31 * 12);
    bytes.extend_from_slice(&4u16.to_le_bytes());
    bytes.extend_from_slice(&(paths.len() as u16).to_le_bytes());
    bytes.extend_from_slice(&(profiles.len() as u16).to_le_bytes());
    bytes.extend_from_slice(&[0; 6]);
    for path in paths {
        let mut alloc_id = path.alloc_id;
        let mut tcont = 0;
        let mut queue = 0;
        let mut encrypted = 0;
        if let Some(ctp) = ctp(path.gem_id) {
            if !path.multicast && path.pbit_mask != 0 {
                let binding = graph.gem_queue_bindings[&ctp.entity_id];
                let owner = binding.1;
                let slot = mapping[&binding];
                alloc_id = word(entity(schema::CLASS_TCONT, owner).ok_or(-22)?, 1).ok_or(-22)?;
                if alloc_id == u16::MAX { return Err(-22); }
                tcont = owner - 0x8000 + 1;
                queue = slot;
                active_tconts.insert(owner);
            }
            if let Some(value) = attribute(ctp, 10) { encrypted = u8::from(value[0] == 1); }
        }
        bytes.extend_from_slice(&alloc_id.to_le_bytes());
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
    let mut schedulers = 0u16;
    for owner in active_tconts {
        let table = weights[&owner];
        if table == [0; 8] { continue; }
        bytes.extend_from_slice(&(owner - 0x8000 + 1).to_le_bytes());
        bytes.extend_from_slice(&[0; 2]);
        bytes.extend_from_slice(&table);
        schedulers += 1;
    }
    bytes[6..8].copy_from_slice(&schedulers.to_le_bytes());
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    Ok(bytes)
}
