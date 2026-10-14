// SPDX-License-Identifier: GPL-2.0-only

//! OMCI authentication and message integrity calculations.

use std::fs::File;
use std::io::{self, Read};

use aes::Aes128;
use cmac::{Cmac, Mac};

/* The MSK name derivation appends this fixed peer identity. */
const MSK_NAME_IDENTITY: [u8; 16] = [
    0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93, 0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93,
];

fn aes_cmac(key: &[u8; 16], message: &[u8]) -> [u8; 16] {
    let mut cmac = <Cmac<Aes128> as Mac>::new_from_slice(key)
        .expect("AES-CMAC-128 accepts a 16-byte key");
    cmac.update(message);
    cmac.finalize().into_bytes().into()
}

pub fn fill_random(bytes: &mut [u8]) -> io::Result<()> {
    File::open("/dev/urandom")?.read_exact(bytes)
}

pub fn authentication_result(
    key: &[u8; 16],
    olt_challenges: &[[u8; 16]],
    onu_challenges: &[[u8; 16]],
    identity: &[u8; 8],
    olt_first: bool,
) -> Option<[u8; 16]> {
    if olt_challenges.is_empty() || onu_challenges.len() != 1 {
        return None;
    }

    /*
     * Class 332 produces one 128-bit authentication result. The direction
     * selects challenge concatenation order: OLT|ONU for the ONU result and
     * ONU|OLT for OLT verification. Selected crypto 1 is AES-CMAC-128.
     */
    let mut message = Vec::with_capacity(1 + olt_challenges.len() * 32 + identity.len());
    message.push(1);
    if olt_first {
        append_challenges(&mut message, olt_challenges);
        append_challenges(&mut message, onu_challenges);
    } else {
        append_challenges(&mut message, onu_challenges);
        append_challenges(&mut message, olt_challenges);
    }
    message.extend_from_slice(identity);
    Some(aes_cmac(key, &message))
}

pub fn master_session_key(
    key: &[u8; 16],
    olt_challenges: &[[u8; 16]],
    onu_challenges: &[[u8; 16]],
) -> Option<[u8; 16]> {
    session_value(key, olt_challenges, onu_challenges, &[], false)
}

pub fn master_session_key_name(
    key: &[u8; 16],
    olt_challenges: &[[u8; 16]],
    onu_challenges: &[[u8; 16]],
) -> Option<[u8; 16]> {
    session_value(key, olt_challenges, onu_challenges, &MSK_NAME_IDENTITY, true)
}

fn session_value(
    key: &[u8; 16],
    olt_challenges: &[[u8; 16]],
    onu_challenges: &[[u8; 16]],
    identity: &[u8],
    onu_first: bool,
) -> Option<[u8; 16]> {
    if olt_challenges.is_empty() || onu_challenges.len() != 1 {
        return None;
    }
    let mut message = Vec::with_capacity(olt_challenges.len() * 32 + identity.len());
    if onu_first {
        append_challenges(&mut message, onu_challenges);
        append_challenges(&mut message, olt_challenges);
    } else {
        append_challenges(&mut message, olt_challenges);
        append_challenges(&mut message, onu_challenges);
    }
    message.extend_from_slice(identity);
    Some(aes_cmac(key, &message))
}

fn append_challenges(output: &mut Vec<u8>, challenges: &[[u8; 16]]) {
    for challenge in challenges {
        output.extend_from_slice(challenge);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cmac_matches_nist_vector() {
        /* NIST SP 800-38B example 1 verifies the RustCrypto API and byte order. */
        let key = [
            0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, 0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf,
            0x4f, 0x3c,
        ];
        let mut cmac = <Cmac<Aes128> as Mac>::new_from_slice(&key).unwrap();
        cmac.update(&[]);
        assert_eq!(
            cmac.finalize().into_bytes().as_slice(),
            [
                0xbb, 0x1d, 0x69, 0x29, 0xe9, 0x59, 0x37, 0x28, 0x7f, 0xa3, 0x7d, 0x12, 0x9b, 0x75,
                0x67, 0x46,
            ]
        );
    }


}
