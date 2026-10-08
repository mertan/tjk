# Güvenlik sınırları

Bu servis, Termux içinde **yeni ve yalnızca bu proje için ayrılmış bir dizinde** analiz girdisi kaydetmek, paketle birlikte gelen sabit hisse taramasını başlatmak ve sonucunu okumak içindir. Gerçek finansal emir göndermez. Telefon üzerinde kurulumu ve Android Tailscale üzerinden erişimi ayrıca doğrulanmalıdır; Cloud testleri telefon kurulumu anlamına gelmez.

## Yetki kapsamı

API, JSON verisi kabul eder. Kabuk, SSH, genel komut çalıştırma, çalıştırılabilir dosya yükleme, kod dağıtımı, paket yükleme, dosya gezgini, keyfî dosya yolu veya emir uç noktası yoktur. İstemci bir tarama işi için yalnızca önceden kaydedilmiş girdinin kimliğini verir. Çalıştırılacak kod, modül, komut satırı, Git referansı ve çalışma dizini istemci tarafından seçilemez.

Sağlayıcı bağlantıları paket içindeki izin verilen salt okunur uç noktalarla sınırlıdır. Girdideki kanıt bağlantıları, istemciye istediği adresten veri indirtme yetkisi vermez. BtcTurk Hisse emir entegrasyonu bu servisin parçası değildir. Veri eksikliği, eski veri veya doğrulanmamış risk bilgisi güvenilir bir aday üretmek için yeterli sayılmaz; sistem PAS verir.

Termux'taki betikler aynı Android uygulama kullanıcısının yetkilerini paylaşabilir. Dizin ve API kontrolleri **işletim sistemi sandbox'ı değildir**. Aynı kullanıcı yetkisiyle çalışan kötü niyetli bir yerel süreç, proje kodunu veya anahtarını okuyup değiştirebilir. Bu yüzden uzaktan yüklenen kod hiçbir koşulda çalıştırılmaz; yerel proje dosyaları ve çalıştırılan paket güvenilir olmalıdır.

## Ağ ve kimlik doğrulama

- Varsayılan dinleme adresi `127.0.0.1:8081` olur. Mevcut `8080` HTTP dosya sunucusu değiştirilmez.
- Android Tailscale aktarımı loopback üzerinden çalışmıyorsa `--bind 0.0.0.0 --tailnet-bind` seçeneği açıkça kullanılabilir. Bu seçenek tüm IPv4 arayüzlerinde socket açar; uygulama yalnızca loopback ve `100.64.0.0/10` kaynaklarını kabul eder. İlk sürüm IPv4 kullanır.
- Android VPN aktarımı kaynak adresini `127.0.0.1` olarak gösterebilir. Kaynak IP filtresi Tailscale kimliğini kanıtlamaz. `X-Forwarded-For` gibi başlıklara güvenilmez.
- Sağlık ve durum sorguları dahil her API isteği HMAC-SHA256 ile doğrulanır. Proje kimliği herkese açık olabilir; anahtar gizlidir.
- Tailscale, doğru tailnet bağlantısındaki taşımayı şifreler. API'nin HTTP kullanması tek başına TLS sağlamaz. Yetkili HTTP proxy'si, yönlendirdiği HTTP gövdesini görebilir. HMAC veri gizliliği sağlamaz; istek ve yanıt bütünlüğünü doğrular.
- İstemci yanıt imzasını doğrulamadan analiz sonucunu güvenilir saymamalıdır. Yönlendirmeler otomatik takip edilmemelidir.

İstek imzası proje kimliğini, yöntemi, tam yolu, zaman damgasını, tek kullanımlık nonce'u ve ham gövdenin özetini kapsar. Saat farkı en fazla 30 saniyedir. Nonce kayıtları diskte atomik `O_EXCL` oluşturma ile tutulur; aynı isteğin yeniden başlatma sonrasında tekrar uygulanması da engellenir. Kayıtlar 90 saniyelik tekrar önleme süresi boyunca saklanır. Saatler doğru olmalıdır; saat hatasını aşmak için doğrulama kapatılmamalıdır. Ayrıntılar [API sözleşmesindedir](API.md).

## Yerel dosyalar ve anahtar

Kurulum mevcut hedef dizin varsa durmalıdır. Var olan bot dizinine kurulum, mevcut dosyaların üstüne yazma, genel temizlik, profil/cron düzenleme veya mevcut servisi yeniden başlatma bu işlemin parçası değildir. Çalışma dizini `0700`, gizli dosyalar `0600` izinleriyle oluşturulur.

32 bayt rastgele HMAC anahtarı diskte 64 karakterlik hex biçimindedir. Anahtarı terminal çıktısına, sohbete, GitHub'a, loglara veya `8080` dosya sunucusunun erişebildiği bir dizine koymayın. İstemciye aktarım yalnızca onaylanmış gizli dosya aktarımı veya gizli ortam yapılandırması üzerinden yapılmalıdır. Paylaşılacak bir aktarım yöntemi henüz yoksa uzaktan erişim hazır sayılmaz; yerel test anahtarın açıklanmasını gerektirmez.

Runtime alanındaki `inputs`, `jobs`, `results` ve `nonces` özel dizinlerdir. Dosya adları doğrulanmış 32 haneli hex kimliklerden türetilir: girdi kimliğini sunucu, iş/sonuç kimliği olan request_id değerini istemci üretir; istemci yolu dosya açma işlemine verilmez. Dizin dosya tanıtıcıları ve `nofollow` kontrolleri, symlink üzerinden yönlendirmeyi engellemek için kullanılır. API mevcut girdiyi veya sonucu silme/değiştirme işlemi sunmaz. Servis yalnızca kendisinin oluşturduğu iş durumu ve nonce kayıtlarının yaşam döngüsünü yönetir.

Anahtar, nonce kayıtları, hesap girdileri, sonuçlar ve günlükler Git kapsamına alınmamalıdır. GitHub'da yalnızca kod, belge ve açıkça sentetik test verisi bulunmalıdır. Bu dosyaları bir tanılama raporuna eklemeyin.

## Kaynak sınırları ve kesinti davranışı

İstek ve kayıt boyutu üst sınırı 128 KiB'dir. Girdi, iş ve sonuç alanlarının her biri 64 kayıtla; nonce alanı 4.096 kayıtla sınırlıdır. Kota dolunca mevcut kayıtların üzerine yazılmaz. Uzaktan silme uç noktası bulunmadığından arşivleme veya yeni çalışma alanı oluşturma yerel ve açık bir bakım işlemi gerektirir.

Aynı anda bir tarama çalışır; yeni tarama başlangıçları arasında en az 30 saniye bulunur. Sağlayıcı toplama bütçesi 60 saniyedir. Bu bütçe, telefon işletim sisteminin servisi askıya almasını engelleyen bir çalışma garantisi değildir. Telefon uykuya geçtiğinde, Termux kapatıldığında veya süreç yeniden başlatıldığında işi yeniden uygulamadan önce iş kimliği ile durum sorgulanmalıdır.

HMAC tekrar koruması ile iş tekrar koruması ayrı amaçlara hizmet eder. Ağ hatasında yeni nonce ile aynı `request_id` ve aynı iş gövdesi gönderilir; ikinci bir tarama başlatılmamalıdır. Aynı `request_id` farklı bir iş için kullanılamaz.

## Doğrulanması gereken kontroller

Testler en az şu davranışları kapsamalıdır:

1. Anahtarsız erişimin reddi; gövde/yol/yöntem/proje değişikliğinde imza hatası.
2. Aynı nonce'un eşzamanlı ve yeniden başlatma sonrasında reddi; zaman penceresi sınırları.
3. Yinelenen güvenlik başlıkları, belirsiz HTTP gövde uzunluğu, query ve kodlanmış yolların reddi.
4. Dizin geçişi, symlink ve mevcut dosya üzerine yazma girişimlerinin reddi.
5. Boyut/kayıt/iş kotası; tekrar gönderilen işin bir kez uygulanması.
6. Veri erişimi olmadan PAS; gerçek emir veya genel komut yolunun bulunmaması.
7. Kurulumun ve testlerin proje dışındaki mevcut dosyaları değiştirmemesi.

Bu liste test sonucu değildir. Gerçekte çalıştırılan testler ve telefon üzerinde doğrulanmamış noktalar test raporunda ayrıca belirtilmelidir.

Kurucu, gerçek web köklerini `--public-root` ile alır veya `--discover-http-port 8080` ile aynı UID altında çalışan standart Python HTTP sunucusunun süreç bilgisini salt okunur inceler. Hedef web kökü altında veya mevcut sembolik bağlantıları üzerinden erişilebilir olamaz. Mevcut bağlantı ağacı sınırlı olarak incelenir; okuma hatası, dolaşım sınırı veya belirsizlik kurulumu anahtar oluşturulmadan durdurur. Bildirilmeyen sunucular ve sonradan değiştirilen kök/bağlantılar bu doğrulamanın kapsamı değildir.
