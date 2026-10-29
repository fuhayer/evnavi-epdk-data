# evnavi-epdk-data

EVnavi iOS uygulamasının okuduğu, Türkiye elektrikli araç şarj istasyonları verisi.
Kaynak: EPDK Şarj@TR (`apigateway.epdk.gov.tr/sarjIstasyonlari`). GitHub Actions ile her gün güncellenir.

| Dosya | İçerik |
|---|---|
| `data/stations.json` | Tüm istasyonlar (halka açık + özel), EPDK alan adlarıyla |
| `data/meta.json` | Üretim zamanı, sürüm hash'i, istasyon sayıları |

## Neden kopya?

EPDK servisi istemci başına yaklaşık günde bir tam indirmeye izin veriyor (aşılınca
`429 ERR-227 QUOTA limit`). Uygulamalar EPDK'yı doğrudan çağırırsa kullanıcılar hızla kotaya
takılır. Bu repo veriyi günde bir kez çeker; uygulama bu statik dosyayı okur.

## Format

```json
{"v":1,"source":"EPDK","generatedAt":"2026-10-29T15:45:00+00:00","version":"abc123def456","data":[
  {"sarjIstasyonuNo":"ŞRJ/7918","sarjIstasyonuAdi":"…","marka":"…","sarjAgiIsletmecisiUnvan":"…",
   "sarjAgiIsletmecisiLisansNo":"…","adres":"…","enlem":39.9197,"boylam":32.8605,
   "hizmetSekli":"HALKA_ACIK","yesilSarjIstasyonuMu":"HAYIR",
   "soketler":[{"soketNo":"SKT/18029","soketTipi":"AC","soketTuru":"AC_TYPE2","soketGucu":"22"}]}
]}
```

`sarjIstasyonuIsletmecisi` alanı kişi adı içerebildiği için (KVKK) alınmaz.

## Elle çalıştırma

```bash
python3 scripts/fetch_epdk.py                       # tek istek atar
python3 scripts/fetch_epdk.py --from-raw raw.json   # istek atmadan ham yanıttan üretir
```

Veri © EPDK. Ticari kullanımdan önce EPDK kullanım koşullarını kontrol edin.
