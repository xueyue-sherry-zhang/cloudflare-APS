#!/usr/bin/env python3
"""
Web scraper for APS Summit schedule to find superconducting qubit related abstracts.
Requires Python 3.7+ and the following packages:
    pip install pandas playwright lxml beautifulsoup4
    playwright install chromium
"""

import re
import time
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://summit.aps.org"
SCHEDULE_URL = f"{BASE}/schedule/"

# URL patterns discovered:
# Pattern 1: https://summit.aps.org/smt/2026/events/MAR-A16 (session page)
# Pattern 2: https://summit.aps.org/events/MAR-A07/12 (individual talk page)
# We'll try both patterns

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
    
    # Strategy 1: Look for an "Abstract" heading (case-insensitive, flexible)
    abstract_heading = soup.find(lambda tag: tag.name in ["h2", "h3", "h4", "h5", "h6", "div", "section"] 
                                  and "abstract" in tag.get_text(strip=True).lower())
    if abstract_heading:
        # Get the next sibling or following content
        for cand in abstract_heading.find_all_next(["p", "div", "section", "span"], limit=50):
            txt = cand.get_text(" ", strip=True)
            if txt and len(txt) > 50 and len(txt) < 5000:  # Reasonable length
                abstract = txt
                break
    
    # Strategy 2: Look for common abstract containers with more selectors
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
        # Find elements containing "Abstract" text
        abstract_labels = soup.find_all(string=re.compile(r"\bAbstract\s*:?", re.IGNORECASE))
        for label in abstract_labels:
            parent = label.parent
            # Get next sibling or parent's next sibling
            next_elem = parent.find_next_sibling()
            if next_elem:
                txt = next_elem.get_text(" ", strip=True)
                if txt and len(txt) > 50:
                    abstract = txt
                    break
            # Or get text from parent's parent
            if not abstract and parent.parent:
                txt = parent.parent.get_text(" ", strip=True)
                # Extract text after "Abstract"
                m = re.search(r"Abstract\s*:?\s*(.+?)(?:\n\n|\n[A-Z][a-z]+\s*:|$)", txt, re.IGNORECASE | re.DOTALL)
                if m:
                    abstract = m.group(1).strip()
                    if len(abstract) > 2000:
                        abstract = abstract[:2000]
                    break
    
    # Strategy 4: Regex search in full text (improved)
    if not abstract:
        text = soup.get_text("\n", strip=True)
        # Try multiple patterns
        patterns = [
            r"\bAbstract\b[:\s]*\n(.+?)(\n\n|\n[A-Z][a-z]+\s*:|\n[A-Z]{2,}\s|$)",
            r"Abstract[:\s]+(.+?)(?:\n\n|$)",
            r"ABSTRACT[:\s]+(.+?)(?:\n\n|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                abstract = m.group(1).strip()
                # Clean up if it's too long
                if len(abstract) > 2000:
                    abstract = abstract[:2000]
                break

    # Extract authors
    authors = ""
    # Look for presenter/author indicators
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
    
    # If no authors found, try to get any text near the title
    if not authors:
        title_elem = soup.find("h1") or soup.find("title")
        if title_elem:
            # Look for text in nearby siblings
            for sibling in title_elem.find_next_siblings(limit=5):
                txt = sibling.get_text(" ", strip=True)
                if txt and len(txt) < 200:  # Authors are usually short
                    authors = txt
                    break

    return {
        "url": url,
        "title": title,
        "authors": authors,
        "abstract": abstract,
    }

def collect_all_event_urls(page):
    """Collect all event URLs from the schedule page."""
    seen_urls = set()
    api_urls = []
    
    # Intercept network requests to find API endpoints
    session_data_url = None
    
    def handle_response(response):
        url = response.url
        if "get-session-data" in url.lower() or "session-data" in url.lower():
            nonlocal session_data_url
            session_data_url = url
            print(f"  Found session data API: {url}")
        elif "events" in url.lower() or "schedule" in url.lower() or "api" in url.lower():
            if url not in api_urls:
                api_urls.append(url)
    
    page.on("response", handle_response)
    
    print("Loading schedule page and collecting all event links...")
    # Set a realistic user agent to help bypass basic bot detection
    page.set_extra_http_headers({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    # Use 'load' instead of 'networkidle' to avoid timeout from continuous network activity
    try:
        page.goto(SCHEDULE_URL, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"Warning: Page load timeout, trying with domcontentloaded: {e}")
        page.goto(SCHEDULE_URL, wait_until="domcontentloaded", timeout=60000)
    
    # Check if we hit a Cloudflare challenge
    page_title = page.title()
    page_content = page.content()
    
    if "Just a moment" in page_title or "challenge" in page_content.lower() or "turnstile" in page_content.lower():
        print("⚠️  Detected Cloudflare challenge page.")
        print("   The site is using bot protection. Waiting for automatic completion...")
        
        # Wait for the challenge to complete - look for the page to change
        max_wait = 30  # seconds
        waited = 0
        check_interval = 2  # seconds
        
        while waited < max_wait:
            page.wait_for_timeout(check_interval * 1000)
            waited += check_interval
            
            current_title = page.title()
            current_url = page.url
            
            # Check if we've moved past the challenge
            if "Just a moment" not in current_title and "/schedule" in current_url:
                print(f"   Challenge completed after {waited} seconds!")
                break
            
            if waited % 10 == 0:
                print(f"   Still waiting... ({waited}/{max_wait}s)")
        else:
            print(f"⚠️  Challenge may not have completed after {max_wait} seconds.")
            print("   If running in headless mode, try setting HEADLESS=False in the script")
            print("   to manually complete the challenge, or wait and run again.")
        
        # Final wait for page to fully load
        page.wait_for_timeout(3000)
    
    # Wait longer for JavaScript to render content
    print("Waiting for dynamic content to load...")
    page.wait_for_timeout(5000)  # Give extra time for dynamic content
    
    # Try to wait for any links to appear (wait up to 10 seconds)
    try:
        page.wait_for_selector("a[href]", timeout=10000)
        print("Links detected on page")
    except Exception:
        print("Warning: No links found after waiting, continuing anyway...")
    
    # Scroll to load all content (in case of lazy loading)
    print("Scrolling to load all content...")
    last_height = page.evaluate("document.body.scrollHeight")
    scroll_attempts = 0
    max_scrolls = 50
    
    while scroll_attempts < max_scrolls:
        # Scroll down
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1500)  # Wait longer between scrolls
        
        # Check if new content loaded
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            scroll_attempts += 1
        else:
            scroll_attempts = 0
        last_height = new_height
        
        if scroll_attempts >= 3:  # No new content for 3 scrolls
            break
    
    # Wait a bit more after scrolling
    page.wait_for_timeout(2000)
    
    # Try to interact with the schedule - click on date tabs or expand sections
    print("Checking for interactive schedule elements...")
    try:
        # Look for date tabs, day selectors, or expand buttons
        date_selectors = [
            "button[class*='day']",
            "button[class*='date']",
            "a[class*='day']",
            "a[class*='date']",
            "[data-day]",
            "[data-date]",
            ".schedule-day",
            ".day-tab"
        ]
        
        for selector in date_selectors:
            try:
                elements = page.locator(selector)
                count = elements.count()
                if count > 0:
                    print(f"  Found {count} potential date/day elements with selector: {selector}")
                    # Click the first few to load their events
                    for i in range(min(3, count)):
                        try:
                            elements.nth(i).click()
                            page.wait_for_timeout(2000)  # Wait for content to load
                        except:
                            pass
                    break
            except:
                continue
    except Exception as e:
        print(f"  Could not interact with schedule: {e}")
    
    # Collect all event links - try multiple strategies
    print("Extracting event links...")
    
    # Strategy 1: Get all links and filter
    print("  Strategy 1: Getting all links from page...")
    all_links = page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            return links.map(link => link.href).filter(href => href);
        }
    """)
    
    print(f"  Found {len(all_links)} total links on page")
    
    # Filter for event URLs - try multiple patterns
    for href in all_links:
        if not href:
            continue
        # Normalize URL
        url = href
        # Check for the correct event URL pattern: summit.aps.org/smt/2026/events/
        if "summit.aps.org/smt/" in url and "/events/" in url:
            seen_urls.add(url)
        # Also check for other patterns
        elif any(pattern in url for pattern in [
            "/smt/2026/events/",
            "my.aps.org/NC__Event",
        ]):
            # Exclude non-event pages
            if not any(exclude in url for exclude in [
                "registration",
                "privacy",
                "attend/",
                "about",
            ]):
                seen_urls.add(url)
    
    # Strategy 2: Try specific selectors - prioritize the correct pattern
    link_selectors = [
        "a[href*='/smt/2026/events/']",
        "a[href*='summit.aps.org/smt/']",
        "a[href*='/events/']",
        "a[href*='/smt/']",
        "a[href*='my.aps.org']",
        "a[href*='NC__Event']",
    ]
    
    # Strategy 3: Look for event data in JavaScript/data attributes
    print("  Strategy 3: Looking for event data in page...")
    try:
        # Try to find event data that might be embedded in the page
        event_data = page.evaluate("""
            () => {
                // Look for common event container patterns
                const eventContainers = Array.from(document.querySelectorAll(
                    '[class*="event"], [class*="session"], [class*="schedule"], [id*="event"], [id*="session"]'
                ));
                
                const eventLinks = [];
                eventContainers.forEach(container => {
                    const links = container.querySelectorAll('a[href]');
                    links.forEach(link => {
                        const href = link.href;
                        // Prioritize the correct pattern
                        if (href && (href.includes('summit.aps.org/smt/') && href.includes('/events/'))) {
                            eventLinks.push(href);
                        } else if (href && (href.includes('event') || href.includes('Event') || href.includes('?id='))) {
                            eventLinks.push(href);
                        }
                    });
                });
                return [...new Set(eventLinks)];
            }
        """)
        print(f"  Found {len(event_data)} potential event links from containers")
        for url in event_data:
            if url:
                # Prioritize the correct pattern
                if "summit.aps.org/smt/" in url and "/events/" in url:
                    seen_urls.add(url)
                elif not any(exclude in url for exclude in ["registration", "privacy", "attend/"]):
                    seen_urls.add(url)
    except Exception as e:
        print(f"  Error in Strategy 3: {e}")
    
    # Strategy 4: Try to extract event IDs from JavaScript/data
    print("  Strategy 4: Looking for event IDs in page data...")
    try:
        event_ids = page.evaluate("""
            () => {
                const results = [];
                
                // Check data attributes
                document.querySelectorAll('[data-event-id], [data-session-id], [data-id], [href*="/events/"]').forEach(el => {
                    const id = el.getAttribute('data-event-id') || 
                              el.getAttribute('data-session-id') || 
                              el.getAttribute('data-id') ||
                              (el.href ? el.href.match(/\\/([A-Z]{3}-[A-Z]\\d{2})\\/?$/) : null);
                    if (id) {
                        const idStr = Array.isArray(id) ? id[1] : id;
                        if (idStr && idStr.match(/^[A-Z]{3}-[A-Z]\\d{2}$/)) {
                            results.push(idStr);
                        }
                    }
                });
                
                // Check for IDs in text that match the pattern
                const text = document.body.innerText;
                const idPattern = /\\b([A-Z]{3}-[A-Z]\\d{2})\\b/g;
                let match;
                while ((match = idPattern.exec(text)) !== null) {
                    results.push(match[1]);
                }
                
                // Check JavaScript variables/objects
                try {
                    // Look for common variable names that might contain event data
                    const scripts = Array.from(document.querySelectorAll('script'));
                    scripts.forEach(script => {
                        const scriptText = script.textContent || script.innerHTML;
                        const matches = scriptText.matchAll(/\\b([A-Z]{3}-[A-Z]\\d{2})\\b/g);
                        for (const m of matches) {
                            results.push(m[1]);
                        }
                    });
                } catch(e) {}
                
                return [...new Set(results)];
            }
        """)
        print(f"  Found {len(event_ids)} potential event IDs from page")
        # Construct URLs from event IDs
        for event_id in event_ids:
            url = f"https://summit.aps.org/smt/2026/events/{event_id}"
            seen_urls.add(url)
    except Exception as e:
        print(f"  Error in Strategy 4: {e}")
    
    # Strategy 5: Extract event IDs from all links and page source more thoroughly
    print("  Strategy 5: Deep extraction of event IDs from page...")
    try:
        # Get all hrefs and extract event IDs
        all_hrefs = page.evaluate("""
            () => {
                const hrefs = [];
                // Get all links
                document.querySelectorAll('a[href]').forEach(a => hrefs.push(a.href));
                // Get all onclick handlers that might contain URLs
                document.querySelectorAll('[onclick]').forEach(el => {
                    const onclick = el.getAttribute('onclick');
                    if (onclick) hrefs.push(onclick);
                });
                return hrefs;
            }
        """)
        
        # Extract event IDs from all hrefs
        event_id_pattern = re.compile(r'/([A-Z]{3}-[A-Z]\d{2})/?')
        found_ids = set()
        for href in all_hrefs:
            matches = event_id_pattern.findall(str(href))
            found_ids.update(matches)
        
        print(f"  Found {len(found_ids)} event IDs from links: {list(found_ids)[:10]}...")
        for event_id in found_ids:
            url = f"https://summit.aps.org/smt/2026/events/{event_id}"
            seen_urls.add(url)
    except Exception as e:
        print(f"  Error in Strategy 5: {e}")
    
    # Strategy 6: Generate event IDs based on common patterns (smart sampling first)
    print("  Strategy 6: Generating likely event IDs...")
    try:
        # First, test a sample to find the actual range
        print("  Testing sample to find actual event ID range...")
        months = ["MAR"]
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        
        # Test a sample: first few numbers of each letter to find which letters are used
        sample_ids = []
        for month in months:
            for letter in letters:
                # Test first 10 of each letter to see if any exist
                for num in range(1, 11):
                    sample_ids.append(f"{month}-{letter}{num:02d}")
        
        # Quick test of samples
        print(f"  Testing {len(sample_ids)} sample IDs to find active ranges...")
        active_letters = set()
        for event_id in sample_ids[:50]:  # Test first 50 samples quickly
            url = f"https://summit.aps.org/smt/2026/events/{event_id}"
            try:
                response = page.request.get(url, timeout=3000)
                if response.status == 200:
                    letter = event_id.split('-')[1][0]
                    active_letters.add(letter)
                    seen_urls.add(url)  # Add the ones that exist
            except:
                pass
        
        if active_letters:
            print(f"  Found active letter ranges: {sorted(active_letters)}")
            # Generate full range for active letters, and test more numbers
            for month in months:
                for letter in active_letters:
                    for num in range(1, 100):
                        event_id = f"{month}-{letter}{num:02d}"
                        url = f"https://summit.aps.org/smt/2026/events/{event_id}"
                        seen_urls.add(url)
        else:
            # If sampling didn't work (Cloudflare blocking), generate full range
            print("  Sampling blocked, generating full range (this will take longer)...")
            for month in months:
                for letter in letters:
                    for num in range(1, 100):
                        event_id = f"{month}-{letter}{num:02d}"
                        url = f"https://summit.aps.org/smt/2026/events/{event_id}"
                        seen_urls.add(url)
    except Exception as e:
        print(f"  Error in Strategy 6: {e}")
        # Fallback: generate full range
        print("  Fallback: generating full range...")
        for month in ["MAR"]:
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                for num in range(1, 100):
                    event_id = f"{month}-{letter}{num:02d}"
                    url = f"https://summit.aps.org/smt/2026/events/{event_id}"
                    seen_urls.add(url)
    
    for selector in link_selectors:
        try:
            anchors = page.locator(selector)
            count = anchors.count()
            print(f"  Found {count} links with selector: {selector}")
            
            for i in range(count):
                try:
                    href = anchors.nth(i).get_attribute("href")
                    if not href:
                        continue
                    
                    # Normalize URL
                    if href.startswith("http"):
                        url = href
                    elif href.startswith("/"):
                        url = f"{BASE}{href}"
                    else:
                        url = f"{BASE}/{href}"
                    
                    # Check for the correct event URL pattern
                    if "summit.aps.org/smt/" in url and "/events/" in url:
                        seen_urls.add(url)
                    elif any(pattern in url for pattern in [
                        "/smt/2026/events/",
                        "my.aps.org/NC__Event",
                    ]):
                        # Exclude non-event pages
                        if not any(exclude in url for exclude in [
                            "registration",
                            "privacy",
                            "attend/",
                            "about",
                        ]):
                            seen_urls.add(url)
                except Exception as e:
                    continue
        except Exception as e:
            print(f"  Error with selector {selector}: {e}")
            continue
    
    # Strategy 7: Try to use the API endpoint if we found it
    if session_data_url:
        print(f"  Strategy 7: Fetching session data from API...")
        try:
            # Fetch the session data
            api_response = page.request.get(session_data_url, timeout=30000)
            if api_response.status == 200:
                import json
                try:
                    data = api_response.json()
                    print(f"  API returned data with {len(str(data))} characters")
                    
                    # Try to extract event IDs from the JSON
                    data_str = json.dumps(data)
                    event_id_matches = re.findall(r'([A-Z]{3}-[A-Z]\d{2})', data_str)
                    if event_id_matches:
                        unique_api_ids = set(event_id_matches)
                        print(f"  Found {len(unique_api_ids)} event IDs in API response")
                        for event_id in unique_api_ids:
                            url = f"https://summit.aps.org/smt/2026/events/{event_id}"
                            seen_urls.add(url)
                    
                    # Also try to find URLs directly in the JSON
                    url_matches = re.findall(r'https://summit\.aps\.org/smt/2026/events/[A-Z]{3}-[A-Z]\d{2}', data_str)
                    for url in url_matches:
                        seen_urls.add(url)
                        
                except json.JSONDecodeError:
                    # If not JSON, try to extract from text
                    text = api_response.text()
                    event_id_matches = re.findall(r'/([A-Z]{3}-[A-Z]\d{2})', text)
                    if event_id_matches:
                        unique_api_ids = set(event_id_matches)
                        print(f"  Found {len(unique_api_ids)} event IDs in API text response")
                        for event_id in unique_api_ids:
                            url = f"https://summit.aps.org/smt/2026/events/{event_id}"
                            seen_urls.add(url)
        except Exception as e:
            print(f"  Error fetching from API: {e}")
    
    print(f"Collected {len(seen_urls)} unique event URLs")
    
    # Debug: print a few sample URLs if found
    if seen_urls:
        print("  Sample URLs:")
        for url in list(seen_urls)[:5]:
            print(f"    {url}")
    else:
        # Save HTML for debugging
        html = page.content()
        with open("debug_schedule_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("  No event URLs found. Saved page HTML to debug_schedule_page.html for inspection.")
    
    return sorted(seen_urls)

def is_superconducting_qubit_related(title, abstract):
    """Check if title or abstract mentions superconducting qubit related terms."""
    combined_text = f"{title} {abstract}".lower()
    
    # Create pattern from keywords
    pattern_parts = []
    for keyword in SUPERCONDUCTING_QUBIT_KEYWORDS:
        # Escape special regex characters
        escaped = re.escape(keyword)
        pattern_parts.append(escaped)
    
    pattern = re.compile("|".join(pattern_parts), re.IGNORECASE)
    return bool(pattern.search(combined_text))

def main():
    rows = []
    
    # Set to False to see the browser (useful for debugging Cloudflare challenges)
    HEADLESS = True
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        # Set longer default timeout for page operations
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.set_default_timeout(60000)  # 60 seconds
        page = context.new_page()
        
        # Collect all event URLs from the schedule
        event_urls = collect_all_event_urls(page)
        
        if not event_urls:
            print("ERROR: No event URLs found. The page structure may have changed.")
            print("Please check the schedule page manually.")
            context.close()
            browser.close()
            return
        
        # Visit each event page and extract information
        print(f"\nVisiting {len(event_urls)} event pages to extract abstracts...")
        print("  (This may take a while if testing many generated URLs...)")
        
        # Separate real URLs from generated ones for different handling  
        real_urls = [u for u in event_urls if '/smt/2026/events/' in u and not any(f'MAR-{l}' in u for l in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')]
        generated_urls = [u for u in event_urls if u not in real_urls]
        
        print(f"  {len(real_urls)} URLs from page extraction")
        print(f"  {len(generated_urls)} URLs from pattern generation")
        
        # Process real URLs first (these are more likely to exist)
        all_urls = real_urls + generated_urls
        
        for idx, url in enumerate(all_urls, 1):
            # Add delay between requests to avoid triggering Cloudflare rate limits
            if idx > 1 and idx % 10 == 0:
                # Every 10 requests, wait a bit longer
                page.wait_for_timeout(2000)
            elif idx > 1:
                # Small delay between requests
                page.wait_for_timeout(500)
            
            try:
                # For generated URLs, skip the pre-check to save time
                # Just go straight to page load - Cloudflare will block either way
                # But we'll handle it faster in the page load
                
                # Use faster loading - domcontentloaded is faster than load
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception as e:
                    # If page doesn't exist or times out, skip it quickly
                    if "404" in str(e) or "not found" in str(e).lower() or "net::ERR" in str(e) or "timeout" in str(e).lower():
                        continue
                    # Try one more time with even shorter timeout
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    except:
                        continue
                
                # Handle Cloudflare on event pages - but be much faster
                page.wait_for_timeout(2000)  # Reduced wait
                page_title = page.title()
                if "Just a moment" in page_title or "challenge" in page_title.lower():
                    # Wait for Cloudflare but with shorter timeout
                    try:
                        # Wait only 8 seconds max - Cloudflare usually completes faster
                        page.wait_for_function(
                            "document.title !== 'Just a moment...' && !document.title.toLowerCase().includes('challenge')",
                            timeout=8000
                        )
                        page.wait_for_timeout(1000)  # Short wait after challenge
                        # Quick verify
                        if "Just a moment" in page.title():
                            continue  # Skip if still on challenge
                    except:
                        # If Cloudflare takes too long, skip immediately
                        continue
                
                # Check if page actually has content (not 404 or error page)
                html = page.content()
                if "404" in html or "not found" in html.lower() or "page not found" in html.lower():
                    continue  # Skip 404 pages
                
                rec = extract_talk_fields(html, url)
                
                # Only add if we got some content (not just a title tag)
                if rec.get("title") and len(rec.get("title", "")) > 10:  # Real title, not just "Just a moment"
                    if rec.get("title") != "Just a moment...":
                        rows.append(rec)
                
                if idx % 100 == 0:
                    print(f"  Progress: {idx}/{len(event_urls)} ({idx*100//len(event_urls)}%) - Found {len(rows)} valid events so far")
                    # Save incrementally every 100 events
                    try:
                        df_temp = pd.DataFrame(rows)
                        df_temp.to_csv("aps_summit_all_events_temp.csv", index=False)
                        if len(rows) > 0:
                            df_temp["is_superconducting_qubit"] = df_temp.apply(
                                lambda row: is_superconducting_qubit_related(
                                    str(row.get("title", "")), 
                                    str(row.get("abstract", ""))
                                ), 
                                axis=1
                            )
                            df_temp_hits = df_temp[df_temp["is_superconducting_qubit"]]
                            df_temp_hits.to_csv("aps_summit_superconducting_qubits_temp.csv", index=False)
                            print(f"    Saved {len(rows)} events ({len(df_temp_hits)} superconducting qubit related) to temp files")
                    except Exception as e:
                        print(f"    Error saving temp files: {e}")
                elif idx % 25 == 0 and len(event_urls) < 1000:
                    print(f"  Progress: {idx}/{len(event_urls)} - Found {len(rows)} valid events")
            except Exception as e:
                # Silently skip errors for generated URLs (many will be 404s)
                if "generated" not in str(url).lower():  # Only log errors for real URLs
                    print(f"  Error processing {url}: {str(e)[:100]}")
                continue
        
        context.close()
        browser.close()
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Filter for superconducting qubit related abstracts
    print("\nFiltering for superconducting qubit related abstracts...")
    df["is_superconducting_qubit"] = df.apply(
        lambda row: is_superconducting_qubit_related(
            str(row.get("title", "")), 
            str(row.get("abstract", ""))
        ), 
        axis=1
    )
    
    df_hits = df[df["is_superconducting_qubit"]].copy()
    
    # Save results
    out_all = "aps_summit_all_events.csv"
    out_hits = "aps_summit_superconducting_qubits.csv"
    
    df.to_csv(out_all, index=False)
    df_hits.to_csv(out_hits, index=False)
    
    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Total events scraped: {len(df)}")
    print(f"  Superconducting qubit related: {len(df_hits)}")
    print(f"\nFiles saved:")
    print(f"  {out_all}  (all events)")
    print(f"  {out_hits}  (superconducting qubit related only)")
    print(f"{'='*60}\n")
    
    # Print some statistics
    if len(df_hits) > 0:
        print("Sample of found abstracts:")
        for idx, row in df_hits.head(5).iterrows():
            print(f"\n  Title: {row['title'][:80]}...")
            print(f"  URL: {row['url']}")

if __name__ == "__main__":
    main()
