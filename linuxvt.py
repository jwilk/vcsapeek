import ctypes
import errno
import fcntl
import os

GIO_UNIMAP = 0x4B66
VT_GETHIFONTMASK = 0x560D
VT_GETSTATE = 0x5603

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

class VT(object):

    def __init__(self, tty=None, vcsa=None):
        self._tty = None
        self._vcsa = None
        if vcsa is None and tty is None:
            console = os.open('/dev/console', os.O_RDONLY | os.O_NOCTTY)
            state = VTState()
            try:
                fcntl.ioctl(console, VT_GETSTATE, state)
            finally:
                os.close(console)
            tty = '/dev/tty%d' % state.active
            vcsa = '/dev/vcsa%d' % state.active
        if tty is not None:
            self._tty = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
            if vcsa is None:
                dev_stat = os.fstat(self._tty)
                minor = dev_stat.st_rdev & 0xff
                major = dev_stat.st_rdev >> 8
                if major != 4:
                    raise NotImplementedError
                if not (0 < minor < 64):
                    raise NotImplementedError
                vcsa = '/dev/vcsa%d' % minor
        assert vcsa is not None
        self._vcsa = os.open(vcsa, os.O_RDONLY)
        if tty is None:
            dev_stat = os.fstat(self._vcsa)
            minor = dev_stat.st_rdev & 0xff
            major = dev_stat.st_rdev >> 8
            if major != 7:
                raise NotImplementedError
            if not (128 < minor < 192):
                raise NotImplementedError
            tty = '/dev/tty%d' % (minor - 128)
            self._tty = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
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
        while 1:
            try:
                fcntl.ioctl(self._tty, GIO_UNIMAP, unimap_desc)
            except IOError as ex:
                if ex.errno == errno.ENOMEM:
                    if unimap_desc.count == 0:
                        raise
                    entries = (Unipair * unimap_desc.count)()
                    unimap_desc.entries = entries
                    continue
                else:
                    raise
            return dict((x.fontpos, chr(x.unicode)) for x in entries)

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

    def peek_plain_text(self):
        lines = self.peek_raw_data()
        return ''.join(
            ''.join(char for char, attr in line) + '\n'
            for line in lines
        )

    def _get_ansi_attr(self, attr=None):
        if attr is None:
            return '\x1b[0m'
        blink = 5 if attr & 1 else 0
        bold = 1 if attr & 16 else 0
        fg = 30 + _linux_color_to_ansi[(attr & 15) >> 1]
        bg = 40 + _linux_color_to_ansi[attr >> 5]
        result = [0, fg, bg]
        if bold:
            result += [bold]
        if blink:
            result += [blink]
        return '\x1b[{0}m'.format(';'.join(map(str, result)))

    def peek_ansi_text(self):
        last_ansi_attr = default_ansi_attr = self._get_ansi_attr()
        result = []
        for line in self.peek_raw_data():
            for char, attr in line:
                ansi_attr = self._get_ansi_attr(attr)
                if ansi_attr != last_ansi_attr:
                    result += [ansi_attr]
                    last_ansi_attr = ansi_attr
                result += [char]
            result += [default_ansi_attr, '\n']
            last_ansi_attr = default_ansi_attr
        return ''.join(result)

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

# vim:ts=4 sw=4 et
