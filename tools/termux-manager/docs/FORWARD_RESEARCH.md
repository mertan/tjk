# ABD 1–5 dolar: geçmiş veri ve ileriye dönük araştırma

29 Ekim 2026 durumu: **VERİ / MODEL EKSİK — TAHMİN ÜRETİMİ BAŞLAMADI.**

Bu ekleme mevcut fiyat/hacim tarayıcısından ayrı bir hazırlık katmanıdır.
Bir yıllık veri indirme girişimini ve bugünden itibaren zaman damgalı hazırlık
kontrollerini kaydeder. Eğitilmiş model, doğrulanmış yükseliş olasılığı,
aktif canlı tahmin, otomatik emir veya zamanlanmış tarama içermez.

## Gerçek erişim kontrolü

- Alpaca bağlı veri aracına JFIN için 29 Ekim 2025 13:30–13:40 UTC arası
  1 dakikalık SIP örneği soruldu; bağlantı zaman aşımı döndü, 0 mum alındı.
- Bu çalışma ortamında mevcut adaptörün ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
  değişkenleri ve standart APCA alternatifleri yok. Değerleri okunmadı/yazılmadı.
- Bu sonuç Alpaca'nın genel olarak kapalı olduğu, ücretli plan gerektiği veya
  kullanıcının telefonundaki anahtarların eksik olduğu anlamına gelmez.
- Investing'in resmî genel API'si yok; bir yıllık dakika arşivi doğrulanmadı.
  Bu ekleme Investing scraper'ı veya gizli endpoint kullanmaz.

## Çalıştırma

Python 3.10+ ve standart kütüphane yeterlidir. İzinli Alpaca erişimi zaten
kurulu bir ortamda, en fazla 20 açıkça seçilmiş sembol için:

```sh
python3 -B forward_research.py history --symbols AAA,BBB --output-dir private-history
python3 -B forward_research.py check --journal private-state/forward.sqlite --history-manifest private-history/manifest.json --output private-state/check-unique.json
```

AAA/BBB gerçek adaylar veya hisse önerisi değildir. Yerel veriyi ve SQLite
dosyasını depoya eklemeyin. Anahtarları komuta, rapora veya kaynak koda yazmayın.
Hiçbir komut hesap emri ya da Telegram mesajı göndermez. Komutlar bir kez
çalışır; zamanlayıcı veya telefondaki çalışan botla bağlantı kurulmuş değildir.

## Verinin ve kayıtların anlamı

- Fiyat aralığı 1–5 dolar, işlem tarihindeki **ham** fiyatla uygulanacaktır;
  bugünkü fiyatla bir yıllık evren seçilmez. İndirici tüm gözlenen mumları korur.
- İndirilen listenin sınırı ve kaynak/feed/ham düzeltme seçimi kaydedilir.
  Bu, bütün ABD hisselerini kapsayan tarihsel bir evren değildir.
- `retrieval_complete` yalnız sayfalama/parçaların aktarıldığını söyler.
  Eksiksiz seanslar, borsadan çıkan hisseler, sembol/şirket kimliği, bölünmeler,
  tarihsel işlem yapılabilirlik ve kotasyon kapsamı ayrıca doğrulanmalıdır.
  Bu doğrulama ve gerçek eğitim yapılmadan `training_ready` doğru olmaz.
- `check` komutu daima açıkça BLOCKED, boş tahmin listesi ve boş olasılık
  üretir. Manifestte elle `training_ready=true` yazmak modeli oluşturmaz.
- Her kontrol üretildiği saatte SQLite'a eklenir. Uygulama eski/gelecek saatli
  kontrolü, saat geri alınmasını ve normal UPDATE/DELETE'yi reddeder. Kayıtlar
  hash zinciriyle bağlanır. Bu dışarıdan doğrulanmış, değiştirilemez bir denetim
  sistemi değildir; yerel dosya sahibi veritabanını değiştirebilir/silebilir.
- Kontrol kaydı bir tahmin, gerçekleşmiş işlem veya başarısız tahmin sayılmaz.

## Modeli tamamlamak için gerekli çalışma

1. Son tamamlanmış seansa kadar bir yıllık gerçek dakikalık veri ve tarihsel
   evren kapsamını doğrula; eksik günleri/sembolleri açıkça raporla.
2. 1–5 dolar için giriş anında bilinen özellikleri ve hedefi sabitle:
   girişten sonraki 20 dakikada stopa gelmeden 2R hedefine erişme.
3. İlk 9 ayı geliştirme/eğitim, son 3 ayı dokunulmamış kronolojik sınama için
   ayır. Etiket ufuklarının sınırdan taşmasını engelle; maliyet/kayma senaryoları,
   eksik sonuçlar, sinyal sayısı, kalibrasyon ve en büyük düşüşü raporla.
4. Modeli, veri kesim saatini ve sürümü dondur. Üretim saati sonrasında geçerli
   olacak koşullu tahmini kalıcı yaz; sonradan giriş kotasyonunu ve sonucu ayrı
   olaylar olarak ekle. Bu canlı sinyal/sonuç motoru henüz uygulanmadı.

Eski `replay.py` bütün seansın 390 dakikasını ister; canlı tahmin fonksiyonu
olarak kullanılamaz. Eski 141 Trade Ideas kayıtları da bu yeni modele eğitim
verisi veya gerçek mum testi sayılmaz. Açılış adayı 09:50–10:30 New York
penceresindedir; günün bu saatleri geçince sabah için tahmin geriye yazılmaz.
Gün ortası stratejisi ayrıca tanımlanıp sınanmalıdır.

## Doğrulama

Yeni kontroller sentetik mekanizma testleridir: tarihsel performans veya
gelecekte kazanma kanıtı değildir. Mevcut tarayıcı ve Telegram akışı değişmez.
29 Ekim doğrulaması: `python3 -B -m unittest discover -s tests` toplam 509
test çalıştırdı; 506 geçti, root ortamındaki izin testlerinden 3'ü atlandı.
Gerçek ilk kontrol ve bağlantı sonucu `FORWARD_CHECK_2026-10-29.json` ile
`HISTORY_ACCESS_2026-10-29.json` dosyalarındadır. İndirici anahtar yoksa ağ
çağrısı veya dosya oluşturma yapmadan JSON hata sonucu döndürür; bu erişim
denemesinin raporu ayrıca kaydedilmiştir.

Resmî veri belgeleri:

- https://docs.alpaca.markets/us/reference/stockbars
- https://docs.alpaca.markets/us/docs/market-data-faq
- https://www.investing-support.com/hc/en-us/articles/115005473825-Do-You-Offer-API-Access-at-Investing-com
- https://www.investing-support.com/hc/en-us/articles/115003946765-Exploring-Historical-Data-on-Investing-com-Charts
