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
