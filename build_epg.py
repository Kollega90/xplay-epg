#!/usr/bin/env python3
"""Сжимает большой XMLTV (.xml / .xml.gz, ~200 МБ) в маленький epg.json для XPlayTv:
оставляет ближайшие ~24 часа, названия каналов (для сопоставления по имени) и описания только для 2 ближайших передач.
Использование: python3 build_epg.py вход.xml[.gz] выход.json"""
import sys, gzip, json, time, calendar, re, xml.etree.ElementTree as ET

HOURS_AHEAD = 24
HOURS_BACK = 3          # на случай ушедших часов пояса/задержки
DESC_FOR = 2            # описания только у стольких передач на канал
DESC_MAX = 200

def ts(s):
    m = re.match(r'^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?\s*([+-]\d{4})?', s or '')
    if not m: return 0
    t = calendar.timegm((int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6] or 0)))
    if m[7]:
        off = (int(m[7][1:3]) * 60 + int(m[7][3:5])) * 60
        t -= off if m[7][0] == '+' else -off
    return t

def main(src, dst):
    f = open(src, 'rb')
    magic = f.read(2); f.seek(0)
    if magic == b'\x1f\x8b': f = gzip.GzipFile(fileobj=f)
    now = int(time.time())
    lo, hi = now - HOURS_BACK * 3600, now + HOURS_AHEAD * 3600
    names, progs = {}, {}
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
            if a and b and b > lo and a < hi and cid:
                t = el.find('title'); d = el.find('desc')
                progs.setdefault(cid, []).append([a, b, (t.text or '').strip() if t is not None else '',
                                                  ((d.text or '').strip() if d is not None else '')])
            el.clear()
    out = {}
    for cid, lst in progs.items():
        if cid.lower().startswith('no_epg'): continue
        lst.sort(key=lambda x: x[0])
        # в источнике бывают дубли: одна и та же передача много раз с разницей в минуту — оставляем первую
        ded = []
        for p in lst:
            if ded and p[2] == ded[-1][2] and p[0] < ded[-1][1]:
                if len(p[3]) > len(ded[-1][3]): ded[-1][3] = p[3]
                continue
            ded.append(p)
        lst = ded
        # описание только у текущей и ближайших
        k = 0
        for p in lst:
            if p[1] > now and k < DESC_FOR:
                p[3] = p[3].replace('\\n', ' ').replace('nn', ' ')[:DESC_MAX]; k += 1
            else:
                p[3] = ''
        out[cid] = {'n': names.get(cid, []), 'p': [p if p[3] else p[:3] for p in lst]}
    with open(dst, 'w', encoding='utf-8') as o:
        json.dump({'v': 1, 'g': now, 'c': out}, o, ensure_ascii=False, separators=(',', ':'))
    n = sum(len(v['p']) for v in out.values())
    print('channels with programmes:', len(out), 'programmes:', n)

if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
