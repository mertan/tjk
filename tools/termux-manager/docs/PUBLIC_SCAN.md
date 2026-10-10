# Alpaca kullanmayan, salt okunur tarama

Bu giriş noktası mevcut yönetim API'sinden bağımsız, tek seferlik bir Python
sürecidir. `auth.key`, `project.json`, yönetim API'si veya broker hesabını
kullanmaz. 8080/8081 servislerini durdurmaz veya yeniden başlatmaz. Tarama
sonucunu yalnız standart çıktıya JSON olarak yazar; runtime kaydı veya emir
oluşturmaz. `execution_enabled=false` her sonuçta korunur.

## Anahtarsız Fintable araştırma modu

```sh
python3 -I -B tools/termux-manager/public_scan.py --provider fintable
```

**Alfabetik ilk 20 seçimi kaldırıldı.** Yukarıdaki komut seçim girdisi olmadan
`DATA_UNAVAILABLE / PAS` ve `RANKING_INPUT_REQUIRED` raporlar. Bu sonuç,
güncel hacim/hareket verisi olmadan piyasanın tarandığını iddia etmez.

Kullanım hakkı ve kaynağı incelenmiş yerel sıralama girdisi varsa:

```sh
python3 -I -B tools/termux-manager/public_scan.py --provider fintable \
  --ranking-input /OZEL/GIRDI/ranking.json
```

Dosyadaki NASDAQ/NYSE kayıtları o çalıştırmada indirilen Nasdaq Trader
dizinleriyle doğrulanır. Fiyatı 1–5 USD olan ve pozitif günlük hareket gösteren
uygun gözlemler, **hacim ve fiyat değişiminin eşit ağırlıklı rekabet yüzdelik
sıralamasıyla** sıralanır. Eşit değerler aynı sıra puanını alır. En fazla **20**
sembol Fintable fiyat/geçmiş/SEC incelemesine aktarılır. Sıralama yalnız girdi
kapsamı içindir; tüm piyasanın en iyi 20 hissesi, güçlü RVOL veya bir yatırım
önerisi olduğu anlamına gelmez. Kaynağı, zaman damgası, gecikmesi ve kapsamı
raporda korunur. Geçersiz veya eski girdi alfabetik seçime geri dönmez.

İzinli ve ücretsiz bir tüm piyasa hacim/yükselenler akışı bu çalışma için
doğrulanamadı. Fintable'ın belgelenmiş fiyat ucu önceden bilinen sembolleri
ister; 4.817 sembolü 20'lik gruplarla sorgulayarak sınır dolanılmaz. Finviz
Elite'in yetkili resmî dışa aktarımı veya uygun lisanslı bir dışa aktarım
girdi olabilir; ücretsiz Finviz/TradingView sayfası kazınmaz. Geçerli kullanım
hakkı, orijinal gözlem zamanı ve alanlar yoksa veri uydurulmaz, PAS üretilir.
[Kaynak incelemesi](FINTABLE_RANKING_SOURCES.md).

Kendi en fazla 20 sembolünü ayrıca `--symbols AAA,BBB` biçiminde verebilirsin;
bu yalnız açık **manuel seçim** olur, hacim/hareket sıralaması sayılmaz.
Semboller o çalıştırmada indirilen dizinde bulunmalıdır. Bunlar sözdizimi
örnekleridir, hisse önerisi değildir. Yinelenen/geçersiz/21+ sembol reddedilir.
`--ranking-input` ile `--symbols` birlikte kullanılamaz; Fintable modu ayrıca
`--observations` veya `--context` ile birleştirilemez.

Belgelenmiş Fintable public API'si bir toplu fiyat isteği yapar. Gözlenen
fiyatı 1–5 USD olan kayıtlar için beş dakikalık geçmiş ve, gerçek SEC iletişim
tanımı varsa, SEC finansman metadata'sı incelenir. Eski bir fiyat aralığı
eşleşmesi güncel aday uygunluğu sayılmaz. `securities` her sorgulanan sembolün
fiyatını, hacmini, kaynak zamanını, indirme zamanını, veri yaşını, haber/SEC
durumunu ve PAS nedenlerini içerir; eksik değerler `null` kalır.

Fintable hacmi yalnız **IEX** işlemleridir. Son iki ardışık, alındığı anda
tamamlanmış normal seans mumu varsa beş dakikalık fiyat değişimi ve önceki
beş dakikaya göre hacim oranı hesaplanır. Bu oran aynı saat diliminin geçmiş
seans ortalamasına göre RVOL değildir; `rvol_5m=null` kalır. Toplama bitince
fiyat ve mum tazeliği yeniden denetlenir. Fiyat için 30 saniye eşiği veya
normal seans doğrulaması, önbellek gecikmesini sıfır yapmaz.

Bu kaynak bir saate kadar önbellek kullanabilir, kapanışa dönebilir ve işlem
için tasarlanmamıştır. Bid/ask ve haber sağlamaz; spread ve olumlu katalizör
doğrulanamaz. SEC metadata bayrakları gösterilir; bayrak yokluğu ATM/dilution
olmadığını ispatlamaz. Bu nedenle **bu otomatik mod tek başına İZLE üretmez**;
eksikler `DATA_UNAVAILABLE / PAS` olarak kalır. Yerel lisanslı gözlem modunun
önceden tanımlı İZLE kapıları ve PR #2 risk motoru değiştirilmemiştir.

Tüm kaynaklar tek **24 GET / 60 saniye** bütçesini paylaşır; bu sınır yirmi
sembolün tamamında derin SEC/geçmiş incelemesi garantisi vermez. Geçmiş
isteklerinden sonra kalan bütçe yetmezse SEC sonucu bilinmiyor/PAS olur.
Fintable istekleri aynı süreçte en fazla saniyede bir, SEC istekleri mevcut
hız sınırıyla yapılır. 403/429 ve yönlendirmeler aşılmaz; otomatik tekrar yok.
Çoklu süreçler/IP'nin toplam kotası operatörün sorumluluğundadır; bu komut
periyodik servis başlatmaz. Kişisel, ticari olmayan kullanım ve Fintable
atıf bağlantısı raporda korunur. [Kaynak koşulları](PUBLIC_DATA_SOURCES.md).

## Veri erişiminin sınırı

Halka açık web sayfasını görebilmek, otomatik veri toplama veya yeniden kullanım
izni değildir. TradingView ve Finviz web ekranlarını kazıyan bir tarayıcı,
CAPTCHA çözme, oturum taklidi, erişim engeli aşma veya belgelenmemiş uç nokta
eklenmemiştir. İzinli bir fiyat kaynağı ve doğrulanabilir güncel veriler yoksa
sonuç **`DATA_UNAVAILABLE` / `PAS`** olur. Bu, canlı taramanın başarılı olduğu
anlamına gelmez; çalıştırılabilen yazılımın veri eksikliğinde durduğunu gösterir.

NASDAQ/NYSE sembol keşfi, fiyat/haber/kotasyon kapsamından ayrı raporlanır.
Sembol listesi fiyat veya alış/satış kotasyonu sağlamaz. Tüm borsalar için
aday bulunamadığı ile tüm borsaların güncel verilerle tarandığı aynı iddia
değildir. Girdi yalnız bir alt kümeyi kapsıyorsa rapor bu sınırı belirtir.

İzinli dışa aktarımdan gelen gözlemler, **operatör beyanına dayalı** girdilerdir;
adaptör kaynağın kriptografik olarak doğrulandığını iddia etmez. CSV/PDF/web
ekranının fiyatını elle güncelmiş gibi zaman damgalamak kabul edilmez. İzin,
alan kapsamı, orijinal zaman damgası veya gerçek gecikme bilinmiyorsa PAS.
Finviz dışa aktarmasının bütün gereken alanları sağladığı varsayılmaz.

Bu adaptör **AL üretmez**. Eksiksiz, güncel ve kabul edilmiş verilerle risk ve
haber/SEC kontrollerini geçen bir kayıt en fazla **İZLE** olabilir. Eski veya
doğrulanamayan veri PAS üretir. Güncel ve doğrulanmış bid/ask olmaması spread'in
geçtiği anlamına gelmez; gerçekleşebilir işlem fiyatı, miktarı veya emir
hazırlığı iddiasında bulunulmaz.

## Tek güvenli Termux komutu

Python 3 ve Git'in önceden kurulu olması gerekir; aşağıdaki blok paket yüklemez.
`DOGRULANMIS_TAM_COMMIT_SHA` yerine PR teslimindeki **40 karakterlik tam commit
SHA** yazılır. Komut boş ve özel yeni bir dizinde yalnız o commit'i getirir,
SHA'yı doğrular ve tek tarama çalıştırır. Mevcut klonları veya kurulumu
güncellemez. Varsayılan dal çalıştırılmaz; shell'e indirilen betik aktarılmaz.

Termux'ta Bash ile çalıştırılır. İleride gerçek, izinli sıralama dosyan varsa
`PUBLIC_RANKING_INPUT` değişkenine özel yerel dosya yolunu verebilirsin;
aşağıdaki blok bu yolu tek argüman olarak aktarır. Değişken yoksa varsayılan
çalıştırma **PAS / RANKING_INPUT_REQUIRED** üretir. Dosya oluşturmaz, örnek
fiyat veya zaman damgası üretmez ve manuel hisse seçimini kendiliğinden yapmaz.

```bash
(
set -eu
umask 077
PUBLIC_SCAN_SHA=DOGRULANMIS_TAM_COMMIT_SHA
case "$PUBLIC_SCAN_SHA" in *[!0-9a-f]*|'') exit 2;; esac
[ "${#PUBLIC_SCAN_SHA}" -eq 40 ]
command -v git >/dev/null
command -v python3 >/dev/null
PUBLIC_SCAN_DIR=$(mktemp -d "$HOME/tjk-public-scan.XXXXXX")
git -c core.hooksPath=/dev/null init --quiet "$PUBLIC_SCAN_DIR"
GIT_ASKPASS= SSH_ASKPASS= GIT_TERMINAL_PROMPT=0 \
  git -C "$PUBLIC_SCAN_DIR" -c core.hooksPath=/dev/null -c credential.helper= \
  fetch --quiet --depth=1 https://github.com/mertan/tjk.git "$PUBLIC_SCAN_SHA"
[ "$(git -C "$PUBLIC_SCAN_DIR" rev-parse FETCH_HEAD)" = "$PUBLIC_SCAN_SHA" ]
git -C "$PUBLIC_SCAN_DIR" -c core.hooksPath=/dev/null \
  checkout --quiet --detach "$PUBLIC_SCAN_SHA"
PUBLIC_SCAN_ARGS=(--provider fintable)
if [ -n "${PUBLIC_RANKING_INPUT:-}" ]; then
  PUBLIC_SCAN_ARGS+=(--ranking-input "$PUBLIC_RANKING_INPUT")
fi
exec python3 -I -B "$PUBLIC_SCAN_DIR/tools/termux-manager/public_scan.py" "${PUBLIC_SCAN_ARGS[@]}"
)
```

Bu komut API kurmaz, port açmaz ve gizli anahtar istemez. Yeni kaynak dizini
bilerek korunur; mevcut dizinlerde dosya yazımı yapılmaz. Python `-I` modu
`PYTHONPATH` ve kullanıcı site paketlerini dışarıda bırakır, `-B` bytecode
yazımını kapatır. HTTP istemcisi varsa mevcut proxy ve CA güven ayarlarını
kullanır. SEC isteği için yalnız `SEC_USER_AGENT` (gerçek iletişim bilgisi
içeren SEC tanımlayıcısı) okunur; Alpaca/BtcTurk/Termux anahtarları okunmaz.
SEC tanımlayıcısı çıktı içine eklenmez. Eksikse SEC kontrolü tamamlanmış sayılmaz.

## Yerel sıralama girdisi

`--ranking-input` yalnız özel bir yerel JSON dosyası okur; URL indirmez.
Dosya, orijinal dışa aktarımdaki alanların aşağıdaki şemaya dönüştürülmüş
halidir. Hazır bir CSV ayrıştırıcısı veya sağlayıcının alanlarını tahmin eden
bir dönüştürücü yoktur. Kaynakta bulunmayan gözlem zamanı, hacim kapsamı veya
gerçek gecikme doldurulamaz. Dosya düzenleme zamanı `as_of` yerine kullanılamaz.
Geçerli dışa aktarım yoksa dosya üretmek yerine varsayılan PAS akışı kullanılır.

Üst düzeyde tam olarak `schema_version`, `source`, `records` bulunur:

| Alan | Gereklilik |
| --- | --- |
| `schema_version` | Tam sayı `1`. |
| `source.provider` | `finviz_elite_export` veya `licensed_public_export`; gerçek kullanım hakkı gerekir. |
| `source.url`, `source.terms_url` | Kaynak ve izin belgesinin HTTPS adresi; kullanıcı bilgisi, query veya fragment içermez. Program bu adresleri indirmez. Finviz için resmî Finviz alan adı ve Elite dışa aktarım hakkı gerekir. |
| `source.permission` | `personal_automated_analysis`. |
| `source.permission_reviewed_at` | Saat dilimli, en fazla 30 günlük izin inceleme zamanı. |
| `source.verification` | `operator_reviewed_original_export`; operatör beyanıdır, bağımsız kaynak doğrulaması değildir. |
| `source.retrieved_at` | Gerçek alma zamanı; saat dilimli, en fazla 300 saniye eski. |
| `source.delay_seconds` | Bilinen kaynak gecikmesi; tam sayı 0–900. Bilinmeyen gecikme `0` yapılamaz. |
| `source.volume_scope` | Tüm satırlar için `consolidated_us` veya `iex`; farklı kapsamlar aynı sıralamada karıştırılamaz. |
| `source.volume_basis` | `session_cumulative_shares`; seansın birikimli hisse adedi. |
| `records` | 1–10.000 satır; bu, Fintable'a gönderilecek sembol sınırını artırmaz. |
| Her satır | Tam olarak `symbol`, `exchange`, `price_usd`, `previous_close_usd`, `volume_shares`, `as_of`, `session_date`. |

`exchange` NASDAQ veya NYSE olmalı ve Nasdaq Trader kaydıyla eşleşmelidir.
Fiyat ve önceki kapanış pozitif sayılar, hacim pozitif tam sayı olmalıdır.
`session_date` New York'taki günün `YYYY-MM-DD` biçimidir; saat dilimli `as_of`
aynı güne ait, en fazla 900 saniye eski ve alma zamanından sonra olmamalıdır.
Gelecek zamanlar reddedilir. Uygun satırların gözlem zamanları arasında en fazla
60 saniye fark olabilir; uyumsuz anlık görüntüler bir sıralamada birleştirilmez.
Fiyat aralığı dışındaki, hareketi pozitif olmayan veya eski kayıtlar elenir.
Yinelenen semboller, çelişkili/eksik şema ve geçersiz kaynak tüm girdiyi reddeder.

Günlük hareket `(price_usd / previous_close_usd - 1) × 100` olarak hesaplanır.
Her ölçütün yüzdelik puanı, kendisinden kesin küçük değere sahip uygun satır
sayısının `max(1, uygun_satır_sayısı - 1)` ile bölünüp 100 ile çarpımıdır.
`rank_score`, hacim ve hareket yüzdeliklerinin ortalamasıdır. Eşit puanda
sırasıyla yüksek günlük hareket, yüksek hacim ve sembol sırası kullanılır.
Alfabetik sıra yalnız tam eşitliği çözebilir; aday kaynağı olamaz.

En fazla **15 dakikalık sıralama girdisi yalnız araştırma önceliği** içindir.
`declared_delay_seconds`, `data_age_seconds`, `as_of`, `volume_scope` ve
`source_verification` raporda görünür; gecikmeli kaynak açıkça belirtilir.
Kaynağın gecikme beyanının `0` olması verinin alındığı anda canlı olduğunu
kanıtlamaz; gözlem yaşı ayrıca okunmalıdır. Sıralama kayıtlarının kararı
**PAS** kalır, işlem veya İZLE uygunluğu sağladıkları iddia edilmez.

Seçilen en fazla 20 hisse daha sonra mevcut Fintable incelemesine girer.
Bu aşamadaki **30 saniyelik fiyat tazeliği kapısı değişmedi**; 15 dakikalık
sıralama penceresi fiyat, haber, SEC veya risk kontrollerini gevşetmez.
IEX hacmi konsolide hacim veya NBBO değildir; bid/ask ve doğrulanmış spread
yokluğu nedeniyle otomatik Fintable akışı AL üretmez ve PAS kalır.

## İzinli gözlem ve risk girdisi

İzinli kaynaktan hazırlanmış yeni JSON dosyalarıyla aynı giriş noktası:

```sh
python3 -I -B /YENI/KAYNAK/tools/termux-manager/public_scan.py \
  --observations /YENI/GIRDI/observations.json \
  --context /YENI/GIRDI/context.json
```

Bu yollar örnektir; mevcut HTTP sunucusunun belge köküne hesap bilgisi veya
girdi dosyası koymayın. Argümanlar URL değildir; program kullanıcıdan verilen
bir URL'yi indirip çalıştırmaz. Dosyalar yalnız okunur; sembolik bağlantı,
sembolik bağlantılı üst dizin, normal dosya dışındaki türler, yinelenen JSON
anahtarları, NaN/Infinity, aşırı boyut ve aşırı derinlik reddedilir.
Her JSON dosyası en fazla 32 MiB, iç içelik 20 seviye, değer ağacı
1.000.000 düğüm olabilir. Adaptör en fazla 10.000 gözlem kabul eder; bu bir
tam piyasa kapsamı garantisi değildir. Pahalı SEC incelemesi filtreleri geçen,
RVOL/momentum/günlük getiri/hacim sırasındaki en fazla 20 hisseyle sınırlıdır;
kalanlar incelenmemiş/PAS olarak belirtilir.

`observations` en üst düzeyde şu alanları taşır:

| Alan | Anlamı |
| --- | --- |
| `schema_version` | `1`. |
| `provenance.provider` | `finviz_elite_export` veya `licensed_public_export`; kaynak izni ayrıca gereklidir. |
| `provenance.source_url`, `terms_url` | Herkese açık HTTPS kaynak/izin belgesi; kullanıcı bilgisi, query veya fragment içermez. Bunlar rastgele indirme hedefi değildir. |
| `provenance.permission` | `personal_automated_analysis`; kullanıcının o veri için gerçek kullanım hakkı bulunmalıdır. |
| `provenance.permission_reviewed_at` | Saat dilimli, en fazla 30 günlük izin inceleme zamanı. |
| `provenance.verification` | `operator_reviewed_original_export`; bağımsız kaynak doğrulaması değildir. |
| `provenance.as_of`, `retrieved_at` | Orijinal veri ve alma zamanı; sırasıyla en fazla 30 ve 60 saniye. |
| `provenance.delay_seconds` | Gerçek kaynak gecikmesi; kabul edilen değer `0`. Gecikmiş veriyi `0` diye etiketlemek geçerli değildir. |
| `market` | PR #2 şemasındaki açık seans bilgisi, seans günü, kaynak ve güncel zaman damgası. |
| `securities` | NASDAQ/NYSE adi hisse gözlemleri; eksik alanlar PAS üretir. |

Her hisse kaydı PR #2'nin `symbol`, `exchange`, `kind`, `broker_available`,
`broker_verified_at`, `trade`, `metrics` ve `news` alanlarını kullanır. `quote`
olmayabilir; yokluğu kotasyon/spread doğrulamasını sağlamaz. Haber ayrıca
`sentiment: "positive"`, `original_source_reviewed: true`, maddi katalizör,
kayıtla eşleşen `news.symbol`, kaynak URL'si,
yayımlanma ve manuel inceleme zamanlarını gerektirir. `filings_review` SEC
belgeleri hakkındaki güncel manuel incelemedir: `status: "clear"`,
`latest_filing_date`, `checked_at`, `coverage_days` (en az 365), `reviewed_by`
ve SEC alan adındaki `source_url` gerekir. Kullanıcı tarafından verilen
`filings` sonucu SEC doğrulamasının yerine geçmez. Gerçek SEC metadata'sı
adaptörün izinli kaynağından alınır; finansman/ATM/dilution bayrakları manuel
`clear` beyanıyla kaldırılamaz.

`context` PR #2'nin `fx`, `account`, `costs` yapılarını içerir:

- `fx`: en fazla 5 dakikalık uygulanabilir USD/TRY alış/satış kuru, kaynak ve zaman.
- `account`: güncel seansın nakit, ücretler sonrası gerçekleşmiş/gerçekleşmemiş
  P/L, açık risk, rezervasyon, pozisyon ve işlem kısıtları bilgisi.
- `costs`: ilgili miktarda geçerli giriş/çıkış ücretleri, kayma payı, kaynak,
  güncel doğrulama ve düşük fiyatlı çıkış kapsamı.

Eski [risk incelemesi](SCANNER_REVIEW.md) bu alanların hesaplamalarını ve açık
kalan insan incelemelerini açıklar. Bu belgede gerçek veriye benzeyen fiyat
örneği verilmez; test fixture'ları yalnız test içindir. Örnek boş bağlam dosyası
canlı hesap/veri girdisi değildir.

## Korunan risk ve veri sınırları

50.000 TL toplam sermaye, ücretler dahil 12.500 TL sembol pozisyon sınırı,
1–5 USD fiyat, %3 planlanan stop ve 2.500 TL günlük zarar/açık risk sınırı
korunur. Spread hem 0,05 USD hem bid'e göre %2,5 sınırını sağlamalıdır.
Hacim, VWAP, aynı zaman aralığına göre RVOL, beş dakikalık momentum ve günlük
getiri kontrolleri PR #2 ile aynıdır. Stop, gerçekleşen zararın en fazla %3
olacağını garanti etmez. Günlük zarar denetimi kalıcı seans kilidi değildir.

Başarılı şekilde üretilmiş PAS/İZLE JSON raporu çıkış kodu `0` verir. Geçersiz
CLI/girdi veya beklenmeyen hata çıkış kodu `2` ve sabit, güvenli hata kodu
verir. Dosya yolu, dosya içeriği, exception metni veya gizli ortam değişkeni
hata çıktısına yazılmaz. Kodun çalışması, veri kapsamı veya kullanım izninin
doğrulandığı anlamına gelmez; raporun kaynak/tazelik/eksik veri alanları ayrıca
değerlendirilmelidir.

## Test

```sh
cd tools/termux-manager
python3 -B -m unittest discover -s tests -v
```

Erişim/lisans değerlendirmesi ve resmî kaynak bağlantıları
[PUBLIC_DATA_SOURCES.md](PUBLIC_DATA_SOURCES.md) belgesindedir.

Testlerde sentetik kaynaklar ve kurgu fiyatlar kullanılır; telefon, broker veya
gizli anahtar gerekmez. PR #2'nin risk motoru ve yönetim servisi değiştirilmez.

8 Ekim 2026 doğrulaması: Python 3.12.14 ve 3.13.13 altında **377 testin
tamamı geçti** (PR #2'nin 269 testi ve 108 yeni test). Çalışma ortamındaki
gerçek tek-seferlik CLI denemesi `DATA_UNAVAILABLE / PAS`,
`execution_enabled=false`, boş aday/izleme listesi döndürdü. Nasdaq Trader
adresleri için güvenli hata `network_unavailable` idi; bu adresler mevcut
Cloud ağ izinlerinde de yoktu. Güncel piyasa verisi alınmadı, SEC incelemesi
yapılmadı ve bütün NASDAQ/NYSE piyasasının tarandığı iddia edilmedi.
Termux'ta dağıtım veya telefon testi yapılmadı.

## Fintable ekinin doğrulaması — 8 Ekim 2026

Python **3.12.14** ve **3.13.5** altında **424 test geçti**: mevcut 377 teste
16 sağlayıcı/transport, 29 rapor/entegrasyon ve 2 CLI testi eklendi. Sentetik
veriler canlı aday raporu değildir. Risk motoru, broker bağlantısı, Termux
servisleri veya gizli anahtar dosyaları değiştirilmedi.

14:20:58 UTC'deki gerçek `--provider fintable` denemesi
[`PUBLIC_SCAN_RUN_2026-10-08.json`](PUBLIC_SCAN_RUN_2026-10-08.json) dosyasında:
**DATA_UNAVAILABLE / PAS; 0 doğrulanmış sembol, 0 İZLE**. İki Nasdaq Trader
dosyasına erişim `network_unavailable`; evren doğrulanamadığı için bu tarama
fiyat/SEC aşamasına geçmedi. Fintable public fiyat endpoint'ine ayrıca yapılan
normal HTTPS denemesi de `network_unavailable` verdi. Çalışma ortamının ağ
listesinde `www.nasdaqtrader.com`, `fintable.io`, `www.sec.gov`, `data.sec.gov`
yoktu; proxy/TLS/alan adı kısıtları değiştirilmedi. Canlı sağlayıcı yanıtı,
güncel fiyat/hacim/haber veya telefonda uçtan uca çalışma doğrulanmadı.

Bağımlılıklar ayrıca GitHub'dan kontrol edildi: [PR #1](https://github.com/mertan/tjk/pull/1)
açık/taslak ve main tabanlı; [PR #2](https://github.com/mertan/tjk/pull/2) açık ve
#1 tabanlı; [PR #3](https://github.com/mertan/tjk/pull/3) açık ve #2 tabanlı.
Üçünün son PR Actions çalışması başarılı. Bu ek doğrudan PR #3'ün
`688a0bc34182164120d5adde95dc82d2da5acabb` commit'i üzerine hazırlanmıştır;
#1–#3 içeriğini içerir. Birleştirme sırası #1 → #2 → #3 → bu ek olmalıdır;
taban değişikliklerinde kontroller yeniden çalıştırılmalıdır. Hiçbir PR
birleştirilmedi veya mevcut PR dalı değiştirilmedi.

## Sıralama ekinin doğrulaması — 8 Ekim 2026

Bu ek, PR #4'ün `c09de0ca321a008efd311a04d393f92331237686` sürümü üzerine
hazırlandı. Python **3.12.14** ve **3.13.13** altında **488 test geçti**
(424 mevcut + 64 yeni). Testlerde alfabetik ilk 20'nin dışındaki güçlü kurgu
kayıt seçiliyor; fiyat/geçmiş/SEC sorgularının tamamı aynı seçilmiş en fazla
20 sembolle sınırlı kalıyor. Sıralama verisi toplama sırasında eskiyince
öncelik listesi siliniyor; yerine 21. sembol sorgulanmıyor.

14:51:43 UTC'de yeni varsayılan komut Cloud'da çalıştırıldı:
`DATA_UNAVAILABLE / PAS`, `RANKING_INPUT_REQUIRED`, `execution_enabled=false`.
İzinli sıralama girdisi olmadığı için hiçbir kaynak isteği yapılmadı;
evren veya fiyatların tarandığı iddia edilmedi. Bu çalışmanın canlı hisse
önerisi yoktur. Kullanıcının telefonda bildirdiği önceki 4.817 sembollük
evren/20 inceleme sonucu bu Cloud testinden ayrıdır. Mevcut Termux servisleri
ve anahtarlar değiştirilmedi. PR #2 risk motoru ve ağ izin listesi korunur.

Önceki PR'lar birleştirilmeden bu değişiklik PR #4 dalını hedefler; sıra
#1 → #2 → #3 → #4 → sıralama ekidir. Bu ek hiçbir PR'ı birleştirmez.
