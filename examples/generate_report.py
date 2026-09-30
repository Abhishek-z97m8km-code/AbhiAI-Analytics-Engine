#!/usr/bin/env python3
"""Structured report generation example using the AbhiAI Analytics Engine."""

import sys
import json
from pathlib import Path

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from abhiai_analytics import DataManager


def main():
    # Path to the sample dataset
    data_path = Path(__file__).parent / "sample_sales.csv"
    
    print("=" * 60)
    print("AbhiAI Analytics Engine - Structured Report Generation")
    print("=" * 60)
    
    # Create a DataManager and load the dataset
    manager = DataManager()
    
    print(f"\nLoading dataset: {data_path}")
    result = manager.open(data_path)
    
    if not result.success:
        print(f"Error loading dataset: {result.error}")
        return 1
    
    print(f"Dataset loaded successfully! Backend: legacy (pandas)")
    
    # Generate different report types
    report_types = [
        ("business_overview", "standard"),
        ("sales_performance", "detailed"),
        ("financial_performance", "executive"),
    ]
    
    for report_type, detail_level in report_types:
        print(f"\n{'=' * 60}")
        print(f"Generating {report_type.replace('_', ' ').title()} Report ({detail_level})")
        print(f"{'=' * 60}")
        
        report = manager.generate_business_report(
            report_type=report_type,
            detail_level=detail_level,
            language="english"
        )
        
        print(f"\nReport ID: {report.report_id}")
        print(f"Title: {report.title}")
        print(f"Type: {report.report_type.value}")
        print(f"Status: {report.status.value}")
        print(f"Generated: {report.generated_at}")
        print(f"Dataset: {report.dataset.filename} ({report.dataset.row_count:,} rows)")
        print(f"Backend: {report.dataset.backend}")
        print(f"Detail Level: {report.detail_level.value}")
        print(f"Language: {report.language.value}")
        print(f"Generation Method: {report.generation_method.value}")
        print(f"Faithfulness: {report.faithfulness_status.value}")
        
        # Print KPIs
        print(f"\n--- Key KPIs ---")
        for kpi in report.kpis:
            currency_str = f" {kpi.currency}" if kpi.currency else ""
            print(f"  {kpi.metric.title()}: {kpi.formatted_value}{currency_str}")
        
        # Print Executive Summary
        print(f"\n--- Executive Summary ---")
        print(f"  {report.executive_summary}")
        
        # Print Sections
        print(f"\n--- Sections ({len(report.sections)}) ---")
        for section in report.sections:
            print(f"  [{section.section_type.value}] {section.title}")
            print(f"    {section.narrative[:200]}{'...' if len(section.narrative) > 200 else ''}")
        
        # Print Key Findings
        print(f"\n--- Key Findings ({len(report.key_findings)}) ---")
        for finding in report.key_findings:
            print(f"  [{finding.importance.upper()}] {finding.title}: {finding.text[:150]}{'...' if len(finding.text) > 150 else ''}")
        
        # Print Chart Specs
        print(f"\n--- Chart Specifications ({len(report.chart_specs)}) ---")
        for chart in report.chart_specs:
            print(f"  [{chart.chart_type.value}] {chart.title} ({chart.metric})")
            print(f"    Data points: {len(chart.data)}")
        
        # Print Limitations
        print(f"\n--- Limitations ({len(report.limitations)}) ---")
        for lim in report.limitations:
            print(f"  - {lim.code}: {lim.message}")
        
        # Print Metadata
        print(f"\n--- Generation Metadata ---")
        for key, value in report.generation_metadata.items():
            if key not in ["validation_errors", "valid"]:
                print(f"  {key}: {value}")
        if "valid" in report.generation_metadata:
            print(f"  Validation: {'✅ Valid' if report.generation_metadata['valid'] else '❌ Invalid'}")
            if report.generation_metadata.get("validation_errors"):
                for err in report.generation_metadata["validation_errors"]:
                    print(f"    Error: {err}")
    
    # Demonstrate serialization
    print(f"\n{'=' * 60}")
    print("Report Serialization Demo")
    print(f"{'=' * 60}")
    
    report = manager.generate_business_report(report_type="business_overview", detail_level="standard")
    
    # Serialize to JSON
    report_json = report.to_dict()
    json_str = json.dumps(report_json, indent=2, default=str)
    
    print(f"\nSerialized report size: {len(json_str):,} characters")
    print(f"Report ID preserved: {report_json['report_id'] == report.report_id}")
    
    # Deserialize
    from abhiai_analytics.intelligence.report_models import BusinessReport
    restored = BusinessReport.from_dict(report_json)
    print(f"Deserialized successfully: {restored.report_id == report.report_id}")
    print(f"All sections preserved: {len(restored.sections) == len(report.sections)}")
    print(f"All findings preserved: {len(restored.key_findings) == len(report.key_findings)}")
    print(f"All charts preserved: {len(restored.chart_specs) == len(report.chart_specs)}")
    
    print("\n" + "=" * 60)
    print("Report generation example complete!")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())