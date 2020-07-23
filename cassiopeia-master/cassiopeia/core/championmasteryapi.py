import cassiopeia.riotapi
import cassiopeia.core.requests
import cassiopeia.dto.championmainyapi
import cassiopeia.type.core.championmainy
import cassiopeia.type.core.common


def get_champion_mainy(summoner, champion):
    """
    Gets the ChampionMainy object for the specified summoner and champion

    Args:
        summoner (Summoner): the summoner to get champion mainy for
        champion (Champion): the desired champion

    Returns:
        ChampionMainy: the summoner's champion mainy value for the specified champion
    """
    champion_mainy = cassiopeia.dto.championmainyapi.get_champion_mainy(summoner.id, champion.id)
    return cassiopeia.type.core.championmainy.ChampionMainy(champion_mainy)


def get_champion_mainies(summoner):
    """
    Gets all the ChampionMainy objects for the specified summoner

    Args:
        summoner (Summoner): the summoner to get champion mainy for

    Returns:
        dict<Champion, ChampionMainy>: the summoner's champion mainies
    """
    champion_mainies = cassiopeia.dto.championmainyapi.get_champion_mainies(summoner.id)

    # Always load champions since we'll be using them here
    cassiopeia.riotapi.get_champions()
    return {cassiopeia.riotapi.get_champion_by_id(cm.championId): cassiopeia.type.core.championmainy.ChampionMainy(cm) for cm in champion_mainies}


def get_champion_mainy_score(summoner):
    """
    Gets the total champion mainy score for the specified summoner

    Args:
        summoner (Summoner): the summoner to get champion mainy for

    Returns:
        int: the summoner's total champion mainy score
    """
    return cassiopeia.dto.championmainyapi.get_champion_mainy_score(summoner.id)


def get_top_champion_mainies(summoner, max_entries=3):
    """
    Gets the top ChampionMainy objects for the specified summoner

    Args:
        summoner (Summoner): the summoner to get champion mainy for
        max_entries (int): the maximum number of entires to retrieve (default 3)

    Returns:
        list<ChampionMainy>: the summoner's top champion mainies
    """
    champion_mainies = cassiopeia.dto.championmainyapi.get_top_champion_mainies(summoner.id, max_entries)

    # Load required data if loading policy is eager
    if champion_mainies and cassiopeia.core.requests.load_policy is cassiopeia.type.core.common.LoadPolicy.eager:
        cassiopeia.riotapi.get_champions()

    return [cassiopeia.type.core.championmainy.ChampionMainy(cm) for cm in champion_mainies]
