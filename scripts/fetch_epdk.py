#!/usr/bin/env python3
"""EPDK Şarj@TR istasyon listesini çekip EVnavi'nin okuduğu statik dosyalara dönüştürür.

EPDK servisi gövdeli GET ({}) bekler ve çok dar bir kotası vardır (istemci başına
yaklaşık günde bir tam indirme). Bu yüzden script her çalıştırmada tek istek atar,
tekrar denemez; hata durumunda mevcut veri olduğu gibi korunur.

Kullanım:
    python3 scripts/fetch_epdk.py                       # EPDK'dan çek, data/ altına yaz
    python3 scripts/fetch_epdk.py --save-raw raw.json   # ham yanıtı da sakla
    python3 scripts/fetch_epdk.py --from-raw raw.json   # istek atmadan ham yanıttan üret
"""

import argparse
import hashlib
import http.client
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST = "apigateway.epdk.gov.tr"
PATH = "/sarjIstasyonlari"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Uygulamanın kullandığı alanlar. "sarjIstasyonuIsletmecisi" kişi adı içerebildiği için (KVKK) alınmaz.
KEPT_FIELDS = (
    "sarjIstasyonuNo",
    "sarjIstasyonuAdi",
    "marka",
    "sarjAgiIsletmecisiUnvan",
    "sarjAgiIsletmecisiLisansNo",
    "adres",
    "enlem",
    "boylam",
    "hizmetSekli",
    "yesilSarjIstasyonuMu",
    "soketler",
)


def fetch_raw() -> bytes:
    conn = http.client.HTTPSConnection(HOST, timeout=120)
    try:
        conn.request("GET", PATH, body=b"{}", headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "evnavi-epdk-data/1.0",
        })
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    if response.status != 200:
        raise RuntimeError(f"EPDK HTTP {response.status}: {body[:300]!r}")
    return body


def valid_station(row: dict) -> bool:
    lat, lon = row.get("enlem"), row.get("boylam")
    return (
        bool(row.get("sarjIstasyonuNo"))
        and isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        and 35 <= lat <= 43 and 25 <= lon <= 45
    )


def transform(raw: bytes) -> list[dict]:
    payload = json.loads(raw)
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Beklenmeyen EPDK yanıtı: {str(payload)[:300]}")
    stations = [{k: row[k] for k in KEPT_FIELDS if k in row} for row in rows if valid_station(row)]
    stations.sort(key=lambda s: s["sarjIstasyonuNo"])
    return stations


def write_outputs(stations: list[dict]) -> bool:
    """Veri değiştiyse dosyaları yazar; değiştiyse True döner."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stations_path = DATA_DIR / "stations.json"
    meta_path = DATA_DIR / "meta.json"

    data_json = json.dumps(stations, ensure_ascii=False, separators=(",", ":"))
    version = hashlib.sha256(data_json.encode()).hexdigest()[:12]

    if meta_path.exists() and json.loads(meta_path.read_text()).get("version") == version:
        print(f"Değişiklik yok ({len(stations)} istasyon, sürüm {version}).")
        return False

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    public_count = sum(1 for s in stations if s.get("hizmetSekli") == "HALKA_ACIK")
    stations_path.write_text(
        '{"v":1,"source":"EPDK","generatedAt":"%s","version":"%s","data":%s}\n'
        % (generated_at, version, data_json),
        encoding="utf-8",
    )
    meta_path.write_text(json.dumps({
        "v": 1,
        "source": "EPDK Şarj@TR (apigateway.epdk.gov.tr/sarjIstasyonlari)",
        "generatedAt": generated_at,
        "version": version,
        "stationCount": len(stations),
        "publicStationCount": public_count,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Yazıldı: {len(stations)} istasyon ({public_count} halka açık), sürüm {version}.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-raw", type=Path)
    parser.add_argument("--from-raw", type=Path)
    args = parser.parse_args()

    try:
        raw = args.from_raw.read_bytes() if args.from_raw else fetch_raw()
        if args.save_raw:
            args.save_raw.write_bytes(raw)
        stations = transform(raw)
    except Exception as error:  # noqa: BLE001 - mevcut veri korunur, iş akışı başarısız sayılmaz
        print(f"::warning::EPDK verisi alınamadı, mevcut veri korunuyor: {error}")
        return 0

    write_outputs(stations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
