# TJK Canlı Radar

Telefon uyumlu canlı TJK analiz paneli. Resmî TJK veri akışından günlük programı, AGF oranlarını, ganyan/ikili/sıralı ikili/çifte muhtemellerini ve her atın gün içindeki oran geçmişini alır.

[![Render'a Yayınla](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mertan/tjk)

## Özellikler

- Tarih, hipodrom ve koşu seçimi
- Ganyan açılış, güncel, en düşük ve en yüksek oran
- Açılıştan itibaren oran hareketi grafiği
- AGF, handikap puanı ve piyasa olasılığı
- Birinci aday, en çok destek, değer ve sürpriz sinyalleri
- 15 saniyede otomatik yenileme
- Telefona ana ekrana eklenebilen uygulama görünümü
- API anahtarı gerektirmez; sunucu TJK’nin açık veri akışına bağlanır

## Çalıştırma

Node.js 20 veya üzeri gerekir.

```bash
npm start
```

Tarayıcıda `http://localhost:4173` adresini açın. Telefon ve bilgisayar aynı Wi‑Fi ağındaysa bilgisayarın yerel IP adresiyle (`http://BILGISAYAR-IP:4173`) telefondan da açılabilir.

## Test

```bash
npm test
```

## Yayınlama

Yukarıdaki **Render'a Yayınla** düğmesi projeyi ücretsiz Node web servisi olarak kurar. `render.yaml`; Frankfurt bölgesi, `npm start` başlangıç komutu ve `/api/status` sağlık kontrolüyle hazırdır.

Uygulama bağımlılıksız bir Node.js sunucusudur. Statik barındırma tek başına yeterli değildir; TJK isteklerini aynı sunucunun güvenli biçimde aktarması gerekir.

## Model notu

Model; ganyan piyasasını, handikap puanını ve gerçek oran geçmişini 55:10:7 göreli ağırlıklarıyla birleştirir. AGF yalnızca bilgi amaçlı gösterilir; puan, sıralama, seçim ve sinyal gücüne katılmaz. Zorunlu veriler eksikse bütün koşu PAS olur. Model puanı ve sinyal gücü kalibre edilmiş kazanma olasılıkları değildir. Bu bir piyasa radarıdır, kesin kazanan modeli değildir. TJK açık akışı yatırılan toplam TL tutarını vermediği için “para yönü” oran daralması üzerinden gösterilir; kesin para miktarı olarak sunulmaz.


## CSV regresyonları ve API davranışı

Belmont 8 Ekim 2026 resmî CSV fixture'ı 5. ve 6. koşuların karışmasını tekrar sınar: 5. koşuda 9, 6. koşuda 8 kayıt, toplam 9 koşu. Dosya kaynağı ve SHA-256 değeri fixture yanında tutulur. Bu program verisi gerçek yarış sonucu değildir. Testler ağ bağlantısı gerektirmez: `npm test`.

API `analysis.status` ve `reasonCodes` döndürür. PAS'ta `runners=[]` ve bütün adaylar `null` olur; ham gözlemler `observations.runners` içindedir. Böylece `status` alanını henüz tanımayan tüketiciler PAS yanıtından yeniden aday üretemez. `modelProbability` geriye uyumluluk adıdır, gerçek kazanma olasılığı değildir.

**Canlı veri sınırı:** Mevcut kaynak koşuya özgü oran güncelleme zamanını doğrulamıyor. Canlı adaptör `SOURCE_FRESHNESS_UNVERIFIED / PAS` döndürür; koşunun planlanan saatini, günlük checksum saatini veya yerel indirme zamanını taze kotasyon kanıtı olarak kullanmaz. Puanlama motoru eksiksiz doğrulanmış girdiler için kullanılabilir; canlı adayları açmadan önce kaynak zamanının anlamı doğrulanmalıdır.

Yerel doğrulama: Node.js 24.19.0 üzerinde 35 test başarılı. HTTP yanıt gövdesi okunurken bağlantı kopması ve bozuk kaynak saatleri de PAS ile kapanır. GitHub Actions Node 20/24 matrisi yalnız test ve sözdizimi kontrolü çalıştırır; dağıtım adımı içermez.

## Tazelik, handikap, önbellek ve backtest

- **Tazelik:** Sabit `SOURCE_FRESHNESS_UNVERIFIED` kaldırıldı. Koşu yalnızca TJK nabzı (`checksum.datetime`, ≤180 sn) ve her koşan atın son zaman damgalı oran noktası (≤720 sn, saat kayması ≤120 sn) doğrulanırsa analiz edilir. Etiketler Europe/Istanbul (UTC+3) saatidir. Aksi halde `SOURCE_FRESHNESS_UNVERIFIED` + `QUOTE_STALE` / `QUOTE_TIMESTAMP_MISSING` / `SOURCE_HEARTBEAT_STALE` / `SOURCE_CLOCK_SKEW` ile PAS. Ayrıntı `freshness` alanında.
- **Handikap:** Yurt içi programlarda boş hücre = eksik (`RATING_MISSING`), yurt dışı programlarda `0` = puansız (`RATING_UNRATED`); yurt içi `0` gerçek sıfırdır. Eksik/puansız değer doldurulmaz, koşu PAS olur.
- **AGF:** Puana katılmaz; yalnızca `analysis.agfComparison` altında karşılaştırma için raporlanır.
- **TJK yükü:** Sınırlı LRU önbellek (`CACHE_MAX_ENTRIES`), eşzamanlı aynı istek birleştirme, global giden istek sınırlayıcı (`UPSTREAM_CONCURRENCY`, `UPSTREAM_MIN_INTERVAL_MS`) ve istemci başına `/api` hız sınırı (`RATE_LIMIT_PER_MIN`, varsayılan 120/dk; Render'da `TRUST_PROXY=1`).
- **Sağlık:** `/healthz` dış servise gitmez; Render sağlık kontrolü buna bağlıdır.
- **Backtest:** `node scripts/backtest.mjs --from 2026-09-26 --to 2026-10-09 --venues domestic --cutoff-min 5 --out rapor.json`. Tahmin yalnızca koşu saatinden `cutoff` dakika önceki zaman damgalı oranlar ve program CSV ile kurulur; sonuç koşu sonrası resmî GANYAN sırası (`R`) ve resmî sonuç CSV kazananıyla çapraz kontrol edilir. En az `--min-sample` (30) geçerli tahmin yoksa oran verilmez.
