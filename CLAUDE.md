# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the server

```bash
sudo python3 newsproxy.py          # normal mode (no logs)
sudo python3 newsproxy.py --debug  # debug mode (INFO logs to stdout)
```

Requires port 80, hence `sudo`. Dependencies: `pip install trafilatura lxml`

## Installing as a launch daemon

```bash
sudo cp com.bruninho.newsproxy.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.bruninho.newsproxy.plist
```

The plist and `newsproxy.sh` are gitignored — they contain absolute local paths and are machine-specific.

## Architecture

`newsproxy.py` is a single-file HTTP server with no framework. It uses `socketserver.ThreadingTCPServer` with a raw TCP handler (`NewsstandHandler`) instead of `BaseHTTPRequestHandler`, because Classic Mac OS RealBasic's `HTTPSocket` requires a clean TCP FIN to fire `PageReceived`.

**Request flow:**
1. `NewsstandHandler.handle()` reads raw bytes until `\r\n\r\n`
2. `_dispatch()` routes by path to one of four handlers
3. Each handler fetches upstream content, converts it to `<League>/<Player>` XML, and returns bytes
4. Response is sent with HTTP/1.0 headers; connection closes with a half-close (`SHUT_WR`) then a 2s wait for the client FIN

**Four endpoints:**
- `/app/index.php` — Google News RSS for a country/section → `<League>` XML
- `/app/topic.php` — Google News RSS for a topic ID (`CAAq...` base64 protobuf) → `<League>` XML
- `/app/feedlist.php` — arbitrary RSS/Atom feed → `<League>` XML; auto-discovers feed URL if given an HTML page
- `/app/article.php` — fetches and extracts article text; decodes Google News redirect URLs via the `batchexecute` API, then extracts body with `trafilatura`

**XML format** (confirmed from live capture of original server):
- Root element is `<League>`, not `<Newsstand>`
- Each item: `<Player><name>title</name><position>url</position></Player>`
- `<position>` must never be empty — a nil value crashes the app with `NilObjectException`
- `Content-Type: text/html; charset=UTF-8` (PHP default, not `text/xml`)

**Caching (all in RAM, nothing on disk):**
- `_cache`: fetch cache for RSS feeds, 300s TTL, keyed by URL
- `_gnews_cache`: decoded Google News URLs (source → real URL), unbounded but capped at 300 items
- `_rss_body_cache`: article body text from feed entries, used as scraping fallback, capped at 300 items
- A daemon thread (`_cache_cleanup_loop`) runs every 60s to evict expired entries and trim oversized caches

**Text output constraints for Mac OS 9:**
- All text is ASCII-transliterated via `_to_ascii()` — accents, typographic quotes, em dashes, etc.
- All whitespace is flattened to single spaces — Mac OS 9 text fields use `\r` for line breaks, and `\n` in XML renders as a square glyph in the app

**Topic IDs** (`CAAQ` dict): maps the `CAAq...` base64 strings the app sends to either a Google News section path or a search query. Over 100 entries covering Technology, Sports, Business, Entertainment, Science, Gaming, Health, and Interests.
