# Solana Pool Screener

[![CI](https://github.com/lyan88/solana-pool-screener/actions/workflows/ci.yml/badge.svg)](https://github.com/lyan88/solana-pool-screener/actions/workflows/ci.yml)

Screener pool baru Solana. Tugasnya satu: menemukan token yang
lolos filter, lalu mengirim satu alert ke Telegram. **Tidak memegang private
key dan tidak melakukan swap.**

## Filter

| Filter | Nilai default | Sumber |
|---|---|---|
| Mint authority revoked | wajib | Rugcheck |
| Freeze authority revoked | wajib | Rugcheck |
| LP burned / locked | ≥ 99% (burned diprioritaskan) | Rugcheck per pool |
| Top 10 holder | < 35% (di luar vault pool & burn, minimal 10 holder dan 5% supply) | Rugcheck |
| Market cap / FDV | $8.000 – $100.000 | GeckoTerminal |
| Likuiditas | ≥ $5.000 | GeckoTerminal |
| Umur pool | < 10 menit | GeckoTerminal |
| Unique buyer | ≥ 15 dalam 2 menit | GeckoTerminal trades |

Semua angka bisa diubah di `.env` (lihat `.env.example`).

## Cara kerja satu putaran

1. Ambil 2 halaman `new_pools` GeckoTerminal (±40 pool).
2. Saring murah: umur, likuiditas, MC, dan `buyers` m5 sebagai prefilter.
3. Untuk pool Meteora DAMM v2, cek API resmi Meteora. Pool yang diblacklist
   atau TVL-nya nyaris nol langsung ditolak (`pool_dead`) tanpa memanggil
   Rugcheck — reserve GeckoTerminal untuk pool seperti ini sering menyesatkan.
4. Untuk kandidat yang tersisa, ambil laporan Rugcheck:
   mint/freeze, top holder, dan status LP pool yang cocok. Kalau API Meteora
   melaporkan lock permanen ≥ 99%, angka itu dipakai lebih dulu.
5. Hanya kalau lolos semua, ambil daftar trade pool dan hitung **unique
   wallet** yang membeli dalam 120 detik terakhir. Harga alert diambil dari
   trade terakhir yang wajar (bukan snapshot `new_pools` yang bisa tertinggal).
6. Kalau lolos, alert masuk **antrean berprioritas** lalu dikirim sesuai
   kuota Telegram. Kandidat yang hampir lolos masuk **watchlist** dan
   dievaluasi ulang tiap 2 menit sampai umurnya 10 menit.

Deteksi berjalan tiap 2 menit lewat cron, jadi satu token bisa dievaluasi
beberapa kali ketika holder makin tersebar.

### Isi alert

- **Harga** memakai trade terakhir yang tidak ditandai `is_anomalous`; kalau
  tidak ada trade segar (≤ 180 detik), snapshot GeckoTerminal dipakai sebagai
  fallback. Formatnya desimal biasa, bukan notasi ilmiah.
- **Mint auth / Freeze auth** dirender dari laporan Rugcheck apa adanya:
  `revoked ✅`, `AKTIF ⚠️`, atau `tidak diketahui ❔`. Tidak pernah
  diasumsikan revoked tanpa data.
- **LP** diberi label `burned` kalau `lpMint` adalah alamat burn (mis.
  pump.fun), `locked` hanya kalau ada bukti lock ≥ 99%.

### Antrean alert

Alert tidak langsung dibuang kalau kuota Telegram penuh:

- disimpan di tabel `alert_queue` dengan skor prioritas
- dikirim segera setelah lolos; antrean hanya menampung saat kuota/jeda
  menghalangi, dan selalu dikirim skor tertinggi lebih dulu
- kedaluwarsa setelah 15 menit supaya tidak mengirim sinyal basi
- kalau tertunda lebih dari 30 detik, alert diberi banner bahwa umur & harga
  adalah kondisi saat deteksi
- kalau tertunda lebih dari 90 detik, harga entry tidak dipakai lagi;
  tracker memakai harga lilin saat alert benar-benar terkirim

### Watchlist

Kandidat yang ditolak karena alasan yang masih bisa membaik (top10 dekat
ambang, LP hampir terkunci, LP belum terbaca, buyer kurang sedikit, trades
gagal) dipantau ulang tanpa harus muncul lagi di `new_pools`. Batasnya
`WATCH_TTL_SEC` (10 menit) dan `WATCH_MAX_CHECKS` (5 kali).

## Pengukuran hasil

Setiap alert diukur otomatis pada +5 menit, +30 menit, +1 jam, dan +6 jam:

- **MFE** — kenaikan maksimum dari harga alert (high lilin)
- **MAE** — penurunan maksimum dari harga alert (low lilin)
- **win** — MFE ≥ +50% (`WIN_MFE_MIN=1.5`)

```bash
python3 track.py --backfill   # siapkan antrean untuk alert lama
python3 track.py --status     # lihat antrean
python3 track.py             # ukur yang sudah jatuh tempo
python3 track.py --dry-run   # ukur tanpa menulis DB
python3 report.py --dry-run  # laporan 24 jam
python3 report.py --telegram # kirim laporan
```

Cron menjalankan tracker tiap 5 menit dan laporan tiap 07:00 UTC.

> MAE dihitung dari **low lilin 1 menit**, jadi bisa lebih ekstrem daripada
> harga penutupan. Untuk token baru, wick -30% sampai -60% dalam 5 menit
> memang sering terjadi. Anggap ini ukuran risiko, bukan harga jual realistis.

## Menjalankan

```bash
git clone https://github.com/lyan88/solana-pool-screener.git
cd solana-pool-screener
cp .env.example .env      # isi token & chat id Telegram
python3 scanner.py --once --dry-run --verbose
python3 scanner.py --test-telegram
python3 scanner.py --once
```

Loop lokal:

```bash
python3 scanner.py --loop 120 --verbose
```

Uji filter tanpa jaringan:

```bash
python3 -m unittest discover -s tests -v
```

## Pasang di VPS

```bash
sudo ./deploy/install.sh
```

Skrip menyalin ke `/opt/screener2`, menjalankan tes + `doctor.py`, lalu
memasang `/etc/cron.d/screener2` (scan 2 menit, tracker 5 menit, laporan
07:00 UTC; user `screener`, `flock`).
Kalau `doctor.py` gagal, cron tidak diaktifkan.

Pemeriksaan manual:

```bash
sudo -u screener python3 /opt/screener2/doctor.py
sudo -u screener python3 /opt/screener2/scanner.py --once --dry-run --verbose
tail -f /opt/screener2/logs/scan.log
```

## Sinkronisasi GitHub

Repo: https://github.com/lyan88/solana-pool-screener

Di VPS pengembangan, `deploy/git-sync.sh` dijalankan cron tiap 15 menit:

- commit & push hanya kalau ada perubahan
- unit test wajib lolos sebelum push
- `.env`, `data/`, dan `logs/` tidak pernah ikut

Menarik update dari GitHub ke produksi (manual, setelah CI hijau):

```bash
sudo ./deploy/update.sh
```

Skrip itu melakukan `git pull --ff-only` sebagai pemilik repo, lalu
menjalankan `deploy/install.sh --force`.

## Batasan yang perlu diketahui

- **Bukan real-time.** GeckoTerminal adalah API publik, bukan stream RPC.
  Pool terbaru bisa terlambat 1–2 menit. Untuk sniper sub-detik, sumbernya
  harus RPC/WebSocket, bukan screener ini.
- **Top holder dari Rugcheck** adalah snapshot. Bisa berubah beberapa detik
  kemudian; ini filter risiko, bukan jaminan.
- **LP "locked" ≠ "burned".** Default menerima keduanya karena banyak pool
  memakai locker. Set `LP_ACCEPT_LOCKED=false` kalau hanya mau LP yang benar-
  benar dikirim ke incinerator.
- **Filter ketat = alert jarang.** Di menit ke-2, top 10 sering masih 50–90%.
  Token baru biasanya lolos setelah beberapa menit, kalau memang terdistribusi.
- **Rugcheck belum tentu mengindeks token berumur beberapa detik.** Kandidat
  seperti itu ditolak dengan alasan `data keamanan tidak tersedia` dan dicoba
  lagi pada putaran berikutnya.
- **Tidak ada jaminan profit.** Screener hanya menyaring; keputusan beli dan
  manajemen risiko tetap di tangan kamu.
- **Batas alert** default 40 pesan/jam dan 400/hari. Alert yang tidak bisa
  dikirim masuk antrean, bukan dibuang. Ubah `TELEGRAM_MAX_PER_HOUR` /
  `TELEGRAM_MAX_PER_DAY` kalau perlu.

## Struktur

```
scanner.py     orkestrasi satu putaran
sources.py     GeckoTerminal + Rugcheck + Meteora, normalisasi data
filters.py     semua aturan filter (murni, bisa diuji)
net.py         HTTP + rate limit adaptif + retry 429 (kunci antar-proses)
track.py       pelacak outcome +5m/+30m/+1j/+6j
report.py      laporan harian ke Telegram
telegram.py    format & kirim alert, batas laju
db.py          SQLite: evaluasi, alert, scan, outcome, cache
doctor.py      cek lingkungan sebelum cron
tests/         unit test filter, outcome, dan format alert
deploy/        cron + install script
```

Data disimpan di `data/screener2.db`:

- `evaluations` — kandidat yang pernah dievaluasi, alasan lolos/tolak
- `alerts` — satu baris per mint yang sudah dikirim
- `alert_queue` — antrean alert berprioritas yang belum terkirim
- `watchlist` — kandidat near-miss yang dipantau ulang
- `scans` — ringkasan tiap putaran (pool, kandidat, alert, error)
- `outcomes` — hasil per alert per horizon (MFE/MAE)
- `security_cache` — laporan Rugcheck, TTL 120 detik

## Tuning cepat

| Gejala | Ubah |
|---|---|
| Terlalu banyak alert | turunkan `TOP10_MAX_PCT`, naikkan `MIN_UNIQUE_BUYERS` |
| Tidak ada alert sama sekali | naikkan `TOP10_MAX_PCT` ke 50, atau `LP_ACCEPT_LOCKED=true` |
| Ketinggalan pool | naikkan `SCAN_PAGES`, turunkan jeda cron (risiko 429) |
| Kena 429 | naikkan `GECKO_MIN_INTERVAL` (6 → 10 detik) |
| Ingin liq lebih aman | naikkan `MIN_LIQ_USD` |

## Lisensi & disclaimer

MIT — lihat [LICENSE](LICENSE).

Proyek ini hanya alat bantu screening dan **bukan nasihat keuangan**. Tidak
ada jaminan profit, dan tidak ada jaminan token yang lolos filter bebas dari
rug. Selalu verifikasi sendiri sebelum membeli.

Setiap push/PR menjalankan unit test lewat GitHub Actions
(`.github/workflows/ci.yml`).
