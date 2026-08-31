# mcptap

MCP stdio sunucuları için sıfır bağımlılıklı telscop (wire tap).

Herhangi bir MCP sunucusunu tek satırlık bir config değişikliğiyle tap'e
sararsınız. mcptap, istemcinizle sunucu arasındaki her JSON-RPC mesajını
yerel bir JSONL dosyasına kaydeder ve o trafiğin gerçekte ne yaptığını
söyler: araç yüzeyinin token fiyatı, parasını ödediğiniz ama hiç
çağırmadığınız araçlar, araç başına gecikme, taksonomili hata listesi,
oturum ortasında çöken sunucular ve modelinize emir gibi okunan araç
tanımları.

SDK yok. Hesap yok. SaaS yok. stdout temiz bir protokol kanalı olarak
kalır; telscop stderr'den konuşur ve `~/.mcptap/sessions/` altına yazar.

**English? → [README.md](README.md)**

## Açın

Tek satır. Sunucuyu doğrudan çalıştırmak yerine tap'ten geçirin:

```json
{
  "mcpServers": {
    "fetch": {
      "command": "mcptap",
      "args": ["wrap", "--", "uvx", "mcp-server-fetch"]
    }
  }
}
```

Stdio konuşan her istemciyle çalışır — Claude Desktop, Cursor, Claude
Code, MCP'yi stdio üzerinden konuşan her şey — ve her stdio sunucusuyla,
herhangi bir dilde. Tap protokolünüzü iletmek için onu çözümlemez;
yalnızca ölçmek için okur.

Kurulum (ya da kurmadan çalıştırın: `uvx --from mcptap mcptap wrap -- …`):

```console
$ pip install mcptap
```

## Ne kazanıyorsunuz?

Her oturum bir dosya. Raporu ona doğrultun:

```console
$ mcptap report ~/.mcptap/sessions/20260831-101500-uvx.jsonl
mcptap report — 20260831-101500-uvx.jsonl
  server: fake-math 9.9.9
  session: 0.32s, 8→ client msgs, 7← server msgs, init 14.9ms

tool surface: 4 tools ≈ 163 tokens (1× tools/list)
       47  send_email  ⚠ imperative description
       43  slow_mul
       38  add
       35  delete_everything
  unused (paid for, never called): delete_everything

tool calls: 5 total, 3 errors
  1× forbidden
  1× invalid_request
  1× retryable
  latency: p50 315.4ms, p95 315.5ms
  ✗ send_email [retryable] (315.4ms): retryable: upstream SMTP connection timed out after 5000ms
  ✗ boom [forbidden] (315.4ms): 401 Unauthorized: invalid API key
  ✗ lying_label [invalid_request] (315.5ms): invalid_request: connection reset by peer

prompt-injection suspects (imperative tool descriptions): send_email

lifecycle: clean exit (code=0)
```

`--json` aynı raporu makine-okur bir belge olarak verir.

### Sayılar neden önemli

Bir `tools/list` yanıtı, yüzey her tazelendiğinde modelinizin bağlamına
enjekte edilir. Gerçek sunucularla telde ölçüldü: tek başına bir GitHub
MCP sunucusu ~290.000 token değerinde yüzey duyurur (karakter/4
sezgisi); aynı yüzeyin tembel yüklenen bir varyantı: ~291. Hiç
çağırmadığınız bir araç bedava değildir — tanımına her turda kira
ödersiniz. mcptap zaten ödediğiniz faturayı gösterir.

### Hata taksonomisi

Hatalar katmanlı sınıflandırılır, en güvenilenden başlayarak:

1. **Öncü tokenler** — `retryable:` / `invalid_request:` / `forbidden:`
   ile başlayan hatalar (tap'ın varlığını bilen sunucular, mcpify ≥ 1.19)
   aynen onurlandırılır; anahtar kelimeler başka desin de.
2. **JSON-RPC hata kodları** — `-32700/-32600/-32601/-32602` →
   invalid_request, `-32603` → retryable.
3. **Anahtar kelime sezgileri** — 401/403/unauthorized → forbidden;
   timeout/reset/429/5xx → retryable; 400/404/422/invalid →
   invalid_request; gerisi dürüstçe `unknown` etiketini alır.

### Sessiz hatalar

Oturum ortasında ölen bir sunucu bunu stdout'tan duyurmaz — istemci
yalnızca cevap almayı keser. mcptap **yanıtsız istekleri** (kimliği olup
hiç yanıt alamayan istemci mesajları) işaretler, çıkış kodunu ve stdout'un
oturum bitmeden kapanıp kapanmadığını gösterir. Çökme, sessiz bir
istemcinin arkasına saklanamaz.

### Prompt enjeksiyonu şüphelileri

"you must", "always", "ignore", "before calling", "important:" ile başlayan
bir araç *tanımı*, model için açıklama değil emir cümlesidir. Zehirlenmiş
araçların çoğu böyle görünür. mcptap bunları şüpheli işaretler — bir
hijyen sinyali, asla hüküm değil.

## Diğer komutlar

**`mcptap watch`** — en yeni (ya da verilen) oturumun canlı tazelenen
raporu, artı son birkaç tel satırı. İstemcinizin yanında çalıştırın,
oturum dosyasının büyümesini izleyin:

```console
$ mcptap watch              # ~/.mcptap/sessions içindeki en yeni oturum
$ mcptap watch --once       # tek kare, döngü yok (betikler için)
```

**`mcptap diff ESKİ YENİ`** — iki oturum arasında telde ne değiştiğini
gösterir. Klasik kullanım, aynı sunucunun yükseltme öncesi/sonrası:

```console
$ mcptap diff old.jsonl new.jsonl
mcptap diff — old.jsonl → new.jsonl
  server: fake-math 9.9.9 → fake-math 9.10.0
  + search (54 tokens)
  - delete_everything (35 tokens)
  ~ add: 38 → 52 tokens (+14)
  tool surface total: 163 → 234 tokens (+71)
  ~ send_email errors: {'retryable': 1} → {'forbidden': 1}
```

**`mcptap replay OTURUM -- cmd`** — kayıtlı bir oturum, regresyon
armağanına dönüşür: istemci betiği taze bir sunucuya yeniden gönderilir ve
tel, kayıtla karşılaştırılır. Fark varsa çıkış kodu 1'dir; betiklere ve
CI'a kapı olarak takılır.

Replay'ler *tempolu* yürür: her istekten sonra yanıtını bekler, öbür
satırı öyle gönderir. Bu nezaket değil — gerçek sunucular (mcp-server-fetch
gibi anyio tabanlılar) stdin EOF'unda kuyruğunu boşaltmadan çıkıyor; tek
darbe replay onları ortada öldürüp sahte bir regresyon üretirdi. Bunu
gerçek sunucuya karşı, dürüst yolundan bulduk.

## Gerçek sunucuya karşı kanıtlandı

Test bataryası, resmî `mcp-server-fetch` çevresinde smoke testleri içerir
(kurulu değilken otomatik atlanır). Tap'ten ölçülen: `mcp-fetch 1.29.1`,
290 tokenlik yüzeyi olan tek `fetch` aracı, initialize ~1.7 s, temiz 0
çıkışı — report, diff ve replay üçü de ona karşı doğrulandı.

## Tasarım kuralları

- **Sıfır bağımlılık.** Yalnızca stdlib; `pip install mcptap` başka hiçbir
  şey getirmez. Python ≥ 3.10.
- **Byte'lar kutsal.** Protokol satırları aynen iletilir (yalnızca satır
  sonu normalleştirilir). Çözümlenemeyen satırlar ham metin olarak
  kaydedilir — bozuk bir eş, tap'i bozamaz.
- **Yerel-öncelikli.** Oturumlar `~/.mcptap/sessions/` altında kalır. Hiçbir
  şey makinenizden çıkmaz, hiçbir zaman.
- **stderr konuşur, stdout iletir.** İstemci, sunucunun gönderdiği
  protokolün aynısını görür; tap notları stderr'e gider.
- **Çıkış kodları geçer.** Sunucu kod 3 ile çökerse istemci 3 görür — tap
  camdır, yastık değil.

## Geliştirme

```console
$ pip install -e . pytest
$ python -m pytest
```

Testler gerçek uçtan uca yolu çalıştırır: `python -m mcptap wrap`, fixture
sunucuların (oturum ortasında çöken bir dahil) çevresinde; sonra hem
iletilen protokole hem kaydedilen oturuma iddia yapar.

## Yol haritası

- araç kendini hak edince halka açmak
- `mcptap doctor` — HTTP sunucuları için `mcpify doctor`'ın yaptığı gibi,
  istemci config'indeki sunucu listesini sağlamlık kontrolünden geçirmek

## Lisans

MIT
