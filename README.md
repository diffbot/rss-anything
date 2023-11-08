# RSS Anything

Uses [Diffbot's Extract API](https://www.diffbot.com/products/extract/) to transform lists of links on websites into an RSS feed.

## Build Locally

**Requirements**
 * Python 3+
 	* [virtualenv](https://virtualenv.pypa.io/en/latest/) recommended but not necessary.
 * npm

```sh
pip install requirements.txt
npm install tailwindcss
npx tailwindcss -i ./static/custom.css -o ./static/main.css
flask run
```