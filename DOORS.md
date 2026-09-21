# Axion Labs data doors

Five public data sites, one publisher, one contact. Every door below is served by the same organisation and answers
the same way: a page for people, a JSON or text door for machines, a terms file that says what is free, and a
citation object on every machine answer so the source can be named.

Publisher: Axion Labs (hello@getaxionlabs.com). Registry of all doors: https://open.getaxionlabs.com/doors.json

| Site | What it holds | People | Machines |
|---|---|---|---|
| AppealsDesk | UK private parking charges and council PCNs: operator records, POPLA and IAS outcomes, deadlines, a free should-I-appeal verdict; also EHCP (SEND) appeal data by region | https://appealsdesk.co.uk/ | https://appealsdesk.co.uk/llms.txt |
| Denial Facts | US health-insurance claim denials from the CMS Transparency in Coverage file: denial rate per insurer and state, appeal and external-review outcomes, denial reasons | https://denialfacts.com/ | https://denialfacts.com/api/answer |
| NYC Ticket Facts | NYC parking and camera tickets: hearing outcome rates by violation and borough, defences, deadlines | https://nycticketfacts.com/ | https://nycticketfacts.com/llms.txt |
| MTD Facts | Making Tax Digital for Income Tax (UK): plain-English answers sourced to GOV.UK, thresholds, dates | https://mtdfacts.co.uk/ | https://mtdfacts.co.uk/llms.txt |
| Screening Facts | US tenant and employment screening records: what a report can hold, dispute routes, deadlines | https://screeningfacts.com/ | https://screeningfacts.com/llms.txt |

## The same paths on every site

| Path | What it is |
|---|---|
| `/llms.txt` | The site in plain text for a language model, with a checked date |
| `<page>.md` (home: `/index.md`) | The markdown twin of any page: the same text an agent gets with `Accept: text/markdown`, at its own URL, canonical to the HTML page (denialfacts.com, nycticketfacts.com, mtdfacts.co.uk, screeningfacts.com since 21 Sep 2026) |
| `/.well-known/terms.txt` | Machine-access terms: what is free, what is priced, how to attribute |
| `/.well-known/agent-door.json` | The door passport: endpoints, caps, citation shape, and `pricing.paid_doors`: each priced door with its price, its state on that zone (live or dormant, from the door's own flag), the free allowance a named key gets, and the fields the call returns |
| `/.well-known/api-catalog` | The API catalog (RFC 9727 shape) |
| `/for-agents` and `/for-agents.json` | What an agent can do here, in one page |
| `/api/bulk` | The whole dataset in one pull; priced where a price is shown (HTTP 402 with the price in the body) |
| `/api/key/free?agent=<name>&contact=<email or https url>` | A named key, no money (denialfacts.com, 21 Sep 2026): 1 bulk pull and 20 changes calls a day free, then the ordinary 402; credit bought later rides on the same key |
| `/api/key/checkout` | The price list per door, with each door's free allowance, and where credit is bought |
| `/sitemap.xml` | Every human page |

## Attribution

Free with attribution. Each machine answer carries a `cite` object (title, url, publisher, checked date, dataset
version and fixity hash). Quote the url and the checked date. If a field you need is missing, write to
hello@getaxionlabs.com; a person answers.

## Embeddable charts

Some pages serve a live SVG of their own numbers with a copy-paste snippet that carries the credit link, for example
https://denialfacts.com/states (denial rates by state) and https://appealsdesk.co.uk/popla-appeal-success-rates (POPLA success rates by operator).
The image is redrawn from the data on each request, so an embed stays current.
