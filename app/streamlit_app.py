"""The demo. Run it from the repo root:

    uv run python -m streamlit run app/streamlit_app.py

This file only sets up the two pages; each one lives in its own module.
"""

import sys
from pathlib import Path

# Streamlit puts this file's folder on the import path, not the repo root, and the pages
# import the agent and the evaluation from there.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import ask_page, evaluation_page  # noqa: E402

st.set_page_config(page_title="Retail analyst agent", page_icon="🛒", layout="wide")
# A wide layout for the tables, but not the whole width of a large screen: lines of text
# that long are hard to read.
st.html("<style>.block-container { max-width: 1100px; }</style>")
with st.sidebar:
    st.caption(
        "An LLM agent over 185,337 supermarket prices from "
        "[Open Prices](https://prices.openfoodfacts.org) (Open Food Facts, ODbL)."
    )
    st.caption(
        "Every answer shows the SQL behind it. "
        "[Code and evaluation on GitHub](https://github.com/pitelet222/Retail-Agent-analyst)."
    )
page = st.navigation(
    [
        st.Page(ask_page.render, title="Ask", url_path="ask", default=True),
        st.Page(evaluation_page.render, title="Evaluation", url_path="evaluation"),
    ]
)
page.run()
