# -*- coding: utf-8 -*-
# 纯 Python .mo 编译器: 读取 .po 中已译条目, 写入 gettext .mo 二进制文件
# 用法: python _compile_mo.py <input.po> <output.mo>
import re, struct, sys, os

LE = "<IIIIIII"  # 魔数/版本/条目数/原串表偏移/译文表偏移/哈希大小/哈希偏移

def unescape(s):
    # 还原 po 字符串字面量中的转义
    return (s.replace('\\"', '"').replace("\\n", "\n")
             .replace("\\t", "\t").replace("\\\\", "\\"))

def parse_po(path):
    """解析 .po, 返回 [(msgid, msgstr)] 已译条目(含 header, 跳过模糊与空译文)"""
    entries = []
    for block in re.split(r"\n\s*\n", open(path, encoding="utf-8").read()):
        fuzzy = bool(re.search(r"^#,.*fuzzy", block, re.M))
        mid = mpl = mstr = None
        kind = None
        for line in block.splitlines():
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^msgid "((?:[^"\\]|\\.)*)"', line)
            if m:
                kind, mid = "id", m.group(1)
                continue
            m = re.match(r'^msgid_plural "((?:[^"\\]|\\.)*)"', line)
            if m:
                mpl = m.group(1)
                continue
            m = re.match(r'^msgstr "((?:[^"\\]|\\.)*)"', line)
            if m:
                kind, mstr = "str", m.group(1)
                continue
            m = re.match(r'^msgstr\[\d+\] "((?:[^"\\]|\\.)*)"', line)
            if m:
                mstr = (mstr + "\0" if mstr else "") + m.group(1)
                continue
            m = re.match(r'^"(?:[^"\\]|\\.)*"$', line)
            if m and kind:
                seg = re.match(r'^"((?:[^"\\]|\\.)*)"$', line).group(1)
                if kind == "id":
                    mid += seg
                else:
                    mstr += seg
        if mid is None or mstr is None or fuzzy:
            continue
        if mpl is not None and mstr:
            # 复数: msgid\0msgid_plural => 译文各形式(\0 连接)
            parts = mstr.split("\0")
            if len(parts) < 2 or any(not p for p in parts):
                continue
            key = unescape(mid) + "\0" + unescape(mpl)
            val = "\0".join(unescape(p) for p in parts)
        elif mstr:
            key = unescape(mid)
            val = unescape(mstr)
        else:
            continue
        entries.append((key, val))
    return entries

def write_mo(entries, path):
    # 按 msgid 排序(gettext 约定), 构造键表/值表
    entries = sorted(entries)
    ids = b"\0".join(k.encode("utf-8") for k, _ in entries) + b"\0"
    strs = b"\0".join(v.encode("utf-8") for _, v in entries) + b"\0"
    n = len(entries)
    keystart = 7 * 4 + 16 * n
    valstart = keystart + len(ids)
    # 每个键/值的位置(相对文件头)与长度表
    # 键/值相对 .mo 文件起始的绝对偏移
    off = []
    p = keystart
    for k, _ in entries:
        kb = k.encode("utf-8")
        off.append((len(kb), p))
        p += len(kb) + 1
    koffs = struct.pack("<" + "II" * n, *[x for o in off for x in (o[0], o[1])])
    off = []
    p = valstart
    for _, v in entries:
        vb = v.encode("utf-8")
        off.append((len(vb), p))
        p += len(vb) + 1
    voffs = struct.pack("<" + "II" * n, *[x for o in off for x in (o[0], o[1])])
    header = struct.pack(LE, 0x950412de, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0)
    with open(path, "wb") as f:
        f.write(header + koffs + voffs + ids + strs)
    return n

if __name__ == "__main__":
    po, mo = sys.argv[1], sys.argv[2]
    ents = parse_po(po)
    n = write_mo(ents, mo)
    print("OK %s -> %s (%d 条, 含 header)" % (po, mo, n))
