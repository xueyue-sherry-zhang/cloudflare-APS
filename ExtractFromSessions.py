#!/usr/bin/env python3
"""
Extract abstracts from APS Summit sessions.
Visits session pages, finds all talks in each session, then extracts abstracts.
"""

import re
import json
import os
import hashlib
from bs4 import BeautifulSoup
try:
    import undetected_playwright as up
    from playwright.sync_api import sync_playwright
    # Use stealth_sync wrapper from undetected-playwright
    UNDETECTED_AVAILABLE = True
except ImportError:
    from playwright.sync_api import sync_playwright
    UNDETECTED_AVAILABLE = False
    up = None
    print("Warning: undetected-playwright not available, using regular playwright")
from tqdm import tqdm

# Keywords to identify superconducting qubit related abstracts
SUPERCONDUCTING_QUBIT_KEYWORDS = [
    "superconducting qubit",
    "transmon",
    "fluxonium",
    "josephson junction",
    "josephson",
    "circuit qed",
    "cqed",
    "cavity qed",
    "quasiparticle",
    "readout resonator",
    "microwave resonator",
    "parametric amplifier",
    "jpa",
    "jtwpa",
    "purcell",
    "two-level system",
    "tls",
    "andreev",
    "coherence time",
    "t1",
    "t2",
    "cat qubit",
    "kerr cat",
    "gralmonium",
    "granular aluminium",
    "squid",
    "quarton",
    "cz gate",
    "cross resonance",
]

def guess_text(soup, selectors):
    """Try multiple selectors to find text content."""
    for sel in selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(" ", strip=True)
    return ""

def extract_talk_fields(html, url):
    """Extract title, authors, and abstract from an event page."""
    soup = BeautifulSoup(html, "lxml")

    # Try multiple selectors for title
    title = guess_text(soup, [
        "h1", 
        "header h1", 
        ".page-title", 
        "[data-testid='page-title']",
        "title",
        ".event-title",
        ".session-title",
        ".session-header h1",
        ".event-header h1"
    ])

    # Extract abstract - try multiple strategies
    abstract = ""
    
    # Strategy 1: Look for an "Abstract" heading
    abstract_heading = soup.find(lambda tag: tag.name in ["h2", "h3", "h4", "h5", "h6", "div", "section"] 
                                  and "abstract" in tag.get_text(strip=True).lower())
    if abstract_heading:
        for cand in abstract_heading.find_all_next(["p", "div", "section", "span"], limit=50):
            txt = cand.get_text(" ", strip=True)
            if txt and len(txt) > 50 and len(txt) < 5000:
                abstract = txt
                break
    
    # Strategy 2: Look for common abstract containers
    if not abstract:
        abstract_containers = soup.select(
            ".abstract, [class*='abstract'], [id*='abstract'], "
            ".session-abstract, .event-abstract, [data-abstract], "
            ".description, [class*='description']"
        )
        for container in abstract_containers:
            txt = container.get_text(" ", strip=True)
            if txt and len(txt) > 50 and len(txt) < 5000:
                abstract = txt
                break
    
    # Strategy 3: Look for text after "Abstract:" label
    if not abstract:
        abstract_labels = soup.find_all(string=re.compile(r"\bAbstract\s*:?", re.IGNORECASE))
        for label in abstract_labels:
            parent = label.parent
            next_elem = parent.find_next_sibling()
            if next_elem:
                txt = next_elem.get_text(" ", strip=True)
                if txt and len(txt) > 50:
                    abstract = txt
                    break
    
    # Strategy 4: Regex search in full text
    if not abstract:
        text = soup.get_text("\n", strip=True)
        patterns = [
            r"\bAbstract\b[:\s]*\n(.+?)(\n\n|\n[A-Z][a-z]+\s*:|\n[A-Z]{2,}\s|$)",
            r"Abstract[:\s]+(.+?)(?:\n\n|$)",
            r"ABSTRACT[:\s]+(.+?)(?:\n\n|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                abstract = m.group(1).strip()
                if len(abstract) > 2000:
                    abstract = abstract[:2000]
                break

    # Extract authors
    authors = ""
    author_patterns = [
        r"\(presenter\)",
        r"Presenter:",
        r"Author[s]?:",
        r"Speaker[s]?:",
    ]
    for pattern in author_patterns:
        possible = soup.find_all(string=re.compile(pattern, re.IGNORECASE))
        if possible:
            parent = possible[0].parent
            authors = parent.get_text(" ", strip=True)
            break
    
    if not authors:
        title_elem = soup.find("h1") or soup.find("title")
        if title_elem:
            for sibling in title_elem.find_next_siblings(limit=5):
                txt = sibling.get_text(" ", strip=True)
                if txt and len(txt) < 200:
                    authors = txt
                    break

    return {
        "url": url,
        "title": title,
        "authors": authors,
        "abstract": abstract,
    }

def extract_talks_from_session(page, session_url):
    """Extract list of talk URLs from a session page."""
    try:
        # Navigate and wait for page to fully load including JavaScript
        page.goto(session_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)  # Wait longer for Cloudflare to initialize and execute JavaScript
        
        # Wait for any Cloudflare Turnstile widget to appear and complete
        try:
            # Check if Turnstile widget exists
            turnstile_selector = '[data-sitekey], #cf-chl-widget, iframe[src*="challenges.cloudflare.com"]'
            turnstile_exists = page.query_selector(turnstile_selector)
            if turnstile_exists:
                print(f"  ⚠ Cloudflare Turnstile widget detected, waiting for completion...")
                # Wait for the challenge to complete (check for success indicators)
                page.wait_for_function(
                    """
                    () => {
                        // Check if challenge completed
                        const successDiv = document.getElementById('HqIO2');
                        const errorDiv = document.getElementById('challenge-error-text');
                        const loadingDiv = document.getElementById('tTobV9');
                        
                        // If success div is visible, challenge passed
                        if (successDiv && successDiv.style.display !== 'none') {
                            return true;
                        }
                        
                        // If error div is visible and not in noscript, challenge failed
                        if (errorDiv && errorDiv.closest('noscript') === null) {
                            return false;
                        }
                        
                        // If loading is hidden and we're past the challenge page
                        if (loadingDiv && loadingDiv.style.display === 'none' && 
                            !document.title.includes('Just a moment')) {
                            return true;
                        }
                        
                        return null; // Still processing
                    }
                    """,
                    timeout=45000
                )
                page.wait_for_timeout(3000)  # Extra wait after challenge completes
                print(f"  ✓ Cloudflare Turnstile challenge completed")
        except:
            pass  # No Turnstile widget or timeout, continue
        
        # Check for Cloudflare challenge and identify type
        html_content = page.content()
        title = page.title()
        
        # Identify specific Cloudflare challenge type
        cf_type = None
        if "Just a moment" in title or "Just a moment" in html_content[:2000]:
            cf_type = "Just a moment (standard challenge)"
        elif "Checking your browser" in html_content[:2000]:
            cf_type = "Checking your browser (verification)"
        elif "cf-browser-verification" in html_content.lower():
            cf_type = "Browser verification (cf-browser-verification)"
        elif "challenge" in title.lower() or "challenge-platform" in html_content.lower():
            cf_type = "Challenge platform"
        elif "ray id" in html_content.lower() or "cf-ray" in html_content.lower():
            # Cloudflare is present but might not be blocking
            if "access denied" in html_content.lower() or "blocked" in html_content.lower():
                cf_type = "Access denied / Blocked"
            else:
                cf_type = "Cloudflare detected (may not be blocking)"
        
        is_cloudflare = cf_type is not None
        
        if is_cloudflare:
            print(f"  ⚠ Cloudflare challenge detected on {session_url}")
            print(f"  Challenge type: {cf_type}")
            
            # Save Cloudflare page for inspection
            try:
                session_id = session_url.split('/')[-1] if '/' in session_url else "unknown"
                cf_filename = os.path.join("cloudflare_pages", f"SESSION_{session_id}_cloudflare.html")
                with open(cf_filename, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f"  💾 Saved Cloudflare page to: {cf_filename}")
            except Exception as e:
                print(f"  Warning: Could not save Cloudflare page: {e}")
            
            print(f"  Waiting for Cloudflare to complete (up to 60s, checking every 3s)...")
            print(f"  Please complete the Cloudflare challenge in the browser window if needed")
            
            # Try to wait for Cloudflare with periodic checks - longer wait for manual verification
            max_wait = 60000  # 60 seconds for manual verification
            check_interval = 3000  # Check every 3 seconds (less frequent checks)
            waited = 0
            passed = False
            
            try:
                while waited < max_wait:
                    page.wait_for_timeout(check_interval)
                    waited += check_interval
                    
                    # Check if Cloudflare passed
                    html_check = page.content()
                    title_check = page.title()
                    still_blocked = ("Just a moment" in title_check or 
                                   "Just a moment" in html_check[:2000] or
                                   "Checking your browser" in html_check[:2000])
                    
                    if not still_blocked:
                        passed = True
                        print(f"  ✓ Cloudflare challenge passed after {waited/1000:.1f}s ({cf_type})")
                        page.wait_for_timeout(2000)  # Extra wait after passing
                        break
                    
                    # Show progress every 15 seconds
                    if waited % 15000 == 0:
                        print(f"  Still waiting for verification... ({waited/1000:.0f}s elapsed)")
                        print(f"  If you've completed the challenge, the script should detect it soon")
                
                if not passed:
                    # Final check
                    new_html = page.content()
                    new_title = page.title()
                    still_cloudflare = ("Just a moment" in new_title or 
                                      "Just a moment" in new_html[:2000] or
                                      "Checking your browser" in new_html[:2000])
                    
                    if still_cloudflare:
                        # Check what type it still is
                        new_cf_type = None
                        if "Just a moment" in new_html[:2000]:
                            new_cf_type = "Just a moment"
                        elif "Checking your browser" in new_html[:2000]:
                            new_cf_type = "Checking your browser"
                        elif "access denied" in new_html.lower():
                            new_cf_type = "Access denied"
                        
                        # Save the failed Cloudflare page
                        try:
                            session_id = session_url.split('/')[-1] if '/' in session_url else "unknown"
                            cf_failed_filename = os.path.join("cloudflare_pages", f"SESSION_{session_id}_FAILED_after_{max_wait//1000}s.html")
                            with open(cf_failed_filename, 'w', encoding='utf-8') as f:
                                f.write(new_html)
                            print(f"  💾 Saved failed Cloudflare page to: {cf_failed_filename}")
                        except Exception as e:
                            print(f"  Warning: Could not save failed Cloudflare page: {e}")
                        
                        print(f"  ✗ Cloudflare challenge failed after {max_wait/1000:.0f}s - still blocked ({new_cf_type or 'unknown type'})")
                        return []
                    else:
                        print(f"  ✓ Cloudflare challenge passed after {max_wait/1000:.0f}s ({cf_type})")
                        
            except Exception as e:
                print(f"  ✗ Cloudflare wait error after {waited/1000:.1f}s: {e}")
                return []
        
        # Check if it's a 404
        html = page.content()
        if "404" in html or "not found" in html.lower() or "page not found" in html.lower():
            return []
        
        soup = BeautifulSoup(html, "lxml")
        
        # Extract session ID from URL
        session_match = re.search(r'/([A-Z]{3}-[A-Z]\d{2})', session_url)
        if not session_match:
            return []
        
        session_id = session_match.group(1)
        talk_urls = []
        
        # Method 1: Find links in HTML
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Make absolute URL
            if href.startswith('http'):
                full_url = href
            elif href.startswith('/'):
                full_url = f"https://summit.aps.org{href}"
            else:
                full_url = f"{session_url.rstrip('/')}/{href}"
            
            # Check if it's a talk URL in this session (ends with /number)
            if re.search(rf'/events/{re.escape(session_id)}/(\d+)$', full_url):
                talk_urls.append(full_url)
        
        # Method 2: Use JavaScript to find all links (more reliable for dynamic content)
        try:
            js_links = page.evaluate(f"""
                () => {{
                    const sessionId = '{session_id}';
                    const links = Array.from(document.querySelectorAll('a[href]'));
                    const talkLinks = [];
                    links.forEach(a => {{
                        const href = a.href;
                        // Match pattern: /events/SESSION-ID/number
                        const match = href.match(new RegExp(`/events/${{sessionId}}/(\\\\d+)$`));
                        if (match) {{
                            talkLinks.push(href);
                        }}
                    }});
                    return [...new Set(talkLinks)].sort();
                }}
            """)
            if js_links:
                talk_urls.extend(js_links)
        except Exception as e:
            pass
        
        # Method 3: Try to find talk numbers in the page content/text
        # Sometimes talks are listed but not as clickable links
        try:
            page_text = soup.get_text()
            # Look for patterns like "Talk 1", "Presentation 12", etc.
            talk_numbers = re.findall(rf'(?:talk|presentation|paper)\s+(\d+)', page_text, re.IGNORECASE)
            for num in set(talk_numbers):
                # Try both URL patterns
                url1 = f"https://summit.aps.org/events/{session_id}/{num}"
                url2 = f"https://summit.aps.org/smt/2026/events/{session_id}/{num}"
                talk_urls.extend([url1, url2])
        except:
            pass
        
        # Remove duplicates and sort
        talk_urls = sorted(set(talk_urls))
        
        return talk_urls
        
    except Exception as e:
        return []

def is_superconducting_qubit_related(title, abstract):
    """Check if title or abstract mentions superconducting qubit related terms."""
    combined_text = f"{title} {abstract}".lower()
    
    pattern_parts = []
    for keyword in SUPERCONDUCTING_QUBIT_KEYWORDS:
        escaped = re.escape(keyword)
        pattern_parts.append(escaped)
    
    pattern = re.compile("|".join(pattern_parts), re.IGNORECASE)
    return bool(pattern.search(combined_text))

def load_session_urls(filename="event_urls.txt"):
    """Load session URLs from file."""
    try:
        with open(filename, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        return urls
    except FileNotFoundError:
        print(f"Error: {filename} not found!")
        print("Generating session URLs...")
        # Fallback: generate them
        session_urls = []
        months = ["MAR"]
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        
        for month in months:
            for letter in letters:
                for num in range(1, 100):
                    event_id = f"{month}-{letter}{num:02d}"
                    url1 = f"https://summit.aps.org/smt/2026/events/{event_id}"
                    session_urls.append(url1)
                    url2 = f"https://summit.aps.org/events/{event_id}"
                    session_urls.append(url2)
        
        return sorted(set(session_urls))

def main():
    print("Loading session URLs from event_urls.txt...")
    session_urls = load_session_urls()
    print(f"Loaded {len(session_urls)} session URLs to process")
    
    rows = []
    HEADLESS = False  # Non-headless mode - browser window will be visible
    
    # Open file for continuous saving
    output_file = "valid_session_urls.txt"
    with open(output_file, 'w') as f:
        f.write("")
    
    def save_session_url(url):
        """Save a valid session URL immediately."""
        with open(output_file, 'a') as f:
            f.write(url + '\n')
    
    playwright_instance = None
    
    # Create persistent browser context directory for cookies/session
    browser_data_dir = os.path.join(os.getcwd(), "browser_data")
    os.makedirs(browser_data_dir, exist_ok=True)
    
    if UNDETECTED_AVAILABLE:
        # Use undetected-playwright stealth wrapper for better Cloudflare bypass
        print("Using undetected-playwright stealth wrapper for better Cloudflare bypass...")
        print(f"Using persistent browser data directory: {browser_data_dir}")
        print("This will save cookies/session so Cloudflare verification persists across pages")
        # Use regular sync_playwright, then wrap context with stealth_sync
        playwright_instance = sync_playwright()
        p = playwright_instance.__enter__()
        context = p.chromium.launch_persistent_context(
            user_data_dir=browser_data_dir,
            headless=HEADLESS,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            java_script_enabled=True,
            locale="en-US",
            timezone_id="America/New_York",
            bypass_csp=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ],
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
        )
        # Wrap context with stealth features
        context = up.stealth_sync(context)
        context.set_default_timeout(60000)
        page = context.new_page()
        browser = None  # launch_persistent_context returns context, not browser
        
        # Add additional stealth scripts and error handling
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({
                    query: () => Promise.resolve({ state: 'granted' })
                })
            });
            
            // Suppress console errors that might interfere with Cloudflare
            const originalError = console.error;
            console.error = function(...args) {
                const msg = args.join(' ');
                // Don't suppress important errors, but suppress require() errors from Cloudflare scripts
                if (!msg.includes('require is not defined')) {
                    originalError.apply(console, args);
                }
            };
            
            // Add a basic require polyfill for Cloudflare scripts if needed
            if (typeof window.require === 'undefined') {
                window.require = function(module) {
                    // Return empty object for Cloudflare modules
                    return {};
                };
            }
        """)
        
        # Suppress console errors in Playwright
        def handle_console(msg):
            text = msg.text
            # Suppress require errors from Cloudflare
            if "require is not defined" not in text:
                if msg.type == 'error':
                    tqdm.write(f"    [Console Error] {text[:100]}")
        
        page.on("console", handle_console)
    else:
        # Fallback to regular playwright - use persistent context for cookies
        print(f"Using persistent browser data directory: {browser_data_dir}")
        playwright_instance = sync_playwright()
        p = playwright_instance.__enter__()
        context = p.chromium.launch_persistent_context(
            user_data_dir=browser_data_dir,
            headless=HEADLESS,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            java_script_enabled=True,
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                '--disable-blink-features=AutomationControlled',
            ],
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        browser = None  # launch_persistent_context returns context, not browser
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)
        context.set_default_timeout(60000)
        page = context.new_page()
    
    print("\nProcessing session URLs...")
    print("For each session, we'll:")
    print("  1. Check if session exists")
    print("  2. Extract list of talks in that session")
    print("  3. Extract abstracts from each talk")
    print()
    
    valid_sessions = 0
    total_talks_found = 0
    total_talks_extracted = 0
    total_superconducting_qubit = 0
    
    # Create output directories
    output_dir = "session_abstracts"
    html_dir = "talk_htmls"
    cloudflare_dir = "cloudflare_pages"
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(html_dir, exist_ok=True)
    os.makedirs(cloudflare_dir, exist_ok=True)
    
    try:
        # Process sessions with tqdm
        for session_url in tqdm(session_urls, desc="Processing sessions", unit="session"):
            try:
                # Small delay to avoid Cloudflare
                if valid_sessions > 0 and valid_sessions % 10 == 0:
                    page.wait_for_timeout(2000)
                else:
                    page.wait_for_timeout(500)
                
                # Extract talks from this session
                tqdm.write(f"  Processing session: {session_url}")
                # Extract session_id from URL
                session_id = session_url.rstrip('/').split('/')[-1] if '/' in session_url else "unknown"
                talk_urls = extract_talks_from_session(page, session_url)
                
                if not talk_urls:
                    tqdm.write(f"  ⚠ No talks found for {session_id} (may be Cloudflare blocked or session doesn't exist)")
                    continue
                
                tqdm.write(f"  ✓ Extracted {len(talk_urls)} talk URLs: {talk_urls[:3]}..." if len(talk_urls) > 3 else f"  ✓ Extracted {len(talk_urls)} talk URLs: {talk_urls}")
                
                if talk_urls:
                    # Session exists and has talks!
                    valid_sessions += 1
                    total_talks_found += len(talk_urls)
                    save_session_url(session_url)
                    
                    session_id = session_url.split('/')[-1]
                    json_file = os.path.join(output_dir, f"{session_id}.json")
                    
                    tqdm.write(f"  Found session {session_id} with {len(talk_urls)} talks")
                    
                    # Initialize list for this session's talks
                    session_talks = []
                    
                    # Now extract abstracts from each talk
                    for talk_url in tqdm(talk_urls, desc=f"  Talks in {session_id}", 
                                        unit="talk", leave=False):
                        try:
                            tqdm.write(f"    Processing talk: {talk_url}")
                            page.goto(talk_url, wait_until="domcontentloaded", timeout=15000)
                            page.wait_for_timeout(2000)
                            
                            # Check for Cloudflare challenge and identify type
                            html_pre_check = page.content()
                            title = page.title()
                            
                            # Identify specific Cloudflare challenge type
                            cf_type = None
                            if "Just a moment" in title or "Just a moment" in html_pre_check[:2000]:
                                cf_type = "Just a moment (standard challenge)"
                            elif "Checking your browser" in html_pre_check[:2000]:
                                cf_type = "Checking your browser (verification)"
                            elif "cf-browser-verification" in html_pre_check.lower():
                                cf_type = "Browser verification (cf-browser-verification)"
                            elif "challenge" in title.lower() or "challenge-platform" in html_pre_check.lower():
                                cf_type = "Challenge platform"
                            elif "ray id" in html_pre_check.lower() or "cf-ray" in html_pre_check.lower():
                                if "access denied" in html_pre_check.lower() or "blocked" in html_pre_check.lower():
                                    cf_type = "Access denied / Blocked"
                                else:
                                    cf_type = "Cloudflare detected (may not be blocking)"
                            
                            is_cloudflare = cf_type is not None
                            
                            if is_cloudflare:
                                tqdm.write(f"    ⚠ Cloudflare challenge detected: {cf_type}")
                                
                                # Save Cloudflare page for inspection
                                try:
                                    url_parts = talk_url.rstrip('/').split('/')
                                    if len(url_parts) >= 2:
                                        session_id_from_url = url_parts[-2]
                                        talk_num = url_parts[-1]
                                        cf_filename = os.path.join(cloudflare_dir, f"TALK_{session_id_from_url}_{talk_num}_cloudflare.html")
                                    else:
                                        import hashlib
                                        url_hash = hashlib.md5(talk_url.encode()).hexdigest()[:8]
                                        cf_filename = os.path.join(cloudflare_dir, f"TALK_{url_hash}_cloudflare.html")
                                    
                                    with open(cf_filename, 'w', encoding='utf-8') as f:
                                        f.write(html_pre_check)
                                    tqdm.write(f"    💾 Saved Cloudflare page to: {cf_filename}")
                                except Exception as e:
                                    tqdm.write(f"    Warning: Could not save Cloudflare page: {e}")
                                
                                tqdm.write(f"    Waiting up to 60s for manual verification (checking every 3s)...")
                                tqdm.write(f"    Please complete the Cloudflare challenge in the browser window")
                                tqdm.write(f"    The script will detect when verification completes")
                                
                                # Try to wait for Cloudflare with periodic checks - longer wait for manual verification
                                max_wait = 60000  # 60 seconds for manual verification
                                check_interval = 3000  # Check every 3 seconds (less frequent checks)
                                waited = 0
                                passed = False
                                
                                try:
                                    while waited < max_wait:
                                        page.wait_for_timeout(check_interval)
                                        waited += check_interval
                                        
                                        # Check if Cloudflare passed - handle navigation gracefully
                                        try:
                                            # Wait for page to be stable (not navigating)
                                            page.wait_for_load_state("domcontentloaded", timeout=2000)
                                            
                                            html_check = page.content()
                                            title_check = page.title()
                                            still_blocked = ("Just a moment" in title_check or 
                                                             "Just a moment" in html_check[:2000] or
                                                             "Checking your browser" in html_check[:2000])
                                            
                                            if not still_blocked:
                                                passed = True
                                                tqdm.write(f"    ✓ Cloudflare challenge passed after {waited/1000:.1f}s ({cf_type})")
                                                # Wait longer for navigation and cookie saving
                                                try:
                                                    page.wait_for_load_state("networkidle", timeout=10000)
                                                except:
                                                    pass
                                                # Extra wait to ensure cookies are saved to persistent context
                                                tqdm.write(f"    Waiting 5s for cookies to be saved...")
                                                page.wait_for_timeout(5000)
                                                break
                                            
                                            # Show progress every 15 seconds
                                            if waited % 15000 == 0:
                                                tqdm.write(f"    Still waiting for verification... ({waited/1000:.0f}s elapsed)")
                                                tqdm.write(f"    If you've completed the challenge, the script should detect it soon")
                                        
                                        except Exception as nav_error:
                                            # Page might be navigating - this could mean challenge passed!
                                            nav_error_str = str(nav_error).lower()
                                            if "navigation" in nav_error_str or "context was destroyed" in nav_error_str or "navigating" in nav_error_str:
                                                tqdm.write(f"    ⚠ Page navigating (challenge may have passed), waiting for navigation to complete...")
                                                try:
                                                    # Wait for navigation to complete - use longer timeout
                                                    page.wait_for_load_state("networkidle", timeout=15000)
                                                    # Extra wait for page to stabilize
                                                    page.wait_for_timeout(3000)
                                                    
                                                    # Check if we're past Cloudflare now
                                                    html_after_nav = page.content()
                                                    title_after_nav = page.title()
                                                    still_blocked_after = ("Just a moment" in title_after_nav or 
                                                                          "Just a moment" in html_after_nav[:2000] or
                                                                          "Checking your browser" in html_after_nav[:2000])
                                                    
                                                    if not still_blocked_after:
                                                        passed = True
                                                        tqdm.write(f"    ✓ Cloudflare challenge passed (navigation completed)")
                                                        # Wait longer after navigation to ensure cookies are saved
                                                        tqdm.write(f"    Waiting 5s for cookies to be saved to persistent context...")
                                                        page.wait_for_timeout(5000)
                                                        break
                                                    else:
                                                        # Check if it's asking to verify again (refresh loop)
                                                        if "verify" in html_after_nav.lower() or "challenge" in html_after_nav.lower()[:1000]:
                                                            tqdm.write(f"    ⚠ Still seeing Cloudflare challenge after navigation")
                                                            tqdm.write(f"    This might mean:")
                                                            tqdm.write(f"      1. Verification didn't complete successfully")
                                                            tqdm.write(f"      2. Cookies aren't being saved properly")
                                                            tqdm.write(f"      3. Cloudflare detected automation")
                                                            tqdm.write(f"    Continuing to wait...")
                                                        # Still blocked, continue waiting
                                                        pass
                                                except Exception as nav_wait_error:
                                                    # If we can't wait for navigation, assume it passed and continue
                                                    nav_wait_str = str(nav_wait_error).lower()
                                                    if "timeout" not in nav_wait_str:
                                                        tqdm.write(f"    ⚠ Navigation wait error, assuming challenge passed: {nav_wait_error}")
                                                        passed = True
                                                        break
                                                    # Navigation timeout, continue waiting
                                                    pass
                                                except Exception as nav_wait_error:
                                                    # If we can't wait for navigation, assume it passed and continue
                                                    nav_wait_str = str(nav_wait_error).lower()
                                                    if "timeout" not in nav_wait_str:
                                                        tqdm.write(f"    ⚠ Navigation wait error, assuming challenge passed: {nav_wait_error}")
                                                        passed = True
                                                        break
                                                    # Navigation timeout, continue waiting
                                                    pass
                                            else:
                                                # Other error, log and continue
                                                if waited % 10000 == 0:
                                                    tqdm.write(f"    Warning during check: {nav_error}")
                                                pass
                                    
                                    if not passed:
                                        # Final check - wait a bit more for manual verification
                                        tqdm.write(f"    Final check after {max_wait/1000:.0f}s...")
                                        try:
                                            page.wait_for_load_state("networkidle", timeout=5000)
                                        except:
                                            pass
                                        
                                        try:
                                            html_after = page.content()
                                            title_after = page.title()
                                            still_cloudflare = ("Just a moment" in title_after or 
                                                              "Just a moment" in html_after[:2000] or
                                                              "Checking your browser" in html_after[:2000] or
                                                              "challenge" in title_after.lower())
                                        except Exception as e:
                                            # If we can't get content, assume navigation happened (challenge passed)
                                            nav_error_str = str(e).lower()
                                            if "navigation" in nav_error_str or "context" in nav_error_str:
                                                tqdm.write(f"    ⚠ Page navigated, assuming challenge passed")
                                                still_cloudflare = False
                                                passed = True
                                            else:
                                                still_cloudflare = True
                                        
                                        if still_cloudflare:
                                            # Check what type it still is
                                            new_cf_type = None
                                            if "Just a moment" in html_after[:2000]:
                                                new_cf_type = "Just a moment"
                                            elif "Checking your browser" in html_after[:2000]:
                                                new_cf_type = "Checking your browser"
                                            elif "access denied" in html_after.lower():
                                                new_cf_type = "Access denied"
                                            
                                            # Save the failed Cloudflare page
                                            try:
                                                url_parts = talk_url.rstrip('/').split('/')
                                                if len(url_parts) >= 2:
                                                    session_id_from_url = url_parts[-2]
                                                    talk_num = url_parts[-1]
                                                    cf_failed_filename = os.path.join(cloudflare_dir, f"TALK_{session_id_from_url}_{talk_num}_FAILED_after_{max_wait//1000}s.html")
                                                else:
                                                    import hashlib
                                                    url_hash = hashlib.md5(talk_url.encode()).hexdigest()[:8]
                                                    cf_failed_filename = os.path.join(cloudflare_dir, f"TALK_{url_hash}_FAILED_after_{max_wait//1000}s.html")
                                                
                                                with open(cf_failed_filename, 'w', encoding='utf-8') as f:
                                                    f.write(html_after)
                                                tqdm.write(f"    💾 Saved failed Cloudflare page to: {cf_failed_filename}")
                                            except Exception as e:
                                                tqdm.write(f"    Warning: Could not save failed Cloudflare page: {e}")
                                            
                                            tqdm.write(f"    ✗ Cloudflare challenge failed after {max_wait/1000:.0f}s - still blocked ({new_cf_type or 'unknown type'}), skipping talk")
                                            continue
                                        else:
                                            tqdm.write(f"    ✓ Cloudflare challenge passed after {max_wait/1000:.0f}s ({cf_type})")
                                            
                                except Exception as e:
                                    error_str = str(e).lower()
                                    if "navigation" in error_str or "context was destroyed" in error_str or "navigating" in error_str:
                                        # Navigation error - challenge might have passed, wait and check
                                        tqdm.write(f"    ⚠ Navigation detected during wait, checking if challenge passed...")
                                        try:
                                            page.wait_for_load_state("networkidle", timeout=10000)
                                            page.wait_for_timeout(2000)
                                            # Check final state
                                            html_final = page.content()
                                            title_final = page.title()
                                            if "Just a moment" not in title_final and "Just a moment" not in html_final[:2000]:
                                                tqdm.write(f"    ✓ Challenge passed (navigation completed)")
                                                passed = True
                                            else:
                                                tqdm.write(f"    ✗ Still blocked after navigation, skipping talk")
                                                continue
                                        except:
                                            tqdm.write(f"    ✗ Could not verify after navigation, skipping talk")
                                            continue
                                    else:
                                        tqdm.write(f"    ✗ Cloudflare wait error after {waited/1000:.1f}s: {e}, skipping talk")
                                        continue
                            
                            # Get page content - handle navigation if challenge passed
                            if is_cloudflare and passed:
                                # Wait extra time for page to fully load after navigation
                                try:
                                    page.wait_for_load_state("networkidle", timeout=10000)
                                    page.wait_for_timeout(3000)
                                except:
                                    pass
                            
                            # Get HTML content - handle navigation gracefully
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=5000)
                                html = page.content()
                            except Exception as nav_err:
                                nav_err_str = str(nav_err).lower()
                                if "navigation" in nav_err_str or "navigating" in nav_err_str or "context" in nav_err_str:
                                    tqdm.write(f"    ⚠ Page navigating, waiting for completion...")
                                    try:
                                        page.wait_for_load_state("networkidle", timeout=15000)
                                        page.wait_for_timeout(3000)
                                        html = page.content()
                                    except Exception as nav_wait_err:
                                        tqdm.write(f"    ✗ Could not get page content after navigation: {nav_wait_err}, skipping")
                                        continue
                                else:
                                    tqdm.write(f"    ✗ Error getting page content: {nav_err}, skipping")
                                    continue
                            
                            # Save HTML for debugging
                            try:
                                # Create filename from URL (session_id/talk_number.html)
                                url_parts = talk_url.rstrip('/').split('/')
                                if len(url_parts) >= 2:
                                    session_id_from_url = url_parts[-2]  # e.g., MAR-A01
                                    talk_num = url_parts[-1]  # e.g., 1, 2, etc.
                                    html_filename = os.path.join(html_dir, f"{session_id_from_url}_{talk_num}.html")
                                else:
                                    # Fallback: use hash of URL
                                    url_hash = hashlib.md5(talk_url.encode()).hexdigest()[:8]
                                    html_filename = os.path.join(html_dir, f"talk_{url_hash}.html")
                                
                                with open(html_filename, 'w', encoding='utf-8') as f:
                                    f.write(html)
                            except Exception as e:
                                tqdm.write(f"    Warning: Could not save HTML: {e}")
                            
                            if "404" in html or "not found" in html.lower():
                                tqdm.write(f"    Talk not found (404), skipping")
                                continue
                            
                            rec = extract_talk_fields(html, talk_url)
                            
                            if rec.get("title") and len(rec.get("title", "")) > 10:
                                if rec.get("title") != "Just a moment...":
                                    # Add superconducting qubit flag
                                    rec["is_superconducting_qubit"] = is_superconducting_qubit_related(
                                        str(rec.get("title", "")),
                                        str(rec.get("abstract", ""))
                                    )
                                    
                                    session_talks.append(rec)
                                    total_talks_extracted += 1
                                    if rec["is_superconducting_qubit"]:
                                        total_superconducting_qubit += 1
                                    
                                    # Save immediately after each talk
                                    try:
                                        # Read existing file if it exists
                                        if os.path.exists(json_file):
                                            with open(json_file, 'r', encoding='utf-8') as f:
                                                existing_talks = json.load(f)
                                        else:
                                            existing_talks = []
                                        
                                        # Add new talk (avoid duplicates by URL)
                                        existing_urls = {talk.get('url') for talk in existing_talks}
                                        if rec.get('url') not in existing_urls:
                                            existing_talks.append(rec)
                                            
                                            # Write back to file
                                            with open(json_file, 'w', encoding='utf-8') as f:
                                                json.dump(existing_talks, f, indent=2, ensure_ascii=False)
                                            tqdm.write(f"    ✓ Saved talk: {rec.get('title', 'Unknown')[:50]}...")
                                    except Exception as e:
                                        tqdm.write(f"    Warning: Could not save talk to {json_file}: {e}")
                                else:
                                    tqdm.write(f"    Invalid title, skipping")
                            else:
                                tqdm.write(f"    No valid title found, skipping")
                            
                        except Exception as e:
                            tqdm.write(f"    Error processing talk {talk_url}: {e}")
                            continue
                    
                    # Final summary for this session
                    if len(session_talks) > 0:
                        sc_qubit_count = sum(1 for talk in session_talks if talk.get("is_superconducting_qubit"))
                        tqdm.write(f"    ✓ Session {session_id} complete: {len(session_talks)} talks saved ({sc_qubit_count} superconducting qubit related)")
                    else:
                        tqdm.write(f"    ⚠ Session {session_id}: No talks extracted")
                
            except Exception as e:
                tqdm.write(f"    ✗ Error processing session {session_url}: {e}")
                import traceback
                tqdm.write(traceback.format_exc())
                continue
        
    finally:
        # Clean up
        try:
            if context:
                context.close()
            if browser is not None:
                browser.close()
            if playwright_instance is not None:
                playwright_instance.__exit__(None, None, None)  # Exit the context manager
        except Exception as e:
            print(f"Warning during cleanup: {e}")
            pass
    
    # Final summary
    print(f"\n{'='*60}")
    print(f"Processing complete!")
    print(f"  Valid sessions found: {valid_sessions}")
    print(f"  Total talks found: {total_talks_found}")
    print(f"  Abstracts extracted: {total_talks_extracted}")
    print(f"  Superconducting qubit related: {total_superconducting_qubit}")
    print(f"{'='*60}\n")
    
    print(f"Results:")
    print(f"  Total sessions processed: {valid_sessions}")
    print(f"  Total talks extracted: {total_talks_extracted}")
    print(f"  Superconducting qubit related: {total_superconducting_qubit}")
    print(f"\nFiles saved:")
    print(f"  {output_dir}/  (JSON files, one per session)")
    print(f"  {output_file}  (valid session URLs)")
    print(f"\nEach session has its own JSON file with all talks and abstracts.")

if __name__ == "__main__":
    main()

