import requests
import os
import secrets
from datetime import datetime
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator
from flask import Flask, request, make_response, render_template, flash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from urllib.parse import quote_plus, unquote

app = Flask(__name__)
app.secret_key = secrets.token_hex() # No need to persist this key between resets

load_dotenv()
DIFFBOT_TOKEN = os.getenv("DIFFBOT_TOKEN", None)

# Really basic rate limiting to avoid taking down the app by bad actors
limiter = Limiter(
    get_remote_address, 
    app=app, 
    storage_uri='memory://'
    )

@app.route('/')
def index():
    return render_template('home.html')

@app.route('/feeds')
def feeds():
    feed_sample = []
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
    return render_template('home.html', url_encoded=quote_plus(request.args.get('url', '')), feed_detail=feed_detail, actual_rss_url=actual_rss_url)

@app.route('/rss')
@limiter.limit("1/second", error_message='Rate limit exceeded')
def rss():
    try:
        fg, rss_url = generate_feed(request.args.get('url', None))
        response = make_response(fg.rss_str())
        response.headers.set('Content-Type', 'application/rss+xml')
        return response
    except Exception as e:
        return make_response(str(e), 400)

@app.route('/atom')
@limiter.limit("1/second", error_message='Rate limit exceeded')
def atom():
    try:
        fg, rss_url = generate_feed(request.args.get('url', None))
        response = make_response(fg.atom_str())
        response.headers.set('Content-Type', 'application/atom+xml')
        return response
    except Exception as e:
        return make_response(str(e), 400)

def generate_feed(url):
    # 1. Extract list from URL
    list_url = url
    feed_items = []
    feed_title = ""
    feed_description = ""
    feed_icon= ""
    feed_url = ""
    actual_rss_url = None

    if not list_url:
        raise Exception("No URL Provided")

    try:
        payload = {
            'token': DIFFBOT_TOKEN,
            'url': unquote(list_url)
        }
        print(payload['url'])
        extracted_list_response = requests.get(f"https://api.diffbot.com/v3/list", params=payload)
        extracted_list = extracted_list_response.json()
        if extracted_list.get("error", None):
            print(extracted_list)
            raise Exception(extracted_list.get("error", "Page Error"))
        feed_items = extracted_list.get("objects", [])[0].get("items", [])
        feed_title = extracted_list.get("objects", [])[0].get("title", feed_url)
        feed_description = extracted_list.get("objects", [])[0].get("pageUrl", "")
        feed_icon = extracted_list.get("objects", [])[0].get("icon", "")
        feed_url = extracted_list.get("objects", [])[0].get("pageUrl", "")
        actual_rss_url = extracted_list.get("objects", [])[0].get("rss_url", None)
    except Exception as e:
        print("Exception: ", e)
        raise Exception(str(e))

    # 2. Instantiate a Feed
    fg = FeedGenerator()
    fg.title(feed_title if feed_title else feed_url)
    fg.id(feed_url)
    fg.description(feed_description)
    fg.icon(feed_icon)
    fg.link(href=feed_url, rel='alternate')
    fg.managingEditor(managingEditor="jerome@diffbot.com")
    fg.docs(docs="https://rss.diffbot.com")

    # 3. Generate feed item from list items
    for article in feed_items:
        if article.get("title", None) and article.get("link", None):
            fe = fg.add_entry()
            fe.title(article.get("title", ""))
            fe.id(article.get("link", ""))
            fe.link(href=article.get("link", ""))
            fe.description(article.get("summary", ""))
            if author := article.get("byline", None) or article.get("author", None):
                fe.author(name=author)
            if published_date := article.get("date", None):
                fe.pubDate(published_date)
    return fg, actual_rss_url