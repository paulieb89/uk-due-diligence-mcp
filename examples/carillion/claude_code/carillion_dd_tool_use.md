# Carillion PLC — Due Diligence Tool Use Transcript

**Task:** Run a due diligence check on Carillion PLC. Search Companies House, check for insolvency notices on the Gazette, and look up any disqualified directors associated with the company.

---

## Step 1 — Company Search + Gazette (parallel)

### Tool: `uk-due-diligence / company_search`

**Query:** `Carillion PLC`

**Result:** 166 total results. Top match:

| Field | Value |
|---|---|
| Company Number | 03782379 |
| Title | CARILLION PLC |
| Status | **Liquidation** |
| Type | PLC |
| Incorporated | 28 May 1999 |

Other Carillion group entities returned (sample):

| Company Number | Title | Status |
|---|---|---|
| 10556292 | CARILLION ADVICE SERVICES LIMITED | Liquidation |
| 01367044 | CARILLION (AM) LIMITED | Liquidation |
| SC020258 | CARILLION (AMBS) LIMITED | Liquidation |
| 00594581 | CARILLION CONSTRUCTION LIMITED | Liquidation |
| 03858865 | CARILLION ENERGY SERVICES LIMITED | Liquidation |
| 02817840 | CARILLION COMMUNICATIONS LIMITED | Active |

---

### Tool: `uk-due-diligence / gazette_insolvency`

**Query:** `Carillion`

**Result:** 50 notices (cap reached). All severity 7 — Appointment of Liquidator (codes 2432/2452).

| Date | Entity | Notice Type |
|---|---|---|
| 2008-03-10 | Carillion AMT Limited | Appointment of Liquidators |
| 2011-11-30 | Carillion Richardson Thanet Limited | Appointment of Liquidators |
| 2018-02-01 | Carillion Fleet Management Limited | Appointment of Liquidator |
| 2018-02-01 | Carillion Specialist Services Limited | Appointment of Liquidator |
| 2018-02-23 | Carillion Professional Services Limited | Appointment of Liquidator |
| 2018-02-23 | Carillion Project Investments Limited | Appointment of Liquidator |
| 2018-02-23 | Carillion Property Services Limited | Appointment of Liquidator |
| 2018-05-01 | Carillion Advice Services Limited | Appointment of Liquidator |
| 2018-06-04 | Carillion (Aspire Construction) Holdings No.2 Limited | Appointment of Liquidators |
| 2018-06-20 | Carillion Utility Services Group Limited | Appointment of Liquidator |
| 2018-06-20 | Carillion Capital Projects Limited | Appointment of Liquidator |
| 2018-06-20 | Carillion (AM) Limited | Appointment of Liquidator |
| 2018-06-20 | Carillion (Denmark) Limited | Appointment of Liquidator |
| 2018-07-10 | Carillion Resourcing Limited | Appointment of Liquidator |
| 2018-07-17 | Carillion Home Services Limited | Appointment of Liquidator |
| 2018-08-15 | Carillion-Breathe Limited | Appointment of Liquidator |
| 2018-08-15 | Carillion (Singapore) Limited | Appointment of Liquidator |
| 2018-08-15 | Carillion Construction (Contracts) Limited | Appointment of Liquidator |
| 2018-08-15 | Carillion Aqumen Management Services Limited | Appointment of Liquidator |
| 2018-08-15 | Carillion Mena Limited | Appointment of Liquidator |
| 2018-09-26 | Carillion Construction Overseas Limited | Appointment of Liquidator |
| 2018-10-23 | Carillion Private Finance (Health) 2013 Limited | Appointment of Liquidator |
| 2018-10-23 | Carillion Private Finance (Health) 2015 Limited | Appointment of Liquidator |
| 2018-10-23 | Carillion Construction (West Indies) Limited | Appointment of Liquidator |
| 2019-04-16 | Carillion AM Developments Limited | Appointment of Liquidator |
| 2019-04-23 | Carillion Powerlines Limited | Appointment of Liquidators |
| 2020-10-13 | Carillion GB Limited | Appointment of Liquidator |

> **Note:** The 50-notice cap was reached. The full Gazette record exceeds this count.

---

## Step 2 — Company Profile, Officers + Director Searches (parallel)

Company number `03782379` confirmed from Step 1.

### Tool: `uk-due-diligence / company_profile`

**Company Number:** `03782379`

| Field | Value |
|---|---|
| Name | CARILLION PLC |
| Status | **Liquidation** |
| Incorporated | 28 May 1999 |
| Registered Office | Central Square, 8th Floor, 29 Wellington Street, Leeds, LS1 4DL |
| SIC Codes | 41201, 42110, 43390, 43999 |
| Has Charges | **Yes** |
| Last Accounts | 31 Dec 2016 (next due 30 Jun 2018 — **overdue**) |
| Confirmation Statement | Due 11 Jun 2018 — **overdue** |

---

### Tool: `uk-due-diligence / company_officers`

**Company Number:** `03782379` | Active officers only

| Name | Role | Appointed | Nationality | DOB |
|---|---|---|---|---|
| COCHRANE, Keith Robertson | Director | 2015-07-02 | British | Feb 1965 |
| DOUGAL, Andrew James Harrower | Director | 2011-10-03 | British | Sep 1951 |
| GREEN, Philip Nevill | Director | 2011-06-01 | British | May 1953 |
| HORNER, Alison Jane | Director | 2013-12-01 | British | Jun 1966 |
| LOVELL, Alan Charles | Director | 2017-11-01 | British | Nov 1953 |
| MORGAN, Baroness Sally | Director | 2017-07-01 | British | Jun 1959 |
| READ, Justin Richard | Director | 2017-12-01 | British | May 1961 |

All 7 officers were still active at the time of collapse. Richard Howson (CEO until July 2017) had resigned prior and does not appear.

---

### Tool: `uk-due-diligence / disqualified_search`

**Query:** `Richard Howson`

Top result matched on name and address (Skipton, North Yorkshire):

| Field | Value |
|---|---|
| Officer ID | `N1KfhDT2MSFx40aJkn_hOPx6uoI` |
| Name | Richard John HOWSON |
| DOB | 1968-08-03 |
| Address | Stirton Grange, Stirton Lane, Skipton, BD23 3LN |

Strong candidate for Carillion's former CEO — proceeded to full profile lookup.

---

### Tool: `uk-due-diligence / disqualified_search`

**Query:** `Philip Green Carillion` → No match for Philip Nevill Green  
**Query:** `Zafar Khan` → No match for Carillion CFO  
**Query:** `Keith Robertson Cochrane` → No match found

---

## Step 3 — Disqualified Director Profile

### Tool: `uk-due-diligence / disqualified_profile`

**Officer ID:** `N1KfhDT2MSFx40aJkn_hOPx6uoI`

| Field | Value |
|---|---|
| Name | Richard HOWSON |
| DOB | 1968-08-03 |
| Nationality | British |
| Disqualified From | **2023-10-25** |
| Disqualified Until | **2031-10-24** (8-year ban) |
| Act | Company Directors Disqualification Act 1986 |
| Section | s.7 (order or undertaking and reporting provisions) |
| Basis | Undertaking (agreed — not a court order) |
| Undertaken On | 2023-10-04 |
| Associated Company | **CARILLION PLC** |
| Case Reference | INV5239039 |

**Confirmed:** Richard Howson is formally disqualified as a company director until 24 October 2031.

---

## Summary of Tool Calls

| # | Tool | Query | Outcome |
|---|---|---|---|
| 1 | `company_search` | Carillion PLC | 166 results; confirmed company number `03782379` |
| 2 | `gazette_insolvency` | Carillion | 50 notices (cap); all Appointment of Liquidator |
| 3 | `company_profile` | `03782379` | In liquidation; accounts & CS overdue; charges outstanding |
| 4 | `company_officers` | `03782379` | 7 active directors at time of collapse |
| 5 | `disqualified_search` | Richard Howson | Officer ID `N1KfhDT2MSFx40aJkn_hOPx6uoI` identified |
| 6 | `disqualified_search` | Philip Green Carillion | No match |
| 7 | `disqualified_search` | Zafar Khan | No match |
| 8 | `disqualified_search` | Keith Robertson Cochrane | No match |
| 9 | `disqualified_profile` | `N1KfhDT2MSFx40aJkn_hOPx6uoI` | **Confirmed 8-year disqualification — Carillion PLC** |
