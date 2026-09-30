#!/usr/bin/env python3
"""Business questions example using the AbhiAI Analytics Engine."""

import sys
from pathlib import Path

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from abhiai_analytics import DataManager


def main():
    # Path to the sample dataset
    data_path = Path(__file__).parent / "sample_sales.csv"
    
    print("=" * 60)
    print("AbhiAI Analytics Engine - Business Questions Example")
    print("=" * 60)
    
    # Create a DataManager and load the dataset
    manager = DataManager()
    
    print(f"\nLoading dataset: {data_path}")
    result = manager.open(data_path)
    
    if not result.success:
        print(f"Error loading dataset: {result.error}")
        return 1
    
    print(f"Dataset loaded successfully! Backend: legacy (pandas)")
    
    # Example business questions
    questions = [
        "What is our total revenue?",
        "What is our total profit?",
        "What is our profit margin?",
        "How many units were sold?",
        "Which region generated the highest revenue?",
        "Which region generated the highest profit?",
        "Which product has the best margin?",
        "How is revenue changing over time?",
        "How is profit trending?",
        "Compare revenue and cost",
        "Did costs increase faster than revenue?",
        "Which region contributed most to revenue?",
        "Why did profit change?",
        "Are there unusual regional revenue values?",
        "Explain the business performance simply.",
    ]
    
    print("\n" + "=" * 60)
    print("Asking Business Questions (Phase 5B/5C)")
    print("=" * 60)
    
    for question in questions:
        print(f"\n📝 Question: {question}")
        
        # Ask the business question (Phase 5B)
        result = manager.ask_business_question(question)
        
        if result.status.value == "answered":
            print(f"   ✅ Status: {result.status.value}")
            print(f"   🔧 Operation: {result.intent.operation.value}")
            print(f"   📊 Backend: {result.backend}")
            
            # Show facts
            for fact in result.facts:
                if fact.percentage is not None:
                    print(f"   📈 {fact.label}: {fact.value:,.2f} ({fact.percentage:.2f}%)")
                elif isinstance(fact.value, (int, float)):
                    print(f"   📈 {fact.label}: {fact.value:,.2f}")
                else:
                    print(f"   📈 {fact.label}: {fact.value}")
            
            # Get grounded explanation (Phase 5C)
            response = manager.explain_business_question(question, mode="standard")
            print(f"   💬 Answer: {response.answer}")
            
            if response.generation_method.value != "deterministic":
                print(f"   🤖 Method: {response.generation_method.value}")
                print(f"   ✓ Faithfulness: {response.faithfulness_status.value}")
        else:
            print(f"   ❌ Status: {result.status.value}")
            if result.suggested_clarification:
                print(f"   💡 Clarification needed: {result.suggested_clarification}")
            if result.limitations:
                for lim in result.limitations:
                    print(f"   ⚠️  Limitation: {lim.code} - {lim.message}")
    
    print("\n" + "=" * 60)
    print("Business questions example complete!")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())