import requests
import os
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator
from flask import Flask, request, make_response, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

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

@app.route('/rss')
@limiter.limit("1/second", error_message='Rate limit exceeded')
def rss():

    # 1. Extract list from URL
    list_url = request.args.get('url', None)
    feed_items = []
    feed_title = ""
    feed_description = ""
    feed_url = ""

    if not list_url:
        return make_response("No URL Provided", 400)

    try:
        extracted_list_response = requests.get(f"https://api.diffbot.com/v3/list?token={DIFFBOT_TOKEN}&url={list_url}")
        extracted_list = extracted_list_response.json()
        if extracted_list.get("error", None):
            raise Exception(extracted_list.get("error", "Page Error"))
        feed_items = extracted_list.get("objects", [])[0].get("items", [])
        feed_title = extracted_list.get("objects", [])[0].get("title", feed_url)
        feed_description = extracted_list.get("objects", [])[0].get("pageUrl", "")
        feed_url = extracted_list.get("objects", [])[0].get("pageUrl", "")
    except Exception as e:
        print(e)
        return make_response(str(e), 400)

    # 2. Instantiate a Feed
    fg = FeedGenerator()
    fg.title(feed_title if feed_title else feed_url)
    fg.description(feed_description)
    fg.link(href=feed_url)
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

    # 4. Return feed
    response = make_response(fg.rss_str())
    response.headers.set('Content-Type', 'application/rss+xml')
    return response