from myapplication import app
from flask import render_template, url_for, redirect
import json


json_file_error = open('myapplication/static/data/errorInfo.json')
errorString = json_file_error.read()
errorPageinfo = json.loads(errorString)

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', error=error, planeswalker=errorPageinfo)
