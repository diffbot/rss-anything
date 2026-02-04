import hashlib
import os
import secrets
import threading
import time

import requests
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator
from flask import Flask, request, make_response, render_template, flash
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from urllib.parse import unquote

app = Flask(__name__)
app.secret_key = secrets.token_hex() # No need to persist this key between resets

load_dotenv()
DIFFBOT_TOKEN = os.getenv("DIFFBOT_TOKEN", None)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CACHE_TTL = int(os.getenv("CACHE_TTL", 900))  # 15 minutes default

# Configure Flask-Caching with Redis backend, fallback to simple cache for local dev
try:
    cache = Cache(app, config={
        'CACHE_TYPE': 'redis',
        'CACHE_REDIS_URL': REDIS_URL,
        'CACHE_DEFAULT_TIMEOUT': CACHE_TTL
    })
    # Test Redis connection
    cache.get('_test')
    CACHE_AVAILABLE = True
except Exception:
    # Fallback to simple in-memory cache if Redis unavailable
    cache = Cache(app, config={
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': CACHE_TTL
    })
    CACHE_AVAILABLE = False

# Rate limiting - use Redis if available for cross-worker support
storage_uri = REDIS_URL if CACHE_AVAILABLE else 'memory://'
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=storage_uri
)

# Distributed locking to prevent thundering herd on cache misses
_local_locks = {}
_local_locks_guard = threading.Lock()

class DistributedLock:
    """Redis-based distributed lock with fallback to threading lock."""
    def __init__(self, key, timeout=30):
        self.key = f"lock:{key}"
        self.timeout = timeout
        self.acquired = False
    
    def __enter__(self):
        if CACHE_AVAILABLE:
            # Use Redis SETNX for distributed locking
            redis_client = cache.cache._read_client
            while not redis_client.set(self.key, "1", nx=True, ex=self.timeout):
                time.sleep(0.05)
            self.acquired = True
        else:
            # Fallback to threading lock for local dev
            with _local_locks_guard:
                if self.key not in _local_locks:
                    _local_locks[self.key] = threading.Lock()
                self._lock = _local_locks[self.key]
            self._lock.acquire()
            self.acquired = True
        return self
    
    def __exit__(self, *args):
        if self.acquired:
            if CACHE_AVAILABLE:
                redis_client = cache.cache._read_client
                redis_client.delete(self.key)
            else:
                self._lock.release()

# =============================================================================
# Helper Functions
# =============================================================================

def normalize_url(url):
    """Normalize URL for cache key and rate limiting."""
    if not url:
        return ''
    return unquote(url).lower().strip()

def get_url_for_rate_limit():
    """Get normalized URL from request for rate limiting."""
    return normalize_url(request.args.get('url', ''))

def add_cache_headers(response, content):
    """Add HTTP cache headers for RSS readers."""
    response.headers['Cache-Control'] = f'public, max-age={CACHE_TTL}'
    etag = hashlib.md5(content).hexdigest()
    response.headers['ETag'] = f'"{etag}"'
    return response

def diffbot_extract(url):
    """Extract list data from Diffbot API."""
    try:
        payload = {
            'token': DIFFBOT_TOKEN,
            'url': unquote(url),
            'paging': "false"
        }
        response = requests.get("https://api.diffbot.com/v3/list", params=payload)
        data = response.json()
        
        if data.get("error"):
            raise Exception(data.get("error", "Page Error"))

        obj = data.get("objects", [])[0]
        return {
            "items": obj.get("items", []),
            "title": obj.get("title", ""),
            "pageUrl": obj.get("pageUrl", ""),
            "icon": obj.get("icon", ""),
            "rss_url": obj.get("rss_url")
        }
    except IndexError:
        raise Exception("No content found on page")
    except Exception as e:
        raise Exception(str(e))

def build_feed(data):
    """Build FeedGenerator from extracted data."""
    feed_items = data.get("items", [])
    feed_title = data.get("title", "")
    feed_url = data.get("pageUrl", "")
    feed_icon = data.get("icon", "")
    actual_rss_url = data.get("rss_url")

    fg = FeedGenerator()
    fg.load_extension('media')
    fg.title(feed_title or feed_url)
    fg.id(feed_url)
    fg.description(feed_url)
    fg.icon(feed_icon)
    fg.link(href=f"https://rss.diffbot.com/rss?url={feed_url}", rel='self')
    fg.link(href=feed_url, rel='alternate')
    fg.managingEditor("jerome@diffbot.com (Jerome Choo)")
    fg.docs("https://rss.diffbot.com")

    for article in reversed(feed_items):
        if article.get("title") and article.get("link"):
            fe = fg.add_entry()
            fe.title(article.get("title", ""))
            fe.id(article.get("link", ""))
            fe.link(href=article.get("link", ""))
            if image := article.get("image"):
                fe.enclosure(url=image, length=0, type='image/jpeg')
                fe.media.thumbnail(url=image)
            fe.description(article.get("summary", ""))
            if author := article.get("byline") or article.get("author"):
                fe.author(name=author)
            if published_date := article.get("date"):
                fe.pubDate(published_date)

    return fg, actual_rss_url

def generate_feed(url):
    """Generate RSS feed with caching and locking to reduce unnecessary Diffbot API calls."""
    if not url:
        raise Exception("No URL Provided")

    cache_key = f"feed:{normalize_url(url)}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return build_feed(cached_data)

    # Use distributed lock to prevent thundering herd on cache miss
    with DistributedLock(cache_key):
        # Double-check cache after acquiring lock (another request may have populated it)
        cached_data = cache.get(cache_key)
        if cached_data:
            return build_feed(cached_data)
        
        feed_data = diffbot_extract(url)
        cache.set(cache_key, feed_data, timeout=CACHE_TTL)
    
    return build_feed(feed_data)

# =============================================================================
# Routes
# =============================================================================

@app.route('/')
def index():
    return render_template('home.html')

@app.route('/feeds')
def feeds():
    feed_detail = {}
    actual_rss_url = ""
    try:
        # Attempt to Generate an RSS Feed
        fg, actual_rss_url = generate_feed(request.args.get('url', None))
        # Feed Details
        feed_detail = {
            "title": fg.title(),
            "description": fg.description(),
            "link": fg.link(),
            "icon": fg.icon()
        }
    except Exception as e:
        flash(str(e), 'Error')
    return render_template('home.html', page_url=request.args.get('url', ''), feed_detail=feed_detail, actual_rss_url=actual_rss_url)

@app.route('/rss')
@limiter.limit("60/minute", error_message='Rate limit exceeded')
def rss():
    try:
        fg, rss_url = generate_feed(request.args.get('url', None))
        content = fg.rss_str()
        response = make_response(content)
        response.headers.set('Content-Type', 'application/rss+xml')
        add_cache_headers(response, content)
        return response
    except Exception as e:
        return make_response(str(e), 400)

@app.route('/atom')
@limiter.limit("60/minute", error_message='Rate limit exceeded')
def atom():
    try:
        fg, rss_url = generate_feed(request.args.get('url', None))
        content = fg.atom_str()
        response = make_response(content)
        response.headers.set('Content-Type', 'application/atom+xml')
        add_cache_headers(response, content)
        return response
    except Exception as e:
        return make_response(str(e), 400)
