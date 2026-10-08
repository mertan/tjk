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
