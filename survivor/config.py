from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

BASE_URL = "https://www.live-tennis.cn"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SLAMS = ["澳网", "法网", "温网", "美网"]
# 加拿大站每年在蒙特利尔 / 多伦多之间对调 ATP 与 WTA，两个名字都算同一档强制 1000。
WTA_FORCED = ["印第安维尔斯", "迈阿密", "马德里", "罗马", "蒙特利尔", "多伦多", "辛辛那提", "北京"]
WTA_UNCOMBINED = ["多哈", "迪拜", "武汉"]
ATP_FORCED = ["印第安维尔斯", "迈阿密", "马德里", "罗马", "多伦多", "蒙特利尔", "辛辛那提", "上海", "巴黎"]
ATP_UNCOMBINED = ["蒙特卡洛"]
ATP_FINALS = ["都灵"]
WTA_FINALS = ["利雅得"]

TITLES = {
    "atp": "ATP幸存者冠军排名",
    "wta": "WTA幸存者冠军排名",
    "atp_instant": "ATP幸存者即时排名",
    "wta_instant": "WTA幸存者即时排名",
    "combined": "幸存者冠军联合排名",
    "combined_instant": "幸存者即时联合排名",
    "atp_show": "ATP球员成绩统计",
    "wta_show": "WTA球员成绩统计",
}

OUTPUT_FILES = {
    "atp": "atp_ranking.html",
    "wta": "wta_ranking.html",
    "atp_instant": "atp_instant_ranking.html",
    "wta_instant": "wta_instant_ranking.html",
    "combined": "combined_ranking.html",
    "combined_instant": "combined_instant_ranking.html",
    "atp_show": "atp_show.html",
    "wta_show": "wta_show.html",
}

PUBLISH_FILES = [
    "index.html",
    "atp_ranking.html",
    "wta_ranking.html",
    "atp_instant_ranking.html",
    "wta_instant_ranking.html",
    "combined_ranking.html",
    "combined_instant_ranking.html",
    "atp_show.html",
    "wta_show.html",
]

STATS_CACHE = ROOT / "data" / "player_events.json"
RANK_PUBLISH_FILES = [
    "index.html",
    "atp_ranking.html",
    "wta_ranking.html",
    "atp_instant_ranking.html",
    "wta_instant_ranking.html",
    "combined_ranking.html",
    "combined_instant_ranking.html",
]
STATS_PUBLISH_FILES = [
    "atp_show.html",
    "wta_show.html",
    "data/player_events.json",
]

REMOTE_SSH = "git@github.com:Zechen-3IS/survivor_champion_ranking.git"

RANK_PAGE = {
    "atp": f"{BASE_URL}/zh/survivor/rank/MS/race",
    "wta": f"{BASE_URL}/zh/survivor/rank/WS/race",
}

RANK_AJAX = {
    "atp": f"{BASE_URL}/zh/survivor/rank/1/race",
    "wta": f"{BASE_URL}/zh/survivor/rank/2/race",
}

YEAR_PAGE = {
    "atp": f"{BASE_URL}/zh/survivor/rank/MS/year",
    "wta": f"{BASE_URL}/zh/survivor/rank/WS/year",
}

YEAR_AJAX = {
    "atp": f"{BASE_URL}/zh/survivor/rank/1/year",
    "wta": f"{BASE_URL}/zh/survivor/rank/2/year",
}

CALENDAR_URL = f"{BASE_URL}/zh/survivor/calendar/{{year}}"
SEED_CUTOFF = 16

MENU_URL = f"{BASE_URL}/zh/survivor/menu"

META_COLS = ["用户名", "主键", "状态", "升降", "主选球员", "备选球员", "排名", "总分", "上周总分", "上周排名", "杀手球员", "存活天数", "选人明细"]


@dataclass
class TourRules:
    tennis_type: str
    slams: list[str] = field(default_factory=lambda: list(SLAMS))
    forced: list[str] = field(default_factory=list)
    uncombined: list[str] = field(default_factory=list)
    finals: list[str] = field(default_factory=list)
    forced_keep: int = 0
    uncombined_keep: int = 0
    other_keep: int = 0

    @classmethod
    def for_tour(cls, tennis_type: str) -> "TourRules":
        if tennis_type == "wta":
            return cls(
                tennis_type="wta",
                forced=list(WTA_FORCED),
                uncombined=list(WTA_UNCOMBINED),
                finals=list(WTA_FINALS),
                forced_keep=6,
                uncombined_keep=1,
                other_keep=7,
            )
        if tennis_type == "atp":
            return cls(
                tennis_type="atp",
                forced=list(ATP_FORCED),
                uncombined=list(ATP_UNCOMBINED),
                finals=list(ATP_FINALS),
                forced_keep=5,
                uncombined_keep=0,
                other_keep=9,
            )
        raise ValueError(f"unknown tennis type: {tennis_type}")


@dataclass
class WeekEvent:
    tennis_type: str
    name: str
    page_id: str
    year: str
    gender: str
    ajax_id: str | None = None
    level: str = ""

    @property
    def score_page(self) -> str:
        return f"{BASE_URL}/zh/survivor/event/{self.page_id}/{self.year}/{self.gender}/score"

    @property
    def detail_page(self) -> str:
        return f"{BASE_URL}/zh/survivor/event/{self.page_id}/{self.year}/{self.gender}/detail"

    @property
    def my_page(self) -> str:
        return f"{BASE_URL}/zh/survivor/event/{self.page_id}/{self.year}/{self.gender}/my"
