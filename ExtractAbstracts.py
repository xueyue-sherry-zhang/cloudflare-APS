#!/usr/bin/env python3
"""
Extract abstracts from APS Summit event URLs using tqdm for progress tracking.
Reads URLs from event_urls.txt and processes them one by one.
"""

import re
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from tqdm import tqdm
import json

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

def is_superconducting_qubit_related(title, abstract):
    """Check if title or abstract mentions superconducting qubit related terms."""
    combined_text = f"{title} {abstract}".lower()
    
    pattern_parts = []
    for keyword in SUPERCONDUCTING_QUBIT_KEYWORDS:
        escaped = re.escape(keyword)
        pattern_parts.append(escaped)
    
    pattern = re.compile("|".join(pattern_parts), re.IGNORECASE)
    return bool(pattern.search(combined_text))

def load_urls(filename="event_urls.txt"):
    """Load URLs from file."""
    try:
        with open(filename, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        return urls
    except FileNotFoundError:
        print(f"Error: {filename} not found!")
        return []

def main():
    # Load URLs
    print("Loading URLs from event_urls.txt...")
    urls = load_urls()
    if not urls:
        print("No URLs found. Exiting.")
        return
    
    print(f"Loaded {len(urls)} URLs to process")
    
    rows = []
    HEADLESS = True
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.set_default_timeout(60000)
        page = context.new_page()
        
        # Process URLs with tqdm progress bar
        print("\nProcessing URLs...")
        for url in tqdm(urls, desc="Extracting abstracts", unit="URL"):
            try:
                # Add small delay to avoid Cloudflare rate limiting
                if len(rows) > 0 and len(rows) % 10 == 0:
                    page.wait_for_timeout(2000)  # Longer delay every 10 requests
                elif len(rows) > 0:
                    page.wait_for_timeout(500)   # Small delay between requests
                
                # Load page
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception as e:
                    if "404" in str(e) or "not found" in str(e).lower() or "timeout" in str(e).lower():
                        continue
                    raise
                
                # Handle Cloudflare
                page.wait_for_timeout(2000)
                page_title = page.title()
                if "Just a moment" in page_title or "challenge" in page_title.lower():
                    try:
                        page.wait_for_function(
                            "document.title !== 'Just a moment...' && !document.title.toLowerCase().includes('challenge')",
                            timeout=8000
                        )
                        page.wait_for_timeout(1000)
                        if "Just a moment" in page.title():
                            continue
                    except:
                        continue
                
                # Check if page has content
                html = page.content()
                if "404" in html or "not found" in html.lower() or "page not found" in html.lower():
                    continue
                
                # Extract data
                rec = extract_talk_fields(html, url)
                
                # Only add if we got real content
                if rec.get("title") and len(rec.get("title", "")) > 10:
                    if rec.get("title") != "Just a moment...":
                        rows.append(rec)
                
                # Save incrementally every 100 events
                if len(rows) > 0 and len(rows) % 100 == 0:
                    df_temp = pd.DataFrame(rows)
                    df_temp.to_csv("aps_summit_all_events_temp.csv", index=False)
                    
                    df_temp["is_superconducting_qubit"] = df_temp.apply(
                        lambda row: is_superconducting_qubit_related(
                            str(row.get("title", "")), 
                            str(row.get("abstract", ""))
                        ), 
                        axis=1
                    )
                    df_temp_hits = df_temp[df_temp["is_superconducting_qubit"]]
                    df_temp_hits.to_csv("aps_summit_superconducting_qubits_temp.csv", index=False)
                    
                    tqdm.write(f"  Saved {len(rows)} events ({len(df_temp_hits)} superconducting qubit related)")
                    
            except Exception as e:
                # Silently continue on errors
                continue
        
        context.close()
        browser.close()
    
    # Final save
    print(f"\nProcessing complete! Found {len(rows)} valid events.")
    
    df = pd.DataFrame(rows)
    df["is_superconducting_qubit"] = df.apply(
        lambda row: is_superconducting_qubit_related(
            str(row.get("title", "")), 
            str(row.get("abstract", ""))
        ), 
        axis=1
    )
    
    df_hits = df[df["is_superconducting_qubit"]].copy()
    
    # Save final results
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

if __name__ == "__main__":
    main()

