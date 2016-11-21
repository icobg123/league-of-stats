# -*- coding: utf-8 -*-
import sys

reload(sys)
sys.setdefaultencoding("utf-8")
import os
import json

from myapplication import app
from flask import render_template, url_for, redirect, send_from_directory, abort
from random import shuffle
from riotwatcher import RiotWatcher, EUROPE_NORDIC_EAST
from cassiopeia import riotapi
from cassiopeia.type.core.common import LoadPolicy
from collections import namedtuple

w = RiotWatcher('23da5b32-c763-41ed-8d7a-f2b1023d8174')

# os.environ["DEV_KEY"] = "23da5b32-c763-41ed-8d7a-f2b1023d8174"
riotapi.set_region("EUNE")

riotapi.print_calls(True)

riotapi.set_api_key('23da5b32-c763-41ed-8d7a-f2b1023d8174')
riotapi.set_load_policy(LoadPolicy.lazy)

# Reading through a JSON files and converting the contents into a dictionaries
json_file_planeswalker = open('myapplication/static/data/planeswalkerInfo.json')
planeswalkerString = json_file_planeswalker.read()
planeswalkerData = json.loads(planeswalkerString)

json_file_homepage = open('myapplication/static/data/homepageInfo.json')
homePageString = json_file_homepage.read()
homePageData = json.loads(homePageString)

json_file_gallery = open('myapplication/static/data/galleryInfo.json')
galleryPageString = json_file_gallery.read()
galleryData = json.loads(galleryPageString)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
homePageTitle = homePageData
galleryTitle = galleryData


@app.route("/")
def home():
    images_names = os.listdir('myapplication/static/images/backgrounds')
    shuffle(images_names)
    print (images_names)
    return render_template('home.html', slideshow=images_names,
                           planeswalker=homePageTitle)


@app.route("/planeswalkers")
# return render_template('home.html', h=h)
def get_gallery():
    images_names = os.listdir('myapplication/static/images/walkers')
    print (images_names)
    return render_template('gallery.html', images_names=images_names,
                           planeswalker=galleryTitle)


@app.route('/planeswalkers/<pwName>')
def planeswalker(pwName):
    images_names = os.listdir('myapplication/static/images/planes')
    # planeswalkerNames = planeswalker_dict.keys()
    planeswalkerNames = planeswalkerData.keys()
    if pwName in planeswalkerNames:
        pwName = planeswalkerData.get(pwName)
        return render_template('planeswalker.html', planeswalker=pwName,
                               images_names=images_names)
    else:
        return abort(404)


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
    championName= riotapi.get_champion_by_name(match1)
    # match = riotapi.get_match(2034758953)
    masteryStats = riotapi.get_champion_mastery_score(username)
    return render_template('testhome.html', summoner=username, champions=champions,
                           match=match1,championName=championName,masteryscore=masteryStats)


#
# @app.route('/summoner/<sumName>')
# def summoner(sumName):
#     # images_names = os.listdir('myapplication/static/images/planes')
#     # planeswalkerNames = planeswalker_dict.keys()
#
#     username = w.get_summoner(name=sumName, region=EUROPE_NORDIC_EAST)
#     rankedstats = w.get_ranked_stats(username['id'], EUROPE_NORDIC_EAST)
#     championIds = w.static_get_champion_list()
#     runes = w.get_rune_pages(username['id'], EUROPE_NORDIC_EAST)
#     # my_ranked_stats_last_season = w.get_ranked_stats(me['id'], season=3)
#     # print (username)
#     return render_template('testhome.html', summoner=username, rankedstats=rankedstats,
#                            champions=championIds, runes=runes)
#     #
#     # if sumName in planeswalkerNames:
#     #     sumName = planeswalkerData.get(sumName)
#     #     return render_template('planeswalker.html', planeswalker=sumName,
#     #                            images_names=images_names)
#     # else:
#     #     return abort(404)


@app.route('/../static/images/walkers/<filename>')
def send_image(filename):
    return send_from_directory("images", filename)


@app.route('/../static/images/backgrounds/<filename>')
def send_bg_images(filename):
    return send_from_directory("images", filename)
