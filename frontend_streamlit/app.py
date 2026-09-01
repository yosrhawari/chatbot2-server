import os

import pandas as pd
import requests
import streamlit as st

# Set page configuration
st.set_page_config(
    page_title="HAYETT 2000 — Espace Assurance",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)

# API Configuration
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")

DEFAULT_MESSAGE = {"role": "assistant", "content": "Bonjour ! Je suis votre assistante HAYETT 2000, posez-moi vos questions sur votre contrat 👇"}

# Sidebar quick actions (label -> question sent to the backend)
QUICK_ACTIONS = {
    "Voir mes garanties": "Quelles sont les garanties de mon contrat ?",
    "Montant de ma prime": "Quel est le montant de ma prime annuelle ?",
    "Faire une réclamation": "Comment effectuer une réclamation ?",
}

TYPING_HTML = '<div class="typing"><span></span><span></span><span></span></div>'

SVG_CALENDAR = (
    '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">'
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4V2M16 4V2M3 9h18"/></svg>'
)

SVG_LAYERS = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    '<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>'
)

# CSS layer reproducing the HAYETT 2000 corporate design (navy sidebar +
# light chat area). Streamlit chrome is hidden and native widgets are
# restyled to match the reference HTML pixel-for-pixel.
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --navy-950:#0b1a33;
  --navy-900:#122a4d;
  --navy-800:#1b3a66;
  --navy-line:#26426e;
  --blue-600:#2457c9;
  --blue-700:#1c46a6;
  --blue-50:#eef3fd;
  --slate-50:#f7f9fc;
  --slate-100:#eef1f6;
  --slate-200:#e2e7f0;
  --slate-400:#9aa5b8;
  --slate-600:#5b6779;
  --ink:#101828;
}

/* ---- Streamlit chrome ---- */
#MainMenu, footer, [data-testid="stHeader"], [data-testid="stStatusWidget"],
[data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarResizeHandle"], [data-testid="stDecoration"] {
  display: none !important;
}
.stApp { background: var(--slate-50); }
html, body, [class*="css"], .stApp, button, input, textarea, select {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
.block-container {
  max-width: 936px;
  margin: 0 auto;
  padding: 100px 48px 0 !important;
}

/* ---- Scrollbars ---- */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: #cbd5e3; border-radius: 8px; }
::-webkit-scrollbar-track { background: transparent; }
[data-testid="stSidebar"] ::-webkit-scrollbar-thumb { background: #26426e; }

/* ---- Sidebar ---- */
[data-testid="stSidebar"] {
  background: var(--navy-950) !important;
  border-right: 1px solid var(--navy-line);
  width: 288px !important;
  min-width: 288px !important;
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding: 26px 22px 20px; }

.brand {
  display: flex; align-items: center; gap: 12px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--navy-line);
  margin-bottom: 20px;
}
.brand-mark {
  width: 40px; height: 40px; border-radius: 10px;
  background: linear-gradient(135deg, var(--blue-600), var(--navy-800));
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.brand-mark svg { width: 22px; height: 22px; stroke: white; }
.brand-text .name { font-weight: 700; font-size: 16.5px; color: #fff; line-height: 1.2; }
.brand-text .role { font-size: 13px; color: #8ea0c4; margin-top: 1px; }

.section { margin-bottom: 22px; }
.section-label {
  font-size: 12.5px; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: #7b8caf; margin-bottom: 11px;
}

.status-card {
  display: flex; align-items: center; gap: 10px;
  background: rgba(255,255,255,.05);
  border: 1px solid var(--navy-line);
  border-radius: 10px;
  padding: 11px 13px;
  margin-bottom: 10px;
}
.status-dot {
  width: 20px; height: 20px; border-radius: 50%;
  background: #1f9d55;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.status-dot.off { background: #d64545; }
.status-dot svg { width: 11px; height: 11px; stroke: white; }
.status-info .t1 { font-size: 15px; font-weight: 600; color: #fff; }
.status-info .t2 { font-size: 13px; color: #8ea0c4; margin-top: 1px; }

.metric-card {
  background: rgba(255,255,255,.05);
  border: 1px solid var(--navy-line);
  border-radius: 10px;
  padding: 13px 14px;
  display: flex; justify-content: space-between; align-items: center;
}
.metric-card .label { font-size: 13.5px; color: #8ea0c4; }
.metric-card .value { font-size: 24px; font-weight: 800; color: #fff; }

/* Quick-action buttons: exact SVG icons injected via CSS background */
[data-testid="stSidebar"] [class*="st-key-qa"] button {
  display: flex; align-items: center; justify-content: flex-start; gap: 10px;
  width: 100%;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 8px;
  padding: 9px 10px !important;
  color: #c9d5ec !important;
  font-size: 14.5px; font-weight: 400;
  text-align: left;
  transition: background .15s, border-color .15s;
}
[data-testid="stSidebar"] [class*="st-key-qa"] button:hover {
  background: rgba(255,255,255,.06) !important;
  border-color: var(--navy-line) !important;
  color: #fff !important;
}
[data-testid="stSidebar"] [class*="st-key-qa"] button::before {
  content: "";
  width: 26px; height: 26px; border-radius: 6px;
  background-color: rgba(36,87,201,.25);
  background-position: center;
  background-repeat: no-repeat;
  background-size: 14px;
  flex-shrink: 0;
}
[data-testid="stSidebar"] .st-key-qa-garanties button::before {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238fb3ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M9 12l2 2 4-4M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z'/%3E%3C/svg%3E");
}
[data-testid="stSidebar"] .st-key-qa-prime button::before {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238fb3ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6'/%3E%3C/svg%3E");
}
[data-testid="stSidebar"] .st-key-qa-reclamation button::before {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238fb3ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z'/%3E%3Cpath d='M14 2v6h6M9 15h6M9 11h3'/%3E%3C/svg%3E");
}

/* Client account panel */
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stSidebar"] [data-testid="stForm"] {
  background: rgba(255,255,255,.04) !important;
  border: 1px solid var(--navy-line) !important;
  border-radius: 12px;
}
.client-desc { font-size: 14px; line-height: 1.55; color: #93a4c4; margin-bottom: 4px; }

[data-testid="stSidebar"] [data-testid="stTextInput"] label p {
  font-size: 13.5px; font-weight: 600; color: #c9d5ec;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input {
  background: #ffffff !important;
  border: 1px solid var(--navy-line) !important;
  border-radius: 8px !important;
  color: #101828 !important;
  font-size: 15px;
  font-weight: 500;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input::placeholder { color: #667085; }
[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus {
  border-color: var(--blue-600) !important;
  box-shadow: 0 0 0 3px rgba(36,87,201,.25) !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] button { color: #6d7fa3; }

[data-testid="stSidebar"] .stButton > button {
  border-radius: 8px;
  font-size: 14.5px;
  font-weight: 600;
  padding: 10px 14px;
}
[data-testid="stSidebar"] .st-key-login_btn button,
[data-testid="stSidebar"] .st-key-logout_btn button,
[data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button {
  background: var(--blue-600) !important;
  border: none !important;
  color: #fff !important;
  box-shadow: none !important;
}
[data-testid="stSidebar"] .st-key-login_btn button:hover,
[data-testid="stSidebar"] .st-key-logout_btn button:hover,
[data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button:hover {
  background: var(--blue-700) !important;
  border: none !important;
  color: #fff !important;
}
[data-testid="stSidebar"] .st-key-clear_btn button {
  background: transparent !important;
  border: 1px solid var(--navy-line) !important;
  color: #c9d5ec !important;
  box-shadow: none !important;
  font-size: 13px;
}
[data-testid="stSidebar"] .st-key-clear_btn button:hover {
  background: rgba(255,255,255,.06) !important;
  border: 1px solid var(--navy-line) !important;
  color: #fff !important;
}

/* ---- Topbar ---- */
.topbar {
  position: fixed; top: 0; left: 288px; right: 0; z-index: 100;
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 32px;
  background: #fff;
  border-bottom: 1px solid var(--slate-200);
}
.breadcrumb { font-size: 14px; color: var(--slate-600); font-weight: 500; }
.breadcrumb b { color: var(--ink); }
.topbar-right { display: flex; gap: 10px; align-items: center; }
.deploy-btn {
  border: 1px solid var(--slate-200);
  background: #fff;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px; font-weight: 600;
  color: var(--ink);
  cursor: pointer;
}
.deploy-btn:hover { background: var(--slate-100); }
.kebab {
  width: 34px; height: 34px;
  border: 1px solid var(--slate-200);
  background: #fff;
  border-radius: 8px;
  cursor: pointer;
  color: var(--slate-600);
}
.kebab:hover { background: var(--slate-100); }

/* ---- Hero ---- */
.hero-badge {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--blue-50);
  color: var(--blue-700);
  font-size: 12px; font-weight: 600;
  padding: 5px 11px;
  border-radius: 20px;
  margin-bottom: 14px;
}
.hero-badge svg { width: 13px; height: 13px; }
.hero-title {
  font-size: 34px !important;
  font-weight: 800 !important;
  color: var(--ink) !important;
  margin: 0 0 8px !important;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.hero-sub {
  font-size: 16px;
  color: var(--slate-600);
  margin: 0 0 28px !important;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.lang-pill {
  font-size: 11px; font-weight: 700;
  background: var(--slate-100);
  color: var(--slate-600);
  border-radius: 5px;
  padding: 3px 7px;
}

/* ---- Chat messages ---- */
[data-testid="stChatMessage"] {
  background: #fff;
  border: 1px solid var(--slate-200);
  border-radius: 12px;
  padding: 13px 16px;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
  gap: 11px;
  max-width: 700px;
  align-items: flex-start;
}
[data-testid="stChatMessage"] [data-testid="stChatMessageAvatarContainer"] { display: none; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  font-size: 16px; line-height: 1.6; color: var(--ink);
}
[data-testid="chat-message-assistant"]::before {
  content: "HA";
  width: 34px; height: 34px; border-radius: 9px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: var(--navy-900);
  color: #8fb3ff;
  font-size: 13px; font-weight: 700;
}
[data-testid="chat-message-user"] {
  background: var(--blue-600);
  border-color: var(--blue-600);
  flex-direction: row-reverse;
  margin-left: auto;
}
[data-testid="chat-message-user"]::before {
  content: "AH";
  width: 34px; height: 34px; border-radius: 9px;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: var(--blue-600);
  color: #fff;
  font-size: 13px; font-weight: 700;
}
[data-testid="chat-message-user"] [data-testid="stMarkdownContainer"],
[data-testid="chat-message-user"] [data-testid="stMarkdownContainer"] p,
[data-testid="chat-message-user"] [data-testid="stMarkdownContainer"] li,
[data-testid="chat-message-user"] [data-testid="stMarkdownContainer"] strong {
  color: #fff;
}

/* Typing indicator */
.typing { display: flex; gap: 4px; align-items: center; padding: 2px; }
.typing span {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--slate-400);
  animation: blink 1.2s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: .25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
}

/* ---- Composer (chat input) ---- */
[data-testid="stBottom"] {
  background: var(--slate-50) !important;
  border-top: 1px solid var(--slate-200);
}
[data-testid="stBottomBlockContainer"] {
  max-width: 888px;
  margin: 0 auto;
  padding: 14px 0 0 !important;
}
[data-testid="stBottomBlockContainer"]::after {
  content: "Vos échanges sont confidentiels et sécurisés";
  display: block;
  text-align: center;
  font-size: 11.5px;
  color: var(--slate-400);
  padding: 2px 0 12px;
}
[data-testid="stChatInput"] { background: transparent; }
[data-testid="stChatInputPrimary"] {
  background: #fff;
  border: 1px solid var(--slate-200);
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(16,24,40,.05);
}
[data-testid="stChatInputPrimary"]:focus-within {
  border-color: var(--blue-600);
  box-shadow: 0 0 0 3px rgba(36,87,201,.14);
}
[data-testid="stChatInputPrimary"] textarea {
  font-size: 16px;
  color: var(--ink);
}
[data-testid="stChatInputPrimary"] textarea::placeholder { color: var(--slate-400); }
[data-testid="stChatInputSubmit"] {
  background: var(--slate-100) !important;
  color: var(--slate-400) !important;
  border-radius: 8px !important;
}
[data-testid="stChatInputSubmit"]:hover {
  background: var(--blue-600) !important;
  color: #fff !important;
}
"""

st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

# Persisted HTTP session: keeps the backend session cookie (conversation +
# authentication) across chat calls.
if "http" not in st.session_state:
    st.session_state.http = requests.Session()

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = [DEFAULT_MESSAGE]

if "client" not in st.session_state:
    st.session_state.client = None

if "auth_checked" not in st.session_state:
    st.session_state.auth_checked = False

if not st.session_state.auth_checked:
    try:
        resp = st.session_state.http.get(f"{API_URL}/me", timeout=5)
        if resp.status_code == 200:
            st.session_state.client = resp.json()
    except requests.exceptions.RequestException:
        pass
    st.session_state.auth_checked = True


# Backend Connection Check
@st.cache_data(ttl=10)
def check_health():
    try:
        response = requests.get(f"{API_URL}/health", timeout=3)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.exceptions.RequestException:
        return None


def clear_memory():
    try:
        st.session_state.http.post(f"{API_URL}/clear-memory", timeout=5)
        st.session_state.messages = [DEFAULT_MESSAGE]
        st.toast("Historique de la conversation effacé !", icon="✅")
    except Exception as e:
        st.error(f"Impossible d'effacer la mémoire côté serveur : {e}")


def do_login(email, password):
    try:
        resp = st.session_state.http.post(
            f"{API_URL}/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            st.session_state.client = resp.json()
            st.toast("Connexion réussie", icon="✅")
            return True
        st.error("Email ou mot de passe incorrect.")
    except requests.exceptions.RequestException as e:
        st.error(f"Impossible de contacter le serveur : {e}")
    return False


def do_logout():
    try:
        st.session_state.http.post(f"{API_URL}/logout", timeout=5)
    except requests.exceptions.RequestException:
        pass
    st.session_state.client = None
    st.session_state.messages = [DEFAULT_MESSAGE]
    st.toast("Déconnecté")


def process_prompt(prompt: str) -> None:
    """Append the user message, show the typing animation, call /chat and
    render the assistant reply with its sources/data expanders."""
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown(TYPING_HTML, unsafe_allow_html=True)

        if not check_health():
            placeholder.error("Impossible de contacter le serveur. Vérifiez la connexion au backend.")
            return

        try:
            response = st.session_state.http.post(
                f"{API_URL}/chat",
                json={"message": prompt},
                timeout=300,
            )

            if response.status_code == 200:
                data = response.json()
                answer = data.get("answer", "No answer provided.")
                sources = data.get("sources", [])
                articles = data.get("articles", [])
                analytics_data = data.get("data", [])
                if not analytics_data and data.get("analytics"):
                    analytics_data = data["analytics"].get("data", [])

                placeholder.markdown(answer)

                if articles:
                    with st.expander("📄 Articles sources"):
                        for article in articles:
                            st.caption(f"**{article['article']}** — {article['title']}")
                            st.caption(f"  ({article.get('source', '')})")
                elif sources:
                    with st.expander("View Sources"):
                        for source in sources:
                            st.caption(f"- {source}")

                if analytics_data and len(analytics_data) > 0:
                    with st.expander("View Data Records"):
                        st.dataframe(pd.DataFrame(analytics_data).astype(str), use_container_width=True)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "sources": sources,
                    "articles": articles,
                    "data": analytics_data,
                    "requires_login": data.get("requires_login", False),
                })
            else:
                error_msg = f"Error from server: {response.status_code}"
                try:
                    detail = response.json().get("detail")
                    if detail:
                        error_msg += f" — {detail}"
                except Exception:
                    pass
                placeholder.error(error_msg)
        except Exception as e:
            placeholder.error(f"An error occurred: {e}")


# ── Sidebar ──────────────────────────────────────────────────────────────────
pending_prompt = None

with st.sidebar:
    # Brand
    st.markdown(f"""
    <div class="brand">
      <div class="brand-mark">{SVG_CALENDAR}</div>
      <div class="brand-text">
        <div class="name">HAYETT 2000</div>
        <div class="role">Espace Assurance Vie</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Statut du système
    health_data = check_health()
    if health_data and health_data.get("status") == "ok":
        docs_indexed = health_data.get("vector_documents", "—")
        st.markdown(f"""
        <div class="section">
          <div class="section-label">Statut du système</div>
          <div class="status-card">
            <div class="status-dot"><svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3"><path d="M4 12l6 6L20 6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
            <div class="status-info">
              <div class="t1">Système opérationnel</div>
              <div class="t2">Connecté au serveur</div>
            </div>
          </div>
          <div class="metric-card">
            <span class="label">Documents indexés</span>
            <span class="value">{docs_indexed}</span>
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="section">
          <div class="section-label">Statut du système</div>
          <div class="status-card">
            <div class="status-dot off"><svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3"><path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
            <div class="status-info">
              <div class="t1">Serveur indisponible</div>
              <div class="t2">Vérifiez le serveur FastAPI</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Actions rapides (SVG icons injected via the st-key-qa CSS rules)
    st.markdown("""
    <div class="section">
      <div class="section-label">Actions rapides</div>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Voir mes garanties", key="qa-garanties", use_container_width=True):
        pending_prompt = QUICK_ACTIONS["Voir mes garanties"]
    if st.button("Montant de ma prime", key="qa-prime", use_container_width=True):
        pending_prompt = QUICK_ACTIONS["Montant de ma prime"]
    if st.button("Faire une réclamation", key="qa-reclamation", use_container_width=True):
        pending_prompt = QUICK_ACTIONS["Faire une réclamation"]

    # Compte client
    st.markdown('<div class="section-label" style="margin-top:6px;">Compte client</div>', unsafe_allow_html=True)
    if st.session_state.client:
        client = st.session_state.client
        st.markdown(f"""
        <div class="status-card">
          <div class="status-info">
            <div class="t1">👤 {client.get('prenom', '')} {client.get('nom', '')}</div>
            <div class="t2">Client n° {client.get('client_id', '')}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Se déconnecter", key="logout_btn", use_container_width=True):
            do_logout()
            st.rerun()
    else:
        with st.form("login_form", clear_on_submit=False, border=True):
            st.markdown(
                '<div class="client-desc">Connectez-vous pour accéder aux '
                "informations personnelles de votre contrat.</div>",
                unsafe_allow_html=True,
            )
            st.text_input("Email", key="login_email", placeholder="ahmed@gmail.com")
            st.text_input("Mot de passe", key="login_password", type="password")
            submitted = st.form_submit_button("🔐 Se connecter", use_container_width=True)
            if submitted:
                email_val = (st.session_state.get("login_email") or "").strip()
                pwd_val = st.session_state.get("login_password") or ""
                if not email_val and not pwd_val:
                    st.error("Veuillez saisir votre email et votre mot de passe.")
                elif not email_val:
                    st.error("Veuillez saisir votre email.")
                elif not pwd_val:
                    st.error("Veuillez saisir votre mot de passe.")
                elif do_login(email_val, pwd_val):
                    st.rerun()

    # Effacer l'historique
    st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)
    if st.button("🗑️ Effacer l'historique", key="clear_btn", use_container_width=True):
        clear_memory()

# ── Main ─────────────────────────────────────────────────────────────────────
# Topbar (breadcrumb + decorative Deploy/kebab, same as the reference UI)
st.markdown("""
<div class="topbar">
  <div class="topbar-left"><span class="breadcrumb">Espace Assurance / <b>Assistant</b></span></div>
  <div class="topbar-right">
    <button class="deploy-btn">Deploy</button>
    <button class="kebab">⋮</button>
  </div>
</div>
""", unsafe_allow_html=True)

# Hero
st.markdown(f"""
<div class="hero-badge">{SVG_LAYERS} Assistant IA sécurisé</div>
<h1 class="hero-title">Assistant HAYETT 2000</h1>
<p class="hero-sub">
  Posez vos questions sur votre contrat d'assurance vie, en français ou en arabe
  <span class="lang-pill">FR</span><span class="lang-pill">TN</span>
</p>
""", unsafe_allow_html=True)

# Display chat messages from history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message.get("requires_login"):
            with st.expander("ℹ️ Connexion requise"):
                st.caption("Les informations personnelles de votre contrat sont disponibles après connexion.")

        # Display cited articles (verbatim titles from the documents)
        if message.get("articles"):
            with st.expander("📄 Articles sources"):
                for article in message["articles"]:
                    st.caption(f"**{article['article']}** — {article['title']}")
                    st.caption(f"  ({article.get('source', '')})")

        # Display sources if any
        if message.get("sources") and not message.get("articles"):
            with st.expander("View Sources"):
                for source in message["sources"]:
                    st.caption(f"- {source}")

        # Display data payload if analytics
        if message.get("data") and len(message["data"]) > 0:
            with st.expander("View Data Records"):
                st.dataframe(pd.DataFrame(message["data"]).astype(str), use_container_width=True)

# Quick action clicked in the sidebar -> run the chat flow here (main area),
# AFTER the history render so the new exchange appears at the bottom.
if pending_prompt:
    process_prompt(pending_prompt)

# Chat Input
if prompt := st.chat_input("What would you like to know?"):
    process_prompt(prompt)
