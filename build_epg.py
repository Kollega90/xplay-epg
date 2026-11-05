#!/usr/bin/env python3
"""Сжимает большой XMLTV (.xml / .xml.gz, ~200 МБ) для XPlayTv.

Выход (рядом с выходным файлом):
  epg.json         - как раньше: ближайшие ~24 часа, названия каналов, описания у 2 ближайших передач;
                     у каждого канала добавлено поле f - имя файла с полной программой (стабильный хеш id)
  ch/<f>.json      - полная программа канала: от 7 суток назад до 2 суток вперёд (для экрана «Программа и архив»)

Использование: python3 build_epg.py вход.xml[.gz] выход.json"""
import sys, os, gzip, json, time, calendar, re, hashlib, xml.etree.ElementTree as ET

HOURS_AHEAD = 24        # главный epg.json: вперёд
HOURS_BACK = 3          # главный epg.json: назад (на случай ушедших часов пояса/задержки)
DESC_FOR = 4            # главный epg.json: описания только у стольких передач на канал (остальное приложение дозагружает из ch/<f>.json)
DESC_MAX = 700

CH_BACK_H = 7 * 24 + 2  # файлы каналов: назад (архив у провайдера 7 суток)
CH_AHEAD_H = 48         # файлы каналов: вперёд
CH_DESC_MAX = 700
MAX_TOTAL = int(os.environ.get('EPG_MAX_TOTAL_MB', '300')) * 1024 * 1024   # предел суммарного размера файлов каналов
# если файлы получаются слишком большими: (длина описания, описания только у передач, закончившихся не раньше N секунд назад; None = у всех)
LEVELS = [(CH_DESC_MAX, None), (400, None), (200, 36 * 3600), (0, 0)]


def ts(s):
    m = re.match(r'^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?\s*([+-]\d{4})?', s or '')
    if not m: return 0
    t = calendar.timegm((int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6] or 0)))
    if m[7]:
        off = (int(m[7][1:3]) * 60 + int(m[7][3:5])) * 60
        t -= off if m[7][0] == '+' else -off
    return t


NN_RE = re.compile(r'(?:(?<=[.!?\u2026\u00bb"\u201d)\]:;])|^)(?:nn)+(?=[\s\u0410-\u042f\u0406\u0407\u0404\u0490A-Z0-9\u00ab"\u201c(\-\u2013\u2014]|$)')


def clean(d):
    # провайдер пишет перенос строки как «nn» (после знака препинания, перед заглавной); слова с «nn» внутри (ODonnell) не трогаем
    d = (d or '').strip().replace('\\n', ' ')
    return NN_RE.sub(' ', d).strip()


def fname(cid, used):
    n = 10
    while True:
        h = hashlib.md5(cid.encode('utf-8')).hexdigest()[:n]
        if used.get(h, cid) == cid:
            used[h] = cid
            return h
        n += 2


def dedupe(lst):
    # в источнике бывают дубли: одна и та же передача много раз с разницей в минуту - оставляем первую
    ded = []
    for p in lst:
        if ded and p[2] == ded[-1][2] and p[0] < ded[-1][1]:
            if len(p[3]) > len(ded[-1][3]): ded[-1][3] = p[3]
            continue
        ded.append(p)
    return ded


def write_channels(chdir, wide, files, now, desc_max, desc_from):
    os.makedirs(chdir, exist_ok=True)
    for old in os.listdir(chdir):
        if old.endswith('.json'): os.remove(os.path.join(chdir, old))
    total = 0
    for cid, lst in wide.items():
        rows = []
        for p in lst:
            d = p[3][:desc_max] if desc_max and (desc_from is None or p[1] >= now - desc_from) else ''
            rows.append([p[0], p[1], p[2], d] if d else [p[0], p[1], p[2]])
        path = os.path.join(chdir, files[cid] + '.json')
        with open(path, 'w', encoding='utf-8') as o:
            json.dump({'v': 1, 'g': now, 'p': rows}, o, ensure_ascii=False, separators=(',', ':'))
        total += os.path.getsize(path)
    return total


def main(src, dst):
    f = open(src, 'rb')
    magic = f.read(2); f.seek(0)
    if magic == b'\x1f\x8b': f = gzip.GzipFile(fileobj=f)
    now = int(time.time())
    lo, hi = now - HOURS_BACK * 3600, now + HOURS_AHEAD * 3600
    wlo, whi = now - CH_BACK_H * 3600, now + CH_AHEAD_H * 3600
    names, wide = {}, {}
    for ev, el in ET.iterparse(f, events=('end',)):
        if el.tag == 'channel':
            cid = el.get('id')
            for d in el.findall('display-name'):
                if d.text:
                    names.setdefault(cid, [])
                    if d.text.strip() not in names[cid]: names[cid].append(d.text.strip())
            el.clear()
        elif el.tag == 'programme':
            a, b = ts(el.get('start')), ts(el.get('stop'))
            cid = el.get('channel')
            if a and b and b > wlo and a < whi and cid and not cid.lower().startswith('no_epg'):
                t = el.find('title'); d = el.find('desc')
                wide.setdefault(cid, []).append([a, b, (t.text or '').strip() if t is not None else '',
                                                 clean(d.text)[:max(DESC_MAX, CH_DESC_MAX) + 50] if d is not None else ''])
            el.clear()
    out, used, files = {}, {}, {}
    for cid in list(wide):
        lst = dedupe(sorted(wide[cid], key=lambda x: x[0]))
        if not lst:
            del wide[cid]; continue
        wide[cid] = lst
        files[cid] = fname(cid, used)
    for cid, lst in wide.items():
        main_l = [list(p) for p in lst if p[1] > lo and p[0] < hi]
        k = 0
        for p in main_l:
            if p[1] > now and k < DESC_FOR:
                p[3] = p[3][:DESC_MAX]; k += 1
            else:
                p[3] = ''
        out[cid] = {'n': names.get(cid, []), 'f': files[cid], 'p': [p if p[3] else p[:3] for p in main_l]}
    with open(dst, 'w', encoding='utf-8') as o:
        json.dump({'v': 1, 'g': now, 'c': out}, o, ensure_ascii=False, separators=(',', ':'))
    chdir = os.path.join(os.path.dirname(os.path.abspath(dst)), 'ch')
    total = 0
    for desc_max, desc_from in LEVELS:
        total = write_channels(chdir, wide, files, now, desc_max, desc_from)
        print('channel files: desc_max=%s total=%.1f MB' % (desc_max, total / 1048576.0))
        if total <= MAX_TOTAL: break
    n = sum(len(v['p']) for v in out.values())
    nw = sum(len(v) for v in wide.values())
    print('channels with programmes:', len(out), 'main programmes:', n, 'channel files:', len(wide), 'file programmes:', nw)
    print('epg.json: %.1f MB, ch/: %.1f MB' % (os.path.getsize(dst) / 1048576.0, total / 1048576.0))


if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
