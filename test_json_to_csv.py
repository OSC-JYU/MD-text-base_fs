import os
import tempfile
import json
import csv
os.environ.setdefault("MD_PATH", os.getcwd())

from api import json_to_csv_custom

def test_json_to_csv_append():
    """Test the append functionality of json_to_csv_custom function"""
    
    # Create a temporary directory for test files
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, "test_output.csv")
        
        # Test data - first batch
        data1 = [
            {"name": "John", "age": 30, "city": "New York"},
            {"name": "Jane", "age": 25, "city": "Boston"}
        ]
        
        # Test data - second batch (with some different fields)
        data2 = [
            {"name": "Bob", "age": 35, "city": "Chicago", "country": "USA"},
            {"name": "Alice", "age": 28, "city": "Seattle"}
        ]
        
        # Test data - third batch (with completely different structure)
        data3 = [
            {"title": "Book1", "author": "Author1", "pages": 200},
            {"title": "Book2", "author": "Author2", "pages": 150}
        ]
        
        print("=== Testing JSON to CSV Append Functionality ===\n")
        
        # Test 1: Create initial CSV file
        print("Test 1: Creating initial CSV file...")
        result1 = json_to_csv_custom(data1, append=False, output_path=output_path)
        print(f"Result 1: {result1[:100]}...")
        
        # Verify first file
        with open(output_path, 'r', encoding='utf-8') as f:
            content1 = f.read()
            print(f"File content after first write:\n{content1}")
        
        # Test 2: Append second batch
        print("\nTest 2: Appending second batch...")
        result2 = json_to_csv_custom(data2, append=True, output_path=output_path)
        print(f"Result 2: {result2}")
        
        # Verify appended content
        with open(output_path, 'r', encoding='utf-8') as f:
            content2 = f.read()
            print(f"File content after second append:\n{content2}")
        
        # Test 3: Append third batch (different structure)
        print("\nTest 3: Appending third batch with different structure...")
        result3 = json_to_csv_custom(data3, append=True, output_path=output_path)
        print(f"Result 3: {result3}")
        
        # Verify final content
        with open(output_path, 'r', encoding='utf-8') as f:
            content3 = f.read()
            print(f"Final file content:\n{content3}")
        
        # Test 4: Verify CSV structure
        print("\nTest 4: Verifying CSV structure...")
        with open(output_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            rows = list(reader)
            print(f"Number of data rows: {len(rows)}")
            print(f"Headers: {reader.fieldnames}")
            
            # Check that all rows have the same structure
            for i, row in enumerate(rows):
                print(f"Row {i+1}: {row}")
        
        # Test 5: Test with nested JSON structure
        print("\nTest 5: Testing with nested JSON structure...")
        nested_data = {
            "books": [
                {"title": "Nested Book1", "author": {"name": "Author1", "age": 40}},
                {"title": "Nested Book2", "author": {"name": "Author2", "age": 35}}
            ]
        }
        
        result4 = json_to_csv_custom(nested_data, append=True, output_path=output_path)
        print(f"Result 4: {result4}")
        
        # Verify nested content
        with open(output_path, 'r', encoding='utf-8') as f:
            content4 = f.read()
            print(f"File content after nested append:\n{content4}")
        
        # Test 6: Test many-to-one scenario (like in the API)
        print("\nTest 6: Testing many-to-one scenario...")
        
        # Simulate the API's many-to-one processing
        many_to_one_data = [
            {"id": 1, "type": "document", "status": "processed"},
            {"id": 2, "type": "image", "status": "pending"}
        ]
        
        # First file (current_file = 1)
        result5 = json_to_csv_custom(many_to_one_data, append=False, output_path=output_path)
        print(f"First file result: {result5[:100]}...")
        
        # Second file (current_file = 2)
        more_data = [
            {"id": 3, "type": "pdf", "status": "completed"},
            {"id": 4, "type": "text", "status": "failed"}
        ]
        
        result6 = json_to_csv_custom(more_data, append=True, output_path=output_path)
        print(f"Second file result: {result6}")
        
        # Verify many-to-one result
        with open(output_path, 'r', encoding='utf-8') as f:
            content5 = f.read()
            print(f"Many-to-one final content:\n{content5}")
        
        print("\n=== All tests completed ===")

def test_edge_cases():
    """Test edge cases for the append functionality"""
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, "edge_test.csv")
        
        print("\n=== Testing Edge Cases ===\n")
        
        # Test 1: Empty data
        print("Test 1: Empty data...")
        result1 = json_to_csv_custom([], append=False, output_path=output_path)
        print(f"Empty data result: '{result1}'")
        
        # Test 2: Single item
        print("\nTest 2: Single item...")
        single_item = [{"test": "value"}]
        result2 = json_to_csv_custom(single_item, append=False, output_path=output_path)
        print(f"Single item result: {result2}")
        
        # Test 3: Append to non-existent file
        print("\nTest 3: Append to non-existent file...")
        non_existent_path = os.path.join(temp_dir, "non_existent.csv")
        result3 = json_to_csv_custom([{"new": "data"}], append=True, output_path=non_existent_path)
        print(f"Append to non-existent result: '{result3}'")
        
        # Test 4: Different field orders
        print("\nTest 4: Different field orders...")
        data1 = [{"a": 1, "b": 2, "c": 3}]
        data2 = [{"c": 4, "a": 5, "b": 6}]  # Different order
        
        json_to_csv_custom(data1, append=False, output_path=output_path)
        result4 = json_to_csv_custom(data2, append=True, output_path=output_path)
        print(f"Different field order result: '{result4}'")
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(f"Different field order content:\n{content}")

if __name__ == "__main__":
    test_json_to_csv_append()
    test_edge_cases()
