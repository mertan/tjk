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

Model; normalize ganyan olasılığını, AGF’yi, handikap puanını ve gerçek oran hareketini birleştirir. Bu bir piyasa radarıdır, kesin kazanan modeli değildir. TJK açık akışı yatırılan toplam TL tutarını vermediği için “para yönü” oran daralması üzerinden gösterilir; kesin para miktarı olarak sunulmaz.
