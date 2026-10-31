# -*- coding: utf-8 -*-
"""
损坏图片清理 - 实际写入阶段（Windows / Python 3）
目标目录: C:\\Users\\Administrator\\Desktop\\AI合同审核\\data\\e9-opinion-export\\

功能:
1. 递归扫描目录下全部 .md 文件，定位正文中的内嵌图片语法
   ![任意alt](data:image/<mime>;base64,<data>)。
2. 对每张内嵌图片做严格校验（不依赖 base64 长度判断）:
   - Base64 严格解码（容忍缺失 padding，不容忍非法字符）；
   - 魔数与声明 mime 一致性核对；
   - JPEG: SOI/EOI、标记结构遍历（APP/COM 载荷视为不透明，避免 EXIF 缩略图
     误判）、SOS 熵编码段扫描、重复流拼接检测；
   - PNG : 签名、逐分块 CRC(zlib.crc32) 校验至 IEND、IHDR/IDAT/IEND 结构、
           重复流拼接检测（IEND 后出现第二段完整 PNG 流）；
   - 对“存疑”与“拟删除”图片追加 Pillow 实际解码尝试（load() 全解码）。
3. 确认损坏的图片: 整段删除图片语法，原位替换为
   **<span style="color:red">图片损坏</span>**
4. 幂等: 已替换处不再含 data URI，重复运行不会重复标记/重复删除；
   仅在内容变化时写文件；原子写入（同目录临时文件 + os.replace）。
5. 输出 _damaged_images.txt 明细清单（UTF-8，TSV）与运行日志（UTF-8）。

判定口径（保守）:
- 删除: base64 解码失败 / 0 字节 / JPEG 截断缺 EOI / JPEG 结构错误 /
        PNG 截断 / PNG CRC 错误 / PNG 结构错误 / 解码失败 /
        确认的重复流拼接（第二段完整流）。
- 保留并标注“复核通过-保留”: 结构完整可解码但存在非致命异常（如 IEND/EOI
  后尾随冗余字节、魔数与声明不符但实际可解码），以及干跑点名存疑文件中的
  图片（须复核后标注）。
- 其余正常图片不入清单。

用法: python fix_damaged_images.py [--workers N] [--limit N] [--dir DIR]
"""

import os
import re
import io
import sys
import time
import base64
import zlib
import argparse
import traceback
import collections
from concurrent.futures import ProcessPoolExecutor

# ----------------------------- 配置 -----------------------------
DATA_DIR = r'C:\Users\Administrator\Desktop\AI合同审核\data\e9-opinion-export'
WORK_ROOT = r'C:\Users\Administrator\Desktop\AI合同审核'
LOG_PATH = os.path.join(WORK_ROOT, '_fix_damaged_images_run.log')

REPLACE_MARKER = '**<span style="color:red">图片损坏</span>**'

# 干跑阶段点名的“PNG 有合法 IEND 但数据存疑”文件（basename），
# 其中图片即使校验全部通过，也必须复核并在清单标注“复核通过-保留”。
DRY_RUN_SUSPECT_FILES = {
    'BQ2023HT01056.md',
    'BQ2025BM00285.md',
}

# 图片语法: ![alt](data:image/mime;base64,<b64> [可选 "title"])
IMG_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\('
    r'\s*data:image/(?P<mime>[\w.+-]+)\s*;\s*base64\s*,\s*'
    r'(?P<b64>[A-Za-z0-9+/=\s]*?)'
    r'\s*(?:"[^"]*")?\)'
)
DATA_URI_RE = re.compile(r'data:image/[\w.+-]+\s*;\s*base64\s*,')
B64CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')

PNG_SIG = b'\x89PNG\r\n\x1a\n'
BOM = b'\xef\xbb\xbf'


# ----------------------------- Base64 / 魔数 -----------------------------

def b64_decode(b64text):
    """返回 (bytes|None, note|None, fatal_reason|None)"""
    cleaned = re.sub(r'\s+', '', b64text)
    if not cleaned:
        return None, None, 'Base64 解码失败（空数据）'
    try:
        return base64.b64decode(cleaned, validate=True), None, None
    except Exception:
        pass
    padded = cleaned + '=' * ((-len(cleaned)) % 4)
    try:
        data = base64.b64decode(padded, validate=True)
        return data, 'base64 缺失 padding（容错解码）', None
    except Exception as e:
        return None, None, f'Base64 解码失败（{type(e).__name__}）'


def sniff_magic(data):
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if data.startswith(PNG_SIG):
        return 'png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data.startswith(b'BM'):
        return 'bmp'
    return 'unknown'


MIME_FAMILY = {
    'jpeg': {'jpeg', 'jpg', 'pjpeg', 'x-citrix-jpeg'},
    'png': {'png', 'x-png', 'apng'},
    'gif': {'gif'},
    'webp': {'webp'},
    'bmp': {'bmp', 'x-ms-bmp', 'x-bmp'},
}


def mime_matches_magic(mime, magic):
    return mime.split('/')[-1].lower() in MIME_FAMILY.get(magic, set())


# ----------------------------- JPEG 结构校验 -----------------------------

def _jpeg_walk(data):
    """标记结构遍历 + SOS 熵段扫描。APP/COM 等载荷按长度跳过（不透明）。
    返回 (reason|None, eoi_end):
      - (None, len(data))            结构完整且 EOI 位于流末尾
      - ('EOI_MID', eoi_end < n)     EOI 之后仍有多余数据（交上级判定）
      - (reason, ...)                致命结构问题"""
    n = len(data)
    if n < 2 or data[:2] != b'\xff\xd8':
        return 'JPEG SOI 缺失', 0
    pos = 2
    while pos < n:
        if data[pos] != 0xFF:
            return 'JPEG 结构错误（缺少 0xFF 标记前缀）', 0
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            return 'JPEG 截断缺 EOI', 0
        marker = data[pos]
        pos += 1
        if marker == 0xD9:  # EOI
            if pos != n:
                return 'EOI_MID', pos
            return None, pos
        if marker == 0xD8:
            return 'JPEG 结构错误（流中出现 SOI）', 0
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue  # 无长度段（TEM / RSTn）
        if pos + 2 > n:
            return 'JPEG 截断（段头不完整）', 0
        seglen = int.from_bytes(data[pos:pos + 2], 'big')
        if seglen < 2 or pos + seglen > n:
            return 'JPEG 截断（段长度越界）', 0
        if marker == 0xDA:  # SOS: 头部之后是熵编码数据
            pos += seglen
            while pos < n:
                b = data[pos]
                if b != 0xFF:
                    pos += 1
                    continue
                j = pos + 1
                if j >= n:
                    return 'JPEG 截断缺 EOI', 0
                m2 = data[j]
                if m2 == 0x00:          # 0xFF00 字节填充
                    pos += 2
                    continue
                if m2 == 0xFF:          # 填充 FF
                    pos += 1
                    continue
                if 0xD0 <= m2 <= 0xD7:  # RSTn
                    pos += 2
                    continue
                break                   # 真实标记，pos 停在 0xFF 交外层
            continue
        pos += seglen
    return 'JPEG 截断缺 EOI', 0


def _tail_has_valid_jpeg(tail):
    i = tail.find(b'\xff\xd8\xff')
    while i != -1:
        reason, _end = _jpeg_walk(tail[i:])
        if reason is None:
            return True
        i = tail.find(b'\xff\xd8\xff', i + 1)
    return False


def check_jpeg_structure(data):
    """返回 (fatal_reason|None, anomalies[list], dup_confirmed)"""
    anomalies = []
    if len(data) < 4:
        return 'JPEG 截断缺 EOI', anomalies, False
    if data[:2] != b'\xff\xd8':
        return 'JPEG SOI 缺失', anomalies, False
    if not data.endswith(b'\xff\xd9'):
        return 'JPEG 截断缺 EOI', anomalies, False

    reason, eoi_end = _jpeg_walk(data)
    if reason is None:
        return None, anomalies, False
    if reason == 'EOI_MID':
        tail = data[eoi_end:]
        if _tail_has_valid_jpeg(tail):
            return None, anomalies, True  # 确认重复流拼接（上级按此删除）
        anomalies.append(f'EOI 后尾随 {len(tail)} 字节冗余数据（非完整第二流）')
        return None, anomalies, False
    return reason, anomalies, False


# ----------------------------- PNG 结构校验 -----------------------------

def check_png_structure(data):
    """逐分块 CRC 校验至 IEND。返回 (fatal_reason|None, anomalies, dup_confirmed)"""
    anomalies = []
    if not data.startswith(PNG_SIG):
        return 'PNG 签名错误', anomalies, False
    pos = 8
    n = len(data)
    seen_iend = False
    seen_ihdr = False
    idat_len = 0
    while pos < n:
        if pos + 8 > n:
            return 'PNG 截断（分块头不完整）', anomalies, False
        length = int.from_bytes(data[pos:pos + 4], 'big')
        ctype = data[pos + 4:pos + 8]
        if not re.match(rb'^[A-Za-z]{4}$', ctype):
            return 'PNG 结构错误（分块类型非法）', anomalies, False
        if length > 0x7FFFFFFF:
            return 'PNG 结构错误（分块长度非法）', anomalies, False
        end = pos + 12 + length
        if end > n:
            return 'PNG 截断（分块数据不完整）', anomalies, False
        cdata = data[pos + 8:pos + 8 + length]
        crc_stored = int.from_bytes(data[pos + 8 + length:end], 'big')
        crc_calc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
        if crc_calc != crc_stored:
            return f'PNG CRC 错误（块 {ctype.decode("ascii", "replace")}）', anomalies, False
        if not seen_ihdr:
            if ctype != b'IHDR':
                return 'PNG 结构错误（缺少 IHDR）', anomalies, False
            if length != 13:
                return 'PNG 结构错误（IHDR 长度错误）', anomalies, False
            seen_ihdr = True
        elif ctype == b'IHDR':
            return 'PNG 结构错误（重复 IHDR）', anomalies, False
        elif ctype == b'IDAT':
            idat_len += length
        elif ctype == b'IEND':
            if length != 0:
                return 'PNG 结构错误（IEND 长度非零）', anomalies, False
            seen_iend = True
            pos = end
            break
        pos = end
    if not seen_iend:
        return 'PNG 截断（缺少 IEND）', anomalies, False
    if idat_len == 0:
        return 'PNG 结构错误（缺少 IDAT）', anomalies, False

    trailing = data[pos:]
    if trailing:
        sig_at = trailing.find(PNG_SIG)
        if sig_at != -1:
            reason2, _a2, _d2 = check_png_structure(trailing[sig_at:])
            if reason2 is None:
                return None, anomalies, True  # 确认重复流拼接
            anomalies.append(f'IEND 后尾随 {len(trailing)} 字节（含无效 PNG 签名片段）')
        else:
            anomalies.append(f'IEND 后尾随 {len(trailing)} 字节')
    return None, anomalies, False


# ----------------------------- 解码尝试 -----------------------------

def pil_decode(data):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            im.load()
        return True, f'Pillow 解码成功（{fmt}）'
    except Exception as e:
        return False, f'Pillow 解码失败（{type(e).__name__}: {e}）'


# ----------------------------- 单图判定 -----------------------------

def judge_image(mime, b64text, must_review, pre_note=''):
    """返回 dict: status(delete|keep_note|keep_silent), reason, action, size"""
    out = {'status': 'keep_silent', 'reason': pre_note, 'action': '', 'size': 0}
    if pre_note:
        must_review = True

    data, note, fatal = b64_decode(b64text)
    if fatal:
        out.update(status='delete', reason=(pre_note + '；' if pre_note else '') + fatal,
                   action='已删除替换红字')
        return out
    out['size'] = len(data)
    if len(data) == 0:
        out.update(status='delete', reason='Base64 解码后为 0 字节', action='已删除替换红字')
        return out

    def pack(status, reason, action):
        r = (pre_note + '；' if pre_note else '') + reason
        if note:
            r += f'；{note}'
        out.update(status=status, reason=r, action=action)
        return out

    magic = sniff_magic(data)

    # 声明 mime 与魔数不符：保守处理——可解码则保留并标注，不可解码则删除
    if magic != 'unknown' and not mime_matches_magic(mime, magic):
        ok, pil_note = pil_decode(data)
        if ok:
            return pack('keep_note',
                        f'复核通过：魔数与声明不符（声明 {mime}，实际 {magic}），{pil_note}',
                        '复核通过-保留')
        return pack('delete',
                    f'魔数与声明不符（声明 {mime}，实际 {magic}）；{pil_note}',
                    '已删除替换红字')

    if magic == 'jpeg':
        fatal_r, anomalies, dup = check_jpeg_structure(data)
        if dup:
            return pack('delete', '重复流拼接（EOI 后检测到第二段完整 JPEG 流）',
                        '已删除替换红字')
        if fatal_r:
            ok, pil_note = pil_decode(data)
            return pack('delete', f'{fatal_r}；{pil_note}', '已删除替换红字')
    elif magic == 'png':
        fatal_r, anomalies, dup = check_png_structure(data)
        if dup:
            return pack('delete', '重复流拼接（IEND 后检测到第二段完整 PNG 流）',
                        '已删除替换红字')
        if fatal_r:
            ok, pil_note = pil_decode(data)
            return pack('delete', f'{fatal_r}；{pil_note}', '已删除替换红字')
    else:
        ok, pil_note = pil_decode(data)
        if not ok:
            return pack('delete', f'解码失败（非 JPEG/PNG 格式 {magic}）；{pil_note}',
                        '已删除替换红字')
        if must_review:
            return pack('keep_note', f'复核通过：{pil_note}', '复核通过-保留')
        return out

    # ---- 结构校验通过的 JPEG/PNG ----
    if anomalies or must_review:
        ok, pil_note = pil_decode(data)
        if not ok:
            return pack('delete', f'解码失败（结构校验通过但无法解码）；{pil_note}',
                        '已删除替换红字')
        extra = '；'.join(anomalies) if anomalies else '结构与 CRC 全部通过'
        return pack('keep_note', f'复核通过：{extra}；{pil_note}', '复核通过-保留')

    return out


# ----------------------------- 单文件处理（子进程执行） -----------------------------

def process_file(job):
    path, rel, must_review_file = job
    result = {
        'rel': rel,
        'file': os.path.basename(path),
        'images': 0,
        'records': [],
        'changed': False,
        'error': None,
        'existing_markers': 0,
        'data_uri_raw': 0,
        'data_uri_matched': 0,
    }
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
        has_bom = raw.startswith(BOM)
        body = raw[3:] if has_bom else raw
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            result['error'] = '文件不是合法 UTF-8，未改动'
            result['records'].append({
                'file': result['file'], 'idx': '-', 'mime': '-', 'size': '-',
                'reason': '文件编码非 UTF-8', 'action': '处理失败-未改动',
            })
            return result

        result['existing_markers'] = text.count('图片损坏</span>')
        result['data_uri_raw'] = len(DATA_URI_RE.findall(text))

        out_parts = []
        last = 0
        idx = 0
        for m in IMG_RE.finditer(text):
            idx += 1
            result['images'] = idx
            mime = m.group('mime').lower()
            b64text = m.group('b64')

            # 疑似 base64 中混入 ')' 导致匹配被截断的防护检查
            follow = text[m.end():m.end() + 1]
            pre_note = ''
            if follow and follow in B64CHARS:
                pre_note = '疑似 base64 含非法字符导致匹配截断'

            try:
                verdict = judge_image(mime, b64text, must_review_file, pre_note)
            except Exception as e:
                verdict = {
                    'status': 'keep_silent',
                    'reason': f'校验异常（{type(e).__name__}: {e}）',
                    'action': '处理失败-未改动',
                    'size': 0,
                }
                result['records'].append({
                    'file': result['file'], 'idx': idx, 'mime': f'data:image/{mime}',
                    'size': '?', 'reason': verdict['reason'], 'action': verdict['action'],
                })
                out_parts.append(text[last:m.end()])
                last = m.end()
                continue

            if verdict['status'] == 'delete':
                out_parts.append(text[last:m.start()])
                out_parts.append(REPLACE_MARKER)
                last = m.end()
                result['records'].append({
                    'file': result['file'], 'idx': idx, 'mime': f'data:image/{mime}',
                    'size': verdict['size'], 'reason': verdict['reason'],
                    'action': verdict['action'],
                })
            else:
                if verdict['status'] == 'keep_note':
                    result['records'].append({
                        'file': result['file'], 'idx': idx, 'mime': f'data:image/{mime}',
                        'size': verdict['size'], 'reason': verdict['reason'],
                        'action': verdict['action'],
                    })
                out_parts.append(text[last:m.end()])
                last = m.end()
        out_parts.append(text[last:])
        result['data_uri_matched'] = idx
        new_text = ''.join(out_parts)

        if new_text != text:
            result['changed'] = True
            new_raw = (BOM if has_bom else b'') + new_text.encode('utf-8')
            tmp = path + '.__fix_tmp__'
            with open(tmp, 'wb') as fh:
                fh.write(new_raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        result['records'].append({
            'file': result['file'], 'idx': '-', 'mime': '-', 'size': '-',
            'reason': f'处理异常: {result["error"]}',
            'action': '处理失败-未改动',
        })
        result['changed'] = False
    return result


# ----------------------------- 主流程 -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=min(os.cpu_count() or 4, 12))
    ap.add_argument('--limit', type=int, default=0, help='仅处理前 N 个文件（调试）')
    ap.add_argument('--dir', default=DATA_DIR, help='目标目录（调试用）')
    ap.add_argument('--log', default=LOG_PATH, help='日志文件路径')
    ap.add_argument('--manifest', default='', help='清单文件路径（默认 <dir>/_damaged_images.txt）')
    args = ap.parse_args()

    data_dir = args.dir
    manifest_path = args.manifest or os.path.join(data_dir, '_damaged_images.txt')
    log_path = args.log

    t0 = time.time()
    log_lines = []

    def log(msg):
        log_lines.append(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}')
        try:
            with io.open(log_path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write('\n'.join(log_lines) + '\n')
        except Exception:
            pass

    md_files = []
    for root, _dirs, files in os.walk(data_dir):
        for f in files:
            if f.lower().endswith('.md'):
                p = os.path.join(root, f)
                md_files.append((p, os.path.relpath(p, data_dir), f in DRY_RUN_SUSPECT_FILES))
    md_files.sort(key=lambda x: x[1])
    if args.limit:
        md_files = md_files[:args.limit]

    log(f'扫描目录: {data_dir}')
    log(f'扫描文件总数(.md): {len(md_files)}  并行进程数: {args.workers}')

    results = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(process_file, md_files, chunksize=16):
            results.append(res)
            done += 1
            if done % 2000 == 0:
                log(f'进度: {done}/{len(md_files)}')

    # ---- 汇总 ----
    total_files = len(md_files)
    total_images = sum(r['images'] for r in results)
    total_raw_uri = sum(r['data_uri_raw'] for r in results)
    total_matched = sum(r['data_uri_matched'] for r in results)
    changed_files = sum(1 for r in results if r['changed'])
    existing_markers = sum(r['existing_markers'] for r in results)

    reason_counter = collections.Counter()
    action_counter = collections.Counter()
    damaged_files = set()
    kept_files = set()
    failed_files = set()
    manifest_rows = []
    for r in sorted(results, key=lambda x: x['rel']):
        for rec in r['records']:
            manifest_rows.append(rec)
            action_counter[rec['action']] += 1
            if rec['action'] == '已删除替换红字':
                reason_counter[rec['reason'].split('；')[0]] += 1
                damaged_files.add(r['file'])
            elif rec['action'] == '复核通过-保留':
                kept_files.add(r['file'])
            else:
                reason_counter['处理失败'] += 1
                failed_files.add(r['file'])

    with io.open(manifest_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('文件名\t图片序号\tmime\t字节数\t损坏原因分类\t处置\n')
        for rec in manifest_rows:
            fh.write('{file}\t{idx}\t{mime}\t{size}\t{reason}\t{action}\n'.format(**rec))

    elapsed = time.time() - t0
    log('---- 最终统计 ----')
    log(f'扫描文件总数(.md)         : {total_files}')
    log(f'嵌入图片总数(匹配语法)    : {total_matched}')
    log(f'裸 data URI 总数(交叉核对): {total_raw_uri}')
    log(f'确认损坏并删除替换红字    : {action_counter.get("已删除替换红字", 0)}')
    for reason, cnt in sorted(reason_counter.items(), key=lambda x: -x[1]):
        log(f'    - {reason}: {cnt}')
    log(f'复核通过-保留             : {action_counter.get("复核通过-保留", 0)}')
    log(f'处理失败-未改动           : {action_counter.get("处理失败-未改动", 0)}')
    log(f'损坏涉及文件数            : {len(damaged_files)}')
    log(f'复核保留涉及文件数        : {len(kept_files)}')
    log(f'处理失败涉及文件数        : {len(failed_files)}')
    log(f'实际写入(内容变更)文件数  : {changed_files}')
    log(f'运行前已存在红字标记数    : {existing_markers}')
    kept_silent = (total_matched
                   - action_counter.get('已删除替换红字', 0)
                   - action_counter.get('复核通过-保留', 0)
                   - action_counter.get('处理失败-未改动', 0))
    log(f'校验正常未变动图片数      : {kept_silent}')
    log(f'清单文件: {manifest_path}')
    log(f'运行耗时: {elapsed:.1f} 秒')
    if failed_files:
        log('处理失败文件:')
        for f in sorted(failed_files):
            log(f'    - {f}')

    with io.open(log_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(log_lines) + '\n')
    print('DONE elapsed=%.1fs' % elapsed)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
