"""Format filtering on search(). No network: the session is a stub."""

from __future__ import annotations

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

FILTERED_PAGE = RESULT_PAGE.replace("Das Schloss", "").replace(
    '<span class="rList_titel">Der Prozess</span>',
    '<span class="rList_titel">Der Prozess (Blu-ray)</span>',
)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestFormatCheckboxIds:
    def test_maps_labels_to_checkbox_ids(self):
        ids = format_checkbox_ids(soup(RESULT_PAGE), [Format.BLU_RAY])
        assert ids == ["sub-PTL1_tree_1_2"]

    def test_only_matches_inside_the_medienart_branch(self):
        """'Blu-ray Disc' also exists under Schlagwort; it must not win."""
        ids = format_checkbox_ids(soup(RESULT_PAGE), [Format.BLU_RAY])
        assert "sub-PTL1_tree_1_9" not in ids

    def test_facet_label_aliases(self):
        """Format.BOOK is 'Buch', but the facet says 'Buch (Print)'."""
        ids = format_checkbox_ids(soup(RESULT_PAGE), [Format.BOOK])
        assert ids == ["sub-PTL1_tree_1_1"]

    def test_labels_compare_case_insensitively(self):
        """The facet lowercases 'sonstiges ...'; the dropdown capitalizes it."""
        ids = format_checkbox_ids(soup(RESULT_PAGE), [Format.OTHER])
        assert ids == ["sub-PTL1_tree_1_3"]

    def test_formats_without_hits_are_dropped(self):
        ids = format_checkbox_ids(soup(RESULT_PAGE), [Format.UMD, Format.BOOK])
        assert ids == ["sub-PTL1_tree_1_1"]

    def test_page_without_tree_yields_nothing(self):
        assert format_checkbox_ids(soup("<html><body/></html>"), [Format.BOOK]) == []


class SearchSession:
    """Replays canned pages and records every submitted control dict."""

    def __init__(self, pages: list[str]) -> None:
        self.pages = [soup(html) for html in pages]
        self.submitted: list[dict] = []
        self.form = FormState(action="/aDISWeb/app", fields={"$Autosuggest": ""})
        self.url = "https://example.invalid/"

    @property
    def page(self):
        return self.pages[len(self.submitted) - 1]

    def submit(self, controls=None):
        self.submitted.append(controls or {})
        return self.pages[len(self.submitted) - 1]


def client_with(pages: list[str]) -> tuple[VoebbClient, SearchSession]:
    client = VoebbClient.__new__(VoebbClient)
    client._credentials = Credentials(user="123", password="secret")
    client._logged_in = False
    client.session = SearchSession(pages)
    return client, client.session


class TestSearchFormatFilter:
    def test_no_formats_searches_once(self):
        client, session = client_with([RESULT_PAGE])
        hits = client.search("kafka")
        assert [h.title for h in hits] == ["Der Prozess", "Das Schloss"]
        assert session.submitted == [{"$Autosuggest": "kafka", "$Button": "Suchen"}]

    def test_formats_tick_facets_and_press_filtern(self):
        client, session = client_with([RESULT_PAGE, FILTERED_PAGE])
        hits = client.search("kafka", formats=[Format.BLU_RAY, Format.BOOK])
        assert [h.title for h in hits] == ["Der Prozess (Blu-ray)"]
        assert session.submitted[1] == {
            "$CbTree_text": ["sub-PTL1_tree_1_2", "sub-PTL1_tree_1_1"],
            "$Button$2": "Filtern",
        }

    def test_formats_without_hits_return_empty_without_second_request(self):
        client, session = client_with([RESULT_PAGE])
        assert client.search("kafka", formats=[Format.UMD]) == []
        assert len(session.submitted) == 1

    def test_empty_formats_means_no_filter(self):
        client, session = client_with([RESULT_PAGE])
        assert len(client.search("kafka", formats=[])) == 2
        assert len(session.submitted) == 1
