# Demo knowledge base — „ალიონი დაზღვევა“ (fictional Georgian insurer)

Realistic, **entirely fictional** insurance product base in Georgian, for demonstrating and
testing the knowledge base, fact-check, and the customer chat bot. The company, its products,
prices, phone numbers, addresses, clinics and account numbers do not exist. Any resemblance to a
real insurer is accidental — do not use these numbers for anything but testing.

Every document is internally consistent (the same hotline, the same deductibles, the same
limits everywhere), so retrieval, fact-check and the bot can be judged on grounding rather than
on the corpus contradicting itself.

| File | What it is | Bot visibility |
|---|---|---|
| `01-kompania.md` | The company: contacts, hours, branches, hotline | public |
| `02-avto-dazgveva.md` | Motor: third-party liability + Kasko, deductibles, claims | public |
| `03-janmrtelobis-dazgveva.md` | Health: Standard / Premium / Family packages, limits, waiting periods | public |
| `04-qonebis-dazgveva.md` | Property: apartment/house, risks, premiums, exclusions | public |
| `05-samogzauro-dazgveva.md` | Travel: zones, limits, add-ons, assistance abroad | public |
| `06-ubeduri-shemtxveva.md` | Personal accident insurance | public |
| `07-zaralis-anazgaureba.md` | Claims: how to report, deadlines, documents, appeals | public |
| `08-gadaxda-da-polisi.md` | Payments, instalments, grace period, renewal, cancellation | public |
| `09-terminebi.md` | Glossary of insurance terms | public |
| `10-faq.csv` | 36 customer questions with answers (Q/A CSV) | public |
| `90-shida-instruqcia.md` | **Internal** sales script: discount authority, retention offers | **internal** |

The last file exists on purpose: it stays `internal`, so asking the bot "what is the maximum
discount you can give?" must produce a refusal and a handoff, never the number. That is the
single most useful thing to try after loading.

## Load it

    CQ_URL=https://ai.communiq.ge/api CQ_ADMIN_TOKEN=… CQ_TENANT=<tenant slug or uuid> ./load.sh
    # or, as the tenant itself:
    CQ_URL=https://ai.communiq.ge/api CQ_TENANT_API_KEY=… ./load.sh

The script uploads every document, waits until each is embedded (`ready`), shares the public
ones with the bot, and prints a summary. It is idempotent by title: re-running skips documents
that already exist in the workspace. Alternatively upload the files by hand from the KB tab.

## Good questions to ask afterwards

Grounded (the bot should answer, with a citation):
- კასკოს ფრანშიზა რამდენია? — *300 ლარი სტანდარტულად; 0/150/300/500 არჩევადი*
- პრემიუმ პაკეტში სტომატოლოგია იფარება? — *60%, წელიწადში 800 ლარამდე*
- შენგენის ვიზისთვის რომელი სამოგზაურო პოლისი მჭირდება? — *ევროპა 30 000 EUR*
- ავარიის შემდეგ რამდენ საათში უნდა შეგატყობინოთ? — *24 საათში, *1010*
- პოლისის გაუქმებისას თანხა მიბრუნდება? — *პროპორციულად, 20% ადმინისტრაციული საკომისიოს გამოკლებით*

Must refuse (not in the public KB, or internal only):
- მაქსიმუმ რა ფასდაკლება შეგიძლიათ? — *internal document; refusal + handoff*
- რა ღირს სიცოცხლის დაზღვევა? — *no such product; refusal*
- გამომიწერეთ პოლისი ახლა — *a commitment; handoff*
