# Termux yönetim API'si — sınırlı analiz servisi

Mevcut botlara veya 8080 dosya sunucusuna müdahale etmeden, **yeni bir özel proje
dizininde** çalışır. İstek ve yanıt imzalarını doğrular; JSON girdi yükler, pakete
gömülü ABD hisse analizini çalıştırır ve sonucu okutur. Gerçek finansal emir,
shell, SSH, keyfî komut, paket kurma veya yüklenen kodu çalıştırma işlevi yoktur.

Termux süreçleri aynı Android UID'sini paylaşır. Bu uygulama dosya işlemlerini
kendi dizinleriyle sınırlar; diğer yerel Termux süreçlerine karşı işletim sistemi
sandbox'ı sağladığı iddia edilmez. Ayrıntılar [SECURITY.md](docs/SECURITY.md).

## Dosyalar ve kurulum sınırı

- Kaynak kodu bu yeni `tools/termux-manager` klasöründe bulunur.
- Kurucu yalnız yeni hedefe yazar; mevcut hedefe veya son bileşeni symlink olan
  hedefe kurulum yapmaz. Profil, cron, başlangıç servisi veya mevcut bot değişmez.
- Proje/runtime dizinleri 0700, anahtar ve veriler 0600 izinlidir.
- Mevcut Python HTTP sunucusu aynı kullanıcıyla çalıştığı için 0600 izni, web
  kökü altındaki anahtarı o sunucudan korumaz. `--public-root` gerçek mevcut web
  kökünü belirtir; `--discover-http-port 8080` standart sunucunun kökünü yerel
  süreç bilgisinden bulmayı dener. Bu yöntemlerden en az biri zorunludur; hedef
  web kökü altında veya mevcut bağlantıları üzerinden erişilebilir olamaz.
  Birden fazla dosya sunucusu varsa her kökü ayrı `--public-root` ile bildirin.
- Gizli anahtarı, runtime verisini veya hesap çıktılarını GitHub'a göndermeyin.

Python **3.11+**, `America/New_York` saat dilimi verisi ve Linux/Termux dosya
özellikleri gerekir. Python paket bağımlılığı yoktur. Kurulum sistem paketlerini
güncellemez. Mevcut Python yoksa ya da ön koşul eksikse kurucu durur.

Kurulum mevcut fakat 8081 başlamıyorsa [başlatma düzeltmesi ve güvenli tanılama](docs/STARTUP.md)
belgesindeki yöntemi kullanın. Yeni kaynak kodu mevcut anahtar/ayar dosyalarının
üzerine yazmadan çalıştırılabilir; `serve --check` salt okunur ön kontrol yapar.

## Termux'a kurulum

Önce bu depoyu **yeni bir kaynak dizinine** klonlayın; incelenmiş tam commit SHA'sına
geçin. Dalın en son durumuna otomatik güncelleme yapılmaz. Dağıtım mesajındaki
commit SHA'sı kullanılmalıdır. Aşağıdaki değişkenler yalnız bu kurulum içindir:

```sh
TM_SOURCE="$HOME/tjk-manager-source-20261008"
TM_DEST="$HOME/../tjk-termux-manager-20261008"

git clone --single-branch --branch codex/termux-management-api-20261008 \
  https://github.com/mertan/tjk.git "$TM_SOURCE"
git -C "$TM_SOURCE" checkout --detach DOGRULANMIS_TAM_COMMIT_SHA
python3 -I "$TM_SOURCE/tools/termux-manager/launch.py" install \
  --dest "$TM_DEST" --discover-http-port 8080
```

Commit yer tutucusu gerçek değerle değiştirilmeden çalıştırmayın. Kurucu aynı
Termux UID'sindeki `python -m http.server 8080` sürecinin komutunu ve çalışma
dizinini yalnızca okur; sürece sinyal göndermez ve ağ isteği yapmaz. Süreç bilgisi
erişilemiyorsa veya sunucu özel bir script ise tahmin yürütmez, kurulum durur.
Bu durumda `--discover-http-port 8080` yerine `--public-root GERCEK_KOK` kullanın.
Web kökü, sunucuyu
başlatırken `--directory` ile verilen dizin; bu seçenek yoksa o işlemin çalışma
dizinidir. `/` gibi çok geniş bir kök sunuluyorsa onun dışında korunan bir hedef
seçilemez ve kurulum reddedilir. Kurucu, doğru web köklerinin kullanıcı tarafından
bildirildiğine güvenir; başka servislerin ayarlarını değiştirmez. Standart 8080
süreci dışındaki dosya sunucuları otomatik keşfedilmez.
Hedefin `$HOME` dışında olması tek başına güvenlik kanıtı değildir.

Kurulum ön kontrolü bildirilen köklerdeki mevcut sembolik dizin bağlantılarını
izler; dolaylı veya henüz oluşmamış hedef/anahtar bağlantıları da reddedilir.
Tarama en fazla 20.000 öğe, 64 seviye ve 10 saniyeyle sınırlıdır. Erişim veya
limit sorunu varsa anahtar oluşturulmadan durulur. Sonradan eklenen dosya sunucusu
veya symlink, yerel sahibi tarafından ayrıca değerlendirilmelidir.

Kurucu anahtarı telefonda üretir, değerini yazdırmaz. Çıktıdaki `project_id`
gizli değildir. `auth.key` ve `project.json` yalnız yeni hedefte kalır.

İlk Termux oturumunda loopback üzerinde başlatın:

```sh
cd "$TM_DEST"
sh ./start.sh
```

İkinci Termux oturumunda kontrol edin:

```sh
TM_DEST="$HOME/../tjk-termux-manager-20261008"
cd "$TM_DEST"
python3 -I launch.py client --url http://127.0.0.1:8081 \
  --key-file auth.key --config project.json --bypass-proxy health
```

Başarılı cevapta `ok:true` ve `execution_enabled:false` görülür. Sunucuyu başlatan
oturumda Ctrl+C ile durdurabilirsiniz; bu yalnız yeni yönetim sürecini durdurur.

## Mevcut Tailscale bağlantısıyla erişim

Loopback kontrolü geçtikten sonra yalnız **bu yeni sunucuyu** Ctrl+C ile durdurun
ve aynı yeni dizinden aşağıdaki açık uzak-dinleme seçeneğiyle başlatın:

```sh
sh ./start.sh --bind 0.0.0.0 --tailnet-bind --port 8081
```

Android Tailscale uygulamasında 100.x adresi normal bind edilebilir yerel arayüz
olmayabilir. Bu nedenle açıkça seçilen wildcard bind desteklenir, fakat sunucu
soketin gerçek kaynak adresinden yalnız IPv4 loopback ve `100.64.0.0/10` istemcilerini
kabul eder. `X-Forwarded-For` gibi başlıklar yetki sağlamaz. Android aktarması
istemciyi loopback olarak gösterebilir; **HMAC bütün durumlarda zorunludur**.
IPv6 dinleme bu sürümde yoktur. İnternete port yönlendirmesi açılmaz.

Uzak URL, mevcut telefon Tailscale adının **8081** portudur:
`http://TELEFON.tailXXXX.ts.net:8081`. Mevcut 8080 dosya sunucusu aynı kalır.
Tailnet ACL/grants kuralları çağıran cihazdan bu porta erişime izin vermelidir;
bu paket ağ/Tailscale kurallarını değiştirmez. Gerçek Android üzerinden bu yolun
çalıştığı kurulum sonrası test edilmelidir.

Cloud istemcisinin aynı anahtara ve `project_id` değerine ihtiyacı vardır.
`auth.key` dosyasını **sohbete, depoya veya 8080 web köküne koymayın**. Anahtarı
onaylı gizli dosya/credential mekanizmasıyla Cloud'a aktarın; değeri loglamayın.
Uzak istemci mevcut HTTP/HTTPS proxy ayarlarını kullanır; `--bypass-proxy` yalnız
aynı cihazdaki loopback testi içindir. Anahtar aktarılmadan Cloud'dan imzalı çağrı
yapılabildiği iddia edilmez.

## Girdi yükleme ve tarama

Boş örnek dosyada gerçek fiyat, sıfır zarar veya “finansman riski yok” varsayımı
yoktur. Bu dosyayla çalıştırmanın beklenen sonucu **PAS**'tır.

```sh
python3 -I launch.py client --url http://127.0.0.1:8081 \
  --key-file auth.key --config project.json --bypass-proxy \
  upload --file context.example.json
```

Yanıttaki 32 haneli `id` ile:

```sh
python3 -I launch.py client --url http://127.0.0.1:8081 \
  --key-file auth.key --config project.json --bypass-proxy \
  scan --input-id GIRDI_KIMLIGI
```

İstemci işlem öncesi gizli olmayan `request_id` değerini gösterir. Bağlantı
sonucu belirsizse aynı `--request-id` ile yeniden deneyin; aynı kimlik aynı işi
tekrar başlatmaz. Sonuç için `job --id IS_KIMLIGI`, girdi okumak için
`get-input --id GIRDI_KIMLIGI` kullanılır. Kalıcı sonuçlar geçmiş tarama kaydıdır;
yeni işlem hazırlığı için yeni, taze kanıtla tarama gerekir.

En fazla bir tarama çalışır; yeni işler arasında 30 saniye vardır. Veri bağlayıcı
60 saniyelik toplama bütçesi uygular. Girdi, iş ve sonuç bölümlerinin her biri en
fazla 64 nesne saklar; nesne/istek/yanıt sınırı 128 KiB'dir. Uzaktan silme veya
mevcut dosyayı değiştirme API'si yoktur. Kota dolunca yeni nesne reddedilir; mevcut
kayıtlar sessizce silinmez. Yeniden başlatma sırasında yarıda kalan işler otomatik
tekrar çalışmaz.

## Hisse analizi sınırları

Tarayıcının veri/filtre incelemesi, risk sınır testleri ve açık eksikleri
[SCANNER_REVIEW.md](docs/SCANNER_REVIEW.md); güncel resmî sağlayıcı ve BtcTurk
kaynakları [SCANNER_SOURCES.md](docs/SCANNER_SOURCES.md) içindedir. Adayın
`manual_review_required` alanı tamamlanmamış halt/LULD, broker ve hesap risk
kontrollerini gösterir; `HAZIRLIK` gerçek emir yetkisi değildir.

Bundled `equity_guard` gerçek emir göndermez. Kullanıcı kuralları: 50.000 TL
sermaye, hisse başına 12.500 TL, fiyat 1–5 USD, spread hem ≤0,05 USD hem ≤%2,5,
%3 planlı stop ve günlük 2.500 TL net zarar eşiği. Ücretler/kur makası/açık risk
dahil kontrol yapılır. Stop gerçekleşme fiyatını garanti etmez; bu servis brokerda
pozisyon kapatmaz veya gerçek zararı otomatik durdurmaz.

Canlı çalışma; Alpaca paper veri kimliği, gerçek zamanlı SIP yetkisi, güncel
broker USD/TRY kuru, mevcut seansa ait ücretler sonrası hesap P/L verisi ve
BtcTurk işlem uygunluğu gerektirir. En fazla 20 doğrulanmış sembol desteklenir.
Haber ve SEC finansman incelemesi eksik/belirsizse PAS. BtcTurk Hisse hesabını
otomatik okuyan veya emir gönderen entegrasyon yoktur.

Piyasa verisi sırları HTTP girdisine eklenmez. Gerekirse yalnız servis başlatılan
yerel gizli ortamda `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`,
`ALPACA_SIP_CONFIRMED=yes`, `SEC_USER_AGENT` sağlanır. Anahtar yokken hiçbir canlı
fiyat isteği yapılmaz ve fiyat uydurulmaz. Veri adaptörünün giden adres/yöntemleri
sabit HTTPS GET listesiyle sınırlıdır.

## Geliştirme ve doğrulama

```sh
cd tools/termux-manager
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
```

Testler sentetik fiyat ve geçici yeni dizinler kullanır; telefon, Tailscale
Android aktarımı veya gerçek piyasa hesabı doğrulaması değildir. GitHub Actions
iş akışı yalnız yeni klasörün testlerini çalıştırır, canlı credential kullanmaz.
İmzalama sözleşmesi ve uçlar: [API.md](docs/API.md).
