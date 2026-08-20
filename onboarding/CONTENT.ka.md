# CQ v3 — Georgian onboarding intake kit · CONTENT SPECIFICATION

**Status:** authoritative. **Owner:** TRACK-DESIGN. **Consumers:** the workbook builder, the guide
builder, the validator and the provisioning tool.

This file is an **internal** specification. It is written in English so engineers can read it, but
**every Georgian string in it is final customer-facing copy**. Build tracks MUST copy Georgian text
from here verbatim — character for character, including punctuation and the Georgian quotation
marks „ “ — and MUST NOT compose, translate, shorten or "improve" any Georgian string of their own.
If a string you need is missing, that is a gap in this spec: report it, do not invent it.

Every string carries a stable **ID** (e.g. `S3.ERR.TOTAL`). Use the IDs in code comments and in
validator output so a copy change here can be traced to its call sites.

---

## 0. Scope, audience, and the one thing to remember

The customer filling this workbook is a **manager at a Georgian bank / insurer / clinic / telecom /
hotel who has no technical background, no time, and no one assigned to configure software.** They
will open the file once, on a laptop, probably in Excel, probably between meetings.

Design consequences that govern every decision below:

1. **Showing beats explaining.** Every data sheet ships with a filled example row. A customer who
   reads nothing and just overwrites the example row still produces valid input.
2. **A valid default state.** The workbook as generated is already submittable: the rubric sheet
   arrives pre-filled with a working 100 % rubric, and the FAQ sheet arrives pre-filled with that
   customer's industry template. Doing nothing is a legitimate (if weak) answer.
3. **Nothing derivable is ever asked.** No keys, no slugs, no ids, no thresholds, no model names,
   no "embedding" anything. If the system can compute it, the form does not mention it.
4. **Only two sheets are mandatory**: `კომპანია` and `ხშირი კითხვები`. Everything else has a stated
   default. This is said out loud on the first sheet, because a form that looks like 400 mandatory
   cells gets abandoned.

---

## 1. Verified technical contract (measured from source — build tracks must not contradict this)

| Fact | Source | Consequence for this kit |
|---|---|---|
| Dimension = `{key, name, description, weight, guidance}`; `key` auto-derived from `name` via `_slug()` | `services/scoring.py` | Form collects **Name / Weight / Guidance** only. `description` is not injected into the prompt, so it is not collected either. |
| Weights must total 100 (±0.5 tolerance) or `save_config` raises | `services/scoring_store.py` | Live total cell + `S3.ERR.TOTAL`. |
| If **every** weight is falsy, weights are distributed evenly | `services/scoring_store.py` | The documented escape hatch `S3.NOTE.EVEN`. |
| `MAX_DIMENSIONS = 30` | `services/scoring.py` | Validation `V13`; the sheet only ships 10 rows and allows up to 50 physical rows. |
| `guidance` is injected verbatim into the scoring system prompt | `services/scoring.py::_build_system` | Guidance is the single highest-leverage field in the kit; §B teaches it with before/after. |
| Scoring judges the **operator**, not the customer | `services/scoring.py` prompt | Stated explicitly on the rubric sheet (`S3.HELP`), because customers get this backwards. |
| CSV import: row 1 = headers; **each later row = exactly one chunk**, rendered `"header: value"` joined by newlines | `services/kb_ingest.py::csv_to_chunks` | Georgian headers are embedded text → headers are chosen to read as natural Georgian labels. One row = one retrievable answer. |
| Free text chunks at `CHUNK_SIZE = 1000` chars with 150 overlap, paragraph-preferring | `services/kb_ingest.py::chunk_text` | "One row = one answer" beats a long policy essay. The 1000-char rule is taught, in customer language, without the number where possible. |
| `visibility ∈ {internal, public}`, **per document**, default `internal`; the public bot retrieves `public` only | `services/kb_console.py`, `routers/chat.py` | The form has a per-row `ხილვადობა` column, and the provisioning tool **splits rows into two documents** by that column (see §G). |
| Autopilot requires ≥1 `public` document | `routers/admin.py` | Validation `V30`. |
| Bot config defaults: `autopilot_enabled=False`, `min_score=0.35`, `min_hits=1`, `top_k=8` | `services/chat_store.py::CHAT_CONFIG_DEFAULTS` | Thresholds are **never** in the form. Collect persona / greeting / refusal / escalation / languages only. |
| `analysis_instructions` is a **global** app setting, not per tenant | `services/settings_store.py`, `routers/admin.py` | Sheet 7 cannot be auto-provisioned per tenant. It is emitted as an operator report + turned into an internal KB doc (§G). |
| `doc_type` defaults: `document` (file), `note` (paste), `faq` (CSV) | `routers/kb.py` | Provisioning uses `faq` and `document`. |
| `clients` has `industry`, `region` | `db/schema.sql` | Sheet 2 fields map straight through. |
| `tenant_users.role ∈ {member, owner}` | `db/schema.sql` | Sheet 2 user block dropdown maps to these two values. |

**Host constraint:** builder/validator scripts run on host **python 3.9.6** with **openpyxl 3.1.5**
and no other spreadsheet library. No `X | Y` runtime unions, no `match`.

---

## 2. Workbook identity, file name, global conventions

### 2.1 File name

`CommuniQ-კითხვარი-{კომპანია}-{YYYY-MM-DD}.xlsx`
Generic (no company yet): `CommuniQ-კითხვარი.xlsx`

### 2.2 Workbook version

Every workbook carries the version token **`CQ-ONB-1.0`** on the hidden `_მეტა` sheet. The validator
identifies files by it.

### 2.3 Hidden sheets (never visible to the customer; `sheet_state = "hidden"`)

**`_მეტა`** — key/value, column A key, column B value:

| A (key) | B (value) |
|---|---|
| `version` | `CQ-ONB-1.0` |
| `industry` | one of `ბანკი` / `სადაზღვევო` / `კლინიკა` / `ტელეკომი` / `სასტუმრო` / `ზოგადი` |
| `generated_at` | ISO date |
| `company` | company name if known, else empty |

**`_სიები`** — one column per dropdown source list, header in row 1. Data-validation formulas point
here (`=_სიები!$A$2:$A$12` etc.). Lists are enumerated in §3 per field.

### 2.4 Visual conventions (fixed — every sheet obeys them)

| Element | Spec |
|---|---|
| Font | Calibri 11 everywhere; sheet title Calibri 16 bold |
| Title row (row 1) | dark brand fill `FF0F172A`, white bold text, row height 30 |
| Subtitle row (row 2) | italic, grey `FF64748B`, row height 20 |
| Help block (row 3, merged across the used columns) | fill `FFEFF6FF`, wrap text, top-aligned, row height 60–90 per sheet |
| Header row (row 6) | fill `FF1E40AF`, white bold, wrap text, centred, row height 32, `AutoFilter` on, freeze panes at `A7` |
| **Example / template rows** | fill `FFFFF7CD` (warm yellow), italic text; the last column of the row carries marker `G.MARK.EXAMPLE` |
| Required-column header | header text ends with ` *` |
| Error highlight (validator output copy only) | not applied in the workbook; validation is server-side |
| All data cells | wrap text, vertical `top` |
| Sheet protection | only `ნიმუში (შევსებული)` is protected (read-only); all other sheets unprotected |

**`G.MARK.EXAMPLE`** (put in the rightmost column of every yellow row):
```
ნიმუშია — შეცვალეთ ან წაშალეთ
```

**`G.NOTE.PLACEHOLDER`** (appended to the help block of every sheet that ships template rows):
```
ნიმუშის სტრიქონებში მითითებული ვადები, თანხები და პირობები პირობითია. აუცილებლად შეცვალეთ თქვენი რეალური პირობებით.
```

**`G.NOTE.BLANK`** (help block, every sheet):
```
თუ რომელიმე პასუხი არ იცით, დატოვეთ უჯრა ცარიელი. ცარიელი უჯრა უკეთესია, ვიდრე მიახლოებითი ან მცდარი პასუხი.
```

### 2.5 Sheet order = filling order

| # | Tab name (exact) | Chars | Mandatory? | Est. time | Skipped ⇒ |
|---|---|---|---|---|---|
| 1 | `დაწყება` | 7 | read | 3 წუთი | — (orientation only) |
| 2 | `კომპანია` | 8 | **yes** | 5 წუთი | cannot provision |
| 3 | `შეფასების რუბრიკა` | 17 | no (pre-filled) | 10 წუთი | no rubric ⇒ calls are transcribed and analysed but not scored |
| 4 | `ხშირი კითხვები` | 14 | **yes** | 45–90 წუთი | no KB ⇒ no fact-check, no bot |
| 5 | `წესები და დოკუმენტები` | 21 | no | 15 წუთი | KB holds FAQ rows only |
| 6 | `ბოტის პარამეტრები` | 17 | no | 10 წუთი | bot stays off; operators still get draft suggestions |
| 7 | `ანალიზის აქცენტები` | 18 | no | 5 წუთი | standard analysis is used |
| 8 | `ნიმუში (შევსებული)` | 18 | read-only | — | — |

Rationale for putting the rubric (3) before the FAQ (4): the rubric arrives pre-filled and can be
finished in ten minutes or simply kept as-is. That is a fast win before the long sheet.

---

## 3. Sheet-by-sheet specification

### SHEET 1 — `დაწყება`

Not a data sheet. Column A width `4`, column B width `96`, column C width `18`, column D width `20`.
No dropdowns, no validation. All text blocks are merged across `B:D` and wrapped.

**`S1.TITLE`** (B1, Calibri 20 bold, dark fill):
```
CommuniQ — დანერგვის კითხვარი
```

**`S1.SUB`** (B2):
```
შეავსეთ ეს ფაილი და დაგვიბრუნეთ ელფოსტით. დანარჩენს ჩვენ გავაკეთებთ.
```

**`S1.H1`** (B4, bold): `რა არის ეს ფაილი`

**`S1.P1`** (B5):
```
ეს არის ერთადერთი ფაილი, რომელიც CommuniQ-ს სჭირდება თქვენი სისტემის ასაწყობად. თქვენ წერთ იმას, რაც ისედაც იცით — თქვენს წესებს, თქვენს ხშირ კითხვებს და იმას, რაც კარგ ზარს კარგად აქცევს. ტექნიკური ნაწილი ჩვენი საქმეა: არაფერი დაგჭირდებათ პროგრამის პარამეტრებიდან და არსად არ მოგიწევთ შესვლა.
```

**`S1.H2`** (B7, bold): `რამდენი დრო დასჭირდება`

**`S1.P2`** (B8):
```
სულ დაახლოებით ორი საათი, ერთი ადამიანისთვის. სავალდებულოა მხოლოდ ორი ფურცელი — „კომპანია“ და „ხშირი კითხვები“. დანარჩენი შეგიძლიათ საერთოდ გამოტოვოთ; მაშინ ჩვენ სტანდარტულ პარამეტრებს გამოვიყენებთ და მოგვიანებით ერთად შევცვლით.
```

**`S1.TABLE`** — header row (B10:D10, bold, light fill), then eight rows B11:D18:

| `ფურცელი` | `დრო` | `სავალდებულოა?` |
|---|---|---|
| `1. კომპანია` | `5 წუთი` | `დიახ` |
| `2. შეფასების რუბრიკა` | `10 წუთი` | `არა — უკვე შევსებულია; შეგიძლიათ ასე დატოვოთ` |
| `3. ხშირი კითხვები` | `45–90 წუთი` | `დიახ — ეს ყველაზე მნიშვნელოვანი ფურცელია` |
| `4. წესები და დოკუმენტები` | `15 წუთი` | `არა` |
| `5. ბოტის პარამეტრები` | `10 წუთი` | `არა — ბოტი ნაგულისხმევად გამორთულია` |
| `6. ანალიზის აქცენტები` | `5 წუთი` | `არა` |
| `7. ნიმუში (შევსებული)` | `წასაკითხი` | `არა — სრულად შევსებული მაგალითი` |

**`S1.H3`** (B20, bold): `ხუთი წესი, რომელიც ყველაფერს ამარტივებს`

**`S1.RULES`** (B21:B25, one per row):
```
1. ერთი სტრიქონი — ერთი კითხვა და ერთი სრული პასუხი. ნუ ჩაწერთ ერთ უჯრაში მთელ დებულებას; დაყავით ცალკე კითხვებად.
```
```
2. დაწერეთ ისე, როგორც კლიენტი კითხულობს, და არა ისე, როგორც შიდა დოკუმენტშია ჩამოყალიბებული.
```
```
3. ყვითელი სტრიქონები ნიმუშია. შეცვალეთ თქვენი რეალური ინფორმაციით ან წაშალეთ — ისე დატოვება არ შეიძლება.
```
```
4. სვეტი „ხილვადობა“ ყველაზე ფრთხილად შეავსეთ: „საჯარო“ ნიშნავს, რომ ამ ტექსტს კლიენტი დაინახავს. ყველაფერი, რაც მხოლოდ თანამშრომლისთვისაა, უნდა იყოს „შიდა“.
```
```
5. თუ პასუხი არ იცით, დატოვეთ ცარიელი. ცარიელი უჯრა უკეთესია, ვიდრე მცდარი პასუხი — მცდარ პასუხს სისტემა კლიენტს გაუმეორებს.
```

**`S1.H4`** (B27, bold): `რა მოხდება შემდეგ`

**`S1.P4`** (B28):
```
დაგვიბრუნეთ ფაილი ელფოსტით. ჩვენ შევამოწმებთ და ერთ სამუშაო დღეში დაგიბრუნებთ შენიშვნებს, თუ რამე დასაზუსტებელი აღმოჩნდა. შემდეგ ჩავრთავთ თქვენს სისტემას და გამოგიგზავნით პორტალის მისამართს იმ ადამიანებისთვის, რომლებსაც ფურცელ „კომპანია“-ზე მიუთითებთ. პაროლს თითოეული მათგანი პირველივე შესვლისას თავად დააყენებს.
```

**`S1.WARN.PASSWORD`** (B29, bold, red text `FFB91C1C`):
```
პაროლი ამ ფაილში არასოდეს დაწეროთ. არც ბარათის ნომერი, არც პირადი ნომრები, არც კლიენტების სია.
```

**`S1.H5`** (B31, bold): `კონტაქტი`

**`S1.P5`** (B32) — the builder substitutes the three placeholders from CLI arguments; if not
supplied it leaves the literal placeholder text so the owner sees what to fill in:
```
კითხვის შემთხვევაში მოგვწერეთ ან დაგვირეკეთ — შევსებას ერთად გავივლით: {{CONTACT_NAME}} · {{CONTACT_EMAIL}} · {{CONTACT_PHONE}}
```

**`S1.P6`** (B34, grey, small):
```
ფაილის ვერსია: CQ-ONB-1.0
```

---

### SHEET 2 — `კომპანია`  *(mandatory)*

Two blocks. Column A width `38` (labels), column B width `52` (answers), column C width `46` (hint),
column D width `16`.

Rows 1–3 use the standard title / subtitle / help block (merged `A:D`).

**`S2.TITLE`** (A1): `კომპანია`
**`S2.SUB`** (A2): `ვის ვაწყობთ სისტემას და ვის სჭირდება წვდომა`
**`S2.HELP`** (A3):
```
შეავსეთ ორივე ბლოკი. პირველი ბლოკი გვეუბნება, ვინ ხართ და როგორ მუშაობთ; მეორე ბლოკი — ვის უნდა შევუქმნათ პორტალზე შესვლა. ვარსკვლავით (*) მონიშნული ველები აუცილებელია. თუ რომელიმე პასუხი არ იცით, დატოვეთ უჯრა ცარიელი.
```

#### Block A — profile (labels in A5:A17, answers in B5:B17, hints in C5:C17)

| Row | `A` label (ID) | Required | Validation | `C` hint |
|---|---|---|---|---|
| 5 | `კომპანიის სახელი *` (`S2.F.NAME`) | yes | free text | `ისე დაწერეთ, როგორც ოფიციალურ დოკუმენტებშია.` |
| 6 | `ინდუსტრია *` (`S2.F.INDUSTRY`) | yes | dropdown `L.INDUSTRY` | `აირჩიეთ ყველაზე ახლო ვარიანტი.` |
| 7 | `ქვეყანა / რეგიონი` (`S2.F.REGION`) | no | free text, default `საქართველო` | `თუ რამდენიმე ქვეყანაშია, ჩამოწერეთ მძიმით.` |
| 8 | `ძირითადი ენა, რომელზეც ზარები მიმდინარეობს *` (`S2.F.LANG`) | yes | dropdown `L.LANG` | `ის ენა, რომელზეც ზარების უმეტესობაა.` |
| 9 | `დამატებითი ენები` (`S2.F.LANG2`) | no | free text | `მაგალითად: რუსული, ინგლისური.` |
| 10 | `მომსახურების არხები` (`S2.F.CHANNELS`) | no | free text | `მაგალითად: ტელეფონი, ვებ-ჩატი, Facebook, ელფოსტა.` |
| 11 | `ოპერატორების რაოდენობა` (`S2.F.AGENTS`) | no | whole number 1–10000 | `დაახლოებით.` |
| 12 | `დღეში ზარების დაახლოებითი რაოდენობა` (`S2.F.CALLS`) | no | whole number 1–1000000 | `დაახლოებით.` |
| 13 | `სამუშაო საათები` (`S2.F.HOURS`) | no | free text | `მაგალითად: ორშ.–პარ. 09:00–18:00, შაბათი 10:00–15:00.` |
| 14 | `საკონტაქტო პირი — სახელი და გვარი *` (`S2.F.CONTACT`) | yes | free text | `ვისაც დანერგვის საკითხებზე მივმართავთ.` |
| 15 | `საკონტაქტო ელფოსტა *` (`S2.F.EMAIL`) | yes | free text, email shape | `ამ მისამართზე გამოგიგზავნით შესვლის ინსტრუქციას.` |
| 16 | `საკონტაქტო ტელეფონი` (`S2.F.PHONE`) | no | free text | — |
| 17 | `როდის გინდათ დაწყება` (`S2.F.START`) | no | free text | `მაგალითად: ამ თვის ბოლომდე.` |

`L.INDUSTRY` (dropdown list, `_სიები` column A):
```
ბანკი
სადაზღვევო
კლინიკა ან საავადმყოფო
ტელეკომი და ინტერნეტი
სასტუმრო და მომსახურება
ვაჭრობა და ონლაინ მაღაზია
ლიზინგი ან მიკროსაფინანსო
სახელმწიფო ან საზოგადოებრივი სერვისი
სხვა
```

`L.LANG` (`_სიები` column B):
```
ქართული
რუსული
ინგლისური
შერეული
```

#### Block B — portal users (header row 20, data rows 21–30)

**`S2.H.USERS`** (A19, bold): `ვის სჭირდება პორტალზე შესვლა`
**`S2.HELP.USERS`** (A20 merged `A:D`, light fill, row height 40):
```
ჩამოწერეთ ის ადამიანები, რომლებმაც უნდა შეძლონ სისტემაში შესვლა — ზარების ატვირთვა, შედეგების ნახვა და ცოდნის ბაზის რედაქტირება. პაროლს თითოეული თავად დააყენებს პირველი შესვლისას; აქ პაროლი არ წეროთ.
```

Header row 21 (`S2.TH.*`), data rows 22–31:

| Col | Header | Width | Required | Validation |
|---|---|---|---|---|
| A | `სახელი და გვარი *` | 30 | yes (if row used) | free text |
| B | `ელფოსტა *` | 34 | yes (if row used) | email shape |
| C | `როლი *` | 22 | yes (if row used) | dropdown `L.ROLE` |
| D | `ჩანაწერი` | 26 | no | free text |

`L.ROLE` (`_სიები` column C) — maps to `tenant_users.role`:
```
მფლობელი — ყველა უფლება
წევრი — ჩვეულებრივი წვდომა
```
Mapping: `მფლობელი — ყველა უფლება` → `owner`; `წევრი — ჩვეულებრივი წვდომა` → `member`.

Example row 22 (yellow, `G.MARK.EXAMPLE` in D):
```
A: ნინო ბერიძე
B: n.beridze@example.ge
C: მფლობელი — ყველა უფლება
D: ხარისხის სამსახურის ხელმძღვანელი
```

---

### SHEET 3 — `შეფასების რუბრიკა`  *(optional — ships valid and pre-filled)*

Columns:

| Col | Header (`S3.TH.*`) | Width | Validation |
|---|---|---|---|
| A | `გამოვიყენოთ? *` | 15 | dropdown `L.YESNO`, default `დიახ` |
| B | `განზომილება *` | 34 | free text |
| C | `წონა (%) *` | 12 | decimal 0–100 |
| D | `შეფასების მითითება — რა იძლევა მაღალ და რა დაბალ ქულას *` | 104 | free text, wrap |
| E | `ჩანაწერი` | 22 | free text (ignored by provisioning) |

`L.YESNO` (`_სიები` column D):
```
დიახ
არა
```

> **Deliberate addition — read before implementing.** Column A is a *control* column, not a data
> field: the customer never types a key, id or threshold. It exists because this sheet arrives
> pre-filled with a menu of ten dimensions, and telling a non-technical user to "delete the rows you
> don't want" produces broken files (deleted headers, orphaned weights). Toggling `არა` is safe and
> reversible. **The validator and provisioner consider only rows with `დიახ`.** Column E is never
> imported anywhere.

Rows 1–4 layout:

**`S3.TITLE`** (A1): `შეფასების რუბრიკა`
**`S3.SUB`** (A2): `რის მიხედვით უნდა შეფასდეს ოპერატორის მუშაობა`
**`S3.HELP`** (A3, merged `A:E`, row height 96):
```
ეს ფურცელი უკვე შევსებულია რვა განზომილებით, რომლებიც ქართული ქოლ-ცენტრების უმეტესობას უხდება. თუ დროა ცოტა, არაფერი შეცვალოთ — ასეც მუშაობს. თუ გინდათ მორგება: შეცვალეთ წონები, გადააკეთეთ მითითების ტექსტი თქვენი წესებით, ან სვეტში „გამოვიყენოთ?“ დააყენეთ „არა“ იმ განზომილებაზე, რომელიც თქვენთვის არ არის მნიშვნელოვანი. ორი განზომილება თავიდანვე გამორთულია — ჩართეთ, თუ გჭირდებათ.

მნიშვნელოვანია: ფასდება ოპერატორი და არა კლიენტი. თუნდაც კლიენტი უხეშად ლაპარაკობდეს, ქულას იღებს ის, თუ როგორ იმუშავა თქვენმა თანამშრომელმა.
```

**`S3.TOTAL.LABEL`** (A4): `სულ (უნდა იყოს 100)`
**`S3.TOTAL.CELL`** (C4) — live formula over the `დიახ` rows:
```
=SUMIF(A7:A56;"დიახ";C7:C56)
```
> Builder note: openpyxl writes the formula string as-is. Use the semicolon form above **only** if
> the file is generated for a locale that requires it — write the **comma** form
> `=SUMIF(A7:A56,"დიახ",C7:C56)` into the file. Excel re-renders separators per locale on open.

Conditional formatting on `C4`: green fill `FFDCFCE7` + green bold text when `=C4=100`; red fill
`FFFEE2E2` + red bold text otherwise. Add cell comment/note **`S3.TOTAL.NOTE`**:
```
ეს რიცხვი თავისით ითვლება. სანამ 100 არ გახდება, რუბრიკას ვერ ჩავრთავთ.
```

**`S3.NOTE.EVEN`** (A5, merged `A:E`, italic, light-blue fill, row height 32) — the escape hatch,
stated exactly as the code behaves:
```
ვერ გადაწყვიტეთ, რომელი რამდენად მნიშვნელოვანია? წაშალეთ ყველა წონა და დატოვეთ სვეტი „წონა (%)“ სრულიად ცარიელი — მაშინ ჩვენ ყველა განზომილებას თანაბარ წონას მივანიჭებთ. მთავარია, ან ყველა უჯრა იყოს შევსებული და ჯამი 100, ან ყველა იყოს ცარიელი. შუალედური ვარიანტი არ მუშაობს.
```

Header row 6. Data rows 7–56 (50 physical rows; `MAX_DIMENSIONS = 30` is enforced by the validator,
not by the sheet). Freeze panes `A7`. Rows 7–16 ship pre-filled (see §B) — these are **not** yellow
example rows; they are a real, keepable menu, so they use normal white fill. Only the two disabled
rows (15–16) get grey text `FF94A3B8`.

**`S3.ERR.TOTAL`** — the exact error copy used by the validator (and by the guide) when the total
is neither 100 nor all-blank:
```
ფურცელზე „შეფასების რუბრიკა“ წონების ჯამია {total}, უნდა იყოს ზუსტად 100. შეცვალეთ რომელიმე წონა ისე, რომ ჯამმა 100 შეადგინოს — ან წაშალეთ ყველა წონა და ჩვენ თანაბრად გავანაწილებთ.
```

---

### SHEET 4 — `ხშირი კითხვები`  *(mandatory — the big one)*

Columns:

| Col | Header (`S4.TH.*`) | Width | Required | Validation |
|---|---|---|---|---|
| A | `კითხვა *` | 46 | yes | free text |
| B | `პასუხი *` | 78 | yes | free text |
| C | `კატეგორია` | 20 | no | dropdown `L.CAT.{industry}`, **error style = information**, `allow_blank=True` |
| D | `ტეგები (მძიმით)` | 34 | no | free text |
| E | `ხილვადობა *` | 16 | yes | dropdown `L.VIS`, `allow_blank=False` |
| F | `შენიშვნა CommuniQ-სთვის` | 26 | no | free text (never imported) |

`L.VIS` (`_სიები` column E):
```
საჯარო
შიდა
```
Mapping: `საჯარო` → `public`; `შიდა` → `internal`.

`L.CAT.{industry}` — per-industry category lists live in `_სიები` columns F.. and are exactly the
`კატეგორია` values used by that industry's template rows in §C. The dropdown uses
`errorStyle="information"` so a customer may type their own category without being blocked; the
prompt text is **`S4.CAT.PROMPT`**:
```
აირჩიეთ სიიდან ან თავად დაწერეთ თქვენი კატეგორია.
```

**`S4.TITLE`** (A1): `ხშირი კითხვები`
**`S4.SUB`** (A2): `ის კითხვები, რომლებსაც კლიენტები ყოველდღე გისვამენ — და თქვენი ნამდვილი პასუხები`
**`S4.HELP`** (A3, merged `A:F`, row height 108):
```
ეს ფურცელი ყველაზე მნიშვნელოვანია. სისტემა სწორედ აქედან იგებს, რა არის თქვენთან სწორი პასუხი: ამით მოწმდება, ოპერატორმა სწორი ინფორმაცია თქვა თუ არა, და ამითვე პასუხობს ბოტი.

ერთი სტრიქონი — ერთი კითხვა და ერთი სრული პასუხი. ისე დაწერეთ პასუხი, რომ ცალკე წაკითხვისასაც სრულად გასაგები იყოს: არ დაწეროთ „იხილეთ ზემოთ“ ან „როგორც უკვე აღვნიშნეთ“.

დაიწყეთ იმ 20 კითხვით, რომელსაც ოპერატორები ყველაზე ხშირად პასუხობენ. კარგია 40–80 სტრიქონი; 20-ზე ნაკლებით სისტემა სუსტად იმუშავებს.

სვეტი „ხილვადობა“: „საჯარო“ ნიშნავს, რომ ამ პასუხს ბოტმა შეიძლება პირდაპირ ათქვას კლიენტთან. „შიდა“ ნიშნავს, რომ ტექსტი მხოლოდ თანამშრომლებისთვისაა და კლიენტს არასოდეს ეჩვენება.
```
(Append `G.NOTE.PLACEHOLDER` and `G.NOTE.BLANK` as the last two lines of the same block.)

Header row 6, freeze `A7`. Rows 7 … 7+N−1 ship the industry template from §C (yellow fill, italic,
`G.MARK.EXAMPLE` in column F). Empty validated rows continue to row 306 (300 rows total).

**`S4.WARN.INTERNAL`** — cell note attached to the `ხილვადობა` header (E6):
```
თუ ეჭვი გეპარებათ, დააყენეთ „შიდა“. შიდა ჩანაწერს კლიენტი ვერასოდეს ნახავს; საჯაროს კი — შესაძლოა დღესვე.
```

---

### SHEET 5 — `წესები და დოკუმენტები`  *(optional)*

Two blocks.

#### Block A — long texts (header row 6, data rows 7–56)

| Col | Header (`S5.TH.*`) | Width | Required | Validation |
|---|---|---|---|---|
| A | `სათაური *` | 34 | yes | free text |
| B | `ტექსტი *` | 96 | yes | free text |
| C | `კატეგორია` | 20 | no | dropdown `L.DOCCAT`, information style |
| D | `ტეგები (მძიმით)` | 28 | no | free text |
| E | `ხილვადობა *` | 16 | yes | dropdown `L.VIS` |
| F | `შენიშვნა CommuniQ-სთვის` | 24 | no | free text |

`L.DOCCAT` (`_სიები` column G):
```
წესები და პირობები
ტარიფები
პროცედურა
სამართლებრივი
სასაუბრო სცენარი
სხვა
```

**`S5.TITLE`** (A1): `წესები და დოკუმენტები`
**`S5.SUB`** (A2): `გრძელი ტექსტები, რომლებიც მთლიანად უნდა შენარჩუნდეს`
**`S5.HELP`** (A3, merged `A:F`, row height 96):
```
ეს ფურცელი არასავალდებულოა. თუ თქვენი ინფორმაცია კითხვა-პასუხად იშლება, ჩაწერეთ ფურცელზე „ხშირი კითხვები“ — იქიდან სისტემა ბევრად უკეთ პოულობს პასუხს.

აქ დაწერეთ მხოლოდ ის, რაც მთლიან ტექსტად უნდა დარჩეს: მაგალითად, ხელშეკრულების პირობები ან სამართლებრივი ფორმულირება.

ერთი უჯრა დაახლოებით ერთი გვერდის ტოლი უნდა იყოს. თუ ტექსტი უფრო გრძელია, სისტემა თავად დაყოფს ნაწილებად და დაყოფის ადგილი შეიძლება წინადადების შუაში მოხვდეს. ამის ნაცვლად თავად დაყავით — თითო თავი ან თითო პუნქტი ცალკე სტრიქონად, თავისი სათაურით.
```

Example row 7 (yellow) — see §C.6.

#### Block B — files you already have (header row 59, data rows 60–74)

**`S5.H.FILES`** (A58, bold): `უკვე არსებული ფაილები`
**`S5.HELP.FILES`** (A59 merged `A:D`, row height 44):
```
თუ გაქვთ მზა ფაილები — შიდა ინსტრუქცია, ტარიფების ცხრილი, ხშირი კითხვების დოკუმენტი — არ გადმოწეროთ ხელით. ჩამოწერეთ აქ და ფაილები იმავე წერილს დაურთეთ. ვიღებთ PDF, DOCX, TXT და MD ფორმატებს.
```

Header row 60, data rows 61–75:

| Col | Header | Width | Validation |
|---|---|---|---|
| A | `ფაილის სახელი *` | 40 | free text |
| B | `რას შეიცავს *` | 56 | free text |
| C | `ხილვადობა *` | 16 | dropdown `L.VIS` |
| D | `ჩანაწერი` | 24 | free text |

Example row 61 (yellow):
```
A: ტარიფები-2026.pdf
B: ყველა პაკეტის ფასი და მომსახურების საკომისიოები
C: შიდა
D: ყოველ კვარტალში ახლდება
```

---

### SHEET 6 — `ბოტის პარამეტრები`  *(optional — bot is off by default)*

Key/value layout. Column A width `44` (label), B width `62` (answer), C width `52` (hint).

**`S6.TITLE`** (A1): `ბოტის პარამეტრები`
**`S6.SUB`** (A2): `როგორ უნდა ესაუბროს ავტომატური ასისტენტი თქვენს კლიენტებს`
**`S6.HELP`** (A3, merged `A:C`, row height 86):
```
ეს ფურცელი არასავალდებულოა. თუ საერთოდ არ შეავსებთ, ბოტი კლიენტებს არ დაელაპარაკება — სამაგიეროდ ის ოპერატორს შესთავაზებს პასუხის მონახაზს, რომელსაც ადამიანი გადახედავს და გაგზავნის. ეს არის უსაფრთხო საწყისი მდგომარეობა და ჩვენ სწორედ ამით გირჩევთ დაწყებას.

ბოტი მხოლოდ იმას იმეორებს, რაც ფურცელზე „ხშირი კითხვები“ მონიშნეთ როგორც „საჯარო“. თუ პასუხს ვერ პოულობს, ის არაფერს იგონებს — ამბობს იმ ტექსტს, რომელსაც ქვემოთ დაწერთ, და საუბარს ადამიანს გადასცემს.
```

Fields (label in A, answer in B, hint in C):

| Row | Label (ID) | Validation | Hint (C) | Default if blank |
|---|---|---|---|---|
| 5 | `გვინდა, რომ ბოტი თავად პასუხობდეს კლიენტებს?` (`S6.F.AUTO`) | dropdown `L.AUTO` | `გირჩევთ დაიწყოთ „ჯერ არა“-ით და ჩართოთ მას შემდეგ, რაც ნახავთ ბოტის პასუხების ხარისხს.` | `არა` (`autopilot_enabled=False`) |
| 6 | `პერსონა — ვინ არის ბოტი და როგორ ლაპარაკობს` (`S6.F.PERSONA`) | free text | `2–3 წინადადება. დაწერეთ ისე, როგორც ახალ თანამშრომელს აუხსნიდით.` | სისტემური ნეიტრალური პერსონა |
| 7 | `მისალმება — ქართულად` (`S6.F.GREET.KA`) | free text | `პირველი წინადადება, რომელსაც კლიენტი დაინახავს.` | — |
| 8 | `მისალმება — ინგლისურად` (`S6.F.GREET.EN`) | free text | `შეავსეთ მხოლოდ თუ ინგლისურადაც პასუხობთ.` | — |
| 9 | `მისალმება — რუსულად` (`S6.F.GREET.RU`) | free text | `შეავსეთ მხოლოდ თუ რუსულადაც პასუხობთ.` | — |
| 10 | `უარის ტექსტი — ქართულად` (`S6.F.REF.KA`) | free text | `ამას ამბობს ბოტი, როცა პასუხი ცოდნის ბაზაში არ არის. ეს არის ის წინადადება, რომელსაც კლიენტი ყველაზე ხშირად ხედავს — ის ადამიანს უნდა სთავაზობდეს, ორჯერ ბოდიშს კი არ იხდიდეს.` | — |
| 11 | `უარის ტექსტი — ინგლისურად` (`S6.F.REF.EN`) | free text | — | — |
| 12 | `უარის ტექსტი — რუსულად` (`S6.F.REF.RU`) | free text | — | — |
| 13 | `პასუხობს ქართულად?` (`S6.F.L.KA`) | dropdown `L.YESNO` | — | `დიახ` |
| 14 | `პასუხობს ინგლისურად?` (`S6.F.L.EN`) | dropdown `L.YESNO` | — | `დიახ` |
| 15 | `პასუხობს რუსულად?` (`S6.F.L.RU`) | dropdown `L.YESNO` | — | `დიახ` |
| 16 | `ესკალაციის საკვანძო სიტყვები (მძიმით)` (`S6.F.ESC.KW`) | free text | `როცა კლიენტის შეტყობინებაში რომელიმე ეს სიტყვა გამოჩნდება, საუბარი მაშინვე ადამიანს გადაეცემა, პასუხის დაწერამდე.` | სისტემური სია |
| 17 | `როდის უნდა გადასცეს ბოტმა საუბარი ადამიანს` (`S6.F.ESC.RULE`) | free text | `თავისუფლად აღწერეთ, თქვენი სიტყვებით.` | — |

`L.AUTO` (`_სიები` column H):
```
დიახ — ბოტი თავად პასუხობს
არა — მხოლოდ მონახაზი ოპერატორისთვის
ჯერ არა — მოგვიანებით გადავწყვეტთ
```
Mapping: first value → `autopilot_enabled=True`; the other two → `False`.

Example values shipped in the yellow "ნიმუში" column D (width 62), one per field row:

**`S6.EX.PERSONA`**
```
თქვენ ხართ „ალფა ბანკის“ მხარდაჭერის ასისტენტი. ისაუბრეთ თავაზიანად და მოკლედ, თქვენობით. მიეცით კონკრეტული პასუხი და, თუ საკითხი ანგარიშს ან თანხას ეხება, შესთავაზეთ ოპერატორთან დაკავშირება.
```
**`S6.EX.GREET.KA`**
```
გამარჯობა! მე ვარ „ალფა ბანკის“ ციფრული ასისტენტი. როგორ დაგეხმაროთ?
```
**`S6.EX.GREET.EN`**
```
Hello! I am the Alpha Bank digital assistant. How can I help you today?
```
**`S6.EX.REF.KA`**
```
ამ კითხვაზე ზუსტი პასუხი ჩემთან არ არის და გამოცნობა არ მინდა. ახლავე დაგაკავშირებთ ოპერატორს, რომელიც დაგეხმარებათ.
```
**`S6.EX.REF.EN`**
```
I do not have a confirmed answer to that, and I would rather not guess. Let me connect you with a colleague who can help.
```
**`S6.EX.ESC.KW`**
```
ადვოკატი, სასამართლო, საჩივარი, თანხის დაბრუნება, თაღლითობა, მედია, ჟურნალისტი
```
**`S6.EX.ESC.RULE`**
```
გადაეცით ადამიანს, როცა კლიენტი უკმაყოფილოა, როცა საქმე ეხება უკვე ჩამოჭრილ თანხას, ან როცა ერთი და იგივე კითხვა მესამედ მეორდება.
```

#### Canned replies mini-block (header row 20, data rows 21–25)

**`S6.H.CANNED`** (A19, bold): `მზა პასუხები`
**`S6.HELP.CANNED`** (A20 merged `A:C`, row height 40):
```
თუ გაქვთ ფრაზები, რომლებიც ყოველთვის სიტყვასიტყვით უნდა ითქვას — მაგალითად, ზარის ჩაწერის შესახებ გაფრთხილება ან სამუშაო საათები — ჩაწერეთ აქ. ბოტი მათ უცვლელად გამოიყენებს.
```
Header row 21: `A: სიტუაცია *` (width 32) · `B: ზუსტი ტექსტი *` (width 72) · `C: ჩანაწერი` (width 24).
Example row 22 (yellow):
```
A: სამუშაო საათები
B: ჩვენ ვმუშაობთ ორშაბათიდან პარასკევის ჩათვლით, 09:00-დან 18:00 საათამდე. შაბათს — 10:00-დან 15:00 საათამდე.
C: —
```

**`S6.NOTE.NOTHRESHOLD`** (A27, merged `A:C`, grey italic) — shown so the customer does not go
looking for knobs that are deliberately absent:
```
ბოტის ტექნიკურ პარამეტრებს — რამდენად მკაცრად ეძებოს პასუხი, რამდენ წყაროს დაეყრდნოს — ჩვენ თვითონ ვაწყობთ და მუშაობის პროცესში ვასწორებთ. თქვენ ამაზე ფიქრი არ დაგჭირდებათ.
```

---

### SHEET 7 — `ანალიზის აქცენტები`  *(optional)*

Column A width `44`, B width `78`, C width `44`.

**`S7.TITLE`** (A1): `ანალიზის აქცენტები`
**`S7.SUB`** (A2): `რას უნდა მიაქციოს სისტემამ განსაკუთრებული ყურადღება ზარების ანალიზისას`
**`S7.HELP`** (A3, merged `A:C`, row height 64):
```
ეს ფურცელი არასავალდებულოა. თუ არ შეავსებთ, სისტემა ყველა ზარს სტანდარტულად გააანალიზებს: შეაჯამებს, განსაზღვრავს განწყობას, გამოყოფს თემებს და სამოქმედო პუნქტებს. აქ იმას წერთ, რაც სწორედ თქვენთვისაა მნიშვნელოვანი და რაც შეჯამებაში აუცილებლად უნდა აისახოს.
```

| Row | Label (ID) | Validation | Hint |
|---|---|---|---|
| 5 | `რა არის თქვენთვის ყველაზე მნიშვნელოვანი ზარების ანალიზში` (`S7.F.MAIN`) | free text (long) | `3–5 წინადადება, თავისუფლად.` |

**`S7.EX.MAIN`** (yellow example in C5):
```
ჩვენთვის მთავარია, დროულად დავინახოთ უკმაყოფილო კლიენტი და შევამოწმოთ, ოპერატორმა სწორი ტარიფი და ვადა დაასახელა თუ არა. განსაკუთრებით გვაინტერესებს ზარები, სადაც კლიენტი ხელშეკრულების გაუქმებას ახსენებს.
```

Three lists follow, side by side, header row 8, data rows 9–23 (15 rows each):

| Col | Header (`S7.TH.*`) | Width |
|---|---|---|
| A | `თემები, რომლებსაც განსაკუთრებული ყურადღება სჭირდება` | 44 |
| B | `ფრაზები, რომლებიც ოპერატორმა აუცილებლად უნდა თქვას` | 60 |
| C | `ფრაზები, რომლებიც ოპერატორმა არ უნდა თქვას` | 60 |

**`S7.HELP.LISTS`** (A7, merged `A:C`, row height 44):
```
თითო სტრიქონში თითო ჩანაწერი. შუა სვეტი განსაკუთრებით სასარგებლოა: ის ფრაზები, რომლებიც კანონით ან შიდა წესით სავალდებულოა, ავტომატურად შემოწმდება ყოველ ზარზე.
```

Yellow example row 9:
```
A: ხელშეკრულების გაუქმება
B: ზარი იწერება ხარისხის კონტროლის მიზნით.
C: ეს ჩვენი პრობლემა არ არის.
```
Yellow example row 10:
```
A: ორმაგი ჩამოჭრა
B: გისურვებთ დღეს კარგად, გმადლობთ დარეკვისთვის.
C: არ ვიცი, სხვას დაურეკეთ.
```

Row 25, label + free text:

| Row | Label (ID) | Hint |
|---|---|---|
| 25 | `როდის უნდა მოინიშნოს ზარი ხელახლა გადასახედად` (`S7.F.FLAG`) | `მაგალითად: როცა კლიენტი საჩივარს ან სასამართლოს ახსენებს.` |

**`S7.EX.FLAG`**:
```
როცა კლიენტი ახსენებს საჩივარს, ეროვნულ ბანკს ან სასამართლოს; ასევე, როცა ზარი 15 წუთზე მეტს გრძელდება გადაწყვეტის გარეშე.
```

> **Build-track note.** `analysis_instructions` is a **global** setting (`settings_store.DEFAULTS`),
> not per-tenant. Sheet 7 therefore cannot be auto-applied to one tenant. Provisioning handling is
> defined in §G.5: the mandatory/forbidden phrase lists become an `internal` KB document, and the
> free-text emphases are printed in the operator report for a human decision.

---

### SHEET 8 — `ნიმუში (შევსებული)`  *(read-only)*

A single scrollable page showing one fictional company filled in end to end, block by block, in the
same order as the workbook. Sheet protection on (`password` not needed; `sheet.protection.sheet =
True`). Column A width `40`, B width `78`, C width `20`, D width `30`, E width `16`.

**`S8.TITLE`** (A1): `ნიმუში — სრულად შევსებული კითხვარი`
**`S8.SUB`** (A2): `გამოგონილი კომპანია. ასე გამოიყურება კარგად შევსებული ფაილი.`
**`S8.HELP`** (A3, merged `A:E`, row height 48):
```
ეს ფურცელი დაცულია და მასში ჩაწერა არ შეიძლება — ის მხოლოდ სანახავადაა. თუ სადმე გაგიჭირდებათ, დააკვირდით, როგორ არის აქ დაწერილი: განსაკუთრებით სვეტს „შეფასების მითითება“ და პასუხების სიგრძეს.
```

Blocks, each with a bold Georgian sub-heading:
`კომპანია` · `პორტალის მომხმარებლები` · `შეფასების რუბრიკა` · `ხშირი კითხვები` ·
`წესები და დოკუმენტები` · `ბოტის პარამეტრები` · `ანალიზის აქცენტები`.

Fictional company used throughout (must stay obviously fictional — never a real Georgian brand):

```
შპს „კლინიკა ნიმუში“
```

Content for the blocks:
- **კომპანია** — `შპს „კლინიკა ნიმუში“` · `კლინიკა ან საავადმყოფო` · `საქართველო, თბილისი` ·
  `ქართული` · `რუსული` · `ტელეფონი, ვებ-ჩატი, Facebook` · `14` · `380` ·
  `ორშ.–პარ. 08:00–20:00, შაბათი 09:00–15:00` · `მარიამ კაპანაძე` · `m.kapanadze@example.ge` ·
  `+995 32 2 00 00 00` · `მომდევნო თვის დასაწყისში`
- **პორტალის მომხმარებლები** — two rows: `მარიამ კაპანაძე / m.kapanadze@example.ge / მფლობელი — ყველა უფლება / ხარისხის სამსახური`
  and `ლევან ჩხეიძე / l.chkheidze@example.ge / წევრი — ჩვეულებრივი წვდომა / ქოლ-ცენტრის ხელმძღვანელი`
- **შეფასების რუბრიკა** — the eight enabled dimensions from §B verbatim, with weights `10 / 15 / 10 / 20 / 20 / 10 / 10 / 5`.
- **ხშირი კითხვები** — the twelve clinic rows from §C.3 verbatim.
- **წესები და დოკუმენტები** — the clinic policy row from §C.6.
- **ბოტის პარამეტრები** — `არა — მხოლოდ მონახაზი ოპერატორისთვის`, plus the clinic-flavoured
  persona/greeting/refusal below.
- **ანალიზის აქცენტები** — `S7.EX.MAIN` adapted to the clinic (below).

**`S8.EX.PERSONA`**
```
თქვენ ხართ „კლინიკა ნიმუშის“ მიმღების ციფრული ასისტენტი. ისაუბრეთ მშვიდად და მოკლედ, თქვენობით. დიაგნოზი და მკურნალობის რჩევა არასოდეს გასცეთ — ჩაწერეთ ვიზიტზე ან დააკავშირეთ ოპერატორს.
```
**`S8.EX.GREET.KA`**
```
გამარჯობა! „კლინიკა ნიმუშის“ ასისტენტი ვარ. დაგეხმარებით ჩაწერაში, ვიზიტის გადატანაში ან ანალიზების პასუხებში.
```
**`S8.EX.REF.KA`**
```
ამ კითხვაზე ზუსტი პასუხი ჩემთან არ არის. დაგაკავშირებთ მიმღების ოპერატორს, რომელიც ზუსტ ინფორმაციას მოგცემთ.
```
**`S8.EX.EMPHASIS`**
```
ჩვენთვის მთავარია, ოპერატორმა სწორად თქვას ვიზიტის ღირებულება და ექიმის მიღების დღეები, და არასოდეს გასცეს სამედიცინო რჩევა ტელეფონით. განსაკუთრებით გვაინტერესებს ზარები, სადაც პაციენტი მწვავე ტკივილს ან სისხლდენას ახსენებს.
```

---

## B. The rubric sheet — dimensions, guidance, and the copy around them

### B.1 The ten shipped dimensions

Rows 7–14 ship with `გამოვიყენოთ? = დიახ`; rows 15–16 ship with `არა` and a blank weight. The eight
enabled weights total exactly **100**.

---

**Row 7 — `მისალმება და იდენტიფიკაცია` — weight `10`**
```
მაღალი ქულა: ოპერატორმა ზარის დასაწყისში დაასახელა კომპანია და საკუთარი სახელი, მიესალმა, სახელი გაიგო და შემდეგ კლიენტს სახელით მიმართა, და დახურულ ინფორმაციაზე საუბრამდე პროცედურით დაადასტურა ვინაობა. დაბალი ქულა: მისალმების გარეშე დაიწყო, არ წარადგინა თავი, კლიენტს ბოლომდე „თქვენ“ მიმართა სახელის გამოყენების გარეშე, ან იდენტიფიკაციამდე გასცა ანგარიშის ან პირადი ინფორმაცია.
```

**Row 8 — `მოსმენა და ემპათია` — weight `15`**
```
მაღალი ქულა: ოპერატორმა ბოლომდე მოისმინა კლიენტი შეწყვეტინების გარეშე, პრობლემა საკუთარი სიტყვებით გაიმეორა დასადასტურებლად („ანუ თანხა ჩამოგეჭრათ, მაგრამ გადახდა არ დაფიქსირდა — სწორად გავიგე?“) და სიტყვიერად აღიარა კლიენტის დისკომფორტი. დაბალი ქულა: შეაწყვეტინა, ერთი და იგივე კითხვა ხელახლა დაუსვა იმის ნიშნად რომ არ უსმენდა, ან პირდაპირ ინსტრუქციაზე გადავიდა პრობლემის დადასტურების გარეშე.
```

**Row 9 — `პრობლემის დაზუსტება` — weight `10`**
```
მაღალი ქულა: ოპერატორმა დასვა კონკრეტული დამაზუსტებელი კითხვები, რომლებიც პრობლემის გადასაჭრელადაა საჭირო — რომელი პროდუქტი, რომელი თარიღი, რა ტექსტი წერია შეცდომაში — და პასუხის გაცემამდე დარწმუნდა, რომ სწორ საკითხზე საუბრობს. დაბალი ქულა: ივარაუდა და სხვა კითხვას უპასუხა, ან რჩევა მისცა ისე, რომ კლიენტის ვითარება არ იცოდა.
```

**Row 10 — `ინფორმაციის სისწორე` — weight `20`**
```
მაღალი ქულა: ყველა ვადა, თანხა, საკომისიო, პირობა და პროცედურა, რაც ოპერატორმა დაასახელა, ემთხვევა კომპანიის მოქმედ წესებს; როცა ზუსტად არ იცოდა, თქვა რომ დააზუსტებდა და დაუბრუნდებოდა. დაბალი ქულა: დაასახელა არასწორი ვადა, თანხა ან პირობა, ვარაუდი დარწმუნებული ტონით თქვა, ან დაპირდა იმას, რაც კომპანიის წესებით შეუძლებელია.
```

**Row 11 — `საკითხის გადაწყვეტა` — weight `20`**
```
მაღალი ქულა: ზარის ბოლოს საკითხი გადაწყვეტილია, ან შეთანხმებულია კონკრეტული გზა — ვინ, რას და როდის გააკეთებს, დასახელებული ვადით. დაბალი ქულა: ზარი ბუნდოვნად დასრულდა („გადავხედავთ და დაგიკავშირდებით“ ვადის დასახელების გარეშე), კლიენტი უსაფუძვლოდ გადაამისამართა სხვა განყოფილებაში, ან ურჩია რომ თავად დაერეკა ხელახლა.
```

**Row 12 — `სავალდებულო გაფრთხილებები და პროცედურა` — weight `10`**
```
მაღალი ქულა: ოპერატორმა თქვა ყველა სავალდებულო ფრაზა, რომელიც ამ ტიპის ზარს სჭირდება — ზარის ჩაწერის შესახებ გაფრთხილება, პერსონალურ მონაცემებზე თანხმობა, პროდუქტის საკომისიოსა და რისკის გაცხადება — და დაიცვა შიდა პროცედურის თანმიმდევრობა. დაბალი ქულა: გამოტოვა სავალდებულო გაფრთხილება, პროდუქტი შესთავაზა ხარჯების დასახელების გარეშე, ან ნაბიჯები არეული თანმიმდევრობით შეასრულა.
```

**Row 13 — `ტონი და პროფესიონალიზმი` — weight `10`**
```
მაღალი ქულა: ოპერატორი მთელი ზარის განმავლობაში იყო თავაზიანი და მშვიდი, მიმართავდა თქვენობით, ისაუბრა გასაგები ენით ზედმეტი ტერმინების გარეშე და დაძაბულ მომენტშიც არ აიმაღლა ტონი. დაბალი ქულა: გაღიზიანება, ირონია, კლიენტის ან კოლეგის კრიტიკა, ჟარგონი, ან პასუხი მხოლოდ შიდა ტერმინებით, რომელიც კლიენტს არ ესმის.
```

**Row 14 — `ზარის დასრულება` — weight `5`**
```
მაღალი ქულა: ოპერატორმა ბოლოს შეაჯამა შეთანხმებული ნაბიჯები, ჰკითხა კლიენტს კიდევ თუ რჩებოდა კითხვა და დაემშვიდობა კომპანიის სახელით. დაბალი ქულა: ზარი შეჯამების გარეშე დასრულდა, ან ოპერატორმა პირველმა გათიშა კლიენტის დამშვიდობებამდე.
```

**Row 15 — `დამატებითი პროდუქტის შეთავაზება` — `გამოვიყენოთ? = არა`, weight blank**
```
მაღალი ქულა: ოპერატორმა კლიენტის საკითხის გადაწყვეტის შემდეგ შესთავაზა ის პროდუქტი ან სერვისი, რომელიც სწორედ ამ სიტუაციას შეესაბამება, ერთხელ, მოკლედ და ხარჯების დასახელებით; უარის შემთხვევაში აღარ გაიმეორა. დაბალი ქულა: შეთავაზება პრობლემის გადაწყვეტამდე, რამდენჯერმე გამეორება უარის მიღების შემდეგ, ან ფასის დაფარვა.
```

**Row 16 — `ლოდინის მართვა` — `გამოვიყენოთ? = არა`, weight blank**
```
მაღალი ქულა: ლოდინის რეჟიმში გადაყვანამდე ოპერატორმა ახსნა მიზეზი და ნება ჰკითხა, დაბრუნების შემდეგ მადლობა გადაუხადა ლოდინისთვის, და ხანგრძლივი პაუზის დროს პერიოდულად უბრუნდებოდა კლიენტს. დაბალი ქულა: ახსნის გარეშე გააჩერა, ერთ წუთზე მეტხანს დატოვა უჩუმრად, ან ლოდინიდან დაბრუნებისას თავიდან დააწყებინა ამბის მოყოლა.
```

### B.2 Guidance: before / after (for the guide, `GUIDE.GUIDANCE.BA`)

**`GUIDE.GUIDANCE.H`**
```
ერთი სვეტი, რომელიც ყველაფერს წყვეტს
```

**`GUIDE.GUIDANCE.P`**
```
სვეტი „შეფასების მითითება“ სიტყვასიტყვით გადაეცემა სისტემას — ის ზუსტად ის ინსტრუქციაა, რომლითაც ზარს 90 ქულა ეძლევა და არა 40. თუ მითითება ბუნდოვანია, ქულებიც შემთხვევითი იქნება. თუ მითითებაში ჩანაწერია ის, რაც ჩანაწერში ისმის ან არ ისმის, ქულები სტაბილური და სამართლიანი გამოვა.

წესი მარტივია: დაწერეთ ის, რაც ჩანაწერში დაფიქსირდება, და არა ის, რაც ოპერატორმა „უნდა იგრძნოს“.
```

**`GUIDE.GUIDANCE.BAD.LABEL`**: `ასე არა`
**`GUIDE.GUIDANCE.BAD`**
```
იყოს თავაზიანი და კლიენტზე ორიენტირებული.
```
**`GUIDE.GUIDANCE.BAD.WHY`**
```
რას ნიშნავს „თავაზიანი“? ერთი შემფასებელი ამას 80-ს დაუწერს, მეორე 45-ს, ორივე დამაჯერებელი არგუმენტით. სისტემაც ზუსტად ასე მოიქცევა — ქულა ერთი და იმავე ზარისთვის ყოველ ჯერზე სხვა იქნება.
```

**`GUIDE.GUIDANCE.GOOD.LABEL`**: `ასე კი`
**`GUIDE.GUIDANCE.GOOD`**
```
მაღალი ქულა: ოპერატორი მიმართავს თქვენობით, ბოლომდე ისმენს კლიენტს შეწყვეტინების გარეშე, პრობლემას საკუთარი სიტყვებით იმეორებს დასადასტურებლად და დაძაბულ მომენტშიც არ იმაღლებს ტონს. დაბალი ქულა: აწყვეტინებს, ირონიით პასუხობს, კოლეგას ან სხვა განყოფილებას აკრიტიკებს, ან შიდა ტერმინებით საუბრობს ისე, რომ კლიენტს არ ესმის.
```
**`GUIDE.GUIDANCE.GOOD.WHY`**
```
აქ ყველა სიტყვა ჩანაწერში მოწმდება: შეაწყვეტინა თუ არა, გაიმეორა თუ არა პრობლემა, აიმაღლა თუ არა ტონი. ამიტომ ორი სხვადასხვა ზარი, რომლებიც ერთნაირად ჩატარდა, ერთნაირ ქულას მიიღებს.
```

**`GUIDE.GUIDANCE.FORMULA`**
```
გამოიყენეთ ეს ორნაწილიანი ფორმა: „მაღალი ქულა: …“ და „დაბალი ქულა: …“. თითოეულში ჩამოწერეთ 3–5 კონკრეტული ქცევა. თუ ვერ ხერხდება იმის დაწერა, თუ როგორ გაიგებთ ჩანაწერიდან, ეს ქცევა შედგა თუ არა — ესე იგი ეს განზომილება ჯერ არ გამოგდით და ჯობს, დროებით გამორთოთ.
```

---

## C. KB templates per industry

Format of every row below: `კითხვა` · `პასუხი` · `კატეგორია` · `ტეგები (მძიმით)` · `ხილვადობა`.

All template rows ship **yellow** with `G.MARK.EXAMPLE`, and the sheet's help block already carries
`G.NOTE.PLACEHOLDER` so the customer knows the figures are stand-ins.

The `ტეგები` column is intentionally filled with **the words customers actually use**, including
misspellings and Russian/English loan words. It is embedded together with the question and answer
(see §1), so it directly improves retrieval — it is not decorative metadata.

### C.1 ბანკი — 12 rows

Category list `L.CAT.ბანკი`: `ბარათები`, `გადარიცხვები`, `ანგარიშები`, `სესხები`,
`ინტერნეტბანკი`, `ტარიფები`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `როგორ დავბლოკო დაკარგული ან მოპარული ბარათი?` | `ბარათის დაბლოკვა შეგიძლიათ სამი გზით: მობილბანკის აპლიკაციაში ბარათის გვერდზე ღილაკით „დაბლოკვა“, ინტერნეტბანკში, ან ცხელ ხაზზე დარეკვით — ოპერატორი დაგიბლოკავთ ვინაობის დადასტურების შემდეგ. დაბლოკვა მყისიერია და უფასოა. ახალი ბარათი მზადდება 3 სამუშაო დღეში და შეგიძლიათ ნებისმიერ ფილიალში აიღოთ.` | `ბარათები` | `დაკარგული ბარათი, მოპარეს ბარათი, ბლოკი, დაბლოკვა, გავაუქმო ბარათი` | `საჯარო` |
| 2 | `რამდენ ხანში მიდის გადარიცხვა სხვა ბანკში?` | `იმავე ბანკის ანგარიშებს შორის გადარიცხვა მყისიერია. სხვა ქართულ ბანკში გადარიცხვა, თუ სამუშაო დღეს 16:30-მდე გააკეთეთ, იმავე დღეს ჩაირიცხება; ამის შემდეგ — მომდევნო სამუშაო დღეს. საზღვარგარეთ გადარიცხვას სჭირდება 1-დან 3 სამუშაო დღემდე, მიმღები ბანკის მიხედვით.` | `გადარიცხვები` | `გადარიცხვა, ჩარიცხვა, რამდენ ხანში, სვიფტი, swift, გადაგზავნა` | `საჯარო` |
| 3 | `როგორ გავხსნა ანგარიში ფილიალში მისვლის გარეშე?` | `ანგარიშის დისტანციურად გახსნა შესაძლებელია მობილბანკის აპლიკაციით: საჭიროა პირადობის მოწმობა ან პასპორტი და სახის ვიდეო-იდენტიფიკაცია. პროცესი დაახლოებით 10 წუთს გრძელდება. ანგარიში აქტიურდება იმავე დღეს, ბარათი კი 3 სამუშაო დღეში მოგივათ არჩეულ მისამართზე.` | `ანგარიშები` | `ანგარიშის გახსნა, დისტანციურად, ონლაინ, ვიდეო იდენტიფიკაცია` | `საჯარო` |
| 4 | `რა დოკუმენტები მჭირდება სესხის განაცხადისთვის?` | `სამომხმარებლო სესხისთვის საჭიროა პირადობის მოწმობა და შემოსავლის დამადასტურებელი დოკუმენტი — ხელფასის ცნობა ბოლო 6 თვეზე ან საბანკო ამონაწერი. მეწარმისთვის დამატებით საჭიროა საგადასახადო დეკლარაცია. თუ ხელფასს ჩვენს ბანკში იღებთ, შემოსავლის ცნობა არ დაგჭირდებათ.` | `სესხები` | `სესხი, დოკუმენტები, ცნობა, ხელფასის ცნობა, სესხის აღება` | `საჯარო` |
| 5 | `როგორ გავიგო სესხის დარჩენილი ნაშთი და შემდეგი გადახდის თარიღი?` | `ორივე ჩანს ინტერნეტბანკსა და მობილბანკში, განყოფილებაში „ჩემი სესხები“ — იქვეა გადახდის გრაფიკი სრულად. ცხელ ხაზზეც გეტყვით, ვინაობის დადასტურების შემდეგ. გრაფიკის PDF ვერსიას ელფოსტაზეც გამოგიგზავნით.` | `სესხები` | `ნაშთი, დავალიანება, გრაფიკი, შემდეგი გადახდა, რამდენი მაქვს` | `საჯარო` |
| 6 | `დამავიწყდა ინტერნეტბანკის პაროლი, რა ვქნა?` | `შესვლის გვერდზე დააჭირეთ „პაროლის აღდგენას“ — კოდი მოვა თქვენს ნომერზე და ახალ პაროლს იქვე დააყენებთ. თუ ნომერი შეცვლილია და კოდი ვერ მიიღეთ, საჭიროა ფილიალში მისვლა პირადობის მოწმობით ან ცხელ ხაზზე დარეკვა ვინაობის დასადასტურებლად.` | `ინტერნეტბანკი` | `პაროლი, დამავიწყდა, აღდგენა, ვერ შევდივარ, დაბლოკილია` | `საჯარო` |
| 7 | `რამდენი თანხის განაღდება შემიძლია ბანკომატიდან დღეში?` | `სტანდარტული დღიური ლიმიტი განაღდებაზე არის 3 000 ლარი. ლიმიტის შეცვლა შეგიძლიათ მობილბანკში, განყოფილებაში „ბარათის პარამეტრები“, ან ცხელ ხაზზე დარეკვით. ჩვენს ბანკომატებში განაღდება უფასოა; სხვა ბანკის ბანკომატში მოქმედებს საკომისიო.` | `ბარათები` | `ლიმიტი, განაღდება, ბანკომატი, რამდენი შემიძლია, ქეში` | `საჯარო` |
| 8 | `როგორ დავხურო ანგარიში?` | `ანგარიშის დახურვისთვის საჭიროა ფილიალში მისვლა პირადობის მოწმობით. დახურვამდე ანგარიშზე არ უნდა იყოს დავალიანება და აქტიური სესხი ან ბარათი. დახურვა უფასოა და იმავე დღეს სრულდება.` | `ანგარიშები` | `დახურვა, გაუქმება, ანგარიშის დახურვა, აღარ მინდა` | `საჯარო` |
| 9 | `სად ვნახო დღევანდელი ვალუტის კურსი?` | `მიმდინარე კურსი ყოველთვის ჩანს ჩვენს ვებგვერდზე მთავარ გვერდზე და მობილბანკის აპლიკაციაში, განყოფილებაში „კონვერტაცია“. კურსი დღის განმავლობაში იცვლება; აპლიკაციაში ნაჩვენებია სწორედ ის კურსი, რომლითაც ოპერაცია შესრულდება.` | `ტარიფები` | `კურსი, ვალუტა, დოლარი, ევრო, კონვერტაცია, გადაცვლა` | `საჯარო` |
| 10 | `რა ღირს ბარათის წლიური მომსახურება?` | `სტანდარტული ბარათის წლიური მომსახურება არის 20 ლარი, პრემიუმ ბარათის — 120 ლარი. ხელფასის პროექტის მონაწილეებისთვის სტანდარტული ბარათი უფასოა. თანხა ერთხელ ჩამოიჭრება ბარათის გამოშვების თარიღზე.` | `ტარიფები` | `ფასი, ღირებულება, საკომისიო, წლიური, მომსახურება` | `საჯარო` |
| 11 | `ოპერატორის უფლებამოსილება საკომისიოს ჩამოწერაზე` | `ოპერატორს შეუძლია დამოუკიდებლად ჩამოწეროს ერთჯერადი საკომისიო 30 ლარამდე, თუ კლიენტი პირველად აპროტესტებს და შეცდომა ჩვენი მხრიდანაა. 30-დან 150 ლარამდე საჭიროა ცვლის უფროსის თანხმობა ჩატში. 150 ლარზე მეტი მხოლოდ განყოფილების ხელმძღვანელის წერილობითი დასტურით. კლიენტს ჩამოწერაზე დაპირება არ მიეცემა, სანამ თანხმობა არ არის მიღებული.` | `შიდა პროცედურა` | `საკომისიო, ჩამოწერა, უფლებამოსილება, კომპენსაცია, ზღვარი` | `შიდა` |
| 12 | `როდის უნდა გადავამისამართოთ ზარი უსაფრთხოების სამსახურში` | `ზარი დაუყოვნებლივ გადამისამართდება უსაფრთხოების სამსახურში, თუ კლიენტი აცხადებს, რომ მისი ბარათით უცნობმა პირმა ისარგებლა, თუ ვინმე მისგან SMS-კოდს ითხოვდა, ან თუ სახეზეა სოციალური ინჟინერიის ნიშნები. ასეთ ზარზე ჯერ იბლოკება ბარათი, შემდეგ ხდება გადამისამართება. კლიენტს არ ეკითხება, რა კოდი მიიღო.` | `შიდა პროცედურა` | `თაღლითობა, ესკალაცია, უსაფრთხოება, სოციალური ინჟინერია, კოდი` | `შიდა` |

### C.2 სადაზღვევო — 12 rows

Category list `L.CAT.სადაზღვევო`: `ზარალის განაცხადი`, `ჯანმრთელობა`, `ავტოდაზღვევა`,
`პოლისი`, `ანაზღაურება`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `როგორ დავაფიქსირო ზარალი?` | `ზარალის განაცხადი შეგიძლიათ შეავსოთ ჩვენს ვებგვერდზე განყოფილებაში „ზარალის დაფიქსირება“, ან დაგვირეკოთ ცხელ ხაზზე. დაგჭირდებათ პოლისის ნომერი, მოვლენის თარიღი და მოკლე აღწერა. განაცხადის მიღებას ადასტურებს SMS ნომრით, რომლითაც შემდეგ სტატუსს ამოწმებთ.` | `ზარალის განაცხადი` | `ზარალი, განაცხადი, დაფიქსირება, შემთხვევა, როგორ განვაცხადო` | `საჯარო` |
| 2 | `რა დოკუმენტები სჭირდება ავტოზარალს?` | `საჭიროა: მართვის მოწმობა, ავტომობილის სარეგისტრაციო მოწმობა, პოლისი და საპატრულო პოლიციის ოქმი ან ავარიის შესახებ შეტყობინების ფორმა. სასურველია დაზიანების ფოტოები ადგილიდან. დოკუმენტების ატვირთვა შესაძლებელია ვებგვერდზევე.` | `ავტოდაზღვევა` | `ავარია, ავტო, დოკუმენტები, ოქმი, ფოტო, საპატრულო` | `საჯარო` |
| 3 | `რამდენ ხანში მივიღებ ანაზღაურებას?` | `სრული დოკუმენტაციის მიღებიდან განაცხადი განიხილება 10 სამუშაო დღეში. დადებითი გადაწყვეტილების შემდეგ თანხა ირიცხება 5 სამუშაო დღეში მითითებულ ანგარიშზე. თუ საქმეს დამატებითი ექსპერტიზა სჭირდება, ვადა შეიძლება გაიზარდოს — ამის შესახებ წერილობით შეგატყობინებთ.` | `ანაზღაურება` | `ანაზღაურება, ვადა, რამდენ ხანში, თანხა, გადმორიცხვა` | `საჯარო` |
| 4 | `რას ფარავს ჯანმრთელობის ბაზისური პაკეტი?` | `ბაზისური პაკეტი ფარავს ამბულატორიულ მომსახურებას, გადაუდებელ სტაციონარს, ლაბორატორიულ და ინსტრუმენტულ კვლევებს ექიმის დანიშნულებით, და მედიკამენტების ხარჯს წლიური ლიმიტის ფარგლებში. პაკეტი არ ფარავს ესთეტიკურ პროცედურებს, სტომატოლოგიას და დაგეგმილ ოპერაციებს, თუ დამატებით არ არის შეძენილი.` | `ჯანმრთელობა` | `დაფარვა, პაკეტი, რას ფარავს, ლიმიტი, სტომატოლოგია` | `საჯარო` |
| 5 | `რა არის ფრანშიზა და როგორ მუშაობს?` | `ფრანშიზა არის თანხა, რომელსაც ზარალის დროს თქვენ თავად ფარავთ, დანარჩენს კი ჩვენ ვანაზღაურებთ. მაგალითად, თუ ფრანშიზა 200 ლარია და ზარალი 1 000 ლარი, ჩვენ 800 ლარს ვანაზღაურებთ. ფრანშიზის ოდენობა თქვენს პოლისშია მითითებული პირველ გვერდზე.` | `პოლისი` | `ფრანშიზა, თანაგადახდა, რა არის, მაგალითი` | `საჯარო` |
| 6 | `როგორ ავიღო გარანტიის წერილი კლინიკისთვის?` | `დაგეგმილ მომსახურებაზე გარანტიის წერილი გაიცემა კლინიკის მიმართვის საფუძველზე. მიმართვა და ექიმის დანიშნულება გამოგვიგზავნეთ ელფოსტით ან ჩვენს აპლიკაციაში; პასუხს იღებთ 1 სამუშაო დღეში. გადაუდებელ შემთხვევაში კლინიკა თავად გვიკავშირდება და წერილი გაიცემა მაშინვე.` | `ჯანმრთელობა` | `გარანტია, გარანტიის წერილი, თანხმობა, კლინიკა, დანიშნულება` | `საჯარო` |
| 7 | `როგორ დავამატო ოჯახის წევრი პოლისზე?` | `ოჯახის წევრის დამატება შესაძლებელია ნებისმიერ დროს. დაგვიკავშირდით და გამოგიგზავნით დამატებით შეთანხმებას; ძალაში შედის ხელმოწერიდან და პრემიის გადახდიდან. თანხა გადაანგარიშდება პოლისის დარჩენილი ვადის პროპორციულად.` | `პოლისი` | `ოჯახი, დამატება, მეუღლე, შვილი, პოლისზე დამატება` | `საჯარო` |
| 8 | `სად ვნახო ჩემი პოლისის ნომერი?` | `პოლისის ნომერი მითითებულია პოლისის პირველ გვერდზე, ზედა მარჯვენა კუთხეში, და იმ ელფოსტაშიც, რომლითაც პოლისი გამოგეგზავნათ. ასევე ჩანს ჩვენს აპლიკაციაში, განყოფილებაში „ჩემი პოლისები“.` | `პოლისი` | `პოლისის ნომერი, სად ვნახო, ნომერი, დამავიწყდა` | `საჯარო` |
| 9 | `შემიძლია თუ არა პოლისის გაუქმება და თანხის დაბრუნება?` | `პოლისის გაუქმება შესაძლებელია ნებისმიერ დროს წერილობითი განცხადებით. თუ პოლისის ვადა ჯერ არ დაწყებულა, თანხა სრულად ბრუნდება. თუ ვადა უკვე მიმდინარეობს და ანაზღაურება არ მოგითხოვიათ, ბრუნდება გამოუყენებელი პერიოდის პროპორციული ნაწილი ადმინისტრაციული ხარჯის გამოკლებით.` | `პოლისი` | `გაუქმება, თანხის დაბრუნება, უარი, აღარ მინდა, გამოსყიდვა` | `საჯარო` |
| 10 | `რა უნდა გავაკეთო ავარიის ადგილზე?` | `პირველ რიგში დარწმუნდით, რომ არავინ დაშავებულა; საჭიროების შემთხვევაში გამოიძახეთ 112. გამოიძახეთ საპატრულო პოლიცია და დაელოდეთ ოქმს — ავტომობილები ოქმის შედგენამდე არ გადაადგილოთ, თუ ეს მოძრაობას არ უშლის. გადაუღეთ ფოტოები ზოგადი ხედით და დაზიანების ახლო ხედით, შემდეგ დაგვირეკეთ ცხელ ხაზზე.` | `ავტოდაზღვევა` | `ავარია, ადგილზე, რა ვქნა, პატრული, 112, ფოტო` | `საჯარო` |
| 11 | `ოპერატორის ზღვარი შეღავათებზე და დათმობებზე` | `ოპერატორს შეუძლია დამოუკიდებლად დათმოს ადმინისტრაციული ხარჯი 50 ლარამდე და შესთავაზოს პრემიის გადახდის გადავადება 15 დღემდე. ფრანშიზის შემცირება, პრემიის ფასდაკლება ან ვადის გახანგრძლივება ოპერატორის უფლებამოსილება არ არის და საჭიროებს ანდერრაიტერის თანხმობას. კლიენტს არ ეთქმება „ვცდილობთ მოვაგვაროთ“, თუ თანხმობა მიღებული არ არის.` | `შიდა პროცედურა` | `დათმობა, შეღავათი, ზღვარი, ანდერრაიტერი, უფლებამოსილება` | `შიდა` |
| 12 | `როგორ ვმოქმედებთ საეჭვო ზარალის შემთხვევაში` | `თუ ზარალის გარემოებები ეწინააღმდეგება პოლისის მონაცემებს, თუ განაცხადი შედის პოლისის გაფორმებიდან 10 დღეში, ან თუ იგივე პირი მესამედ აფიქსირებს მსგავს ზარალს, საქმე ინიშნება შემოწმებაზე. ოპერატორი კლიენტს ეჭვს არ ატყობინებს და არ ახსენებს შემოწმებას — მას ეუბნება სტანდარტულ ვადას და საქმეს გადასცემს ზარალის დეპარტამენტს ნიშნულით.` | `შიდა პროცედურა` | `თაღლითობა, საეჭვო, შემოწმება, ესკალაცია, ზარალის დეპარტამენტი` | `შიდა` |

### C.3 კლინიკა — 12 rows

Category list `L.CAT.კლინიკა`: `ჩაწერა`, `ანალიზები`, `ვიზიტი`, `დაზღვევა და გადახდა`,
`დოკუმენტები`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `როგორ ჩავეწერო ექიმთან?` | `ჩაწერა შესაძლებელია ცხელ ხაზზე დარეკვით, ვებგვერდიდან ან ჩვენს Facebook გვერდზე მიწერით. დაგჭირდებათ სახელი, გვარი, პირადი ნომერი და ექიმის ან მიმართულების დასახელება. ჩაწერის დადასტურება მოგივათ SMS-ით ვიზიტამდე ერთი დღით ადრე.` | `ჩაწერა` | `ჩაწერა, რიგი, ვიზიტი, დაჯავშნა, როგორ ჩავეწერო` | `საჯარო` |
| 2 | `როგორ გადავიტანო ან გავაუქმო ვიზიტი?` | `ვიზიტის გადატანა ან გაუქმება უფასოა, თუ ამას ვიზიტამდე მინიმუმ 3 საათით ადრე გვაცნობებთ — დაგვირეკეთ ან უპასუხეთ დამადასტურებელ SMS-ს. ამის შემდეგ გაუქმებული ვიზიტი ჩაითვლება გამოტოვებულად და ხელახლა ჩაწერა შესაძლებელია მხოლოდ თავისუფალ დროზე.` | `ჩაწერა` | `გადატანა, გაუქმება, ვერ მოვალ, სხვა დროზე, გადავწერო` | `საჯარო` |
| 3 | `როდის და როგორ მივიღებ ანალიზების პასუხს?` | `სტანდარტული სისხლის ანალიზების პასუხი მზადდება იმავე დღეს, 18:00 საათამდე; ჰორმონებისა და სპეციფიკური კვლევების — 2-დან 5 სამუშაო დღემდე. პასუხი მოგივათ ელფოსტაზე PDF ფაილად და ასევე ხელმისაწვდომია ვებგვერდზე პირად კაბინეტში. ბეჭდიანი ვერსია გაიცემა მიმღებში პირადობის მოწმობით.` | `ანალიზები` | `ანალიზი, პასუხი, შედეგი, როდის მზადდება, ლაბორატორია` | `საჯარო` |
| 4 | `მუშაობთ თუ არა შაბათ-კვირას?` | `შაბათს კლინიკა მუშაობს 09:00-დან 15:00 საათამდე; ამ დღეს იღებენ თერაპევტი, პედიატრი და ლაბორატორია. კვირას კლინიკა დაკეტილია, მუშაობს მხოლოდ გადაუდებელი დახმარების განყოფილება, 24 საათი.` | `ვიზიტი` | `სამუშაო საათები, შაბათი, კვირა, ღიაა, დღეს მუშაობთ` | `საჯარო` |
| 5 | `საჭიროა თუ არა ექიმის მიმართვა?` | `თერაპევტთან, პედიატრთან და ზოგადი პროფილის ექიმებთან მიმართვა არ არის საჭირო. ვიწრო სპეციალისტთან — მაგალითად, ენდოკრინოლოგთან ან კარდიოლოგთან — მიმართვა საჭიროა მხოლოდ იმ შემთხვევაში, თუ ვიზიტს დაზღვევით ან სახელმწიფო პროგრამით ანაზღაურებთ.` | `ვიზიტი` | `მიმართვა, ფორმა 100, საჭიროა თუ არა, სპეციალისტი` | `საჯარო` |
| 6 | `მუშაობთ თუ არა ჩემს სადაზღვევოსთან?` | `ჩვენ ვთანამშრომლობთ საქართველოში მოქმედ ყველა მსხვილ სადაზღვევო კომპანიასთან. ვიზიტამდე გვითხარით სადაზღვევოს დასახელება და პოლისის ნომერი — შევამოწმებთ დაფარვას და გეტყვით, საჭიროა თუ არა გარანტიის წერილი. თანაგადახდის ნაწილს ადგილზე იხდით.` | `დაზღვევა და გადახდა` | `დაზღვევა, სადაზღვევო, პოლისი, თანაგადახდა, ანაზღაურება` | `საჯარო` |
| 7 | `რა ღირს ექიმთან კონსულტაცია?` | `თერაპევტისა და პედიატრის კონსულტაცია ღირს 60 ლარი, ვიწრო სპეციალისტის — 80 ლარი. განმეორებითი ვიზიტი იმავე ექიმთან 30 დღის განმავლობაში ღირს 40 ლარი. დაზღვევის შემთხვევაში იხდით მხოლოდ თანაგადახდის ნაწილს.` | `დაზღვევა და გადახდა` | `ფასი, ღირებულება, რა ღირს, კონსულტაცია, გადახდა` | `საჯარო` |
| 8 | `როგორ მოვემზადო სისხლის ანალიზისთვის?` | `სისხლი უნდა ჩააბაროთ უზმოზე — ბოლო კვებიდან უნდა გავიდეს მინიმუმ 8 საათი; წყლის დალევა შეიძლება. ანალიზის წინა დღეს მოერიდეთ ცხიმიან საკვებსა და ალკოჰოლს. თუ მუდმივად იღებთ მედიკამენტს, ჩაბარებამდე დაუკავშირდით ექიმს, უნდა შეწყვიტოთ თუ არა.` | `ანალიზები` | `მომზადება, უზმოზე, ჭამა, წამალი, სისხლის ჩაბარება` | `საჯარო` |
| 9 | `შეიძლება თუ არა ბავშვი მარტო მოვიდეს ვიზიტზე?` | `18 წლამდე პაციენტი ვიზიტზე უნდა მოვიდეს მშობელთან ან კანონიერ წარმომადგენელთან ერთად. თუ თანმხლები სხვა ნათესავია, საჭიროა მშობლის წერილობითი თანხმობა. გადაუდებელ შემთხვევაში დახმარება გაეწევა დაუყოვნებლივ, თანხლების მიუხედავად.` | `ვიზიტი` | `ბავშვი, მშობელი, თანხმობა, არასრულწლოვანი, თანმხლები` | `საჯარო` |
| 10 | `როგორ ავიღო სამედიცინო ცნობა?` | `ცნობას გასცემს ის ექიმი, რომელთანაც ვიზიტი გქონდათ. მოთხოვნა შეგიძლიათ დატოვოთ მიმღებში ან ცხელ ხაზზე; ცნობა მზადდება 1 სამუშაო დღეში და გაიცემა პირადობის მოწმობით. ცნობის ღირებულებაა 10 ლარი.` | `დოკუმენტები` | `ცნობა, სამსახურისთვის, ბაღისთვის, დოკუმენტი, ამონაწერი` | `საჯარო` |
| 11 | `როდის უნდა გადავრთოთ ზარი დაუყოვნებლივ ექიმზე ან 112-ზე` | `ოპერატორი წყვეტს ჩაწერის პროცედურას და ზარს დაუყოვნებლივ გადასცემს რიგგარეშედ, თუ პაციენტი ახსენებს: გულმკერდის ტკივილს, სუნთქვის გაძნელებას, უეცარ სისუსტეს სხეულის ერთ მხარეს, მეტყველების დარღვევას, უეცარ ძლიერ თავის ტკივილს, უწყვეტ სისხლდენას, გონების დაკარგვას, ან ორსულობისას მუცლის ტკივილს. ასეთ დროს ოპერატორი ეუბნება პაციენტს, დარეკოს 112-ზე, და ზარს არ თიშავს პასუხის მიღებამდე. ჩაწერის ან ფასის შესახებ საუბარი ამ მომენტში წყდება.` | `შიდა პროცედურა` | `გადაუდებელი, 112, სასწრაფო, ესკალაცია, ტკივილი, სისხლდენა` | `შიდა` |
| 12 | `ვის შეიძლება გავცეთ პაციენტის ინფორმაცია ტელეფონით` | `ანალიზის შედეგი, დიაგნოზი და ვიზიტის დეტალები ტელეფონით გაეცემა მხოლოდ თავად პაციენტს, ვინაობის დადასტურების შემდეგ. 18 წლამდე პაციენტზე — მშობელს ან კანონიერ წარმომადგენელს. სხვა ნათესავს, დამსაქმებელს ან მეზობელს ინფორმაცია არ ეძლევა, თუნდაც პირადი ნომერი იცოდეს. თუ პაციენტს სურს, რომ ინფორმაცია სხვამაც მიიღოს, საჭიროა მისი წერილობითი თანხმობა.` | `შიდა პროცედურა` | `კონფიდენციალობა, მონაცემები, ვის გავცე, ნათესავი, თანხმობა` | `შიდა` |

### C.4 ტელეკომი — 12 rows

Category list `L.CAT.ტელეკომი`: `ინტერნეტი`, `ტელევიზია`, `მობილური`, `ბილინგი`,
`ტექნიკური მომსახურება`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `ინტერნეტი არ მუშაობს, რა გავაკეთო?` | `პირველ რიგში გამორთეთ როუტერი დენიდან, დაელოდეთ 30 წამს და ხელახლა ჩართეთ — შემთხვევების უმეტესობა ამით წყდება. თუ როუტერზე წითელი ინდიკატორი ანთია, ეს ხაზის პრობლემაზე მიუთითებს. თუ გადატვირთვამ არ უშველა, დაგვირეკეთ — შევამოწმებთ ხაზს დისტანციურად და საჭიროების შემთხვევაში გამოვგზავნით ტექნიკოსს.` | `ინტერნეტი` | `არ მუშაობს, გათიშულია, ინტერნეტი, როუტერი, არ მაქვს კავშირი` | `საჯარო` |
| 2 | `როგორ გავიგო ჩემი დავალიანება?` | `დავალიანება ჩანს ჩვენს აპლიკაციაში მთავარ გვერდზე და პირად კაბინეტში ვებგვერდზე. ასევე შეგიძლიათ დაგვირეკოთ — ოპერატორი გეტყვით აბონენტის ნომრის დადასტურების შემდეგ. ყოველი თვის დასაწყისში ანგარიშფაქტურას ელფოსტაზეც გიგზავნით.` | `ბილინგი` | `დავალიანება, ბალანსი, ანგარიში, რამდენი მაქვს, გადასახდელი` | `საჯარო` |
| 3 | `როგორ შევცვალო პაკეტი?` | `პაკეტის შეცვლა შესაძლებელია აპლიკაციიდან, პირადი კაბინეტიდან ან ცხელ ხაზზე დარეკვით. უფრო ძვირ პაკეტზე გადასვლა ძალაში შედის მაშინვე, უფრო იაფზე — მომდევნო ანგარიშსწორების პერიოდიდან. პაკეტის შეცვლა უფასოა.` | `ბილინგი` | `პაკეტი, ტარიფი, შეცვლა, გადასვლა, ავწიო, დავწიო` | `საჯარო` |
| 4 | `გადავდივარ სხვა მისამართზე, შემიძლია სერვისის გადატანა?` | `დიახ. დაგვირეკეთ გადასვლამდე მინიმუმ 3 სამუშაო დღით ადრე და გვითხარით ახალი მისამართი — შევამოწმებთ, არის თუ არა იქ ჩვენი ქსელი. თუ ტექნიკური საშუალება არსებობს, გადატანა ხდება ტექნიკოსის ერთი ვიზიტით. ხელშეკრულება და პაკეტი უცვლელი რჩება.` | `ტექნიკური მომსახურება` | `გადატანა, მისამართი, გადავდივარ, ახალი ბინა, გადაიტანეთ` | `საჯარო` |
| 5 | `როგორ შევცვალო Wi-Fi-ის პაროლი?` | `პაროლის შეცვლა შეგიძლიათ ჩვენს აპლიკაციაში, განყოფილებაში „ჩემი ქსელი“ — ცვლილება ძალაში შედის ერთ წუთში და მოწყობილობებს ხელახლა დაერთება სჭირდებათ. თუ აპლიკაცია არ გაქვთ, დაგვირეკეთ და ოპერატორი შეგიცვლით პაროლს დისტანციურად.` | `ინტერნეტი` | `wifi, ვაიფაი, პაროლი, შეცვლა, დამავიწყდა` | `საჯარო` |
| 6 | `როგორ გავაუქმო ხელშეკრულება?` | `ხელშეკრულების გაუქმებისთვის საჭიროა განცხადება — შეგიძლიათ შემოიტანოთ ფილიალში ან გამოგვიგზავნოთ ელფოსტით. სერვისი ითიშება განცხადებიდან 5 სამუშაო დღეში, დავალიანების სრულად დაფარვის შემდეგ. ჩვენი აღჭურვილობა — როუტერი და მიმღები — უნდა დაბრუნდეს, წინააღმდეგ შემთხვევაში დაერიცხება ღირებულება.` | `ბილინგი` | `გაუქმება, გათიშვა, ხელშეკრულება, აღარ მინდა, შეწყვეტა` | `საჯარო` |
| 7 | `როგორ მუშაობს ინტერნეტი საზღვარგარეთ?` | `როუმინგი ავტომატურად ჩართულია ყველა აქტიურ ნომერზე. ევროკავშირის ქვეყნებში მოქმედებს დღიური პაკეტი, დანარჩენ ქვეყნებში — წუთობრივი და მეგაბაიტობრივი ტარიფი. მგზავრობამდე გირჩევთ, აპლიკაციაში ჩართოთ სამოგზაურო პაკეტი — ის რამდენჯერმე იაფია სტანდარტულ ტარიფზე.` | `მობილური` | `როუმინგი, საზღვარგარეთ, საზღვარი, მოგზაურობა, ინტერნეტი უცხოეთში` | `საჯარო` |
| 8 | `რამდენ ხანში მოვა ტექნიკოსი და რა ღირს?` | `ტექნიკოსი გამოდის განაცხადიდან 24 საათში, თბილისში — ხშირად იმავე დღეს. ვიზიტი უფასოა, თუ პრობლემა ჩვენს ხაზზე ან ჩვენს აღჭურვილობაშია. თუ მიზეზი აბონენტის მოწყობილობაა ან შიდა გაყვანილობა, ვიზიტი ღირს 30 ლარი — ამის შესახებ ტექნიკოსი ადგილზე გაფრთხილებთ სამუშაოს დაწყებამდე.` | `ტექნიკური მომსახურება` | `ტექნიკოსი, გამოძახება, როდის მოვა, ღირს, ვიზიტი` | `საჯარო` |
| 9 | `სად ვნახო არხების სია?` | `არხების სრული სია, პაკეტების მიხედვით, განთავსებულია ჩვენს ვებგვერდზე განყოფილებაში „ტელევიზია“. სია ასევე ჩანს მიმღების მენიუში ღილაკზე „არხების გზამკვლევი“. არხების შემადგენლობა შეიძლება შეიცვალოს — ცვლილებას წინასწარ გაცნობებთ SMS-ით.` | `ტელევიზია` | `არხები, სია, ტელევიზია, რა არხებია, პაკეტი` | `საჯარო` |
| 10 | `შემიძლია თუ არა ნომრის შენარჩუნება სხვა ოპერატორიდან გადმოსვლისას?` | `დიახ, ნომრის შენარჩუნება უფასოა და კანონით გარანტირებული. მოგვმართეთ პირადობის მოწმობით — განაცხადს ჩვენ ვაფორმებთ და ნომერი გადმოდის 1 სამუშაო დღეში. გადმოსვლამდე დაფარეთ დავალიანება წინა ოპერატორთან, თორემ განაცხადი უარყოფილი იქნება.` | `მობილური` | `ნომრის შენარჩუნება, გადმოსვლა, პორტირება, სხვა ოპერატორი` | `საჯარო` |
| 11 | `როდის ვთავაზობთ კომპენსაციას შეფერხებისთვის` | `ოპერატორს შეუძლია დამოუკიდებლად ჩამოაკლოს აბონენტს ერთი დღის საფასური, თუ სერვისი 6 საათზე მეტხანს იყო გათიშული და მიზეზი ჩვენს მხარეს იყო. 3 დღეზე მეტი შეფერხების კომპენსაცია საჭიროებს ცვლის უფროსის დადასტურებას. ერთ აბონენტზე თვეში ერთი კომპენსაციაზე მეტი ავტომატურად არ გაიცემა. კომპენსაცია არ ჰპირდება მაშინ, როცა მიზეზი ჯერ დადგენილი არ არის.` | `შიდა პროცედურა` | `კომპენსაცია, ჩამოკლება, შეღავათი, უფლებამოსილება, შეფერხება` | `შიდა` |
| 12 | `როგორ ვმოქმედებთ მასშტაბური ავარიის დროს` | `როცა ერთ უბანში 20-ზე მეტი განაცხადია, ოპერატორი ცალკეულ განაცხადს აღარ აფიქსირებს — იყენებს ავარიის საერთო ნომერს, რომელსაც ქსელის სამსახური ჩატში აქვეყნებს. აბონენტს ეუბნება უბანში დაფიქსირებულ შეფერხებას და აღდგენის სავარაუდო დროს, ზუსტი საათის დაპირების გარეშე. ტექნიკოსი ასეთ ზარზე არ იგზავნება.` | `შიდა პროცედურა` | `ავარია, მასშტაბური, ესკალაცია, ქსელი, უბანი, შეფერხება` | `შიდა` |

### C.5 სასტუმრო და მომსახურება — 12 rows

Category list `L.CAT.სასტუმრო`: `დაჯავშნა`, `ჩექ-ინი`, `ნომრები და სერვისი`, `გადახდა`,
`ლოკაცია და ტრანსფერი`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `რა დროს არის ჩექ-ინი და ჩექ-აუთი?` | `ჩექ-ინი იწყება 14:00 საათიდან, ჩექ-აუთი — 12:00 საათამდე. ჩამოსვლის დღეს ბარგის უფასოდ დატოვება შესაძლებელია მიმღებში ნებისმიერ დროს, ნომრის მზადყოფნამდეც და გამგზავრების დღესაც.` | `ჩექ-ინი` | `ჩექინი, ჩექაუთი, დრო, როდის შემიძლია, ბარგი` | `საჯარო` |
| 2 | `როგორია გაუქმების პირობები?` | `უფასო გაუქმება შესაძლებელია ჩამოსვლამდე 48 საათით ადრე — თანხა სრულად ბრუნდება. თუ გააუქმებთ ამ ვადის შემდეგ ან საერთოდ არ ჩამოხვალთ, ირიცხება ერთი ღამის საფასური. არასაბრუნებელი ტარიფით დაჯავშნისას თანხა არ ბრუნდება, მაგრამ თარიღის გადატანა ერთხელ შესაძლებელია.` | `დაჯავშნა` | `გაუქმება, დაბრუნება, ვერ ჩამოვალ, გადატანა, პირობები` | `საჯარო` |
| 3 | `შესაძლებელია თუ არა ადრეული ჩექ-ინი ან გვიანი ჩექ-აუთი?` | `ორივე შესაძლებელია ნომრების დატვირთვის მიხედვით. ადრეული ჩექ-ინი 10:00 საათიდან და გვიანი ჩექ-აუთი 16:00 საათამდე ღირს ღამის ღირებულების ნახევარი. თუ სასტუმრო არ არის სავსე, ხშირად უფასოდაც ვაწყობთ — დაგვირეკეთ ჩამოსვლის დღეს დილით და დაგიდასტურებთ.` | `ჩექ-ინი` | `ადრეული, გვიანი, early check in, late check out, ადრე მოვალ` | `საჯარო` |
| 4 | `შემიძლია ცხოველთან ერთად ჩამოსვლა?` | `დიახ, 10 კილოგრამამდე ცხოველი დაშვებულია. საფასური არის 30 ლარი ვიზიტზე, რაც მოიცავს დამატებით დასუფთავებას. გთხოვთ, დაჯავშნისას აუცილებლად მიუთითოთ ცხოველის შესახებ — ცხოველთან ერთად სტუმრებს ვათავსებთ მხოლოდ განსაზღვრულ ნომრებში.` | `ნომრები და სერვისი` | `ცხოველი, ძაღლი, კატა, შინაური ცხოველი, დაშვებულია` | `საჯარო` |
| 5 | `საუზმე შედის ფასში?` | `საუზმე შედის ყველა სტანდარტულ ტარიფში და მიეწოდება 07:30-დან 10:30 საათამდე რესტორანში პირველ სართულზე. ადრეული გამგზავრების შემთხვევაში მიმღებში წინა საღამოს შეგიძლიათ მოითხოვოთ საუზმე-კალათა — უფასოდ.` | `ნომრები და სერვისი` | `საუზმე, შედის, ფასში, კვება, რესტორანი` | `საჯარო` |
| 6 | `გაქვთ თუ არა პარკინგი?` | `დიახ, სასტუმროს აქვს დაცული პარკინგი შენობის უკან. სტუმრებისთვის ადგილი უფასოა, მაგრამ რაოდენობა შეზღუდულია — ადგილის დაჯავშნა შესაძლებელია ნომრის დაჯავშნისთანავე. პარკინგში შესვლა ხდება მიმღებში გაცემული ბარათით.` | `ლოკაცია და ტრანსფერი` | `პარკინგი, მანქანა, ავტოსადგომი, უფასოა, ადგილი` | `საჯარო` |
| 7 | `აწყობთ თუ არა ტრანსფერს აეროპორტიდან?` | `დიახ. ტრანსფერი აეროპორტიდან სასტუმრომდე ღირს 60 ლარი ერთი მიმართულებით, ავტომობილი 3 მგზავრამდე. დაჯავშნეთ მინიმუმ 12 საათით ადრე და მოგვწერეთ ფრენის ნომერი — მძღოლი დაგხვდებათ სახელიან ტაბლოსთან ჩამოსვლის დარბაზში.` | `ლოკაცია და ტრანსფერი` | `ტრანსფერი, აეროპორტი, მძღოლი, ტაქსი, შემხვედრი` | `საჯარო` |
| 8 | `უფასოა თუ არა ბავშვის განთავსება?` | `6 წლამდე ბავშვი უფასოდ თავსდება მშობლების ნომერში არსებულ საწოლზე. საბავშვო საწოლი უფასოა და საჭიროა წინასწარ მოთხოვნა. 6-დან 12 წლამდე ბავშვისთვის დამატებითი საწოლი ღირს 40 ლარი ღამეში, საუზმის ჩათვლით.` | `ნომრები და სერვისი` | `ბავშვი, საწოლი, უფასო, ოჯახი, დამატებითი ადგილი` | `საჯარო` |
| 9 | `როგორია ინტერნეტი და როგორ დავუკავშირდე?` | `Wi-Fi უფასოა და ხელმისაწვდომია მთელ სასტუმროში, ნომრებშიც და საერთო სივრცეებშიც. ქსელის სახელი და პაროლი მითითებულია ნომრის ბარათზე და მიმღებთანაც მოგცემენ. თუ სიგნალი სუსტია, დაგვირეკეთ მიმღებში — გამოგიგზავნით თანამშრომელს გამაძლიერებლით.` | `ნომრები და სერვისი` | `wifi, ინტერნეტი, პაროლი, ქსელი, უფასოა` | `საჯარო` |
| 10 | `რა ფორმით შემიძლია გადახდა?` | `ვიღებთ ნაღდ ანგარიშსწორებას ლარში, ასევე Visa და Mastercard ბარათებს. კომპანიისთვის შესაძლებელია უნაღდო ანგარიშსწორება ინვოისით — ამისთვის დაგვიკავშირდით ჩამოსვლამდე. დაჯავშნისას ბარათი გამოიყენება მხოლოდ გარანტიისთვის, თანხა ჩამოიჭრება ჩექ-აუთისას.` | `გადახდა` | `გადახდა, ბარათი, ნაღდი, ინვოისი, ანგარიშსწორება` | `საჯარო` |
| 11 | `ოპერატორის უფლება ფასდაკლებასა და აფგრეიდზე` | `მიმღების თანამშრომელს შეუძლია დამოუკიდებლად შესთავაზოს უფასო აფგრეიდი შემდეგი კატეგორიის ნომერზე, თუ ის თავისუფალია და სტუმარი 3 ღამეზე მეტს რჩება. პირდაპირი დაჯავშნისას ფასდაკლების ზღვარია 10 პროცენტი. ამაზე მეტი — მხოლოდ მენეჯერის თანხმობით. ფასდაკლება არ ეთქმება სტუმარს ტელეფონით, სანამ თანხმობა არ არის მიღებული.` | `შიდა პროცედურა` | `ფასდაკლება, აფგრეიდი, უფლებამოსილება, ზღვარი, მენეჯერი` | `შიდა` |
| 12 | `როგორ ვმოქმედებთ ოვერბუქინგის დროს` | `თუ ნომერი ვერ გამოთავისუფლდა, სტუმარი თავსდება იმავე ან უფრო მაღალი კატეგორიის ნომერში ჩვენს პარტნიორ სასტუმროში, ჩვენივე ხარჯით, ტრანსფერის ჩათვლით. სტუმარს არ ეთქმება „ადგილი აღარ არის“ — ეთქმება, რომ დღეს მას სხვა სასტუმროში ვათავსებთ ჩვენი ხარჯით და ხვალ ვაბრუნებთ. მენეჯერი ინფორმირდება მაშინვე.` | `შიდა პროცედურა` | `ოვერბუქინგი, ადგილი არ არის, გადაყვანა, ესკალაცია` | `შიდა` |

### C.6 ზოგადი (fallback when `industry = სხვა`) — 8 rows

Category list `L.CAT.ზოგადი`: `მომსახურება`, `გადახდა`, `შეკვეთა`, `დაბრუნება`,
`კონტაქტი`, `შიდა პროცედურა`

| # | კითხვა | პასუხი | კატეგორია | ტეგები | ხილვადობა |
|---|---|---|---|---|---|
| 1 | `როგორია თქვენი სამუშაო საათები?` | `ვმუშაობთ ორშაბათიდან პარასკევის ჩათვლით, 09:00-დან 18:00 საათამდე; შაბათს — 10:00-დან 15:00 საათამდე. კვირას და უქმე დღეებში დაკეტილია. წერილებს ელფოსტაზე ვპასუხობთ ერთი სამუშაო დღის განმავლობაში.` | `კონტაქტი` | `სამუშაო საათები, როდის მუშაობთ, ღიაა, უქმე დღეები` | `საჯარო` |
| 2 | `როგორ დაგიკავშირდეთ?` | `დაგვირეკეთ ცხელ ხაზზე, მოგვწერეთ ელფოსტით ან ჩვენს Facebook გვერდზე. ჩატში პასუხობს ჯერ ავტომატური ასისტენტი და, საჭიროების შემთხვევაში, საუბარს ოპერატორს გადასცემს.` | `კონტაქტი` | `კონტაქტი, ნომერი, ელფოსტა, როგორ დაგიკავშირდეთ` | `საჯარო` |
| 3 | `რა ფორმით შემიძლია გადახდა?` | `ვიღებთ ნაღდ ანგარიშსწორებას, ბარათს და გადარიცხვას. კომპანიებისთვის შესაძლებელია ინვოისით გადახდა 5 სამუშაო დღის განმავლობაში. გადახდის დამადასტურებელი დოკუმენტი ავტომატურად მოგდით ელფოსტაზე.` | `გადახდა` | `გადახდა, ბარათი, ნაღდი, ინვოისი, გადარიცხვა` | `საჯარო` |
| 4 | `როგორ გავიგო ჩემი შეკვეთის სტატუსი?` | `სტატუსი ჩანს პირად კაბინეტში, განყოფილებაში „ჩემი შეკვეთები“. ყოველი ეტაპის შეცვლისას ავტომატურ SMS-საც იღებთ. თუ სტატუსი ორ დღეზე მეტხანს არ იცვლება, დაგვირეკეთ და შევამოწმებთ.` | `შეკვეთა` | `სტატუსი, შეკვეთა, სად არის, როდის მოვა, თვალყური` | `საჯარო` |
| 5 | `შემიძლია თუ არა შეკვეთის დაბრუნება?` | `დაბრუნება შესაძლებელია მიღებიდან 14 დღის განმავლობაში, თუ ნივთი გამოუყენებელია და შენარჩუნებულია შეფუთვა. თანხა ბრუნდება იმავე ფორმით, რომლითაც გადაიხადეთ, 5 სამუშაო დღეში. ტრანსპორტირების ხარჯს ვფარავთ ჩვენ, თუ ნივთი დაზიანებული ან არასწორი მოგივიდათ.` | `დაბრუნება` | `დაბრუნება, თანხის დაბრუნება, გაცვლა, არ მომწონს, ვადა` | `საჯარო` |
| 6 | `რა ვადაში პასუხობთ საჩივარს?` | `წერილობით საჩივარს ვიხილავთ 10 სამუშაო დღეში და პასუხს გიგზავნით ელფოსტით. თუ საკითხს დამატებითი შემოწმება სჭირდება, გაცნობებთ ვადის გაგრძელების შესახებ. საჩივრის დაფიქსირება შეგიძლიათ ვებგვერდიდან ან ცხელ ხაზზე.` | `მომსახურება` | `საჩივარი, პრეტენზია, უკმაყოფილო, პასუხი, ვადა` | `საჯარო` |
| 7 | `ოპერატორის უფლებამოსილება დათმობებზე` | `ოპერატორს შეუძლია დამოუკიდებლად შესთავაზოს კლიენტს მიწოდების ხარჯის ჩამოწერა ან 20 ლარამდე ერთჯერადი კომპენსაცია, თუ შეცდომა ჩვენი მხრიდანაა. ამაზე მეტი საჭიროებს ცვლის უფროსის თანხმობას. კლიენტს დათმობა არ ეპირება მანამ, სანამ თანხმობა არ არის მიღებული.` | `შიდა პროცედურა` | `დათმობა, კომპენსაცია, ზღვარი, უფლებამოსილება` | `შიდა` |
| 8 | `როდის გადავცემთ ზარს ხელმძღვანელს` | `ზარი გადაეცემა ცვლის უფროსს, თუ კლიენტი მოითხოვს ხელმძღვანელს, თუ ახსენებს სასამართლოს, ადვოკატს ან მედიას, ან თუ იგივე საკითხზე მესამედ რეკავს. გადაცემამდე ოპერატორი მოკლედ აჯამებს საკითხს ჩატში, რომ კლიენტს ამბის თავიდან მოყოლა არ დასჭირდეს.` | `შიდა პროცედურა` | `ესკალაცია, ხელმძღვანელი, გადაცემა, საჩივარი, ცვლის უფროსი` | `შიდა` |

---

## D. Authoring rules — Georgian copy for the guide

Each rule ships as: heading, body, `ასე არა` example, `ასე კი` example. IDs `GUIDE.R1` … `GUIDE.R8`.

### `GUIDE.R1` — one row = one answer

**Heading:** `ერთი სტრიქონი — ერთი პასუხი`

**Body:**
```
სისტემა თქვენს ცოდნის ბაზაში ეძებს არა მთელ დოკუმენტს, არამედ ცალკეულ ნაწილს. თუ ერთ სტრიქონში ჩაწერთ ერთ კითხვას და მის სრულ პასუხს, ძებნის ერთეული ზუსტად ემთხვევა კითხვის ერთეულს და პასუხი ყოველთვის ზუსტად იპოვება.

თუ ერთ უჯრაში ჩააგდებთ მთელ დებულებას, სისტემა მას იძულებით დაყოფს დაახლოებით ერთი გვერდის ნაწილებად და დაყოფის ადგილი ხშირად წინადადების შუაში მოხვდება. მაშინ კლიენტს პასუხის ნახევარი მიუვა, ან სულ სხვა პუნქტი.
```

**`ასე არა`:**
```
კითხვა: დაბრუნების პოლიტიკა
პასუხი: 1. ნივთის დაბრუნება შესაძლებელია 14 დღეში. 2. ნივთი უნდა იყოს გამოუყენებელი. 3. თანხა ბრუნდება 5 დღეში. 4. ტრანსპორტირებას ვფარავთ, თუ ნივთი დაზიანებულია. 5. აქციით შეძენილი ნივთი არ ბრუნდება. 6. საჩივრის ვადაა 10 დღე. 7. …
```

**`ასე კი`:**
```
კითხვა: რა ვადაში შემიძლია ნივთის დაბრუნება?
პასუხი: დაბრუნება შესაძლებელია მიღებიდან 14 დღის განმავლობაში, თუ ნივთი გამოუყენებელია და შეფუთვა შენარჩუნებულია.

კითხვა: რამდენ ხანში დამიბრუნდება თანხა?
პასუხი: თანხა ბრუნდება იმავე ფორმით, რომლითაც გადაიხადეთ, 5 სამუშაო დღეში.

კითხვა: აქციით შეძენილი ნივთი ბრუნდება?
პასუხი: აქციით ან ფასდაკლებით შეძენილი ნივთი დაბრუნებას არ ექვემდებარება, თუმცა ზომის გაცვლა შესაძლებელია 14 დღეში.
```

### `GUIDE.R2` — write the customer's question, not the company's wording

**Heading:** `დაწერეთ ისე, როგორც კლიენტი კითხულობს`

**Body:**
```
სისტემა კლიენტის სიტყვებს ადარებს თქვენს სიტყვებს. თუ კითხვა შიდა ტერმინითაა ჩაწერილი, ხოლო კლიენტი სულ სხვა სიტყვას ამბობს, დამთხვევა სუსტი იქნება და პასუხი შეიძლება საერთოდ ვერ მოიძებნოს.
```

**`ასე არა`:**
```
კითხვა: სააბონენტო მომსახურების ყოველთვიური საფასურის ინდექსაცია
```

**`ასე კი`:**
```
კითხვა: რატომ გამეზარდა ყოველთვიური გადასახადი?
```

### `GUIDE.R3` — put the customer's own words in `ტეგები`

**Heading:** `სვეტში „ტეგები“ ჩაწერეთ ის სიტყვები, რომლებსაც კლიენტები ნამდვილად ამბობენ`

**Body:**
```
სვეტი „ტეგები (მძიმით)“ არ არის ფორმალობა — ისიც ისევე მონაწილეობს ძებნაში, როგორც კითხვა და პასუხი. ჩაწერეთ იქ ყველა სხვა ფორმულირება, რომლითაც ერთსა და იმავე კითხვას გისვამენ: სასაუბრო ვარიანტები, რუსული ან ინგლისური სიტყვები, გავრცელებული შეცდომებიც კი. ეს ერთი სვეტი ხშირად უფრო მეტს ცვლის, ვიდრე პასუხის გადაწერა.
```

**`ასე არა`:**
```
ტეგები: ბარათი
```

**`ასე კი`:**
```
ტეგები: დაკარგული ბარათი, მოპარეს ბარათი, ბლოკი, დაბლოკვა, ბარათი ვერ ვიპოვე, карта, blocked
```

### `GUIDE.R4` — every answer stands alone

**Heading:** `თითოეული პასუხი ცალკე უნდა იკითხებოდეს`

**Body:**
```
სისტემა კლიენტს აჩვენებს მხოლოდ ერთ სტრიქონს — არა მთელ ცხრილს და არა წინა სტრიქონს. ამიტომ პასუხში არ უნდა იყოს მითითება სხვა ადგილზე. თუ პასუხის გასაგებად წინა სტრიქონია საჭირო, გაიმეორეთ საჭირო ინფორმაცია.
```

**`ასე არა`:**
```
პასუხი: იგივე პირობები მოქმედებს, რაც ზემოთ აღწერილ შემთხვევაში, ოღონდ ვადა ორჯერ ნაკლებია.
```

**`ასე კი`:**
```
პასუხი: პრემიუმ პაკეტზე გაუქმება უფასოა ჩამოსვლამდე 24 საათით ადრე; ამის შემდეგ ირიცხება ერთი ღამის საფასური.
```

### `GUIDE.R5` — volatile facts get their own short rows

**Heading:** `ფასები და ვადები ცალკე, მოკლე სტრიქონებად`

**Body:**
```
ის, რაც ხშირად იცვლება — ფასი, საკომისიო, ვადა, აქცია — ჩაწერეთ ცალკე მოკლე სტრიქონად. მაშინ განახლება ერთი უჯრის შეცვლაა და არა მთელი ტექსტის თავიდან წაკითხვა. თუ იგივე რიცხვი ხუთ სხვადასხვა პასუხშია ჩაწერილი, ერთხელაც აუცილებლად დაგრჩებათ ძველი ციფრი — და სისტემა სწორედ იმ ძველ ციფრს ეტყვის კლიენტს.
```

**`ასე არა`:**
```
პასუხი: მომსახურება მოიცავს კონსულტაციას, დიაგნოსტიკას და დასკვნას; კონსულტაცია ღირს 60 ლარი, ხოლო განმეორებითი ვიზიტი 40 ლარი, თუმცა დაზღვევის შემთხვევაში ფასი განსხვავდება…
```

**`ასე კი`:**
```
კითხვა: რა ღირს კონსულტაცია?
პასუხი: თერაპევტის კონსულტაცია ღირს 60 ლარი, განმეორებითი ვიზიტი 30 დღის განმავლობაში — 40 ლარი.
შენიშვნა CommuniQ-სთვის: ფასები ახლდება ყოველი წლის იანვარში.
```

### `GUIDE.R6` — what must never be `საჯარო`

**Heading:** `რა არ უნდა მოხვდეს საჯარო ჩანაწერში`

**Body:**
```
„საჯარო“ ნიშნავს, რომ ამ ტექსტს ბოტმა შეიძლება სიტყვასიტყვით ათქვას კლიენტთან. ამიტომ საჯარო არ უნდა იყოს:

— ოპერატორის უფლებამოსილება: რამდენს ჩამოწერს, რა ფასდაკლებას იძლევა, სად არის ზღვარი;
— შიდა ესკალაციის წესები და ის, თუ როდის ვთვლით საქმეს საეჭვოდ;
— თანამშრომლების სახელები, შიდა ნომრები, ჩატის სახელწოდებები;
— თვითღირებულება, მოგების მარჟა, პარტნიორებთან შეთანხმებული პირობები;
— კონკრეტული კლიენტების მონაცემები, ნებისმიერი პირადი ნომერი ან ბარათის ნომერი;
— ის, რაც ჯერ არ გამოგიცხადებიათ: ახალი ტარიფი, დაგეგმილი ცვლილება, აქცია.

ეს ინფორმაცია ცოდნის ბაზაში მაინც უნდა იყოს — ის ოპერატორს სჭირდება. უბრალოდ, სვეტში „ხილვადობა“ დააყენეთ „შიდა“. თუ ეჭვი გეპარებათ, დააყენეთ „შიდა“: შემდეგ გამოქვეყნება ერთი წამის საქმეა, უკან წაშლა კი — უკვე არა.
```

**`ასე არა`:**
```
კითხვა: რა ფასდაკლება შემიძლია მივიღო?
პასუხი: ოპერატორს შეუძლია 10%-მდე ფასდაკლება, მენეჯერს კი 25%-მდე.
ხილვადობა: საჯარო
```

**`ასე კი`:**
```
კითხვა: მაქვს თუ არა ფასდაკლების მიღების შესაძლებლობა?
პასუხი: მოქმედი აქციები და ფასდაკლებები გამოქვეყნებულია ჩვენს ვებგვერდზე განყოფილებაში „შეთავაზებები“. თუ გრძელვადიან თანამშრომლობას გეგმავთ, ინდივიდუალურ პირობებზე დაგვიკავშირდით.
ხილვადობა: საჯარო

კითხვა: ოპერატორის უფლება ფასდაკლებაზე
პასუხი: ოპერატორს შეუძლია დამოუკიდებლად შესთავაზოს 10%-მდე ფასდაკლება; 25%-მდე — მენეჯერის თანხმობით. კლიენტს ფასდაკლება არ ეპირება თანხმობის მიღებამდე.
ხილვადობა: შიდა
```

### `GUIDE.R7` — when the answer depends on segment, branch or tariff

**Heading:** `როცა პასუხი პაკეტზე, ფილიალზე ან სეგმენტზეა დამოკიდებული`

**Body:**
```
თუ ერთსა და იმავე კითხვაზე პასუხი განსხვავდება, ორი გზა გაქვთ. თუ ვარიანტები ორი ან სამია, დაწერეთ ცალკე სტრიქონები, თითოეული თავისი კითხვით — ასე უფრო ზუსტად იპოვება. თუ ვარიანტი ბევრია, დაწერეთ ერთი სტრიქონი, რომელშიც ყველა ვარიანტი ჩამოთვლილია მოკლედ და ერთ ადგილას.

არასოდეს დაწეროთ ისე, თითქოს ერთი პასუხი ყველასთვის მოქმედებდეს — სწორედ აქედან იბადება ყველაზე ხშირი მცდარი ინფორმაცია.
```

**`ასე არა`:**
```
პასუხი: გადარიცხვა უფასოა.
```

**`ასე კი`:**
```
კითხვა: რა ღირს გადარიცხვა სტანდარტულ პაკეტზე?
პასუხი: სტანდარტულ პაკეტზე გადარიცხვა სხვა ბანკში ღირს 0,5%, მინიმუმ 1 ლარი.

კითხვა: რა ღირს გადარიცხვა პრემიუმ პაკეტზე?
პასუხი: პრემიუმ პაკეტზე გადარიცხვა ყველა ქართულ ბანკში უფასოა.
```

### `GUIDE.R8` — how much is enough, and where to start

**Heading:** `საიდან დაიწყოთ და როდის გაჩერდეთ`

**Body:**
```
არ დაიწყოთ დოკუმენტების გადმოწერით. დაიწყეთ იმ ოცი კითხვით, რომელსაც ოპერატორები ყოველდღე პასუხობენ — უბრალოდ ჰკითხეთ მათ, ან გადახედეთ ბოლო კვირის ჩატებს. ეს ოცი სტრიქონი უფრო მეტს იძლევა, ვიდრე ორასგვერდიანი დებულება.

კარგი საწყისი მოცულობაა 40-დან 80 სტრიქონამდე. 20-ზე ნაკლებით სისტემა ხშირად ვერ იპოვის პასუხს და ბოტი ხშირად იტყვის უარს. მოცულობა შემდეგაც იზრდება: სისტემა თავად აჩვენებს, რომელ კითხვებზე ვერ პოულობს პასუხს, და თქვენ მხოლოდ იმ ხარვეზებს შეავსებთ.
```

---

## E. Bot settings and analysis emphases — additional copy

All labels, hints and example values for Sheet 6 and Sheet 7 are specified inline in §3
(`S6.*`, `S7.*`). Additional strings the guide reuses:

**`GUIDE.BOT.H`**: `ბოტი მხოლოდ იმას ამბობს, რაც თქვენ დაწერეთ`
**`GUIDE.BOT.P`**
```
ბოტი არაფერს იგონებს. ის პასუხობს მხოლოდ იმ სტრიქონებით, რომლებიც ფურცელზე „ხშირი კითხვები“ მონიშნეთ როგორც „საჯარო“. თუ პასუხს ვერ პოულობს, ის ამბობს თქვენს „უარის ტექსტს“ და საუბარს ადამიანს გადასცემს. სწორედ ამიტომ ორი რამ ყველაზე მნიშვნელოვანია: რამდენი საჯარო სტრიქონი გაქვთ და როგორ არის დაწერილი უარის ტექსტი.
```

**`GUIDE.BOT.REFUSAL.H`**: `უარის ტექსტი — ყველაზე ხშირად წაკითხული წინადადება`
**`GUIDE.BOT.REFUSAL.P`**
```
დასაწყისში ეს ტექსტი კლიენტებმა შეიძლება ყველა სხვა პასუხზე მეტჯერ ნახონ. ის უნდა იყოს მოკლე, არ უნდა იხდიდეს ორჯერ ბოდიშს და აუცილებლად უნდა სთავაზობდეს შემდეგ ნაბიჯს — ცოცხალ ადამიანს.
```
**`GUIDE.BOT.REFUSAL.BAD`**
```
უკაცრავად, ბოდიში, სამწუხაროდ ვერ დაგეხმარებით. სცადეთ მოგვიანებით.
```
**`GUIDE.BOT.REFUSAL.GOOD`**
```
ამ კითხვაზე ზუსტი პასუხი ჩემთან არ არის და გამოცნობა არ მინდა. ახლავე დაგაკავშირებთ ოპერატორს, რომელიც დაგეხმარებათ.
```

**`GUIDE.EMPH.H`**: `ანალიზის აქცენტები — რას გამოგადგებათ`
**`GUIDE.EMPH.P`**
```
ეს ფურცელი გავლენას ახდენს იმაზე, რას ხედავთ ზარის შეჯამებაში. ყველაზე მეტ სარგებელს იძლევა შუა სვეტი — სავალდებულო ფრაზები. თუ იქ ჩაწერთ, რაც ოპერატორმა აუცილებლად უნდა თქვას, ჩვენ ამას შევამოწმებთ ყოველ ზარზე და, თუ გნებავთ, ცალკე განზომილებადაც დავამატებთ თქვენს რუბრიკაში.
```

---

## F. Validation rules

The validator reads a returned workbook and prints a Georgian report for the **owner**, who forwards
the relevant lines to the customer. Two severities:

- **`შეცდომა`** — blocks provisioning.
- **`გაფრთხილება`** — provisioning proceeds; the owner decides whether to ask.
- **`ინფორმაცია`** — no action needed; explains what default will apply.

Placeholders: `{sheet}` sheet tab name, `{row}` 1-based Excel row number, `{col}` header text,
`{total}`, `{n}`, `{value}`, `{lang}`. Every message names the sheet, the row and the fix.

**Report header `V.HEADER`:**
```
ფაილის შემოწმების შედეგი: {errors} შეცდომა, {warnings} გაფრთხილება. შეცდომების გასწორების გარეშე სისტემას ვერ ჩავრთავთ; გაფრთხილებები არჩევითია, მაგრამ სასურველია.
```
**`V.OK`:**
```
ფაილი წესრიგშია — შეცდომა არ არის. შეგვიძლია დანერგვა დავიწყოთ.
```

| ID | Severity | Condition | Georgian message |
|---|---|---|---|
| `V01` | შეცდომა | no `_მეტა` sheet / not a CQ workbook | `ეს ფაილი არ არის CommuniQ-ის კითხვარი, ან შენახვისას სტრუქტურა დაზიანდა. გთხოვთ, ჩამოტვირთოთ ორიგინალი ფაილი და ხელახლა შეავსოთ — ან გამოგვიგზავნოთ ის, რაც გაქვთ, და ჩვენ გადმოვიტანთ.` |
| `V02` | გაფრთხილება | `_მეტა.version` ≠ current | `ფაილი შევსებულია კითხვარის ძველი ვერსიით ({value}). შემოწმებას მაინც გავაკეთებთ, მაგრამ შესაძლოა რამდენიმე ველი ახლა სხვაგვარად ერქვას.` |
| `V03` | შეცდომა | a required sheet is missing | `ფაილს აკლია ფურცელი „{sheet}“. სავარაუდოდ შემთხვევით წაიშალა. გამოგვიგზავნეთ ორიგინალი ფაილი და ჩვენ დაგეხმარებით აღდგენაში.` |
| `V04` | შეცდომა | `S2.F.NAME` empty | `ფურცელზე „კომპანია“ არ არის შევსებული კომპანიის სახელი. ეს ერთადერთი ველია, რომლის გარეშეც ვერაფერს დავიწყებთ.` |
| `V05` | შეცდომა | `S2.F.INDUSTRY` empty or not in `L.INDUSTRY` | `ფურცელზე „კომპანია“ ინდუსტრია არ არის არჩეული. აირჩიეთ ჩამოსაშლელი სიიდან ყველაზე ახლო ვარიანტი; თუ ვერცერთი გიხდებათ, აირჩიეთ „სხვა“.` |
| `V06` | შეცდომა | `S2.F.EMAIL` empty or not email-shaped | `ფურცელზე „კომპანია“ საკონტაქტო ელფოსტა არ არის ან არასწორადაა ჩაწერილი: „{value}“. სწორ მისამართს უნდა ჰქონდეს სახე მაგალითად: name@company.ge` |
| `V07` | შეცდომა | user row has a name or email but role blank/invalid | `ფურცელზე „კომპანია“, სტრიქონი {row}: ამ ადამიანს არ აქვს არჩეული როლი. აირჩიეთ „მფლობელი“ ან „წევრი“, ან წაშალეთ სტრიქონი.` |
| `V08` | შეცდომა | duplicate user email | `ფურცელზე „კომპანია“ ელფოსტა „{value}“ ორჯერ წერია (სტრიქონები {row}). ერთი ელფოსტით მხოლოდ ერთი მომხმარებელი იქმნება — წაშალეთ დუბლიკატი ან ჩაწერეთ სხვა მისამართი.` |
| `V09` | გაფრთხილება | `S2.F.LANG` empty | `ფურცელზე „კომპანია“ არ არის მითითებული ზარების ძირითადი ენა. ვივარაუდებთ ქართულს.` |
| `V10` | გაფრთხილება | no user rows at all | `ფურცელზე „კომპანია“ არავინაა ჩაწერილი, ვისაც პორტალზე შესვლა სჭირდება. შესვლას შევუქმნით მხოლოდ საკონტაქტო პირს; დანარჩენებს მოგვიანებით დავამატებთ.` |
| `V11` | გაფრთხილება | rubric sheet has zero `დიახ` rows | `ფურცელზე „შეფასების რუბრიკა“ არცერთი განზომილება არ არის ჩართული. ზარები მაინც გადაიწერება და გაანალიზდება, მაგრამ ქულებს ვერ დავთვლით. თუ ეს განზრახ არის — არაფერი გიშავთ, რუბრიკას მოგვიანებითაც დავამატებთ.` |
| `V12` | შეცდომა | row has weight or guidance but empty name | `ფურცელზე „შეფასების რუბრიკა“, სტრიქონი {row}: შევსებულია წონა ან მითითება, მაგრამ განზომილების სახელი ცარიელია. ჩაწერეთ სახელი ან წაშალეთ სტრიქონი.` |
| `V13` | შეცდომა | weight not numeric, negative, or > 100 | `ფურცელზე „შეფასების რუბრიკა“, სტრიქონი {row}: წონა უნდა იყოს რიცხვი 0-დან 100-მდე. ახლა წერია „{value}“. პროცენტის ნიშანი და ტექსტი არ ჩაწეროთ — მხოლოდ რიცხვი.` |
| `V14` | შეცდომა | some weights filled, some blank | `ფურცელზე „შეფასების რუბრიკა“ ნაწილს წონა აქვს, ნაწილს — არა. ან ყველა ჩართულ განზომილებას მიუთითეთ წონა ისე, რომ ჯამი 100 იყოს, ან წაშალეთ ყველა წონა და ჩვენ თანაბრად გავანაწილებთ.` |
| `V15` | შეცდომა | total ≠ 100 (±0.5) and not all blank | *(exactly `S3.ERR.TOTAL`)* `ფურცელზე „შეფასების რუბრიკა“ წონების ჯამია {total}, უნდა იყოს ზუსტად 100. შეცვალეთ რომელიმე წონა ისე, რომ ჯამმა 100 შეადგინოს — ან წაშალეთ ყველა წონა და ჩვენ თანაბრად გავანაწილებთ.` |
| `V16` | ინფორმაცია | all weights blank | `ფურცელზე „შეფასების რუბრიკა“ წონები არ არის შევსებული. ყველა განზომილებას თანაბარ წონას მივანიჭებთ — {n} განზომილება, თითო {value}.` |
| `V17` | შეცდომა | more than 30 enabled dimensions | `ფურცელზე „შეფასების რუბრიკა“ ჩართულია {n} განზომილება. მაქსიმუმია 30, პრაქტიკაში კი საუკეთესო შედეგს 5-დან 8-მდე იძლევა. სვეტში „გამოვიყენოთ?“ დააყენეთ „არა“ ნაკლებად მნიშვნელოვანებზე.` |
| `V18` | შეცდომა | duplicate dimension names among enabled rows | `ფურცელზე „შეფასების რუბრიკა“ სახელი „{value}“ ორჯერ წერია (სტრიქონები {row}). თითოეულ განზომილებას უნდა ჰქონდეს განსხვავებული სახელი.` |
| `V19` | გაფრთხილება | enabled row with empty guidance | `ფურცელზე „შეფასების რუბრიკა“, სტრიქონი {row} („{value}“): შეფასების მითითება ცარიელია. ეს ის ტექსტია, რომლითაც სისტემა ქულას ადგენს — მის გარეშე ქულა შემთხვევითი გამოვა. დაწერეთ, რა იძლევა მაღალ და რა დაბალ ქულას.` |
| `V20` | გაფრთხილება | guidance shorter than 40 chars, or matches the vague-phrase list | `ფურცელზე „შეფასების რუბრიკა“, სტრიქონი {row} („{value}“): მითითება ძალიან ზოგადია. დაწერეთ ის, რაც ჩანაწერში ისმის — მაგალითად, „მიესალმა და დაასახელა კომპანია“, „არ შეაწყვეტინა“, „დაასახელა ზუსტი ვადა“ — და არა „იყოს თავაზიანი“.` |
| `V21` | გაფრთხილება | more than 8 enabled dimensions (but ≤ 30, so `V17` did not fire) | `ფურცელზე „შეფასების რუბრიკა“ ჩართულია {n} განზომილება. ეს იმუშავებს, მაგრამ 8-ზე მეტი განზომილება ქულას ძნელად წასაკითხს ხდის. გირჩევთ, ყველაზე მნიშვნელოვანი 5–8 დატოვოთ.` |
| `V22` | შეცდომა | FAQ sheet has zero usable rows **and** documents sheet is empty | `ცოდნის ბაზა ცარიელია: ფურცელზე „ხშირი კითხვები“ არცერთი სტრიქონი არ არის შევსებული. ამის გარეშე სისტემა ვერ შეამოწმებს, ოპერატორმა სწორი ინფორმაცია თქვა თუ არა, და ბოტიც ვერ იმუშავებს. საკმარისია 20 ყველაზე ხშირი კითხვა.` |
| `V23` | შეცდომა | question filled, answer empty (or the reverse) | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: შევსებულია მხოლოდ ერთი უჯრა. კითხვაც და პასუხიც ორივე უნდა იყოს შევსებული — ან შეავსეთ მეორეც, ან წაშალეთ სტრიქონი.` |
| `V24` | შეცდომა | `ხილვადობა` empty or not in `L.VIS` | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: არ არის არჩეული ხილვადობა. აირჩიეთ „საჯარო“, თუ ამ პასუხს კლიენტი უნდა ხედავდეს, ან „შიდა“, თუ ის მხოლოდ თანამშრომლებისთვისაა.` |
| `V25` | გაფრთხილება | answer shorter than 15 characters | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: პასუხი ძალიან მოკლეა („{value}“). დაწერეთ სრული წინადადება — კლიენტი ზუსტად ამ ტექსტს დაინახავს.` |
| `V26` | გაფრთხილება | answer longer than 1000 characters | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: პასუხი ძალიან გრძელია და სისტემა მას ნაწილებად დაყოფს, შესაძლოა წინადადების შუაში. დაყავით რამდენიმე ცალკე კითხვად — ასე პასუხი ბევრად უკეთ იპოვება.` |
| `V27` | გაფრთხილება | duplicate or near-duplicate questions | `ფურცელზე „ხშირი კითხვები“ სტრიქონები {row} ერთსა და იმავე კითხვას იმეორებს. დატოვეთ ერთი, ყველაზე სრული პასუხით — ორი მსგავსი სტრიქონი ერთმანეთს უშლის ხელს ძებნისას.` |
| `V28` | გაფრთხილება | a shipped template row returned byte-identical | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: ეს ჩვენი ნიმუშის სტრიქონია და უცვლელად დარჩა. თუ ეს პასუხი თქვენც ზუსტად ასე მოქმედებს, ყველაფერი რიგზეა — უბრალოდ დაგვიდასტურეთ. თუ არა, შეცვალეთ ან წაშალეთ, რომ სისტემამ პირობითი ინფორმაცია არ თქვას.` |
| `V29` | გაფრთხილება | a `საჯარო` row matches the internal-marker list (`უფლებამოსილება`, `ზღვარი`, `ოპერატორს შეუძლია`, `შიდა`, `მარჟა`, `თვითღირებულება`, `ცვლის უფროსი`, `ესკალაცია`) | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: ეს პასუხი მონიშნულია როგორც „საჯარო“, მაგრამ შიდა წესს ჰგავს (ნახსენებია „{value}“). გადაამოწმეთ — თუ ეს ტექსტი მხოლოდ თანამშრომლისთვისაა, დააყენეთ „შიდა“.` |
| `V30` | გაფრთხილება | answer contains a placeholder (`[`, `XXX`, `___`, `მიუთითეთ`) | `ფურცელზე „ხშირი კითხვები“, სტრიქონი {row}: პასუხში დარჩა შესავსები ადგილი („{value}“). ჩაწერეთ თქვენი რეალური მონაცემი — სისტემა ტექსტს ისე იტყვის, როგორც წერია.` |
| `V31` | ინფორმაცია | fewer than 20 usable FAQ rows | `ფურცელზე „ხშირი კითხვები“ შევსებულია {n} სტრიქონი. ამით დავიწყებთ, მაგრამ სისტემა ხშირად ვერ იპოვის პასუხს. კარგი შედეგისთვის სასურველია 40-დან 80-მდე. მოგვიანებით ჩვენვე გაჩვენებთ, რომელი კითხვები აკლია.` |
| `V32` | ინფორმაცია | zero `საჯარო` rows | `ფურცელზე „ხშირი კითხვები“ ყველა სტრიქონი „შიდაა“. ეს დასაშვებია — ოპერატორები ისარგებლებენ — მაგრამ ბოტი ვერ ჩაირთვება, სანამ ერთი მაინც არ გამოქვეყნდება.` |
| `V33` | შეცდომა | document row: title filled, text empty (or the reverse) | `ფურცელზე „წესები და დოკუმენტები“, სტრიქონი {row}: სათაური ან ტექსტი აკლია. ორივე უნდა იყოს შევსებული — ან შეავსეთ, ან წაშალეთ სტრიქონი.` |
| `V34` | ინფორმაცია | document text longer than 1000 chars | `ფურცელზე „წესები და დოკუმენტები“, სტრიქონი {row}: ტექსტი გრძელია და ჩვენ მას ნაწილებად დავყოფთ. ეს ნორმალურია. თუ გინდათ, პასუხები უფრო ზუსტად იპოვებოდეს, ეს ტექსტი ცალკე კითხვებად დაშალეთ ფურცელზე „ხშირი კითხვები“.` |
| `V35` | გაფრთხილება | a file is listed in the attachments block | `ფურცელზე „წესები და დოკუმენტები“ ჩამოწერილია ფაილი „{value}“. გთხოვთ, დაურთოთ იმავე წერილს — ჩვენთან ის ჯერ არ მოსულა.` |
| `V36` | შეცდომა | `S6.F.AUTO` = yes and no refusal text for any enabled language | `ფურცელზე „ბოტის პარამეტრები“ მონიშნეთ, რომ ბოტმა თავად უნდა უპასუხოს, მაგრამ „უარის ტექსტი“ {lang} ენაზე ცარიელია. ეს ის წინადადებაა, რომელსაც ბოტი ამბობს, როცა პასუხი არ იცის — მის გარეშე ბოტს ვერ ჩავრთავთ.` |
| `V37` | შეცდომა | `S6.F.AUTO` = yes and zero `საჯარო` FAQ rows and zero public documents | `ფურცელზე „ბოტის პარამეტრები“ მონიშნეთ, რომ ბოტმა თავად უნდა უპასუხოს, მაგრამ არცერთი პასუხი არ არის მონიშნული როგორც „საჯარო“. ბოტს მხოლოდ საჯარო ჩანაწერების თქმა შეუძლია, ამიტომ ის ყველა კითხვაზე უარს იტყოდა. მონიშნეთ „საჯარო“ იმ სტრიქონებზე, რომელთა ნახვის უფლებაც კლიენტს აქვს.` |
| `V38` | შეცდომა | `S6.F.AUTO` = yes and all three language toggles = `არა` | `ფურცელზე „ბოტის პარამეტრები“ არცერთი ენა არ არის მონიშნული. აირჩიეთ მინიმუმ ერთი ენა, რომელზეც ბოტი პასუხობს.` |
| `V39` | გაფრთხილება | a language is enabled but its greeting is empty | `ფურცელზე „ბოტის პარამეტრები“ {lang} ენა მონიშნულია, მაგრამ მისალმება ამ ენაზე ცარიელია. გამოვიყენებთ ნეიტრალურ მისალმებას; თუ გინდათ თქვენი ფორმულირება, ჩაწერეთ.` |
| `V40` | გაფრთხილება | persona empty while `S6.F.AUTO` = yes | `ფურცელზე „ბოტის პარამეტრები“ არ არის აღწერილი პერსონა. ბოტი ნეიტრალურად ილაპარაკებს. 2–3 წინადადება საკმარისია, რომ ის თქვენს ტონს დაემსგავსოს.` |
| `V41` | ინფორმაცია | Sheet 6 entirely empty | `ფურცელი „ბოტის პარამეტრები“ არ არის შევსებული. ბოტი კლიენტებს არ დაელაპარაკება; სამაგიეროდ ოპერატორები მიიღებენ პასუხის მონახაზებს. ეს უსაფრთხო საწყისი მდგომარეობაა და ნებისმიერ დროს შეიცვლება.` |
| `V42` | ინფორმაცია | Sheet 7 entirely empty | `ფურცელი „ანალიზის აქცენტები“ არ არის შევსებული. ზარებს სტანდარტულად გავაანალიზებთ — შეჯამება, განწყობა, თემები და სამოქმედო პუნქტები.` |
| `V43` | ინფორმაცია | mandatory phrases filled but no compliance-like dimension enabled | `ფურცელზე „ანალიზის აქცენტები“ ჩაწერეთ სავალდებულო ფრაზები, მაგრამ რუბრიკაში შესაბამისი განზომილება არ არის ჩართული. თუ გნებავთ, ჩავრთავთ განზომილებას „სავალდებულო გაფრთხილებები და პროცედურა“ — ასე ეს ფრაზები ქულაზეც აისახება.` |
| `V44` | გაფრთხილება | any cell matches a card-number / personal-ID / password pattern | `ფურცელზე „{sheet}“, სტრიქონი {row}: ტექსტი ჰგავს პირად ან საბანკო მონაცემს. გთხოვთ, წაშალოთ — ამ ფაილში კონკრეტული ადამიანების მონაცემები არ უნდა იყოს.` |

**Vague-guidance phrase list for `V20`** (case-insensitive substring match, whole guidance ≤ 80 chars):
```
იყოს თავაზიანი
კარგად იმუშაოს
პროფესიონალურად მოიქცეს
კლიენტზე ორიენტირებული
სწორად უპასუხოს
იყოს ყურადღებიანი
დადებითი განწყობა
ხარისხიანი მომსახურება
```

---

## G. Provisioning mapping (form → system)

This section is normative for the provisioning tool. It exists here because it depends on choices
made in this spec (column names, splitting, stripping).

### G.1 Tenant + users
- `S2.F.NAME` → `clients.name`. **Slug is derived by the tool, never asked.**
- `S2.F.INDUSTRY` → `clients.industry` (store the Georgian label verbatim).
- `S2.F.REGION` → `clients.region` (default `საქართველო` if blank).
- `S2.F.LANG`, `S2.F.LANG2`, `S2.F.CHANNELS`, `S2.F.AGENTS`, `S2.F.CALLS`, `S2.F.HOURS`,
  `S2.F.START` → `clients.settings` JSONB under key `onboarding`.
- Each user row → `tenant_users` (`username` = email local part or full email per existing
  convention; `role` per the §3 mapping). **Never generate or transmit a password from the form.**

### G.2 Rubric
- Enabled rows only (`გამოვიყენოთ? = დიახ`).
- `{name: <განზომილება>, weight: <წონა>, guidance: <შეფასების მითითება>}` — `key` omitted so
  `normalize_dimensions()` derives it via `_slug()`. `description` omitted.
- Column `ჩანაწერი` (E) is dropped.
- If all weights blank → send them blank and let `save_config` distribute evenly. Do **not**
  pre-compute the split in the tool; the server's behaviour is the contract.
- `rubric` (the free-text overall note) is left empty by the form; the operator may add one.

### G.3 KB — FAQ sheet
Group the usable rows by `ხილვადობა`, producing **up to two CSV imports**:

| Group | `title` | `doc_type` | `visibility` |
|---|---|---|---|
| `საჯარო` rows | `ხშირი კითხვები — საჯარო` | `faq` | `public` |
| `შიდა` rows | `ხშირი კითხვები — შიდა` | `faq` | `internal` |

CSV written for import — **header row exactly, in this order**:
```
კითხვა,პასუხი,კატეგორია,ტეგები
```
- **`ხილვადობა` is stripped** — it selects the group; leaving it in would embed the literal string
  `ხილვადობა: საჯარო` into every chunk.
- **`შენიშვნა CommuniQ-სთვის` is stripped** — internal-to-us, never embedded.
- `კითხვა`, `პასუხი`, `კატეგორია`, `ტეგები` are **kept**, because `csv_to_chunks()` renders them as
  `"header: value"` lines and the tag synonyms measurably improve retrieval. This is deliberate.
- Document `tags[]` = the distinct `კატეგორია` values in the group, capped at 20.
- Empty `კატეგორია` / `ტეგები` cells are omitted per row automatically (`csv_to_chunks` skips empty
  values), so no filler text is needed.

### G.4 KB — documents sheet
- One `kb_documents` row per filled sheet row: `title` = `სათაური`, text = `ტექსტი`,
  `doc_type` = `document`, `tags` = `კატეგორია` + `ტეგები (მძიმით)` split on commas,
  `visibility` per the row.
- Attachment block rows are printed in the operator report only; the files arrive by email and are
  imported by hand with the stated visibility.

### G.5 Bot config and analysis emphases
- Sheet 6 → `chat_configs` via the tenant bot-config endpoint: `persona`, `greeting{ka,en,ru}`,
  `refusal_copy{ka,en,ru}`, `languages` (from the three toggles), `canned` (from the mini-table),
  `autopilot_enabled` (only `დიახ — ბოტი თავად პასუხობს` maps to `True`), escalation keywords into
  `settings`. **`min_score`, `min_hits`, `top_k` are never written from the form** — the stored
  defaults stand.
- Sheet 7:
  - Mandatory + forbidden phrase lists → one `internal` KB document titled
    **`სავალდებულო და აკრძალული ფრაზები`**, `doc_type = document`, tags `[პროცედურა, ფრაზები]`,
    body built as two labelled sections using exactly these Georgian headings:
    ```
    ფრაზები, რომლებიც ოპერატორმა აუცილებლად უნდა თქვას
    ```
    ```
    ფრაზები, რომლებიც ოპერატორმა არ უნდა თქვას
    ```
  - `S7.F.MAIN`, the topics list and `S7.F.FLAG` → printed verbatim in the operator report under the
    heading `ანალიზის აქცენტები — ხელით გადასატანი`, because `analysis_instructions` is a global
    setting and merging it is a human decision.

### G.6 Order of operations
1. Create client → 2. create users → 3. import public FAQ CSV → 4. import internal FAQ CSV →
5. import documents → 6. save rubric → 7. save bot config (last, so `V37`'s public-document
precondition already holds).

---

## H. Open items for the build tracks

1. The workbook builder takes `--industry` and pre-fills Sheet 4 from §C; unknown/blank → §C.6.
2. The builder substitutes `{{CONTACT_NAME}}`, `{{CONTACT_EMAIL}}`, `{{CONTACT_PHONE}}` in `S1.P5`
   when given, and otherwise writes the literal placeholders.
3. `_სიები` and `_მეტა` must be `sheet_state = "hidden"`, never `"veryHidden"` — the owner may need
   to unhide them.
4. openpyxl caveat: `DataValidation` ranges must be added per sheet before `freeze_panes`; a
   validation applied to a whole column (`E7:E306`) is one object, not 300.
5. Nothing in this kit may ask for, store, or transmit a password, card number, or personal ID —
   `V44` exists to catch it when a customer volunteers one anyway.
