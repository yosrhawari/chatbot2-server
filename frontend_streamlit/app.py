import streamlit as st
import requests
import json
import os
import pandas as pd

# Set page configuration
st.set_page_config(
    page_title=" Vilavi Assistant",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

# Custom CSS for a premium look
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');

    /* Global settings */
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }

    /* Main background with subtle gradient */
    .stApp {
        background: linear-gradient(135deg, #f0f4fd 0%, #e1e9fb 100%);
    }
    
    /* Header styling with gradient text */
    h1 {
        background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 700 !important;
        letter-spacing: -0.5px;
        margin-bottom: 0.5rem;
    }
    
    /* Sleek light Glassmorphism for sidebar */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(248, 250, 252, 0.9) 100%) !important;
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(226, 232, 240, 0.8);
        box-shadow: 4px 0 24px rgba(0, 0, 0, 0.04);
    }
    
    /* Make sure sidebar titles/text look good on the light background */
    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] p {
        color: #1e293b;
    }

    [data-testid="stSidebar"] h1 {
        font-size: 1.8rem !important;
        font-weight: 700 !important;
        background: linear-gradient(135deg, #3B82F6 0%, #8B5CF6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.1rem !important;
    }

    [data-testid="stSidebar"] h2 {
        font-size: 0.75rem !important;
        font-weight: 600 !important;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 0.4rem !important;
        margin-bottom: 0.3rem !important;
    }

    [data-testid="stSidebar"] hr {
        border-color: rgba(226, 232, 240, 0.6);
        margin: 0.4rem 0 !important;
    }

    /* Sidebar metric container */
    [data-testid="stSidebar"] [data-testid="stMetricContainer"] {
        background: linear-gradient(135deg, rgba(59, 130, 246, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
        border: 1px solid rgba(59, 130, 246, 0.15);
        border-radius: 12px;
        padding: 1rem;
        margin: 0.5rem 0;
    }

    /* Sidebar success/error messages */
    [data-testid="stSidebar"] .stSuccess {
        background: linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(52, 211, 153, 0.1) 100%);
        border: 1px solid rgba(16, 185, 129, 0.2);
        border-radius: 10px;
        padding: 0.75rem 1rem;
    }

    [data-testid="stSidebar"] .stError {
        background: linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(248, 113, 113, 0.1) 100%);
        border: 1px solid rgba(239, 68, 68, 0.2);
        border-radius: 10px;
        padding: 0.75rem 1rem;
    }

    /* Sidebar info card style */
    [data-testid="stSidebar"] .stMarkdown {
        color: #475569;
        line-height: 1.6;
    }
    
    /* Chat message container animations & styling */
    .stChatMessage {
        background-color: rgba(255, 255, 255, 0.85);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 16px 20px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.03);
        margin-bottom: 20px;
        border: 1px solid rgba(255, 255, 255, 0.8);
        animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        opacity: 0;
        transform: translateY(10px);
    }
    
    /* Distinct styling for assistant vs user messages */
    .stChatMessage[data-testid="chat-message-user"] {
        background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
        border-right: 4px solid #3B82F6;
    }
    
    .stChatMessage[data-testid="chat-message-assistant"] {
        background: linear-gradient(135deg, #ffffff 0%, #fdfdff 100%);
        border-left: 4px solid #10b981;
    }

    @keyframes fadeIn {
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    /* Metric styling */
    [data-testid="stMetricValue"] {
        color: #34d399 !important;
        font-weight: 700;
        text-shadow: 0 0 20px rgba(52, 211, 153, 0.4);
    }
    [data-testid="stMetricLabel"] {
        color: #94a3b8 !important;
    }
    
    /* Button styling */
    .stButton>button {
        background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
        color: white !important;
        border: none;
        border-radius: 10px;
        font-weight: 500;
        padding: 0.5rem 1rem;
        transition: all 0.3s ease;
        box-shadow: 0 4px 14px 0 rgba(59, 130, 246, 0.39);
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(59, 130, 246, 0.23);
        background: linear-gradient(135deg, #60A5FA 0%, #3B82F6 100%);
    }

    /* Sidebar button styling */
    [data-testid="stSidebar"] .stButton>button {
        color: white !important;
    }
    
    /* Input box styling */
    .stChatInputContainer {
        border-radius: 16px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.8);
        background: rgba(255, 255, 255, 0.8);
        backdrop-filter: blur(10px);
    }

    /* Expanders */
    .streamlit-expanderHeader {
        background-color: transparent;
        color: #475569;
        font-weight: 500;
    }
    
    /* Custom Scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    ::-webkit-scrollbar-track {
        background: transparent;
    }
    ::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #94a3b8;
    }
</style>
""", unsafe_allow_html=True)

# API Configuration
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")

# Initialize session state
DEFAULT_MESSAGE = {"role": "assistant", "content": "Bonjour ! Je suis Vivi, votre assistante virtuelle, poser moi vos questions 👇"}
if "messages" not in st.session_state:
    st.session_state.messages = [DEFAULT_MESSAGE]

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
        requests.post(f"{API_URL}/clear-memory", timeout=5)
        st.session_state.messages = [DEFAULT_MESSAGE]
        st.toast("Conversation memory cleared successfully!", icon="✅")
    except Exception as e:
        st.error(f"Failed to clear memory on the server: {e}")

# Sidebar
with st.sidebar:
    # Header with gradient logo
    st.markdown("""
    <div style='text-align: center; padding: 0.4rem 0;'>
        <div style='font-size: 7rem; margin-bottom: -1.9rem;'>🤖</div>
        <p style='color: #64748b; font-size: 0.95rem; margin: 1.1rem 0 0 0;'>
            Your intelligent assistant
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # System Status Section
    st.markdown("""
    <h2 style='font-size: 0.75rem; font-weight: 600; color: #64748b; 
               text-transform: uppercase; letter-spacing: 0.05em; margin: 0.3rem 0 0.3rem 0;'>
        System Status
    </h2>
    """, unsafe_allow_html=True)
    
    health_data = check_health()
    
    if health_data and health_data.get("status") == "ok":
        st.markdown("""
        <div style='background: linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(52, 211, 153, 0.1) 100%);
                    border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 8px; 
                    padding: 0.4rem 0.6rem; margin: 0.2rem 0;'>
            <div style='display: flex; align-items: center; gap: 0.3rem;'>
                <span style='font-size: 1rem;'>✅</span>
                <span style='color: #065f46; font-weight: 600; font-size: 0.75rem;'>Connected to Backend</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if "vector_documents" in health_data:
            st.markdown(f"""
            <div style='background: linear-gradient(135deg, rgba(59, 130, 246, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
                        border: 1px solid rgba(59, 130, 246, 0.15); border-radius: 8px; 
                        padding: 0.4rem 0.6rem; margin: 0.15rem 0;'>
                <div style='color: #64748b; font-size: 0.65rem; margin-bottom: 0.05rem;'>Documents Indexed</div>
                <div style='font-size: 1.2rem; font-weight: 700; color: #3B82F6;'>{health_data['vector_documents']}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style='background: linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(248, 113, 113, 0.1) 100%);
                    border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 8px; 
                    padding: 0.4rem 0.6rem; margin: 0.2rem 0;'>
            <div style='display: flex; align-items: center; gap: 0.3rem;'>
                <span style='font-size: 1rem;'>❌</span>
                <span style='color: #991b1b; font-weight: 600; font-size: 0.75rem;'>Backend Disconnected</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.caption("Please ensure the FastAPI server is running.")

    st.markdown("---")

    # Quick Actions
    st.markdown("""
    <h2 style='font-size: 0.75rem; font-weight: 600; color: #64748b;
               text-transform: uppercase; letter-spacing: 0.05em; margin: 0.3rem 0 0.3rem 0;'>
        Quick Actions
    </h2>
    """, unsafe_allow_html=True)

    if st.button("🗑️ Clear Chat History", use_container_width=True):
        clear_memory()

    # Footer
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; padding: 0.3rem 0; color: #94a3b8; font-size: 0.65rem;'>
        <div style='margin-bottom: 0.1rem;'>Version 2.8.1</div>
        <div>© 2026 VilaBot</div>
    </div>
    """, unsafe_allow_html=True)

# Main Application
st.title("VilaBot")
st.markdown("Besoin d'informations ? Posez-moi vos questions !")

# Display chat messages from history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        
        # Display sources if any
        if message.get("sources"):
            with st.expander("View Sources"):
                for source in message["sources"]:
                    st.caption(f"- {source}")
                    
        # Display data payload if analytics
        if message.get("data") and len(message["data"]) > 0:
            with st.expander("View Data Records"):
                st.dataframe(pd.DataFrame(message["data"]).astype(str), use_container_width=True)

# Chat Input
if prompt := st.chat_input("What would you like to know?"):
    # Add user message to state and display
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call Backend API
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        if not health_data:
            st.error("Cannot reach the backend server. Please check the connection.")
            st.stop()
            
        with st.spinner("Thinking..."):
            try:
                response = requests.post(
                    f"{API_URL}/chat",
                    json={"message": prompt},
                    timeout=300
                )
                
                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "No answer provided.")
                    sources = data.get("sources", [])
                    analytics_data = data.get("data", [])
                    if not analytics_data and data.get("analytics"):
                        analytics_data = data["analytics"].get("data", [])
                    
                    message_placeholder.markdown(answer)
                    
                    if sources:
                        with st.expander("View Sources"):
                            for source in sources:
                                st.caption(f"- {source}")
                                
                    if analytics_data and len(analytics_data) > 0:
                        with st.expander("View Data Records"):
                            st.dataframe(pd.DataFrame(analytics_data).astype(str), use_container_width=True)
                            
                    # Add to session state
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": answer,
                        "sources": sources,
                        "data": analytics_data
                    })
                else:
                    error_msg = f"Error from server: {response.status_code}"
                    message_placeholder.error(error_msg)
            except Exception as e:
                message_placeholder.error(f"An error occurred: {e}")
