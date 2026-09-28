"""A small read-only CHD (MAME "Compressed Hunks of Data") reader.

Just enough to read a few sectors from a CD or DVD image — the disc's
SYSTEM.CNF or PARAM.SFO — so CHD games get a platform, serial and region like
their ISO and BIN/CUE siblings. Follows MAME's chd.cpp / huffman.cpp for
version 5 files:

  * the compressed hunk map (Huffman-coded compression types + bit-packed
    lengths and offsets);
  * the codecs chdman uses by default for discs: zlib, LZMA and Huffman for
    DVDs; cdzl / cdlz (Deflate / LZMA of the sector data plus subcode) for CDs.

FLAC (audio tracks), Zstandard, parent CHDs and older CHD versions aren't
supported; reading such a hunk raises ChdError and the caller falls back to
the folder name.
"""
import lzma
import re
import struct
import zlib

SECTOR = 2048
CD_FRAME = 2448          # 2352 bytes of sector + 96 of subcode
CD_SECTOR = 2352
CD_SUBCODE = 96

# Hunk compression types in the v5 map (chd.cpp).
TYPE_0, TYPE_1, TYPE_2, TYPE_3 = 0, 1, 2, 3
NONE, SELF, PARENT = 4, 5, 6
RLE_SMALL, RLE_LARGE = 7, 8
SELF_0, SELF_1, PARENT_SELF, PARENT_0, PARENT_1 = 9, 10, 11, 12, 13


class ChdError(Exception):
    pass


class _Bits:
    """MSB-first bit reader; reads past the end yield zeros (as MAME's).
    Reads at most 32 bits at a time from a byte window — the hunk map of a
    DVD image holds a million entries, so this has to be cheap."""

    def __init__(self, data):
        self.data = bytes(data) + b"\x00" * 8
        self.pos = 0

    def peek(self, count):
        if count == 0:
            return 0
        pos = self.pos
        window = int.from_bytes(self.data[pos >> 3:(pos >> 3) + 5], "big")
        return (window >> (40 - (pos & 7) - count)) & ((1 << count) - 1)

    def read(self, count):
        value = self.peek(count)
        self.pos += count
        return value

    def skip(self, count):
        self.pos += count


class _Huffman:
    """Canonical Huffman decoder with MAME's code assignment."""

    def __init__(self, numcodes, maxbits):
        self.numcodes = numcodes
        self.maxbits = maxbits
        self.bits = [0] * numcodes
        self.lookup = None

    def _build(self):
        histo = [0] * 33
        for n in self.bits:
            if n > self.maxbits:
                raise ChdError("bad huffman tree")
            histo[n] += 1
        start = 0
        for length in range(32, 0, -1):
            nxt = (start + histo[length]) >> 1
            histo[length] = start
            start = nxt
        lookup = [(0, 0)] * (1 << self.maxbits)
        for symbol, n in enumerate(self.bits):
            if n == 0:
                continue
            code = histo[n]
            histo[n] += 1
            shift = self.maxbits - n
            first = code << shift
            for i in range(first, first + (1 << shift)):
                lookup[i] = (symbol, n)
        self.lookup = lookup

    def decode(self, bits):
        symbol, n = self.lookup[bits.peek(self.maxbits)]
        bits.skip(n)
        return symbol

    def import_rle(self, bits):
        numbits = 5 if self.maxbits >= 16 else 4 if self.maxbits >= 8 else 3
        cur = 0
        while cur < self.numcodes:
            n = bits.read(numbits)
            if n != 1:
                self.bits[cur] = n
                cur += 1
                continue
            n = bits.read(numbits)
            if n == 1:
                self.bits[cur] = n
                cur += 1
                continue
            repeat = bits.read(numbits) + 3
            while repeat and cur < self.numcodes:
                self.bits[cur] = n
                cur += 1
                repeat -= 1
        self._build()

    def import_huffman(self, bits):
        small = _Huffman(24, 6)
        small.bits[0] = bits.read(3)
        start = bits.read(3) + 1
        count = 0
        for index in range(1, 24):
            if index < start or count == 7:
                small.bits[index] = 0
            else:
                count = bits.read(3)
                small.bits[index] = 0 if count == 7 else count
        small._build()
        temp, fullbits = self.numcodes - 9, 0
        while temp:
            temp >>= 1
            fullbits += 1
        last = cur = 0
        while cur < self.numcodes:
            value = small.decode(bits)
            if value:
                last = value - 1
                self.bits[cur] = last
                cur += 1
            else:
                count = bits.read(3) + 2
                if count == 9:
                    count += bits.read(fullbits)
                while count and cur < self.numcodes:
                    self.bits[cur] = last
                    cur += 1
                    count -= 1
        self._build()


def _inflate(data, size):
    return zlib.decompressobj(-15).decompress(data, size)


def _unlzma(data, size):
    dict_size = 1 << max(12, (size - 1).bit_length())
    decoder = lzma.LZMADecompressor(
        lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                                   "lc": 3, "lp": 0, "pb": 2}])
    return decoder.decompress(data, size)


def _unhuff(data, size):
    bits = _Bits(data)
    tree = _Huffman(256, 16)
    tree.import_huffman(bits)
    return bytes(tree.decode(bits) for _ in range(size))


def _uncd(data, size, base):
    frames = size // CD_FRAME
    len_bytes = 2 if size < 65536 else 3
    ecc_bytes = (frames + 7) // 8
    header = ecc_bytes + len_bytes
    base_len = int.from_bytes(data[ecc_bytes:ecc_bytes + len_bytes], "big")
    sectors = base(data[header:header + base_len], frames * CD_SECTOR)
    # The subcode isn't needed to read files; sync/ECC bytes that chdman
    # stripped don't touch the user data either, so neither is rebuilt.
    out = bytearray(size)
    for frame in range(frames):
        out[frame * CD_FRAME:frame * CD_FRAME + CD_SECTOR] = \
            sectors[frame * CD_SECTOR:(frame + 1) * CD_SECTOR]
    return bytes(out)


CODECS = {
    b"zlib": _inflate,
    b"lzma": _unlzma,
    b"huff": _unhuff,
    b"cdzl": lambda d, n: _uncd(d, n, _inflate),
    b"cdlz": lambda d, n: _uncd(d, n, _unlzma),
}


class Chd:
    def __init__(self, path):
        self._fh = open(path, "rb")
        try:
            self._open()
        except ChdError:
            self.close()
            raise
        except Exception as exc:     # any malformed file is just "not readable"
            self.close()
            raise ChdError(f"{type(exc).__name__}: {exc}") from exc

    def close(self):
        self._fh.close()

    def _open(self):
        head = self._fh.read(124)
        if head[:8] != b"MComprHD":
            raise ChdError("not a CHD file")
        version = struct.unpack(">I", head[12:16])[0]
        if version != 5:
            raise ChdError(f"CHD version {version} isn't supported")
        self.codecs = [head[16 + 4 * i:20 + 4 * i] for i in range(4)]
        self.logical, self.mapoffset, self.metaoffset = struct.unpack(">QQQ", head[32:56])
        self.hunkbytes, self.unitbytes = struct.unpack(">II", head[56:64])
        if head[104:124].strip(b"\x00"):
            raise ChdError("CHDs that need a parent aren't supported")
        self.hunks = (self.logical + self.hunkbytes - 1) // self.hunkbytes
        self._map = self._read_map()
        self._cache = {}

    def _read_map(self):
        fh = self._fh
        fh.seek(self.mapoffset)
        if self.codecs[0] == b"\x00\x00\x00\x00":
            # Uncompressed CHD: one 4-byte hunk offset each.
            raw = fh.read(4 * self.hunks)
            return [(NONE, self.hunkbytes, struct.unpack(">I", raw[i:i + 4])[0] * self.hunkbytes)
                    for i in range(0, len(raw), 4)]
        header = fh.read(16)
        length = struct.unpack(">I", header[0:4])[0]
        datastart = int.from_bytes(header[4:10], "big")
        lengthbits, selfbits, parentbits = header[12], header[13], header[14]
        bits = _Bits(fh.read(length))
        tree = _Huffman(16, 8)
        tree.import_rle(bits)
        kinds = []
        last = repeat = 0
        for _ in range(self.hunks):
            if repeat:
                kinds.append(last)
                repeat -= 1
                continue
            value = tree.decode(bits)
            if value == RLE_SMALL:
                kinds.append(last)
                repeat = 2 + tree.decode(bits)
            elif value == RLE_LARGE:
                kinds.append(last)
                repeat = 2 + 16 + (tree.decode(bits) << 4)
                repeat += tree.decode(bits)
            else:
                kinds.append(value)
                last = value
        entries = []
        cur = datastart
        last_self = last_parent = 0
        for hunk, kind in enumerate(kinds):
            length = offset = 0
            if kind in (TYPE_0, TYPE_1, TYPE_2, TYPE_3):
                length = bits.read(lengthbits)
                offset = cur
                cur += length
                bits.read(16)                        # crc
            elif kind == NONE:
                length = self.hunkbytes
                offset = cur
                cur += length
                bits.read(16)
            elif kind == SELF:
                offset = last_self = bits.read(selfbits)
            elif kind == PARENT:
                offset = last_parent = bits.read(parentbits)
            elif kind in (SELF_0, SELF_1):
                if kind == SELF_1:
                    last_self += 1
                kind, offset = SELF, last_self
            elif kind == PARENT_SELF:
                kind = PARENT
                offset = last_parent = hunk * self.hunkbytes // self.unitbytes
            elif kind in (PARENT_0, PARENT_1):
                if kind == PARENT_1:
                    last_parent += self.hunkbytes // self.unitbytes
                kind, offset = PARENT, last_parent
            entries.append((kind, length, offset))
        return entries

    def hunk(self, index, depth=0):
        if index in self._cache:
            return self._cache[index]
        if index >= len(self._map) or depth > 8:
            raise ChdError("hunk out of range")
        kind, length, offset = self._map[index]
        if kind == SELF:
            data = self.hunk(offset, depth + 1)
        elif kind == PARENT:
            raise ChdError("CHDs that need a parent aren't supported")
        elif kind == NONE:
            self._fh.seek(offset)
            data = self._fh.read(self.hunkbytes) if offset else bytes(self.hunkbytes)
        else:
            codec = CODECS.get(self.codecs[kind])
            if codec is None:
                raise ChdError(f"codec {self.codecs[kind]!r} isn't supported")
            self._fh.seek(offset)
            try:
                data = codec(self._fh.read(length), self.hunkbytes)
            except Exception as exc:
                raise ChdError(f"{type(exc).__name__}: {exc}") from exc
        if len(self._cache) > 32:
            self._cache.clear()
        self._cache[index] = data
        return data

    def read_raw(self, offset, size):
        out = bytearray()
        while size > 0 and offset < self.logical:
            index, within = divmod(offset, self.hunkbytes)
            chunk = self.hunk(index)[within:within + size]
            if not chunk:
                break
            out += chunk
            offset += len(chunk)
            size -= len(chunk)
        return bytes(out)

    def metadata(self):
        """[(tag, bytes)] from the metadata chain."""
        out, offset = [], self.metaoffset
        for _ in range(256):
            if not offset:
                break
            self._fh.seek(offset)
            entry = self._fh.read(16)
            if len(entry) < 16:
                break
            length = int.from_bytes(entry[5:8], "big")
            out.append((entry[:4], self._fh.read(length)))
            offset = struct.unpack(">Q", entry[8:16])[0]
        return out


class ChdSource:
    """ISO-style access (2048-byte user-data sectors) to a CHD disc image, for
    romscan's ISO9660 reader. For CDs it follows the first track's layout from
    the CHT2 metadata."""

    def __init__(self, path):
        self.chd = Chd(path)
        self.media = None
        try:
            self._read_layout()
        except ChdError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise ChdError(f"{type(exc).__name__}: {exc}") from exc

    def _read_layout(self):
        self._frame_offset = 0      # bytes into a CD frame where user data starts
        self._first_frame = 0
        for tag, data in self.chd.metadata():
            if tag == b"DVD ":
                self.media = "dvd"
                break
            if tag in (b"CHT2", b"CHTR"):
                self.media = "cd"
                text = data.rstrip(b"\x00").decode("ascii", "replace")
                fields = dict(re.findall(r"(\w+):(\S+)", text))
                kind = fields.get("TYPE", "MODE1")
                self._frame_offset = {"MODE1_RAW": 16, "MODE2_RAW": 24,
                                      "MODE2_FORM1": 0, "MODE2_FORM_MIX": 0,
                                      "MODE2": 8}.get(kind, 0)
                if fields.get("PGTYPE", "").startswith("V"):
                    self._first_frame = int(fields.get("PREGAP", "0") or 0)
                break
        if self.media is None:
            raise ChdError("not a CD or DVD image")

    def read(self, offset, size):
        if self.media == "dvd":
            return self.chd.read_raw(offset, size)
        out = bytearray()
        while size > 0:
            lba, within = divmod(offset, SECTOR)
            take = min(size, SECTOR - within)
            frame = (self._first_frame + lba) * CD_FRAME
            chunk = self.chd.read_raw(frame + self._frame_offset + within, take)
            if not chunk:
                break
            out += chunk
            offset += len(chunk)
            size -= len(chunk)
        return bytes(out)

    def close(self):
        self.chd.close()
