import concurrent.futures
import queue
import pandas as pd
import json
import os
import time
import base64
import re
import requests
import subprocess
import tempfile
import shutil
from urllib.parse import urlparse
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

class UniversalPDFDownloader:
    def __init__(self, download_dir, unpaywall_email=None, semantic_scholar_key=None, core_api_key=None):
        self.download_dir = download_dir
        self.unpaywall_email = unpaywall_email
        self.ss_key = semantic_scholar_key
        self.core_api_key = core_api_key
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def _clean_filename(self, filename):
        filename = re.sub(r'[\\/*?:"<>|]', "", filename)
        return filename.strip()[:120]

    def _stream_pdf(self, url, filename, extra_headers=None):
        request_headers = self.headers.copy()
        if extra_headers:
            request_headers.update(extra_headers)

        safe_name = self._clean_filename(filename)
        filepath = os.path.join(self.download_dir, f"{safe_name}.pdf")

        try:
            response = requests.get(url, headers=request_headers, stream=True, timeout=25)
            content_type = response.headers.get('Content-Type', '').lower()

            if response.status_code == 200 and ('pdf' in content_type or 'octet-stream' in content_type):
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
        except Exception:
            pass
        return False

    def download_by_unpaywall(self, doi, filename):
        if not self.unpaywall_email: return False
        url = f"https://api.unpaywall.org/v2/{doi}?email={self.unpaywall_email}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('is_oa') and data.get('best_oa_location'):
                    pdf_url = data['best_oa_location'].get('url_for_pdf')
                    if pdf_url:
                        return self._stream_pdf(pdf_url, filename)
        except:
            pass
        return False

    def download_by_pmcid(self, pmcid, filename):
        clean_id = str(pmcid).upper().replace("PMC", "").strip()
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{clean_id}/pdf/"
        return self._stream_pdf(url, filename)

    def download_by_arxiv_id(self, arxiv_id, filename):
        url = f"https://export.arxiv.org/pdf/{arxiv_id}.pdf"
        return self._stream_pdf(url, filename)

    def download_by_doaj(self, doi, filename):
        api_url = f"https://doaj.org/api/v2/search/articles/doi:{doi}"
        try:
            res = requests.get(api_url, timeout=10)
            if res.status_code == 200 and res.json().get("results"):
                article = res.json()["results"][0].get("bibjson", {})
                for link in article.get("link", []):
                    if link.get("type") == "fulltext":
                        if self._stream_pdf(link.get("url"), filename):
                            return True
        except:
            pass
        return False

    def download_by_semantic_scholar(self, query_url, filename):
        if not self.ss_key: return False
        headers = {"x-api-key": self.ss_key}
        try:
            res = requests.get(query_url, headers=headers, timeout=12)
            if res.status_code == 429:
                time.sleep(1.2)
                res = requests.get(query_url, headers=headers, timeout=12)
                
            if res.status_code == 200:
                data = res.json()
                paper = data.get("data", [data])[0] if "data" in data else data
                
                if paper.get("openAccessPdf") and paper["openAccessPdf"].get("url"):
                    if self._stream_pdf(paper["openAccessPdf"]["url"], filename):
                        return True
                        
                if paper.get("externalIds") and "ArXiv" in paper["externalIds"]:
                    if self.download_by_arxiv_id(paper["externalIds"]["ArXiv"], filename):
                        return True
        except:
            pass
        return False

    def download_by_core(self, query, filename):
        if not self.core_api_key: return False
        
        search_url = "https://api.core.ac.uk/v3/search/works"
        core_headers = {"Authorization": f"Bearer {self.core_api_key}", "Content-Type": "application/json"}
        
        try:
            res = requests.get(search_url, headers=core_headers, params={"q": query, "limit": 1}, timeout=15)
            if res.status_code == 200 and res.json().get("results"):
                paper = res.json()["results"][0]
                core_id = paper.get("id")
                dl_url = f"https://api.core.ac.uk/v3/works/{core_id}/download"
                dl_headers = {"Accept": "application/pdf"}
                return self._stream_pdf(dl_url, filename, extra_headers=dl_headers)
            elif res.status_code == 429:
                time.sleep(2)
        except:
            pass
        return False

    def get_pdf(self, doi, title, author, pmcid, arxiv_id, filename):
        if pmcid and pmcid != 'nan':
            if self.download_by_pmcid(pmcid, filename): return True, "PubMed Central"
        if arxiv_id and arxiv_id != 'nan':
            if self.download_by_arxiv_id(arxiv_id, filename): return True, "arXiv"
            
        if doi and doi != 'nan':
            if self.download_by_unpaywall(doi, filename): return True, "Unpaywall"
            if self.download_by_doaj(doi, filename): return True, "DOAJ"
            ss_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title,openAccessPdf,externalIds"
            if self.download_by_semantic_scholar(ss_url, filename): return True, "Semantic Scholar"
            
            core_query = f'(doi:"{doi}") AND _exists_:fullText'
            if self.download_by_core(core_query, filename): return True, "CORE API"
            
        if title and title != 'nan':
            ss_query = f"{title} {author}".strip()
            ss_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={ss_query}&limit=1&fields=title,openAccessPdf,externalIds"
            if self.download_by_semantic_scholar(ss_url, filename): return True, "Semantic Scholar"
            
            query_parts = [f'title:"{title}"']
            if author and author != 'nan': query_parts.append(f'authors:"{author}"')
            core_query = f"({' AND '.join(query_parts)}) AND _exists_:fullText"
            if self.download_by_core(core_query, filename): return True, "CORE API"
            
        return False, None

class ScraperEngine:
    def __init__(self, excel_path, log_callback, progress_callback, stats_callback, max_workers=3, 
                 groq_api_key=None, unpaywall_email=None, ss_key=None, core_api_key=None):
        self.excel_path = excel_path
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.stats_callback = stats_callback
        self.max_workers = max_workers
        self.running = True
        self.output_dir = os.path.join(os.path.dirname(excel_path), "extracted_literature")
        os.makedirs(self.output_dir, exist_ok=True)
        self.rules = self.load_rules()
        self.driver_pool = queue.Queue()
        
        self.groq_api_key = groq_api_key
        self.groq_client = None
        if groq_api_key:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=groq_api_key)
            except Exception as e:
                self.log("error", f"Failed to initialize Groq client: {e}")

        self.universal_downloader = UniversalPDFDownloader(
            download_dir=self.output_dir,
            unpaywall_email=unpaywall_email,
            semantic_scholar_key=ss_key,
            core_api_key=core_api_key
        )

    def analyze_page_with_llm(self, page_text, article_name):
        if not self.groq_client:
            return {"is_full_paper": True, "extracted_abstract": ""}
            
        self.log("log", f"Analyzing page with LLM for {article_name}...")
        prompt = f"""You are an expert academic assistant. Analyze the following webpage text from an academic publisher.
Determine if the provided text contains the full body of the research paper (e.g., Introduction, Methods, Results, Discussion) or if it only provides the Abstract/Summary (because the full paper is behind a paywall).
Respond in pure JSON format with exactly two keys:
- "is_full_paper": boolean (true if full paper, false if only abstract)
- "extracted_abstract": string (the text of the abstract. If it is a full paper, leave this empty, otherwise provide the abstract text).

Text to analyze (first 8000 chars):
{page_text[:8000]}
"""
        try:
            chat_completion = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-8b-8192",
                response_format={"type": "json_object"},
                temperature=0.0
            )
            response_str = chat_completion.choices[0].message.content
            result = json.loads(response_str)
            return result
        except Exception as e:
            self.log("error", f"LLM Analysis failed for {article_name}: {e}")
            return {"is_full_paper": True, "extracted_abstract": ""}

    def log(self, msg_type, data):
        self.log_callback(msg_type, data)

    def load_rules(self):
        rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal_rules.json")
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log("error", f"Failed to load journal rules: {e}. Using default.")
            return {"default": {"pdf_meta_tag": "citation_pdf_url", "button_xpath": "//button[contains(., 'Download PDF')]", "timeout": 20}}

    def stop(self):
        self.running = False

    def initialize_drivers(self):
        self.log("log", f"Initializing {self.max_workers} browser instances sequentially...")
        for i in range(self.max_workers):
            if not self.running:
                break
            try:
                # Streamlit Cloud FIX: uc=False (read-only filesystem blocks uc_driver binary patch), headless=True
                driver = Driver(uc=False, headless=True, no_sandbox=True)
                self.driver_pool.put(driver)
                self.log("log", f"Browser {i+1} initialized.")
            except Exception as e:
                self.log("error", f"Failed to init browser {i+1}: {e}")

    def cleanup_drivers(self):
        self.log("log", "Cleaning up browser instances...")
        close_count = 0
        while not self.driver_pool.empty():
            driver = self.driver_pool.get()
            try:
                driver.quit()
                close_count += 1
            except:
                pass
        self.log("log", f"Cleaned up {close_count} browsers.")

    def run(self):
        try:
            self.log("log", "Reading Excel file...")
            df = pd.read_excel(self.excel_path)
            
            required_cols = ['DOI', 'Article Name', 'Format Name']
            for col in required_cols:
                if col not in df.columns:
                    self.log("error", f"Missing required column: {col}")
                    return

            duplicate_dois = df[df.duplicated(subset=['DOI'], keep=False)]['DOI'].dropna().unique()
            self.log("log", f"Found {len(duplicate_dois)} duplicate DOIs for Conference process.")

            self.initialize_drivers()
            if not self.running:
                self.cleanup_drivers()
                return

            total_rows = len(df)
            completed = 0

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for index, row in df.iterrows():
                    futures.append(executor.submit(self.process_row_with_driver, row, duplicate_dois))

                for future in concurrent.futures.as_completed(futures):
                    if not self.running:
                        self.log("log", "Process stopped by user.")
                        break
                    try:
                        future.result()
                    except Exception as e:
                        self.log("error", f"Thread error: {e}")
                    
                    completed += 1
                    progress = completed / total_rows
                    self.progress_callback(progress)

            self.log("done", None)
        except Exception as e:
            self.log("error", f"Fatal error: {str(e)}")
        finally:
            self.cleanup_drivers()
            self.log_callback("done", None)

    def process_row_with_driver(self, row, duplicate_dois):
        if not self.running:
            return
            
        driver = self.driver_pool.get()
        try:
            self.process_row(driver, row, duplicate_dois)
        finally:
            self.driver_pool.put(driver)

    def download_via_scihub(self, doi, format_name):
        if not doi or str(doi) == 'nan':
            return False
            
        self.log("log", f"Attempting Native Sci-Hub Extraction for DOI: {doi}")
        mirrors = ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"]
        
        for mirror in mirrors:
            try:
                url = f"{mirror}/{doi}"
                res = requests.get(url, headers=self.universal_downloader.headers, timeout=15)
                if res.status_code == 200:
                    import re
                    # Sci-Hub usually puts the PDF link in an embed or iframe tag
                    pdf_match = re.search(r'<embed[^>]*src=[\'"]([^\'"]+)[\'"]', res.text)
                    if not pdf_match:
                        pdf_match = re.search(r'<iframe[^>]*src=[\'"]([^\'"]+)[\'"]', res.text)
                        
                    if pdf_match:
                        pdf_url = pdf_match.group(1)
                        # Normalize protocol-relative or absolute-path URLs
                        if pdf_url.startswith("//"):
                            pdf_url = "https:" + pdf_url
                        elif pdf_url.startswith("/"):
                            pdf_url = mirror + pdf_url
                            
                        # Stream the PDF directly
                        if self.universal_downloader._stream_pdf(pdf_url, format_name):
                            return True
            except Exception as e:
                pass
                
        return False

    def process_row(self, driver, row, duplicate_dois):
        doi = str(row['DOI']).strip()
        article_name = str(row['Article Name']).strip()
        format_name = str(row['Format Name']).strip()
        author_name = str(row.get('Author Name', '')).strip()
        pmcid = str(row.get('PMCID', '')).strip()
        arxiv_id = str(row.get('arXiv ID', '')).strip()
        bing_link = str(row.get('Bing Link', '')).strip()

        is_conference = doi in duplicate_dois
        self.log("log", f"============================\nProcessing: {article_name}")

        # Tier 1-5: Universal Downloader
        success, source = self.universal_downloader.get_pdf(doi, article_name, author_name, pmcid, arxiv_id, format_name)
        if success:
            self.log("log", f"✅ Successfully downloaded '{format_name}' via {source}")
            self.stats_callback(source)
            return
            
        # Tier 6: Native Sci-Hub
        if self.download_via_scihub(doi, format_name):
            self.log("log", f"✅ Successfully downloaded '{format_name}' via Sci-Hub")
            self.stats_callback("Sci-Hub")
            return

        # Tier 7: Selenium / LLM
        self.log("log", f"⚠️ API Fallbacks exhausted. Initiating Selenium Engine for {article_name}...")

        if not doi or doi == 'nan' or not doi.startswith('http'):
            self.log("log", f"Invalid DOI. Attempting Bing Fallback.")
            if self.route_4_bing_fallback(driver, article_name, author_name, bing_link, format_name):
                self.stats_callback("Selenium & LLM")
            return

        try:
            driver.get(doi)
            time.sleep(4)
            
            page_text = driver.get_page_source().lower()
            if "error" in driver.get_current_url().lower() or "not found" in page_text:
                 self.log("log", f"DOI dead. Attempting Bing Fallback.")
                 if self.route_4_bing_fallback(driver, article_name, author_name, bing_link, format_name):
                     self.stats_callback("Selenium & LLM")
                 return
        except Exception as e:
            self.log("log", f"DOI Nav Error. Attempting Bing Fallback.")
            if self.route_4_bing_fallback(driver, article_name, author_name, bing_link, format_name):
                self.stats_callback("Selenium & LLM")
            return

        current_url = driver.get_current_url()
        domain = urlparse(current_url).netloc.replace("www.", "")
        
        if is_conference:
            if self.route_3_conference(driver, article_name, format_name):
                self.stats_callback("Selenium & LLM")
                return

        rule = self.rules.get(domain, self.rules.get("default", {}))
        
        if self.execute_route_1_and_2(driver, rule, format_name, article_name):
            self.stats_callback("Selenium & LLM")
        else:
            self.log("log", f"❌ All extraction routes failed for {article_name}.")

    def execute_route_1_and_2(self, driver, rule, format_name, article_name):
        try:
            page_text_raw = driver.get_text("body")
        except:
            page_text_raw = driver.get_page_source()
            
        analysis = self.analyze_page_with_llm(page_text_raw, article_name)
        
        if not analysis.get("is_full_paper", True):
            self.log("log", f"LLM detected abstract-only. Saving abstract PDF directly.")
            abstract_text = analysis.get("extracted_abstract", "")
            if abstract_text:
                try:
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Arial", size=12)
                    safe_text = abstract_text.encode('latin-1', 'replace').decode('latin-1')
                    pdf.multi_cell(0, 10, txt=f"Title: {article_name}\n\nAbstract:\n{safe_text}")
                    save_path = os.path.join(self.output_dir, f"{format_name}_abstract.pdf")
                    pdf.output(save_path)
                    return True
                except Exception as e:
                    self.log("error", f"Abstract PDF Generation Error: {e}")
        
        timeout = rule.get("timeout", 15)
        pdf_meta = rule.get("pdf_meta_tag", "citation_pdf_url")
        xpath_btn = rule.get("button_xpath", "")

        try:
            driver.implicitly_wait(min(timeout, 5))
            meta_element = driver.find_elements(By.CSS_SELECTOR, f"meta[name='{pdf_meta}']")
            if meta_element:
                pdf_url = meta_element[0].get_attribute("content")
                if pdf_url:
                    driver.get(pdf_url)
                    time.sleep(5)
                    self.save_pdf_from_browser(driver, format_name)
                    return True
            
            if xpath_btn:
                try:
                    if driver.is_element_present(xpath_btn):
                        driver.click(xpath_btn)
                        time.sleep(5)
                        self.save_pdf_from_browser(driver, format_name)
                        return True
                except:
                    pass
        except Exception:
            pass
        finally:
            driver.implicitly_wait(10)

        return self.route_2_print_abstract(driver, format_name, article_name)

    def route_2_print_abstract(self, driver, format_name, article_name="article"):
        try:
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "landscape": False, "displayHeaderFooter": False, "printBackground": True, "preferCSSPageSize": True
            })
            pdf_bytes = base64.b64decode(pdf_data['data'])
            save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
            with open(save_path, "wb") as f:
                f.write(pdf_bytes)
            return True
        except Exception as e:
            return False

    def route_3_conference(self, driver, article_name, format_name):
        try:
            paragraphs = driver.find_elements(By.CSS_SELECTOR, "p")
            best_match, best_score = None, 0
            for p in paragraphs:
                text = p.text.strip()
                if not text: continue
                score = fuzz.token_set_ratio(article_name, text)
                if score > best_score:
                    best_score = score
                    best_match = text
                    
            if best_score > 90 and best_match:
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", size=12)
                safe_text = best_match.encode('latin-1', 'replace').decode('latin-1')
                pdf.multi_cell(0, 10, txt=safe_text)
                save_path = os.path.join(self.output_dir, f"{format_name}_conference.pdf")
                pdf.output(save_path)
                return True
        except Exception:
            pass
        return False

    def route_4_bing_fallback(self, driver, article_name, author_name, bing_link, format_name):
        try:
            query = f"{article_name} {author_name}".strip()
            search_url = bing_link if bing_link and bing_link != 'nan' else f"https://www.bing.com/search?q={query}"
            driver.get(search_url)
            time.sleep(3)
            
            results = driver.find_elements(By.CSS_SELECTOR, "li.b_algo h2 a")
            urls = [el.get_attribute("href") for el in results[:10]]
            
            for url in urls:
                if not self.running: break
                if not url: continue
                driver.get(url)
                time.sleep(3)
                
                page_title = driver.title
                page_body = driver.get_text("body")[:5000]
                
                if max(fuzz.token_set_ratio(article_name, page_title), fuzz.token_set_ratio(article_name, page_body)) > 90:
                    domain = urlparse(url).netloc.replace("www.", "")
                    rule = self.rules.get(domain, self.rules.get("default", {}))
                    return self.execute_route_1_and_2(driver, rule, format_name, article_name)
        except Exception:
            pass
        return False

    def save_pdf_from_browser(self, driver, format_name):
        self.route_2_print_abstract(driver, format_name)
