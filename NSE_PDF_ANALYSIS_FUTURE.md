# Future Implementation: NSE/BSE PDF News Analysis

## What & Why

The breaking news scanner already fetches the `attchmntFile` PDF link from NSE corporate announcements. These PDFs are the **ground truth source** that every financial news article summarises — reading them directly eliminates journalist interpretation and spin.

> [!IMPORTANT]
> This is the single highest-value improvement remaining in the news pipeline.
> All the infrastructure (breaking news scanner, Groq LLM, FinBERT) is already in place.

---

## What to Build

### 1. PDF Text Extractor
```
File: backend/app/services/pdf_extractor.py
Library: pdfplumber (free, no API key)
```
- Download the NSE/BSE PDF from the `attchmntFile` URL
- Extract plain text (most corporate PDFs are text-based, not scanned images)
- Truncate to first 3000 tokens for LLM input

### 2. LLM Summariser
```
File: backend/app/services/pdf_summariser.py
LLM: Groq (already in codebase, already has API key configured)
Model: llama-3.3-70b-versatile (same as signal explainer)
```
Prompt template:
```
You are a financial analyst for Indian equities.
Summarise this NSE corporate announcement in 4 bullet points:
- Key financial figures (revenue, profit/loss, EPS)
- Management guidance or outlook
- Any risks or concerns mentioned
- Corporate actions (dividend, buyback, merger terms)

Announcement: {extracted_text}
```
Output stored in Redis: `pdf_summary:{SYMBOL}:{date}` with 24h TTL

### 3. Enhanced FinBERT Scoring
In `news_sentiment.py`, when an article has a PDF summary available:
- Concatenate: `headline + ". " + pdf_summary_bullets`
- Feed this richer text into FinBERT instead of headline alone
- Results in much more accurate sentiment for earnings / SEBI orders

### 4. Hook into Breaking News Scanner
In `breaking_news_scanner.py`, after a HIGH-impact NSE announcement is found:
```python
# After triggering fetch_news_sentiment...
if art.get("symbol") and art.get("url", "").endswith(".pdf"):
    from app.tasks.pdf_analyser import analyse_pdf
    analyse_pdf.apply_async(
        args=[art["symbol"], art["url"]],
        queue="low_priority",
        countdown=10,
    )
```

---

## Implementation Effort

| Step | Estimated Time | New Dependencies |
|------|---------------|-----------------|
| PDF extractor | ~2 hours | `pdfplumber` (add to requirements.txt) |
| LLM summariser | ~1 hour | None (Groq already configured) |
| FinBERT integration | ~1 hour | None |
| Breaking scanner hook | ~30 min | None |
| **Total** | **~4.5 hours** | **pdfplumber only** |

---

## Where This Fits in Signal Generation

```
Before (current):
  headline + RSS summary → FinBERT → sentiment_score (15% of signal weight)

After (with PDF analysis):
  headline + PDF summary (earnings numbers, board decision text) → FinBERT
  → richer sentiment_score → better signal confidence on earnings days
```

> [!TIP]
> Priority: implement on a day before a major earnings season (Q1 results: July, Q2: October, Q3: January, Q4: April). That's when PDF content diverges most from headlines.

> [!NOTE]
> NSE PDF URLs follow the pattern:
> `https://nsearchives.nseindia.com/corporate/ann/{SYMBOL}/{date}/{filename}.pdf`
> These are publicly accessible — no authentication, no rate limiting observed.

---

## What to Skip

- **Full news article scraping** — news sites block aggressively, FinBERT's 512-token limit makes it pointless
- **OCR for scanned PDFs** — most NSE corporate PDFs are text-based; OCR adds complexity for <5% of filings
- **Paid news APIs** — unnecessary given the quality of free NSE/BSE official feeds
