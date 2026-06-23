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
    tiers = ["Unpaywall", "PubMed Central", "arXiv", "DOAJ", "Semantic Scholar", "CORE API", "Sci-Hub", "Scrape.do Proxy", "Selenium & LLM"]
    total_loaded = sum([f.get('retrieved', 0) for f in state.flow_status])
    
    html = "<div style='display:flex; justify-content:space-between; align-items:flex-end; position:relative; padding-top:70px; padding-bottom: 30px; font-family:sans-serif; overflow-x:auto;'>"
    
    # Track line
    html += "<div style='position:absolute; bottom:40px; left:0; width:100%; height:4px; background:#ddd; z-index:1;'></div>"
    
    # Start Line
    html += "<div style='position:absolute; bottom:25px; left:20px; width:4px; height:30px; background:#28a745; z-index:2; border-radius:2px;' title='Start'></div>"
    
    # Finish Line (Checkered pattern)
    html += "<div style='position:absolute; bottom:25px; right:20px; width:12px; height:30px; background:repeating-conic-gradient(#333 0% 25%, #fff 0% 50%) 50% / 6px 6px; z-index:2; border: 1px solid #333;' title='Finish'></div>"
    
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
                <div style='position:absolute; bottom: 45px; left:50%; transform:translateX(-50%); z-index:20; filter: drop-shadow(0px 4px 4px rgba(0,0,0,0.25));'>
                    <svg width="45" height="45" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
                      <path fill="#007bff" d="M2 20h34v26H2z"/>
                      <path fill="#0056b3" d="M36 30h12l8 8v8H36z"/>
                      <rect x="40" y="32" width="6" height="6" fill="#87ceeb"/>
                      <circle cx="12" cy="48" r="6" fill="#333"/>
                      <circle cx="28" cy="48" r="6" fill="#333"/>
                      <circle cx="48" cy="48" r="6" fill="#333"/>
                      <circle cx="12" cy="48" r="3" fill="#ccc"/>
                      <circle cx="28" cy="48" r="3" fill="#ccc"/>
                      <circle cx="48" cy="48" r="3" fill="#ccc"/>
                    </svg>
                    <div style='position:absolute; top:-25px; left:50%; transform:translateX(-50%); font-weight:bold; background:#333; color:#fff; padding:3px 8px; border-radius:4px; font-size:12px; white-space:nowrap;'>
                        {total_loaded} PDFs
                    </div>
                </div>
                """
            elif f["status"] == "Completed":
                status_color = "#28a745"
                loaded_text = f"+{f['retrieved']} Loaded"
                if i == len(state.flow_status) - 1 and len(state.flow_status) == len(tiers):
                    truck_html = f"""
                    <div style='position:absolute; bottom: 45px; left:50%; transform:translateX(-50%); z-index:20; filter: drop-shadow(0px 4px 4px rgba(0,0,0,0.25));'>
                        <svg width="45" height="45" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
                          <path fill="#28a745" d="M2 20h34v26H2z"/>
                          <path fill="#1e7e34" d="M36 30h12l8 8v8H36z"/>
                          <rect x="40" y="32" width="6" height="6" fill="#87ceeb"/>
                          <circle cx="12" cy="48" r="6" fill="#333"/>
                          <circle cx="28" cy="48" r="6" fill="#333"/>
                          <circle cx="48" cy="48" r="6" fill="#333"/>
                          <circle cx="12" cy="48" r="3" fill="#ccc"/>
                          <circle cx="28" cy="48" r="3" fill="#ccc"/>
                          <circle cx="48" cy="48" r="3" fill="#ccc"/>
                        </svg>
                        <div style='position:absolute; top:-25px; left:50%; transform:translateX(-50%); font-weight:bold; background:#28a745; color:#fff; padding:3px 8px; border-radius:4px; font-size:12px; white-space:nowrap;'>
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
    
    # CRITICAL FIX: Streamlit treats indented text as a code block. 
    # We must strip newlines and excessive spaces before rendering.
    clean_html = " ".join(html.split())
    st.markdown(clean_html, unsafe_allow_html=True)


st.title("Universal Literature Extractor")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Configuration")
    
    with st.expander("API Keys & Settings", expanded=True):
        groq_key = st.text_input("Groq API Key (Required)", type="password", help="For LLM abstract detection")
        scrape_do_key = st.text_input("Scrape.do API Key (Anti-Bot Bypass)", type="password", help="Will route through Scrape.do to bypass Cloudflare/Akamai")
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
                    ss_key=ss_key, core_api_key=core_key, scrape_do_key=scrape_do_key
                )
                state.engine = engine
                threading.Thread(target=engine.run, daemon=True).start()
                st.rerun()
                
    with tab2:
        st.markdown("**Manual Entry (Multiple Articles)**")
        st.markdown("Add as many rows as you need below, then click Extract.")
        
        # Initialize empty dataframe with correct columns
        df_columns = ["DOI", "Article Name", "Format Name", "Author Name", "PMCID", "arXiv ID", "Bing Link"]
        if 'manual_df' not in st.session_state:
            st.session_state.manual_df = pd.DataFrame(columns=df_columns)
            # Add one empty row by default
            st.session_state.manual_df.loc[0] = ["", "", "", "", "", "", ""]
            
        edited_df = st.data_editor(st.session_state.manual_df, num_rows="dynamic", use_container_width=True, hide_index=True)
        
        manual_submit = st.button("Extract Articles", use_container_width=True, type="primary")
        
        if manual_submit:
            # Filter out completely empty rows
            valid_df = edited_df.dropna(how='all')
            # Require at least DOI, Article Name, Format Name
            valid_df = valid_df[valid_df['DOI'].astype(bool) & valid_df['Article Name'].astype(bool) & valid_df['Format Name'].astype(bool)]
            
            if valid_df.empty:
                st.error("Please enter at least one valid article with a DOI, Article Name, and Format Name.")
            elif not groq_key:
                st.error("Groq API Key is required.")
            else:
                tmp_dir = tempfile.gettempdir()
                tmp_path = os.path.join(tmp_dir, "manual_entry.xlsx")
                valid_df.to_excel(tmp_path, index=False)
                
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
                    ss_key=ss_key, core_api_key=core_key, scrape_do_key=scrape_do_key
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
