# The checked crawler list

`data/ai-readability/crawlers.csv` lists every crawler name the AI-readability study sends to a
site or reads in robots.txt, each tied to its company's own documentation. It is the source for
the next statewide run (epic #47, issue #42). The scanner does not read it yet; that is issue #44.

## Columns

| Column | Meaning |
|---|---|
| `company` | Who runs the crawler |
| `name` | The crawler's name (its robots.txt token) |
| `purpose` | What it does, in a few plain words the company's page supports |
| `ua_exact` | The User-Agent label, copied word for word from the company's page. Several labels are joined with ` \|\| `. `token only` means the company publishes the name but no full label. |
| `send` | `yes`: sent to sites as a User-Agent. `rules-only`: read in robots.txt, never sent. |
| `follows_robots_per_vendor` | `yes`, `no`, `partly` or `not stated`, strictly as the company's page says for this name |
| `publishes_ips` | `yes` if the company publishes its crawler's addresses or a way to verify them |
| `ip_list_url` | Where, when given. Several are joined with ` \|\| `. |
| `doc_url` | The company page the row was checked against |
| `checked` | The date the row was checked |
| `proof_quote` | Under 15 words, word for word from the page |
| `reason_not_sent` | Required for `rules-only` rows |

## Rules

1. **Only the company's own page counts.** A third-party bot directory is never a source; it may
   only help find the official page.
2. **Labels are copied, never written.** If the page gives no full label, the row says
   `token only`. Never assemble one.
3. **Example labels.** Some companies publish a label as an example that may change (Anthropic's
   Claude-Agent, Meta's crawlers, Google-GeminiNotebook) or with a version stand-in such as
   `Chrome/W.X.Y.Z` (Google, Amazon, Bing). The rule for sending them: send the label exactly as
   published on the check date, mark it as an example, and never fill in a version number.
4. **Meta's long labels** carry a relative link, `(+/documentation/sharing/webmasters/web-crawlers)`,
   exactly as Meta's page shows it. Meta's page also publishes the short form (for example
   `meta-externalagent/1.1`) on its own; that is the one to send.
5. **A page that needs a browser** is read in a real browser, never guessed. Bing's crawler page
   and Meta's crawler page need one.
6. **Every row is checked twice:** once when built, once by a separate reviewer who sees only the
   list and the links. The run does not start until both are clean.

## Names we read but never send

| Name | Why not sent |
|---|---|
| Google-Extended, Applebot-Extended | Exist only in robots.txt; no crawler sends them |
| facebookexternalhit | Meta's link-preview fetcher, not an AI crawler |
| GrokBot, xAI-Grok, Grok-DeepSearch, xAI-SearchBot | No xAI page found on xAI's own sites |
| Bytespider | No ByteDance page found |
| Copilot-specific names | Microsoft publishes none; Bing's crawler page lists Bingbot, AdIdxBot, BingPreview, MicrosoftPreview and BingVideoPreview |

## Check history

- **2026-11-10, built:** 29 rows from company pages. Bing's and Meta's pages read in a real
  browser. No name on the old list had been dropped by its company.
- **2026-11-10, second review:** 25 rows fully right, 4 fixed (bingbot's verification link and its
  mobile label, CCBot's purpose and quote, meta-externalfetcher's purpose, the Copilot reason).
  Every label and quote matched its page character for character.
- **2026-11-10, five names added** after Noel approved them: Claude-Agent, meta-webindexer,
  MistralAI-Index, MistralAI-Training, Google-GeminiNotebook. Second review: none wrong, 3 fixed
  (send Meta's short label; Google-GeminiNotebook's robots.txt set to `partly`, because Google
  says user-triggered fetchers as a group "generally ignore robots.txt"; the third Google address
  file added).

## What the scanner sends today, against this list

Checked 2026-11-10 against `IDENTITIES` in `scripts/probe_bot_access.py`. To be fixed in #44.

| Scanner key | Finding |
|---|---|
| `gptbot`, `oai_searchbot`, `chatgpt_user`, `ccbot` | Match the list word for word |
| `googlebot` | Sends `Chrome/126.0.0.0` where Google's page has `Chrome/W.X.Y.Z`: a filled-in version, against rule 3. Google also publishes `Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)` with no version. |
| `perplexitybot`, `perplexity_user` | Perplexity's page now gives a shorter label, `Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)` (and the same shape for Perplexity-User). The scanner's label is an older form. |
| `claudebot`, `claude_user` | Anthropic publishes the name only. The scanner sends an assembled label, already marked `string_verified: false`. How to send a name-only crawler is a decision for #44. |
