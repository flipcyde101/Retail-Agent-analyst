"""The language guess, on sentences taken from real answers of the agent."""

import pytest

from agent.language import detect_language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("There are 30,805 price records for Carrefour stores.", "en"),
        ("Paris has the most stores, with 349 locations in the database.", "en"),
        ("Hay 25.296 precios registrados en tiendas de Grenoble.", "es"),
        ("Carrefour tiene tiendas en 241 ciudades distintas y E.Leclerc en 179.", "es"),
        ("La base contient 2 285 magasins, tous en France, et ils sont dans le pays.", "fr"),
        ("44 736 prix ont été relevés en 2025 dans les magasins.", "fr"),
        ("La date du prix le plus récent de la base est le 12 novembre 2026.", "fr"),
    ],
)
def test_real_answers_are_recognised(text, language):
    assert detect_language(text) == language


@pytest.mark.parametrize("text", ["", "30805", "E.Leclerc", "Auchan 1.83"])
def test_too_little_text_is_unknown_rather_than_a_guess(text):
    assert detect_language(text) == "unknown"


def test_an_english_refusal_with_two_no_is_not_taken_for_spanish():
    # Found in review: "no" was a Spanish marker, and a correct answer was sent back.
    assert detect_language("No data exists for Spain; no rows.") != "es"
    assert detect_language("No. There are no stores outside France.") == "en"


def test_an_opening_question_mark_is_enough_for_spanish():
    assert detect_language("¿Es más barato Mercadona o Carrefour?") == "es"
