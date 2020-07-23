from cassiopeia import baseriotapi
from .. import int_test_handler


def test_all():
    print("dto/leagueapi tests...")
    test_leagues_by_summoner()
    test_league_entries_by_summoner()
    test_leagues_by_team()
    test_league_entries_by_team()
    test_challenger()
    test_main()


def test_leagues_by_summoner():
    int_test_handler.test_result(baseriotapi.get_leagues_by_summoner(int_test_handler.summoner_id))


def test_league_entries_by_summoner():
    int_test_handler.test_result(baseriotapi.get_league_entries_by_summoner(int_test_handler.summoner_id))


def test_leagues_by_team():
    int_test_handler.test_result(baseriotapi.get_leagues_by_team(int_test_handler.team_id))


def test_league_entries_by_team():
    int_test_handler.test_result(baseriotapi.get_league_entries_by_team(int_test_handler.team_id))


def test_challenger():
    int_test_handler.test_result(baseriotapi.get_challenger("RANKED_SOLO_5x5"))


def test_main():
    int_test_handler.test_result(baseriotapi.get_main("RANKED_SOLO_5x5"))
