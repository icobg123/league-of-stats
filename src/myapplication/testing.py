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
    summonername = db.Column(db.Unicode(50), unique=True)
    summonerID = db.Column(db.Integer, unique=True)

    def __init__(self, id, summonername, summonerID):
        self.id = id
        self.summonername = summonername
        self.summonerID = summonerID

    def __repr__(self):
        return '< ID %r with summonerName %s and summonerID %s>' % (
            self.id, self.summonername, self.summonerID)


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
    username = riotapi.get_summoner_by_name(sumName)

    exists = db.session.query(summoners.summonerID).filter_by(
        summonername=username.name).scalar() is not None
    alreadyThere = ''
    if exists is False:
        addNewSumomner = summoners(id=3222, summonername=username.name,
                                   summonerID=username.id)
        app.logger.info(summoners.id)

        db.session.add(addNewSumomner)
        db.session.commit()
        app.logger.info(summoners.id)  # increment the primary id
    else:
        alreadyThere = 'They are there'

    
    return render_template('testhome.html', summoner=username, alreadyThere=alreadyThere)


if __name__ == '__main__':
    init(app)
    app.run(
        host=app.config['ip_address'],
        port=int(app.config['port'])
    )
