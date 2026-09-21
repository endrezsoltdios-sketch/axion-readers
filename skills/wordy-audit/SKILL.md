---
name: wordy-audit
description: Measure a web page against the "too many words" checklist and put the page that ranks for the same keyword beside it: word count, above-the-fold words, H2s, eyebrows, CTAs, em-dashes, real images, repeated sentences, keyword in the H1. Use when someone asks whether a page is too wordy, reads like AI, or why a shorter competitor outranks it. Not a rewrite tool; it prints numbers.
license: FSL-1.1-ALv2
---

# Wordy audit

One page in, one row of numbers out. Name the page that ranks and it prints both rows and the ratio line.

```bash
python scripts/wordy_audit.py https://yoursite.com/page --kw "your keyword"
python scripts/wordy_audit.py https://yoursite.com/page --winner https://theirs.com/page --kw "your keyword"
python scripts/wordy_audit.py https://yoursite.com/page --json
python scripts/wordy_audit.py --selftest
```

Stdlib only, no keys, read-only. A page that will not load prints the error string as its HTTP column and exits 1.

## Reading the row

- `words` and `atf_words`: total visible words and the first ~1,200 characters. The pages that rank for consumer
  queries in our own reads were 1,100 to 1,400 words with 5 to 6 real images; ours were 3,000 to 5,600 with none.
- `emdash`: em- and en-dashes in visible text. Winners we measured carried 0 to 26; our pages 43 to 265. Readers
  now treat a dash-heavy page as machine-written.
- `real_imgs`: images whose filename is not a logo, icon, sprite, pixel or avatar.
- `eyebrows`, `ctas`, `h2`: page furniture. A class-named eyebrow that also sits right before a heading counts on
  both rules; the number is for comparing two pages read by the same tool, not an absolute.
- `repeated`: sentences of eight or more words that appear twice. Anything above 0 is a template leak.
- `kw_in_h1` and `after_h1`: whether the keyword is in the first H1, read the way an engine reads it (case and punctuation dropped, so "Parking violation, New York" carries "parking violation new york"), and what the reader meets right after it. No keyword given: `n/a`, never a fail.

## What to do with it

Compare, do not judge one row alone. The question is "what does the page that ranks do that ours does not", and
the answer is usually in three columns: words, em-dashes, real images. Rewrite for those, re-run, keep the before
and after rows with the date.

Hosted version, weekly runs against a whole site with the referring-domain count beside each row: hello@getaxionlabs.com
