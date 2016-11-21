import random

from cassiopeia import riotapi

riotapi.set_region("NA")
riotapi.set_api_key("23da5b32-c763-41ed-8d7a-f2b1023d8174")

summoner = riotapi.get_summoner_by_name("FatalElement")
print (summoner.id)
print("{name} is a level {level} summoner on the NA server.".format(name=summoner.name, level=summoner.level))

champions = riotapi.get_champions()
random_champion = random.choice(champions)
print("He enjoys playing LoL on all different champions, like {name}.".format(name=random_champion.name))

challenger_league = riotapi.get_challenger()
best_na = challenger_league[0].summoner
print("He's much better at writing Python code than he is at LoL. He'll never be as good as {name}.".format(name=best_na.name))