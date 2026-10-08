#!/usr/bin/env python3
"""Compare native calibration I2C operations with emulated stock machine code."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tools"))
from trace_stock_bosa import MODELS, trace

source = r'''
#include <stdio.h>
#include <stdlib.h>
#include "rtl9617c-bosa-sequence.h"
static unsigned char cells[257][256];
static unsigned int page;
static int wr(void *ctx, unsigned int reg, unsigned char value) {
    printf("write %u %u %u\n",page,reg,value);
    if (reg==127) page=value; else cells[reg>=128?page:256][reg]=value;
    return 0;
}
static int rd(void *ctx, unsigned int reg, unsigned char *value) {
    *value = page==129 && reg==137 ? 2 : cells[reg>=128?page:256][reg];
    printf("read %u %u %u\n",page,reg,*value);
    return 0;
}
static int wait_ready(void *ctx, unsigned int reg, unsigned char mask, unsigned char value) {
    unsigned char current;
    int ret=rd(ctx,reg,&current);
    return ret ? ret : (current&mask)==value ? 0 : -1;
}
int main(int argc,char **argv) {
    unsigned char calibration[1024];
    FILE *input=fopen(argv[1],"rb");
    if (!input || fread(calibration,1,sizeof(calibration),input)!=sizeof(calibration)) return 1;
    fclose(input);
    struct rtl_bosa_io io={.write=wr,.read=rd,.wait=wait_ready};
    return rtl_bosa_calibrate(&io,calibration,atoi(argv[2])) ? 1 : 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "logs/bosa-sequence-comparison.json")
    args = parser.parse_args()
    binary = root / "analysis/private/live-files/opt/upt/apps/apps/bin/rtkbosa"
    original = root / "analysis/private/live-files/var/config/rtkbosa_k.bin"
    headers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
    results = []
    with tempfile.TemporaryDirectory(dir=root / "artifacts") as directory:
        directory = Path(directory)
        cfile, program = directory / "native.c", directory / "native"
        cfile.write_text(source)
        subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        "-fsanitize=address,undefined", "-I", str(headers), str(cfile), "-o", str(program)], check=True)
        cases = {"original": original.read_bytes(),
                 "pattern": bytes((i * 73 + (i >> 3)) & 255 for i in range(1024))}
        for name, data in cases.items():
            calibration = directory / "calibration.bin"
            calibration.write_bytes(data)
            for model, number in MODELS.items():
                stock = trace(binary, data, number)
                output = subprocess.check_output([str(program), str(calibration), str(number)], text=True)
                native = [tuple([line.split()[0]] + list(map(int, line.split()[1:]))) for line in output.splitlines()]
                reference = [(op["op"], op["page"], op["reg"], op["value"]) for op in stock["operations"]]
                if native != reference:
                    for i, (actual, expected) in enumerate(zip(native, reference)):
                        if actual != expected:
                            raise AssertionError(f"{name}/{model} operation {i}: {actual} != {expected}")
                    raise AssertionError(f"{name}/{model}: operation count {len(native)} != {len(reference)}")
                results.append({"calibration": name, "model": model, "operations": len(native),
                                "stock_function": stock["function"], "exact_match": True})
    args.output.write_text(json.dumps({"stock_binary_sha256": stock["binary_sha256"],
        "ASan_UBSan": True, "hardware_verified": False, "cases": results}, indent=2) + "\n")
    print(f"{len(results)} stock/native calibration comparisons passed")


if __name__ == "__main__":
    main()
