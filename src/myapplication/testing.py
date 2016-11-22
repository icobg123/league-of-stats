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
riotapi.set_region("EUNE")

riotapi.print_calls(True)

riotapi.set_api_key('23da5b32-c763-41ed-8d7a-f2b1023d8174')
riotapi.set_load_policy(LoadPolicy.lazy)


class Summoners(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
    summonerName = db.Column(db.Unicode(50), unique=True)
    summonerLevel = db.Column(db.Integer)

    # summonerID = db.Column(db.Integer, unique=True)

    # def __init__(self, id, summonerName, summonerID):
    def __init__(self, id, summonerName, summonerLevel):
        self.id = id
        self.summonerName = summonerName
        self.summonerLevel = summonerLevel
        # self.summonerID = summonerID

    def __repr__(self):
        return ' %s' % self.summonerName

        # class RunePages(db.Model):
        #     id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
        #     runePageName = db.Column(db.Unicode(50))
        #     runePageCurrent = db.Column(db.BOOLEAN)
        #     runePageRunes = db.Column(db.Unicode(500))
        #     summoner_ID = db.Column(db.Integer, db.ForeignKey('summoner.id'))
        #     foreign_keys =[Summoners.id]
        # summonerID = db.Column(db.Integer, unique=True)

        # def __init__(self, id, summonerName, summonerID):
        # def __init__(self, id, runePageName, runePageCurrent, runePageRunes, summonerID):
        #     self.id = id
        #     self.runePageName = runePageName
        #     self.runePageCurrent = runePageCurrent
        #     self.runePageRunes = runePageRunes
        #     self.summonerID = summonerID
        #     # self.summonerID = summonerID
        #
        # def __repr__(self):
        #     return ' %s' % self.runePageName


db.drop_all()
db.create_all()


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
        print ('Could not read  config: '), config_location


@app.route('/', methods=['GET', 'POST'])
def home():
    err = None

    if request.method == 'POST':
        if not request.form['summonerName']:
            err = 'Please provide your summoner name'
        # elif not request.form['region']:
        #     err = 'Please set your region'
        else:
            summonerName = request.form['summonerName']
            return redirect(url_for('summoner', sumName=summonerName))
            # return redirect(url_for('summoner', sumName=summonerName,
            #                         region=riotapi.set_region(request.form['region'])))

    return render_template('homepage.html', err=err)


@app.route('/summoner/<sumName>')
def summoner(sumName):
    # riotapi.set_region(region)
    exists = db.session.query(Summoners.id).filter(
        Summoners.summonerName.ilike(sumName)).scalar() is not None
    alreadyThere = ''
    summonerData = []
    username = riotapi.get_summoner_by_name(sumName)
    if exists is False:

        addNewSumomner = Summoners(id=username.id, summonerName=username.name,
                                   summonerLevel=username.level)

        db.session.add(addNewSumomner)
        db.session.commit()

        rune_pages = riotapi.get_rune_pages(username)
        #
        # addRunePages = RunePages(id=rune_pages.id, runePageName=rune_pages.name,
        #                          runePageCurrent=rune_pages.current,
        #                          runePageRunes=rune_pages.rune, summonerID=username.id)
        #
        # db.session.add(addRunePages)
        # db.session.commit()

        # covert the results from the query into a dict with column names and value pairs
        query = Summoners.query.filter_by(id=username.id).first()

        dictionary = dict((col, getattr(query, col))
                          for col in
                          query.__table__.columns.keys())

        match_list = username.match_list()
        match = match_list[0].match()

        return render_template('testhome.html', summoner=username,
                               alreadyThere=alreadyThere,
                               rune_pages=rune_pages,
                               summonerData=dictionary,
                               match_info=match)

    else:
        alreadyThere = 'They are there'

        query = Summoners.query.filter(Summoners.summonerName.contains(sumName)).first()

        dictionary = dict((col, getattr(query, col))
                          for col in
                          query.__table__.columns.keys())

        summonerData = dict((row.summonerLevel, row)
                            for row in
                            db.session.query(Summoners).filter(
                                Summoners.summonerName.ilike(sumName)
                            ))

        rune_pages = riotapi.get_rune_pages(username)
        return render_template('testhomeSummoner.html', alreadyThere=alreadyThere,
                               summonerData=dictionary,
                               rune_pages=rune_pages
                               )


if __name__ == '__main__':
    init(app)
    app.run(
        host=app.config['ip_address'],
        port=int(app.config['port'])
    )
