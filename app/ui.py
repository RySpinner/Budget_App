"""Small helpers shared by the app pages."""


def md(text: str) -> str:
    """Make text safe for Streamlit markdown (st.write, st.success, st.error...).

    Streamlit renders text between two "$" signs as a math formula, so a
    message like "$5.00 of $10.00" would come out garbled. Escaping each "$"
    shows it as a plain dollar sign.
    """
    return str(text).replace("$", "\\$")
