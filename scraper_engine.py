import concurrent.futures
import queue
import pandas as pd
import json
import os
import time
import base64
import re
import requests
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
        filename = re.sub(r'[\\/*?:"<>|]', "", str(filename))
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
                return True, "Success"
            else:
                return False, f"Invalid Content-Type or Status: {response.status_code}"
        except Exception as e:
            return False, f"Stream Error: {str(e)}"

    def download_by_unpaywall(self, doi, filename):
        if not self.unpaywall_email: return False, "No Unpaywall Email"
        url = f"https://api.unpaywall.org/v2/{doi}?email={self.unpaywall_email}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('is_oa') and data.get('best_oa_location'):
                    pdf_url = data['best_oa_location'].get('url_for_pdf')
                    if pdf_url:
                        return self._stream_pdf(pdf_url, filename)
                return False, "Not Open Access"
            return False, f"API Error {res.status_code}"
        except Exception as e:
            return False, f"Request Failed: {str(e)}"

    def download_by_pmcid(self, pmcid, filename):
        if not pmcid or str(pmcid).lower() == 'nan': return False, "Missing PMCID"
        clean_id = str(pmcid).upper().replace("PMC", "").strip()
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{clean_id}/pdf/"
        return self._stream_pdf(url, filename)

    def download_by_arxiv_id(self, arxiv_id, filename):
        if not arxiv_id or str(arxiv_id).lower() == 'nan': return False, "Missing arXiv ID"
        url = f"https://export.arxiv.org/pdf/{arxiv_id}.pdf"
        return self._stream_pdf(url, filename)

    def download_by_doaj(self, doi, filename):
        if not doi or str(doi).lower() == 'nan': return False, "Missing DOI"
        api_url = f"https://doaj.org/api/v2/search/articles/doi:{doi}"
        try:
            res = requests.get(api_url, timeout=10)
            if res.status_code == 200 and res.json().get("results"):
                article = res.json()["results"][0].get("bibjson", {})
                for link in article.get("link", []):
                    if link.get("type") == "fulltext":
                        return self._stream_pdf(link.get("url"), filename)
            return False, "Not found in DOAJ"
        except Exception as e:
            return False, f"DOAJ Error: {str(e)}"

    def download_by_semantic_scholar(self, query_url, filename):
        if not self.ss_key: return False, "No Semantic Scholar Key"
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
                    return self._stream_pdf(paper["openAccessPdf"]["url"], filename)
                        
                if paper.get("externalIds") and "ArXiv" in paper["externalIds"]:
                    return self.download_by_arxiv_id(paper["externalIds"]["ArXiv"], filename)
                    
                return False, "No Open Access PDF found"
            return False, f"API Error {res.status_code}"
        except Exception as e:
            return False, f"SS Error: {str(e)}"

    def download_by_core(self, query, filename):
        if not self.core_api_key: return False, "No CORE API Key"
        
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
                return False, "Rate Limited"
            return False, "Not found in CORE"
        except Exception as e:
            return False, f"CORE Error: {str(e)}"

class ScraperEngine:
    def __init__(self, excel_path, log_callback, progress_callback, stats_callback, flow_callback, max_workers=3, 
                 groq_api_key=None, unpaywall_email=None, ss_key=None, core_api_key=None, zenrows_key=None):
        self.excel_path = excel_path
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.stats_callback = stats_callback
        self.flow_callback = flow_callback
        self.max_workers = max_workers
        self.running = True
        self.output_dir = os.path.join(os.path.dirname(excel_path), "extracted_literature")
        os.makedirs(self.output_dir, exist_ok=True)
        self.rules = self.load_rules()
        self.driver_pool = queue.Queue()
        self.report_data = []
        self.zenrows_key = zenrows_key
        
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
                model="llama-3.1-8b-instant",
                response_format={"type": "json_object"},
                temperature=0.0
            )
            response_str = chat_completion.choices[0].message.content
            result = json.loads(response_str)
            return result
        except Exception as e:
            return {"is_full_paper": True, "extracted_abstract": ""}

    def log(self, msg_type, data):
        self.log_callback(msg_type, data)

    def load_rules(self):
        rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal_rules.json")
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"default": {"pdf_meta_tag": "citation_pdf_url", "button_xpath": "//button[contains(., 'Download PDF')]", "timeout": 20}}

    def stop(self):
        self.running = False

    def initialize_drivers(self):
        self.log("log", f"Initializing {self.max_workers} Chromium browsers in background...")
        for i in range(self.max_workers):
            if not self.running:
                break
            try:
                driver = Driver(uc=False, headless=True, no_sandbox=True)
                self.driver_pool.put(driver)
            except Exception as e:
                self.log("error", f"Failed to init browser {i+1}: {e}")

    def cleanup_drivers(self):
        self.log("log", "Cleaning up browser instances...")
        while not self.driver_pool.empty():
            driver = self.driver_pool.get()
            try:
                driver.quit()
            except:
                pass

    def record_history(self, article_name, tier, status, message):
        self.report_data.append({
            "Article Name": article_name,
            "Tier": tier,
            "Status": status,
            "Message": message
        })

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
            remaining_df = df.copy()
            total_pdfs = len(remaining_df)

            # Pre-initialize Chromium so they are ready instantly for the last tier
            self.initialize_drivers()

            # Define Tiers
            tiers = [
                ("Unpaywall", self._tier_unpaywall),
                ("PubMed Central", self._tier_pmc),
                ("arXiv", self._tier_arxiv),
                ("DOAJ", self._tier_doaj),
                ("Semantic Scholar", self._tier_ss),
                ("CORE API", self._tier_core),
                ("Sci-Hub", self._tier_scihub),
                ("ZenRows Proxy", self._tier_zenrows),
                ("Selenium & LLM", self._tier_selenium)
            ]

            completed_count = 0

            for tier_name, tier_func in tiers:
                if not self.running or len(remaining_df) == 0:
                    break

                input_count = len(remaining_df)
                self.log("log", f"Starting {tier_name} pass for {input_count} remaining PDFs...")
                
                # Flow Update: Processing
                self.flow_callback({
                    "tier": tier_name,
                    "input": input_count,
                    "retrieved": 0,
                    "remaining": input_count,
                    "status": "Processing..."
                })

                success_indices = []
                retrieved_this_tier = 0

                # Batch processing
                workers = 10 if tier_name != "Selenium & LLM" else self.max_workers

                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_idx = {
                        executor.submit(tier_func, row, duplicate_dois): idx 
                        for idx, row in remaining_df.iterrows()
                    }

                    for future in concurrent.futures.as_completed(future_to_idx):
                        if not self.running:
                            break
                        idx = future_to_idx[future]
                        row = remaining_df.loc[idx]
                        article_name = str(row['Article Name']).strip()
                        
                        try:
                            success, message = future.result()
                        except Exception as e:
                            success, message = False, f"Thread Crash: {str(e)}"

                        if success:
                            success_indices.append(idx)
                            retrieved_this_tier += 1
                            completed_count += 1
                            self.stats_callback(tier_name)
                            self.record_history(article_name, tier_name, "Success", message)
                            self.log("log", f"[{tier_name}] Downloaded: '{article_name}'")
                        else:
                            self.record_history(article_name, tier_name, "Failed", message)
                            self.log("error", f"[{tier_name}] Failed: '{article_name}' - Reason: {message}")

                        self.progress_callback(completed_count / total_pdfs)

                remaining_df = remaining_df.drop(index=success_indices)
                
                # Flow Update: Completed
                self.flow_callback({
                    "tier": tier_name,
                    "input": input_count,
                    "retrieved": retrieved_this_tier,
                    "remaining": len(remaining_df),
                    "status": "Completed"
                })

            # Generate Report
            report_df = pd.DataFrame(self.report_data)
            report_path = os.path.join(self.output_dir, "scraping_report.xlsx")
            report_df.to_excel(report_path, index=False)
            self.log("log", "Saved scraping_report.xlsx to output directory.")

        except Exception as e:
            self.log("error", f"Fatal error: {str(e)}")
        finally:
            self.cleanup_drivers()
            self.log_callback("done", None)

    # --- Tier Functions ---
    def _tier_unpaywall(self, row, dups):
        doi, fmt = str(row['DOI']).strip(), str(row['Format Name']).strip()
        if doi and doi != 'nan':
            return self.universal_downloader.download_by_unpaywall(doi, fmt)
        return False, "Invalid DOI"

    def _tier_pmc(self, row, dups):
        pmcid, fmt = str(row.get('PMCID', '')).strip(), str(row['Format Name']).strip()
        return self.universal_downloader.download_by_pmcid(pmcid, fmt)

    def _tier_arxiv(self, row, dups):
        arxiv_id, fmt = str(row.get('arXiv ID', '')).strip(), str(row['Format Name']).strip()
        return self.universal_downloader.download_by_arxiv_id(arxiv_id, fmt)

    def _tier_doaj(self, row, dups):
        doi, fmt = str(row['DOI']).strip(), str(row['Format Name']).strip()
        if doi and doi != 'nan':
            return self.universal_downloader.download_by_doaj(doi, fmt)
        return False, "Invalid DOI"

    def _tier_ss(self, row, dups):
        doi, title, author, fmt = str(row['DOI']).strip(), str(row['Article Name']).strip(), str(row.get('Author Name', '')).strip(), str(row['Format Name']).strip()
        if doi and doi != 'nan':
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title,openAccessPdf,externalIds"
            suc, msg = self.universal_downloader.download_by_semantic_scholar(url, fmt)
            if suc: return suc, msg
        if title and title != 'nan':
            query = f"{title} {author}".strip()
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit=1&fields=title,openAccessPdf,externalIds"
            return self.universal_downloader.download_by_semantic_scholar(url, fmt)
        return False, "Missing Title and DOI"

    def _tier_core(self, row, dups):
        doi, title, author, fmt = str(row['DOI']).strip(), str(row['Article Name']).strip(), str(row.get('Author Name', '')).strip(), str(row['Format Name']).strip()
        if doi and doi != 'nan':
            query = f'(doi:"{doi}") AND _exists_:fullText'
            suc, msg = self.universal_downloader.download_by_core(query, fmt)
            if suc: return suc, msg
        if title and title != 'nan':
            parts = [f'title:"{title}"']
            if author and author != 'nan': parts.append(f'authors:"{author}"')
            query = f"({' AND '.join(parts)}) AND _exists_:fullText"
            return self.universal_downloader.download_by_core(query, fmt)
        return False, "Missing Title and DOI"

    def _tier_scihub(self, row, dups):
        doi, fmt = str(row['DOI']).strip(), str(row['Format Name']).strip()
        if not doi or doi == 'nan': return False, "Missing DOI"
        mirrors = ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"]
        for mirror in mirrors:
            try:
                res = requests.get(f"{mirror}/{doi}", headers=self.universal_downloader.headers, timeout=15)
                if res.status_code == 200:
                    pdf_match = re.search(r'<embed[^>]*src=[\'"]([^\'"]+)[\'"]', res.text)
                    if not pdf_match: pdf_match = re.search(r'<iframe[^>]*src=[\'"]([^\'"]+)[\'"]', res.text)
                    if pdf_match:
                        pdf_url = pdf_match.group(1)
                        if pdf_url.startswith("//"): pdf_url = "https:" + pdf_url
                        elif pdf_url.startswith("/"): pdf_url = mirror + pdf_url
                        suc, msg = self.universal_downloader._stream_pdf(pdf_url, fmt)
                        if suc: return True, "Sci-Hub Extraction Success"
            except: pass
        return False, "Not found on Sci-Hub mirrors"

    def _tier_zenrows(self, row, dups):
        if not self.zenrows_key:
            return False, "ZenRows API Key not configured"
            
        doi = str(row['DOI']).strip()
        format_name = str(row['Format Name']).strip()
        if doi.startswith('10.'):
            doi = f"https://doi.org/{doi}"
            
        if not doi or doi == 'nan' or not doi.startswith('http'):
            return False, "Invalid DOI for ZenRows"
            
        try:
            # 1. Fetch raw HTML to bypass Cloudflare
            params = {
                "apikey": self.zenrows_key,
                "url": doi,
                "js_render": "true",
                "antibot": "true",
                "premium_proxy": "true",
                "proxy_country": "us"
            }
            res = requests.get("https://api.zenrows.com/v1/", params=params, timeout=45)
            
            if res.status_code != 200:
                return False, f"ZenRows Blocked or Failed: {res.status_code}"
                
            html = res.text
            
            # 2. Extract PDF Link from DOM
            pdf_url = None
            meta_match = re.search(r'<meta\s+(?:[^>]*?\s+)?name=[\'"]citation_pdf_url[\'"]\s+content=[\'"]([^\'"]+)[\'"]', html, re.IGNORECASE)
            if meta_match:
                pdf_url = meta_match.group(1)
            else:
                # Fallback regex hunt for download buttons
                anchor_match = re.search(r'<a[^>]+href=[\'"]([^\'"]+\.pdf[^\'"]*)[\'"][^>]*>', html, re.IGNORECASE)
                if anchor_match:
                    pdf_url = anchor_match.group(1)
            
            if not pdf_url:
                return False, "Bypassed Cloudflare but no PDF link found in DOM"
                
            # Handle relative URLs
            if pdf_url.startswith('/'):
                domain = "{0.scheme}://{0.netloc}".format(urlparse(res.url))
                pdf_url = domain + pdf_url
                
            # 3. Stream the actual PDF through ZenRows
            pdf_params = {
                "apikey": self.zenrows_key,
                "url": pdf_url,
                "antibot": "true",
                "premium_proxy": "true",
                "proxy_country": "us"
            }
            pdf_res = requests.get("https://api.zenrows.com/v1/", params=pdf_params, stream=True, timeout=60)
            
            content_type = pdf_res.headers.get('Content-Type', '').lower()
            if pdf_res.status_code == 200 and ('pdf' in content_type or 'octet-stream' in content_type):
                safe_name = self.universal_downloader._clean_filename(format_name)
                filepath = os.path.join(self.output_dir, f"{safe_name}.pdf")
                with open(filepath, 'wb') as f:
                    for chunk in pdf_res.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                return True, "Saved via ZenRows Premium Proxy"
                
            return False, "ZenRows found PDF link but stream failed"
            
        except Exception as e:
            return False, f"ZenRows Error: {str(e)}"

    def _tier_selenium(self, row, duplicate_dois):
        driver = self.driver_pool.get()
        try:
            doi = str(row['DOI']).strip()
            article_name = str(row['Article Name']).strip()
            format_name = str(row['Format Name']).strip()
            author_name = str(row.get('Author Name', '')).strip()
            bing_link = str(row.get('Bing Link', '')).strip()
            is_conference = doi in duplicate_dois

            if doi.startswith('10.'):
                doi = f"https://doi.org/{doi}"

            if not doi or doi == 'nan' or not doi.startswith('http'):
                self.log("log", f"[Selenium] Invalid DOI for '{article_name}'. Pivoting to Search Fallback.")
                return self._selenium_search_fallback(driver, article_name, author_name, bing_link, format_name)

            try:
                driver.get(doi)
                time.sleep(4)
                page_text_raw = driver.get_page_source().lower()
                
                # Cloudflare / Captcha Check
                if "cloudflare" in page_text_raw or "please wait while your request is being verified" in page_text_raw or "captcha" in page_text_raw:
                    self.log("log", f"[Selenium] Captcha/Cloudflare detected for '{article_name}'. Waiting 15s for auto-bypass...")
                    time.sleep(15)
                    page_text_raw = driver.get_page_source().lower()
                    
                if "error" in driver.get_current_url().lower() or "not found" in page_text_raw or "404" in page_text_raw:
                     self.log("log", f"[Selenium] 404/Error on DOI for '{article_name}'. Pivoting to Search Fallback.")
                     return self._selenium_search_fallback(driver, article_name, author_name, bing_link, format_name)
            except:
                self.log("log", f"[Selenium] Driver error on DOI for '{article_name}'. Pivoting to Search Fallback.")
                return self._selenium_search_fallback(driver, article_name, author_name, bing_link, format_name)

            domain = urlparse(driver.get_current_url()).netloc.replace("www.", "")
            
            if is_conference:
                suc, msg = self._selenium_conference(driver, article_name, format_name)
                if suc: return suc, msg

            rule = self.rules.get(domain, self.rules.get("default", {}))
            return self._selenium_execute_routes(driver, rule, format_name, article_name)
        finally:
            self.driver_pool.put(driver)

    def _selenium_execute_routes(self, driver, rule, format_name, article_name):
        try: page_text_raw = driver.get_text("body")
        except: page_text_raw = driver.get_page_source()
            
        analysis = self.analyze_page_with_llm(page_text_raw, article_name)
        if not analysis.get("is_full_paper", True):
            abstract_text = analysis.get("extracted_abstract", "")
            if abstract_text:
                try:
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Arial", size=12)
                    pdf.multi_cell(0, 10, txt=f"Title: {article_name}\n\nAbstract:\n{abstract_text.encode('latin-1', 'replace').decode('latin-1')}")
                    pdf.output(os.path.join(self.output_dir, f"{format_name}_abstract.pdf"))
                    return True, "Saved LLM Extracted Abstract"
                except Exception as e:
                    pass

        timeout = rule.get("timeout", 15)
        pdf_meta = rule.get("pdf_meta_tag", "citation_pdf_url")
        xpath_btn = rule.get("button_xpath", "")

        try:
            driver.implicitly_wait(min(timeout, 5))
            meta = driver.find_elements(By.CSS_SELECTOR, f"meta[name='{pdf_meta}']")
            if meta and meta[0].get_attribute("content"):
                driver.get(meta[0].get_attribute("content"))
                time.sleep(5)
                if self._selenium_print_pdf(driver, format_name): return True, "Saved via Meta Tag"
            
            if xpath_btn:
                if driver.is_element_present(xpath_btn):
                    driver.click(xpath_btn)
                    time.sleep(5)
                    if self._selenium_print_pdf(driver, format_name): return True, "Saved via Journal Rule Button"
            
            # Aggressive Button Hunt
            for fallback_xpath in [
                "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download pdf')]",
                "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download article')]",
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'pdf')]"
            ]:
                if driver.is_element_present(fallback_xpath):
                    try:
                        driver.click(fallback_xpath)
                        time.sleep(5)
                        if self._selenium_print_pdf(driver, format_name): return True, "Saved via Aggressive Button Hunt"
                    except: pass
        except: pass
        finally: driver.implicitly_wait(10)

        if self._selenium_print_pdf(driver, format_name): return True, "Saved Webpage Print Fallback"
        return False, "Selenium Routes Failed"

    def _selenium_print_pdf(self, driver, format_name):
        try:
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {"landscape": False, "displayHeaderFooter": False, "printBackground": True, "preferCSSPageSize": True})
            with open(os.path.join(self.output_dir, f"{format_name}.pdf"), "wb") as f:
                f.write(base64.b64decode(pdf_data['data']))
            return True
        except: return False

    def _selenium_conference(self, driver, article_name, format_name):
        try:
            best_match, best_score = None, 0
            for p in driver.find_elements(By.CSS_SELECTOR, "p"):
                text = p.text.strip()
                if not text: continue
                score = fuzz.token_set_ratio(article_name, text)
                if score > best_score:
                    best_score, best_match = score, text
                    
            if best_score > 90 and best_match:
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", size=12)
                pdf.multi_cell(0, 10, txt=best_match.encode('latin-1', 'replace').decode('latin-1'))
                pdf.output(os.path.join(self.output_dir, f"{format_name}_conference.pdf"))
                return True, "Extracted Conference Paragraph"
        except: pass
        return False, "Conference match failed"

    def _selenium_search_fallback(self, driver, article_name, author_name, bing_link, format_name):
        query = f"{article_name} {author_name}".strip()
        search_urls = [
            bing_link if bing_link and bing_link != 'nan' else None,
            f"https://html.duckduckgo.com/html/?q={query}",
            f"https://www.bing.com/search?q={query}"
        ]
        
        for search_url in [u for u in search_urls if u]:
            try:
                driver.get(search_url)
                time.sleep(3)
                
                if "duckduckgo" in search_url:
                    elements = driver.find_elements(By.CSS_SELECTOR, "a.result__url")[:5]
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, "li.b_algo h2 a")[:5]
                    
                urls = [el.get_attribute("href") for el in elements if el.get_attribute("href")]
                
                for url in urls:
                    if not self.running or not url: continue
                    driver.get(url)
                    time.sleep(4)
                    
                    try: page_text = driver.get_text("body")[:5000]
                    except: page_text = driver.get_page_source()[:5000]
                    
                    if max(fuzz.token_set_ratio(article_name, driver.title), fuzz.token_set_ratio(article_name, page_text)) > 85:
                        domain = urlparse(url).netloc.replace("www.", "")
                        rule = self.rules.get(domain, self.rules.get("default", {}))
                        suc, msg = self._selenium_execute_routes(driver, rule, format_name, article_name)
                        if suc: return suc, msg
            except: pass
            
        return False, "All Search Fallbacks Failed"
