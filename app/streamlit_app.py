"""Entry point for the web app.

Run from the project folder:
    streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

# Let the pages import the `budget` package from the project folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Household Spending", page_icon=":material/payments:", layout="wide")

pages = [
    st.Page("pages/import_page.py", title="Import", icon=":material/upload_file:", default=True),
    st.Page("pages/review_page.py", title="Review", icon=":material/checklist:", url_path="review"),
    st.Page("pages/rules_page.py", title="Rules", icon=":material/rule:", url_path="rules"),
    # Transactions, Dashboard and Settings arrive in later phases.
]
st.navigation(pages).run()
