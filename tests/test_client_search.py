"""Format filtering on search(). No network: the session is a stub."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from voebb.client import VoebbClient
from voebb.config import Credentials
from voebb.models import Format
from voebb.parsers import format_checkbox_ids
from voebb.session import FormState

# A trimmed Trefferliste: two hits, the filter tree with a "Medienart" branch
# (labels carry their hit counts) and a "Schlagwort" branch that reuses one of
# the same labels, plus the Filtern button under its positional name.
RESULT_PAGE = """
<html><body><form name="Form0" action="/aDISWeb/app">
<input name="$Autosuggest" type="search"/>
<input name="$Button$2" type="submit" value="Filtern"/>
<div class="cbtree_div" id="PTL1_tree_1"><ul>
  <li class="cbtree_branch_li"><button>Medienart</button>
    <div class="cbtree_divsub"><ul>
      <li><a class="cbtree_leaf_a" id="lnk-PTL1_tree_1_1">Buch (Print) <span>(87)</span></a></li>
      <li><a class="cbtree_leaf_a" id="lnk-PTL1_tree_1_2">Blu-ray Disc <span>(6)</span></a></li>
      <li><a class="cbtree_leaf_a" id="lnk-PTL1_tree_1_3">sonstiges Material oder Gegenstand <span>(1)</span></a></li>
    </ul></div>
  </li>
  <li class="cbtree_branch_li"><button>Schlagwort</button>
    <div class="cbtree_divsub"><ul>
      <li><a class="cbtree_leaf_a" id="lnk-PTL1_tree_1_9">Blu-ray Disc <span>(3)</span></a></li>
    </ul></div>
  </li>
</ul></div>
<ul>
  <li class="rList_li"><span class="rList_num">1</span>
    <span class="rList_titel">Der Prozess</span></li>
  <li class="rList_li"><span class="rList_num">2</span>
    <span class="rList_titel">Das Schloss</span></li>
</ul>
</form></body></html>
"""

# What the server renders after Filtern: only the Blu-ray hit is left.
FILTERED_PAGE = """
<html><body><form name="Form0" action="/aDISWeb/app">
<ul>
  <li class="rList_li"><span class="rList_num">1</span>
    <span class="rList_titel">Der Prozess (Blu-ray)</span></li>
</ul>
</form></body></html>
"""


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# -- resolving formats to facet checkboxes --------------------------------


def test_maps_labels_to_checkbox_ids():
    assert format_checkbox_ids(soup(RESULT_PAGE), [Format.BLU_RAY]) == ["sub-PTL1_tree_1_2"]


def test_only_matches_inside_the_medienart_branch():
    """'Blu-ray Disc' also exists under Schlagwort; it must not win."""
    assert "sub-PTL1_tree_1_9" not in format_checkbox_ids(soup(RESULT_PAGE), [Format.BLU_RAY])


def test_facet_label_aliases():
    """Format.BOOK is 'Buch', but the facet says 'Buch (Print)'."""
    assert format_checkbox_ids(soup(RESULT_PAGE), [Format.BOOK]) == ["sub-PTL1_tree_1_1"]


def test_labels_compare_case_insensitively():
    """The facet lowercases 'sonstiges ...'; the dropdown capitalizes it."""
    assert format_checkbox_ids(soup(RESULT_PAGE), [Format.OTHER]) == ["sub-PTL1_tree_1_3"]


def test_formats_without_hits_are_dropped():
    assert format_checkbox_ids(soup(RESULT_PAGE), [Format.UMD, Format.BOOK]) == [
        "sub-PTL1_tree_1_1"
    ]


def test_page_without_tree_yields_nothing():
    assert format_checkbox_ids(soup("<html><body/></html>"), [Format.BOOK]) == []


# -- search() driving the filter ------------------------------------------


class SearchSession:
    """Replays canned result pages and records every submitted control dict."""

    def __init__(self, pages: list[BeautifulSoup]) -> None:
        self.pages = pages
        self.submitted: list[dict] = []
        self.form = FormState(action="/aDISWeb/app", fields={"$Autosuggest": ""})
        self.url = "https://example.invalid/"

    def submit(self, controls: dict | None = None) -> BeautifulSoup:
        self.submitted.append(controls or {})
        return self.pages[len(self.submitted) - 1]


@pytest.fixture
def searching(monkeypatch):
    """A (client, session) pair whose session replays the given pages."""

    def make(*pages: str) -> tuple[VoebbClient, SearchSession]:
        session = SearchSession([soup(page) for page in pages])
        monkeypatch.setattr("voebb.client.AdisSession", lambda **kwargs: session)
        return VoebbClient(Credentials(user="123", password="secret")), session

    return make


def test_no_formats_searches_once(searching):
    client, session = searching(RESULT_PAGE)
    assert [h.title for h in client.search("kafka")] == ["Der Prozess", "Das Schloss"]
    assert session.submitted == [{"$Autosuggest": "kafka", "$Button": "Suchen"}]


def test_formats_tick_facets_and_press_filtern(searching):
    client, session = searching(RESULT_PAGE, FILTERED_PAGE)
    hits = client.search("kafka", formats=[Format.BLU_RAY, Format.BOOK])
    assert [h.title for h in hits] == ["Der Prozess (Blu-ray)"]
    assert session.submitted[1] == {
        "$CbTree_text": ["sub-PTL1_tree_1_2", "sub-PTL1_tree_1_1"],
        "$Button$2": "Filtern",
    }


def test_formats_without_hits_return_empty_without_second_request(searching):
    client, session = searching(RESULT_PAGE)
    assert client.search("kafka", formats=[Format.UMD]) == []
    assert len(session.submitted) == 1


def test_empty_formats_means_no_filter(searching):
    client, session = searching(RESULT_PAGE)
    assert len(client.search("kafka", formats=[])) == 2
    assert len(session.submitted) == 1
