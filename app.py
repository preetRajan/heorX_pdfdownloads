import streamlit as st
import pandas as pd
import tempfile
import os
import threading
from scraper_engine import ScraperEngine
import time
import shutil

st.set_page_config(page_title="Literature Scraper", layout="wide")

class AppState:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.is_scraping = False
        self.engine = None
        self.flow_status = []
        self.stats = {
            "Unpaywall": 0, "PubMed Central": 0, "arXiv": 0, "DOAJ": 0,
            "Semantic Scholar": 0, "CORE API": 0, "Sci-Hub": 0, "Selenium & LLM": 0
        }

if 'app_state' not in st.session_state:
    st.session_state.app_state = AppState()

state = st.session_state.app_state

def log_callback(msg_type, msg):
    if msg_type == "done":
        state.is_scraping = False
    else:
        prefix = "[ERROR]" if msg_type == "error" else "[OK]" if "Downloaded:" in msg else "[INFO]"
        state.logs.append(f"{time.strftime('%H:%M:%S')} {prefix} {msg}")

def progress_callback(progress):
    state.progress = progress

def stats_callback(source):
    if source in state.stats:
        state.stats[source] += 1
    else:
        state.stats[source] = 1

def flow_callback(flow_data):
    found = False
    for i, item in enumerate(state.flow_status):
        if item['tier'] == flow_data['tier']:
            state.flow_status[i] = flow_data
            found = True
            break
    if not found:
        state.flow_status.append(flow_data)

def render_truck_visualization():
    tiers = ["Unpaywall", "PubMed Central", "arXiv", "DOAJ", "Semantic Scholar", "CORE API", "Sci-Hub", "Selenium & LLM"]
    total_loaded = sum([f.get('retrieved', 0) for f in state.flow_status])
    
    html = "<div style='display:flex; justify-content:space-between; align-items:flex-end; position:relative; padding-top:70px; padding-bottom: 30px; font-family:sans-serif; overflow-x:auto;'>"
    
    # Track line
    html += "<div style='position:absolute; bottom:40px; left:0; width:100%; height:4px; background:#ddd; z-index:1;'></div>"
    
    for i, tier in enumerate(tiers):
        f = next((item for item in state.flow_status if item["tier"] == tier), None)
        status_color = "#ccc"
        loaded_text = "Waiting"
        truck_html = ""
        
        if f:
            if f["status"] == "Processing...":
                status_color = "#FFA500"
                loaded_text = "Loading..."
                truck_html = f"""
                <div style='position:absolute; bottom: 45px; left:50%; transform:translateX(-50%); z-index:20;'>
                    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="1" y="3" width="15" height="13"></rect>
                        <polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon>
                        <circle cx="5.5" cy="18.5" r="2.5"></circle>
                        <circle cx="18.5" cy="18.5" r="2.5"></circle>
                    </svg>
                    <div style='position:absolute; top:-25px; left:50%; transform:translateX(-50%); font-weight:bold; background:#333; color:#fff; padding:2px 6px; border-radius:4px; font-size:12px; white-space:nowrap;'>
                        {total_loaded} PDFs
                    </div>
                </div>
                """
            elif f["status"] == "Completed":
                status_color = "#28a745"
                loaded_text = f"+{f['retrieved']} Loaded"
                if i == len(state.flow_status) - 1 and len(state.flow_status) == len(tiers):
                    truck_html = f"""
                    <div style='position:absolute; bottom: 45px; left:50%; transform:translateX(-50%); z-index:20;'>
                        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="1" y="3" width="15" height="13"></rect>
                            <polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon>
                            <circle cx="5.5" cy="18.5" r="2.5"></circle>
                            <circle cx="18.5" cy="18.5" r="2.5"></circle>
                        </svg>
                        <div style='position:absolute; top:-25px; left:50%; transform:translateX(-50%); font-weight:bold; background:#28a745; color:#fff; padding:2px 6px; border-radius:4px; font-size:12px; white-space:nowrap;'>
                            {total_loaded} PDFs (Done)
                        </div>
                    </div>
                    """
        
        html += f"""
        <div style='text-align:center; flex:1; position:relative; z-index:10;'>
            {truck_html}
            <div style='height:16px; width:16px; background-color:{status_color}; border-radius:50%; margin:0 auto; border:3px solid #fff; box-shadow:0 0 0 2px {status_color};'></div>
            <div style='font-size:11px; margin-top:15px; font-weight:bold; color:#333; word-wrap:break-word; max-width:80%; margin-left:auto; margin-right:auto;'>{tier}</div>
            <div style='font-size:10px; color:#666; margin-top:2px;'>{loaded_text}</div>
        </div>
        """
        
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


st.title("Universal Literature Extractor")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Configuration")
    
    with st.expander("API Keys & Settings", expanded=True):
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
                state.flow_status = []
                
                engine = ScraperEngine(
                    excel_path=tmp_path, log_callback=log_callback, 
                    progress_callback=progress_callback, stats_callback=stats_callback,
                    flow_callback=flow_callback, max_workers=max_workers,
                    groq_api_key=groq_key, unpaywall_email=unpaywall_email,
                    ss_key=ss_key, core_api_key=core_key
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
                    pd.DataFrame([{
                        "DOI": doi_input, "Article Name": article_name_input, "Format Name": format_name_input,
                        "Author Name": author_input, "PMCID": pmcid_input, "arXiv ID": arxiv_input, "Bing Link": bing_link_input
                    }]).to_excel(tmp_path, index=False)
                    
                    state.is_scraping = True
                    state.logs = []
                    state.progress = 0.0
                    state.stats = {k: 0 for k in state.stats}
                    state.flow_status = []
                    
                    engine = ScraperEngine(
                        excel_path=tmp_path, log_callback=log_callback, 
                        progress_callback=progress_callback, stats_callback=stats_callback,
                        flow_callback=flow_callback, max_workers=max_workers,
                        groq_api_key=groq_key, unpaywall_email=unpaywall_email,
                        ss_key=ss_key, core_api_key=core_key
                    )
                    state.engine = engine
                    threading.Thread(target=engine.run, daemon=True).start()
                    st.rerun()

with col2:
    st.subheader("Real-Time Dashboard")
    
    if not state.is_scraping and state.engine and os.path.exists(state.engine.output_dir):
        st.success("Extraction Completed!")
        zip_path = os.path.join(tempfile.gettempdir(), "extracted_literature")
        shutil.make_archive(zip_path, 'zip', state.engine.output_dir)
        with open(f"{zip_path}.zip", "rb") as f:
            st.download_button("Download Extracted PDFs & Report", f, "extracted_pdfs.zip", type="primary", use_container_width=True)
            
    st.markdown("### Pipeline Flow Visualization")
    if not state.flow_status:
        st.info("Waiting for extraction to begin...")
    else:
        render_truck_visualization()
    
    st.markdown("### Live Logs")
    if state.is_scraping:
        st.progress(state.progress)
        if st.button("Stop Scraper", type="primary"):
            if state.engine:
                state.engine.stop()
                st.warning("Stopping sequence initiated...")

    log_container = st.container(height=350)
    for log in reversed(state.logs[-200:]):
        log_container.text(log)

# Auto-refresh loop
if state.is_scraping:
    time.sleep(2)
    st.rerun()
