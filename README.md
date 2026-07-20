# Newsstand Mirror Server

A drop-in Python replacement for `www.getnewsstand.com`, the backend that powered the **Newsstand 1.1** Mac OS 9 app. Since the original server is no longer online, this mirror lets you run Newsstand on real vintage hardware or an emulator with live news content from Google News and custom RSS feeds.

## Background

**Newsstand 1.1** was a Mac OS 9 news reader built by Alex Robb and distributed through [getnewsstand.com](http://www.getnewsstand.com). The app fetched headlines and articles from a server-side PHP backend. This project replicates that backend entirely in Python.

The XML format and endpoint structure were discovered through binary analysis of the PowerPC PEF executable and live traffic capture from the original server.

## Requirements

- Python 3.9+
- [trafilatura](https://trafilatura.readthedocs.io/) — article text extraction (`pip install trafilatura`)
- [lxml](https://lxml.de/) — malformed feed recovery (`pip install lxml`)
- Mac OS 9 machine (real or emulated) running Newsstand 1.1

## Setup

**1. On the machine running the server**, start the mirror:

```bash
sudo python3 newsstand.py
```

Runs on port 80. `sudo` is required to bind to a privileged port.

**2. On the Mac OS 9 machine**, redirect `www.getnewsstand.com` to your server's IP by editing the Hosts file:

- Open **TCP/IP** control panel → select **Hosts** file
- Add: `www.getnewsstand.com  <your-server-ip>`

Or, if using SheepShaver/Basilisk II, edit the host machine's `/etc/hosts`.

**3. Launch Newsstand 1.1** — it will connect to your mirror automatically.

## Features

- **Live Google News** content for all countries and sections supported by the original app
- **Custom RSS/Atom feeds** — both RSS 2.0 and Atom formats are supported
- **Feed auto-discovery** — if you enter a site's homepage URL instead of the feed URL, the server finds the RSS link automatically
- **Article extraction** — fetches and strips articles to plain text using trafilatura
- **Google News URL decoding** — resolves Google News redirect URLs to the real article via the batchexecute API
- **RSS body cache** — for sites that block scraping (WAF, 403), uses the article summary from the feed itself
- **Malformed feed recovery** — fixes unescaped `&` characters and falls back to lxml with recovery mode
- **Mac OS 9 text compatibility** — all output is ASCII-transliterated (accents, diacritics, typographic punctuation) and whitespace-flattened to prevent display artifacts

## Endpoints

See [`endpoints.txt`](endpoints.txt) for full documentation of all endpoints, parameters, XML format, and technical notes.

## Credits

- **Alex Robb** — original creator of Newsstand 1.1 and the getnewsstand.com backend. This project exists to keep his app alive.
- **Action Retro** — creator of [68k-news](https://github.com/ActionRetro/68k-news), a similar news server for 68k Macs. The approach for fetching real article content (including the Google News URL decoding technique) was informed by work in that project.

## License

MIT
