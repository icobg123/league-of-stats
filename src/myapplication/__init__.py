from flask import Flask

app = Flask(__name__)
import myapplication.run
import myapplication.error
