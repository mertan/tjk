# Halka açık veri kaynakları ve erişim sınırları

Kontrol tarihi: **8 Ekim 2026**. Bu değerlendirme sağlayıcıların resmî yardım,
lisans ve geliştirici belgelerine dayanır. Sayfanın internette görüntülenebilmesi,
verisinin otomatik toplanmasına veya algoritmik işlenmesine izin verildiği
anlamına gelmez. Erişim engeli, CAPTCHA, ücretli erişim veya oturum sınırı aşılmaz.

Buradaki **API anahtarsız** kapsam piyasa verisi anahtarı gerektirmeyen ayrı
araştırma adaptörüdür. Fintable ve SEC'in HTTP/JSON servisleri teknik olarak API'dir;
"hiçbir HTTP API çağrısı yok" iddiasında bulunulmaz.

## Kaynak kararı

| Kaynak | Doğrulanan erişim ve gecikme | Adaptör kararı |
| --- | --- | --- |
| Fintable public fiyat API'si | [Resmî API belgeleri](https://fintable.io/docs) anahtarsız fiyat/OHLCV erişimini açıkça tanımlar; varsayılan IEX verisi, bir saate kadar önbellek gecikmesi. [Koşullar](https://fintable.io/terms-of-service) kişisel/ticari olmayan kullanım hakkı verir. | Kısıtlı kişisel araştırma kaynağı; güncellik her yanıtta doğrulanır. Konsolide hacim, bid/ask veya haber kaynağı değildir; tek başına `İZLE` sağlayamaz. |
| Yahoo Finance / yfinance | [Yahoo koşulları](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html?ncid=mbr_idnedulnk00000001) açık ön izin olmadan otomatik veri toplamayı yasaklar. [yfinance belgeleri](https://ranaroussi.github.io/yfinance/) Yahoo ile bağlantılı/onanmış olmadığını ve veri hakları için Yahoo koşullarının geçerli olduğunu belirtir. | Kapalı. Bir kütüphanenin açık kaynak lisansı veya erişilebilir chart endpoint'i veri toplama izni sağlamaz. |
| Cboe gecikmeli kotasyon tablosu | [Resmî kotasyon sayfası](https://www.cboe.com/delayed_quotes/ui) otomatik sorgu/yazılımla tablo indirmeyi açıkça yasaklar. | Kapalı; gecikmeli olması otomasyon izni yaratmaz. |
| TradingView | [Koşulların 3. bölümü](https://www.tradingview.com/policies/) görüntüleme dışı algoritmik kullanım ve risk yönetimi kullanımını sınırlar. [Otomatik toplama açıklaması](https://www.tradingview.com/support/solutions/43000674726-why-is-my-account-banned-due-to-suspicious-activity/) script, scraper ve benzeri toplama yöntemlerini yasaklar. | Otomatik fiyat/haber sağlayıcısı olarak kapalı. Ekran görüntüsü veya elle kopyalama, algoritmik kullanım izninin yerine geçmez. |
| Finviz ücretsiz sayfalar | [Güncel SSS](https://finviz.com/help/faq) NASDAQ, NYSE ve AMEX kapsamı ile **1 dakika** gecikme bildirir. Eski kaynaklardaki 15/20 dakika bilgisi bu kontrol için kullanılmadı. [robots.txt](https://finviz.com/robots.txt) genel filtreli screener ve export yollarını sınırlar; bazı hazır ekranlara izin vermesi ayrıca veri kullanım lisansı oluşturmaz. | Ücretsiz sayfalardan otomatik fiyat toplama izni doğrulanmadı; scraper kapalı. Sayfa erişim zamanı kotasyon zamanı değildir. |
| Finviz Elite dışa aktarımı | [Elite açıklaması](https://finviz.com/elite) CSV/Excel dışa aktarımı ve otomatik iş akışlarını destekler. [7 Ekim 2026 tarihli resmî açıklama](https://finviz.com/blog/our-upgraded-api-higher-limits-clearer-data-and-more-ways-to-connect/) tek seferlik CSV ile token gerektiren düzenli API kullanımını ayırır. | Yetkili kullanıcının resmî araçla aldığı yerel dışa aktarım değerlendirilebilir; bu ücretsiz anonim veri akışı değildir. Abonelik ve kullanım hakkı varsayılmaz. Güncel bid/ask, NBBO ve kaynak zaman damgası ayrıca doğrulanmalıdır. |
| Nasdaq Trader sembol dizini | [Yardım](https://www.nasdaqtrader.com/Trader.aspx?id=Help) günlük, oturumsuz sembol dosyası indirme ve tablo/veritabanına aktarmayı belgeler. [Telif koşulları](https://www.nasdaqtrader.com/Trader.aspx?id=CopyDisclaimMain) kişisel/ticari olmayan kopya istisnası ve bildirim koruma şartı içerir. | Yalnızca kişisel kullanımda sembol evreni; fiyat/hacim/spread kaynağı değildir. Ham dizin dosyaları depoda veya halka açık raporda yeniden yayımlanmaz. |
| SEC EDGAR | [Geliştirici belgeleri](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) submissions/XBRL JSON için anahtar veya kimlik doğrulama gerekmediğini bildirir. [Erişim kuralları](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) tanımlayıcı User-Agent ve toplam en fazla 10 istek/saniye ister. | Sembol/CIK eşleştirmesi ve finansman dosyaları için izin verilen otomatik kaynak. Kimlik bilgisi yerine gerçek uygulama/iletişim tanımı kullanılır; hız sınırı, 403/429 ve ağ reddi korunur. |
| Nasdaq.com finans sayfaları | [Genel koşullar](https://www.nasdaq.com/legal) veri analizi yazılımı geliştirmek için içerik çıkarımını yazılı izne bağlar. | Sayfa/screener scraper'ı kapalı. Nasdaq Trader'ın belgelenmiş indirme yoluyla karıştırılmaz. |
| Stooq | [Koşullar](https://stooq.com/t/) ve [tarihsel indirme](https://stooq.com/db/h/) kontrolünde tarayıcı doğrulaması istendi; güncel otomasyon izni doğrulanamadı. | Engelde duruldu; başka alan adı, kullanıcı aracısı veya CAPTCHA aşma denenmedi. Tarihsel dosya güncel bid/ask yerine kullanılamaz. |
| StockAnalysis | [Resmî API SSS](https://stockanalysis.com/help/faq/api-access/) programatik erişim sunulmadığını ve sağlayıcı lisanslarının programatik yeniden dağıtımı kapsamadığını belirtir. | İç endpoint'ler scraper'a bağlanmaz. |
| StockMarketScan | [Resmî belgeler](https://stockmarketscan.com/docs) anahtarsız sembol metadata'sında fiyat/gün bilgisini sunar; hacimli OHLCV uçları Basic+ ve anahtar gerektirir. | Anahtarsız fiyat+hacim ihtiyacı için yetersiz. |
| Alpaca / Twelve Data | [Alpaca hisse veri belgeleri](https://docs.alpaca.markets/us/docs/about-market-data-api) ve [Twelve Data belgeleri](https://twelvedata.com/docs) kimlik bilgisi/API anahtarı gerektirir. | Mevcut anahtarlar değiştirilmez; bu anahtarsız adaptörün kaynağı yapılmaz. |

Bu araştırmada **izni ve güncelliği doğrulanmış, anahtarsız canlı ABD
bid/ask/NBBO akışı bulunamadı**. Fintable fiyatı veya Finviz CSV desteği
NBBO doğrulaması sayılmaz. Bu adaptörün işlem yetkisi yoktur; `AL` üretmez.

## Fintable entegrasyonunun kapsamı

[API belgesi](https://fintable.io/docs) `Public Data API`, `Stock price`,
`Rate limits` ve `Caching` bölümleri:

- `GET https://fintable.io/api/v2/prices?symbols=AAA,BBB`: sağlayıcı sınırı 50;
  bu tarayıcı en fazla **20** sembol kullanır.
- `GET https://fintable.io/api/v2/prices/{symbol}/history`: `timeframe`,
  `start`, `end`, `limit` parametreleriyle OHLCV; en fazla 1000 bar.
- `Accept: application/json`; public fiyat ailesi **60 istek/dakika/IP**.
  `429` ve `Retry-After` korunur; engelde kaynak değiştirilerek kaçış yapılmaz.
- `price`, `volume`, `as_of`, `trading_day`, `feed` ayrı saklanır.
  Eksik sembol, boş alan veya eski zaman damgası veri varmış gibi tamamlanmaz.
- Varsayılan `feed=iex` yalnız IEX işlemleridir; hacim konsolide değildir.
  İnce hissede fiyat kapanışa dönebilir. Geçmiş barlar split/temettü ayarlıdır.
- Önbellek gecikmesi bir saate çıkabilir; kaynak işlem amaçlı tasarlanmamıştır.
  Güncellik eşiği gevşetilmez. Eksik bid/ask'tan spread türetilmez; haber kapsamı yoktur.

Hacim karşılaştırması aynı kaynak ve zaman kapsamını gerektirir. IEX gözlemleri
yalnız araştırma bağlamında gösterilir; **Fintable tek başına `İZLE` üretemez**.
Güncel ve yeterli veri yoksa sonuç `DATA_UNAVAILABLE / PAS` olur.

[Kullanım koşulları](https://fintable.io/terms-of-service) kişisel, ticari olmayan
indirme/görüntülemeye izin verir; hizmetin yeniden satışı ve rekabet eden ürün
geliştirme kısıtlıdır. Buradaki kapsam kullanıcının yerel kişisel araştırmasıdır;
ham verinin kamuya dağıtımı veya başka kullanıcılara hizmet sunumu için genel bir
lisans iddiası taşımaz. Bu koşul değerlendirmesi canlı erişimin çalıştığına dair
kanıt değildir.

## Sembol evreni ve zaman damgası

[Nasdaq Trader tanımları](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs)
`nasdaqlisted.txt` ve `otherlisted.txt` dosyalarını belgeler. İkinci dosyada
`Exchange=N` NYSE'dir; NYSE Arca veya NYSE MKT ile birleştirilmez. Test kayıtları,
ETF'ler ve hisse niteliği doğrulanamayan araçlar aday sayılmaz. Resmî
[otherlisted duyurusu](https://nasdaqtrader.com/tradernews.aspx?id=dtn2010-001)
web indirme yolunu da açıklar. HTTPS uç noktaları:

- `https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt`
- `https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt`

Dosyanın son satırındaki `File Creation Time` ham değeri korunmalıdır. Belgede
`1217200717:03` örneği vardır; incelenen Symbol Directory tanımında saat dilimi
açıkça belirtilmemiştir. UTC veya New York saati sessizce atanamaz.
[Sembol arama sayfasının](https://www.nasdaqtrader.com/trader.aspx?id=symbollookup)
"geçerli işlem günü" kapsamı, tekil fiyat veya kotasyon zaman damgası değildir.
Sayfadaki belirli Nasdaq events/listed verilerine ilişkin lisans istisnası,
Nasdaq.com fiyat/hacim uçlarının tamamı için otomasyon izni olarak yorumlanmaz.
İndirme zamanı, sağlayıcının üretim zamanı ve doğrulanmış piyasa gözlem zamanı
ayrı tutulur. Saat dilimi/güncellik belirsizliği raporda gizlenmez.

SEC'in `https://www.sec.gov/files/company_tickers_exchange.json` dosyası
CIK/ticker/borsa eşleştirmesi sağlar. SEC [erişim belgesinde](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
bu eşleştirmelerin dönemsel güncellendiğini ve doğruluk/kapsam garantisi
verilmediğini açıklar. Yalnızca dosyayı şimdi indirmek, bütün NASDAQ/NYSE
hisselerinin güncel ve eksiksiz tarandığını kanıtlamaz.

## Haber, finansman ve aday sınırı

SEC submissions yanıtları, [belgelere göre](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
dosyalar yayımlandıkça güncellenir; tipik işleme gecikmesi submissions için bir
saniyenin altındadır, yoğunlukta uzayabilir. Bu bildirim, şirket haberlerinin
tamamının kapsandığını veya finansman riskinin olmadığı sonucunu vermez.

[Geliştirici kuralları](https://www.sec.gov/about/developer-resources) toplam
10 istek/saniye sınırını kullanıcı başına, makine sayısından bağımsız uygular.
[SEC SSS](https://www.sec.gov/about/webmaster-frequently-asked-questions)
tanımlayıcı uygulama/kurum ve gerçek iletişim bilgisi içeren `User-Agent` ister.
Eksik iletişim bilgisi uydurulmaz; yapılandırma yoksa SEC incelemesi tamamlandı
sayılmaz. 403/429 veya ağ reddi, riskin bulunmadığı anlamına gelmez.

S-1/S-3/F-1/F-3, 424B, EFFECT ve ilgili 8-K/6-K kayıtları inceleme gerektiren
finansman işaretleridir. Form adı tek başına ATM/dilution tespiti değildir;
prospektüs, ekler, aktif raf kaydı ve satış anlaşmaları ayrıca okunmalıdır.
Eksik geçmiş veya tamamlanmamış inceleme "risk yok" olarak işaretlenemez.
Olumlu haber için yayımlanma zamanı, şirket/sembol ilişkisi ve asıl haber
kaynağı gerekir. Başlığın olumlu kelime içermesi yeterli değildir. İhraççı RSS
ve haber siteleri ancak ilgili kaynağın erişim/kullanım izni doğrulandığında
eklenebilir; genel bir izin varsayımı yapılmaz.

Fintable modunda bir çalıştırmada en fazla 20 sembol değerlendirilir; örneklem bütün NASDAQ/NYSE
evreni taranmış gibi sunulmaz. 1–5 USD aralığı ancak doğrulanmış güncel fiyatla
uygulanabilir. Fiyat aralığı, hacim artışı, momentum, spread, olumlu haber ve SEC
incelemesi birlikte yeterli kanıt sağlamadan `İZLE` verilmez.

Kaynak belgelerini araştırma aracıyla okumak, çalışan adaptörün bu ağlara
eriştiği anlamına gelmez. Araştırma sonuçları veya belgelerdeki örnek fiyatlar
canlı gözlem olarak aktarılmaz; ağ kısıtları aşılmaz. Bu belge bir canlı tarama
raporu değildir. Her çalıştırmanın gerçek veri zamanı, eksikleri ve erişim
sonucu kendi raporuna yazılır; veri sağlanamıyorsa **`DATA_UNAVAILABLE / PAS`**.
