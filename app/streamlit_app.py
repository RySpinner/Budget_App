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

from app.auth_gate import require_login  # noqa: E402

require_login()   # shows the login form and stops here until the password is entered

pages = [
    st.Page("views/dashboard_page.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("views/import_page.py", title="Import", icon=":material/upload_file:", url_path="import"),
    st.Page("views/review_page.py", title="Review", icon=":material/checklist:", url_path="review"),
    st.Page("views/transactions_page.py", title="Transactions", icon=":material/receipt_long:", url_path="transactions"),
    st.Page("views/budgets_page.py", title="Budgets", icon=":material/savings:", url_path="budgets"),
    st.Page("views/rules_page.py", title="Rules", icon=":material/rule:", url_path="rules"),
    # A Settings page (categories, cards, people) has no phase yet; see TODO.md.
]
st.navigation(pages).run()
