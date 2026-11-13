import pytest

from data.chains import normalise_chain


@pytest.mark.parametrize(
    ("brand", "name", "expected"),
    [
        ("E.Leclerc", None, "E.Leclerc"),
        ("E. Leclerc", None, "E.Leclerc"),
        ("Centre Commercial E.Leclerc", None, "E.Leclerc"),
        ("E.Leclerc Drive", None, "E.Leclerc"),
        ("Carrefour Market", None, "Carrefour"),
        ("Carrefour City", None, "Carrefour"),
        ("Intermarché Hyper", None, "Intermarché"),
        ("Auchan Supermarché", None, "Auchan"),
        ("Super U", None, "U"),
        ("U Express", None, "U"),
        ("Utile", None, "U"),
        ("Monop'", None, "Monoprix"),
        ("ALDI", None, "Aldi"),
        ("Le Petit Casino", None, "Casino"),
    ],
)
def test_spellings_and_formats_collapse_into_one_chain(brand, name, expected):
    assert normalise_chain(brand, name) == expected


def test_known_chain_is_recognised_from_the_shop_name_when_brand_is_missing():
    assert normalise_chain(None, "La Rochelle - Villeneuve Intermarché") == "Intermarché"


def test_unknown_brand_is_kept_as_its_own_chain():
    assert normalise_chain("Botanic", "Botanic Grenoble") == "Botanic"


def test_independent_shop_has_no_chain():
    assert normalise_chain(None, "L'Éléfàn") is None
    assert normalise_chain(None, None) is None


def test_short_names_do_not_match_inside_other_words():
    # "Aldi" must not match "Geraldine", nor "U" rules match any name containing a u.
    assert normalise_chain(None, "Chez Geraldine") is None
    assert normalise_chain(None, "Au Bon Marché") is None
