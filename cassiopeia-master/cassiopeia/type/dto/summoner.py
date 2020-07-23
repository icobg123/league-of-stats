import cassiopeia.type.dto.common
import cassiopeia.type.core.common


if cassiopeia.type.dto.common.sqlalchemy_imported:
    import sqlalchemy
    import sqlalchemy.orm


@cassiopeia.type.core.common.inheritdocs
class RunePages(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Args:
        pages (list<RunePage>): collection of rune pages associated with the summoner
        summonerId (int): summoner ID
    """
    def __init__(self, dictionary):
        self.pages = [(RunePage(p) if not isinstance(p, RunePage) else p) for p in dictionary.get("pages", []) if p]
        self.summonerId = dictionary.get("summonerId", 0)

    @property
    def rune_ids(self):
        """
        Gets all rune IDs contained in this object
        """
        ids = set()
        for p in self.pages:
            ids = ids | p.rune_ids
        return ids


@cassiopeia.type.core.common.inheritdocs
class RunePage(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Gets all rune IDs contained in this object
    """
    def __init__(self, dictionary):
        self.current = dictionary.get("current", False)
        self.id = dictionary.get("id", 0)
        self.name = dictionary.get("name", "")
        self.slots = [(RuneSlot(s) if not isinstance(s, RuneSlot) else s) for s in dictionary.get("slots", []) if s]

    @property
    def rune_ids(self):
        """
        Args:
            current (bool): indicates if the page is the current page
            id (int): rune page ID
            name (str): rune page name
            slots (list<RuneSlot>): collection of rune slots associated with the rune page
        """
        ids = set()
        for s in self.slots:
            if s.runeId:
                ids.add(s.runeId)
        return ids


@cassiopeia.type.core.common.inheritdocs
class RuneSlot(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Args:
        current (bool): indicates if the page is the current page
        id (int): rune page ID
        name (str): rune page name
        slots (list<RuneSlot>): collection of rune slots associated with the rune page
    """
    def __init__(self, dictionary):
        self.runeId = dictionary.get("runeId", 0)
        self.runeSlotId = dictionary.get("runeSlotId", 0)


@cassiopeia.type.core.common.inheritdocs
class MainyPages(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Gets all rune IDs contained in this object
    """
    def __init__(self, dictionary):
        self.pages = [(MainyPage(p) if not isinstance(p, MainyPage) else p) for p in dictionary.get("pages", []) if p]
        self.summonerId = dictionary.get("summonerId", 0)

    @property
    def mainy_ids(self):
        """
        Args:
            runeId (int): rune ID associated with the rune slot. For static information correlating to rune IDs, please refer to the LoL Static Data API.
            runeSlotId (int): rune slot ID.
        """
        ids = set()
        for p in self.pages:
            ids = ids | p.mainy_ids
        return ids


@cassiopeia.type.core.common.inheritdocs
class MainyPage(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Args:
        runeId (int): rune ID associated with the rune slot. For static information correlating to rune IDs, please refer to the LoL Static Data API.
        runeSlotId (int): rune slot ID.
    """
    def __init__(self, dictionary):
        self.current = dictionary.get("current", False)
        self.id = dictionary.get("id", 0)
        self.mainies = [(Mainy(s) if not isinstance(s, Mainy) else s) for s in dictionary.get("mainies", []) if s]
        self.name = dictionary.get("name", "")

    @property
    def mainy_ids(self):
        """
        Args:
            pages (list<MainyPage>): collection of mainy pages associated with the summoner
            summonerId (int): summoner ID
        """
        ids = set()
        for m in self.mainies:
            if m.id:
                ids.add(m.id)
        return ids


@cassiopeia.type.core.common.inheritdocs
class Mainy(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Args:
        pages (list<MainyPage>): collection of mainy pages associated with the summoner
        summonerId (int): summoner ID
    """
    def __init__(self, dictionary):
        self.id = dictionary.get("id", 0)
        self.rank = dictionary.get("rank", 0)


@cassiopeia.type.core.common.inheritdocs
class Summoner(cassiopeia.type.dto.common.CassiopeiaDto):
    """
    Gets all mainy IDs contained in this object
    """
    def __init__(self, dictionary):
        self.id = dictionary.get("id", 0)
        self.name = dictionary.get("name", "")
        self.profileIconId = dictionary.get("profileIconId", 0)
        self.revisionDate = dictionary.get("revisionDate", 0)
        self.summonerLevel = dictionary.get("summonerLevel", 0)


###############################
# Dynamic SQLAlchemy bindings #
###############################
def _sa_bind_rune_page():
    global RunePage

    @cassiopeia.type.core.common.inheritdocs
    class RunePage(RunePage, cassiopeia.type.dto.common.BaseDB):
        __tablename__ = "RunePage"
        current = sqlalchemy.Column(sqlalchemy.Boolean)
        id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        name = sqlalchemy.Column(sqlalchemy.String(50))
        slots = sqlalchemy.orm.relationship("cassiopeia.type.dto.summoner.RuneSlot", cascade="all, delete-orphan, delete, merge", passive_deletes=True)


def _sa_bind_rune_slot():
    global RuneSlot

    @cassiopeia.type.core.common.inheritdocs
    class RuneSlot(RuneSlot, cassiopeia.type.dto.common.BaseDB):
        __tablename__ = "RuneSlot"
        runeId = sqlalchemy.Column(sqlalchemy.Integer)
        runeSlotId = sqlalchemy.Column(sqlalchemy.Integer)
        _id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        _page_id = sqlalchemy.Column(sqlalchemy.Integer, sqlalchemy.ForeignKey("RunePage.id", ondelete="CASCADE"))


def _sa_bind_mainy_page():
    global MainyPage

    @cassiopeia.type.core.common.inheritdocs
    class MainyPage(MainyPage, cassiopeia.type.dto.common.BaseDB):
        __tablename__ = "MainyPage"
        current = sqlalchemy.Column(sqlalchemy.Boolean)
        id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        mainies = sqlalchemy.orm.relationship("cassiopeia.type.dto.summoner.Mainy", cascade="all, delete-orphan, delete, merge", passive_deletes=True)
        name = sqlalchemy.Column(sqlalchemy.String(50))


def _sa_bind_mainy():
    global Mainy

    @cassiopeia.type.core.common.inheritdocs
    class Mainy(Mainy, cassiopeia.type.dto.common.BaseDB):
        __tablename__ = "MainySlot"
        id = sqlalchemy.Column(sqlalchemy.Integer)
        rank = sqlalchemy.Column(sqlalchemy.Integer)
        _id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        _page_id = sqlalchemy.Column(sqlalchemy.Integer, sqlalchemy.ForeignKey("MainyPage.id", ondelete="CASCADE"))


def _sa_bind_summoner():
    global Summoner

    @cassiopeia.type.core.common.inheritdocs
    class Summoner(Summoner, cassiopeia.type.dto.common.BaseDB):
        __tablename__ = "Summoner"
        id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        name = sqlalchemy.Column(sqlalchemy.String(30))
        profileIconId = sqlalchemy.Column(sqlalchemy.Integer)
        revisionDate = sqlalchemy.Column(sqlalchemy.BigInteger)
        summonerLevel = sqlalchemy.Column(sqlalchemy.Integer)


def _sa_bind_all():
    _sa_bind_rune_page()
    _sa_bind_rune_slot()
    _sa_bind_mainy_page()
    _sa_bind_mainy()
    _sa_bind_summoner()
