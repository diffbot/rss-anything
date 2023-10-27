# base image
FROM nikolaik/python-nodejs:latest

WORKDIR /app
COPY ./ /app

# install requirements
RUN pip install -r requirements.txt

# start app
EXPOSE 8000
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:8000"]