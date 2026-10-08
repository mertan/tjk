# Hacim ve fiyat hareketi için aday kaynağı araştırması

Kontrol tarihi: **8 Ekim 2026**. Bu belge canlı fiyat veya hisse önerisi içermez.
Resmî kaynak belgelerini okumak, tarayıcının canlı veriye eriştiğini kanıtlamaz.
Anahtarsız kullanım, sınırsız veya her amaç için lisanslı kullanım anlamına gelmez.

## Fintable: izinli fiyat erişimi, belgelenmiş keşif ucu yok

[Fintable belgeleri](https://fintable.io/docs), halka açık ve kimlik doğrulamasız
`GET /api/v2/prices?symbols=...`, tek sembol fiyatı ve OHLCV geçmişini tanımlar.
Toplu fiyat isteği sembollerin önceden bilinmesini gerektirir; sağlayıcı sınırı
50'dir. Tarayıcının **20 sembol** sınırı değişmez. İncelenen belgelerde en aktif
hisseler, yükselenler veya tüm evreni sıralayan bir uç tanımlanmıyor.

`price`, `previous_close`, `change_percent`, `volume`, `trading_day`, `as_of` ve
`feed` alanları sıralama girdisi olabilir. Fakat varsayılan `iex`, yalnız IEX
işlemlerini kapsar: hacim konsolide değildir, az işlem gören hissede fiyat günün
kapanışına dönebilir. Yanıttaki `as_of` gerçek gözlem zamanı olarak korunmalıdır.
İndirme zamanı bu alanın yerine geçmez. Önbellek bir saate kadar gecikme
oluşturabilir; sağlayıcı veriyi işlem amaçlı tasarlamadığını açıklar.

Public fiyat ailesi **60 istek/dakika/IP** sınırına tabidir. `429`, erişim reddi
ve CAPTCHA aşılmaz. Kaynak değiştirerek veya toplu evreni 20'lik parçalara bölerek
bu tarayıcının sınırı dolanılmaz. [Kullanım koşulları](https://fintable.io/terms-of-service)
da geçerlidir. Fiyat sıralaması NBBO, spread, haber veya finansman incelemesi
yerine geçmez.

## Diğer kaynaklar

| Kaynak | Resmî kanıt | Karar |
| --- | --- | --- |
| Finviz Elite | [Elite sayfası](https://finviz.com/elite), screener ve fiyat dışa aktarımlarını otomatik iş akışlarında kullanmayı açıkça destekler. [SSS](https://finviz.com/help/faq) dışa aktarım/API erişimini Elite özelliği olarak tanımlar; ücretsiz kotasyonları 1 dakika gecikmeli belirtir. | Kullanıcının geçerli yetkiyle resmî araçtan aldığı yerel dışa aktarım incelenebilir. Abonelik varsayılmaz, giriş yapılmaz ve ücretsiz sayfa kazıması eklenmez. CSV'nin oluşturulma zamanı, her kotasyonun gözlem zamanı değildir. |
| TradingView | [Koşullar, bölüm 3](https://www.tradingview.com/policies/) görüntüleme dışı algoritmik kullanım, risk yönetimi ve içerik işleme için kısıtlar getirir. | Bu tarayıcı için otomatik keşif kaynağı olarak kullanılmaz; elle dışa aktarım aynı kullanım kısıtını kendiliğinden kaldırmaz. |
| Cboe Most Active | [ABD hisse sayfası](https://www.cboe.com/markets/us/equities) Cboe borsalarının en aktif hisselerini gösterir. [İçerik kullanımı](https://www.cboe.com/use-of-content) ön onay ve imzalı lisans ister; [genel koşullar](https://www.cboe.com/terms) kişisel görüntüleme/indirme hakkını diğer kullanımlardan ayırır. | Bu uygulama için gerekli kullanım hakkı doğrulanmadığından otomatik kaynak eklenmez. Cboe hacmi bütün piyasanın konsolide hacmi olarak sunulmaz. |
| Nasdaq Trader sembol dosyaları | [Dosya tanımları](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs), sembol ve listeleme bilgilerini belgeler. | NASDAQ/NYSE evrenini doğrular; fiyat hareketi veya hacim sıralaması sağlamaz. |

## Uygulama için sonuç

Bu araştırmada kullanım izni doğrulanmış, ücretsiz ve anahtarsız bir
**tüm piyasa en aktif/yükselenler keşif akışı bulunamadı**. Bu, böyle bir
kaynağın hiçbir yerde bulunmadığı iddiası değildir. Fintable'ın belgelenmiş
uçları bilinen sembolleri sorgulamaya uygundur; alfabetik ilk 20 seçimini
piyasa hacmine dayalı keşif gibi sunmak doğru değildir.

Sıralama için kaynak/kullanım hakkı incelenmiş bir yerel gözlem kümesi gerekir.
Kaynak, borsa, fiyat, hacim kapsamı, fiyat hareketinin karşılaştırma temeli,
gözlem zamanı, seans tarihi ve gecikme bilgisi birlikte değerlendirilmelidir.
Farklı borsa kapsamındaki hacimler eşdeğer kabul edilmemelidir. Fiyat değişimi,
hacim ve zaman bilgisi eksik, çelişkili veya eskiyse aday üretilmemelidir.

Seçilen en fazla 20 sembol için mevcut risk ve veri doğrulamaları ayrıca
çalışmalıdır. Sıralamadaki yer, `AL`, `İZLE` veya bir işlem önerisi değildir.
Geçerli sıralama girdisi yoksa alfabetik seçime sessizce dönmek yerine
**`DATA_UNAVAILABLE / PAS`** ve eksik girdinin nedeni raporlanmalıdır.
