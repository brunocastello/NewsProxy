#!/usr/bin/env python3
import sys; sys.dont_write_bytecode = True
"""
Newsstand Mirror Server — drop-in for www.getnewsstand.com

CONFIRMED FORMAT (captured from live getnewsstand.com, July 2026):
  - Root element is <League>, not <Newsstand>
  - Each item: <Player><name>title</name><position>url</position></Player>
  - Article URL format: /app/article.php?loc=US&a=<google_news_rss_url>
  - Article response: <Player><name>headline</name><position>body_text</position></Player>
  - Content-Type: text/html; charset=UTF-8 (PHP default, not text/xml)

Endpoints served:
  /app/index.php?loc=US[&section=nation|world]   → <Player> XML
  /app/topic.php?topic=CAAq...                   → <Player> XML
  /app/feedlist.php?feed=<url>                   → <Player> XML
  /app/article.php?url=<url>                     → headline!!break!!body

Usage: sudo python3 newsstand_server.py
Mac OS 9 HOSTS: www.getnewsstand.com A 10.0.3.2
"""

import json
import ssl
import html as html_mod
import socketserver
import select
import socket as _socket
import threading
import urllib.request
import urllib.error
import urllib.parse
import gzip
import zlib
import time
import logging
import sys
import re
import xml.etree.ElementTree as ET
import trafilatura

log = logging.getLogger("newsstand")

# ---------------------------------------------------------------------------
# Country map: app loc code -> (hl, gl, ceid)
# ---------------------------------------------------------------------------
COUNTRY = {
    "US": ("en-US", "US", "US:en"), "UK": ("en-GB", "GB", "GB:en"),
    "AU": ("en-AU", "AU", "AU:en"), "CA": ("en-CA", "CA", "CA:en"),
    "IN": ("en-IN", "IN", "IN:en"), "IE": ("en-IE", "IE", "IE:en"),
    "PK": ("en-PK", "PK", "PK:en"), "NZ": ("en-NZ", "NZ", "NZ:en"),
    "JP": ("ja-JP", "JP", "JP:ja"), "DE": ("de-DE", "DE", "DE:de"),
    "FR": ("fr-FR", "FR", "FR:fr"), "IT": ("it-IT", "IT", "IT:it"),
    "BR": ("pt-BR", "BR", "BR:pt"), "PT": ("pt-PT", "PT", "PT:pt"),
    "MX": ("es-MX", "MX", "MX:es"), "AR": ("es-AR", "AR", "AR:es"),
    "CL": ("es-CL", "CL", "CL:es"), "CO": ("es-CO", "CO", "CO:es"),
    "AT": ("de-AT", "AT", "AT:de"), "CH": ("de-CH", "CH", "CH:de"),
    "BE": ("fr-BE", "BE", "BE:fr"), "NL": ("nl-NL", "NL", "NL:nl"),
    "PL": ("pl-PL", "PL", "PL:pl"), "RU": ("ru-RU", "RU", "RU:ru"),
    "TR": ("tr-TR", "TR", "TR:tr"), "TW": ("zh-TW", "TW", "TW:zh"),
    "KR": ("ko-KR", "KR", "KR:ko"), "DK": ("da-DK", "DK", "DK:da"),
    "GR": ("el-GR", "GR", "GR:el"),
}
DEFAULT_COUNTRY = ("en-US", "US", "US:en")

# ---------------------------------------------------------------------------
# CAAq topic ID → Google News RSS URL strategy
# ---------------------------------------------------------------------------
CAAQ = {
    # Technology
    "CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB": ("section", "TECHNOLOGY"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRzFuYTJjU0FtVnVLQUFQAQ":       ("search",  "Amazon technology"),
    "CAAqBwgKMOvMpAow7td3":                                        ("search",  "Apple Inc"),
    "CAAqJQgKIh9DQkFTRVFvTEwyMHZNREV4YzIxaU5ESVNBbVZ1S0FBUAE":  ("search",  "Apple Watch"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNRzFyZWhJQ1pXNG9BQVAB":          ("search",  "Artificial Intelligence"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR1IyTlhJU0FtVnVLQUFQ":          ("search",  "cameras photography"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREl5ZUY4U0FtVnVLQUFQAQ":        ("search",  "cybersecurity"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNSGd5TUd0bWJoSUNaVzRvQUFQAQ":   ("search",  "digital privacy"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR2N5WW1NU0FtVnVLQUFQAQ":        ("search",  "drones"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREp0Y25BU0FtVnVLQUFQAQ":        ("search",  "consumer electronics"),
    "CAAqBwgKMOjY3gow0cTWAQ":                                      ("search",  "Google technology"),
    "CAAqKAgKIiJDQkFTRXdvTkwyY3ZNVEZvWHpjMVpHTnNlUklDWlc0b0FBUAE": ("search", "iPad Apple"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREkzYkc1NmN4SUNaVzRvQUFQAQ":   ("search",  "iPhone Apple"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNSHBrTmhJQ1pXNG9BQVAB":          ("search",  "Mac Apple"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFUxZVhJU0FtVnVLQUFQAQ":        ("search",  "macOS Apple"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFJ6ZGpRU0FtVnVLQUFQAQ":        ("search",  "Microsoft"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ0TTNZU0FtVnVLQUFQAQ":        ("search",  "computers PC"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNR2h1WW5OdU14SUNaVzRvQUFQAQ":   ("search",  "Samsung Galaxy"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREUyT1hwb0VnSmxiaWdBUAE":       ("search",  "smartphones"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREk0TUhkb2RCSUNaVzRvQUFQAQ":   ("search",  "smartwatches wearables"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRmYm5rU0FtVnVLQUFQAQ":        ("search",  "virtual reality VR"),
    # Sports
    "CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp1ZEdvU0FtVnVHZ0pWVXlnQVAB": ("section", "SPORTS"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREU0YW5vU0FtVnVLQUFQAQ":        ("search",  "baseball MLB"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREU0ZHpnU0FtVnVLQUFQAQ":        ("search",  "basketball NBA"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGw0Y0Y4U0FtVnVLQUFQAQ":        ("search",  "cricket"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZ3T1hsd0VnSmxiaWdBUAE":       ("search",  "fantasy football"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREozWTJvU0FtVnVLQUFQAQ":        ("search",  "fantasy sports"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNR3B0WHhJQ1pXNG9BQVAB":          ("search",  "NFL football"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE0zYUhvU0FtVnVLQUFQAQ":        ("search",  "golf PGA"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE4wYlhJU0FtVnVLQUFQAQ":        ("search",  "hockey NHL"),
    "CAAqBwgKMPGm8wowobHaAg":                                      ("search",  "lacrosse"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRE01ZVhwekVnSmxiaWdBUAE":       ("search",  "NCAA basketball"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREY2Wm1ZU0FtVnVLQUFQAQ":        ("search",  "NCAA football"),
    "CAAqBwgKMMSHjQsw4uGeAw":                                      ("search",  "rugby"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFpmWm5jU0FtVnVLQUFQAQ":        ("search",  "skateboarding"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGN4YXpBU0FtVnVLQUFQAQ":        ("search",  "skiing"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGx4WjIwU0FtVnVLQUFQAQ":        ("search",  "snowboarding"),
    "CAAqJQgKIh9DQkFTRVFvTEwyMHZNREV4Tm5JeGJXb1NBbVZ1S0FBUAE":  ("search",  "soccer football"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFpmWnpjU0FtVnVLQUFQAQ":        ("search",  "surfing"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRxWW1nU0FtVnVLQUFQAQ":        ("search",  "table tennis"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRpY3pBU0FtVnVLQUFQAQ":        ("search",  "tennis"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRmTlRNU0FtVnVLQUFQAQ":        ("search",  "volleyball"),
    # Interests
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRFJ5YXpobkVnSmxiaWdBUAE":       ("search",  "3D printing"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNR3BpYXhJQ1pXNG9BQVAB":          ("search",  "animals wildlife"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNR3BxZHhJQ1pXNG9BQVAB":          ("search",  "art news"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR1JqWDNZU0FtVnVLQUFQAQ":        ("search",  "astronomy"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRE5vWm00d0VnSmxiaWdBUAE":       ("search",  "backpacking travel"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR1IyTXpRU0FtVnVLQUFQAQ":        ("search",  "baking"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZtTTNBU0FtVnVLQUFQAQ":        ("search",  "BASE jumping"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZtTkRNU0FtVnVLQUFQAQ":        ("search",  "beauty cosmetics"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREYyYmpNMUVnSmxiaWdBUAE":       ("search",  "beekeeping"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREUxT1RrU0FtVnVLQUFQAQ":        ("search",  "beer craft brewing"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREUzWkhZNEVnSmxiaWdBUAE":       ("search",  "bird watching"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZvTm1RMEVnSmxiaWdBUAE":       ("search",  "camping outdoors"),
    "CAAqIAgKIhpDQkFTRFFvSEwyMHZNR3MwYWhJQ1pXNG9BQVAB":          ("search",  "cars automotive"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREY1Y25nU0FtVnVLQUFQAQ":        ("search",  "cats pets"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZzWWpVU0FtVnVLQUFQAQ":        ("search",  "chess"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREoyY1dadEVnSmxiaWdBUAE":       ("search",  "coffee"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREV5YURJMEVnSmxiaWdBUAE":       ("search",  "comics"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ0Wmw4U0FtVnVLQUFQAQ":        ("search",  "computer programming"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ0ZEdJU0FtVnVLQUFQAQ":        ("search",  "cooking recipes"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ6WjJ3U0FtVnVLQUFQAQ":        ("search",  "cycling biking"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREpqZDIwU0FtVnVLQUFQAQ":        ("search",  "design"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREUzY21OeEVnSmxiaWdBUAE":       ("search",  "DIY home improvement"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNR0owT1d4eUVnSmxiaWdBUAE":       ("search",  "dogs pets"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREpqYzJZU0FtVnVLQUFQAQ":        ("search",  "drawing illustration"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR3A0ZHpVU0FtVnVLQUFQAQ":        ("search",  "horse riding equestrian"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGswYW1NU0FtVnVLQUFQAQ":        ("search",  "fishing"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREo0ZWpJU0FtVnVLQUFQAQ":        ("search",  "Formula 1"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRE5xTjNockVnSmxiaWdBUAE":       ("search",  "fossil collecting"),
    "CAAqJQgKIh9DQkFTRVFvSUwyMHZNRE0wZGw4U0JXVnVMVWRDS0FBUAE":  ("search",  "gardening plants"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE01TWpnU0FtVnVLQUFQAQ":        ("search",  "geocaching"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNR1EwYW5ReUVnSmxiaWdBUAE":       ("search",  "ghost hunting paranormal"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE01TjNjU0FtVnVLQUFQAQ":        ("search",  "gymnastics"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREV5ZGpScUVnSmxiaWdBUAE":       ("search",  "hiking trails"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGw1TUd3U0FtVnVLQUFQAQ":        ("search",  "horoscopes astrology"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGx3TlcwU0FtVnVLQUFQAQ":        ("search",  "hunting"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREp5Wm1SeEVnSmxiaWdBUAE":       ("search",  "interior design"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRjlrTm00U0FtVnVLQUFQAQ":        ("search",  "jigsaw puzzles"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFEzWm5JU0FtVnVLQUFQAQ":        ("search",  "knitting"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRFp5Tm1SeUVnSmxiaWdBUAE":       ("search",  "Lego"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFI0YzNZU0FtVnVLQUFQAQ":        ("search",  "martial arts MMA"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZvTm5KcUVnSmxiaWdBUAE":       ("search",  "military defense"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFJmYzNZU0FtVnVLQUFQAQ":        ("search",  "motorcycles"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFV6WmpVU0FtVnVLQUFQAQ":        ("search",  "mountain climbing"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZuZWpVU0FtVnVLQUFQAQ":        ("search",  "national parks"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ4WkdnU0FtVnVLQUFQAQ":        ("search",  "painting art"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREV4ZVhjMUVnSmxiaWdBUAE":       ("search",  "papermaking craft"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFYzYTNjU0FtVnVLQUFQAQ":        ("search",  "photography"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ6TW5NU0FtVnVLQUFQAQ":        ("search",  "plants gardening"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ4ZERBU0FtVnVLQUFQAQ":        ("search",  "politics"),
    "CAAqBwgKMKHY9Qowit3cAg":                                      ("search",  "US Senate"),
    "CAAqBwgKMJHY9Qow-tzcAg":                                      ("search",  "US House Representatives"),
    "CAAqBwgKMJvPpAow7dx3":                                        ("search",  "US Supreme Court"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFkwY21zU0FtVnVLQUFQAQ":        ("search",  "pottery ceramics"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFk1Wm1RU0FtVnVLQUFQAQ":        ("search",  "quilting"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREp3TUhRMVpoSUNaVzRvQUFQAQ":   ("search",  "robotics"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFozY25RU0FtVnVLQUFQAQ":        ("search",  "sailing"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRFF3ZGpkNkVnSmxiaWdBUAE":       ("search",  "scrapbooking"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFp0YzNFU0FtVnVLQUFQAQ":        ("search",  "sculpture art"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRzE2T0hRU0FtVnVLQUFQAQ":        ("search",  "sewing fashion"),
    "CAAqBwgKMIy88wow48XTAg":                                      ("search",  "social media"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRE4wTVhkMkVnSmxiaWdBUAE":       ("search",  "speedcubing Rubik"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRGxyYW5CeEVnSmxiaWdBUAE":       ("search",  "scuba diving"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGQzYlc0U0FtVnVLQUFQAQ":        ("search",  "urban exploration"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREp3TVRSeWVCSUNaVzRvQUFQAQ":   ("search",  "video editing"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE5qTXpFU0FtVnVLQUFQAQ":        ("search",  "visual design"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZ5ZG1SNkVnSmxiaWdBUAE":       ("search",  "whale watching"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGd4Y1dNU0FtVnVLQUFQAQ":        ("search",  "wine"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGd4Y21JU0FtVnVLQUFQAQ":        ("search",  "writing authors"),
    # Business
    "CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB": ("section", "BUSINESS"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNSFp3YWpSZlloSUNaVzRvQUFQAQ":   ("search",  "cryptocurrency Bitcoin"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREp0T1RZU0FtVnVLQUFQAQ":        ("search",  "e-commerce"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNR2RtY0hNekVnSmxiaWdBUAE":       ("search",  "economy"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREp1ZDNFU0FtVnVLQUFQAQ":        ("search",  "entrepreneurship startups"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR2RmWm13U0FtVnVLQUFQAQ":        ("search",  "investing stocks finance"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREkxY25ReWN4SUNaVzRvQUFQAQ":   ("search",  "leadership business"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREYzWnpCbkVnSmxiaWdBUAE":       ("search",  "marketing advertising"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREY1Tm1OeEVnSmxiaWdBUAE":       ("search",  "personal finance money"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFpzY25rU0FtVnVLQUFQAQ":        ("search",  "retirement"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZ3WkdNMEVnSmxiaWdBUAE":       ("search",  "small business"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRpTjNJU0FtVnVLQUFQAQ":        ("search",  "business strategy"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRuT0RJU0FtVnVLQUFQAQ":        ("search",  "taxes IRS"),
    # Entertainment
    "CAAqJggKIiBDQkFTRWdvSUwyMHZNREpxYW5RU0FtVnVHZ0pWVXlnQVAB": ("section", "ENTERTAINMENT"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNRFI1WTJ4eVp4SUNaVzRvQUFQAQ":   ("search",  "books literature"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ5Wm5vU0FtVnVLQUFQAQ":        ("search",  "celebrities Hollywood"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNR2RuY1RCdEVnSmxiaWdBUAE":       ("search",  "classical music"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZzZVhZU0FtVnVLQUFQAQ":        ("search",  "country music"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGxpTTNZU0FtVnVLQUFQAQ":        ("search",  "Disney"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR3AwWkhBU0FtVnVLQUFQAQ":        ("search",  "documentaries film"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRGhqZVdaMEVnSmxiaWdBUAE":       ("search",  "EDM electronic music"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREpmYUdobUVnSmxiaWdBUAE":       ("search",  "film industry Hollywood"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREp4T1cwU0FtVnVLQUFQAQ":        ("search",  "movie reviews"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREoyZUc0U0FtVnVLQUFQAQ":        ("search",  "movies cinema"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFJ5YkdZU0FtVnVLQUFQAQ":        ("search",  "music"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREkyTTNGemF4SUNaVzRvQUFQAQ":   ("search",  "music festivals"),
    "CAAqBwgKMPvbgwswi9H_Ag":                                      ("search",  "music streaming Spotify"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREUzY21aZkVnSmxiaWdBUAE":       ("search",  "Netflix streaming"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRFF3TlRod0VnSmxiaWdBUAE":       ("search",  "podcasts"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ5T1hRU0FtVnVLQUFQAQ":        ("search",  "pop music"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGxzYldJU0FtVnVLQUFQAQ":        ("search",  "reality TV"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFppZVRjU0FtVnVLQUFQAQ":        ("search",  "rock music"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFp0ZGpZU0FtVnVLQUFQAQ":        ("search",  "Star Trek"),
    "CAAqBwgKMLTw0gEwuow1":                                        ("search",  "Star Wars"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNR2RzZERZM01CSUNaVzRvQUFQAQ":   ("search",  "hip-hop rap music"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREo1WHpScVp4SUNaVzRvQUFQAQ":   ("search",  "Hulu streaming"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREk1T1hoZkVnSmxiaWdBUAE":       ("search",  "indie films"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREo1YURoc0VnSmxiaWdBUAE":       ("search",  "K-Pop music"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFU0YWpJU0FtVnVLQUFQAQ":        ("search",  "Marvel movies"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZ6T0d4eUVnSmxiaWdBUAE":       ("search",  "media entertainment news"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE15ZEd3U0FtVnVLQUFQAQ":        ("search",  "fashion style"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREp4YzJSd2F4SUNaVzRvQUFQAQ":   ("search",  "theatre Broadway"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNRFJrWDNkbVl4SUNaVzRvQUFQAQ":   ("search",  "theatre reviews"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRqTlRJU0FtVnVLQUFQAQ":        ("search",  "TV shows television"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRExxWTNaekVnSmxiaWdBUAE":       ("search",  "YouTube creators"),
    # Science
    "CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp0Y1RjU0FtVnVHZ0pWVXlnQVAB": ("section", "SCIENCE"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREUxTkRBU0FtVnVLQUFQAQ":        ("search",  "biology"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ0YTNFU0FtVnVLQUFQAQ":        ("search",  "computer science"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ0YW5RU0FtVnVLQUFQAQ":        ("search",  "physics"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR1JqZWpJU0FtVnVLQUFQAQ":        ("search",  "geology"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE0yWHpJU0FtVnVLQUFQAQ":        ("search",  "genetics DNA"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREp3ZVRBNUVnSmxiaWdBUAE":       ("search",  "environment climate change"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ0Wm5RU0FtVnVLQUFQAQ":        ("search",  "NASA space"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZpTm1NU0FtVnVLQUFQAQ":        ("search",  "neuroscience brain"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREU0TXpOM0VnSmxiaWdBUAE":       ("search",  "space astronomy"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRE5tYTNsM0VnSmxiaWdBUAE":       ("search",  "SpaceX"),
    "CAAqBwgKMPHX9Qow-9vcAg":                                      ("search",  "wildlife nature"),
    # Gaming
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZ0ZHpFU0FtVnVLQUFQAQ":        ("search",  "video games gaming"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNRGQ2YkRadEVnSmxiaWdBUAE":       ("search",  "2K Games"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREV5TTJvMkVnSmxiaWdBUAE":       ("search",  "Bethesda games"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFU1ZDJzU0FtVnVLQUFQAQ":        ("search",  "Nintendo"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFY2ZUdzU0FtVnVLQUFQAQ":        ("search",  "PlayStation Sony"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREZmTkcxdUVnSmxiaWdBUAE":       ("search",  "Rockstar Games GTA"),
    "CAAqKAgKIiJDQkFTRXdvTkwyY3ZNVEZpWXpVM05XdHhYeElDWlc0b0FBUAE": ("search", "Square Enix games"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREp4WkhscUVnSmxiaWdBUAE":       ("search",  "Ubisoft games"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNSFp3YUdKbWFoSUNaVzRvQUFQAQ":   ("search",  "Xbox Microsoft gaming"),
    # Health
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNR3QwTlRFU0FtVnVLQUFQAQ":        ("section", "HEALTH"),
    "CAAqJAgKIh5DQkFTRUFvS0wyMHZNREp5YW1kNmNCSUNaVzRvQUFQAQ":   ("search",  "CrossFit"),
    "CAAqIggKIhxDQkFTRHdvSkwyMHZNREU1ZHpab0VnSmxiaWdBUAE":       ("search",  "exercise fitness"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFE0ZW1RU0FtVnVLQUFQAQ":        ("search",  "keto diet"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFV4TjJ3U0FtVnVLQUFQAQ":        ("search",  "meditation mindfulness"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFpvTjJvU0FtVnVLQUFQAQ":        ("search",  "running marathon"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGRmYUhrU0FtVnVLQUFQAQ":        ("search",  "veganism"),
    "CAAqIQgKIhtDQkFTRGdvSUwyMHZNRGcwT1d3U0FtVnVLQUFQAQ":        ("search",  "weightlifting"),
}

GOOGLE_NEWS_BASE = "https://news.google.com"
_cache: dict = {}
CACHE_TTL = 300
MAX_CACHE_ITEMS = 300
CACHE_CLEANUP_INTERVAL = 60

ARTICLE_BASE = "http://www.getnewsstand.com/app/article.php"

# Set to "normal" for live content, "hardcode" to test the XML format.
DEBUG_FORMAT = "normal"


_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Cache of article content extracted from RSS feed bodies, keyed by article URL.
# Populated by rss_to_player_xml; used by _handle_article to avoid re-fetching
# articles from sites that block scrapers (403, WAF, etc.)
_rss_body_cache: dict = {}   # url -> (title, body_text)


def fetch_url(url: str, timeout: int = 15) -> bytes:
    now = time.time()
    if url in _cache:
        ts, data = _cache[url]
        if now - ts < CACHE_TTL:
            log.info("CACHE HIT %s", url[:80])
            return data
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        enc = resp.info().get("Content-Encoding", "")
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
    _cache[url] = (now, raw)
    log.info("FETCHED %s (%d bytes)", url[:80], len(raw))
    return raw


def fetch_article(url: str, timeout: int = 20) -> bytes:
    """Fetch article page. Uses Chrome UA + Google News referer + SSL bypass (PR #24 approach)."""
    now = time.time()
    cache_key = "article:" + url
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://news.google.com/",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        enc = resp.info().get("Content-Encoding", "")
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
    _cache[cache_key] = (now, raw)
    return raw


def google_news_url(hl: str, gl: str, ceid: str, path: str = "") -> str:
    params = urllib.parse.urlencode({"hl": hl, "gl": gl, "ceid": ceid})
    return f"{GOOGLE_NEWS_BASE}/rss{path}?{params}"


def build_index_url(loc: str) -> str:
    hl, gl, ceid = COUNTRY.get(loc.upper(), DEFAULT_COUNTRY)
    return google_news_url(hl, gl, ceid)


def build_section_url(section: str, loc: str) -> str:
    hl, gl, ceid = COUNTRY.get(loc.upper(), DEFAULT_COUNTRY)
    if section == "nation":
        return google_news_url(hl, gl, ceid, f"/headlines/section/geo/{gl}")
    elif section == "world":
        return google_news_url(hl, gl, ceid, "/headlines/section/topic/WORLD")
    return google_news_url(hl, gl, ceid)


def build_topic_url(topic_id: str) -> str:
    entry = CAAQ.get(topic_id)
    if entry is None:
        log.warning("Unknown topic ID: %s", topic_id)
        hl, gl, ceid = DEFAULT_COUNTRY
        return google_news_url(hl, gl, ceid)
    kind, value = entry
    hl, gl, ceid = DEFAULT_COUNTRY
    if kind == "section":
        return google_news_url(hl, gl, ceid, f"/headlines/section/topic/{value}")
    else:
        q = urllib.parse.quote(value)
        return f"{GOOGLE_NEWS_BASE}/rss/search?q={q}&{urllib.parse.urlencode({'hl': hl, 'gl': gl, 'ceid': ceid})}"


_ASCII_SUBS = str.maketrans({
    # Typographic punctuation
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...", " ": " ",
    "•": "*", "·": "*", "°": " deg",
    "®": "(R)", "©": "(C)", "™": "(TM)",
    "½": "1/2", "¼": "1/4", "¾": "3/4",
    # Lowercase with diacritics (Portuguese, Spanish, French, German...)
    "á": "a", "à": "a", "â": "a", "ã": "a", "ä": "a", "å": "a", "æ": "ae",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i",
    "ó": "o", "ò": "o", "ô": "o", "õ": "o", "ö": "o", "ø": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ý": "y", "ÿ": "y",
    "ç": "c", "ñ": "n", "ß": "ss",
    "º": "o.", "ª": "a.",
    # Uppercase with diacritics
    "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Ä": "A", "Å": "A", "Æ": "AE",
    "É": "E", "È": "E", "Ê": "E", "Ë": "E",
    "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
    "Ó": "O", "Ò": "O", "Ô": "O", "Õ": "O", "Ö": "O", "Ø": "O",
    "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
    "Ý": "Y", "Ç": "C", "Ñ": "N",
})


def _to_ascii(text: str) -> str:
    text = text.translate(_ASCII_SUBS)
    return "".join(ch if ord(ch) < 128 else "?" for ch in text)


def _strip_html(text: str) -> str:
    text = html_mod.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_NS_ATOM = "http://www.w3.org/2005/Atom"
_ERROR_POSITION = _xml_escape(f"{ARTICLE_BASE}?loc=US&a=error")


def _make_player_xml(players: list) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<League>"
        + "".join(players)
        + "</League>"
    )
    return xml.encode("utf-8")


def _player_entry(title: str, link: str, loc: str) -> str:
    article_url = f"{ARTICLE_BASE}?loc={loc}&a={link}"
    return (
        f"<Player>"
        f"<name>{_xml_escape(title)}</name>"
        f"<position>{_xml_escape(article_url)}</position>"
        f"</Player>"
    )


def rss_to_player_xml(raw: bytes, loc: str = "US") -> bytes:
    """
    Convert RSS 2.0 or Atom feed to <League>/<Player> XML.
    Handles both formats so custom feeds work regardless of type.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # ET is strict. Try progressively more lenient approaches:
        # 1. Strip UTF-8 BOM
        # 2. Fix unescaped & signs (common in RSS titles like "AT&T")
        # 3. lxml with recover=True (handles most malformed XML)
        cleaned = raw.lstrip(b'\xef\xbb\xbf')
        cleaned = re.sub(
            rb'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)',
            b'&amp;', cleaned
        )
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError as exc2:
            log.warning("Feed ET parse failed (%s), trying lxml recover", exc2)
            try:
                from lxml import etree as _lxml
                parser = _lxml.XMLParser(recover=True, encoding="utf-8")
                root = _lxml.fromstring(raw, parser=parser)
                log.info("Feed parsed with lxml recover")
            except Exception as exc3:
                log.warning("Feed lxml also failed: %s — first 200: %s", exc3, raw[:200])
                return _error_player_xml("Feed parse error - invalid XML")

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    # ── Atom feed ──────────────────────────────────────────────────────────
    if root_tag == "feed":
        players = []
        ns = f"{{{_NS_ATOM}}}"
        entries = root.findall(f"{ns}entry") or root.findall("entry")
        for entry in entries:
            title_el = entry.find(f"{ns}title")
            if title_el is None:
                title_el = entry.find("title")
            if title_el is None:
                continue
            title = _to_ascii(_strip_html(title_el.text or ""))
            if not title:
                continue

            # <link href="..."> — prefer rel="alternate", fall back to any link
            link = ""
            link_els = entry.findall(f"{ns}link") or entry.findall("link")
            for lel in link_els:
                rel = lel.get("rel", "alternate")
                href = lel.get("href", "")
                if href and rel in ("alternate", ""):
                    link = href
                    break
            if not link:
                id_el = entry.find(f"{ns}id")
                if id_el is None:
                    id_el = entry.find("id")
                if id_el is not None and (id_el.text or "").startswith("http"):
                    link = id_el.text.strip()
            if not link:
                continue

            # Cache article body from feed content/summary for sites that block scraping
            body_el = entry.find(f"{ns}content")
            if body_el is None:
                body_el = entry.find(f"{ns}summary")
            if body_el is None:
                body_el = entry.find("content")
            if body_el is None:
                body_el = entry.find("summary")
            if body_el is not None:
                raw_body = _to_ascii(_strip_html(body_el.text or ""))
                if len(raw_body) > 80:
                    _rss_body_cache[link] = (title, raw_body)

            players.append(_player_entry(title, link, loc))

        log.info("rss_to_player_xml (Atom): %d entries", len(players))
        return _make_player_xml(players) if players else _error_player_xml("No entries in Atom feed")

    # ── RSS 2.0 ────────────────────────────────────────────────────────────
    channel = root.find("channel")
    if channel is None:
        return _error_player_xml("No channel in RSS feed")

    _CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"

    def get_text(elem, tag: str) -> str:
        child = elem.find(tag)
        return (child.text or "").strip() if child is not None else ""

    players = []
    for item in channel.findall("item"):
        title = _to_ascii(_strip_html(get_text(item, "title")))
        if not title:
            continue
        link = get_text(item, "link")
        if not link:
            link = get_text(item, "guid")
        if not link:
            continue

        # Cache article body from content:encoded or description
        body_text = ""
        content_el = item.find(f"{_CONTENT_NS}encoded")
        if content_el is not None:
            body_text = _to_ascii(_strip_html(content_el.text or ""))
        if not body_text:
            desc = get_text(item, "description")
            body_text = _to_ascii(_strip_html(desc))
        if len(body_text) > 80:
            _rss_body_cache[link] = (title, body_text)

        players.append(_player_entry(title, link, loc))

    log.info("rss_to_player_xml (RSS): %d items", len(players))
    return _make_player_xml(players) if players else _error_player_xml("No items in RSS feed")


def _discover_feed_url(html: bytes, base_url: str) -> str:
    """Find RSS/Atom feed URL from an HTML page's <link rel="alternate"> tags."""
    text = html.decode("utf-8", errors="replace")
    # Match both attribute orderings: rel before type, type before rel
    patterns = [
        r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+rel=["\']alternate["\']',
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            href = m.group(1)
            return href if href.startswith("http") else urllib.parse.urljoin(base_url, href)
    return ""


def _error_player_xml(message: str) -> bytes:
    msg = _xml_escape(_to_ascii(message))
    # Never emit empty <position> — a nil URL in the app causes NilObjectException crash.
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<League><Player><name>{msg}</name>'
        f'<position>{_ERROR_POSITION}</position>'
        f'</Player></League>'
    ).encode("utf-8")


def extract_article(html_bytes: bytes) -> tuple:
    """
    Extract (title, body) from HTML using trafilatura.
    Returns plain ASCII strings — no newlines (Mac OS 9 uses \\r, XML normalises \\r→\\n).
    """
    try:
        html_str = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_str = html_bytes.decode("latin-1", errors="replace")

    # trafilatura extracts main content, ignoring nav/ads/footer
    body_raw = trafilatura.extract(
        html_str,
        include_tables=False,
        include_comments=False,
        favor_precision=True,
        no_fallback=False,
    )

    # Fall back to title tag if trafilatura finds nothing
    title_m = re.search(r'<title[^>]*>([^<]{1,300})</title>', html_str, re.IGNORECASE)
    title_raw = title_m.group(1).strip() if title_m else "Article"

    title = _to_ascii(title_raw[:200])
    if body_raw:
        # Collapse whitespace: Mac OS 9 Classic text fields use \r, not \n.
        # XML normalises \r→\n, so newlines in the XML become \n in the app
        # and display as squares. Flatten to single-spaced prose.
        body_flat = re.sub(r'\s+', ' ', body_raw).strip()
        body = _to_ascii(body_flat[:12000])
    else:
        body = "Could not extract article text."

    return title, body


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)

_gnews_cache: dict = {}


def _cache_cleanup_loop():
    while True:
        time.sleep(CACHE_CLEANUP_INTERVAL)
        now = time.time()
        expired = [k for k, (ts, _) in list(_cache.items()) if now - ts >= CACHE_TTL]
        for k in expired:
            _cache.pop(k, None)
        for cache in (_gnews_cache, _rss_body_cache):
            if len(cache) > MAX_CACHE_ITEMS:
                trim = list(cache.keys())[: len(cache) - MAX_CACHE_ITEMS * 3 // 4]
                for k in trim:
                    cache.pop(k, None)
        log.debug(
            "Cache sweep: %d fetch, %d gnews, %d rss_body",
            len(_cache), len(_gnews_cache), len(_rss_body_cache),
        )


def decode_google_news_url(source_url: str, timeout: int = 15) -> str:
    """
    Decode a Google News RSS redirect URL to get the real article URL.
    Python port of 68k-news/php/googlenews.php GoogleDecoder.
    Returns source_url unchanged on any failure.
    """
    if source_url in _gnews_cache:
        return _gnews_cache[source_url]

    parsed = urllib.parse.urlparse(source_url)
    if "news.google.com" not in (parsed.netloc or ""):
        return source_url

    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 2 or path_parts[-2] not in ("articles", "read"):
        return source_url

    base64_str = path_parts[-1].split("?")[0]

    # Step 1: fetch Google News page to extract signature + timestamp
    html = None
    for try_url in [
        f"https://news.google.com/rss/articles/{base64_str}",
        f"https://news.google.com/articles/{base64_str}",
    ]:
        try:
            req = urllib.request.Request(try_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            break
        except Exception:
            continue

    if not html:
        log.warning("decode_google_news_url: could not fetch Google News page")
        return source_url

    sg = re.search(r'data-n-a-sg="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    if not sg or not ts:
        log.warning("decode_google_news_url: data-n-a-sg/ts not found in page")
        return source_url

    signature = sg.group(1)
    timestamp = ts.group(1)

    # Step 2: POST to batchexecute to decode URL
    inner_args = (
        f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",'
        f'null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,'
        f'null,0,0,null,0],"{base64_str}",{timestamp},"{signature}"]'
    )
    f_req = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner_args]]]))

    try:
        post_req = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data=f_req.encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": _UA,
            },
        )
        with urllib.request.urlopen(post_req, timeout=timeout) as resp:
            response_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("decode_google_news_url: batchexecute failed: %s", e)
        return source_url

    # Response: "size\n\nJSON\n\n..." — split on double newline, skip size
    parts = response_text.split("\n\n", 1)
    if len(parts) < 2:
        log.warning("decode_google_news_url: unexpected response format")
        return source_url

    try:
        outer = json.loads(parts[1])
        inner_str = outer[0][2]
        inner = json.loads(inner_str)
        if inner and len(inner) > 1:
            real_url = inner[1]
            log.info("Decoded Google News -> %s", real_url[:80])
            _gnews_cache[source_url] = real_url
            return real_url
    except Exception as e:
        log.warning("decode_google_news_url: parse failed: %s", e)

    return source_url


# ---------------------------------------------------------------------------
# Raw TCP HTTP handler — bypasses BaseHTTPRequestHandler entirely so we
# send the absolute minimum headers and close the socket cleanly (FIN, not
# RST).  RealBasic's HTTPSocket on Classic Mac OS fires PageReceived when it
# sees the TCP FIN, so a clean half-close is critical.
# ---------------------------------------------------------------------------

class NewsstandHandler(socketserver.BaseRequestHandler):

    def handle(self):
        conn = self.request
        try:
            conn.settimeout(10.0)
            raw = self._read_request(conn)
            if not raw:
                return

            first_line = raw.split(b"\r\n")[0].decode("ascii", errors="ignore")
            parts = first_line.split(" ")
            if len(parts) < 2:
                return
            path_query = parts[1]

            log.info(">> GET %s from %s", path_query, self.client_address[0])
            log.debug("RAW REQUEST: %r", raw)

            body, ctype = self._dispatch(path_query)

            # Minimal HTTP/1.0 response — no Server:, no Date:.
            # Include Content-Length so HTTPSocket knows response end without
            # relying solely on FIN (some RealBasic Classic builds require it).
            header = (
                f"HTTP/1.0 200 OK\r\n"
                f"Content-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode("ascii")

            conn.sendall(header + body)
            log.info("SENT %d bytes body, ctype=%s, path=%s", len(body), ctype, path_query[:60])

        except Exception as exc:
            log.error("Handler error: %s", exc)
        finally:
            self._close_clean(conn)

    def _read_request(self, conn) -> bytes:
        """Read until blank line (end of HTTP headers)."""
        buf = b""
        try:
            while b"\r\n\r\n" not in buf and len(buf) < 8192:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        return buf

    def _close_clean(self, conn):
        """Close connection. Log what happens so we can diagnose PageReceived."""
        # Step 1: half-close write side — tells Mac OS 9 our response is done
        try:
            conn.shutdown(_socket.SHUT_WR)
            log.debug("CLOSE: SHUT_WR sent (FIN to client)")
        except OSError as e:
            log.debug("CLOSE: SHUT_WR failed: %s", e)

        # Step 2: wait up to 2 s for client to send its FIN (after PageReceived fires)
        try:
            ready, _, _ = select.select([conn], [], [], 2.0)
            if ready:
                conn.recv(4096)
        except OSError:
            pass

        # Step 3: close our side
        try:
            conn.close()
        except OSError:
            pass

    def _dispatch(self, path_query: str):
        """Route request and return (body_bytes, content_type_str)."""
        parsed = urllib.parse.urlparse(path_query)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        # Real getnewsstand.com returns text/html for all endpoints
        ctype = "text/html; charset=UTF-8"
        try:
            if path == "/app/index.php":
                return self._handle_index(qs), ctype
            elif path == "/app/topic.php":
                return self._handle_topic(qs), ctype
            elif path == "/app/feedlist.php":
                return self._handle_feedlist(qs), ctype
            elif path == "/app/article.php":
                return self._handle_article(qs), ctype
            else:
                return _error_player_xml(f"Unknown: {path}"), ctype
        except urllib.error.URLError as exc:
            log.error("Upstream error: %s", exc)
            return _error_player_xml(f"Network error: {exc}"), ctype
        except Exception as exc:
            log.exception("Unhandled error")
            return _error_player_xml(str(exc)), ctype

    def send_xml(self, data: bytes, status: int = 200):
        """Legacy compatibility — returns the bytes (caller uses _dispatch now)."""
        return data

    def send_text(self, data: bytes, status: int = 200):
        return data

    def _debug_xml(self) -> bytes:
        """Hardcoded XML for testing — uses confirmed <League>/<Player>/<name>/<position> format."""
        art_url = _xml_escape("http://www.getnewsstand.com/app/article.php?loc=US&a=https://news.google.com/rss/articles/test")
        if DEBUG_FORMAT == "minimal":
            return b'<?xml version="1.0" encoding="UTF-8"?><League><Player><name>Hello</name><position></position></Player></League>'
        elif DEBUG_FORMAT in ("nodecl", "hardcode"):
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<League>'
                f'<Player><name>Test Article - BBC News</name><position>{art_url}</position></Player>'
                f'<Player><name>Second Article - Reuters</name><position>{art_url}</position></Player>'
                '</League>'
            ).encode("utf-8")
        return b'<?xml version="1.0" encoding="UTF-8"?><League><Player><name>DEBUG</name><position></position></Player></League>'

    def _handle_index(self, qs: dict) -> bytes:
        if DEBUG_FORMAT != "normal":
            log.info("DEBUG_FORMAT=%r: returning hardcoded XML", DEBUG_FORMAT)
            return self._debug_xml()
        loc = (qs.get("loc", ["US"])[0] or "US").upper()
        raw_section = (qs.get("section", [""])[0] or "").lower().strip()
        if "nation" in raw_section:
            section_key = "nation"
        elif "world" in raw_section:
            section_key = "world"
        else:
            section_key = ""
        url = build_section_url(section_key, loc) if section_key else build_index_url(loc)
        log.info("index.php section=%r loc=%s -> %s", section_key or "top", loc, url[:80])
        data = fetch_url(url)
        return rss_to_player_xml(data, loc)

    def _handle_topic(self, qs: dict) -> bytes:
        if DEBUG_FORMAT != "normal":
            log.info("DEBUG_FORMAT=%r: returning hardcoded XML", DEBUG_FORMAT)
            return self._debug_xml()
        topic_id = qs.get("topic", [None])[0]
        if not topic_id:
            return _error_player_xml("Missing topic")
        loc = (qs.get("loc", ["US"])[0] or "US").upper()
        url = build_topic_url(topic_id)
        log.info("topic.php %s -> %s", topic_id[:20], url[:80])
        data = fetch_url(url)
        return rss_to_player_xml(data, loc)

    def _handle_feedlist(self, qs: dict) -> bytes:
        feed_url = qs.get("feed", [None])[0]
        if not feed_url:
            return _error_player_xml("Missing feed")
        log.info("feedlist.php -> %s", feed_url[:80])
        data = fetch_url(feed_url, timeout=20)
        # If the URL pointed to an HTML page instead of a feed, auto-discover
        # the RSS/Atom link from the page's <link rel="alternate"> tags.
        if data.lstrip()[:100].lower().lstrip(b'\xef\xbb\xbf')[:15] in (
            b'<!doctype html>', b'<html',
        ) or data.lstrip()[:15].lower().startswith((b'<!doctype', b'<html')):
            discovered = _discover_feed_url(data, feed_url)
            if discovered:
                log.info("feedlist: HTML page, auto-discovered feed -> %s", discovered[:80])
                data = fetch_url(discovered, timeout=20)
            else:
                return _error_player_xml("Not a feed - no RSS/Atom link found on page")
        return rss_to_player_xml(data)

    def _article_xml(self, title: str, body: str) -> bytes:
        t = _xml_escape(_to_ascii(title)[:200])
        # Flatten ALL whitespace to single spaces.
        # Classic Mac OS text fields use \r for line breaks; XML normalises \r→\n,
        # so \n in the XML text node ends up as a square glyph in the app.
        body_flat = re.sub(r'\s+', ' ', _to_ascii(body)).strip()[:12000]
        b = _xml_escape(body_flat)
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Player><name>{t}</name><position>{b}</position></Player>'
        ).encode("utf-8")

    def _handle_article(self, qs: dict) -> bytes:
        # Real server uses ?loc=US&a=<google_news_url>
        article_url = qs.get("a", [None])[0]
        if not article_url:
            return self._article_xml("Error", "Missing article URL parameter.")
        log.info("article.php a=%s", article_url[:80])
        real_url = article_url
        try:
            real_url = decode_google_news_url(article_url)
            log.info("article.php real=%s", real_url[:80])

            # For non-Google-News URLs, use RSS body cache populated at feed time.
            # This avoids fetching from sites that block scrapers (403, WAF, etc.)
            if real_url in _rss_body_cache:
                cached_title, cached_body = _rss_body_cache[real_url]
                log.info("article.php: serving from RSS body cache")
                return self._article_xml(cached_title, cached_body)

            html = fetch_article(real_url)
            title, body = extract_article(html)

            # If trafilatura couldn't extract and we have a cached body, use it
            if body == "Could not extract article text." and real_url in _rss_body_cache:
                cached_title, cached_body = _rss_body_cache[real_url]
                log.info("article.php: trafilatura failed, falling back to RSS cache")
                return self._article_xml(cached_title, cached_body)

        except urllib.error.HTTPError as exc:
            log.warning("Article HTTP %s: %s", exc.code, real_url[:80])
            # Try RSS cache before giving an error
            if real_url in _rss_body_cache:
                cached_title, cached_body = _rss_body_cache[real_url]
                log.info("article.php: HTTP %s, falling back to RSS cache", exc.code)
                return self._article_xml(cached_title, cached_body)
            if exc.code == 401:
                title = "Login required"
                body = ("This article is behind a paywall or requires you to be "
                        "logged in. Try finding a free version of this story.")
            elif exc.code == 403:
                title = "Access blocked"
                body = ("This website blocked the request. The article may be "
                        "region-restricted or require a subscription.")
            elif exc.code == 404:
                title = "Article not found"
                body = "This article may have been moved or deleted by the publisher."
            else:
                title = "Could not load article"
                body = f"The server returned an error ({exc.code}). Try again later."
        except Exception as exc:
            log.warning("Article fetch failed: %s", exc)
            if real_url in _rss_body_cache:
                cached_title, cached_body = _rss_body_cache[real_url]
                log.info("article.php: fetch failed, falling back to RSS cache")
                return self._article_xml(cached_title, cached_body)
            title = "Could not load article"
            body = "A network error occurred while loading this article. Try again."
        return self._article_xml(title, body)


if __name__ == "__main__":
    if "--debug" in sys.argv:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    else:
        logging.disable(logging.CRITICAL)

    print("Newsstand Mirror Server (raw TCP mode)")
    print("Listening on port 80")
    print("Mac OS 9 HOSTS: www.getnewsstand.com A 10.0.3.2")
    print("-" * 60)
    try:
        socketserver.TCPServer.allow_reuse_address = True
        server = socketserver.ThreadingTCPServer(("", 80), NewsstandHandler)
        threading.Thread(target=_cache_cleanup_loop, daemon=True, name="cache-cleanup").start()
        server.serve_forever(poll_interval=1.0)
    except PermissionError:
        print("ERROR: Run with sudo python3 newsstand_server.py")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Server stopped.")
