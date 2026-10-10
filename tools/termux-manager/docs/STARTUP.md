# Termux başlatma düzeltmesi ve güvenli tanılama

Önceki sürüm `Manager could not start; verify private project configuration and
bind availability.` mesajıyla bütün başlangıç hatalarını aynı gösteriyordu.
`auth.key`, `project.json` ve `runtime` bulunması tek başına doğrulamaların
geçtiğini göstermez. Eski `start.sh`, proje kimliğini gömülü argümandan alır;
`server.py` doğrudan `project.json` okumuyordu.

## Doğrulanan kod hatası

Anahtara ve runtime'a erişirken bütün üst dizinler `O_RDONLY | O_DIRECTORY` ile
açılıyordu. Android'de `/data` gibi bir üst dizin yalnız geçişe izin verip
listelemeyi engelleyebilir. Bu durumda bilinen alt dosya doğrudan okunabildiği
halde eski doğrulayıcı `EACCES` ile durur. Bu işlem **socket bind'dan önce**
gerçekleşir; bind adresini değiştirmek bu hatayı gidermez.

Hata, Linux üzerinde ayrıcalıksız kullanıcı ve `0111` izinli üst dizinle yeniden
üretildi. Düzeltme yalnız üst dizinleri `O_PATH | O_DIRECTORY | O_NOFOLLOW` ile
geçer; son dizin normal okunabilir descriptor olarak açılır. Symlink reddi,
sahiplik, özel dizinlerde `0700`, özel dosyalarda `0600`, tek hardlink ve içerik
sınırları korunur. Hiçbir dizinin izinleri otomatik değiştirilmez. Son descriptor
`listdir`, `flock`, `fsync` işlemlerini desteklemeye devam eder.

Bu, telefon belirtileriyle uyumlu ve testle doğrulanmış bir kod hatasıdır.
Telefonun gerçek errno çıktısı alınmadan aynı nedenin o cihazda kesinleştiği
iddia edilmez. Python 3.13 sürümünün bu hataya sebep olduğuna dair kanıt yoktur.

## Mevcut kurulumu değiştirmeden yeni kodu başlatma

Aşağıdaki blok yeni bir kaynak klasörü oluşturur; mevcut klonu veya kurulmuş
kaynak dosyalarını güncellemez. `DOGRULANMIS_TAM_COMMIT_SHA` yerine dağıtım
mesajındaki tam düzeltme commit'ini yazın. `TM_PROJECT` önceki kurulum konumudur.
Kurulum farklı bir yerdeyse yalnız bu konumu uyarlayın.

```sh
(
set -eu
umask 077
TM_PROJECT=$(CDPATH= cd -- "$HOME/../tjk-termux-manager-20261008" && pwd -P)
TM_FIX=$(mktemp -d "$HOME/tjk-manager-fix.XXXXXX")
git clone --quiet --single-branch --branch codex/termux-management-api-20261008 \
  https://github.com/mertan/tjk.git "$TM_FIX"
git -C "$TM_FIX" checkout --quiet --detach DOGRULANMIS_TAM_COMMIT_SHA
exec python3 -I "$TM_FIX/tools/termux-manager/launch.py" serve \
  --root "$TM_PROJECT/runtime" --key-file "$TM_PROJECT/auth.key" \
  --config "$TM_PROJECT/project.json" --bind 0.0.0.0 --tailnet-bind --port 8081
)
```

`pwd -P` mevcut kurulumun fiziksel yolunu kullanır; ham `..` veya symlink
bileşenleri güvenlik kontrollerini gevşetmeden giderilir. Anahtar ve ayar dosyası
yerinde, salt okunur kullanılır; hiçbir gizli dosya yeni kaynak klasörüne
kopyalanmaz. `--config` mevcut kurucunun oluşturduğu özel `0600` JSON dosyasından
yalnız proje kimliğini alır; `--project-id` alternatifi de desteklenir.

Başlayan servis kendi runtime dizininde normal süreç kilidini oluşturabilir;
sonraki imzalı istekler nonce, girdi ve iş kayıtları oluşturabilir. Eski botlar,
8080 sunucusu, anahtar, ayarlar ve kurulu kaynaklar değiştirilmez. Gerçek finansal
emir yetkisi kapalıdır. Servis ön planda çalışır; Ctrl+C yalnız bu süreci durdurur.
Eski `start.sh` eski kodu başlatır; bu düzeltmeyi çalıştırmak için yeni kaynak
yolundaki `launch.py` kullanılmalıdır. İstemci için de yeni kaynak kodunu kullanın;
aynı üst dizin hatası eski istemcinin anahtar okumasını etkiler.

## Salt okunur kontrol ve hata kodları

Aynı `serve` komutuna `--check` eklendiğinde anahtar, proje kimliği, dizin ve
bind **ayarları** doğrulanır. Socket açılmaz, süreç kilidi alınmaz/oluşturulmaz,
dosya içeriği veya izinleri değiştirilmez. Başarılı sonuç:

```json
{"event":"manager_preflight_ok","execution_enabled":false,"server_lock_checked":false,"socket_bound":false}
```

Bu sonuç portun boş olduğunu veya Tailscale erişimini doğrulamaz. Gerçek bind
yalnız normal başlatmada yapılır. Başarılı başlangıçta `Analysis API listening
on 0.0.0.0:8081; execution_enabled=false` mesajı görülür.

Hatalar yerel stderr'e tek JSON kaydı olarak gider. Örnek:

```json
{"event":"manager_start_failed","stage":"authentication_key","code":"unsafe_directory","errno":"EACCES","execution_enabled":false,"python":"3.13.13"}
```

Bu örnek bir gerçek telefon ölçümü değildir. Çıktı yalnız sabit aşama/kod,
tanınan errno adı ve Python sürümü içerir. Anahtar, dosya yolu, dosya içeriği,
hesap bilgisi, ortam değişkenleri, exception metni ve traceback yazılmaz.
Nedeni bilinmeyen hatalar `unexpected_failure` olur; exception metni eklenmez.

| Aşama / kod | Anlamı |
| --- | --- |
| `arguments / invalid_arguments` | Komut seçeneği eksik/geçersiz; değerler loglanmaz. |
| `bind_config / invalid_bind` veya `invalid_port` | Dinleme ayarı kabul edilmiyor; `0.0.0.0` için `--tailnet-bind` gerekir. |
| `project_config / invalid_json`, `not_found`, `unsafe_file` | Yapılandırma okunamadı, JSON geçersiz veya özel dosya denetimi başarısız. |
| `identity / invalid_project_id` | Kimlik 32 küçük harfli hex karakter değil. |
| `authentication_key / invalid_key` | Anahtar beklenen 64 küçük harfli hex karakter biçiminde değil. |
| `authentication_key / unsafe_file` | Anahtar açılamıyor veya sahiplik/0600/tek hardlink/boyut denetimi başarısız. |
| `authentication_key` veya `runtime / unsafe_directory` | Bir dizin açılamıyor; runtime dizini/alt dizinleri için sahiplik/0700 denetimi de yapılır. |
| `server_lock / manager_already_running` | Aynı runtime'ı kullanan başka süreç kilidi tutuyor; ikinci servis başlamaz. |
| `server_lock / lock_open_failed`, `unsafe_server_lock`, `lock_unavailable` | Süreç kilidi açılamıyor, dosyası güvenli değil veya kilitleme başarısız. |
| `socket_bind / socket_unavailable`, errno `EADDRINUSE` | Port başka bir süreçte açık. Mevcut süreç otomatik durdurulmaz. |
| `socket_bind / socket_unavailable`, errno `EADDRNOTAVAIL` | İstenen adres bu cihazda bind edilemiyor. |
| Herhangi bir aşama, errno `EACCES` veya `EPERM` | İzin/erişim politikası engeli; izinler otomatik gevşetilmez. |

`errno:null` işletim sistemi hatası yerine uygulama doğrulamasının başarısız
olduğunu gösterebilir. Güvenli JSON hata satırı paylaşılabilir; `auth.key`,
`project.json` içeriği veya ortam değişkenleri paylaşılmamalıdır.
