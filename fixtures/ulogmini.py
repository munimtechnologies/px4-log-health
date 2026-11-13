"""ulogmini 0.1.0: a small reader for PX4 ULog flight logs that needs only the Python standard library.

It reads the subset a log screen needs: the file header, message formats (including arrays, nested types and
_padding fields), info messages, parameters, subscriptions, data messages, logged text messages, tagged
messages and dropouts. Corrupt stretches are skipped by searching for the next sync marker, the way pyulog
does; a message cut off at the end of the file stops parsing and sets `truncated`.

The interface mirrors the part of pyulog.ULog that MV2's log tools use: data_list (objects with name,
multi_id, data = {field: list}), msg_info_dict, initial_parameters, changed_parameters, logged_messages
(objects with timestamp, log_level, message), start_timestamp and last_timestamp. Values are plain Python
ints and floats (float32 fields are widened exactly, as numpy's .item() does). It is a stand-in for jobs
that run without network access, not a replacement for pyulog (BSD-3-Clause), which remains the reference.

Format reference: https://docs.px4.io/main/en/dev_log/ulog_file_format.html
Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
"""
import struct

__version__ = "0.1.0"

MAGIC = b"ULog\x01\x12\x35"
SYNC = bytes([0x2F, 0x73, 0x13, 0x20, 0x25, 0x0C, 0xBB, 0x12])
# base type -> (struct code, size). char and bool are read as unsigned bytes inside data messages.
TYPES = {"int8_t": ("b", 1), "uint8_t": ("B", 1), "int16_t": ("h", 2), "uint16_t": ("H", 2),
         "int32_t": ("i", 4), "uint32_t": ("I", 4), "int64_t": ("q", 8), "uint64_t": ("Q", 8),
         "float": ("f", 4), "double": ("d", 8), "bool": ("B", 1), "char": ("B", 1)}
KNOWN = set(b"BFIMPQARDLCSO")
MAX_NESTING = 10
_HDR = struct.Struct("<HB")
_U16 = struct.Struct("<H")
_U64 = struct.Struct("<Q")


def split_type(t):
    """'float[4]' -> ('float', 4); 'uint64_t' -> ('uint64_t', None). Raises ValueError on a bad count."""
    t = t.strip()
    if t.endswith("]") and "[" in t:
        base, _, n = t[:-1].partition("[")
        return base, int(n)
    return t, None


def parse_format(text):
    """'name:type field;type[n] field;...' -> (name, [(base type, field, count or None)])."""
    text = text.rstrip("\x00")
    name, sep, rest = text.partition(":")
    if not sep or not name:
        raise ValueError("bad format %r" % text[:60])
    fields = []
    for item in rest.split(";"):
        item = item.strip()
        if not item:
            continue
        ftype, _, fname = item.partition(" ")
        fname = fname.strip()
        if not fname:
            raise ValueError("bad field %r" % item)
        base, count = split_type(ftype)
        fields.append((base, fname, count))
    return name, fields


def layout(formats, name, prefix="", depth=0):
    """Flatten a format into [(struct code, size, field name)]; padding gives ('x', bytes, None)."""
    if depth > MAX_NESTING:
        raise ValueError("nesting too deep in %s" % name)
    out = []
    for base, fname, count in formats[name]:
        reps = 1 if count is None else count
        if base in TYPES:
            code, size = TYPES[base]
            if fname.startswith("_padding"):
                out.append(("x", size * reps, None))
                continue
            for k in range(reps):
                out.append((code, size, prefix + fname + ("" if count is None else "[%d]" % k)))
        elif base in formats:
            for k in range(reps):
                sub = prefix + fname + ("" if count is None else "[%d]" % k) + "."
                out.extend(layout(formats, base, sub, depth + 1))
        else:
            raise ValueError("unknown type %r in %s" % (base, name))
    return out


def compile_layout(items):
    """-> (struct.Struct, field names, bytes of padding at the very end of the message)."""
    codes, names, tail = [], [], 0
    for code, size, fname in items:
        if fname is None:
            codes.append("%dx" % size)
            tail += size
        else:
            codes.append(code)
            names.append(fname)
            tail = 0
    return struct.Struct("<" + "".join(codes)), names, tail


def decode_value(ftype, raw):
    """Decode an info or parameter value. char arrays become str (trailing NULs dropped)."""
    base, count = split_type(ftype)
    if base == "char":
        return raw.decode("utf-8", "replace").rstrip("\x00")
    if base not in TYPES:
        return None
    code, size = TYPES[base]
    reps = 1 if count is None else count
    if base == "bool":
        code = "?"
    if len(raw) < size * reps:
        return None
    vals = struct.unpack_from("<%d%s" % (reps, code), raw, 0)
    return vals[0] if count is None else list(vals)


class Data:
    """One subscription (topic + multi_id) with its rows as columns."""

    def __init__(self, name, multi_id, msg_id, field_names):
        self.name, self.multi_id, self.msg_id = name, multi_id, msg_id
        self.field_names = field_names
        self.rows = []
        self.short_rows = 0
        self.data = {}

    def finish(self):
        n = len(self.rows)
        cols = list(zip(*self.rows)) if n else [() for _ in self.field_names]
        self.data = {f: list(c) for f, c in zip(self.field_names, cols)}
        self.rows = None
        return n


class MessageLogging:
    __slots__ = ("log_level", "timestamp", "message", "tag")

    def __init__(self, log_level, timestamp, message, tag=None):
        self.log_level, self.timestamp, self.message, self.tag = log_level, timestamp, message, tag


class ULog:
    """ULog(path, message_name_filter_list=None, disable_str_exceptions=True): parse the whole file."""

    def __init__(self, path, message_name_filter_list=None, disable_str_exceptions=True):
        self.filter = set(message_name_filter_list) if message_name_filter_list else None
        self.msg_info_dict = {}
        self.initial_parameters = {}
        self.changed_parameters = []
        self.logged_messages = []
        self.logged_messages_tagged = []
        self.dropouts = []
        self.data_list = []
        self.formats = {}
        self.file_version = None
        self.start_timestamp = 0
        self.last_timestamp = 0
        self.corruptions = 0
        self.truncated = False
        self.short_rows = 0
        with open(path, "rb") as f:
            buf = f.read()
        self._parse(buf)

    def _parse(self, buf):
        if len(buf) < 16 or buf[:7] != MAGIC:
            raise ValueError("not a ULog file")
        self.file_version = buf[7]
        self.start_timestamp = _U64.unpack_from(buf, 8)[0]
        formats, layouts, subs, all_subs = self.formats, {}, {}, []
        in_data, last = False, 0
        pos, n = 16, len(buf)
        hdr, u16, u64 = _HDR.unpack_from, _U16.unpack_from, _U64.unpack_from
        while pos + 3 <= n:
            size, mtype = hdr(buf, pos)
            if mtype not in KNOWN:
                self.corruptions += 1
                k = buf.find(SYNC, pos + 1)
                if k < 0:
                    break
                pos = k + len(SYNC)
                continue
            bs = pos + 3
            end = bs + size
            if end > n:
                self.truncated = True
                break
            if mtype == 0x44:  # D: data
                if size >= 2:
                    sub = subs.get(u16(buf, bs)[0])
                    if sub is not None:
                        st, obj, tsi, pad = sub
                        avail = size - 2
                        vals = None
                        if avail >= st.size:
                            vals = st.unpack_from(buf, bs + 2)
                        elif st.size - avail <= pad:
                            vals = st.unpack(buf[bs + 2:end] + bytes(st.size - avail))
                        else:
                            obj.short_rows += 1
                        if vals is not None:
                            obj.rows.append(vals)
                            if tsi is not None and vals[tsi] > last:
                                last = vals[tsi]
            elif mtype == 0x41:  # A: add subscription
                in_data = True
                if size >= 3:
                    multi_id, msg_id = buf[bs], u16(buf, bs + 1)[0]
                    name = buf[bs + 3:end].decode("utf-8", "replace").rstrip("\x00")
                    lay = None
                    if self.filter is None or name in self.filter:
                        lay = layouts.get(name)
                        if lay is None and name in formats:
                            try:
                                lay = compile_layout(layout(formats, name))
                            except (ValueError, KeyError, struct.error):
                                lay = False
                            layouts[name] = lay
                    if lay:
                        st, names, pad = lay
                        obj = Data(name, multi_id, msg_id, names)
                        all_subs.append(obj)
                        tsi = names.index("timestamp") if "timestamp" in names else None
                        subs[msg_id] = (st, obj, tsi, pad)
                    else:
                        subs.pop(msg_id, None)
            elif mtype == 0x46:  # F: format
                try:
                    fname, fields = parse_format(buf[bs:end].decode("utf-8", "replace"))
                    formats[fname] = fields
                except ValueError:
                    pass
            elif mtype in (0x49, 0x50):  # I: info, P: parameter
                if size >= 1 and buf[bs] <= size - 1:
                    kl = buf[bs]
                    key = buf[bs + 1:bs + 1 + kl].decode("utf-8", "replace")
                    ftype, _, kname = key.partition(" ")
                    try:
                        val = decode_value(ftype, buf[bs + 1 + kl:end])
                    except (ValueError, struct.error):
                        val = None
                    if mtype == 0x49:
                        self.msg_info_dict[kname] = val
                    elif not in_data:
                        self.initial_parameters[kname] = val
                    else:
                        self.changed_parameters.append((last, kname, val))
            elif mtype == 0x4C:  # L: logged text
                if size >= 9:
                    self.logged_messages.append(MessageLogging(
                        buf[bs], u64(buf, bs + 1)[0], buf[bs + 9:end].decode("utf-8", "replace").rstrip("\x00")))
            elif mtype == 0x43:  # C: tagged logged text
                if size >= 11:
                    self.logged_messages_tagged.append(MessageLogging(
                        buf[bs], u64(buf, bs + 3)[0], buf[bs + 11:end].decode("utf-8", "replace").rstrip("\x00"),
                        u16(buf, bs + 1)[0]))
            elif mtype == 0x52:  # R: remove subscription
                if size >= 3:
                    subs.pop(u16(buf, bs + 1)[0], None)
            elif mtype == 0x4F:  # O: dropout
                if size >= 2:
                    self.dropouts.append((last, u16(buf, bs)[0]))
            # B (flags), M (multi info), Q (default parameter) and S (sync) carry nothing this reader needs.
            pos = end
        self.last_timestamp = last
        kept = []
        for obj in all_subs:
            self.short_rows += obj.short_rows
            if obj.finish():
                kept.append(obj)
        self.data_list = kept
