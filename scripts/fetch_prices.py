#!/usr/bin/env python3
"""Şarj operatörlerinin güncel kWh fiyatlarını KENDİ RESMİ SİTELERİNDEN çeker → data/prices.json

EPDK verisinde fiyat yoktur. Her operatör için resmi fiyat sayfası ve fiyatı okuyan bir desen tanımlıdır.
Bir sayfa okunamazsa (site değişti, erişilemedi) uydurma değer üretilmez: önceki geçerli fiyat
`checkedAt` tarihiyle korunur ve `stale: true` olarak işaretlenir.

Kullanım: python3 scripts/fetch_prices.py
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
# robots.txt kontrolünde kendimizi bu adla tanıtırız.
BOT_NAME = "evnavi-epdk-data"
NUM = r"(\d{1,2}[.,]\d{1,2})"

# brand: EPDK verisindeki "marka" alanı. Desenler sayfanın düz metni üzerinde çalışır ("raw": True ise
# sayfaya gömülü veri dahil ham HTML üzerinde); her desen 1 (tek fiyat) veya 2 (en düşük–en yüksek) sayı yakalar.
# Not: Eşarj (esarj.com) robots.txt ile tüm otomatik erişimi yasakladığı için listede yoktur.
OPERATORS = [
    {"brand": "zes", "url": "https://zes.net/tr/fiyatlandirma",
     "ac": rf"AC\s*{NUM}\s*₺",
     "dc": rf"DC-2\s*{NUM}\s*₺\s*/\s*kWh\s*-\s*{NUM}\s*₺",
     "note": "Fiyatlar lokasyona göre değişebilir."},
    {"brand": "Trugo", "url": "https://www.trugo.com.tr/price",
     "ac": rf"AC cihazlar için 1 kWh ücreti\s*{NUM}\s*TL",
     "dc": rf"DC cihazlar için 1 kWh ücreti\s*{NUM}\s*TL\s*-\s*{NUM}\s*TL",
     "note": "Fiyatlar lokasyona göre değişebilir."},
    {"brand": "EN YAKIT", "url": "https://www.enyakit.com.tr/ucretlendirme",
     "dc": rf"{NUM}\s*TL/kWh fiyatla",
     "note": "Ağın tamamı DC. Seçili istasyonlarda kampanyalı fiyat uygulanabilir."},
    {"brand": "oncharge", "url": "https://www.oncharge.com.tr/fiyatlar",
     "ac": rf"AC Soket Tarifesi\s*{NUM}\s*TL",
     "dc": rf"DC Soket Tarifesi\s*{NUM}\s*TL",
     "note": "Lokasyon veya soket bazlı fiyatlar değişebilir."},
    {"brand": "Otojet", "url": "https://www.oto-jet.com.tr/fiyatlarimiz",
     "ac": rf"AC Tarife\s*₺\s*{NUM}",
     "dc": rf"DC Tarife\s*₺\s*{NUM}"},
    {"brand": "D-Charge", "url": "https://dcharge.com.tr/tarifeler",
     "ac": rf"Tüm AC Soketler\s*{NUM}\s*TL",
     "dc": rf"Tüm DC Soketler\s*{NUM}\s*TL"},
    {"brand": "beefull", "url": "https://beefull.com/tarifeler/elektrikli-arac-tarifesi/",
     "ac": rf"AC SOKET TARİFESİ\s*Fiyat\s*/\s*Birim\s*{NUM}\s*TL",
     "dc": rf"DC SOKET TARİFESİ\s*Fiyat\s*/\s*Birim\s*{NUM}\s*TL"},
    {"brand": "ovolt", "url": "https://ovolt.com.tr/tarifeler", "raw": True,
     "ac": rf'"currentType":"AC","oldPrice":"[^"]*","currentPrice":"{NUM}\s*TL',
     "dc": rf'"currentType":"DC","oldPrice":"[^"]*","currentPrice":"{NUM}\s*TL\s*-\s*{NUM}\s*TL',
     "note": "Seçili lokasyonlarda kampanyalı fiyat uygulanabilir."},
    {"brand": "ASTOR", "url": "https://astorsarj.com.tr",
     "ac": rf"AC FİYATLANDIRMA\s*₺\s*{NUM}",
     "dc": rf"DC FİYATLANDIRMA\s*₺\s*{NUM}"},
    {"brand": "AKSA ŞARJ", "url": "https://www.aksasarj.com.tr/ucretler",
     "ac": rf"AC\s*{NUM}\s*-\s*{NUM}\s*TL\s*/\s*kWh",
     "dc": rf"kWh\s*DC\s*{NUM}\s*-\s*{NUM}\s*TL",
     "note": "Fiyatlar lokasyona göre değişebilir."},
    {"brand": "MKS-ŞARJ", "url": "https://mkssarj.com/tarife",
     "ac": rf"AC Tip 2 Soketler\s*{NUM}\s*TL",
     "dc": rf"DC CCS\s*{NUM}\s*TL"},
    {"brand": "MİGGO", "url": "https://www.mig-go.com",
     "ac": rf"AC Tarife\s*{NUM}\s*₺",
     "dc": rf"DC Tarife\s*{NUM}\s*₺"},
    {"brand": "NEVA ŞARJ", "url": "https://nevasarj.com/sayfalar/istasyonlarimiz/arac-sarj-ac-dc-kwh-fiyat-tarifeleri.html",
     "ac": rf"AC Soketler\s*{NUM}\s*TL",
     "dc": rf"DC Soketler\s*{NUM}\s*TL"},
    {"brand": "i-şarj", "url": "https://www.isarj.com/tarifeler/",
     "ac": rf"BİRİM AC\s*{NUM}\s*TL",
     "dc": rf"BİRİM DC\s*{NUM}\s*TL"},
    {"brand": "ŞarjMatik", "url": "https://sarjmatik.com.tr/ChargingPrices/",
     "ac": rf"AC 22 kW Soketler\s*₺\s*{NUM}",
     "dc": rf"DC 60-90 kW Soketler\s*₺\s*{NUM}"},
    {"brand": "ŞARJON", "url": "https://sarjon.com.tr/fiyatlar/",
     "ac": rf"AC\s*{NUM}\s*TL\s*/\s*kWh\s*DC-1",
     "dc": rf"DC-1\s*{NUM}\s*TL\s*/\s*kWh\s*DC-2\s*{NUM}\s*TL",
     "note": "DC fiyatı istasyonun gücüne göre değişir."},
    {"brand": "LUMİCLE", "url": "https://lumicle.com.tr",
     "ac": rf"AC Soket Tarifesi:\s*{NUM}\s*₺",
     "dc": rf"DC Soket Tarifesi\s*{NUM}\s*₺"},
    {"brand": "TORA", "url": "https://torasarj.com/fiyatlar/",
     "ac": rf"AC Soket Tarifesi\s*{NUM}\s*TL",
     "dc": rf"DC Soket Tarifesi\s*{NUM}\s*TL"},
    {"brand": "efish", "url": "https://www.efish.com.tr/tr/fiyatlandirma/",
     "ac": rf"FİYATLANDIRMA AC\s*{NUM}\s*TL",
     "dc": rf"TL/kWh DC\s*{NUM}\s*TL"},
    {"brand": "ADZE", "url": "https://www.adze.com/tr/fiyat-tarifesi",
     "ac": rf"AC şarj\s*{NUM}\s*TL/kWh",
     "dc": rf"DC hızlı şarj \(40-350 kW\) ise\s*{NUM}\s*TL"},
    {"brand": "tunçmatik", "url": "https://tuncmatikcharge.com/fiyatlar/",
     "ac": rf"AC Soket Tarifesi\s*{NUM}\s*₺",
     "dc": rf"DC Soket Tarifesi\s*{NUM}\s*₺"},
    {"brand": "varr TM", "url": "https://www.varr.com.tr",
     "ac": rf"Tüm AC Soketler\s*{NUM}\s*TL",
     "dc": rf"Tüm DC Soketler\s*;?\s*{NUM}\s*TL"},
    {"brand": "ZEPLİN CAR rental", "url": "https://www.zeplinenerji.com",
     "ac": rf"AC Soketler\s*=\s*{NUM}\s*TL",
     "dc": rf"DC Soketler\s*=\s*{NUM}\s*TL"},
]


def norm(s: str) -> str:
    s = (s or "").replace("İ", "I").replace("ı", "i").upper()
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


def robots_allows(url: str) -> bool:
    """Sitenin robots.txt kuralları bu sayfaya otomatik erişime izin veriyor mu? (Dosya yoksa izinli sayılır.)"""
    parts = urllib.parse.urlparse(url)
    parser = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            parser.parse(r.read().decode("utf-8", "ignore").splitlines())
    except Exception:  # noqa: BLE001 - robots.txt yok/okunamıyor
        return True
    return parser.can_fetch(BOT_NAME, url)


def page_text(url: str, raw: bool = False) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "tr"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "ignore")
    if raw:
        # Next.js gibi sitelerde veri, kaçışlı JSON olarak <script> içinde gömülüdür.
        return body.replace('\\"', '"')
    body = re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html.unescape(body))


def parse_range(pattern: str | None, text: str) -> dict | None:
    if not pattern:
        return None
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        raise ValueError(f"desen bulunamadı: {pattern}")
    values = sorted(float(v.replace(",", ".")) for v in m.groups() if v)
    if not values or not all(3 <= v <= 40 for v in values):  # TL/kWh için makul aralık
        raise ValueError(f"beklenmeyen değer: {values}")
    return {"min": values[0], "max": values[-1]}


def operator_names() -> dict[str, str]:
    """EPDK verisinden marka → işletmeci ünvanı (fiyatı istasyonlara bağlamak için)."""
    rows = json.loads((DATA_DIR / "stations.json").read_text(encoding="utf-8"))["data"]
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        by_op = counts.setdefault(norm(r.get("marka")), {})
        op = r.get("sarjAgiIsletmecisiUnvan") or ""
        by_op[op] = by_op.get(op, 0) + 1
    return {brand: max(ops, key=ops.get) for brand, ops in counts.items()}


def main() -> int:
    path = DATA_DIR / "prices.json"
    previous = {}
    if path.exists():
        previous = {p["brandKey"]: p for p in json.loads(path.read_text(encoding="utf-8")).get("operators", [])}
    names = operator_names()
    today = datetime.now(timezone.utc).date().isoformat()
    result, failures = [], 0

    for op in OPERATORS:
        key = norm(op["brand"])
        entry = {"brand": op["brand"], "brandKey": key, "operatorName": names.get(key),
                 "operatorKey": norm(names.get(key, "")), "source": op["url"], "note": op.get("note")}
        try:
            if not robots_allows(op["url"]):
                raise PermissionError("robots.txt otomatik erişime izin vermiyor")
            text = page_text(op["url"], raw=op.get("raw", False))
            entry.update(ac=parse_range(op.get("ac"), text), dc=parse_range(op.get("dc"), text),
                         checkedAt=today, stale=False)
            print(f"✓ {op['brand']}: AC {entry['ac']} DC {entry['dc']}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            old = previous.get(key)
            if old:
                entry.update(ac=old.get("ac"), dc=old.get("dc"), checkedAt=old.get("checkedAt"), stale=True)
            print(f"::warning::{op['brand']} fiyatı okunamadı ({error}); "
                  + ("önceki değer korunuyor." if old else "fiyat yok."))
            if not old:
                continue
        result.append(entry)
        time.sleep(1)

    payload = {"v": 1, "currency": "TRY", "unit": "kWh", "vatIncluded": True,
               "source": "Operatörlerin resmi web siteleri", "operators": result}
    new_json = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == new_json:
        print("Fiyatlarda değişiklik yok.")
    else:
        path.write_text(new_json, encoding="utf-8")
        print(f"Yazıldı: {len(result)} operatör ({failures} okunamadı).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
