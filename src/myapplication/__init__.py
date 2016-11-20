from flask import Flask

app = Flask(__name__)
import myapplication.views
import myapplication.error
