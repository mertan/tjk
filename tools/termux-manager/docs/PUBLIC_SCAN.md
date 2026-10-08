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

Nasdaq Trader NASDAQ/NYSE dizinlerinden en fazla **20** uygun sembol seçilir.
Varsayılan seçim alfabetik ilk 20 kayıttır: sembol dosyasında fiyat yoktur,
bu seçim tüm piyasadaki en güçlü veya 1–5 USD aralığındaki en iyi 20 hisse
iddiası değildir. Kendi en fazla 20 sembolünü `--symbols AAA,BBB` biçiminde
verebilirsin; semboller o çalıştırmada indirilen dizinde bulunmalıdır. Bunlar
sözdizimi örnekleridir, hisse önerisi değildir. Yinelenen/geçersiz/21+ sembol
ve yerel gözlem/bağlam dosyasıyla bu modu karıştırmak reddedilir.

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

```sh
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
exec python3 -I -B "$PUBLIC_SCAN_DIR/tools/termux-manager/public_scan.py" --provider fintable
)
```

Bu komut API kurmaz, port açmaz ve gizli anahtar istemez. Yeni kaynak dizini
bilerek korunur; mevcut dizinlerde dosya yazımı yapılmaz. Python `-I` modu
`PYTHONPATH` ve kullanıcı site paketlerini dışarıda bırakır, `-B` bytecode
yazımını kapatır. HTTP istemcisi varsa mevcut proxy ve CA güven ayarlarını
kullanır. SEC isteği için yalnız `SEC_USER_AGENT` (gerçek iletişim bilgisi
içeren SEC tanımlayıcısı) okunur; Alpaca/BtcTurk/Termux anahtarları okunmaz.
SEC tanımlayıcısı çıktı içine eklenmez. Eksikse SEC kontrolü tamamlanmış sayılmaz.

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
listesinde `www.nasdaqtrader.com`, `fintable.io`, `www.sec.gov`, `data.sec.gov`+yoktu; proxy/TLS/alan adı kısıtları değiştirilmedi. Canlı sağlayıcı yanıtı,
güncel fiyat/hacim/haber veya telefonda uçtan uca çalışma doğrulanmadı.

Bağımlılıklar ayrıca GitHub'dan kontrol edildi: [PR #1](https://github.com/mertan/tjk/pull/1)
açık/taslak ve main tabanlı; [PR #2](https://github.com/mertan/tjk/pull/2) açık ve
#1 tabanlı; [PR #3](https://github.com/mertan/tjk/pull/3) açık ve #2 tabanlı.
Üçünün son PR Actions çalışması başarılı. Bu ek doğrudan PR #3'ün
`688a0bc34182164120d5adde95dc82d2da5acabb` commit'i üzerine hazırlanmıştır;
#1–#3 içeriğini içerir. Birleştirme sırası #1 → #2 → #3 → bu ek olmalıdır;
taban değişikliklerinde kontroller yeniden çalıştırılmalıdır. Hiçbir PR
birleştirilmedi veya mevcut PR dalı değiştirilmedi.
