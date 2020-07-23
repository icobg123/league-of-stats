from cassiopeia import baseriotapi
from .. import int_test_handler


def test_all():
    print("dto/staticdataapi tests...")
    test_champion()
    test_champions()
    test_item()
    test_items()
    test_language_strings()
    test_languages()
    test_maps()
    test_mainy()
    test_mainies()
    test_realm()
    test_rune()
    test_runes()
    test_summoner_spell()
    test_summoner_spells()
    test_versions()


def test_champion():
    int_test_handler.test_result(baseriotapi.get_champion(int_test_handler.champion_id))


def test_champions():
    int_test_handler.test_result(baseriotapi.get_champions())


def test_item():
    int_test_handler.test_result(baseriotapi.get_item(int_test_handler.item_id))


def test_items():
    int_test_handler.test_result(baseriotapi.get_items())


def test_language_strings():
    int_test_handler.test_result(baseriotapi.get_language_strings())


def test_languages():
    int_test_handler.test_result(baseriotapi.get_languages())


def test_maps():
    int_test_handler.test_result(baseriotapi.get_maps())


def test_mainy():
    int_test_handler.test_result(baseriotapi.get_mainy(int_test_handler.mainy_id))


def test_mainies():
    int_test_handler.test_result(baseriotapi.get_mainies())


def test_realm():
    int_test_handler.test_result(baseriotapi.get_realm())


def test_rune():
    int_test_handler.test_result(baseriotapi.get_rune(int_test_handler.rune_id))


def test_runes():
    int_test_handler.test_result(baseriotapi.get_runes())


def test_summoner_spell():
    int_test_handler.test_result(baseriotapi.get_summoner_spell(int_test_handler.summoner_spell_id))


def test_summoner_spells():
    int_test_handler.test_result(baseriotapi.get_summoner_spells())


def test_versions():
    int_test_handler.test_result(baseriotapi.get_versions())
