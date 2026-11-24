---
title: The Gazette (thegazette.co.uk) data API — verified reference
as_of: 2026-11-21
verified_by: live probes + official docs
probes: 26 requests to www.thegazette.co.uk, 2026-11-21 15:38–15:45 UTC, curl, User-Agent `uk-due-diligence-mcp-docs (+https://github.com/paulieb89/uk-due-diligence-mcp)`
related: gazette.py, http_client.py, issue #98
updated: 2026-11-24 (issue #98 probes of /notice/{id} with Accept: application/xml; see Per-notice formats)
---

# The Gazette data API

Labels used below:

- **documented** means an official page says it. The URL is given.
- **observed** means a live probe on 2026-11-21 showed it. The probe is given as `METHOD path?query [Accept]`.
- **not verified** means it was neither documented nor probed in this pass.

Base URL: `https://www.thegazette.co.uk`. HTTPS only (**documented**, [DevDocs home.md][dd-home]). No API key is needed for anything below (**observed**).

The official developer docs ([TheGazette/DevDocs][dd-repo]) were last changed in 2022 (`notice-feed.md` and `notice.md` on 2022-06-17, `notice-taxonomy.md` on 2022-04-08). Expect drift.

## TL;DR for this repo

0. **Route in use (since #98, 2026-11-24): `GET /notice/{id}` with `Accept: application/xml`.** `gazette.py` `_fetch_gazette_notice` sends exactly that, one request per notice, and parses the XHTML+RDFa body. Items 1–4 below are the 2026-11-21 findings that led there.
1. **`data.json?view=linked-data` is blocked, not broken.** The per-notice call before #107 (`gazette.py` `_fetch_gazette_notice`) was `GET /notice/{id}/data.json?view=linked-data` with `Accept: application/json`. It returns **429 `text/html` from `awselb/2.0` with `Retry-After: 900`** (**observed**). robots.txt disallows `*/notice/*?view=linked-data*` (**documented**, [robots.txt][robots]). This matches what #98 reports from Fly ("429 on every attempt"). The 500 in #98 comes from the plain `data.json`, with no `view` parameter.
2. **Plain `/notice/{id}/data.json` returns 500** with an XML body, even though the header says `Content-Type: application/json` (**observed**).
3. **`/notice/{id}/data.xml` returns 200**, but robots.txt **disallows** `/notice/*/data.xml` (**documented**). The same goes for `data.rdf`, `data.ttl`, `data.jsonld` and `data.pdf`. The fair-use policy requires crawlers to obey robots.txt. A `data.xml` fallback for #98 would therefore break the fair-use policy, even though it works today.
4. **`GET /notice/{id}`** is not disallowed by robots.txt. With curl's default `Accept: */*` it returned **the same bytes as `data.xml`** (`application/xml`, 13,266 bytes, byte-identical) (**observed**) for a modern numeric ID. This is the robots-compliant route to the XHTML+RDFa body. For **legacy** IDs (`L-…`, `E-…`), `*/*` returns the full HTML site page as `text/html`, which is not well-formed XML; `Accept: application/xml` returns the XHTML+RDFa (**observed** 2026-11-24, #98). That is why the client sends `Accept: application/xml` explicitly. `Accept: application/ld+json` returns 200 JSON-LD with structured fields but **no notice text** (**observed** 2026-11-24, #98).
5. **robots.txt sets `Crawl-delay: 10`** (**documented**). That is stricter than the "5 requests per 10 seconds" in the fair-use policy, which is what `http_client.py` paces to.
6. The fair-use policy says to crawl **between 9pm and 7am UK time** and to include an **application version** in the User-Agent (**documented**). `http_client.USER_AGENT` includes a version. These probes ran in business hours and their User-Agent had no version.
7. Feed: when `results-page-size` is over 100, the feed silently caps the page at 100. In one probe without `text`, 200 returned 500 (**observed**). `noticetype` had **no observable effect** on `/insolvency/notice` (**observed**). A single result still arrives as a **list** (**observed**).

## Endpoints

| Endpoint | Purpose | Status 2026-11-21 | Basis |
|---|---|---|---|
| `GET /{service}/notice/data.json` | Notice search feed, JSON | 200 | documented ([notice-feed.md][dd-feed]) + observed |
| `GET /{service}/notice/data.feed` | Same feed, Atom XML | 200 | documented + observed |
| `GET /{service}/notice/data.htm` | Same feed, HTML | not verified | documented |
| `GET /{service}/{edition}/notice` | Feed limited to one edition | not verified | documented |
| `GET /notice/{id}` and the `data.*` variants | One notice | see [Per-notice formats](#per-notice-formats) | documented ([notice.md][dd-notice], [/data/formats][formats]) |
| `GET /notice-taxonomy/data.json` | Notice code taxonomy | **500** `{"status":"500"}` | documented as sign-in only ([notice-taxonomy.md][dd-tax]); observed |
| `/sparql` | SPARQL endpoint | not verified | linked from [/data][data] |

The docs list these `{service}` values ([notice-feed.md][dd-feed]):

| `{service}` | What it returns |
|---|---|
| `all-notices` | Everything, including matches from supplements |
| `insolvency` | Categories 24 (Corporate Insolvency) and 25 (Personal Insolvency) |
| `wills-and-probate` | Notice code 2903 (Deceased Estates) |

Feed content negotiation (**documented**):

| `Accept` header | Response |
|---|---|
| `application/json` | JSON |
| `application/xml` or `application/atom+xml` | Atom |
| `text/html` or `*/*` | HTML |

Every response observed carries `Vary: Accept`.

## Feed parameters

Probes ran against `/insolvency/notice/data.json` with `Accept: application/json`, unless the row says otherwise.

| Name | Documented? | Values | Observed behaviour |
|---|---|---|---|
| `text` | **No.** Not in the [parameter table][dd-feed]. | Free text | **Works.** `text=carillion` gave `f:total` 198. The term comes back in `openSearch:Query.@searchTerms`. Matches are wrapped in `<em class="highlight">` inside `content`. Results are not in date order, so the default sort looks like relevance. |
| `categorycode` | Yes: "1 or more 2 digit … separated by +", example `24+25` | `24`, `25`, … | **Filters.** `text=carillion&categorycode=24` gave 194, against 198 without it. `categorycode=24` with no text gave 2,222,714. |
| `noticetype` | Yes: "1 or more 4 digit notice code(s) separated by +", example `2903+2450` | 4-digit codes | **No observable filtering on `/insolvency`.** `noticetype=2450`, `2450%2B2431` and `2450+2431` (with `text=carillion`) each gave `f:total` 198, the unfiltered count. The results included code 2432. On `/all-notices`, `noticetype=2450&text=carillion` gave 1201 hits, led by supplement pages that have no `f:notice-code`. The docs and behaviour disagree. |
| `start-publish-date`, `end-publish-date` | Yes: ISO `YYYY-MM-DD`; each works alone | Dates | **Works.** A 2018-01-01 to 2018-12-31 range with `text=carillion&categorycode=24` gave 194 → 129, all published in 2018. |
| `sort-by` | Yes: `latest-date` or `oldest-date` | — | **Works.** `oldest-date` returned results in ascending date order. Without `text`, the default order was newest first, the same as `latest-date`. |
| `results-page-size` | Yes: "Positive Integer". **No maximum is documented.** | Integer | Default is **10** (`f:page-size` "10" when omitted). 100 → 100. 101 and 200 with `text` → **capped at 100** with no error (`f:page-size` "100"). 200 **without** `text` (`categorycode=24` only) → **500** `{"status":"500"}` after about 10 s. This was seen once and its cause is not isolated. |
| `results-page` | Yes: "highest valid page … via the HATEOAS links" | Integer from 1 | Past the end (`results-page=999` with 194 hits): **200**, the `entry` key is **absent**, there is no `next` link, and `f:page-start` is 4991. |
| `start-/end-date-of-death`, `start-/end-claim-expiry-date` | Yes, for code 2903 only | Dates | not verified |
| `location-postcode-[n]`, `location-distance-[n]`, `location-local-authority-[n]` | Yes, n = 1–10, OR'ed together | — | not verified |
| `edition`, `issue` | Yes | `London`, `Edinburgh`, `Belfast`; issue numbers | not verified |

**Paging gotcha** (**observed**): the `first`, `next` and `last` links **drop `results-page-size`**. The `last` page number is still computed from the size you asked for: with `size=5` and 194 hits, `last` is `results-page=39`. Following `next` as returned falls back to 10 per page. Rebuild paging URLs yourself rather than following the links.

## Response shapes

### Feed JSON

This is from `GET /insolvency/notice/data.json?text=carillion&categorycode=24&results-page-size=5` (**observed**). The shape matches the sample in [notice-feed.md][dd-feed].

| Key | Type | Example |
|---|---|---|
| `id` | str | `https://www.thegazette.co.uk/insolvency/notice/data.feed?text=carillion&…` (the Atom URL, even on JSON) |
| `link` | list of `{"@href","@rel","@type","@title"?}` | rels in order: `alternate` (Atom), `first`, `next` (absent on the last page or past the end), `last`, `alternate` (HTML), `self` |
| `title` | str | `"Search Result"` |
| `updated` | str, ISO datetime | `"2026-11-21T15:39:47.596112Z"` (time of the request) |
| `openSearch:Query` | dict | `{"@role":"request","@searchTerms":"carillion"}` |
| `f:page-number`, `f:page-size`, `f:page-start`, `f:page-stop` | **str** of digits | `"1"`, `"5"`, `"1"`, `"5"`; with no hits, `f:page-start` and `f:page-stop` are `"0"` |
| `f:total` | **str** of digits | `"194"`. The count of matches for the whole query. It is `"0"` when nothing matches. |
| `f:facets` | null | `null` |
| `entry` | **list** | Present as a list even with `results-page-size=1`. **Absent** when there are 0 hits or the page is past the end. |

On `entry`: this pass never saw a lone entry sent as a dict, which `gazette.py` `_feed_entries` guards against. The guard is harmless. The docs' JSON sample also shows a list.

### Feed entry

| Field | Type | Example | Notes |
|---|---|---|---|
| `id` | str | `https://www.thegazette.co.uk/id/notice/3040417` | The last path segment is the notice ID. It can be numeric (`3040417`) or legacy (`L-58635-471825`). Supplement-page hits on `/all-notices` use `…/London/issue/825100/supplement/407`. |
| `f:status` | str | `"published"` | |
| `f:notice-code` | str | `"2431"` | **Missing on supplement-page hits** (seen on `/all-notices`). |
| `f:name`, `f:familyName` | str | `"User"`, `"Migration"` (on pre-2014 migrated notices) | Appear to be submitter name fields. Not needed, and may hold personal data. |
| `title` | str | `"CARILLION (ASPIRE SERVICES) LIMITED "` | Newer notices use the company name, sometimes with a trailing space. Legacy notices use the notice type (`"Resolutions for Winding-up"`). |
| `link` | list | `self` = `/id/notice/{id}`; an untyped link = `/notice/{id}`; `alternate`s for `data.ttl`, `data.jsonld`, `data.rdf`, and `?view=linked-data` in TTL, JSON-SIMPLE, RDFJSON, RDF, XML and HTML | Several of these targets are robots-disallowed. See [Fair use](#fair-use-and-licensing). |
| `author` | dict | `{"name":"tso"}` | |
| `updated` | str, ISO datetime | `"2014-05-25T19:14:40+01:00"` | |
| `published` | str, ISO datetime **with no timezone** | `"2018-06-04T17:42:24"` | |
| `category` | dict | `{"@term":"Resolutions for Winding-up"}` | |
| `content` | str (HTML fragment) | `<div><p><em class="highlight">CARILLION</em> (ASPIRE SERVICES) HOLDINGS NO.2 LIM…` | A **snippet** cut with `…`, not the full notice. |
| `geo:Point` | present on some entries | — | Inner shape not verified. |

### Feed Atom (`data.feed`)

From `GET /insolvency/notice/data.feed?text=carillion&categorycode=24&results-page-size=2` (**observed**):

- Status 200.
- The `Content-Type` **header was missing** from the response.
- The root is `<feed xmlns="http://www.w3.org/2005/Atom">`, with namespaces `f:` (`https://www.thegazette.co.uk/facets`), `geo:` and `openSearch:`. Elements and links mirror the JSON.
- The paging links drop `results-page-size` here too.

### Per-notice `data.xml` (XHTML+RDFa)

This is the structure `gazette.py` parses for #98, served from `GET /notice/{id}` with `Accept: application/xml` (never from `data.xml` itself, which is robots-disallowed). It was **observed** on notice 5256044 (code 2452, Winding-Up Order). The same bytes come back from `GET /notice/5256044` with `Accept: */*`.

| What you need | Where it is |
|---|---|
| Root | `<html xmlns="http://www.w3.org/1999/xhtml" IdURI="https://www.thegazette.co.uk/id/notice/{id}" prefix="gaz: … insolvency: … gazorg: …">` |
| Title | `/html/head/title`, e.g. `PROFESSIONAL TRAINING GROUP UK LIMITED` |
| Notice code | `dd[@property="gaz:hasNoticeCode"]` (text `2452`). Also `article/@class` = `full-notice full-notice-2452` and `div.content/@data-gazettes-type` = `2452`. |
| Notice type label | `dd[@data-ui-class="notice-type"]`, e.g. `Winding-Up Orders` |
| Category label | `dd[@data-ui-class="category"]`, e.g. `Corporate Insolvency` |
| Publication date | `dd[@property="gaz:hasPublicationDate"]/@content`, e.g. `2026-11-21T13:20:10`. Also the `<time datetime>` child. |
| Notice ID | `dd[@property="gaz:hasNoticeID"]` (`5256044`), and `span[@property="gaz:hasNoticeNumber"]/@content` |
| Edition | `span[@property="gaz:editionName"]` (`London`) |
| **Body text** | `body/article/div[@class="content"]` (`about="this:notifiableThing"`). Take its text content. |
| Company name and number | Inside the body: `span[@property="gazorg:name"]` and `span[@property="gazorg:companyNumber"]` (`11981040`). The company resource is `http://business.data.gov.uk/id/company/{number}`. |
| Typed dates | `@property` values such as `insolvency:datePetitionFiled`, `insolvency:dateWoundUp` and `insolvency:dateOfAppointment`, each with `@content` set to an ISO date |
| Court | `court:courtName`, `court:caseNumber`, `court:caseYear` |
| Skip these | `div.rdfa-data` (hidden enrichment spans: postcodes, lat/long, related dates); `dl.metadata`, unless you want the fields above; `section.summary` (a repeat) |

Parsing caveats (**observed**):

- Office-holder names, phone numbers and emails appear inside the body.
- Some text sits in `@content` attributes rather than in element text, e.g. `foaf:name` on empty spans. A plain text extraction will miss it, and gets the human-readable text instead.
- The XML is well-formed. Response headers: `x-robots-tag: noindex,follow`, `content-disposition: inline;data.xml`, `cache-control: public, s-maxage=7200`. It was served from the CloudFront cache (`x-cache: Hit`).

The 2026-11-21 pass sampled one notice (a modern numeric-ID 2452). The #98 probes on 2026-11-24 checked the same structure on `4420724` (2410), `4953471` (2443) and legacy `L-57793-2040` (2452). On the legacy notice:

- the title is the notice type (`Winding-Up Orders`);
- there are no `gazorg:companyNumber`, `gazorg:name` or `gazorg:previousCompanyName` properties (the number appears only in the body text, and `gazette.py` does not parse it from there);
- `gaz:hasPublicationDate` has no `@content`; the date is only in `time/@datetime` (`2005-10-21`). `gazette.py` falls back to the first `time/@datetime` outside `div.rdfa-data`, cut to `YYYY-MM-DD`.

`E-…` legacy notices were probed only with `Accept: */*` (HTML site page); their `application/xml` shape is expected to match `L-…` but is **not verified**.

## Per-notice formats

All rows were probed on notice 5256044 on 2026-11-21 unless they say otherwise. In the "Robots" column, "disallowed" means robots.txt forbids the path.

**Route in use:** `/notice/{id}` with `Accept: application/xml` (first row). `gazette.py` requires the response media type to be exactly `application/xml` (parameters such as `charset` allowed) and returns a retryable `transient` error otherwise, for a malformed body, or for a body with no `div.content`. It does not parse the HTML site page.

| Format / URL | Documented response | Status today | Robots | Notes |
|---|---|---|---|---|
| `/notice/{id}`, `Accept: application/xml` (**our code path**, #98) | not listed | **200 `application/xml`**, well-formed XHTML+RDFa (**observed** 2026-11-24 on 4420724, 18,690 B; 4953471; L-57793-2040, 9,805 B) | allowed | For 4420724, byte-identical to the `*/*` response. Works for legacy `L-…` IDs too. |
| `/notice/{id}`, `Accept: */*` (curl default) | (X)HTML5+RDFa page with site chrome ([formats][formats]) | **200 `application/xml`** for modern numeric IDs; **200 `text/html` site page** for legacy IDs (L-57793-2040, E-26343-2431-168; **observed** 2026-11-24) | allowed | Modern: byte-identical to `data.xml`, with no site chrome. Legacy: the full site page, not well-formed XML. Not used. |
| `/notice/{id}`, `Accept: text/html` | HTML page | not verified | allowed | Probe cancelled |
| `/notice/{id}`, `Accept: application/ld+json` | JSON-LD | **200 JSON-LD** with structured fields but **no notice body text** (**observed** 2026-11-24, #98) | allowed | Not used: the text needs the XML route, and one request per notice is the budget. |
| `/notice/{id}/data.xml` | XHTML+RDFa, notice content only | **200** `application/xml;charset=UTF-8`, 13,266 bytes | **disallowed** | Structure above |
| `/notice/{id}/data.json`, `Accept: application/json` | not listed without `view` | **500** `application/json`; body `<?xml …?><error><status>500</status></error>` | allowed | #98. 3395688 was not re-probed in this pass; #98 reports 500 for it too. |
| `/notice/{id}/data.json?view=linked-data`, `Accept: application/json` (our code path until #107) | "JSON LD" ([notice.md][dd-notice]) or "JSON" (content-negotiation table) | **429 `text/html`**, `server: awselb/2.0`, `Retry-After: 900` | **disallowed** (`*/notice/*?view=linked-data*`) | The block looked path-specific: the next two requests (plain `data.json`, `data.xml`) were answered normally. |
| `/notice/{id}/data.jsonld` | JSON-LD | not verified | disallowed | |
| `/notice/{id}/data.rdf` | RDF/XML | not verified | disallowed | |
| `/notice/{id}/data.ttl` | Turtle | not verified | disallowed | |
| `/notice/{id}/data.pdf` | PDF | not verified | disallowed | |
| `/notice/{id}/data.htm` | Website HTML | not verified | allowed | |
| `/notice/{id}/data.feed` | not documented for notices | not verified | allowed | |
| `?view=linked-data` variants (`.rdfjson`, `.xml`, `.ttl`, `.rdf`, `.html`) | Linked-data views | not probed on purpose | disallowed | Probing them risks the same 900 s block |

## Notice categories and codes

Feed evidence (**observed**):

- Category **24 = Corporate Insolvency** and **25 = Personal Insolvency**. **Documented** in [notice-feed.md][dd-feed] (the `insolvency` service row) and in the taxonomy sample (`code="24" name="Corporate Insolvency" service-key="insolvency"`). `categorycode=24` filtering was observed.
- **2903 = Deceased Estates** (**documented**).
- One code is **documented** in the taxonomy sample: **2450** = "Petitions to Wind Up (Companies)", in section `2400/5` "Winding-up By The Court".
- **Observed** in category-24 results: 2406, 2410, 2431, 2432, 2433, 2435, 2441, 2442, 2443, 2446, 2450, 2452, 2454. All are in `gazette.py` `NOTICE_LABELS`.
- `data.xml` for 2452 labels it "Winding-Up Orders", category "Corporate Insolvency". `NOTICE_LABELS` has "Winding up order (companies)".
- `data.xml` RDF types for 2452 (**observed**): `insolvency:WindingUpOrdersNotice` and `insolvency:WindingUpOrder`.

The full taxonomy:

- `/notice-taxonomy/data.json` returned **500 `{"status":"500"}`** without sign-in (**observed**). [notice-taxonomy.md][dd-tax] says it is "only available to authenticated users" and lists "notices which can be placed by the current user" (**documented**).
- The rest of `NOTICE_LABELS` is not verified against any official source in this pass.

## Fair use and licensing

From the [fair-use policy][fair] (**documented**). Automated tools must:

- crawl between **9pm and 7am UK time**;
- send at most **5 requests every 10 seconds**;
- **comply with robots.txt and any crawl delay**;
- use a unique User-Agent with **application name and version** (e.g. `My-Bot/1.0.0`), plus a contact method such as a company email or website URL, and no personal details;
- avoid excessive load, and not bypass rate limits.

When a client goes over the limit, the server may return **429 with `Retry-After`**, which "must" be respected. Limits may be reduced, and the policy changed, without notice.

From [robots.txt][robots] (**documented**, `User-agent: *`):

- **`Crawl-delay: 10`**.
- These paths are disallowed:
  - `/notice/*/data.xml`, `data.pdf`, `data.rdf`, `data.ttl` and `data.jsonld`
  - `*/notice/*?view=linked-data*`
  - `/notice/*/version/*` and `/notice/*/provenance*`
  - `/sign-in`
  - many specific notices and issues
- These are **not** disallowed: `/notice/{id}`, `/notice/{id}/data.json`, `/notice/{id}/data.htm`, and the feeds (`/insolvency/notice/data.json`, `data.feed`).

Observed rate-limit behaviour: the only 429 in 26 requests at 10 s or more apart was on the `view=linked-data` URL. It came from the AWS load balancer (`awselb/2.0`) with `Retry-After: 900`, which suggests a path rule rather than a rate trip. That reading is an inference from a single sample.

Licensing (**documented**):

- Gazette data is Crown Copyright under the **Open Government Licence v3.0**, "unless stated otherwise". The OGL **does not cover re-use of personal data** ([/data][data]; [DevDocs home.md][dd-home]).
- Suggested citation: "Recorded in The Gazette, issue {n}, {date}" ([/data][data]).
- Support contact: TSO customer services, listed in [DevDocs home.md][dd-home].

## Known issues (as of 2026-11-21)

1. **#98: per-notice JSON is unavailable** (resolved for this repo on 2026-11-24 by switching to `GET /notice/{id}` with `Accept: application/xml`; the findings below are as of 2026-11-21).
   - Plain `/notice/{id}/data.json` → 500, with an XML error body under a JSON content type (**observed**, 5256044).
   - `/notice/{id}/data.json?view=linked-data` (what `gazette.py` requests) → 429 with `Retry-After: 900` from the WAF/ELB (**observed**). That path is robots-disallowed (**documented**).
   - From Fly, #98 reports "429 on every attempt", which is consistent with the second bullet.
   - Ways forward, from the evidence above:
     - `data.xml` works, but it is robots-disallowed.
     - `GET /notice/{id}` with `Accept: */*` or `application/xml` returns the same XHTML+RDFa and is robots-allowed (**observed** for `*/*` only on 2026-11-21; `application/xml` **observed** on 2026-11-24, and required for legacy IDs).
     - Content negotiation for JSON-LD on `/notice/{id}` returns no body text (**observed** 2026-11-24).
2. **Feed `results-page-size` above 100**: silently capped at 100 when `text` is set. In one probe without `text`, size 200 → 500 (**observed**). `FEED_PAGE_SIZE = 100` in `gazette.py` is safe.
3. **`noticetype` appears to be ignored** on `/insolvency/notice` (**observed**). Do not rely on it to narrow results. Filter on `f:notice-code` client-side, as `gazette.py` already does.
4. **Paging links drop `results-page-size`** (**observed**).
5. **`http_client.py` pacing vs `Crawl-delay`**: the client paces to 5 requests per 10 s, the fair-use number. robots.txt asks for a 10 s crawl delay (**documented**). Neither document says which takes precedence.
6. **Atom feed responses have no `Content-Type` header** (**observed**).

## Probe log

All 26 requests went to `https://www.thegazette.co.uk`, 2026-11-21 UTC, spaced at least 10 s apart after the first four (which were 3 s apart). They were read-only, and corporate or generic search terms only.

| # | Request | Result |
|---|---|---|
| 1–4 | `/data`, `/fair-use-policy`, `/data/formats`, `/robots.txt` | 200 each |
| 5 | feed `text=carillion&categorycode=24&results-page-size=5&results-page=1` | 200, total 194 |
| 6 | feed `text=carillion&results-page-size=5` | 200, total 198 |
| 7 | feed `categorycode=24&results-page-size=200` | **500** |
| 8 | feed `text=carillion&categorycode=24&results-page-size=5&results-page=999` | 200, no `entry` |
| 9 | feed `text=carillion&categorycode=24&results-page-size=1` | 200, `entry` is a list of 1 |
| 10 | feed `text=zzqxqnomatchzz&categorycode=24` | 200, total "0", no `entry` |
| 11 | feed `text=carillion&categorycode=24&results-page-size=100` | 200, size 100 |
| 12 | feed `…&results-page-size=101` | 200, capped at 100 |
| 13 | feed `text=carillion&noticetype=2450%2B2431&results-page-size=3` | 200, total 198 (not filtered) |
| 14 | feed `text=carillion&categorycode=24&start-publish-date=2018-01-01&end-publish-date=2018-12-31&results-page-size=3&sort-by=oldest-date` | 200, total 129, ascending |
| 15 | feed `categorycode=24&results-page-size=3&sort-by=latest-date` | 200, total 2,222,714; newest is notice 5256044 |
| 16 | `/insolvency/notice/data.feed?text=carillion&categorycode=24&results-page-size=2` | 200 Atom, no Content-Type |
| 17 | feed `categorycode=24&results-page-size=100` | 200 |
| 18 | feed `text=carillion&noticetype=2450&results-page-size=3` | 200, total 198 |
| 19 | feed `text=carillion&noticetype=2450+2431&results-page-size=3` | 200, total 198 |
| 20 | `/notice-taxonomy/data.json` | **500** |
| 21 | `/all-notices/notice/data.json?text=carillion&noticetype=2450&results-page-size=3` | 200, total 1201, supplement pages |
| 22 | feed `text=carillion&categorycode=24&results-page-size=200` | 200, capped at 100 |
| 23 | `/notice/5256044` (`Accept: */*`) | 200 `application/xml`, same bytes as #26 |
| 24 | `/notice/5256044/data.json?view=linked-data` (`Accept: application/json`) | **429**, `Retry-After: 900` |
| 25 | `/notice/5256044/data.json` (`Accept: application/json`) | **500** |
| 26 | `/notice/5256044/data.xml` | 200 |

The developer docs were read through the GitHub API (`gh api repos/TheGazette/DevDocs/...`), not from thegazette.co.uk.

## Sources

- [data]: https://www.thegazette.co.uk/data — "Re-using our data": OGL, personal-data exclusion, linked-data formats, citation wording
- [formats]: https://www.thegazette.co.uk/data/formats — per-notice representation URIs and content negotiation
- [fair]: https://www.thegazette.co.uk/fair-use-policy — rate, hours, User-Agent, 429 and Retry-After
- [robots]: https://www.thegazette.co.uk/robots.txt — Crawl-delay 10; disallowed notice formats and `view=linked-data`
- [dd-repo]: https://github.com/TheGazette/DevDocs — official developer documentation (last pushed 2026-10-26; the notice pages are unchanged since 2022)
- [dd-home]: https://github.com/TheGazette/DevDocs/blob/master/home.md — base URL, HTTPS only, OGL v3, support contact
- [dd-feed]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice-feed.md — feed services, representations, parameter table, JSON and Atom samples
- [dd-notice]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice.md — per-notice representations, the `view` parameter
- [dd-tax]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice-taxonomy.md — taxonomy endpoint (authenticated users only), sample codes
- Not read: `/sparql`, `/flint`, `/dataservice`, `/data/longitudinal-datasets`, and the DevDocs SPARQL, publication, authentication and My Gazette pages.

[data]: https://www.thegazette.co.uk/data
[formats]: https://www.thegazette.co.uk/data/formats
[fair]: https://www.thegazette.co.uk/fair-use-policy
[robots]: https://www.thegazette.co.uk/robots.txt
[dd-repo]: https://github.com/TheGazette/DevDocs
[dd-home]: https://github.com/TheGazette/DevDocs/blob/master/home.md
[dd-feed]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice-feed.md
[dd-notice]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice.md
[dd-tax]: https://github.com/TheGazette/DevDocs/blob/master/notice/notice-taxonomy.md
