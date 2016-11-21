# -*- coding: utf-8 -*-
import sys
import json
import ConfigParser
from flask import Flask, render_template, url_for, redirect, request, send_from_directory, \
    abort, \
    g, \
    flash, session
from random import shuffle
from riotwatcher import RiotWatcher, EUROPE_NORDIC_EAST
from cassiopeia import riotapi
from cassiopeia.type.core.common import LoadPolicy

from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config[
    'SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/tc/CW2/src/myapplication/var/league_of_legends.db'

db = SQLAlchemy(app)

dblocation = 'var/sqlite3.db'


class summoners(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    summonername = db.Column(db.Unicode(50), unique=True)
    summonerID = db.Column(db.Integer, unique=True)

    def __init__(self, id, summonername, summonerID):
        self.id = id
        self.summonername = summonername
        self.summonerID = summonerID

    def __repr__(self):
        return '< ID %r with summonerName %s and summonerID %s>' % (
            self.id, self.summonername, self.summonerID)




def init(app):
    config = ConfigParser.ConfigParser()
    config_location = "etc/config.cfg"
    try:
        config.read(config_location)

        app.config['DEBUG'] = config.get("config", "debug")
        app.config['ip_address'] = config.get("config", "ip_address")
        app.config['port'] = config.get("config", "port")
        app.config['url'] = config.get("config", "url")
        app.secret_key = os.urandom(24)
        app.permanent_session_lifetime = timedelta(seconds=60)

    except:
        print ('Could not read config: '), config_location


# Database functions

# get access to database connection
def get_db():
    db = getattr(g, 'db', None)
    if db is None:
        db = sqlite3.connect(dblocation)
        g.db = db
        db.row_factory = sqlite3.Row
    return db


# @app.before_request
# def before_request():
#     g.user = None
#     if 'user_id' in session:
#         g.user = query_db('select * from user where user_id = ?',
#                           [session['user_id']], one=True)

# initialize schema
def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()


# combines getting the cursor,executing and fetching tha results
def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv


@app.before_request
def before_request():
    g.user = None
    if 'summonerName' in session:
        g.user = query_db('select * from summoner where summonerName = ?',
                          [session['summonerName']], one=True)


# after each request
@app.teardown_appcontext
def close_db_connection(exception):
    db = getattr(g, 'db', None)
    if db is None:
        db.close()


# query for username
def get_userid(username):
    rv = query_db('select user_id from user where username = ?',
                  [username], one=True)
    return rv[0] if rv else None


# query for summoner
def get_summoner(summonerName):
    rv = query_db('select summonerDBid from summoner where summonerName = ?',
                  [summonerName], one=True)
    return rv[0] if rv else None


w = RiotWatcher('23da5b32-c763-41ed-8d7a-f2b1023d8174')

# os.environ["DEV_KEY"] = "23da5b32-c763-41ed-8d7a-f2b1023d8174"
riotapi.set_region("EUNE")

riotapi.print_calls(True)

riotapi.set_api_key('23da5b32-c763-41ed-8d7a-f2b1023d8174')
riotapi.set_load_policy(LoadPolicy.lazy)


@app.route('/', methods=['GET', 'POST'])
def home():
    err = None
    if request.method == 'POST':
        if not request.form['summonerName']:
            err = 'Please provide us with your summoner name'
        elif not request.form['region']:
            err = 'Please write your E-mail address'
        elif get_summoner(request.form['summonerName']) is not None:
            err = 'The username is already taken'
        else:
            db = get_db()
            db.execute('''INSERT INTO summonerDBid(
              summonerName) VALUES (?)''',
                       [request.form['summonerName']])
            db.commit()
            flash('Your summoner info has been stored')
            summonerName = request.form['summonerName']

            return redirect(url_for('summoner', sumName=summonerName,
                                    region=riotapi.set_region(request.form['region'])))

    return render_template('homepage.html', err=err)


@app.route('/summoner/<sumName>')
def summoner(sumName):
    # images_names = os.listdir('myapplication/static/images/planes')
    # planeswalkerNames = planeswalker_dict.keys()
    username = riotapi.get_summoner_by_name(sumName)
    # rankedstats = riotapi.get_ranked_stats(sumName)
    champions = riotapi.get_champions()
    # championIds = riotapi.get_champions()
    # mapping = {champion.id: champion.name for champion in championIds}
    #
    # runes = riotapi.get_rune_pages(sumName)
    sumId = username.id
    match1 = riotapi.get_match_list(username, 3)
    championName = riotapi.get_champion_by_name(match1)
    # match = riotapi.get_match(2034758953)
    masteryStats = riotapi.get_champion_mastery_score(username)
    return render_template('testhome.html', summoner=username, champions=champions,
                           match=match1, championName=championName,
                           masteryscore=masteryStats)


if __name__ == '__main__':
    init(app)
    app.run(
        host=app.config['ip_address'],
        port=int(app.config['port'])
    )
