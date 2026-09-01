"""Plain data returned by the client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Format(Enum):
    """A media format, e.g. book, audiobook, Blu-ray, etc.

    Values are the exact option strings of the "Medienart" dropdown on the
    site's advanced search form (as of 2026-09), in the dropdown's order.
    """

    ARTICLE = "Aufsatz"
    PICTORIAL = "Bildliche Darstellung"
    BLU_RAY = "Blu-ray Disc"
    BOOK = "Buch"
    CD = "CD"
    CD_ROM = "CD-ROM"
    MACHINE_READABLE_MATERIAL = "Computerlesbares Material"
    COMPUTER_GAME = "Computerspiel"
    SLIDES = "Dias"
    FLOPPY_DISK = "Diskette"
    PRINTED_MATTER = "Druckschrift"
    DVD_AUDIO = "DVD-Audio"
    DVD_ROM = "DVD-ROM"
    DVD = "DVD-Video"
    E_AUDIO = "E-Audio"
    E_BOOK = "E-Book"
    E_JOURNAL = "E-Journal"
    E_LEARNING = "E-Learning"
    ELECTRONIC_RESOURCE = "Elektronische Ressource"
    ELECTRONIC_STORAGE_MEDIUM = "Elektronisches Speichermedium"
    E_VIDEO = "E-Video"
    FILM_MATERIAL = "Filmmaterial"
    OBJECT = "Gegenstand"
    DEVICE = "Gerät"
    GRAPHIC = "Grafik"
    MANUSCRIPT = "Handschrift"
    MAP_OR_PLAN = "Karte/Plan"
    CONSOLE_GAME = "Konsolenspiel"
    TABLETOP_GAME = "Konventionelles Spiel"
    TABLETOP_GAME_ACCESSIBLE = "Konventionelles Spiel (für Blinde und Sehbehinderte)"
    ART_PRINT = "Kunstdruck"
    MAP = "Landkarte"
    PAINTING = "Malerei"
    CASSETTE = "MC"
    MEDIA_COMBINATION = "Medienkombination"
    MICROCARD = "Mikrocard"
    MICROFICHE = "Mikrofiche"
    MICROFILM = "Mikrofilm"
    MP3 = "MP3"
    MUSICAL_INSTRUMENT = "Musikinstrument"
    SHEET_MUSIC = "Noten"
    BRAILLE_MUSIC = "Notenschrift (für Blinde)"
    SCULPTURE = "Plastik"
    BRAILLE_CONTRACTED = "Punktschrift (Kurzschrift)"
    BRAILLE_FULL = "Punktschrift (Vollschrift)"
    TACTILE_RELIEF = "Relief (für Blinde)"
    VINYL_RECORD = "Schallplatte"
    OTHER = "Sonstiges Material oder Gegenstand"
    BLU_RAY_4K = "Ultra HD Blu-ray"
    UMD = "UMD"
    VIDEO = "Video"
    JOURNAL_ISSUE = "Zeitschriftenheft"
    PERIODICAL = "Zeitschrift/Zeitung"


@dataclass(frozen=True)
class Loan:
    """One currently borrowed item."""

    title: str
    library: str
    due_date: date | None
    note: str = ""
    renewals: int | None = None
    media_type: str = ""
    shelf_mark: str = ""
    item_number: str = ""

    @property
    def days_left(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


@dataclass(frozen=True)
class SearchResult:
    """One hit from the catalogue."""

    position: int
    title: str
    author: str = ""
    year: str = ""
    media_type: str = ""
