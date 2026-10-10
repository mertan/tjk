# ABD hisse tarayıcısı: kaynak doğrulaması

Kontrol tarihi: **2026-10-08 (UTC)**. Bu inceleme resmî, herkese açık belgelerin
okunmasına dayanır. Kullanıcının anahtarları, veri aboneliği veya hesabı
okunmadı; canlı fiyat, telefon isteği ve finansal emir gönderilmedi. Dokümanda
bir hizmetin bulunması, kullanıcının o hizmete erişim yetkisini kanıtlamaz.

## Piyasa verisi ve kapsam

| Kaynak | Doğrulanan özellik | Tarayıcı için sonuç |
| --- | --- | --- |
| [Alpaca abonelikleri](https://docs.alpaca.markets/us/docs/about-market-data-api) | Basic hisse verisi gerçek zamanda yalnız IEX; Algo Trader Plus tüm ABD borsalarını kapsar. Hisse ve ETF aynı veri kapsamındadır. | Spread ve bütün piyasa hacmi için açıkça `feed=sip` gerekir. IEX veya 15 dakika gecikmiş veri sessiz yedek olamaz. |
| [Alpaca snapshots](https://docs.alpaca.markets/us/reference/stocksnapshots-1) | Son işlem, son kotasyon, dakika/gün ve önceki gün barı bulunur. Aboneliğe göre varsayılan feed değişir. | Son fiyat tek başına spread değildir; bid/ask ve kendi zaman damgaları gerekir. Varsayılan feed kullanılmamalıdır. |
| [Alpaca tarihsel barlar](https://docs.alpaca.markets/us/reference/stockbars) | `5Min`, SIP, sayfalama ve `adjustment=split` desteklenir. Split ayarı ileri/ters bölünmeler için hem fiyatı hem hacmi düzeltir. | Beş dakikalık momentum ve aynı New York saat dilimi aralığının tarihsel hacim karşılaştırması için uygundur. Eksik sayfalar ve tamamlanmamış barlar kanıt değildir. |
| [Alpaca haberleri](https://docs.alpaca.markets/us/v1.1/reference/news-3) | `/v1beta1/news`; sembol filtresi, en fazla 50 haber/sayfa, güncelleme zamanına göre sıralama, isteğe bağlı içerik ve sayfalama vardır. | Başlık eşleşmesi katalizör doğrulaması sağlamaz. `include_content=false` ile tam metin analizi yapıldığı söylenemez. |
| [SEC Submissions](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Şirket/CIK bazında başvuru geçmişi ve metadata; en az bir yıllık veya en son 1.000 başvuru, hangisi daha fazlaysa; ek tarih dosyaları bulunabilir. | Finansman belgelerini bulmak için birincil kaynak; metadata finansman riski olmadığı sonucunu vermez. |

Alpaca veri erişimi kimlik doğrulaması gerektirir. Yerel
`ALPACA_SIP_CONFIRMED=yes` beyanı tek başına abonelik kanıtı değildir: SIP isteğinin
başarısı, güncel kotasyon/işlem zamanları ve piyasa saati birlikte denetlenmelidir.
[Alpaca FAQ](https://docs.alpaca.markets/us/docs/market-data-faq), güncel SIP
uçlarında abonelik gerektiğini ve aboneliksiz tarihsel SIP sorgularının en az
15 dakika eski olması gerektiğini açıklar. Aynı belge son veri uçlarının
bölünme düzeltmesi uygulamadığını belirtir; bu nedenle önceki snapshot kapanışı
ile split düzeltilmiş bar kapanışı karıştırılmamalıdır. Belirsiz kurumsal işlem
veya uyuşmazlıkta sonuç **PAS** olmalıdır.

Mevcut adaptör en fazla 20 açıkça yapılandırılmış sembolü inceler. Tüm ABD
piyasasını kendiliğinden keşfeden bir tarayıcı değildir. Alpaca'nın
[`us_equity` varlık sınıfı](https://docs.alpaca.markets/us/reference/get-v2-assets-1)
tek başına adi hisse sınıflandırmasının kanıtı sayılmamalıdır. ETF, ADR,
varant, imtiyazlı hisse veya başka bir menkul kıymeti yalnız sembol biçiminden
ayıklamak yeterli değildir. Tür ve broker işlem uygunluğu ayrıca doğrulanmalıdır;
bir sağlayıcıdaki işlem uygunluğu BtcTurk uygunluğunu kanıtlamaz.

## Spread, kotasyon miktarı ve işlem durumu

[Alpaca'nın resmî değişiklik kaydı](https://docs.alpaca.markets/us/v1.1/changelog/marketdata-bid-and-ask-size-display-change),
CTA/UTP hisse kotasyon miktarlarının **2025-11-03 itibarıyla round lot yerine
pay adedi** olarak gösterildiğini doğrular. Dolayısıyla güncel `bs`/`as`
değerlerini 100 ile çarpmak likiditeyi yanlış büyütür. Önceki tarihlerin miktar
birimi ayrı değerlendirilmelidir. Bu değişimle round lot büyüklüğü de fiyat
kademesine bağlıdır; tüm fiyatlar ve tarihler için sabit 100 varsayımı doğru
değildir.

Spread kontrolü için iki koşul birlikte uygulanmalıdır:
`ask - bid <= 0.05 USD` ve `(ask - bid) / bid <= 0.025`. Pozitif miktar ve fiyat,
alış/satış sırası, tazelik ve kaynak kapsamı da gerekir. Görünen en iyi fiyatın
miktarı derin emir defteri veya gerçekleşme garantisi değildir.

[Alpaca quote condition kaynağı](https://docs.alpaca.markets/us/reference/stockmetaconditions-1)
kotasyon koşullarının ayrı sözlüğünü sunar. [UTP teknik şartnamesi,
bölüm 7.2](https://utpplan.com/DOC/UtpBinaryInputSpec_3.0.pdf), `R` koşulunu
normal iki taraflı otomatik kotasyon; `N` ve `U` koşullarını bağlayıcı olmayan,
`L` koşulunu kapalı kotasyon, `Z` koşulunu açılmama/yeniden başlamama olarak
tanımlar. Sayısal spreadin dar olması bu durumları güvenli hale getirmez.
Yalnız normal kotasyonları kabul etmek ihtiyatlı bir tarama politikasıdır;
bu, tüm işlem durdurmalarının saptandığını kanıtlamaz. İncelenen REST belgeleri,
her uygunsuz koşulun son kotasyondan önceden ayıklandığına ilişkin yeterli bir
garanti sağlamıyor. Güncel sembol bazlı halt/LULD durumu ayrı bir veri gerektirir.

CTA tarafı da ayrıca doğrulandı: [CQS Pillar çıktı şartnamesi, Ek H,
sayfa 99–100](https://www.ctaplan.com/publicdocs/ctaplan/CQS_Pillar_Output_Specification_Odd_Lots.pdf)
`R` kodunu normal kotasyon olarak tanımlar; `C`/`L` kapanış, `N` bağlayıcı
olmayan, `U` yavaş ve otomatik gerçekleşmeye uygun olmayan kotasyondur.
[Alpaca'nın resmî snapshot örneği](https://alpaca.markets/learn/snapshot-api)
`latestQuote.c` alanını `["R"]` olarak gösterir. Bu nedenle yalnız tam olarak
`["R"]` kabul edip eksik, bilinmeyen veya ek koşullu kayıtları **PAS** yapmak,
CTA ve UTP için desteklenen ihtiyatlı bir seçimdir. Bazı başka koşullar da BBO
hesabına uygun olabilir; bu dar kabul listesi onların tümünü modellemez.
`R` sıfır fiyat/miktarla da gelebilir; pozitif fiyat, miktar, tazelik ve spread
kontrollerinin yerine geçmez. Ayrı işlem durumu/LULD verisinin yerini de tutmaz.

## Hacim, momentum ve haber katalizörü

Tamamlanmış normal seans barları, resmi takvim, tatil/erken kapanış ve
`America/New_York` saat dilimi kullanılmalıdır. Saat dilimi yaz saati geçişini
hesaba katmalıdır. Tek borsa hacmi tüm piyasa hacmi yerine konamaz. On önceki
seansın aynı beş dakikalık aralığına göre RVOL, gün içindeki doğal hacim
değişimini azaltır; bu istatistik stratejinin kârlılığını kanıtlamaz.

Bir milyon pay, iki milyon USD günlük hacim, RVOL eşiği, beş dakikalık/günlük
momentum ve VWAP koşulları **strateji seçimleridir**; sağlayıcı tarafından
doğrulanmış işlem sinyalleri değildir. Düşük fiyatlı hisselerde boş barlar,
ters bölünmeler ve durdurmalar özellikle önemlidir. Katı eksik veri reddi bazı
işlem gören hisseleri de dışlayabilir; kayıp veri tamamlanmış gibi gösterilmez.

[Alpaca haber akışı](https://docs.alpaca.markets/us/docs/streaming-real-time-news)
Benzinga kaynak alanı, semboller, yayımlanma/güncellenme zamanı, başlık, özet,
bağlantı ve içerik alanlarını belgeler. Adaptörün yalnız haber metadata'sını
alması durumunda olumlu/kötü haber ayrımı, olayın büyüklüğü ve yayıncının
dayandığı şirket/FDA gibi birincil belgenin doğruluğu insan incelemesine
bağlıdır. Haber üzerinde sonradan değişiklik yapılması önceki incelemeyi
geçersiz kılabilir. Haber ve finansman incelemesi güncel değilse **PAS**.
Son bir sayfada bulunmayan bir haber için kayıt uydurulmamalı; sayfalama
sınırından ötürü gözden kaçmış olabileceği açık bir kısıttır.

## ATM, dilution ve finansman kontrolü

SEC metadata'sında belge bulunmaması, kısa açıklamada anahtar kelime geçmemesi
veya haber başlığının olumlu olması mevcut ATM programını, dönüştürülebilir borcu,
varantı ya da finansman ihtiyacını ortadan kaldırmaz. Önceki yıllarda açılmış bir
raf kaydı halen kullanılabilir; 365 günlük tarama tek başına tam inceleme değildir.
Şirket belgeleri, ekler, prospektüsler ve güncel sermaye yapısı ayrıca okunmalıdır.

[SEC'nin resmî submission type listesi](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgarlink-online-submission-types)
yalnız `S-1`/`S-3` ve `F-1`/`F-3` dışında `S-1MEF`, `S-3ASR`, `S-3MEF`,
`F-1MEF`, `F-3ASR`, `F-3MEF`, `POSASR` ve başka kayıt türleri içerir.
[SEC form indeksinin açıklamasına göre](https://www.sec.gov/file/edgarfilermmanual-vol2-c3),
ASR otomatik raf kaydıdır; MEF varyantları Rule 462(b) altında ilave menkul
kıymet kaydı içerir; POSASR bu raf kayıtlarının sonraki değişikliklerini ifade
eder. Bu varyantları atlayan eşitlik listesi eksik risk kontrolüdür. Bir kayıt
başvurusu tek başına yeni hisselerin bugün satıldığını kanıtlamaz; otomatik
tarayıcının bu kaydı inceleme gerektiren risk olarak engellemesi ayrı bir
ihtiyatlı politikadır.

[SEC erişim kuralı](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
tanımlanmış User-Agent ve en fazla saniyede 10 istek sınırı belirtir. Mevcut
adaptörün süreç başına saniyede en fazla 2 istek yaklaşımı bu üst sınırın
altındadır; aynı çıkış IP'sini kullanan başka süreçler ayrıca hesaba katılmalıdır.
SEC erişim hatası veya eksik arşiv kapsamı “risk yok” sonucuna dönüştürülemez.

## Alternatif veri sağlayıcı

[Massive son kotasyon API'si](https://massive.com/docs/rest/stocks/trades-quotes/last-quote)
NBBO bid/ask, miktar ve zaman damgasını;
[tarihsel kotasyon API'si](https://www.massive.com/docs/rest/stocks/trades-quotes/quotes)
kotasyon geçmişini belgeler. Gerçek zamanlı kapsam uygun ücretli plan/yetki
gerektirir. Belgelenmiş bir alternatif olması bu depoda entegre veya test edilmiş
olduğu anlamına gelmez. Eklenirse feed kapsamı, miktar birimi, timestamp,
kurumsal işlemler, koşul kodları ve veri lisansı ayrı doğrulanmalıdır.
Mevcut sistemde sessiz sağlayıcı değişimi yapılmaz.

## BtcTurk Hisse gerçek emir API'sinin durumu

[BtcTurk Hisse resmî sitesi](https://hisse.btcturk.com/), ABD hisselerine mobil
uygulama üzerinden erişimi anlatır. [Yurt dışı emir gerçekleştirme
politikası](https://cdn.btcturk.com/btcturkhisse/Islem_Araciligi_Birimi_Yurt_Disi_Piyasalar_Emir_Gerceklestirme_Politikasi.pdf)
sayfa 3'te karşı kurum olarak DriveWealth'i ve uygulamanın API bağlantısını;
sayfa 4'te müşterilerin mobil uygulama üzerinden işlem yapmasını açıklar.
Bu, kurumlar arasındaki entegrasyonu gösterir; müşteriye özel bir bot API'si
erişimi veya kullanıcıya verilmiş DriveWealth API yetkisi sağlamaz.

[BtcTurk emir API'si](https://docs.btcturk.com/docs/private-endpoints/submit-order/)
**Kripto** belgesidir; BTCUSDT/BTCTRY gibi çiftler kullanır. Bu uç ABD hisse
emirleri için kullanılamaz. [API yetki belgesi](https://docs.btcturk.com/docs/api-access-permissions/)
de anahtarların Kripto sitesinden oluşturulduğunu açıklar.

2026-10-08 tarihinde incelenen resmî Hisse sitesi, politika ve API belgelerinde
perakende müşterinin ABD hisse emri göndermesi için desteklenen endpoint,
kimlik doğrulama, yetki kapsamı, emir yaşam döngüsü ve test ortamı
**doğrulanamadı**. Bu sonuç “BtcTurk içinde hiçbir API yok” iddiası değildir.
Desteklenen müşteri entegrasyonu bu bileşenlerle resmen doğrulanmadan gerçek
emir bağlayıcısı eklenmemeli; mobil uygulamanın özel uçları taklit edilmemelidir.
Tarayıcı ve yönetim servisi `execution_enabled=false` olarak kalır.
