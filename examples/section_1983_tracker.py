#!/usr/bin/env python3
"""
Section 1983 Case Tracker
Searches CourtListener for recently filed Section 1983 civil rights cases
related to law enforcement, prisons, and jails.
"""

import requests
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
import re


class CourtListenerTracker:
    """Track Section 1983 civil rights cases on CourtListener."""
    
    BASE_URL = "https://www.courtlistener.com/api/rest/v4/search/"
    
    # Keywords that indicate law enforcement/corrections cases
    LAW_ENFORCEMENT_KEYWORDS = [
        # Law enforcement related
        'police', 'officer', 'sheriff', 'deputy', 'detective', 'trooper',
        'arrest', 'excessive force', 'unlawful detention', 'false arrest',
        'use of force', 'taser', 'shooting', 'beaten', 'assault',
        
        # Corrections related
        'prison', 'jail', 'correctional', 'detention', 'custody',
        'inmate', 'prisoner', 'incarcerated', 'pretrial detention',
        'conditions of confinement', 'deliberate indifference',
        'medical care', 'solitary confinement', 'segregation',
        
        # Officials
        'warden', 'corrections officer', 'jailer', 'guards',
        'department of corrections', 'bureau of prisons',
        
        # Common issues
        'qualified immunity', 'Fourth Amendment', 'Eighth Amendment',
        'unreasonable seizure', 'cruel and unusual punishment'
    ]
    
    def __init__(self, api_token: Optional[str] = None):
        """
        Initialize the tracker.
        
        Args:
            api_token: CourtListener API token. If not provided, will look for
                      COURTLISTENER_TOKEN environment variable.
        """
        self.api_token = api_token or os.environ.get('COURTLISTENER_TOKEN')
        if not self.api_token:
            print("Warning: No API token provided. You may hit rate limits.")
            print("Get a token at: https://www.courtlistener.com/register/")
            print("Then set COURTLISTENER_TOKEN environment variable or pass to constructor.")
        
        self.session = requests.Session()
        if self.api_token:
            self.session.headers.update({
                'Authorization': f'Token {self.api_token}'
            })
    
    def search_section_1983_cases(
        self,
        days_back: int = 30,
        max_results: int = 100,
        use_search_api: bool = True
    ) -> List[Dict]:
        """
        Search for recently filed Section 1983 cases.
        
        Args:
            days_back: How many days back to search
            max_results: Maximum number of results to return
            use_search_api: If True, use Search API (default, recommended); if False, use Dockets API
            
        Returns:
            List of case dictionaries
        """
        all_results = []
        
        print(f"\nSearching for Section 1983 cases filed in the last {days_back} days...")
        print("This may take a moment...\n")
        
        if use_search_api:
            # Use Search API with query string (DEFAULT - recommended based on results)
            all_results = self._search_via_search_api(max_results)
        else:
            # Use Dockets API with exact cause filter (alternative method)
            all_results = self._search_via_dockets_api(max_results)
        
        return all_results[:max_results]
    
    def _search_via_dockets_api(self, max_results: int) -> List[Dict]:
        """Search using the Dockets API with cause filter."""
        base_url = "https://www.courtlistener.com/api/rest/v4/dockets/"
        
        # The Dockets API supports filtering by cause
        params = {
            'cause': '42:1983 Civil Rights Act',
            'order_by': '-date_filed',  # Note: snake_case for Dockets API
        }
        
        all_results = []
        next_url = base_url
        page = 1
        
        while len(all_results) < max_results and next_url:
            try:
                if next_url == base_url:
                    response = self.session.get(next_url, params=params)
                else:
                    # For pagination, use the full next URL
                    response = self.session.get(next_url)
                
                response.raise_for_status()
                data = response.json()
                
                results = data.get('results', [])
                
                if page == 1:
                    total_count = data.get('count', 0)
                    print(f"Total matching dockets in database: {total_count}")
                    if total_count == 0:
                        print("\nNo Section 1983 cases found with cause='42:1983 Civil Rights Act'")
                        print("This could mean:")
                        print("  1. No recent cases match this exact cause string")
                        print("  2. The cause format varies by court")
                        print("  3. Your jurisdiction doesn't use this cause code format")
                        print("\nTry using the debug script to see available cases:")
                        print("  python debug_api.py")
                        break
                
                if not results:
                    break
                
                print(f"Fetched page {page}, got {len(results)} dockets...")
                page += 1
                
                # Convert to format similar to search results
                for docket in results:
                    # Normalize field names to match search API format
                    normalized = {
                        'caseName': docket.get('case_name', ''),
                        'docketNumber': docket.get('docket_number', ''),
                        'court': docket.get('court_id', ''),
                        'dateFiled': docket.get('date_filed', ''),
                        'cause': docket.get('cause', ''),
                        'absolute_url': docket.get('absolute_url', ''),
                        'docket_id': docket.get('id', ''),
                        'court_id': docket.get('court_id', ''),
                        'nature_of_suit': docket.get('nature_of_suit', ''),
                    }
                    all_results.append(normalized)
                
                # Get next page URL
                next_url = data.get('next')
                    
            except requests.exceptions.RequestException as e:
                print(f"Error fetching data: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Status code: {e.response.status_code}")
                    print(f"Response: {e.response.text[:500]}")
                break
        
        return all_results
    
    def _search_via_search_api(self, max_results: int) -> List[Dict]:
        """Search using the Search API with query string."""
        # Use Search API with a query that actually finds civil rights cases
        # Based on diagnostic results, this is the most reliable approach
        params = {
            'type': 'r',  # RECAP dockets
            'q': 'civil rights 1983',  # Broader query that catches various cause formats
            'order_by': 'dateFiled desc',
        }
        
        all_results = []
        cursor = None
        
        while len(all_results) < max_results:
            if cursor:
                params['cursor'] = cursor
            
            try:
                response = self.session.get(self.BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()
                
                results = data.get('results', [])
                if not results:
                    break
                
                all_results.extend(results)
                
                # Check if there's a next page
                cursor = self._extract_cursor_from_url(data.get('next'))
                if not cursor:
                    break
                    
            except requests.exceptions.RequestException as e:
                print(f"Error fetching data: {e}")
                break
        
        return all_results
    
    def _extract_cursor_from_url(self, url: Optional[str]) -> Optional[str]:
        """Extract cursor parameter from pagination URL."""
        if not url:
            return None
        match = re.search(r'cursor=([^&]+)', url)
        return match.group(1) if match else None
    
    def is_relevant_case(self, case: Dict) -> bool:
        """
        Check if a case is related to law enforcement/corrections.
        
        Args:
            case: Case dictionary from API
            
        Returns:
            True if case appears related to law enforcement/corrections
        """
        # Combine all searchable text fields
        # Handle both Search API and Dockets API field names
        searchable_text = ' '.join([
            str(case.get('caseName', case.get('case_name', ''))),
            str(case.get('docketNumber', case.get('docket_number', ''))),
            str(case.get('cause', '')),
            str(case.get('nature_of_suit', case.get('suitNature', ''))),
            str(case.get('snippet', '')),
        ]).lower()
        
        # Check for keywords
        for keyword in self.LAW_ENFORCEMENT_KEYWORDS:
            if keyword.lower() in searchable_text:
                return True
        
        return False
    
    def format_case_output(self, case: Dict, include_snippet: bool = True) -> str:
        """
        Format a case for display.
        
        Args:
            case: Case dictionary
            include_snippet: Whether to include text snippet
            
        Returns:
            Formatted string
        """
        output = []
        output.append(f"\n{'='*80}")
        output.append(f"Case Name: {case.get('caseName', 'N/A')}")
        output.append(f"Docket Number: {case.get('docketNumber', 'N/A')}")
        output.append(f"Court: {case.get('court', 'N/A')}")
        output.append(f"Date Filed: {case.get('dateFiled', 'N/A')}")
        output.append(f"Cause: {case.get('cause', 'N/A')}")
        
        # Extract parties if available
        if 'snippet' in case and case['snippet']:
            output.append(f"\nSnippet: {case['snippet'][:500]}...")
        
        # URL
        if 'absolute_url' in case:
            output.append(f"\nURL: https://www.courtlistener.com{case['absolute_url']}")
        
        output.append('='*80)
        
        return '\n'.join(output)
    
    def save_results_to_json(self, cases: List[Dict], filename: str):
        """Save results to JSON file."""
        with open(filename, 'w') as f:
            json.dump({
                'generated_at': datetime.now().isoformat(),
                'total_cases': len(cases),
                'cases': cases
            }, f, indent=2)
        print(f"\nResults saved to: {filename}")
    
    def run_search_and_filter(
        self,
        days_back: int = 30,
        max_results: int = 100,
        output_file: Optional[str] = None,
        use_search_api: bool = False
    ):
        """
        Run a complete search and filter operation.
        
        Args:
            days_back: Days to look back
            max_results: Maximum results to fetch
            output_file: Optional JSON file to save results
            use_search_api: If True, use Search API; if False, use Dockets API
        """
        # Search for cases
        all_cases = self.search_section_1983_cases(
            days_back=days_back,
            max_results=max_results,
            use_search_api=use_search_api
        )
        
        print(f"Found {len(all_cases)} Section 1983 cases total.\n")
        print("Filtering for law enforcement/corrections cases...\n")
        
        # Filter for relevant cases
        relevant_cases = [case for case in all_cases if self.is_relevant_case(case)]
        
        print(f"{'='*80}")
        print(f"FILTERED RESULTS: {len(relevant_cases)} relevant cases found")
        print(f"{'='*80}")
        
        # Display results
        for i, case in enumerate(relevant_cases, 1):
            print(f"\n[{i}/{len(relevant_cases)}]")
            print(self.format_case_output(case))
        
        # Save to file if requested
        if output_file:
            self.save_results_to_json(relevant_cases, output_file)
        
        return relevant_cases


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Search CourtListener for Section 1983 cases related to law enforcement/corrections'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to look back (default: 30)'
    )
    parser.add_argument(
        '--max-results',
        type=int,
        default=100,
        help='Maximum number of results to fetch (default: 100)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Output JSON file path (optional)'
    )
    parser.add_argument(
        '--token',
        type=str,
        help='CourtListener API token (or set COURTLISTENER_TOKEN env var)'
    )
    parser.add_argument(
        '--use-dockets-api',
        action='store_true',
        help='Use Dockets API instead of Search API (default: Search API)'
    )
    
    args = parser.parse_args()
    
    # Create tracker
    tracker = CourtListenerTracker(api_token=args.token)
    
    # Show which API method we're using
    api_method = "Dockets API" if args.use_dockets_api else "Search API (recommended)"
    print(f"Using: {api_method}")
    
    # Run search
    tracker.run_search_and_filter(
        days_back=args.days,
        max_results=args.max_results,
        output_file=args.output,
        use_search_api=not args.use_dockets_api  # Invert since default is now Search API
    )


if __name__ == '__main__':
    main()
