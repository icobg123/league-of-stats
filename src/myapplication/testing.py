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


class summoners(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
    summonerName = db.Column(db.Unicode(50), unique=True)
    revisionDate = db.Column(db.Integer)
    summonerLevel = db.Column(db.Integer)

    # summonerID = db.Column(db.Integer, unique=True)

    # def __init__(self, id, summonerName, summonerID):
    def __init__(self, id, summonerName, summonerLevel):
        self.id = id
        self.summonerName = summonerName
        self.summonerLevel = summonerLevel
        # self.summonerID = summonerID

    def __repr__(self):
        return ' %s' % (
            self.summonerName)


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
        print ('Could not read config: '), config_location


@app.route('/summoner/<sumName>')
def summoner(sumName):
    exists = db.session.query(summoners.id).filter(
        summoners.summonerName.ilike(sumName)).scalar() is not None
    alreadyThere = ''
    summonerData = []
    if exists is False:
        username = riotapi.get_summoner_by_name(sumName)
        addNewSumomner = summoners(id=username.id, summonerName=username.name,
                                   summonerLevel=username.level)

        app.logger.info(summoners.id)

        db.session.add(addNewSumomner)
        db.session.commit()
        app.logger.info(summoners.id)  # increment the primary id

        # covert the results from the query into a dict with column names and value pairs
        query = summoners.query.filter_by(id=username.id).first()

        dictionary = dict((col, getattr(query, col))
                          for col in
                          query.__table__.columns.keys())

        return render_template('testhome.html', summoner=username,
                               alreadyThere=alreadyThere,
                               summonerData=dictionary)

    else:
        alreadyThere = 'They are there'

        query = summoners.query.filter(summoners.summonerName.contains(sumName)).first()

        dictionary = dict((col, getattr(query, col))
                          for col in
                          query.__table__.columns.keys())

        summonerData = dict((row.summonerLevel, row)
                            for row in
                            db.session.query(summoners).filter(
                                summoners.summonerName.ilike(sumName)
                            ))
        return render_template('testhomeSummoner.html', alreadyThere=alreadyThere,
                               summonerData=dictionary)


if __name__ == '__main__':
    init(app)
    app.run(
        host=app.config['ip_address'],
        port=int(app.config['port'])
    )
