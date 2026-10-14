// SPDX-License-Identifier: GPL-2.0-only

pub struct IdentityConfig {
    pub omcc_version: u8,
    pub disable_enhanced_security: bool,
    pub vendor_id: Vec<u8>,
    pub equipment_id: Vec<u8>,
    pub hardware_version: Vec<u8>,
    pub software_version: Vec<u8>,
    pub loid: Vec<u8>,
    pub loid_password: Vec<u8>,
    pub operator_id: Vec<u8>,
    pub serial_number: Vec<u8>,
    pub psk: Option<[u8; 16]>,
}

impl IdentityConfig {
    pub fn onu_g_serial(&self) -> Vec<u8> {
        self.serial_number.clone()
    }
}

pub fn fixed_width(value: &[u8], width: usize) -> Vec<u8> {
    let mut result = vec![0; width];
    let length = value.len().min(width);
    result[..length].copy_from_slice(&value[..length]);
    result
}
