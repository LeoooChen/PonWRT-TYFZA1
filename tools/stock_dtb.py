"""Read board DT properties and translate banked GPIOs to native pin numbers."""
import struct

def fdt_nodes(data):
    header = struct.unpack_from('>10I', data)
    assert header[0] == 0xd00dfeed and header[1] == len(data)
    strings = data[header[3]:header[3] + header[8]]
    position, stack, nodes = header[2], [], {}
    while position < header[2] + header[9]:
        token = struct.unpack_from('>I', data, position)[0]
        position += 4
        if token == 1:
            end = data.index(b'\0', position)
            stack.append(data[position:end].decode())
            nodes['/' + '/'.join(stack[1:])] = {}
            position = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from('>II', data, position)
            position += 8
            name = strings[name_offset:strings.index(b'\0', name_offset)].decode()
            nodes['/' + '/'.join(stack[1:])][name] = data[position:position + length]
            position = (position + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            return nodes
        else:
            raise ValueError('Unknown FDT token')
    raise ValueError('Missing FDT end')

def cells(value):
    return list(struct.unpack('>%dI' % (len(value) // 4), value))

def names(value):
    return value.rstrip(b'\0').decode().split('\0')

def phandles(nodes):
    return {cells(properties['phandle'])[0]: (path, properties)
            for path, properties in nodes.items() if 'phandle' in properties}

def banked_gpio(handles, specifier, enabled=True):
    provider_path, provider = handles[specifier[0]]
    assert cells(provider['#gpio-cells']) == [2] and len(specifier) == 3
    bank = cells(provider['reg'])[0]
    pin, flags = specifier[1:]
    ranges = cells(provider['gpio-ranges'])
    assert len(ranges) == 4 and ranges[1] == 0 and ranges[2] == bank * 32
    if enabled:
        assert 0 <= pin < min(cells(provider['ngpios'])[0], ranges[3])
    return {'controller': provider_path, 'bank': bank, 'bank_pin': pin,
            'native_pin': ranges[2] + pin if enabled else None, 'flags': flags,
            'unused': not enabled}
