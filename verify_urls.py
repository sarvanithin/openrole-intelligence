#!/usr/bin/env python3
"""
Verify which company career URLs are working.
Uses requests library for simple HTTP HEAD/GET checks.
"""
import csv
import sys
import time
from typing import List, Tuple
from urllib.parse import urlparse

import requests


def verify_url(url: str, timeout: int = 10) -> Tuple[str, bool, str]:
    """
    Verify if a URL is accessible.
    
    Returns:
        Tuple of (url, is_working, status_message)
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        # First try HEAD request (faster)
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        
        if response.status_code == 405:  # Method not allowed, try GET
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        
        if response.ok:
            return (url, True, f"OK - HTTP {response.status_code}")
        else:
            return (url, False, f"Error - HTTP {response.status_code}")
            
    except requests.exceptions.Timeout:
        return (url, False, "Error - Timeout")
    except requests.exceptions.ConnectionError:
        return (url, False, "Error - Connection failed")
    except requests.exceptions.SSLError:
        return (url, False, "Error - SSL error")
    except Exception as e:
        error_msg = str(e)[:50]
        return (url, False, f"Error - {error_msg}")


def verify_companies_csv(input_csv: str, output_csv: str):
    """Verify all URLs in a companies CSV and output results."""
    
    # Read input CSV
    companies = []
    with open(input_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            companies.append(row)
    
    print(f"Verifying {len(companies)} URLs...")
    print("=" * 60)
    
    results = []
    working_count = 0
    
    for i, company in enumerate(companies):
        name = company.get('Company Name', '')
        url = company.get('Career Search URL', '')
        platform = company.get('Platform Type', '')
        
        if not url:
            continue
        
        print(f"  [{i+1:3d}/{len(companies)}] {name[:30]:30s}", end=' ', flush=True)
        
        _, is_working, status = verify_url(url)
        
        if is_working:
            working_count += 1
            print("✓ Working")
        else:
            print(f"✗ {status}")
        
        results.append({
            'Company Name': name,
            'Career Search URL': url,
            'Platform Type': platform,
            'Status': 'Working' if is_working else 'Not Working',
            'Details': status,
        })
        
        # Small delay to be respectful
        time.sleep(0.2)
    
    # Write output CSV with all results
    print("\n" + "=" * 60)
    with open(output_csv, 'w', newline='') as f:
        fieldnames = ['Company Name', 'Career Search URL', 'Platform Type', 'Status', 'Details']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"=== SUMMARY ===")
    print(f"Total: {len(results)}")
    print(f"Working: {working_count}")
    print(f"Not Working: {len(results) - working_count}")
    print(f"\nFull results: {output_csv}")
    
    # Also output a verified-only CSV (for direct import)
    verified_csv = output_csv.replace('.csv', '_verified_only.csv')
    with open(verified_csv, 'w', newline='') as f:
        fieldnames = ['Company Name', 'Career Search URL', 'Platform Type']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            if r['Status'] == 'Working':
                writer.writerow({
                    'Company Name': r['Company Name'],
                    'Career Search URL': r['Career Search URL'],
                    'Platform Type': r['Platform Type'],
                })
    
    print(f"Verified URLs only: {verified_csv}")
    print("=" * 60)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python verify_urls.py <input.csv> [output.csv]")
        print("Example: python verify_urls.py companies_h1b_250.csv companies_verified.csv")
        sys.exit(1)
    
    input_csv = sys.argv[1]
    output_csv = sys.argv[2] if len(sys.argv) > 2 else 'companies_verification_results.csv'
    
    verify_companies_csv(input_csv, output_csv)
