#!/usr/bin/env python3
"""KGM otoyol ve köprü ücret tarifelerini EVnavi/Resources/kgm_tolls.json dosyasına dönüştürür.

Kaynaklar:
- Tarifeler: KGM "Otoyol ve Köprü Geçiş Ücretleri" sayfasındaki PDF'ler (araç sınıfı 1-6, KDV dahil).
- Gişe ve köprü konumları: OpenStreetMap (Overpass). Gişe adları tarifedeki istasyon adlarıyla eşleştirilir;
  eşleşmeyen (KGM dışı ya da adsız) gişeler alınmaz.

Kullanım (pdfplumber gerekir):  python3 Tools/fetch_kgm_tolls.py [çıktı.json]
Aynı script evnavi-epdk-data reposunda haftalık çalışır (data/kgm_tolls.json); uygulama "Ücretleri güncelle"
menüsüyle oradan indirir. Tarife ve gişeler değişmediyse dosyaya dokunulmaz (gereksiz commit olmasın).
"""
import hashlib
import io
import itertools
import os
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from datetime import date

import pdfplumber

PAGE = "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Otoyollar/UcretlerYeni.aspx"
BASE = "https://www.kgm.gov.tr"
OVERPASS = "https://overpass-api.de/api/interpreter"
OUT = "EVnavi/Resources/kgm_tolls.json"
UA = {"User-Agent": "EVnavi-data-script/1.0"}

# Dosya adı -> uygulamada gösterilecek ad. Köprüler tek fiyatlıdır; otoyollarda gişeden gişeye değişir.
NAMES = {
    "1-15Temmuz-FSM": "15 Temmuz Şehitler ve FSM Köprüleri",
    "2-Osmangazi": "Osmangazi Köprüsü",
    "3-YSSKoprusu": "Yavuz Sultan Selim Köprüsü",
    "4-1915Canakkale": "1915 Çanakkale Köprüsü",
    "5-AnadoluOtoyoluCamlica-Akinci": "Anadolu Otoyolu (Çamlıca–Akıncı)",
    "6-Izmir-Cesme": "İzmir–Çeşme Otoyolu",
    "7-Izmir-Aydin": "İzmir–Aydın Otoyolu",
    "8-CukurovaOtoyoluAdana-Gaziantep": "Çukurova Otoyolu (Adana–Gaziantep)",
    "9-CukurovaOtoyoluGaziantep-Sanliurfa": "Çukurova Otoyolu (Gaziantep–Şanlıurfa)",
    "10-CukurovaOtoyoluNigde-Mersin-Adana": "Çukurova Otoyolu (Niğde–Mersin–Adana)",
    "11-AvrupaOtoyoluMahmutbey-Edirne": "Avrupa Otoyolu (Mahmutbey–Edirne)",
    "12-Gebze-Orhangazi-Izmir": "Gebze–Orhangazi–İzmir Otoyolu",
    "13-YSSKuzeyCevreYolu": "Kuzey Çevre Otoyolu (YSS)",
    "14-KMOAvrupaKinali-Odayeri": "Kuzey Marmara Otoyolu Avrupa (Kınalı–Odayeri)",
    "15-KMOAnadoluKurtkoy-Akyazi": "Kuzey Marmara Otoyolu Anadolu (Kurtköy–Akyazı)",
    "16-Menemen-Aliaga-Candarli": "Menemen–Aliağa–Çandarlı Otoyolu",
    "17-Ankara-Nigde": "Ankara–Niğde Otoyolu",
    "18-Malkara-Canakkale": "Malkara–Çanakkale Otoyolu",
    "19-Aydin-Denizli": "Aydın–Denizli Otoyolu",
    "20-Cesmeli-KizkalesiTR": "Çeşmeli–Kızkalesi Otoyolu",
}
BRIDGES = {
    "1-15Temmuz-FSM": ["15 Temmuz Şehitler Köprüsü", "Fatih Sultan Mehmet Köprüsü"],
    "2-Osmangazi": ["Osmangazi Köprüsü"],
    "3-YSSKoprusu": ["Yavuz Sultan Selim Köprüsü"],
    "4-1915Canakkale": ["1915 Çanakkale Köprüsü"],
}
PRICE = re.compile(r"(?<![\d.])(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}")


def get(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(s):
    return (s or "").replace("​", "").strip()


def norm(s):
    """Karşılaştırma için: Türkçe büyük harf, aksansız, yalnız harf/rakam."""
    s = clean(s).replace("i", "İ").replace("ı", "I").upper()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if c.isalnum() and not unicodedata.combining(c))


def price(s):
    return float(s.replace(".", "").replace(",", "."))


def class_prices(line, bridge):
    """Satırdaki araç sınıfı ve fiyatlar. Yazımlar: "AYDIN 3 ₺ 1 .070,00 ₺ 135,00" (₺ önde, rakamlar boşluklu),
    "KINALI 3 480,00 120,00", "4 3 .705,00 435,00" ve köprülerde "1 1.170,00 ₺"."""
    if "km" in line:
        return None
    if bridge:  # köprü: "1 AKS ARALIĞI ... 59,00 8,75" -> yeni tarife ilk fiyattır
        m = re.match(r"\s*([1-6])\b.*?(\d{1,3}(?:\.\d{3})*,\d{2})", line.replace("₺", " "))
        return (int(m.group(1)), [price(m.group(2))]) if m else None
    m = re.match(r"([^₺]*?)(?:^|\s)([1-6])\s*₺(.*)$", line)
    if m:
        prices = [re.sub(r"\s", "", c) for c in m.group(3).split("₺")]
        return int(m.group(2)), [price(p) for p in prices if PRICE.fullmatch(p)]
    line = re.sub(r"(\d) \.(\d{3})", r"\1.\2", line.replace("₺", " ").replace("TL", " "))
    first = PRICE.search(line)
    before = line[:first.start()].split() if first else []
    # Sınıf, satırdaki ilk fiyatın hemen önündeki rakamdır; sonrasında PDF'in böldüğü rakamlar ("3 15,00") birleştirilir.
    if not before or before[-1] not in {"1", "2", "3", "4", "5", "6"}:
        return None
    rest = re.sub(r"(?<![\d.,])(\d) (\d{1,2},\d{2})", r"\1\2", line[first.start():])
    return int(before[-1]), [price(p) for p in PRICE.findall(rest)]


# Tablo başlığı okunamayan ya da bozuk okunan istasyon adları.
STATION_FIXES = {
    "19-Aydin-Denizli": ["AYDIN ALIN", "KÖŞK", "YENİ PAZAR", "NAZİLLİ", "KUYUCAK", "BUHARKENT", "SARAYKÖY",
                         "KUMKISIK-A", "KUMKISIK-B", "PAMUKKALE", "KOCABAŞ"],
}
NAME_FIXES = {"DÜZCES OARNG.ANİZE": "DÜZCE ORGANİZE"}
LABELS = {"ARAÇ SINIFI", "İSTASYON", "SINIF", "ÇIKIŞ GİŞELERİ", "GİRİŞ / ÇIKIŞ", "İstasyon", "Sınıf", "Giriş / Çıkış"}


def stations_from_tables(tables):
    """İstasyon adları: fiyat içermeyen, en çok adlı başlık satırı."""
    best = []
    for table in tables:
        for row in table[:6]:
            cells = [clean(c).replace("\n", " ") for c in row if clean(c)]
            cells = [NAME_FIXES.get(c, c) for c in cells]
            names = [c for c in cells if re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", c) and not PRICE.search(c)
                     and c not in LABELS]
            if len(names) > len(best):
                best = names
    return best


def triangular_matrix(lines, n):
    """Alt üçgen tablo (Anadolu, Çukurova, İzmir-Çeşme...): her istasyonun satırından önce, önceki istasyonlara
    uzaklıkları veren "… km" etiket satırı gelir. Etiketteki km sayısı (k) satırın hangi istasyon olduğunu verir;
    satırda k ücret + en uzak mesafe ücreti bulunur. Biçime uymayan satır (boş hücreli) atlanır, o hücreler boş kalır."""
    matrix = [[None] * n for _ in range(n)]
    k, rows = 0, 0
    for line in lines:
        kms = re.findall(r"\d[\d.,]*\s*km", line)
        if kms:
            k = len(kms)
            continue
        found = class_prices(line, False)
        if not found or found[0] != 1:
            continue
        values = found[1]
        if k is not None and k < n and len(values) == k + 1:
            for j in range(k):
                matrix[k][j] = matrix[j][k] = values[j]
            matrix[k][k] = values[k]
            rows += 1
        k = None  # sonraki satır kendi etiketini bekler
    return matrix if rows >= n // 2 else None


def square_matrix(rows, n, max_missing=2):
    """Kare tablo (giriş satır, çıkış sütun). PDF'te okunamayan satırlar (en çok iki), tablo simetrik olduğundan
    sütunlardan tamamlanır: eksik satırların yeri, simetriyi en iyi koruyan konumlar seçilerek bulunur.
    İki eksik satırın kesiştiği hücre bilinmez (boş kalır)."""
    rows = [r for r in rows if len(r) == n]
    missing = n - len(rows)
    if n < 2 or not 0 <= missing <= max_missing:
        return None
    if missing == 0:
        return rows
    best = None
    for gaps in itertools.combinations(range(n), missing):
        it = iter(rows)
        full = [None if i in gaps else next(it) for i in range(n)]
        pairs = [(full[i][j], full[j][i]) for i in range(n) for j in range(i + 1, n) if full[i] and full[j]]
        misses = sum(1 for a, b in pairs if a != b)
        if best is None or misses < best[0]:
            best = (misses, gaps, full, len(pairs))
    misses, gaps, full, compared = best
    if misses > compared * 0.15:  # bazı tek yönlü bağlantılarda (ör. Karacabey) tablo tam simetrik değil
        return None
    for g in gaps:
        full[g] = [full[i][g] if full[i] else None for i in range(n)]
    for g in gaps:
        known = [v for v in full[g] if v is not None]
        full[g][g] = max(known) if known else None
    return full


TWIN = re.compile(r"(.*?\d,\d{2}\s+)([1-6])(\s+\d.*)$")


def right_table_lines(lines):
    """Yan yana iki tablolu PDF'te (Gebze-Orhangazi-İzmir) satırların sağ tablo kısmı: ikinci sınıf rakamından sonrası."""
    return [m.group(2) + m.group(3) if (m := TWIN.match(line)) else line for line in lines]


def left_table_max(lines):
    """Sol tablonun (Gebze-Bursa) köprü sütunları hariç en yüksek otomobil ücreti."""
    best = 0
    for line in lines:
        m = TWIN.match(line)
        found = class_prices(m.group(1), False) if m else None
        if found and found[0] == 1:
            # Osmangazi Köprüsü (1.000 TL üstü) sütunları köprü ücretidir; köprü ayrıca hesaplanır.
            best = max([best] + [v for v in found[1] if v < 1000])
    return best or None


def parse_pdf(blob, bridge, pid):
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        text = "\n".join(clean(p.extract_text()) for p in pdf.pages)
        tables = [t for p in pdf.pages for t in p.extract_tables()]
    m = re.search(r"(\d{2})[./](\d{2})[./](\d{4})\D{0,20}(?:saat|Saat|İTİBAR)", text)
    valid = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None

    # Her araç sınıfının tablodaki en yüksek ücreti (köprülerde tek ücret).
    lines = text.splitlines()
    best = {}
    for line in lines:
        found = class_prices(line, bridge)
        if found and found[1]:
            best[found[0]] = max(best.get(found[0], 0), max(found[1]))
    classes = [best.get(k) for k in range(1, 7)]
    if bridge:
        return text, valid, classes if all(classes) else None, [], None, None

    stations = STATION_FIXES.get(pid) or stations_from_tables(tables)
    n = len(stations)
    other_max = None
    if pid.startswith("12-"):  # iki tablo yan yana: sağdaki (Bursa-İzmir) matris, soldaki için en yüksek ücret
        other_max = left_table_max(lines)
        lines = right_table_lines(lines)
    # Üçgen tablolarda her istasyonun "… km" etiket satırı vardır; dipnottaki tek tük "km" sayılmaz.
    if sum(1 for line in lines if re.search(r"\d\s*km\b", line)) >= n // 2:
        matrix = triangular_matrix(lines, n)
    else:
        otomobil = [f[1] for f in (class_prices(line, False) for line in lines) if f and f[0] == 1]
        matrix = square_matrix(otomobil, n)
    return text, valid, classes if all(classes) else None, stations, matrix, other_max


def overpass(query):
    cache = os.path.join(tempfile.gettempdir(), "kgm_overpass_" + hashlib.sha1(query.encode()).hexdigest() + ".json")
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
        with open(cache) as f:
            return json.load(f)
    for attempt in range(4):
        try:
            # macOS'un eski Python SSL kitaplığı Overpass sunucusuyla bağlanamadığı için curl kullanılır.
            out = subprocess.run(["curl", "-sf", "-m", "200", "-A", UA["User-Agent"], OVERPASS,
                                  "--data-urlencode", "data=" + query], capture_output=True, check=True).stdout
            elements = json.loads(out)["elements"]
            with open(cache, "w") as f:
                json.dump(elements, f)
            return elements
        except Exception as e:  # Overpass sık sık meşgul döner
            print("Overpass yeniden deneniyor:", e, file=sys.stderr)
            time.sleep(15 * (attempt + 1))
    raise SystemExit("Overpass'a ulaşılamadı")


def aliases(station):
    """İstasyon adının gişe adıyla karşılaştırılacak biçimleri: "NİĞDE ALIN" -> NIGDE, "MALKARA G-1" -> MALKARA,
    "DÜZCE (GÖLYAKA)" -> DUZCEGOLYAKA, DUZCE, GOLYAKA."""
    base = re.sub(r"\s+(ALIN|Alın|G-\d+)$", "", clean(station))
    names = {base, re.sub(r"\s*\(.*?\)", "", base)} | set(re.findall(r"\((.*?)\)", base))
    return {norm(n) for n in names if norm(n)}


def station_match(key, stations, exact):
    """Gişe adının eşleştiği istasyon indeksi. Birebir değilse yalnız gişe adı istasyon adının başıysa
    ("Kulu" -> "Kulu-Kırıkkale") kabul edilir; tersine ("Gebze OSB" -> "GEBZE") başka gişe olabileceği için edilmez."""
    hits = []
    for i, station in enumerate(stations):
        for alias in aliases(station):
            if alias == key or (not exact and len(key) >= 4 and alias.startswith(key)):
                hits.append((len(alias), i))
    return max(hits)[1] if hits else None


def center(e):
    return (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])


def main():
    html = get(PAGE).decode("utf-8", "replace")
    paths = sorted(set(re.findall(r"/SiteCollectionDocuments/[^\"'<> ]+?/(\d+-[A-Za-z0-9-]+)\.pdf", html)),
                   key=lambda s: int(s.split("-")[0]))
    folder = re.search(r"(/SiteCollectionDocuments/[^\"'<> ]+?/)\d+-[A-Za-z0-9-]+\.pdf", html).group(1)

    tariffs, texts = [], {}
    for pid in paths:
        url = BASE + folder + pid + ".pdf"
        text, valid, classes, stations, matrix, other_max = parse_pdf(get(url), pid in BRIDGES, pid)
        texts[pid] = norm(text)
        tariffs.append({"id": pid, "name": NAMES.get(pid, pid), "kind": "bridge" if pid in BRIDGES else "motorway",
                        "validFrom": valid, "url": url, "prices": classes,
                        "stations": stations, "matrix": matrix,
                        **({"otherSectionMax": other_max} if other_max else {})})
        filled = sum(v is not None for r in matrix for v in r) if matrix else 0
        print(f"{pid}: {valid} oto={classes and classes[0]} istasyon={len(stations)} "
              f"matris={filled}/{len(stations) ** 2} diğer={other_max}", file=sys.stderr)

    # Gişeler: aynı adlı düğümler 3 km içinde tek noktada toplanır.
    booths = {}
    for e in overpass('[out:json][timeout:150];nwr["barrier"="toll_booth"]["name"](35.8,25.6,42.2,44.9);out center tags;'):
        name = re.sub(r"\s+(giriş|çıkış|giris|cikis)$", "", clean(e["tags"]["name"]), flags=re.I)
        if not name or not re.search(r"[a-zçğıöşü]", name, re.I) or re.search(r"[α-ω]", name, re.I):
            continue
        booths.setdefault(name, []).append(center(e))
    points = []
    for name, coords in booths.items():
        groups = []
        for lat, lon in coords:
            g = next((g for g in groups if abs(g[0][0] - lat) < 0.03 and abs(g[0][1] - lon) < 0.03), None)
            (g if g else groups.append([]) or groups[-1]).append((lat, lon))
        short = re.sub(r"\s*(Gişeleri|Gişesi|Gişe|Toll Plaza)$", "", name, flags=re.I)
        key = norm(short)
        if len(key) < 4 or "KOPRU" in key:
            continue
        # Önce tarifenin istasyon başlıklarıyla birebir, sonra baş eşleşme, en son tüm PDF metninde arama.
        motorways = [t for t in tariffs if t["kind"] == "motorway"]
        matches = ([t["id"] for t in motorways if station_match(key, t["stations"], exact=True) is not None]
                   or [t["id"] for t in motorways if station_match(key, t["stations"], exact=False) is not None]
                   or [t["id"] for t in motorways if key in texts[t["id"]]])
        for g in groups:
            lat, lon = sum(c[0] for c in g) / len(g), sum(c[1] for c in g) / len(g)
            points.append({"name": short, "lat": round(lat, 6), "lon": round(lon, 6), "candidates": matches})

    # Birden çok otoyolda geçen adlar, tek eşleşen gişelerin ağırlık merkezine en yakın otoyola verilir.
    centroids = {}
    for p in points:
        if len(p["candidates"]) == 1 or len(set(p["candidates"])) == 1:
            centroids.setdefault(p["candidates"][0], []).append((p["lat"], p["lon"]))
    centroids = {k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)) for k, v in centroids.items()}
    result = []
    for p in points:
        cands = p.pop("candidates")
        if len(cands) > 1:
            cands = [c for c in cands if c in centroids]
        if not cands:
            continue
        tid = cands[0] if len(cands) == 1 else min(
            cands, key=lambda c: (centroids[c][0] - p["lat"]) ** 2 + (centroids[c][1] - p["lon"]) ** 2)
        # Ücret matrisindeki istasyon.
        tariff = next(t for t in tariffs if t["id"] == tid)
        key = norm(p["name"])
        index = station_match(key, tariff["stations"], exact=True)
        if index is None:
            index = station_match(key, tariff["stations"], exact=False)
        result.append({"tariff": tid, **p, **({"station": index} if index is not None else {})})

    # Köprüler: köprü yapısının (way) merkezi.
    names = [b for bs in BRIDGES.values() for b in bs]
    query = "".join(f'way["bridge"]["name"="{b}"](35.8,25.6,42.2,44.9);' for b in names)
    els = overpass(f"[out:json][timeout:90];({query});out center tags;")
    for tid, bnames in BRIDGES.items():
        for bname in bnames:
            found = [center(e) for e in els if e.get("tags", {}).get("name") == bname]
            # Aynı adı taşıyan uzak yapılar (ör. başka ildeki köprüler) atılır: en kalabalık 5 km'lik küme alınır.
            found = max(([c for c in found if abs(c[0] - a[0]) < 0.05 and abs(c[1] - a[1]) < 0.05] for a in found),
                        key=len, default=[])
            if not found:
                print("Köprü konumu bulunamadı:", bname, file=sys.stderr)
                continue
            result.append({"tariff": tid, "name": bname, "lat": round(sum(c[0] for c in found) / len(found), 6),
                           "lon": round(sum(c[1] for c in found) / len(found), 6)})

    out = {"source": PAGE, "locations": "© OpenStreetMap katkıcıları", "generated": date.today().isoformat(),
           "tariffs": tariffs, "points": sorted(result, key=lambda p: (p["tariff"], p["name"]))}
    path = sys.argv[1] if len(sys.argv) > 1 else OUT
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("tariffs") == out["tariffs"] and old.get("points") == out["points"]:
            print(f"Değişiklik yok: {path}", file=sys.stderr)
            return
    except (OSError, ValueError):
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{len(tariffs)} tarife, {len(result)} nokta -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
