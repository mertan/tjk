# Test ve inceleme sonucu

8 Ekim 2026, Cloud Linux üzerinde **246 testin tamamı** hem Python **3.12.14**
hem de Python **3.13.13** ile geçti. Testler ayrıcalıksız kullanıcıyla çalıştı.

| Kapsam | Test |
|---|---:|
| Sermaye, fiyat, spread, stop, günlük risk ve veri tazeliği | 36 |
| Salt okunur Alpaca/SEC veri adaptörü, hata ve zaman kontrolleri | 37 |
| HMAC, kalıcı nonce, özel dosyalar ve sınırlandırılmış depolama | 44 |
| İstemci imza doğrulaması, kurulum ve web kökü/bağlantı koruması | 55 |
| Standart Python HTTP kökünün salt okunur keşfi | 8 |
| Sabit tarama, tek iş ve kalıcı tekrar koruması | 9 |
| Yeni kurulumdan gerçek CLI/HTTP uçtan uca akış | 1 |
| HTTP protokolü, kimlik, sınırlar ve işlem kilidi | 20 |
| Android benzeri üst dizin izinleri ve özel JSON okuma | 12 |
| Başlatma hataları, gizli bilgi sızdırmayan tanılama ve salt okunur kontrol | 24 |

Komut: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v`.

Başlatma düzeltmesinin regresyonunda, `0111` üst dizin altında bilinen anahtar
doğrudan okunabilirken önceki `O_RDONLY` geçişinin gerçek `EACCES` ürettiği
doğrulandı. `O_PATH` ile onarılan geçiş, son dizinin listeleme/kilit/sync işlevini
ve symlink/izin korumalarını koruyor. Test gerçek Android SELinux politikasını
taklit ettiğini iddia etmez.

Gerçek dolu socket için `socket_bind / socket_unavailable / EADDRINUSE`, çalışan
süreç kilidi için `server_lock / manager_already_running` doğrulandı. Başarısız
bind kilidi bırakıyor; tekrar başlatma mümkün. Anahtar/yol/hesap işaretçileri,
keyfi exception mesajları ve döngülü neden zincirleri tanılama çıktısına sızmıyor.
`--check` dosya içeriklerini/izinlerini koruyor, socket veya kilit oluşturmuyor.
`--config` ile başlangıç mevcut dosyaları koruyup yalnız normal runtime kilidini
oluşturuyor. [Başlatma belgesi](STARTUP.md) aynı anahtarla yeni kaynak dizininden
çalıştırma yolunu açıklıyor. CI Python 3.12 ve 3.13 matrisi kullanıyor.

Uçtan uca test yeni geçici dizine kurdu, imzalı istemciyle JSON girdisi yükledi,
pakete gömülü taramayı başlattı ve **PAS / execution_enabled=false** sonucunu
okudu. Mevcut hedefe tekrar kurulum reddedildi; başlangıçtaki örnek dosya aynı
kaldı. Testler gerçek piyasa fiyatı veya broker hesabı kullanmadı.

Bağımsız incelemede bulunan ve düzeltilen başlıca durumlar:

- Bildirilen web kökü içindeki mevcut symlink'in yeni kardeş proje/anahtarına
  erişebilmesi: sınırlı, salt okunur bağlantı ağacı kontrolüyle reddediliyor.
- Gelecekteki anahtara işaret eden dangling symlink: anahtar üretilmeden reddediliyor.
- Özel Python betiğinin argümanındaki `-m http.server` ifadesinin sunucu sanılması:
  gerçek interpreter modül çağrısı doğrulanıyor; belirsizlikte keşif duruyor.
- Depolama limitine sığan nesnenin HTTP yanıt zarfıyla limiti aşması: imzalı 413.
- Yeniden başlatma/nonce tekrar kullanımı, bozuk gövde, yinelenen başlıklar,
  symlink/hardlink, dizin geçişi ve yavaş istekler: regresyon testleri var.

Bu testler **Android/Termux üzerinde yapılmadı**. Mevcut 8080 servisi değiştirilmedi,
telefona kurulum yapılmadı ve SSH kullanılmadı. Yerel çalışma alanındaki mevcut
projenin 11 kaynak dosyasının SHA-256 değerleri değişmedi. GitHub değişiklikleri
yeni klasör ve yeni test iş akışı eklemelerinden oluşur.

Kod anahtar, hesap bilgisi veya gerçek tarama sonucu içermez. GitHub Actions
aynı sentetik testleri çalıştırır; uzak CI sonucu commit üzerindeki kontrol
durumundan ayrıca görülebilir. Telefon erişimi, anahtarın güvenli aktarımı,
Tailscale ACL'si ve gerçek veri aboneliği kurulum sonrasında ayrıca doğrulanır.
