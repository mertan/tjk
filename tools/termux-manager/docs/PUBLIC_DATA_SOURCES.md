# Halka açık veri kaynakları ve erişim sınırları

Kontrol tarihi: **8 Ekim 2026**. Bu değerlendirme sağlayıcıların resmî yardım,
lisans ve geliştirici belgelerine dayanır. Sayfanın internette görüntülenebilmesi,
verisinin otomatik toplanmasına veya algoritmik işlenmesine izin verildiği
anlamına gelmez. Erişim engeli, CAPTCHA, ücretli erişim veya oturum sınırı aşılmaz.

Buradaki **API'siz** kapsam Alpaca/piyasa verisi API anahtarı gerektirmeyen ayrı
adaptördür. SEC'in anahtarsız HTTP/JSON servisi teknik olarak bir API'dir;
"hiçbir HTTP API çağrısı yok" iddiasında bulunulmaz.

## Kaynak kararı

| Kaynak | Doğrulanan erişim ve gecikme | Adaptör kararı |
| --- | --- | --- |
| TradingView | [Koşulların 3. bölümü](https://www.tradingview.com/policies/) görüntüleme dışı algoritmik kullanım ve risk yönetimi kullanımını sınırlar. [Otomatik toplama açıklaması](https://www.tradingview.com/support/solutions/43000674726-why-is-my-account-banned-due-to-suspicious-activity/) script, scraper ve benzeri toplama yöntemlerini yasaklar. | Otomatik fiyat/haber sağlayıcısı olarak kapalı. Ekran görüntüsü veya elle kopyalama, algoritmik kullanım izninin yerine geçmez. |
| Finviz ücretsiz sayfalar | [Güncel SSS](https://finviz.com/help/faq) NASDAQ, NYSE ve AMEX kapsamı ile **1 dakika** gecikme bildirir. Eski kaynaklardaki 15/20 dakika bilgisi bu kontrol için kullanılmadı. [robots.txt](https://finviz.com/robots.txt) genel filtreli screener ve export yollarını sınırlar; bazı hazır ekranlara izin vermesi ayrıca veri kullanım lisansı oluşturmaz. | Ücretsiz sayfalardan otomatik fiyat toplama izni doğrulanmadı; scraper kapalı. Sayfa erişim zamanı kotasyon zamanı değildir. |
| Finviz Elite dışa aktarımı | [Elite açıklaması](https://finviz.com/elite) CSV/Excel dışa aktarımı ve otomatik iş akışlarını destekler. [7 Ekim 2026 tarihli resmî açıklama](https://finviz.com/blog/our-upgraded-api-higher-limits-clearer-data-and-more-ways-to-connect/) tek seferlik CSV ile token gerektiren düzenli API kullanımını ayırır. | Yetkili kullanıcının resmî araçla aldığı yerel dışa aktarım değerlendirilebilir; bu ücretsiz anonim veri akışı değildir. Abonelik ve kullanım hakkı varsayılmaz. Güncel bid/ask, NBBO ve kaynak zaman damgası ayrıca doğrulanmalıdır. |
| Nasdaq Trader sembol dizini | [Yardım](https://www.nasdaqtrader.com/Trader.aspx?id=Help) günlük, oturumsuz sembol dosyası indirme ve tablo/veritabanına aktarmayı belgeler. [Telif koşulları](https://www.nasdaqtrader.com/Trader.aspx?id=CopyDisclaimMain) kişisel/ticari olmayan kopya istisnası ve bildirim koruma şartı içerir. | Yalnızca kişisel kullanımda sembol evreni; fiyat/hacim/spread kaynağı değildir. Ham dizin dosyaları depoda veya halka açık raporda yeniden yayımlanmaz. |
| SEC EDGAR | [Geliştirici belgeleri](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) submissions/XBRL JSON için anahtar veya kimlik doğrulama gerekmediğini bildirir. [Erişim kuralları](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) tanımlayıcı User-Agent ve toplam en fazla 10 istek/saniye ister. | Sembol/CIK eşleştirmesi ve finansman dosyaları için izin verilen otomatik kaynak. Kimlik bilgisi yerine gerçek uygulama/iletişim tanımı kullanılır; hız sınırı, 403/429 ve ağ reddi korunur. |
| Nasdaq.com finans sayfaları | [Genel koşullar](https://www.nasdaq.com/legal) veri analizi yazılımı geliştirmek için içerik çıkarımını yazılı izne bağlar. | Sayfa/screener scraper'ı kapalı. Nasdaq Trader'ın belgelenmiş indirme yoluyla karıştırılmaz. |
| Stooq | [Koşullar](https://stooq.com/t/) ve [tarihsel indirme](https://stooq.com/db/h/) kontrolünde tarayıcı doğrulaması istendi; güncel otomasyon izni doğrulanamadı. | Engelde duruldu; başka alan adı, kullanıcı aracısı veya CAPTCHA aşma denenmedi. Tarihsel dosya güncel bid/ask yerine kullanılamaz. |

Bu kaynaklardan **izni ve güncelliği doğrulanmış, anahtarsız canlı ABD
bid/ask/NBBO akışı kurulmuş değildir**. Finviz CSV desteği dahi NBBO doğrulaması
sayılmaz. Bu adaptörün işlem yetkisi yoktur; eksik kotasyonla `AL` üretmez.

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

S-1/S-3/F-1/F-3, 424B, EFFECT ve ilgili 8-K/6-K kayıtları inceleme gerektiren
finansman işaretleridir. Form adı tek başına ATM/dilution tespiti değildir;
prospektüs, ekler, aktif raf kaydı ve satış anlaşmaları ayrıca okunmalıdır.
Eksik geçmiş veya tamamlanmamış inceleme "risk yok" olarak işaretlenemez.
Olumlu haber için yayımlanma zamanı, şirket/sembol ilişkisi ve asıl haber
kaynağı gerekir. Başlığın olumlu kelime içermesi yeterli değildir. İhraççı RSS
ve haber siteleri ancak ilgili kaynağın erişim/kullanım izni doğrulandığında
eklenebilir; genel bir izin varsayımı yapılmaz.

8 Ekim 2026 Cloud kontrolünde finans alan adları mevcut çalışma ortamının
izinli ağ hedefleri arasında değildi. Kaynak belgelerini araştırma aracıyla
okumak, çalışan adaptörün bu ağlara eriştiği anlamına gelmez; araştırma aracı
canlı veri aktarımı için ağ politikasını aşan bir yol olarak kullanılmadı.
Doğrulanmış güncel piyasa gözlemi ve yetkili dışa aktarım mevcut olmadığından
bu araştırmadan güncel aday/fiyat üretilmedi: **`DATA_UNAVAILABLE / PAS`**.
