import datetime

import cassiopeia.riotapi
import cassiopeia.type.core.common
import cassiopeia.type.dto.summoner


@cassiopeia.type.core.common.inheritdocs
class RunePage(cassiopeia.type.core.common.CassiopeiaObject):
    dto_type = cassiopeia.type.dto.summoner.RunePage

    def __str__(self):
        return "Rune Page ({name})".format(name=self.name)

    def __iter__(self):
        return iter(self.runes)

    def __len__(self):
        return len(self.runes)

    def __getitem__(self, index):
        return self.runes[index]

    def __eq__(self, other):
        return self.id == other.id

    def __ne__(self, other):
        return self.id != other.id

    def __hash__(self):
        return hash(self.id)

    @property
    def current(self):
        """
        Returns:
            bool: whether or not this rune page is active
        """
        return self.data.current

    @property
    def id(self):
        """
        Returns:
            int: the rune page's id
        """
        return self.data.id

    @property
    def name(self):
        """
        Returns:
            str: the rune page's name
        """
        return self.data.name

    @cassiopeia.type.core.common.lazyproperty
    def runes(self):
        """
        Returns:
            list<Rune>: the runes in this rune page
        """
        runes = {}
        for slot in self.data.slots:
            try:
                runes[slot.runeId] += 1
            except KeyError:
                runes[slot.runeId] = 1

        fetched = cassiopeia.riotapi.get_runes(list(runes.keys()))
        return {rune: runes[rune.id] for rune in fetched}


@cassiopeia.type.core.common.inheritdocs
class MainyPage(cassiopeia.type.core.common.CassiopeiaObject):
    dto_type = cassiopeia.type.dto.summoner.MainyPage

    def __str__(self):
        return "Mainy Page ({name})".format(name=self.name)

    def __iter__(self):
        return iter(self.mainies)

    def __len__(self):
        return len(self.mainies)

    def __getitem__(self, index):
        return self.mainies[index]

    def __eq__(self, other):
        return self.id == other.id

    def __ne__(self, other):
        return self.id != other.id

    def __hash__(self):
        return hash(self.id)

    @property
    def current(self):
        """
        Returns:
            bool: whether or not this mainy page is active
        """
        return self.data.current

    @property
    def id(self):
        """
        Returns:
            int: the mainy page's id
        """
        return self.data.id

    @cassiopeia.type.core.common.lazyproperty
    def mainies(self):
        """
        Returns:
            list<Mainy>: this mainy page's mainies
        """
        mainies = []
        ranks = []
        for mainy in self.data.mainies.items():
            mainies.append(mainy[0])
            ranks.append(mainy[1])
        return dict(zip(cassiopeia.riotapi.get_mainies(mainies), ranks))

    @property
    def name(self):
        """
        Returns:
            str: the mainy page's name
        """
        return self.data.name


@cassiopeia.type.core.common.inheritdocs
class Summoner(cassiopeia.type.core.common.CassiopeiaObject):
    dto_type = cassiopeia.type.dto.summoner.Summoner

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return self.id == other.id

    def __ne__(self, other):
        return self.id != other.id

    def __hash__(self):
        return hash(self.id)

    @property
    def id(self):
        """
        Returns:
            int: the summoner's id
        """
        return self.data.id

    @property
    def name(self):
        """
        Returns:
            str: the summoner's name
        """
        return self.data.name

    @property
    def profile_icon_id(self):
        """
        Returns:
            int: the ID of the summoner icon associated with the summoner
        """
        return self.data.profileIconId

    @cassiopeia.type.core.common.lazyproperty
    def modify_date(self):
        """
        Returns:
            datetime: the date this summoner was last modified specified as epoch milliseconds. The following events will update this timestamp: profile icon change, playing the tutorial or advanced tutorial, finishing a game, summoner name change
        """
        return datetime.datetime.utcfromtimestamp(self.data.revisionDate / 1000) if self.data.revisionDate else None

    @property
    def level(self):
        """
        Returns:
            int: the Summoner's level
        """
        return self.data.summonerLevel

    @cassiopeia.type.core.common.immutablemethod
    def current_game(self):
        """Gets the game the summoner is currently in, if they're in one

        Returns:
            Game: the game they're in (or None if they aren't in one)
        """
        return cassiopeia.riotapi.get_current_game(self)

    @cassiopeia.type.core.common.immutablemethod
    def recent_games(self):
        """
        Returns:
            int: the id of this summoner's rune page
        """
        return cassiopeia.riotapi.get_recent_games(self)

    @cassiopeia.type.core.common.immutablemethod
    def rune_pages(self):
        """
        Returns:
            str: the name of this summoner's rune page
        """
        return cassiopeia.riotapi.get_rune_pages(self)

    @cassiopeia.type.core.common.immutablemethod
    def mainy_pages(self):
        """
        Returns:
            list<Rune>: the runes in this rune page
        """
        return cassiopeia.riotapi.get_mainy_pages(self)

    @cassiopeia.type.core.common.immutablemethod
    def leagues(self):
        """
        Returns:
            bool: whether or not this mainy page is active
        """
        return cassiopeia.riotapi.get_leagues_by_summoner(self)

    @cassiopeia.type.core.common.immutablemethod
    def league_entries(self):
        """
        Returns:
            int: the id of the mainy page for this summoner
        """
        return cassiopeia.riotapi.get_league_entries_by_summoner(self)

    @cassiopeia.type.core.common.immutablemethod
    def teams(self):
        """
        Returns:
            list<Mainy>: this mainy page's mainies
        """
        return cassiopeia.riotapi.get_teams_by_summoner(self)

    @cassiopeia.type.core.common.immutablemethod
    def match_list(self, num_matches=0, begin_index=0, begin_time=0, end_time=0, champions=None, ranked_queues=None, seasons=None):
        """
        Args:
            num_matches (int): the maximum number of matches to retrieve. 0 will get as many as possible. (default 0)
            begin_index (int): the game index to start from (default 0)
            begin_time (int | datetime): the begin time to use for fetching games (default 0)
            end_time (int | datetime): the end time to use for fetching games (default 0)
            champions (Champion | list<Champion>): the champion(s) to limit the results to (default None)
            ranked_queues Queue | list<Queue>: the ranked queue(s) to limit the results to (default None)
            seasons (Season | list<Season>): the season(s) to limit the results to (default None)

        Returns:
            str: the name of this summoner's mainy page
        """
        return cassiopeia.riotapi.get_match_list(self, num_matches, begin_index, begin_time, end_time, champions, ranked_queues, seasons)

    @cassiopeia.type.core.common.immutablemethod
    def ranked_stats(self, season=None):
        """
        Returns:
            int: the summoner's id
        """
        return cassiopeia.riotapi.get_ranked_stats(self, season)

    @cassiopeia.type.core.common.immutablemethod
    def stats(self, season=None):
        """
        Returns:
            str: the summoner's name
        """
        return cassiopeia.riotapi.get_stats(self, season)

    @cassiopeia.type.core.common.immutablemethod
    def champion_mainy(self, champion):
        """
        Returns:
            int: the ID of the summoner icon associated with the summoner
        """
        return cassiopeia.riotapi.get_champion_mainy(self, champion)

    @cassiopeia.type.core.common.immutablemethod
    def champion_mainies(self):
        """
        Returns:
            datetime: the date this summoner was last modified specified as epoch milliseconds. The following events will update this timestamp: profile icon change, playing the tutorial or advanced tutorial, finishing a game, summoner name change
        """
        return cassiopeia.riotapi.get_champion_mainies(self)

    @cassiopeia.type.core.common.immutablemethod
    def champion_mainy_score(self):
        """
        Returns:
            int: the Summoner's level
        """
        return cassiopeia.riotapi.get_champion_mainy_score(self)

    @cassiopeia.type.core.common.immutablemethod
    def top_champion_mainies(self, max_entries=3):
        """
        Gets the game the summoner is currently in, if they're in one

        Returns:
            Game: the game they're in (or None if they aren't in one)
        """
        return cassiopeia.riotapi.get_top_champion_mainies(self, max_entries)


###############################
# Dynamic SQLAlchemy bindings #
###############################
def _sa_rebind_all():
    RunePage.dto_type = cassiopeia.type.dto.summoner.RunePage
    MainyPage.dto_type = cassiopeia.type.dto.summoner.MainyPage
    Summoner.dto_type = cassiopeia.type.dto.summoner.Summoner
