# -*- coding: utf-8 -*-
# .po 文件未翻译/fuzzy 条目的提取与回填工具
# 用法:
#   python po_tool.py extract <po_path>   # 打印需翻译条目(自文件末尾往前)
#   python po_tool.py apply   <po_path> <json_path>  # 按行号回填译文并去 fuzzy
import sys, json, re, os

def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")

def unescape(s):
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

# 解析出一个 msgid "..." 可能跨多行的完整值
def grab_quoted(lines, i):
    # lines[i] 形如: msgid "..." 或 msgstr[1] "..."  (只处理该行的引号内)
    m = re.match(r'^\s*msg(?:id|str(?:\[\d+\])?)\s+"((?:[^"\\]|\\.)*)"\s*$', lines[i])
    if not m:
        return None, i
    val = m.group(1)
    j = i + 1
    while j < len(lines) and lines[j].startswith('"'):
        mm = re.match(r'^"((?:[^"\\]|\\.)*)"\s*$', lines[j])
        if not mm:
            break
        val += mm.group(1)
        j += 1
    return val, j  # 返回完整值 和 结束行下标(不含)

def parse_entries(lines):
    """返回条目列表: 每项含 start(块起始行号,1-based), fuzzy_line, msgid_line,
       msgid 值, msgstr 行范围, 当前 msgstr 值列表"""
    entries = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if re.match(r'^msgid\s+"', line):
            # 跳过头部条目 msgid ""
            val, j = grab_quoted(lines, i)
            if val is None:
                i += 1
                continue
            if val == "":
                i = j
                continue
            # 回退到块起始: 向上找该块第一个连续行(上一个空行之后的下一行)
            start = i
            k = i - 1
            while k >= 0 and lines[k].strip() != "":
                start = k
                k -= 1
            # 块结束: 从 j 之后找空行
            end = j
            while end < n and lines[end].strip() != "":
                end += 1
            block = lines[start:end]
            fuzzy_line = None
            for idx, bl in enumerate(block):
                if re.match(r'^#,.*\bfuzzy\b', bl):
                    fuzzy_line = start + idx
            msgid = unescape(val)
            # 收集 msgstr: 记录其完整行范围(含多行续行、plural), 便于整段删除
            msgstr_vals = []
            ms_start = None      # 0-based 首行
            ms_end = None        # 0-based 结束(不含)
            p = j
            while p < end:
                lm = re.match(r'^\s*msgstr(?:\[\d+\])?\s+"', lines[p])
                if lm:
                    v, p2 = grab_quoted(lines, p)
                    if ms_start is None:
                        ms_start = p
                    ms_end = p2
                    msgstr_vals.append(v)
                    p = p2
                else:
                    p += 1
            msgstr_text = "".join(msgstr_vals)
            entries.append({
                "start": start + 1,           # 1-based
                "end": end,                    # 1-based 块结束(空行号或文件尾+1)
                "fuzzy_line": fuzzy_line,      # 1-based or None
                "msgid_line": i + 1,
                "msgid": msgid,
                "ms_start": ms_start,         # 0-based 或 None
                "ms_end": ms_end,             # 0-based(不含) 或 None
                "msgstr": unescape(msgstr_text) if msgstr_text else "",
                "plural": len(msgstr_vals) > 1,
            })
            # 逐行扫描(不跳跃), 确保相邻的重复 msgid 条目都能被捕获
            i += 1
        else:
            i += 1
    return entries

def cmd_extract(po, outpath=None):
    lines = read_lines(po)
    entries = parse_entries(lines)
    # 需要处理: 空译文 或 fuzzy
    todo = []
    for e in entries:
        if e["msgstr"].strip() == "" or e["fuzzy_line"] is not None:
            todo.append(e)
    # 从文件末尾往前(逐行 NDJSON)
    todo.sort(key=lambda x: -x["msgid_line"])
    out_lines = ["NEED_COUNT=%d" % len(todo)]
    for e in todo:
        out_lines.append(json.dumps({
            "msgid_line": e["msgid_line"],
            "fuzzy": e["fuzzy_line"] is not None,
            "plural": e["plural"],
            "msgid": e["msgid"],
            "cur": e["msgstr"],
        }, ensure_ascii=False))
    if outpath:
        with open(outpath, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out_lines) + "\n")
    else:
        sys.stdout.write("\n".join(out_lines) + "\n")

def cmd_apply(po, jpath):
    with open(jpath, encoding="utf-8") as f:
        data = json.load(f)
    # data: list of {"msgid_line": int, "translation": str 或 [str,...]}
    # 统一转义: 反斜杠、双引号、换行
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    lines = read_lines(po)
    entries = parse_entries(lines)
    by_line = {e["msgid_line"]: e for e in entries}

    to_del = set()      # 待删除的原始行号(0-based): 原 msgstr 行 + fuzzy 标记行
    inserts = {}        # 原始行号(0-based) -> 要插在该行之后的新行列表
    applied = 0
    for item in data:
        e = by_line.get(item["msgid_line"])
        if not e:
            print("WARN no entry at msgid_line", item["msgid_line"])
            continue
        # 情形A: keep=True —— 已有合格译文, 仅去除 fuzzy 标记, 保留原 msgstr 行不动
        # 注意: fuzzy_line 为 0-based, to_del 亦为 0-based, 直接加入
        if item.get("keep"):
            if e["fuzzy_line"] is not None:
                to_del.add(e["fuzzy_line"])
            applied += 1
            continue
        # 情形B: 提供新译文 —— 删除原 msgstr 行(及 fuzzy 行)并插入新译文
        trans = item["translation"]
        if isinstance(trans, list):
            new_lines = ['msgstr[%d] "%s"' % (k, esc(t)) for k, t in enumerate(trans)]
        else:
            new_lines = ['msgstr "%s"' % esc(trans)]
        # 定位 msgid 值结束行(0-based): msgid 字符串可能跨多行, 直到不再以 " 开头
        p = e["msgid_line"] - 1
        while p + 1 < len(lines) and lines[p + 1].startswith('"'):
            p += 1
        inserts[p] = new_lines
        # 标记原 msgstr 整个行范围(含多行续行)删除
        if e["ms_start"] is not None:
            for ml in range(e["ms_start"], e["ms_end"]):
                to_del.add(ml)
        # 若有 fuzzy 标记行则一并删除(复核通过即去标记)
        if e["fuzzy_line"] is not None:
            to_del.add(e["fuzzy_line"])
        applied += 1

    # 原子安全: 只要有任一条目未匹配到, 立即中止且【不写文件】, 避免部分修改造成损坏
    if applied < len(data):
        print("ABORT: matched %d/%d, file left UNCHANGED" % (applied, len(data)))
        return

    # 单次遍历重建文件: 所有坐标均来自原始文件, 不存在行号错位
    new = []
    for idx, line in enumerate(lines):
        if idx in to_del:
            continue
        new.append(line)
        if idx in inserts:
            new.extend(inserts[idx])
    with open(po, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(new))
    print("APPLIED=%d" % applied)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "extract":
        cmd_extract(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "apply":
        cmd_apply(sys.argv[2], sys.argv[3])
    else:
        print("unknown cmd", cmd)
