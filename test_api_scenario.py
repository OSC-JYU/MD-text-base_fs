import os
import tempfile
import json
os.environ.setdefault("MD_PATH", os.getcwd())

from api import json_to_csv_custom

def test_api_many_to_one_scenario():
    """Test the exact scenario used in the API's /process endpoint"""
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, "api_test.csv")
        
        print("=== Testing API Many-to-One Scenario ===\n")
        
        # Simulate the API's many-to-one processing
        # This mimics what happens in the /process endpoint
        
        # File 1: current_file = 1, total_files = 3
        print("Processing file 1/3...")
        content_json_1 = [
            {"id": 1, "title": "Document 1", "type": "pdf", "status": "processed"},
            {"id": 2, "title": "Document 2", "type": "docx", "status": "pending"}
        ]
        
        result1 = json_to_csv_custom(content_json_1, {}, append=False, output_path=output_path)
        print(f"File 1 result: {result1[:100]}...")
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content1 = f.read()
            print(f"File 1 content:\n{content1}")
        
        # File 2: current_file = 2, total_files = 3
        print("\nProcessing file 2/3...")
        content_json_2 = [
            {"id": 3, "title": "Document 3", "type": "txt", "status": "completed"},
            {"id": 4, "title": "Document 4", "type": "pdf", "status": "failed"}
        ]
        
        result2 = json_to_csv_custom(content_json_2, {}, append=True, output_path=output_path)
        print(f"File 2 result: '{result2}'")
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content2 = f.read()
            print(f"File 2 content:\n{content2}")
        
        # File 3: current_file = 3, total_files = 3 (last file)
        print("\nProcessing file 3/3 (last file)...")
        content_json_3 = [
            {"id": 5, "title": "Document 5", "type": "html", "status": "processed"},
            {"id": 6, "title": "Document 6", "type": "md", "status": "completed"}
        ]
        
        result3 = json_to_csv_custom(content_json_3, {}, append=True, output_path=output_path)
        print(f"File 3 result: '{result3}'")
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content3 = f.read()
            print(f"Final content:\n{content3}")
        
        # Verify the final CSV structure
        print("\n=== Verification ===")
        import csv
        with open(output_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            rows = list(reader)
            print(f"Total rows: {len(rows)}")
            print(f"Headers: {reader.fieldnames}")
            
            for i, row in enumerate(rows, 1):
                print(f"Row {i}: {row}")
        
        # Test that all data is preserved and no duplicates
        expected_ids = [1, 2, 3, 4, 5, 6]
        actual_ids = [int(row['id']) for row in rows]
        print(f"\nExpected IDs: {expected_ids}")
        print(f"Actual IDs: {actual_ids}")
        print(f"All data preserved: {set(expected_ids) == set(actual_ids)}")
        
        # Test that headers are only at the top
        with open(output_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            header_count = sum(1 for line in lines if line.startswith('"id"'))
            print(f"Header count (should be 1): {header_count}")

def test_different_json_structures():
    """Test appending with different JSON structures"""
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, "structure_test.csv")
        
        print("\n=== Testing Different JSON Structures ===\n")
        
        # Test 1: List of objects
        print("Test 1: List of objects")
        data1 = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        json_to_csv_custom(data1, {}, append=False, output_path=output_path)
        
        # Test 2: Single object with array value
        print("Test 2: Single object with array value")
        data2 = {"items": [{"x": 5, "y": 6}, {"x": 7, "y": 8}]}
        json_to_csv_custom(data2, {}, append=True, output_path=output_path)
        
        # Test 3: Another list
        print("Test 3: Another list")
        data3 = [{"z": 9, "w": 10}]
        json_to_csv_custom(data3, {}, append=True, output_path=output_path)
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(f"Final content:\n{content}")

if __name__ == "__main__":
    test_api_many_to_one_scenario()
    test_different_json_structures()
