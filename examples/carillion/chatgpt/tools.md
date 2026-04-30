# Carillion PLC — ChatGPT Tool Call Transcript

> Exported from ChatGPT. Token counts are ChatGPT's internal usage indicators and are preserved for reference. Collapsed arrays (`[{…}]`) indicate data was available but truncated in the export.

---

## Call 1 — `company_search`

**Input** _(13 tokens)_
```
query: "Carillion PLC"
items_per_page: 10
```

**Output** _(701 tokens)_
```
query: "Carillion PLC"
total_results: 166
start_index: 0
items_per_page: 10
returned: 10
has_more: true
items: (10)[{…}, {…}, {…}, {…}, {…}, {…}, {…}, {…}, {…}, {…}]
```

---

## Call 2 — `company_profile`

**Input** _(8 tokens)_
```
company_number: "03782379"
```

**Output** _(158 tokens)_
```
company_number: "03782379"
company_name: "CARILLION PLC"
company_status: "liquidation"
company_type: null
date_of_creation: "1999-05-28"
sic_codes: (4)["41201", "42110", "43390", "43999"]
registered_office_address: {
  address_line_1: "Central Square 8th Floor",
  address_line_2: "29 Wellington Street",
  locality: "Leeds",
  postal_code: "LS1 4DL",
  region: "West Yorkshire"
}
has_charges: true
accounts: { overdue: true, last_accounts_made_up_to: "2016-12-31", next_due: "2018-06-30" }
confirmation_statement: { overdue: true, next_due: "2018-06-11" }
```

---

## Call 3 — `company_officers` (attempt 1)

**Input** _(14 tokens)_
```
company_number: "03782379"
items_per_page: 50
```

**Output:** No tool response

---

## Call 4 — `company_officers` (attempt 2)

**Input** _(8 tokens)_
```
company_number: "03782379"
```

**Output** _(1,233 tokens)_
```
company_number: "03782379"
include_resigned: false
total: 7
high_appointment_count_flag: null
officers: (7)[{…}, {…}, {…}, {…}, {…}, {…}, {…}]
```

---

## Call 5 — `disqualified_search` (attempt 1)

**Input** _(7 tokens)_
```
query: "Carillion PLC"
```

**Output:** No tool response

---

## Call 6 — `gazette_insolvency`

**Input** _(14 tokens)_
```
entity_name: "Carillion PLC"
max_notices: 100
```

**Output** _(477 tokens)_
```
entity_name: "Carillion PLC"
notice_type_filter: null
start_date: null
end_date: null
total_notices: 4
max_notices_cap: 100
notices: (4)[{…}, {…}, {…}, {…}]
```

> **Note:** Searching for `"Carillion PLC"` (exact name) returned only 4 notices. Broadening to `"Carillion"` yields 50+. The narrow query missed most group subsidiary notices.

---

## Call 7 — `disqualified_search` (attempt 2)

**Input** _(6 tokens)_
```
query: "Carillion"
```

**Output:** No tool response

---

## Call 8 — `disqualified_search` (attempt 3)

**Input** _(12 tokens)_
```
query: "Carillion"
items_per_page: 100
```

**Output** _(34 tokens)_
```
query: "Carillion"
total_results: 0
start_index: 0
items_per_page: 100
returned: 0
has_more: false
items: []
```

> **Note:** `disqualified_search` expects a **person's name**, not a company name. Searching `"Carillion"` correctly returns zero results — disqualified directors must be looked up by individual name (e.g. `"Richard Howson"`).

---

## Call 9 — `company_psc`

**Input** _(8 tokens)_
```
company_number: "03782379"
```

**Output** _(59 tokens)_
```
company_number: "03782379"
total: 0
overseas_corporate_psc_flag: 0
psc: []
note: "No registrable PSC. Typical for widely-held listed PLCs where no
       single person or entity holds 25%+ of shares or voting rights."
```

---

## Call 10 — `gazette_notice` (full record)

**Input** _(8 tokens)_
```
notice_id: "2948343"
```

**Output** _(1,106 tokens)_
```
format: "linked-data-api"
version: "0.2"
result: {
  _about: "https://www.thegazette.co.uk/notice/2948343/data.json?view=linked-data"
  label: "2948343"
  primaryTopic: {
    _about: "https://www.thegazette.co.uk/id/notice/2948343"
    publisher: "TSO (The Stationery Office), St Crispins, Duke Street, Norwich, NR3 1PD"
    sameAs: "https://www.thegazette.co.uk/id/edition/London/issue/62178/notice/5227133001"
    hasNoticeCode: 2452
    hasNoticeID: "2948343"
    hasPageNumber: "1122"
    hasPublicationDate: "Friday, 19-Jan-2018 10:20:30 UTC"
    isInIssue: "https://www.thegazette.co.uk/id/edition/London/issue/62178"
  }
}
```

> **Note:** Notice 2948343 is a type-2452 (Appointment of Liquidator), published 19 January 2018 — four days after Carillion entered compulsory liquidation on 15 January 2018.

---

## Summary

| # | Tool | Input | Result |
|---|---|---|---|
| 1 | `company_search` | `"Carillion PLC"` | 166 results; company number `03782379` identified |
| 2 | `company_profile` | `03782379` | In liquidation; accounts & CS overdue; charges outstanding |
| 3 | `company_officers` | `03782379` (with `items_per_page: 50`) | **No tool response** |
| 4 | `company_officers` | `03782379` | 7 active officers returned |
| 5 | `disqualified_search` | `"Carillion PLC"` | **No tool response** |
| 6 | `gazette_insolvency` | `"Carillion PLC"` | 4 notices (narrow query — missed group subsidiaries) |
| 7 | `disqualified_search` | `"Carillion"` | **No tool response** |
| 8 | `disqualified_search` | `"Carillion"` | 0 results (tool requires a person name, not company name) |
| 9 | `company_psc` | `03782379` | No registrable PSC — expected for a listed PLC |
| 10 | `gazette_notice` | `2948343` | Liquidator appointment notice, published 19 Jan 2018 |
