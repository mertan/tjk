# Ortak Telegram komutları ve başarı kaydı

Bu modül mevcut **Python Telegram alıcısının içinden** çağrılır. Telegram'a bağlanmaz; token, polling, webhook veya ikinci bir alıcı içermez. Telefon servisi başlatmaz ve bahis/finansal emir göndermez.

Mevcut alıcının kütüphanesi ve dosyası henüz paylaşılmadığı için canlı bağlantı yapılmamıştır. Aşağıdaki arayüz çevrimdışı test edilir; mevcut alıcının uygun mesaj işleyicisine bağlanması ayrıca gerekir.

| Komut | Davranış |
| --- | --- |
| `/at` | At yarışı sağlayıcısının doğrulanmış, güncel girdilerini değerlendirir. AGF kullanılmaz. |
| `/basket` | Basketbol sağlayıcısının doğrulanmış, güncel girdilerini değerlendirir. |
| `/futbol` | Futbol sağlayıcısının doğrulanmış, güncel girdilerini değerlendirir. |
| `/hisse` | Hisse sağlayıcısının zorunlu veri ve risk koşullarını değerlendirir. Emir göndermez. |
| `/durum` | Yerel sağlayıcı/kayıt durumunu gösterir. Ağ sorgusu yapmaz. |
| `/performans` | Başarıyı branş ve model bazında, PAS ve bekleyen sonuçları ayrı sayarak gösterir. |

Üretim veri sağlayıcıları ve sonuç doğrulayıcıları bu değişiklikle kurulmaz. Bir sağlayıcı yoksa veya zorunlu veri eksik, eski ya da doğrulanamıyorsa **PAS** kaydedilir. PR #6'daki canlı TJK kaynağının güncelliği doğrulanamadığından bu koşul kaldırılmış sayılmaz. Tahminin kendisi doğrulanmış sonuç değildir.

## Mevcut alıcıya bağlama

`tools/telegram-commands` dizinini mevcut Python uygulamasının modül arama yoluna ekleyin. İzin verilen sohbet ve kullanıcı listelerinin ikisi de açıkça verilmelidir. Bu listeleri gelen mesajdan türetmeyin.

```python
from tjk_commands import CommandDispatcher, PredictionStore

# PRIVATE_PROJECT_DIR: yalnız bu bileşene ayrılmış yeni, özel bir Path.
# Mevcut bot veritabanını veya HTTP sunucusunun yayınladığı dizini kullanmayın.
store = PredictionStore(PRIVATE_PROJECT_DIR / "commands.sqlite3")
dispatcher = CommandDispatcher(
    store,
    allowed_chat_ids=ALLOWED_CHAT_IDS,
    allowed_user_ids=ALLOWED_USER_IDS,
    providers={},  # Sağlayıcı yok: tahmin komutları PAS üretir.
    bot_username=EXISTING_BOT_USERNAME,
)

def existing_message_handler(update_dict):
    reply = dispatcher.handle_update(update_dict)
    if reply is not None:
        # Mevcut alıcının mevcut gönderme işlevini kullanın.
        # Yeni polling/webhook döngüsü başlatmayın; parse_mode kullanmayın.
        existing_send(reply.chat_id, reply.text)
```

Bu bir bağlantı şemasıdır: `PRIVATE_PROJECT_DIR`, `ALLOWED_CHAT_IDS`, `ALLOWED_USER_IDS`, `EXISTING_BOT_USERNAME` ve `existing_send` mevcut uygulamada tanımlanmalıdır. Kütüphane bir update nesnesi veriyorsa, desteklediği `to_dict()` ile sözlük elde edin. Yalnız mevcut alıcının doğruladığı gerçek Bot API update'lerini geçirin; mesaj içindeki JSON'u update olarak kabul etmeyin. `None` dönüyorsa diğer mevcut komut işleyicisine devam edilebilir; yanıt döndüyse aynı komutu ikinci kez işlemeyin. Async alıcıda mevcut gönderme yöntemini `await` edin; yeni event loop açmayın. Uygulama kapanırken `store.close()` çağırın. Token bu modüle verilmez. Mesajın gönderilmesi mevcut alıcının sorumluluğudur; testler Telegram mesajı göndermez.

## Sağlayıcı sözleşmesi

`ProviderRegistration(model="model-v1", callback=..., source_hosts=frozenset({...}))` bir branşa kaydedilir. Callback, `CommandRequest(sport, args, now)` alır ve `Candidate` döndürür. Bunlar incelenmiş uygulama kodları içindir; Telegram kullanıcısının sağlayıcı kaydetmesi veya `inputs_complete/verified` bayrağı göndermesi desteklenmez. Sağlayıcı model girdilerinin gerçek değerlerini, alan bütünlüğünü ve kaynak zamanının anlamını doğrulamalıdır. Bir boolean alanı, tek başına dış verinin doğrulandığını kanıtlamaz. Üretim adaptörü bağlanmadan önce bu doğrulama ayrıca incelenmelidir; AGF hiçbir adaptörde özellik olarak kullanılmamalıdır.

Her zorunlu girdi için kaynak kimliği, izin verilen HTTPS kaynağı, saat dilimli kaynak zamanı, doğrulama durumu ve bilinen gecikme gerekir. İndirme saati, program saati veya gün içi genel heartbeat kaynak zamanının yerine geçmez. Kaynak yaşı ve bildirilen gecikme aşağıdaki sınırları aşarsa PAS oluşur; gelecek zaman, bilinmeyen gecikme, eksik girdi ve izin verilmeyen kaynak da PAS'tır. Tahmin ancak olayın başlangıç/kapanış anından önce kaydedilebilir.

| Branş | Zorunlu girdiler ve en fazla yaş |
| --- | --- |
| at | event/runners/history: 300 sn; odds: 60 sn; ratings: 24 saat |
| basket, futbol | event: 300 sn; lineups: 900 sn; form: 24 saat; market: 60 sn |
| hisse | prices/nbbo/account: 5 sn; volume/momentum: 60 sn; news: 24 saat; sec: 900 sn; fx: 300 sn |

Hissede fiyat ve NBBO için ayrıca bildirilen gecikme **0** olmalıdır. Decimal risk kontrolleri: fiyat 1–5 USD; spread hem ≤0,05 USD hem alış kotasyonuna göre ≤%2,5; pozisyon ≤12.500 TL; mevcut pozisyonlar dahil toplam ≤50.000 TL; planlanan stop %3; günlük gerçekleşmiş+gerçekleşmemiş zarar <2.500 TL ve yeni planlanan stop zararıyla toplam ≤2.500 TL. Göreli hacim en az 2, momentum pozitif, doğrulanmış pozitif haber, temiz SEC finansman kontrolü ve açık normal seans gerekir. Bu değerler işlem talimatı oluşturmaz. Stop fiyatı gerçekleşme garantisi değildir. Eksiksiz sentetik hisse girdisinde bile yanıt yalnız **İZLE (gözlemsel tahmin)** olur; AL veya emir yoktur.

`PR6RaceProvider(existing_snapshot_reader)` mevcut uygulamanın önceden elde ettiği analizini okuyabilir; kendi ağı veya kaynak toplayıcısı yoktur. PR #6 kaynak saatini doğrulamadığından `SOURCE_FRESHNESS_UNVERIFIED/PAS` korunur. Ham adaylar, AGF ve yeniden puanlama bu köprüden geçirilmez.

## Kayıtlar ve başarı hesabı

Tahminler (`predictions`) ile doğrulanmış gerçek sonuçlar (`verified_results`) ayrı SQLite tablolarında tutulur. Üçüncü tablo `command_receipts`, tekrar teslim edilen update'leri aynı kayda bağlar. Aynı sohbet, branş, model sürümü, olay, pazar ve değerlendirme zamanı için farklı komutlar da tek tahmin sayılır; böylece tekrar istemek başarıyı şişirmez. Önceki bir tahmin kaydı yeniden bulunduğunda eski seçim yeni sinyal gibi gönderilmez: yanıt `PAS / PREVIOUS_RECORD_EXISTS` olur, ilk kayıt ve başarıdaki tek ağırlığı korunur. Bu tekrar yanıtı yeni PAS/tahmin satırı oluşturmaz. Diğer PAS kayıtları istek bazındadır. Sohbetler birbirinin performansını okuyamaz.

Sonuç kaydı Telegram komutundan alınmaz. `PredictionStore(..., verifiers={"source-id": trusted_verifier})` ile uygulamanın açıkça kaydettiği doğrulayıcı, yetkili kaynağın kesin sonucunu doğruladıktan sonra `VerifiedResult` döndürür. `store.record_result("source-id", evidence)` bunu kaydeder; varsayılan doğrulayıcı yoktur. Serbest bir `verified=True` iddiası veya kullanıcı mesajı kabul edilmez. Verifier başarısızsa sonuç yazılmaz ve istisna metni dışarı verilmez. Gerçek doğrulayıcıların kaynak otoritesi ve kesin sonuç durumunu doğrulama mantığı henüz bağlanmamıştır; o zamana kadar tahmin bekler. Testlerdeki doğrulayıcı ve örnekler kurmacadır.

`FINAL` için sonuç zamanı olayın kapanışından önce olamaz; doğrulama ve kayıt zamanları geriye gidemez. `VOID` iptali olay başlamadan doğrulanabilir. İptal ayrı sayılır ve yeni tahmini engeller. Sonuç; branş, olay, pazar ve kapanış/hedef zamanı tam eşleşince tahminle ilişkilendirilir. Hisse adaptörü sabit hedef anını ve sonucun nasıl değerlendirileceğini tahminden önce tanımlamalıdır. Aynı model sürümüyle sonradan seçim değiştirmek desteklenmez. Çelişen sonuç düzeltmesi sessizce üzerine yazılmaz; açık bir denetimli düzeltme tasarımı gerektirir.

Başarı oranının paydası yalnız doğrulanmış sonuçla eşleşen, değerlendirilebilir tahminlerdir. PAS ve bekleyen kayıtlar paydaya eklenmez. Branş/model grupları birbirine karıştırılmaz. Başarı oranı kâr/zarar veya gelecek başarı garantisi değildir. Kayıtlar değişmez tutulur; bir tahminin sonradan sonucuna göre düzenlenmesi desteklenmez.

Yeni veritabanını yalnız uygulamanın erişebildiği özel bir dizinde tutun. Dizin sahibi uygulamanın kullanıcısı olmalı ve izinleri `0700` olmalıdır. Store yeni dizinleri özel izinlerle oluşturur; mevcut ilgisiz veritabanına göç uygulamaz. Token, HMAC anahtarı veya mevcut botların veritabanını bu dizine kopyalamayın. Veritabanını Git'e eklemeyin ve HTTP üzerinden yayınlamayın.

## Yerel test

Depo kökünde, Python 3.12 veya 3.13 ile:

```sh
PYTHONPATH=tools/telegram-commands python3 -m unittest discover -s tools/telegram-commands/tests -v
```

Testler kurmaca girdiler ve geçici SQLite dosyaları kullanır; piyasa taraması, Telegram bağlantısı veya telefon kurulumu yapmaz. CI aynı testleri Python 3.12 ve 3.13'te çalıştırır.

Yerel doğrulama: Python 3.12.14 ve 3.13.13 üzerinde **76/76** test başarılı; depodaki **35/35** Node regresyon testi de başarılı. Gerçek Telegram alıcısı, üretim veri/sonuç sağlayıcısı veya telefon bağlantısı test edilmiş sayılmaz.

Bu geliştirme PR #6 dalının üzerine hazırlanır; birleştirme sırası PR #6, ardından ortak komut PR'ıdır. İkisi için de kullanıcı onayı gerekir. Bu belge kurulum veya servis başlatma talimatı değildir; mevcut alıcı ayrıntıları geldikten sonra bağlantı noktası kesinleştirilmelidir.
