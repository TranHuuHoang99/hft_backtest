# AI Prompt Format chuẩn cho Claude / Codex — Gen code không lan man

> Mục đích: luyện tập cho technical assessment (120', focus: edge cases, code review, debugging,
> writing clear specifications for AI coding agents).
> Phần "writing clear specifications for AI coding agents" của đề chính là test khả năng viết spec —
> và template dưới đây chính là cấu trúc spec đó. Luyện điền template này = luyện luôn phần đó của đề.

## 1. Nguyên tắc cốt lõi (vì sao format này không bị lan man)

1. **DELIVERABLE đứng trước** — nói rõ model phải trả về *cái gì* (1 hàm? 1 class? 1 file?) trước khi nói cách làm.
2. **Requirement đánh số (R1, R2…)** — model tự đối chiếu từng mục, không tự thêm tính năng.
3. **Examples > prose** — 2–4 cặp `in -> out` ghim behavior chính xác hơn cả đoạn mô tả dài.
4. **Edge cases liệt kê tường minh kèm expected** — phần bị chấm nhiều nhất; không để model tự đoán.
5. **ANTI-SCOPE** — liệt kê thứ CẤM làm. Đây là "công tắt" chống lan man mạnh nhất.
6. **Khóa format output** — "return ONE code block, no explanation" cắt sạch prose thừa.
7. **VERIFY BEFORE ANSWER** — bắt model tự chạy lại examples trong đầu trước khi trả lời.

## 2. Template đầy đủ (copy nguyên khối, điền vào `<...>`)

```text
[ROLE]
Senior engineer solving a timed technical assessment.
Optimize for correctness and clarity. No clever tricks.

[TASK]
<1 câu: input gì -> làm gì -> trả về gì>

[REQUIREMENTS]
R1. <hành vi chính>
R2. <hành vi phụ / quy tắc ưu tiên>
R3. <cách xử lý khi dữ liệu thiếu / vi phạm format>

[INPUT SPEC]
- Format: <kiểu, cấu trúc>
- Range: <giá trị min/max, kích thước tối đa>

[OUTPUT SPEC]
- Format: <kiểu trả về; nếu là string thì mô tả chính xác từng ký tự>

[EXAMPLES]
in: <...> -> out: <...>
in: <...> -> out: <...>
in: <...> -> out: <...>

[EDGE CASES]
E1. <empty / size 1>          -> expected: <...>
E2. <min/max value / overflow> -> expected: <...>
E3. <duplicates / tie>         -> expected: <...>
E4. <invalid input>            -> expected: <...>

[CONSTRAINTS]
- Language: <C++17 | Python 3.11 | ...>
- Time: O(<...>), Memory: O(<...>)
- Stdlib only. No external dependencies.

[DELIVERABLE]
- Exactly one <function/class>: `<signature>`
- Return ONE code block. No explanation, no main(), no test code.

[ANTI-SCOPE]
- Do NOT add features, logging, or error handling not listed in R1..Rn.
- Do NOT wrap in a class / refactor / add comments unless behavior is ambiguous.

[VERIFY BEFORE ANSWER]
- Trace each EXAMPLE mentally; confirm output matches exactly.
- If the spec is ambiguous, choose the standard interpretation and mark it
  with a comment `// ASSUMPTION:` — do not ask questions.
```

## 3. Template nhanh (task đơn giản)

```text
[TASK] <1 câu>
[SPEC] in: <...> -> out: <...>; edge: <1-2 edge case kèm expected>
[CONSTRAINTS] <ngôn ngữ>, O(<...>), stdlib only
[DELIVERABLE] one function `<signature>`, code block only, no explanation
```

## 4. Template cho từng focus area của đề

### 4.1 Edge cases — checklist rà trước khi gửi bất kỳ prompt nào

- [ ] Empty input / size 0, size 1
- [ ] Min/max của range (int overflow, float precision — nhất là khi so sánh `==` trên double)
- [ ] All equal / all duplicates / đã sắp xếp / sắp xếp ngược
- [ ] Negative, zero; NaN/inf nếu là số thực
- [ ] Tie-breaking: khi giá trị bằng nhau thì giữ thứ tự nào?
- [ ] Invalid format: thiếu field, sai kiểu, null
- [ ] Kích thước lớn nhất có thể (vượt complexity yêu cầu không?)

### 4.2 Code review

```text
[ROLE] Code reviewer for a timed assessment. Be strict, not exhaustive.

[CODE]
<code>

[REVIEW FOR]
1. Correctness bugs — each must include a concrete input that triggers it
2. Missed edge cases
3. Blocking readability issues only

[FORMAT]
- One finding per line: <BUG|EDGE|STYLE> <location> — <problem> — <one-line fix>
- Do NOT rewrite the code. Do NOT praise. Do NOT report minor style nits.
```

### 4.3 Debugging

```text
[SYMPTOM] <expected vs actual, 1 câu>
[REPRO]
  input:    <...>
  expected: <...>
  actual:   <...>  <stack trace / log nếu có>
[ALREADY TRIED] <...>
[CONSTRAINTS] Fix must not change <public API / behavior X>
[DELIVERABLE]
1. Root cause: <= 3 câu
2. Minimal patch (diff only) — no rewrite, no refactor
```

## 5. Ví dụ điền đầy đủ (rolling VWAP — style HFT)

```text
[ROLE]
Senior engineer solving a timed technical assessment.
Optimize for correctness and clarity.

[TASK]
Implement a rolling VWAP calculator over the last k trades of a stream.

[REQUIREMENTS]
R1. vwap = sum(price_i * qty_i) / sum(qty_i) over the last k trades seen.
R2. If sum(qty) == 0 in the window, return 0.0 — not NaN, no exception.
R3. Trades with qty == 0 still occupy a slot in the window.
R4. Fewer than k trades seen -> use all trades seen so far.

[INPUT SPEC]
- Constructor: RollingVwap(int k), 1 <= k <= 10^6
- on_trade(double price, int qty) called sequentially; price >= 0, qty >= 0

[OUTPUT SPEC]
- double vwap (raw IEEE double, no rounding)

[EXAMPLES]  (k = 3)
on_trade(10.0, 2) -> 10.0
on_trade(20.0, 2) -> 15.0
on_trade(30.0, 0) -> 15.0   (R3: qty=0 vẫn chiếm slot)
on_trade(40.0, 2) -> 30.0   (window = {(20,2),(30,0),(40,2)}: (40+0+80)/4)

[EDGE CASES]
E1. No trade yet        -> return 0.0
E2. All qty == 0        -> return 0.0  (R2)
E3. k > số trade hiện có -> dùng hết trades đã thấy (R4)

[CONSTRAINTS]
- Language: C++17
- Time O(1) per call, Memory O(k)
- Stdlib only

[DELIVERABLE]
- Exactly one class `RollingVwap` with `RollingVwap(int k)` and `double on_trade(double, int)`
- Return ONE code block. No explanation, no main(), no tests.

[ANTI-SCOPE]
- Do NOT add thread-safety, logging, or input validation beyond R1..R4.
- Do NOT round or format the output value.

[VERIFY BEFORE ANSWER]
- Trace the 4 example calls in order; confirm 10.0, 15.0, 15.0, 30.0.
- If ambiguous, choose the standard interpretation and mark `// ASSUMPTION:`.
```

## 6. Checklist 30 giây trước khi bấm gửi

- [ ] Đọc xong TASK có biết ngay phải viết hàm/class gì không?
- [ ] Có >= 2 examples?
- [ ] Có >= 3 edge cases và **mỗi cái đều ghi rõ expected**?
- [ ] DELIVERABLE đã khóa output format (1 code block, không giải thích)?
- [ ] ANTI-SCOPE có ít nhất 1 dòng "Do NOT add features..."?

## 7. Ghi chú riêng cho Claude vs Codex

- Template dùng chung cho cả hai; **giữ phần template bằng tiếng Anh** cho kết quả ổn định nhất.
- **Claude**: hay "over-deliver" (thêm giải thích, thêm test, thêm error handling) → DELIVERABLE + ANTI-SCOPE là 2 section quan trọng nhất.
- **Codex**: làm đúng literal từng chữ → spec càng quantified (số, range, format cụ thể) thì càng ít bị đoán sai.
- Nếu output vẫn lan man: reply ngắn `Reread DELIVERABLE and ANTI-SCOPE. Output again.` — thường là đủ.
