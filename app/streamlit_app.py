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

st.set_page_config(page_title="Retail analyst agent", layout="wide")
page = st.navigation(
    [
        st.Page(ask_page.render, title="Ask", url_path="ask", default=True),
        st.Page(evaluation_page.render, title="Evaluation", url_path="evaluation"),
    ]
)
page.run()
