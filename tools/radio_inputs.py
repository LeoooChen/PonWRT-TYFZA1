"""Use the native radio firmware format when preparing board runtime data."""
import ctypes
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/fw.h'

class Section(ctypes.Structure):
    _fields_ = [('address', ctypes.c_uint), ('offset', ctypes.c_uint), ('bytes', ctypes.c_uint),
                ('type', ctypes.c_ubyte), ('redownload', ctypes.c_ubyte)]

class Image(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint) for name in ('chip', 'commit', 'header_bytes', 'dynamic_bytes', 'section_count')] + \
               [('cut', ctypes.c_ubyte), ('version', ctypes.c_ubyte * 4), ('command_version', ctypes.c_ubyte),
                ('sections', Section * 6)]

class NativeRadioFormat:
    def __init__(self):
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='radio-format-')
        work = Path(self.temporary.name)
        source, binary = work / 'format.c', work / 'format.so'
        source.write_text('''#include "fw.h"
int parse(const unsigned char *p,size_t n,struct rtl_ax_ap_image *out){return rtl_ax_ap_parse(p,n,out);}
int header(const unsigned char *p,const struct rtl_ax_ap_image *in,unsigned char *out,size_t n){return rtl_ax_ap_download_header(p,in,out,n);}
const char *name(unsigned int chip,unsigned int cv){return rtl_ax_ap_name(chip,cv);}
unsigned int chunk(unsigned int n){return rtl_ax_ap_chunk_bytes(n);}
int match(unsigned int chip,unsigned int cv,const struct rtl_ax_ap_image *image){return rtl_ax_ap_match(chip,cv,image);}
''')
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I' + str(HEADER.parent), str(source), '-o', str(binary)], check=True)
        self.library = ctypes.CDLL(str(binary))
        self.library.parse.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(Image)]
        self.library.header.argtypes = [ctypes.c_void_p, ctypes.POINTER(Image), ctypes.c_void_p, ctypes.c_size_t]
        self.library.name.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.library.name.restype = ctypes.c_char_p
        self.library.chunk.argtypes = [ctypes.c_uint]
        self.library.chunk.restype = ctypes.c_uint
        self.library.match.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(Image)]

    def parse(self, data):
        image = Image()
        source = ctypes.create_string_buffer(data)
        result = self.library.parse(source, len(data), ctypes.byref(image))
        if result:
            raise ValueError('Invalid native radio firmware layout: ' + str(result))
        return image

    def header(self, data, image):
        source = ctypes.create_string_buffer(data)
        output = ctypes.create_string_buffer(256)
        result = self.library.header(source, ctypes.byref(image), output, len(output))
        if result < 0:
            raise ValueError('Cannot encode firmware download header: ' + str(result))
        return output.raw[:result]

    def close(self):
        self.temporary.cleanup()
