#!/usr/bin/env python3
"""Basic analysis example using the AbhiAI Analytics Engine."""

import sys
from pathlib import Path

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from abhiai_analytics import DataManager


def main():
    # Path to the sample dataset
    data_path = Path(__file__).parent / "sample_sales.csv"
    
    print("=" * 60)
    print("AbhiAI Analytics Engine - Basic Analysis Example")
    print("=" * 60)
    
    # Create a DataManager and load the dataset
    manager = DataManager()
    
    print(f"\nLoading dataset: {data_path}")
    result = manager.open(data_path)
    
    if not result.success:
        print(f"Error loading dataset: {result.error}")
        return 1
    
    print(f"Dataset loaded successfully!")
    print(f"  File: {result.info.path.name}")
    print(f"  Type: {result.info.file_type}")
    print(f"  Rows: {result.info.shapes[result.info.active_sheet][0]}")
    print(f"  Columns: {result.info.shapes[result.info.active_sheet][1]}")
    print(f"  Backend: legacy (pandas)")
    
    # Generate business insights (Phase 5A)
    print("\n" + "=" * 60)
    print("Generating Business Insights (Phase 5A)")
    print("=" * 60)
    
    insights = manager.generate_business_insights(granularity="monthly")
    
    print(f"\nDataset ID: {insights.dataset_id}")
    print(f"Backend: {insights.backend}")
    print(f"Currency: {insights.currency or 'Unknown'}")
    print(f"\nInsights generated: {len(insights.insights)}")
    print(f"Limitations: {len(insights.limitations)}")
    
    # Print KPI insights
    print("\n--- Key Performance Indicators ---")
    for insight in insights.insights:
        if insight.category.value == "kpi":
            print(f"  {insight.metric.title()}: {insight.current_value:,.2f}")
    
    # Print comparison insights
    print("\n--- Period Comparisons ---")
    for insight in insights.insights:
        if insight.category.value == "comparison":
            print(f"  {insight.summary}")
    
    # Print trend insights
    print("\n--- Trends ---")
    for insight in insights.insights:
        if insight.category.value == "trend":
            print(f"  {insight.summary}")
    
    # Print dimension insights
    print("\n--- Top Performers by Dimension ---")
    for insight in insights.insights:
        if insight.category.value == "dimension":
            print(f"  {insight.summary}")
    
    # Print contribution insights
    print("\n--- Contribution Analysis ---")
    for insight in insights.insights:
        if insight.category.value == "contribution":
            print(f"  {insight.summary}")
    
    # Print profitability insights
    print("\n--- Profitability ---")
    for insight in insights.insights:
        if insight.category.value == "profitability":
            print(f"  {insight.summary}")
    
    # Print limitations
    print("\n--- Limitations ---")
    for lim in insights.limitations:
        print(f"  - {lim.code}: {lim.message}")
    
    print("\n" + "=" * 60)
    print("Basic analysis complete!")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())