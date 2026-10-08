# Alpaca kullanmayan, salt okunur tarama

Bu giriş noktası mevcut yönetim API'sinden bağımsız, tek seferlik bir Python
sürecidir. `auth.key`, `project.json`, yönetim API'si veya broker hesabını
kullanmaz. 8080/8081 servislerini durdurmaz veya yeniden başlatmaz. Tarama
sonucunu yalnız standart çıktıya JSON olarak yazar; runtime kaydı veya emir
oluşturmaz. `execution_enabled=false` her sonuçta korunur.

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
exec python3 -I -B "$PUBLIC_SCAN_DIR/tools/termux-manager/public_scan.py"
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
