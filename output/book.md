# Algoritma dan Struktur Data

# Bab 1. Bab 1: Pengantar Struktur Data dan Analisis Kompleksitas

## Tujuan Pembelajaran

- Memahami konsep dasar struktur data dan mengapa efisiensi data penting.
- Mampu menjelaskan notasi asimtotik (O, Omega, Theta) dan bagaimana menggunakannya untuk menganalisis kompleksitas algoritma.
- Mampu menghitung kompleksitas waktu dan ruang untuk algoritma sederhana.

## Definisi Struktur Data dan Tujuan Penggunaannya

Dalam ilmu komputer, sebuah **struktur data (data structure)** adalah cara untuk mengatur dan menyimpan data agar mudah diakses, diubah, dan dikelola. Pilihan struktur data yang tepat sangat krusial dalam merancang program yang efisien. Mengapa efisiensi data penting? Karena operasi pada data yang terstruktur dengan baik akan jauh lebih cepat dan membutuhkan sumber daya (memori dan CPU) yang lebih sedikit dibandingkan dengan data yang tidak terstruktur. Efisiensi ini menjadi sangat kritis saat aplikasi menangani volume data yang besar (skalabilitas); struktur data yang tidak efisien dapat menyebabkan aplikasi menjadi lambat atau bahkan mengalami kegagalan sistem saat beban data meningkat. Contoh struktur data yang umum meliputi: array, linked list, stack, queue, tree, dan graph. Setiap struktur data memiliki karakteristik dan kelebihan serta kekurangan tersendiri, yang membuatnya lebih cocok untuk tugas-tugas tertentu. Misalnya, array sangat efisien untuk mengakses elemen berdasarkan indeksnya, tetapi kurang fleksibel dalam hal ukuran. Sebaliknya, linked list lebih fleksibel dalam hal ukuran, namun akses elemen memerlukan traversal melalui daftar tersebut. Pemilihan struktur data yang tepat harus didasarkan pada kebutuhan spesifik aplikasi yang sedang dikembangkan. Pertimbangkan operasi yang paling sering dilakukan pada data, dan pilih struktur data yang mendukung operasi tersebut secara efisien.

Selain efisiensi waktu, kita juga harus mempertimbangkan **kompleksitas ruang (space complexity)**, yaitu jumlah memori yang dibutuhkan oleh algoritma untuk berjalan seiring dengan bertambahnya ukuran input. Pemilihan struktur data berdampak langsung pada penggunaan memori. Sebagai contoh, sebuah array statis mengalokasikan blok memori kontinu yang besar di awal, sementara linked list mengalokasikan memori secara dinamis untuk setiap elemen namun membutuhkan ruang tambahan untuk menyimpan pointer ke elemen berikutnya. Memahami kompleksitas ruang membantu pengembang memilih struktur data yang tidak hanya cepat, tetapi juga hemat memori, terutama pada sistem dengan sumber daya terbatas.

## Pengantar Notasi Asimtotik

Analisis kompleksitas algoritma adalah bidang studi yang mempelajari bagaimana performa algoritma berubah seiring dengan bertambahnya ukuran input. Kita tidak peduli dengan detail implementasi spesifik, melainkan fokus pada *perilaku* algoritma dalam batas-batas tertentu. Untuk tujuan ini, kita menggunakan **notasi asimtotik (asymptotic notation)**. Notasi asimtotik memberikan cara untuk mengekspresikan pertumbuhan fungsi dalam batas-batas tertentu, terutama ketika input menjadi sangat besar. Notasi asimtotik sangat berguna dalam membandingkan efisiensi algoritma yang berbeda. Dengan membandingkan laju pertumbuhan (growth rate) dua algoritma, kita dapat memilih algoritma yang lebih baik untuk skala data tertentu tanpa harus menjalankan kode tersebut pada setiap mesin yang berbeda. Notasi asimtotik yang paling umum adalah Big O (O), Big Omega (Omega, Ω), dan Big Theta (Theta, Θ). Penggunaan notasi asimtotik memungkinkan kita untuk membuat prediksi tentang performa algoritma tanpa perlu mengetahui detail implementasi yang tepat. Ini sangat penting karena performa algoritma dapat sangat dipengaruhi oleh detail implementasi yang mungkin bervariasi antar bahasa pemrograman atau platform.

## Notasi O (Big O)

Notasi Big O (O) digunakan untuk menggambarkan batas atas (upper bound) dari kompleksitas waktu algoritma. Ia memberikan jaminan bahwa algoritma tidak akan pernah berjalan lebih lambat daripada batas yang ditentukan. Secara formal, sebuah fungsi $f(n)$ dikatakan $O(g(n))$ jika terdapat konstanta positif $c$ dan $n_0$ sedemikian sehingga $0 \le f(n) \le c \cdot g(n)$ untuk semua $n \ge n_0$. 

Big O tidak memberikan waktu eksekusi dalam detik, melainkan menggambarkan bagaimana jumlah operasi meningkat seiring bertambahnya ukuran input 'n'. Misalnya, jika sebuah algoritma adalah $O(n)$, maka waktu eksekusi tidak akan tumbuh lebih cepat dari linear; dalam banyak kasus praktis, pertumbuhan memang mendekati proporsional, tetapi tidak dijamin. Notasi Big O sangat penting untuk memilih algoritma yang lebih baik; misalnya, algoritma dengan $O(\log n)$ akan jauh lebih unggul daripada $O(n)$ saat data mencapai jutaan elemen. Contoh umum: Algoritma pencarian linear memiliki kompleksitas $O(n)$ karena dalam kasus terburuk, algoritma harus memeriksa setiap elemen dalam array. Algoritma pencarian biner memiliki kompleksitas $O(\log n)$ karena membagi data menjadi dua pada setiap langkah. Penting untuk diingat bahwa Big O memberikan batas atas, bukan waktu eksekusi absolut.

## Notasi Omega (Big Omega) dan Theta (Big Theta)

Selain Big O, terdapat notasi Big Omega (Ω) dan Big Theta (Θ) untuk analisis yang lebih presisi. Notasi Big Omega (Ω) memberikan batas bawah (lower bound). Secara formal, $f(n) = \Omega(g(n))$ jika terdapat konstanta positif $c$ dan $n_0$ sedemikian sehingga $0 \le c \cdot g(n) \le f(n)$ untuk semua $n \ge n_0$. Ini berarti algoritma akan membutuhkan *setidaknya* jumlah operasi tersebut untuk menyelesaikan tugasnya. Contohnya, jika algoritma pencarian memiliki $\Omega(1)$, berarti dalam kondisi terbaik, ia hanya butuh satu operasi.

Notasi Big Theta (Θ) digunakan ketika batas atas dan batas bawah adalah sama, memberikan deskripsi yang ketat (tight bound). Secara formal, $f(n) = \Theta(g(n))$ jika terdapat konstanta positif $c_1, c_2,$ dan $n_0$ sedemikian sehingga $0 \le c_1 \cdot g(n) \le f(n) \le c_2 \cdot g(n)$ untuk semua $n \ge n_0$. Dengan kata lain, jika sebuah algoritma adalah $O(f(n))$ dan juga $\Omega(f(n))$, maka algoritma tersebut adalah $\Theta(f(n))$. Perbedaan utamanya adalah: Big O adalah 'tidak lebih dari', Big Omega adalah 'tidak kurang dari', dan Big Theta adalah 'tepat pada laju pertumbuhan'. Ketiganya digunakan bersamaan untuk memberikan gambaran lengkap tentang performa algoritma dalam berbagai skenario.

## Analisis Kasus Terburuk, Rata-rata, dan Terbaik

Saat menganalisis kompleksitas, kita mempertimbangkan tiga skenario: kasus terbaik (best-case), kasus rata-rata (average-case), dan kasus terburuk (worst-case). Kasus terbaik terjadi saat input berada dalam kondisi paling menguntungkan (misal: target pencarian berada di indeks pertama). Kasus rata-rata adalah ekspektasi performa berdasarkan distribusi input yang umum. Kasus terburuk menggambarkan skenario paling tidak menguntungkan (misal: target berada di akhir array atau tidak ada). 

Pemilihan metrik analisis tergantung pada aplikasi yang dikembangkan. Analisis kasus terburuk sering dianggap paling krusial karena memberikan jaminan performa minimum (garansi) bahwa sistem tidak akan melampaui batas waktu tertentu. Namun, dalam beberapa situasi, kasus rata-rata lebih relevan. Sebagai contoh, algoritma Quicksort memiliki kasus terburuk $O(n^2)$, tetapi dalam praktiknya, kasus rata-ratanya adalah $O(n \log n)$, yang sangat efisien. Oleh karena itu, Quicksort tetap menjadi pilihan populer meskipun kasus terburuknya kurang ideal.

## Contoh Analisis Kompleksitas: Pencarian Linear

```python
def linear_search(arr, target):
  for i in range(len(arr)):
    if arr[i] == target:
      return i
  return -1

# Ilustrasi pertumbuhan kompleksitas
import time
for size in [100, 10000, 1000000]:
    test_arr = list(range(size))
    target = size - 1 # Kasus terburuk (elemen terakhir)
    start = time.time()
    linear_search(test_arr, target)
    end = time.time()
    print(f"Ukuran {size}: {end - start:.5f} detik")
```
Dalam pencarian linear, jumlah operasi meningkat secara linear seiring bertambahnya 'n'. Pada kode di atas, jika ukuran array meningkat 100 kali lipat, waktu eksekusi tidak akan tumbuh lebih cepat dari linear. Inilah yang dimaksud dengan $O(n)$. Namun, perlu diperjelas bahwa meskipun $O(n)$ adalah batas atas teoritis, pada array yang sangat besar, performa praktis dapat dipengaruhi oleh faktor perangkat keras seperti *cache miss* pada memori, sehingga pertumbuhan waktu mungkin tidak selalu linear sempurna di dunia nyata.

## Contoh Analisis Kompleksitas Ruang

Kompleksitas ruang diukur berdasarkan jumlah memori tambahan yang digunakan oleh algoritma relatif terhadap ukuran input. Perhatikan dua fungsi berikut:

```python
# Contoh 1: Kompleksitas Ruang O(1)
def find_sum(arr):
    total = 0  # Hanya menggunakan satu variabel tambahan
    for x in arr:
        total += x
    return total

# Contoh 2: Kompleksitas Ruang O(n)
def duplicate_list(arr):
    new_list = []  # Membuat list baru yang ukurannya sebanding dengan input
    for x in arr:
        new_list.append(x)
    return new_list
```
Pada `find_sum`, tidak peduli berapa pun ukuran `arr`, kita hanya menggunakan satu variabel `total`. Maka kompleksitas ruangnya adalah $O(1)$ (konstan). Sebaliknya, pada `duplicate_list`, jika input `arr` memiliki $n$ elemen, maka `new_list` juga akan memiliki $n$ elemen. Karena penggunaan memori tumbuh secara linear terhadap input, kompleksitas ruangnya adalah $O(n)$. Hal yang sama berlaku pada rekursi; setiap panggilan fungsi yang belum selesai akan memakan ruang di *call stack*, sehingga fungsi rekursif yang memanggil dirinya sendiri sebanyak $n$ kali biasanya memiliki kompleksitas ruang $O(n)$.

## Contoh Analisis Kompleksitas: Pencarian Biner

```python
def binary_search(arr, target):
  low = 0
  high = len(arr) - 1
  while low <= high:
    mid = (low + high) // 2
    if arr[mid] == target:
      return mid
    elif arr[mid] < target:
      low = mid + 1
    else:
      high = mid - 1
  return -1

# Ilustrasi pertumbuhan kompleksitas
import time
for size in [100, 10000, 1000000]:
    test_arr = list(range(size))
    target = size - 1 # Kasus terburuk
    start = time.time()
    binary_search(test_arr, target)
    end = time.time()
    print(f"Ukuran {size}: {end - start:.5f} detik")
```
Berbeda dengan pencarian linear, pencarian biner membagi ruang pencarian menjadi setengah pada setiap langkah. Peningkatan ukuran input dari 10.000 ke 1.000.000 (100 kali lipat) hanya menambah sedikit langkah perbandingan. Hal ini menunjukkan efisiensi $O(\log n)$, yang jauh lebih skalabel untuk data besar dibandingkan $O(n)$.

## Contoh

**Contoh 1.1**

Contoh kode pencarian linear dengan simulasi pertumbuhan waktu eksekusi.

**Contoh 1.2**

Contoh kode pencarian biner dengan simulasi pertumbuhan waktu eksekusi.

**Contoh 1.3**

Contoh perbandingan fungsi dengan kompleksitas ruang O(1) dan O(n).

## Latihan

1. Hitunglah kompleksitas waktu (Big O) dari fungsi yang melakukan iterasi bersarang (nested loop) untuk mencetak semua pasangan elemen dalam sebuah array berukuran n.
2. Diberikan dua algoritma: Algoritma A dengan kompleksitas O(n log n) dan Algoritma B dengan O(n²). Manakah yang Anda pilih jika input data mencapai 1 juta elemen? Jelaskan alasannya.
3. Analisis sebuah fungsi yang hanya mengakses elemen array pada indeks ke-0, ke-n/2, dan ke-n. Berapakah kompleksitas waktunya?
4. Jelaskan perbedaan antara kasus terbaik dan kasus terburuk pada algoritma Bubble Sort.

> **Catatan verifikasi** — klaim berikut belum memiliki bukti
> pendukung dari knowledge base dan perlu diverifikasi dosen:
>
> - Perlu diverifikasi dosen: Penjelasan mengenai dampak cache miss pada performa praktis O(n) untuk array yang sangat besar.

## Referensi Bab

- Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. Introduction to Algorithms. MIT Press.
- Sedgewick, R., & Wayne, K. Algorithms. Addison-Wesley.

---
