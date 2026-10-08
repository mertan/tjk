# Yönetim API'si v1

API yalnızca paketlenmiş analiz taramasını yönetir. Her uç nokta imzalı istek gerektirir. Varsayılan adres `http://127.0.0.1:8081` olur; uzaktan istemci aynı porttaki yetkili Tailscale adresini kullanır. Mevcut `8080` dosya sunucusuna yönetim isteği veya anahtar gönderilmez.

## İstek imzası

Kurulum iki değer üretir:

| Değer | Biçim | Gizlilik |
| --- | --- | --- |
| Proje kimliği | 32 küçük harfli hex karakter | Gizli değildir; imzanın servis/proje kapsamını belirler. |
| HMAC anahtarı | 32 rastgele bayt, diskte 64 hex karakter | Gizlidir; `0600` izinli dosyada saklanır. |

Her istekte şu başlıklar birer kez bulunmalıdır:

```text
X-TM-Project: <32 karakter proje kimliği>
X-TM-Time: <Unix zaman damgası, saniye, tam sayı>
X-TM-Nonce: <16 rastgele baytın 32 küçük harfli hex gösterimi>
X-TM-Signature: <HMAC-SHA256, 64 küçük harfli hex karakter>
```

POST gövdesi UTF-8 JSON'dur; `Content-Type: application/json` ve doğru `Content-Length` kullanılmalıdır. GET isteği gövdesizdir. Gövde boyutu en fazla 131.072 bayttır. `Transfer-Encoding`, sıkıştırılmış gövde, yinelenen güvenlik/uzunluk başlıkları, query, kodlanmış yol ve keyfî dosya yolu desteklenmez.

İmzalanacak metin aşağıdaki satırların **tek `\n` karakteriyle**, sonda ek satır olmadan birleştirilmesidir:

```text
TM1
<proje kimliği>
<büyük harfli HTTP yöntemi>
<tam API yolu>
<X-TM-Time değeri>
<X-TM-Nonce değeri>
<ham istek gövdesinin SHA-256 hex özeti>
```

Yol örneği `/v1/inputs` olur; şema, hostname veya query imza yoluna eklenmez. Anahtar dosyasındaki hex metin önce baytlara çevrilir. Gövde, imza hesaplandıktan sonra yeniden JSON'a dönüştürülmemelidir: boşluk veya anahtar sırasındaki değişiklik dahi özeti değiştirir. Gövdesiz GET için boş bayt dizisinin SHA-256 özeti kullanılır.

Python'da imza hesabının özeti:

```python
import hashlib
import hmac

body_hash = hashlib.sha256(body_bytes).hexdigest()
canonical = "\n".join([
    "TM1", project_id, method, path, timestamp, nonce, body_hash,
]).encode("ascii")
signature = hmac.new(bytes.fromhex(key_hex), canonical, hashlib.sha256).hexdigest()
```

Sunucu en fazla ±30 saniye saat farkını kabul eder. Her HTTP denemesinde yeni bir kriptografik rastgele nonce üretilmelidir. Nonce tekrarları kalıcı kayıtla engellenir; 90 saniyelik kayıt süresi kabul penceresinden uzundur. Nonce alanı 4.096 kayıtla sınırlıdır; kota dolduğunda tekrar koruması devre dışı bırakılmaz.

## Yanıt doğrulama

Doğrulanmış isteğin yanıtındaki `X-TM-Response-Signature` başlığı, aynı anahtarla aşağıdaki metnin HMAC-SHA256 hex özetidir. Yine sonda ek satır yoktur:

```text
TM1-RESPONSE
<isteğin nonce değeri>
<HTTP durum kodu>
<ham yanıt gövdesinin SHA-256 hex özeti>
```

İstemci, yanıtı JSON olarak kullanmadan önce HTTP kodu ve **tam alınan baytlar** üzerinden bu imzayı sabit süreli karşılaştırma ile doğrulamalıdır. İmzasız veya hatalı imzalı yanıt analiz sonucu sayılmaz. Kimlik doğrulama veya HTTP çerçeveleme aşamasında reddedilen bir isteğin yanıtı imzasız olabilir. İstemci bunu yerel hata olarak göstermeli; güvenilir bir sunucu sonucu gibi işlememelidir.

## Uç noktalar

Tüm nesne kimlikleri 32 küçük harfli hex karakterdir. Dosya adı veya yol kabul edilmez.

| Yöntem ve yol | İstek gövdesi | Başarılı davranış |
| --- | --- | --- |
| `GET /v1/health` | Yok | `200`: kimliği doğrulanmış sağlık yanıtı. |
| `GET /v1/status` | Yok | `200`: servis durumu ve çalışma sınırları. |
| `POST /v1/inputs` | `{"context": {...}}` | `201`: sunucunun ürettiği `id` ile yeni girdi. |
| `GET /v1/inputs/{id}` | Yok | `200`: kaydedilmiş girdi. |
| `POST /v1/jobs` | Aşağıdaki sabit iş nesnesi | `202`: kabul edilen işin `id` ve `status` alanları. |
| `GET /v1/jobs/{id}` | Yok | `200`: işin güncel durumu; tamamlandığında analiz sonucu. |

Girdi ve iş oluşturma uç noktaları dosya yükleme veya komut yürütme arayüzü değildir. İsteklerde emir açma, shell, dağıtım, silme ve mevcut girdiyi değiştirme işlemi yoktur.

### Veri olmadan girdi oluşturma

```json
{"context": {}}
```

Bu, eksik verinin PAS ürettiğini kontrol etmek için kullanılabilir. Hesap, kur ve piyasa verisi yerine uydurma değer eklenmemelidir. `context` bir JSON nesnesi olmalıdır; analiz alanlarının yeterliliğini tarama motoru denetler. Sağlayıcı anahtarı `context` içine konulmamalıdır. İstemci tarayıcının gerçek zamanlı veri toplamasını, imzalı olsa bile gönderilmiş bir fiyatla değiştiremez.

Yanıtın `id` alanı sonraki çağrıda `input_id` olarak kullanılır. Girdi oluşturma çağrısının ayrı bir istemci idempotency anahtarı yoktur; ağ hatası sonrasında aynı çağrının yeniden gönderilmesi ek bir girdi oluşturabilir. Tek başına girdi oluşturmak tarama başlatmaz.

### Tarama işi oluşturma

```json
{
  "action": "scan",
  "input_id": "<girdi yanıtındaki 32 karakter kimlik>",
  "request_id": "<istemcinin ürettiği 32 karakter rastgele kimlik>"
}
```

Yukarıdaki açıklamalı yer tutucular çalışır kimlik değildir; gerçek çağrıda 32 küçük harfli hex değerler kullanılır. Tek desteklenen `action`, `scan` değeridir. Aynı anda bir tarama çalışabilir; yeni iş başlangıçları arasında en az 30 saniye bulunmalıdır. Sağlayıcı veri toplama bütçesi 60 saniyedir.

`request_id`, aynı mantıksal işin tekrar uygulanmasını önler. Ağ yanıtı kaybolursa aynı `request_id` ve aynı iş gövdesini **yeni HTTP nonce'u ve zaman damgasıyla** tekrar gönderin. Servis mevcut işi döndürür; yeni tarama başlatmaz. Aynı `request_id` farklı içerikle kullanılırsa istek reddedilir. Dönen iş kimliğiyle `GET /v1/jobs/{id}` sorgulanır. HTTP `202`, hisse bulunduğu veya bir finansal emir oluşturulduğu anlamına gelmez.

## Hatalar ve sınırlar

Yanıtta bulunan hata metinleri sır veya ham sağlayıcı yanıtı içermemelidir. Kimlik doğrulama hatası, bilinmeyen nesne, geçersiz şema, nonce/iş uyuşmazlığı ve kota hatası bir analiz sinyali sayılmaz. İstemci yalnızca doğruladığı başarılı iş sonucunu değerlendirmelidir. `PAS` ise motorun geçerli bir analiz sonucudur: yeterli doğrulanmış veri veya kurallara uyan aday bulunmamıştır.

Girdi, iş ve sonuç alanlarının her birinde en fazla 64 kayıt tutulur. Kayıt başına üst sınır 128 KiB'dir. Kotalar dolduğunda eski dosyalar otomatik olarak üzerine yazılmaz. API uzaktan arşivleme/silme sunmaz. Yeniden başlatma veya Android'in süreci durdurması sonrasında önce kayıtlı işin durumunu sorgulayın; sorgu başarısızlığını yeni bir finansal işlem için gerekçe olarak kullanmayın.

Kurulum, anahtar aktarımı ve ağın kalan güven sınırları için [SECURITY.md](SECURITY.md) dosyasına bakın.
