# -*- coding: utf-8 -*-
import sys
import os
import collections
import urllib, json
import ConfigParser
from flask import Flask, render_template, url_for, redirect, request, send_from_directory, \
    abort, \
    g, \
    flash, session
from random import shuffle
from riotwatcher import RiotWatcher, EUROPE_NORDIC_EAST
from cassiopeia import riotapi
from cassiopeia.type.core.common import LoadPolicy, StatSummaryType
from cassiopeia.type.api.exception import APIError
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config[
    'SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/tc/CW2/src/myapplication/var/league_of_legends.db'
staticDataItems = "http://ddragon.leagueoflegends.com/cdn/6.22.1/data/en_US/item.json"
response = urllib.urlopen(staticDataItems)
data = json.loads(response.read())
datapoints = data['data']

db = SQLAlchemy(app)

dblocation = 'var/sqlite3.db'
# riotapi.set_region("EUNE")

riotapi.print_calls(True)

riotapi.set_api_key('23da5b32-c763-41ed-8d7a-f2b1023d8174')
# riotapi.set_load_policy(LoadPolicy.lazy)
riotapi.set_load_policy("eager")


class Summoners(db.Model):
    __tablename__ = 'summoners'
    id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
    summonerName = db.Column(db.Unicode(50), unique=True)
    summonerLevel = db.Column(db.Integer)
    summonerIcon = db.Column(db.Integer)

    # summonerID = db.Column(db.Integer, unique=True)

    # def __init__(self, id, summonerName, summonerID):
    def __init__(self, id, summonerName, summonerLevel, summonerIcon):
        self.id = id
        self.summonerName = summonerName
        self.summonerLevel = summonerLevel
        self.summonerIcon = summonerIcon
        # self.summonerID = summonerID

    def __repr__(self):
        return ' %s' % self.summonerName


class Items(db.Model):
    __tablename__ = 'items'
    id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
    itemName = db.Column(db.Unicode(50))
    itemImage = db.Column(db.Unicode)
    itemDescription = db.Column(db.Unicode)
    itemGold = db.Column(db.Integer)

    # summonerID = db.Column(db.Integer, unique=True)

    # def __init__(self, id, summonerName, summonerID):
    def __init__(self, id, itemName, itemImage, itemDescription, itemGold):
        self.id = id
        self.itemName = itemName

        # self.itemDesc = itemDesc
        # self.itemText = itemText
        self.itemImage = itemImage
        self.itemDescription = itemDescription
        self.itemGold = itemGold
        # self.summonerID = summonerID

    def __repr__(self):
        return ' %s' % self.itemName


class Matches(db.Model):
    __tablename__ = 'matches'
    id = db.Column(db.Integer, primary_key=True, default=lambda: uuid.uuid4().hex)
    matchRegion = db.Column(db.Unicode(50))
    matchDuration = db.Column(db.Integer)
    matchParticipants = db.Column(db.Unicode(1000))
    matchMode = db.Column(db.Unicode(50))

    # summonerID = db.Column(db.Integer, unique=True)

    # def __init__(self, id, summonerName, summonerID):
    def __init__(self, id, matchRegion, matchDuration, matchParticipants, matchMode):
        self.id = id
        self.matchRegion = matchRegion
        self.matchDuration = matchDuration
        # self.itemDesc = itemDesc
        # self.itemText = itemText
        self.matchParticipants = matchParticipants
        self.matchMode = matchMode
        # self.summonerID = summonerID

    def __repr__(self):
        return ' %s' % self.id

        # class Teams(db.Model):
        #     __tablename__ = 'teams'
        #     teamId = db.Column(db.Integer)
        #     matchRegion = db.Column(db.Unicode(50))
        #     matchDuration = db.Column(db.Integer)
        #     matchId = db.Column(ForeignKey('matches.id'))
        #     matchParticipants = db.Column(db.Unicode(1000))
        #     matchMode = db.Column(db.Unicode(50))
        #
        #     matches = relationship("Matches")
        #
        #     __table_args__ = (
        #         PrimaryKeyConstraint('teamId', 'matchId'),
        #         ForeignKeyConstraint(
        #             ['writer_id', 'magazine_id'],
        #             ['writer.id', 'writer.magazine_id']
        #         ),
        #     )
        #
        #
        #     # summonerID = db.Column(db.Integer, unique=True)
        #
        #     # def __init__(self, id, summonerName, summonerID):
        #     def __init__(self, id, matchRegion, matchDuration, matchParticipants, matchMode):
        #         self.id = id
        #         self.matchRegion = matchRegion
        #         self.matchDuration = matchDuration
        #         # self.itemDesc = itemDesc
        #         # self.itemText = itemText
        #         self.matchParticipants = matchParticipants
        #         self.matchMode = matchMode
        #         # self.summonerID = summonerID
        #
        #     def __repr__(self):
        #         return ' %s' % self.id
        #



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

for item in datapoints.iteritems():
    # print (item[1]['name'], item[1]['gold']['base'], item[1]['image']['full'])

    addItem = Items(id=item[0], itemName=item[1]['name'],
                    itemDescription=item[1]['description'],
                    itemImage=item[1]['image']['full'],
                    itemGold=item[1]['gold']['base'])
    db.session.add(addItem)
    db.session.commit()


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
    except:
        print ('Could not read  config: '), config_location


@app.route('/', methods=['GET', 'POST'])
def home():
    err = None

    if request.method == 'POST':
        if not request.form['summonerName']:
            err = 'Please provide your summoner name'
        elif not request.form['region']:
            err = 'Please set your region'
        else:
            summonerName = request.form['summonerName']
            region = request.form['region']
            return redirect(url_for('summoner', sumName=summonerName, region=region))

    return render_template('homepage.html', err=err)


@app.route('/json')
def json():
    return render_template('homepage.html', json=data)


@app.route('/items')
def items():
    itemQuery = Items.query.all()
    itemsD = {}
    for item in itemQuery:
        itemsD[item] = to_dict(item)

    return render_template('items.html', item_info=itemsD)


@app.route('/summoner/<region>/<sumName>')
def summoner(region, sumName):
    riotapi.set_region(region)
    riotapi.set_load_policy(LoadPolicy.eager)
    # check if the summoner is already in the database
    exists = db.session.query(Summoners.id).filter(
        Summoners.summonerName.ilike(sumName)).scalar() is not None
    alreadyThere = ''
    summonerData = []

    if exists is False:
        try:
            username = riotapi.get_summoner_by_name(sumName)
            addNewSumomner = Summoners(id=username.id, summonerName=username.name,
                                       summonerLevel=username.level,
                                       summonerIcon=username.profile_icon_id)

            db.session.add(addNewSumomner)
            db.session.commit()

            rune_pages = riotapi.get_rune_pages(username)

            # covert the results from the query into a dict with column names and value pairs
            query = Summoners.query.filter_by(id=username.id).first()

            summnersDic = to_dict(query)

            match_list = username.match_list()
            match = match_list[0].match()

            return render_template('testhomeSummoner.html', summoner=username,
                                   alreadyThere=alreadyThere,
                                   match_info=match,
                                   summonerData=summnersDic,
                                   rune_pages=rune_pages, region=region
                                   )
        except APIError as error:
            if error.error_code in [404]:
                return render_template('404.html', summoner=sumName)
    else:

        alreadyThere = 'They are there'

        try:
            username = riotapi.get_summoner_by_name(sumName)
            rune_pages = riotapi.get_rune_pages(username)

            query = Summoners.query.filter(
                Summoners.summonerName.contains(sumName)).first()

            # summnersDic = dict((col, getattr(query, col))
            #                    for col in
            #                    query.__table__.columns.keys())

            summonerData = dict((row.summonerLevel, row)
                                for row in
                                db.session.query(Summoners).filter(
                                    Summoners.summonerName.ilike(sumName)
                                ))
            # covert the results from the query into a dict with column names and value pairs
            summnersDic = to_dict(query)

            match_list = username.match_list()
            match = match_list[0].match()

            return render_template('testhomeSummoner.html', summoner=username,
                                   alreadyThere=alreadyThere,
                                   match_info=match,
                                   summonerData=summnersDic,
                                   rune_pages=rune_pages, region=region
                                   )
        except APIError as error:
            if error.error_code in [404]:
                return render_template('404.html', summoner=sumName)

                # rune_pages = riotapi.get_rune_pages(username)


@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', error=error, )


def to_dict(model_instance, query_instance=None):
    if hasattr(model_instance, '__table__'):
        return {c.name: str(getattr(model_instance, c.name)) for c in
                model_instance.__table__.columns}
    else:
        cols = query_instance.column_descriptions
        return {cols[i]['name']: model_instance[i] for i in range(len(cols))}


if __name__ == '__main__':
    init(app)
    app.run(
        host=app.config['ip_address'],
        port=int(app.config['port'])
    )
