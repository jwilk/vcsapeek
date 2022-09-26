# encoding=UTF-8

# Copyright © 2012-2022 Jakub Wilk <jwilk@jwilk.net>
# SPDX-License-Identifier: MIT

import ctypes
import errno
import fcntl
import os
import re

0_0  # Python >= 3.6 is required

GIO_UNIMAP = 0x4B66
VT_GETHIFONTMASK = 0x560D
VT_GETSTATE = 0x5603

# From <linux/major.h>:
TTY_MAJOR = 4
VCS_MAJOR = 7

# From <linux/vt.h>:
MIN_NR_CONSOLES = 1
MAX_NR_CONSOLES = 63

class VTState(ctypes.Structure):
    _fields_ = [
        ('active', ctypes.c_ushort),
        ('signal', ctypes.c_ushort),
        ('state', ctypes.c_ushort),
    ]

class Unipair(ctypes.Structure):
    _fields_ = [
        ('unicode', ctypes.c_ushort),
        ('fontpos', ctypes.c_ushort),
    ]

class UnimapDesc(ctypes.Structure):
    _fields_ = [
        ('count', ctypes.c_ushort),
        ('entries', ctypes.POINTER(Unipair))
    ]

class VCSAHeader(ctypes.Structure):
    _fields_ = [
        ('height', ctypes.c_ubyte),
        ('width', ctypes.c_ubyte),
        ('x', ctypes.c_ubyte),
        ('y', ctypes.c_ubyte),
    ]

class VCSAChar(ctypes.Structure):
    _fields_ = [
        ('char', ctypes.c_ubyte),
        ('attr', ctypes.c_ubyte),
    ]

_linux_color_to_ansi = {
    0: 0,
    4: 1,
    2: 2,
    6: 3,
    1: 4,
    5: 5,
    3: 6,
    7: 7,
}

_ansi_to_css = {
    1: 'font-weight: bold',
    5: 'text-decoration: blink',
    30: ('color: black', 'color: darkgrey'),
    31: ('color: darkred', 'color: red'),
    32: ('color: darkgreen', 'color: green'),
    33: ('color: darkorange', 'color: orange'),
    34: ('color: darkblue', 'color: darkblue'),
    35: ('color: darkmagenta', 'color: magenta'),
    36: ('color: darkcyan', 'color: cyan'),
    37: ('color: lightgrey', 'color: white'),
    40: 'background-color: black',
    41: 'background-color: darkred',
    42: 'background-color: darkgreen',
    43: 'background-color: darkorange',
    44: 'background-color: darkblue',
    45: 'background-color: darkmagenta',
    46: 'background-color: darkcyan',
    47: 'background-color: lightgrey',
}

def format_ansi(attrs):
    attrs = str.join(';', map(str, attrs))
    return f'\x1B[{attrs}m'

class VT(object):

    def get_active_vt(self):
        try:
            with open('/sys/class/tty/tty0/active', 'rt', encoding='ASCII') as fp:
                tty0 = fp.read()
        except OSError:
            pass
        else:
            match = re.match(r'^tty(\d+)$', tty0)
            if match is not None:
                return int(match.group(1))
        console = os.open('/dev/tty0', os.O_RDONLY | os.O_NOCTTY)
        state = VTState()
        try:
            fcntl.ioctl(console, VT_GETSTATE, state)
        finally:
            os.close(console)
        return state.active

    def __init__(self, device=None):
        self._tty = None
        self._vcsa = None
        if device is None:
            n = self.get_active_vt()
        else:
            fd = os.open(device, os.O_RDONLY | os.O_NOCTTY)
            try:
                dev_stat = os.fstat(fd)
                minor = dev_stat.st_rdev & 0xFF
                major = dev_stat.st_rdev >> 8
                if major == TTY_MAJOR:
                    n = minor
                    if 0 < n <= MAX_NR_CONSOLES:
                        pass
                    else:
                        raise OSError(errno.ENOTTY, 'not a /dev/ttyN device', device)
                    self._tty = os.dup(fd)
                elif major == VCS_MAJOR:
                    n = minor - 128
                    if MIN_NR_CONSOLES <= n <= MAX_NR_CONSOLES:
                        pass
                    else:
                        raise OSError(errno.ENOTTY, 'not a /dev/vcsaN device', device)
                    self._vcsa = os.dup(fd)
            finally:
                os.close(fd)
        if self._tty is None:
            tty = f'/dev/tty{n}'
            self._tty = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
        if self._vcsa is None:
            vcsa = f'/dev/vcsa{n}'
            self._vcsa = os.open(vcsa, os.O_RDONLY)
        assert self._tty is not None
        assert self._vcsa is not None
        self._unicode_map = self._get_unicode_map()
        self._hi_font_mask = self._get_hi_font_mask()

    def _get_hi_font_mask(self):
        mask = ctypes.c_ushort()
        fcntl.ioctl(self._tty, VT_GETHIFONTMASK, mask)
        assert mask.value & 0xFF == 0
        if mask.value != 0x100:
            raise NotImplementedError
        return mask.value >> 8

    def _get_unicode_map(self):
        entries = []
        unimap_desc = UnimapDesc(count=0, entries=None)
        while True:
            try:
                fcntl.ioctl(self._tty, GIO_UNIMAP, unimap_desc)
            except OSError as ex:
                if ex.errno == errno.ENOMEM:
                    if unimap_desc.count == 0:
                        raise
                    entries = (Unipair * unimap_desc.count)()
                    unimap_desc.entries = entries
                    continue
                else:
                    raise
            m = {}
            for entry in entries:
                try:
                    old_chr = m[entry.fontpos]
                except LookupError:
                    m[entry.fontpos] = chr(entry.unicode)
                else:
                    m[entry.fontpos] = min(old_chr, chr(entry.unicode))
            return m

    def peek_raw_data(self):
        os.lseek(self._vcsa, 0, os.SEEK_SET)
        header = os.read(self._vcsa, 4)
        header = VCSAHeader.from_buffer_copy(header)
        width, height = header.width, header.height
        del header
        for y in range(height):
            yield list(self._read_raw_line(width))

    def _read_raw_line(self, width):
        hi_font_mask = self._hi_font_mask
        unicode_map = self._unicode_map
        line = os.read(self._vcsa, width * 2)
        line = (VCSAChar * width).from_buffer_copy(line)
        for char in line:
            char, attr = char.char, char.attr
            if attr & hi_font_mask:
                char |= 0x100
            char = unicode_map[char]
            attr = attr & ~hi_font_mask
            yield char, attr

    def peek_text(self):
        lines = self.peek_raw_data()
        return str.join('', (
            str.join('', (char for char, attr in line)) + '\n'
            for line in lines
        ))

    def _get_ansi_attr(self, attr=None):
        if attr is None:
            return [0]
        blink = 5 if attr & 1 else 0
        bold = 1 if attr & 16 else 0
        fg = 30 + _linux_color_to_ansi[(attr & 15) >> 1]
        bg = 40 + _linux_color_to_ansi[attr >> 5]
        result = [0, fg, bg]
        if bold:
            result += [bold]
        if blink:
            result += [blink]
        return result

    def peek_ansi(self):
        last_ansi_attr = default_ansi_attr = self._get_ansi_attr()
        result = []
        for line in self.peek_raw_data():
            for char, attr in line:
                ansi_attr = self._get_ansi_attr(attr)
                if ansi_attr != last_ansi_attr:
                    result += [format_ansi(ansi_attr)]
                    last_ansi_attr = ansi_attr
                result += [char]
            result += [format_ansi(default_ansi_attr), '\n']
            last_ansi_attr = default_ansi_attr
        return str.join('', result)

    def peek_xhtml(self):
        import lxml.html
        root_elt = lxml.html.Element('pre')
        root_elt.attrib['class'] = 'tty'
        last_ansi_attr = self._get_ansi_attr()
        elt = None
        for line in self.peek_raw_data():
            if elt is not None:
                elt.tail = '\n'
                elt = None
            for char, attr in line:
                ansi_attr = self._get_ansi_attr(attr)
                if (ansi_attr != last_ansi_attr) or (elt is None):
                    last_ansi_attr = ansi_attr
                    elt = lxml.html.Element('span')
                    root_elt.append(elt)
                    assert 0 in ansi_attr
                    bold = 1 in ansi_attr
                    css = []
                    for a in ansi_attr:
                        if a == 0:
                            continue
                        css_chunk = _ansi_to_css[a]
                        if isinstance(css_chunk, tuple):
                            css_chunk = css_chunk[bold]
                        css += [css_chunk]
                    css = str.join('; ', css)
                    elt.attrib['style'] = str(css)
                elt.text = (elt.text or '') + char
        if elt is not None:
            elt.tail = '\n'
        return lxml.html.tostring(root_elt, encoding='unicode') + '\n'

    def __enter__(self):
        return self

    def __exit__(self, *excinfo):
        if self._tty is not None:
            os.close(self._tty)
            self._tty = None
        if self._vcsa is not None:
            os.close(self._vcsa)
            self._vcsa = None

__all__ = ['VT']

# vim:ts=4 sts=4 sw=4 et
