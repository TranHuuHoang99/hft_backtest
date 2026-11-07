# hft_backtest

https://leetcode.com/problems/number-of-orders-in-the-backlog/


Nhóm 1: Các dạng đề CSV Parsing thực tế (Từ LeetCode Discuss)

Khi phỏng vấn Quant, người ta sẽ đưa cho bạn một chuỗi đại diện cho dòng dữ liệu chứng khoán (Tick Data) dạng CSV và yêu cầu bạn parse thủ công (không dùng thư viện).
1. Dạng đề "Airbnb & Amazon CSV Parser" (String Tokenization)
	• Mô tả: Cho một chuỗi CSV gồm các trường cách nhau bằng dấu phẩy. Khó khăn nằm ở chỗ: các trường chứa ký tự đặc biệt sẽ được bọc trong dấu ngoặc kép "". Nếu trong trường đó lại có dấu phẩy hoặc chính dấu ngoặc kép (được escape bằng ""), bạn phải parse chính xác.
	• Từ khóa tìm kiếm trên LeetCode Discuss: airbnb csv parser hoặc amazon phone csv parser.
2. Dạng đề "Meta / Apple Tech Screening: CSV Aggregation"
	• Mô tả: Đọc dữ liệu từ file CSV (ví dụ: dòng thời gian giao dịch chứng khoán), tính toán các chỉ số như Trung bình trượt (Moving Average), đếm số lệnh (Count), hoặc tính toán theo công thức toán học/tài chính, sau đó nhóm (Group By) và sắp xếp kết quả.


https://leetcode.com/problems/number-of-orders-in-the-backlog/description/
https://leetcode.com/problems/stock-price-fluctuation/description/
https://leetcode.com/problems/online-stock-span/description/
https://leetcode.com/problems/find-median-from-data-stream/description/

---

## Synthetic data generators (`tools/`)

Two generators for interview practice. Both share the same market model,
row format and dirty-row rules, so a short file is a prefix of a longer one
(given the same `--seed`, `--start`, `--dirty` and `--rows-per-day`).

### `gen_market_data.py` -- by file size

```bash
python gen_market_data.py 6B          # 6 bytes      (header only, 153 B)
python gen_market_data.py 80KB
python gen_market_data.py 100MB
python gen_market_data.py 1GB
python gen_market_data.py 1GB --sessions 10 --dirty 0.4 --out big.csv
python gen_market_data.py --verify out.csv
```

A bare number means gigabytes (`1.5` = 1.5 GB), keeping the old behaviour.
Units: `B KB MB GB TB` (decimal) and `KiB MiB GiB TiB` (binary). The default
output name is `market_ticks_<SIZE>.csv`.

### `gen_market_data_lines.py` -- by row count

```bash
python gen_market_data_lines.py 2        # 2 rows
python gen_market_data_lines.py 10
python gen_market_data_lines.py 100
python gen_market_data_lines.py 1.5M --dirty 0.7
python gen_market_data_lines.py 100 --verify out.csv
```

`k` / `M` / `B` suffixes are accepted. The file is cut at a line boundary, so
the count is exact the moment the requested number of rows is reached (the
session calendar only limits the upper bound: 30 days x `--rows-per-day`).

### The parsing rules the output is built to exercise

A CSV line is a comma-separated list of fields that may also be:

1. **Simple fields** -- `abc,def` -> `["abc", "def"]`
2. **Quoted fields** -- a field with a comma or a quote is wrapped in `"`.
   `"Cozy Apartment, Downtown"` is one field: `Cozy Apartment, Downtown`
3. **Escaped quotes** -- `""` inside a quoted field is one literal `"`.
   `"The ""Luxury"" Suite"` -> `The "Luxury" Suite`
4. **Empty fields** -- `a,,b` -> `["a", "", "b"]`, including boundary commas.

Worked examples (all appear in the generated data):

| input | output |
|---|---|
| `Standard Room,123,4.9` | `["Standard Room", "123", "4.9"]` |
| `Airbnb,"Cozy Apartment, Downtown",450` | `["Airbnb", "Cozy Apartment, Downtown", "450"]` |
| `"Amazon Echo","The ""All-New"" Version",99.99` | `["Amazon Echo", "The \"All-New\" Version", "99.99"]` |
| `,,"Empty fields test",` | `["", "", "Empty fields test", ""]` |

### What the generators emit

`--dirty P` (default `0.5`) makes roughly `P` of the rows dirty. A dirty row
keeps the 12 core columns and may:

* quote any text/numeric column, with a comma inside (`"34800,sand"`),
* embed escaped quotes (`"The ""NAVY"" 500"`),
* put an extra value *before* `ts_us` (`REQ-ANBR-8841,<ts_us>,...`) and/or
  *after* `ask_volume` (`...,GTC`),
* leave empty fields at the row boundaries (`,<ts_us>,...` or `...,,`).

The header always has 15 columns
(`ts_us ... ask_volume,buyer_ref,seller_ref,venue_note`) and clean rows are
padded with empty extras, so every line has a defined value for all 15.

`--verify` re-parses a file with a reference implementation of the rules
(`gen_dirty.parse_csv_line`, pure Python, no `csv` module) and checks every
line, timestamp ordering, and the market invariants on quote-free rows.

> **Note on dirty rows:** vendor annotations can occupy numeric columns
> (e.g. `match_volume` = `"34800,sand"`). A submission must cleanse a field
> before calling `int()` on it, which is the point of the exercise. Use
> `--dirty 0` if you want a strictly typed file.

