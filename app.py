import streamlit as st
import pandas as pd
import tempfile
import os
import threading
from scraper_engine import ScraperEngine
import time

st.set_page_config(page_title="Literature Scraper", layout="wide")

class AppState:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.is_scraping = False
        self.engine = None
        self.stats = {
            "Unpaywall": 0,
            "PubMed Central": 0,
            "arXiv": 0,
            "DOAJ": 0,
            "Semantic Scholar": 0,
            "CORE API": 0,
            "Sci-Hub": 0,
            "Selenium & LLM": 0
        }

if 'app_state' not in st.session_state:
    st.session_state.app_state = AppState()

state = st.session_state.app_state

def log_callback(msg_type, msg):
    if msg_type == "done":
        state.is_scraping = False
    else:
        state.logs.append(f"{time.strftime('%H:%M:%S')} - {msg}")

def progress_callback(progress):
    state.progress = progress

def stats_callback(source):
    if source in state.stats:
        state.stats[source] += 1
    else:
        state.stats[source] = 1

st.title("📚 Universal Literature Extractor")

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.subheader("Login Gateway")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit_btn = st.form_submit_button("Login")
        
        if submit_btn:
            if username == "ZS_HEOR" and password == "ZS_HEOR":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Invalid credentials")
    st.stop()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Configuration")
    
    with st.expander("🔑 API Keys & Settings", expanded=True):
        groq_key = st.text_input("Groq API Key (Required)", type="password", help="For LLM abstract detection")
        core_key = st.text_input("CORE API Key", value="rdbipaBHZm02PjTOA8h6evxMyYsf47FD", type="password")
        ss_key = st.text_input("Semantic Scholar API Key", value="8D7qtvI8UC1xX5gQJnQj87NPBiPmzycV1NcIJ76w", type="password")
        unpaywall_email = st.text_input("Unpaywall Email", value="rajanjatt110@gmail.com")
        
    max_workers = st.slider("Concurrent Chrome Browsers", 1, 5, 2, help="Used only for the Selenium Fallback Tier")
    
    tab1, tab2 = st.tabs(["Upload Excel", "Manual Entry"])
    
    with tab1:
        st.markdown("""
        **Excel Format Required:**
        - `DOI`: Full URL or standard DOI string
        - `Article Name`: Full title of the paper
        - `Format Name`: Desired output filename
        - `Author Name` *(Optional but highly recommended for better accuracy)*
        - `PMCID` *(Optional)*
        - `arXiv ID` *(Optional)*
        """)
        uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"])
        
        if st.button("Start Excel Scrape", disabled=state.is_scraping, use_container_width=True):
            if not uploaded_file:
                st.error("Please upload a file first.")
            elif not groq_key:
                st.error("Groq API Key is required.")
            else:
                tmp_dir = tempfile.gettempdir()
                tmp_path = os.path.join(tmp_dir, uploaded_file.name)
                with open(tmp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                state.is_scraping = True
                state.logs = []
                state.progress = 0.0
                state.stats = {k: 0 for k in state.stats}
                
                engine = ScraperEngine(
                    excel_path=tmp_path, 
                    log_callback=log_callback, 
                    progress_callback=progress_callback, 
                    stats_callback=stats_callback,
                    max_workers=max_workers,
                    groq_api_key=groq_key,
                    unpaywall_email=unpaywall_email,
                    ss_key=ss_key,
                    core_api_key=core_key
                )
                state.engine = engine
                
                threading.Thread(target=engine.run, daemon=True).start()
                st.rerun()
                
    with tab2:
        st.markdown("**Single Article Extraction**")
        with st.form("manual_entry_form"):
            doi_input = st.text_input("DOI (Required)")
            article_name_input = st.text_input("Article Name (Required)")
            format_name_input = st.text_input("Format Name (Output filename without .pdf) (Required)")
            author_input = st.text_input("Author Name (Optional)")
            pmcid_input = st.text_input("PMCID (Optional)")
            arxiv_input = st.text_input("arXiv ID (Optional)")
            bing_link_input = st.text_input("Bing Fallback Link (Optional)")
            
            manual_submit = st.form_submit_button("Extract Article", use_container_width=True)
            
            if manual_submit:
                if not doi_input or not article_name_input or not format_name_input:
                    st.error("Please fill in DOI, Article Name, and Format Name.")
                elif not groq_key:
                    st.error("Groq API Key is required.")
                else:
                    tmp_dir = tempfile.gettempdir()
                    tmp_path = os.path.join(tmp_dir, "manual_entry.xlsx")
                    df = pd.DataFrame([{
                        "DOI": doi_input,
                        "Article Name": article_name_input,
                        "Format Name": format_name_input,
                        "Author Name": author_input,
                        "PMCID": pmcid_input,
                        "arXiv ID": arxiv_input,
                        "Bing Link": bing_link_input
                    }])
                    df.to_excel(tmp_path, index=False)
                    
                    state.is_scraping = True
                    state.logs = []
                    state.progress = 0.0
                    state.stats = {k: 0 for k in state.stats}
                    
                    engine = ScraperEngine(
                        excel_path=tmp_path, 
                        log_callback=log_callback, 
                        progress_callback=progress_callback, 
                        stats_callback=stats_callback,
                        max_workers=max_workers,
                        groq_api_key=groq_key,
                        unpaywall_email=unpaywall_email,
                        ss_key=ss_key,
                        core_api_key=core_key
                    )
                    state.engine = engine
                    
                    threading.Thread(target=engine.run, daemon=True).start()
                    st.rerun()

with col2:
    st.subheader("Real-Time Dashboard")
    
    # Render Metrics Grid
    st.markdown("### 📊 Extraction Success Metrics")
    cols = st.columns(4)
    stat_items = list(state.stats.items())
    for i, (source, count) in enumerate(stat_items):
        cols[i % 4].metric(label=source, value=count)
    
    st.divider()
    
    st.markdown("### 📜 Live Logs")
    if state.is_scraping:
        st.progress(state.progress)
        if st.button("Stop Scraper", type="primary"):
            if state.engine:
                state.engine.stop()
                st.warning("Stopping sequence initiated...")

    log_container = st.container(height=300)
    for log in reversed(state.logs[-100:]):
        log_container.text(log)

    if st.button("Refresh UI"):
        st.rerun()
