"""Login screen shown before any page (one shared password).

Streamlit can read the browser's cookies (st.context.cookies) but has no
built-in way to set one, so a tiny script in an invisible component writes
the signed "remember me" cookie. The cookie only holds an expiry date and a
signature (see budget/auth.py), never the password.
"""

import time

import streamlit as st
import streamlit.components.v1 as components

from budget.auth import TOKEN_DAYS, make_token, token_is_valid, verify_password
from budget.config import app_settings

COOKIE = "household_budget_login"


def _write_cookie(value: str, max_age_seconds: int) -> None:
    components.html(
        f"<script>parent.document.cookie = '{COOKIE}={value}; max-age={max_age_seconds}; "
        f"path=/; SameSite=Strict';</script>",
        height=0,
    )


def require_login() -> None:
    """Return if the visitor is logged in; otherwise show the login form and stop the page."""
    settings = app_settings()
    if settings.get("require_password", True) is False:
        st.sidebar.caption("Password check is off (require_password = false in secrets.toml).")
        return

    password_hash, cookie_secret = settings.get("password_hash"), settings.get("cookie_secret")
    if not password_hash or not cookie_secret:
        st.error("No password is set yet. Run `python -m budget.auth` and put the [app] lines it "
                 "prints into config/secrets.toml, then restart the app.")
        st.stop()

    state = st.session_state
    # The browser sends its cookies when the page loads, so right after "Log out"
    # the old cookie is still visible here; logged_out makes us ignore it.
    if (not state.get("logged_in") and not state.get("logged_out")
            and token_is_valid(st.context.cookies.get(COOKIE), cookie_secret, password_hash)):
        state.logged_in = True

    if state.get("logged_in"):
        if "new_cookie" in state:                       # just logged in with "remember me"
            _write_cookie(state.pop("new_cookie"), TOKEN_DAYS * 86400)
        if st.sidebar.button("Log out"):
            state.logged_in = False
            state.logged_out = True
            st.rerun()
        return

    if state.get("logged_out"):
        _write_cookie("", 0)   # remove the remember-me cookie from this browser

    st.title("Household Spending")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        remember = st.checkbox(f"Remember this browser for {TOKEN_DAYS} days", value=True)
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        if verify_password(password, password_hash):
            state.logged_in = True
            state.logged_out = False
            if remember:
                state.new_cookie = make_token(cookie_secret, password_hash)
            st.rerun()
        time.sleep(1)  # slow down guessing
        st.error("Wrong password.")
    st.stop()
