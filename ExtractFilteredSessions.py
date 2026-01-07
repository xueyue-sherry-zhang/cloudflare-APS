#!/usr/bin/env python3
"""
Extract session URLs from a filtered schedule page.
"""

from playwright.sync_api import sync_playwright
import re
import time

HEADLESS = True

def extract_session_urls_from_page(page, url):
    """Extract all session URLs from the filtered schedule page."""
    print(f"Fetching filtered schedule page: {url}")
    
    # Track network requests to find API endpoints
    api_responses = []
    
    def handle_response(response):
        """Capture API responses."""
        url_str = response.url
        if any(keyword in url_str.lower() for keyword in ['api', 'schedule', 'event', 'session', 'data', 'json']):
            try:
                api_responses.append({
                    'url': url_str,
                    'status': response.status,
                    'headers': response.headers
                })
            except:
                pass
    
    page.on("response", handle_response)
    
    try:
        # Navigate to the page
        response = page.goto(url, wait_until="load", timeout=60000)
        
        if response.status != 200:
            print(f"Warning: Got status {response.status}")
        
        # Wait for Cloudflare challenge
        print("Waiting for page to load...")
        page.wait_for_timeout(5000)
        
        # Check for Cloudflare challenge
        content_check = page.content()
        if "Just a moment" in content_check or "Checking your browser" in content_check:
            print("Cloudflare challenge detected, waiting...")
            try:
                page.wait_for_function(
                    "document.body.innerText.indexOf('Just a moment') === -1 && document.body.innerText.indexOf('Checking your browser') === -1",
                    timeout=45000
                )
                page.wait_for_timeout(3000)
            except:
                print("Cloudflare challenge timeout, continuing anyway...")
        
        # Wait for schedule content to appear
        print("Waiting for schedule content...")
        try:
            # Wait for any session-related elements
            page.wait_for_selector("a[href*='events'], [href*='MAR-'], .session, .event", timeout=30000)
        except:
            print("No session selectors found, trying to continue...")
        
        page.wait_for_timeout(3000)
        
        # Scroll to load all content (lazy loading)
        print("Scrolling to load all sessions...")
        last_height = page.evaluate("document.body.scrollHeight")
        scroll_attempts = 0
        max_scrolls = 30
        
        while scroll_attempts < max_scrolls:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                # Try scrolling back up and down to trigger lazy loading
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
            last_height = new_height
            scroll_attempts += 1
            print(f"  Scroll {scroll_attempts}/{max_scrolls}, height: {new_height}")
        
        # Extract session URLs using multiple methods
        session_urls = set()
        
        print("Extracting session URLs...")
        
        # Get page content for analysis
        page_content = page.content()
        
        # Save page content for debugging
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(page_content)
        print("Saved page content to debug_page.html for inspection")
        
        # Method 1: Extract all links using JavaScript
        print("Method 1: Extracting all links...")
        links = page.evaluate("""
            () => {
                const links = [];
                // Get all anchor tags
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href || a.getAttribute('href');
                    if (href) {
                        links.push(href);
                    }
                });
                // Also check for data attributes and onclick handlers
                document.querySelectorAll('[data-href], [onclick]').forEach(el => {
                    const href = el.getAttribute('data-href') || el.getAttribute('href');
                    if (href) {
                        links.push(href);
                    }
                });
                return [...new Set(links)];
            }
        """)
        
        print(f"Found {len(links)} total links")
        
        # Filter for session URLs - multiple patterns
        patterns = [
            re.compile(r'https://summit\.aps\.org/events/(MAR-[A-Z]\d{2})'),
            re.compile(r'/events/(MAR-[A-Z]\d{2})'),
            re.compile(r'events/(MAR-[A-Z]\d{2})'),
        ]
        
        for link in links:
            for pattern in patterns:
                match = pattern.search(link)
                if match:
                    session_id = match.group(1)
                    session_url = f"https://summit.aps.org/events/{session_id}"
                    session_urls.add(session_url)
                    break
        
        # Method 2: Look for session IDs in page content (HTML and text)
        print("Method 2: Searching page content...")
        for pattern in patterns:
            matches = pattern.findall(page_content)
            for session_id in matches:
                session_url = f"https://summit.aps.org/events/{session_id}"
                session_urls.add(session_url)
        
        # Method 3: Extract from text content
        print("Method 3: Extracting from text content...")
        try:
            text_content = page.evaluate("document.body.innerText")
            for pattern in patterns:
                matches = pattern.findall(text_content)
                for session_id in matches:
                    session_url = f"https://summit.aps.org/events/{session_id}"
                    session_urls.add(session_url)
        except Exception as e:
            print(f"Method 3 error: {e}")
        
        # Method 4: Look for session elements with various selectors
        print("Method 4: Using CSS selectors...")
        try:
            session_elements = page.evaluate("""
                () => {
                    const sessions = [];
                    // Try various selectors
                    const selectors = [
                        'a[href*="/events/MAR-"]',
                        'a[href*="MAR-"]',
                        '[href*="/events/MAR-"]',
                        '[data-session-id]',
                        '[data-event-id]',
                        '.session a',
                        '.event a',
                        '[class*="session"] a',
                        '[class*="event"] a'
                    ];
                    
                    selectors.forEach(sel => {
                        try {
                            document.querySelectorAll(sel).forEach(el => {
                                const href = el.href || el.getAttribute('href') || el.getAttribute('data-href');
                                if (href) {
                                    sessions.push(href.startsWith('http') ? href : 'https://summit.aps.org' + href);
                                }
                            });
                        } catch(e) {}
                    });
                    
                    return [...new Set(sessions)];
                }
            """)
            
            for url in session_elements:
                for pattern in patterns:
                    match = pattern.search(url)
                    if match:
                        session_id = match.group(1)
                        session_url = f"https://summit.aps.org/events/{session_id}"
                        session_urls.add(session_url)
                        break
        except Exception as e:
            print(f"Method 4 error: {e}")
        
        # Method 5: Look for session IDs in any attribute
        print("Method 5: Searching all attributes...")
        try:
            all_attrs = page.evaluate("""
                () => {
                    const attrs = [];
                    document.querySelectorAll('*').forEach(el => {
                        Array.from(el.attributes).forEach(attr => {
                            attrs.push(attr.value);
                        });
                    });
                    return attrs;
                }
            """)
            
            session_pattern = re.compile(r'MAR-[A-Z]\d{2}')
            for attr_value in all_attrs:
                matches = session_pattern.findall(attr_value)
                for session_id in matches:
                    session_url = f"https://summit.aps.org/events/{session_id}"
                    session_urls.add(session_url)
        except Exception as e:
            print(f"Method 5 error: {e}")
        
        # Method 6: Check API responses
        print(f"Method 6: Checking {len(api_responses)} API responses...")
        for api_resp in api_responses:
            print(f"  Found API: {api_resp['url']} (status: {api_resp['status']})")
            try:
                # Try to get response body
                resp = page.request.get(api_resp['url'], timeout=10000)
                if resp.status == 200:
                    try:
                        body = resp.text()
                        # Look for session IDs in JSON
                        for pattern in patterns:
                            matches = pattern.findall(body)
                            for session_id in matches:
                                session_url = f"https://summit.aps.org/events/{session_id}"
                                session_urls.add(session_url)
                    except:
                        pass
            except:
                pass
        
        # Method 7: Try to extract from JavaScript variables/state
        print("Method 7: Extracting from JavaScript state...")
        try:
            js_state = page.evaluate("""
                () => {
                    const state = {};
                    // Try to find session data in window object
                    if (window.__INITIAL_STATE__) state.initial = JSON.stringify(window.__INITIAL_STATE__);
                    if (window.__APOLLO_STATE__) state.apollo = JSON.stringify(window.__APOLLO_STATE__);
                    if (window.__NEXT_DATA__) state.next = JSON.stringify(window.__NEXT_DATA__);
                    // Try React/Vue state
                    if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
                        try {
                            const reactRoots = window.__REACT_DEVTOOLS_GLOBAL_HOOK__.renderers;
                            if (reactRoots) state.react = 'found';
                        } catch(e) {}
                    }
                    // Get all script tags content
                    const scripts = Array.from(document.querySelectorAll('script')).map(s => s.textContent).join('\\n');
                    state.scripts = scripts.substring(0, 50000); // Limit size
                    return state;
                }
            """)
            
            for key, value in js_state.items():
                if value:
                    for pattern in patterns:
                        matches = pattern.findall(value)
                        for session_id in matches:
                            session_url = f"https://summit.aps.org/events/{session_id}"
                            session_urls.add(session_url)
        except Exception as e:
            print(f"Method 7 error: {e}")
        
        sorted_urls = sorted(session_urls)
        print(f"\n✓ Extracted {len(sorted_urls)} unique session URLs")
        
        return sorted_urls
        
    except Exception as e:
        print(f"Error extracting sessions: {e}")
        import traceback
        traceback.print_exc()
        return []


def main():
    filtered_url = "https://summit.aps.org/schedule/?c=eyJldCI6Ikludml0ZWQgU2Vzc2lvbnxGb2N1c8SOxJDEksSUfEPElEdyaWJ1xIvEjcSPxJHEk24ifQ"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.set_default_timeout(60000)
        page = context.new_page()
        
        session_urls = extract_session_urls_from_page(page, filtered_url)
        
        if session_urls:
            # Save to event_urls.txt
            output_file = "event_urls.txt"
            with open(output_file, 'w') as f:
                for url in session_urls:
                    f.write(url + '\n')
            
            print(f"\n✓ Saved {len(session_urls)} session URLs to {output_file}")
            print(f"\nFirst 10 URLs:")
            for url in session_urls[:10]:
                print(f"  {url}")
            if len(session_urls) > 10:
                print(f"\nLast 10 URLs:")
                for url in session_urls[-10:]:
                    print(f"  {url}")
        else:
            print("\n✗ No session URLs found!")
            print("Page content preview:")
            try:
                content = page.content()[:2000]
                print(content)
            except:
                pass
        
        browser.close()


if __name__ == "__main__":
    main()

