#!/usr/bin/env python3
"""Inspect an embedded stock FIT and public U-Boot key; never execute scripts."""
import argparse
import hashlib
import io
import json
import re
import struct
from pathlib import Path

from pyfdt.pyfdt import FdtBlobParse, FdtNode, FdtPropertyBytes, FdtPropertyWords


def props(node):
    return {p.get_name(): p for p in node}


def raw(prop):
    if isinstance(prop, FdtPropertyBytes):
        return bytes(v & 255 for v in prop.bytes)
    if isinstance(prop, FdtPropertyWords):
        return b"".join(struct.pack(">I", v) for v in prop.words)
    raise ValueError("Expected embedded binary property")


def strings(prop):
    return prop.strings if prop is not None and hasattr(prop, "strings") else []


def public_key(dts):
    def words(name):
        match = re.search(re.escape(name) + r"\s*=\s*<([^>]+)>", dts)
        if not match:
            raise ValueError(f"Missing public key property: {name}")
        return b"".join(struct.pack(">I", int(v, 0)) for v in match[1].split())
    return int.from_bytes(words("rsa,modulus"), "big"), int.from_bytes(words("rsa,exponent"), "big")


def verify_image_signature(payload, signature, modulus, exponent):
    # SHA-256 RSA PKCS#1 v1.5 signatures on embedded image payloads only.
    # FIT configuration signatures require hashed-nodes/regions processing;
    # deliberately do not call an image check a configuration signature check.
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size or int.from_bytes(signature, "big") >= modulus:
        return False
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
    encoded = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big") == encoded


def inspect(fit, key_dts):
    if fit.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("FIT exceeds analysis limit")
    data = fit.read_bytes()
    root = FdtBlobParse(io.BytesIO(data)).to_fdt().get_rootnode()
    root_props = props(root)
    images = root_props["images"]
    configurations = props(root_props["configurations"])
    conf_name = strings(configurations["default"])[0]
    conf = props(configurations[conf_name])
    key_text = key_dts.read_text(encoding="utf-8")
    modulus, exponent = public_key(key_text)
    result = {
        "fit_sha256": hashlib.sha256(data).hexdigest(),
        "uboot_key_dts_sha256": hashlib.sha256(key_dts.read_bytes()).hexdigest(),
        "public_key_bits": modulus.bit_length(),
        "required_configuration_signature_in_key_dts": 'required = "conf"' in key_text,
        "default_configuration": conf_name,
        "configuration_signature_present": "signature" in conf,
        "configuration_signature_verified": False,
        "images": [],
    }
    for image in images:
        if not isinstance(image, FdtNode):
            continue
        image_props = props(image)
        payload = raw(image_props["data"])
        record = {"name": image.get_name(), "bytes": len(payload),
                  "sha256": hashlib.sha256(payload).hexdigest()}
        for name in ("type", "arch", "compression"):
            record[name] = strings(image_props.get(name))
        for name in ("load", "entry"):
            if name in image_props:
                record[name] = [hex(v) for v in image_props[name].words]
        if record["compression"] == ["lzma"]:
            if len(payload) < 13:
                raise ValueError("Truncated LZMA-alone header")
            prop = payload[0]
            record["lzma_alone"] = {"lc": prop % 9, "lp": (prop // 9) % 5,
                                     "pb": prop // 45,
                                     "dictionary_bytes": struct.unpack_from("<I", payload, 1)[0]}
        signature = image_props.get("signature")
        record["signature_present"] = signature is not None
        if signature is not None:
            sig_props = props(signature)
            record["signature_algorithm"] = strings(sig_props.get("algo"))
            if record["signature_algorithm"] == ["sha256,rsa2048"]:
                record["image_signature_matches_embedded_public_key"] = verify_image_signature(
                    payload, raw(sig_props["value"]), modulus, exponent)
        if record["type"] == ["script"]:
            script = payload.decode("utf-8", errors="strict")
            record["script_sha256"] = hashlib.sha256(payload).hexdigest()
            record["script_sets_dm_verity"] = "dm-mod.create" in script and "verity_root_hash" in script
            record["script_boots_unzip_src"] = "bootm ${unzip_src}" in script
            record["script_executed"] = False
        result["images"].append(record)
    result["limits"] = [
        "No configuration signature verification; no script execution.",
        "Backup load/entry fields are observations, not approved native load addresses.",
        "Embedded required=conf is not a live secure-boot/fuse measurement.",
        "Native persistent UBI installation needs the actual bootcmd and signing/booti policy.",
    ]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fit", type=Path)
    parser.add_argument("uboot_key_dts", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    output = json.dumps(inspect(args.fit, args.uboot_key_dts), indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output)
