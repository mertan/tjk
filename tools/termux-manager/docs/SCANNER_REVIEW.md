# ABD hisse tarayıcısı: kod ve risk incelemesi

Tarih: **2026-10-08**. İncelenen başlangıç commit'i:
`5186f22351950135fbbb920fd02460b3dc6411a3`. Tarayıcı, henüz main'e birleşmemiş
Termux yönetim API'si dalında bulunuyordu. Bu inceleme ayrı worktree ve ayrı dalda
yapıldı; mevcut PR #1'in dalı, main ve telefondaki kurulum değiştirilmedi.
Kaynak doğrulaması: [SCANNER_SOURCES.md](SCANNER_SOURCES.md).

## Sonuç ve kullanım sınırı

Bu sistem en fazla **20 önceden doğrulanmış sembol** için veri toplar ve en iyi
tek adayı manuel işlem hazırlığına ayırır. Bütün ABD piyasasını tarayan otomatik
keşif sistemi değildir. `HAZIRLIK`, `DRAFT_REQUIRES_MANUAL_REVIEW` anlamındadır;
`execution_enabled=false` bütün yollarda korunur. Veri veya kanıt eksikse
**PAS**. Gerçek piyasa sinyali, işlem kârlılığı veya emir gerçekleşmesi bu
incelemede test edilmedi.

## Düzeltilen bulgular

| Bulgu | Eski davranışın sentetik kanıtı | Düzeltme |
| --- | --- | --- |
| Kesin spread/bütçe sınırlarının yuvarlanması | Bid 2; ask 2.050000000000000000000000000001: iki spread sınırı da aşılmışken HAZIRLIK. Benzer küçük farklar nakit, sermaye ve risk sınırında kaybolabiliyordu. | Sınırlı girdi boyutuna uygun bağımsız 512 basamaklı Decimal bağlamı; yüzde spread karşılaştırmasında çarpım. |
| Eksik beş dakikalık mumun sonradan tamamlanmış sayılması | Bar isteği 14:04:55'te yapılırken açık olan 14:00 barı, haber/SEC işlemleri 14:05:05'te bitince tamamlanmış gibi kullanılıyordu. | Yalnız bar isteği başlarken tamamlanmış gözlemler tutulur. Gerekli pencere ilerlemişse eksik bar/PAS. |
| Yinelenen takvim günleriyle yanlış RVOL geçmişi | Bir önceki seansın on kopyası on geçmiş seans olarak sayılıyordu. | Tekil seans tarihleri, geçerli açılış/kapanış ve zaman aralıkları doğrulanır. |
| Eksik SEC finansman form kapsamı | S-3ASR/F-3ASR gibi kayıtlar eski dar listeye takılmayıp manuel clear devralabiliyordu. | ASR, MEF, D/DPOS, POS AM/POSASR türleri dahil genişletilmiş resmî form listesi. |
| Boş SEC form alanı | Boş veya bozuk form metadata'sı risk incelemesini durdurmuyordu. | Eksik/yanlış türde form `sec_form_invalid` ile durur. |
| Kotasyon koşullarının yok sayılması | `N`, `U`, `L`, `Z` koşullu güncel kotasyonlar da HAZIRLIK üretebiliyordu. | Yalnız tam `["R"]` kabul edilir; eksik/bilinmeyen/karışık koşullar PAS. Bu ihtiyatlı politika bütün geçerli kotasyon tiplerini desteklediği iddiası değildir. |

Kotasyonun normal olması durdurma olmadığını kanıtlamaz. Aday çıktısına
`manual_review_required` listesi eklenerek güncel halt/LULD, broker emir/stop
desteği ve bağımsız hesap/açık risk mutabakatı bekleyen kontroller olarak gösterilir.
Bu alanlar işlem yetkisi vermez.

## Risk kuralları ve test sonucu

| Kural | Uygulanan anlam ve doğrulama |
| --- | --- |
| 50.000 TL sermaye | Mevcut pozisyonların piyasa değeri + yeni alım tutarı + giriş ücreti sermaye tavanını aşmaz. Nakit ayrıca sınırdır. Bekleyen emir rezervasyonu varsa mutabakat olmadan PAS. |
| 12.500 TL tek pozisyon | Aynı sembolde mevcut piyasa değeri + yeni giriş debiti, giriş ücreti dahil, tavanı aşmaz. Yinelenen sembol pozisyonları toplanır. |
| Hisse 1–5 USD | Ask ve son işlem fiyatında her iki uç dahil. Aralık dışı fiyat PAS. |
| Spread | Hem mutlak 0,05 USD hem bid'e göre %2,5 sınırı sağlanmalı; sınırlar dahil. Ters kotasyon reddedilir. |
| Planlanan stop %3 | Ask × 0,97; mevcut taslakta sente yukarı yuvarlama nedeniyle fiili mesafe %3'ten küçük olabilir. Tahmini risk fiili stop, alış/satış USD/TRY, iki yön ücret ve kayma payını kullanır. |
| Günlük zarar 2.500 TL | Mevcut seansın ücretler sonrası gerçekleşmiş + gerçekleşmemiş net zararı eşikte veya üstündeyse PAS. Zarar + mevcut açık risk + yeni tahmini risk 2.500 TL'yi aşamaz. |

**Tamamı kurgusal** fiyat/kur/ücretlerle doğrulanan örnekler:

- Ask 2 USD, USD/TRY alış/satış 40/39,9, giriş/çıkış ücretleri 2'şer TL,
  kayma %0,25: **156 pay**, giriş debiti **12.482 TL**, stop **1,94 USD**,
  tahmini yeni risk **439,87 TL**.
- Aynı girdilerde günlük zarar **2.000 TL**, mevcut açık risk **400 TL**:
  **34 pay**, toplam günlük risk **2.499 TL**.
- Eşit kur 40, ücret/kayma sıfır, günlük zarar **2.497,60 TL**: bir payın
  2,40 TL riski toplamı tam **2.500 TL** yapar; daha küçük boşlukta **PAS**.
- Güncel net zarar **2.500 TL**: yeni aday yok, **PAS**.

Bunlar piyasa fiyatı veya yatırım önerisi değildir. %3 stop, boşluk, durdurma,
fiyat kayması veya kur hareketinde azami gerçekleşen zararı garanti etmez.

## Filtrelerin değerlendirmesi

- **Kaynak:** yalnız açıkça istenen Alpaca SIP; IEX, gecikmiş veri veya başka
  sağlayıcıya sessiz geçiş yok. SIP yetkisi beyanı tek başına yeterli değildir;
  gerçek yanıt ve zaman kontrolleri de gerekir. Anahtar/yetki yoksa ağ çağrısı
  yapılmadan PAS.
- **Tazelik:** kotasyon en fazla 15 saniye, son işlem 30 saniye; piyasa saati ve
  normal seans takvimi doğrulanır. USD/TRY ve hesap verisi en fazla 5 dakika.
- **Hacim/momentum:** tamamlanmış normal seans hacmi ≥1 milyon pay ve ≥2 milyon
  USD; on farklı önceki seansın aynı beş dakikalık aralığına göre RVOL ≥2;
  beş dakikalık getiri ≥%1; günlük getiri ≥%3; son fiyat VWAP üstünde.
  Bunlar mevcut tasarım eşikleridir, doğrulanmış kârlılık sonuçları değildir.
- **Haber:** son 24 saatte yayımlanmış Benzinga kaydı, sembol/URL/zaman eşleşmesi,
  maddi katalizör ve manuel inceleme gerekir. Earnings/FDA/contract/merger/guidance
  kategorileri desteklenir. Güncellenmiş haber önceki incelemeyi geçersiz kılabilir.
- **Finansman:** en az 365 günlük SEC metadata/arşiv kapsamı, güncel manuel
  belge incelemesi ve boş risk bayrağı gerekir. Kayıt/finansman bayrağı manuel
  clear ile kaldırılamaz. Metadata tek başına ATM/dilution olmadığını kanıtlamaz.
- **Birimler:** 2025-11-03 sonrası Alpaca quote miktarı pay adedidir; 100 ile
  çarpılmaz. Tarihsel barlarda split düzeltmesi kullanılır; kapanış uyuşmazlığı
  kurumsal işlem belirsizliği/PAS üretir.

## Açık kalan eksikler

1. Veri aboneliği ve canlı sağlayıcı erişimi bu görevde denenmedi. Sentetik
   başarının canlı veri kapsamını veya plan yetkisini kanıtladığı söylenemez.
2. Haber önem/yön değerlendirmesi ve SEC tam belge/ek okuması otomatik değildir.
   365 günden eski ama hâlâ açık bir ATM/raf kaydı manuel incelemede ayrıca
   araştırılmalıdır. İnsan beyanlarının doğruluğu kod tarafından kanıtlanmaz.
3. Bağımsız gerçek zamanlı halt/LULD kaynağı ve emir defteri derinliği yoktur.
   Normal R, taze spread ve görünen ask miktarı gerçekleşme garantisi değildir.
4. BtcTurk uygunluğu, nakit/kur/ücret, seans P/L ve `open_risk_try` güncel,
   kullanıcı tarafından sağlanan inceleme girdileridir. Pozisyon başına açık
   risk otomatik hesaplanıp broker hesabıyla mutabakat yapılmaz. Ücret girdileri
   güncel ve ilgili miktara uygun toplam ücret olmalıdır.
5. Günlük zarar eşiği güncel net P/L kontrolüdür; gün içinde bir kez eşik
   görüldükten sonra kalıcı kilit değildir. Sonraki güvenilir hesap görüntüsünde
   zarar eşik altına düşerse taslak yeniden oluşabilir. Sürekli izleme ve
   güvenilir broker mutabakatı olmadan kalıcı günlük durdurma iddiası yapılamaz.
6. Adi hisse türü ve BtcTurk'te işlem uygunluğu ayrıca doğrulanır. Alpaca
   `us_equity` veya `tradable` alanları BtcTurk yetkisini kanıtlamaz.
7. Desteklenen BtcTurk Hisse müşteri emir API'si doğrulanmadı. Kripto API'si
   veya uygulamanın kurumlar arası DriveWealth bağlantısı yerine kullanılamaz.
   Emir bağlayıcısı ve gerçek alım-satım eklenmedi.

## Doğrulama ve değişiklik kapsamı

Başlangıçta 246 test geçti. Bu PR sonrası **269 test**, Cloud Linux üzerinde
Python **3.12.14** ve **3.13.13** ile geçti. Bunlar 41 risk, 45 sağlayıcı ve
10 sağlayıcı→risk bütünleşim testi dahil tüm yönetim servisi regresyonlarını
kapsar. Risk testlerinden biri ayrıca **324 kurgusal kombinasyonu** denetler.

Tam testler gerçek credential içermeyen süreç ortamlarında çalıştırıldı.
Uzak piyasa/telefon çağrısı ve gerçek emir yoktur. Yeni testler yalnız sentetik
transport, fiyat, haber, SEC kaydı ve hesap verileri kullanır. HMAC/kurulum/
HTTP sınır testleri de geçti. Kod, test, kaynak raporu ve bu inceleme belgesi
dışında değişiklik yoktur; mevcut anahtarlar veya telefon servisleri okunmadı/
değiştirilmedi.

```sh
cd tools/termux-manager
python3 -B -m unittest discover -s tests -v
```

PR #1 main'e birleşene kadar bu PR'ın tabanı
`codex/termux-management-api-20261008` dalıdır; diff yalnız tarayıcı
incelemesini içerir. Ana dal veya mevcut API PR'ı otomatik birleştirilmez.
