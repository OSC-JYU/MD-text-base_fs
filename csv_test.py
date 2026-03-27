#!/usr/bin/env python3
"""
Test script for JSON to CSV conversion
"""

import json
import io
import csv
from typing import List,Dict, Any, Union

def flatten_json_custom(data: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
    """
    Custom function to flatten nested JSON with special handling for arrays
    """
    items = []
    
    for key, value in data.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        
        if isinstance(value, dict):
            items.extend(flatten_json_custom(value, new_key, sep=sep).items())
        elif isinstance(value, list):
            # Join arrays with semicolon and space
            items.append((new_key, "; ".join(str(item) for item in value)))
        else:
            items.append((new_key, value))
    
    return dict(items)

def json_to_csv_custom(json_data: Union[Dict, List], output_path: str = None) -> str:
    """
    Convert JSON to CSV using custom flattening logic
    """
    flattened_data = []
    
    if isinstance(json_data, list):
        # If it's a list, process each item
        for item in json_data:
            flattened_data.append(flatten_json_custom(item))
    elif isinstance(json_data, dict):
        # Check if the dict has a single key with an array value (like {'books': [...]})
        keys = list(json_data.keys())
        if len(keys) == 1 and isinstance(json_data[keys[0]], list):
            # Process the array under the single key - each item becomes a row
            # Prefix keys with the parent key name
            parent_key = keys[0]
            for item in json_data[keys[0]]:
                flattened_item = flatten_json_custom(item, parent_key=parent_key)
                flattened_data.append(flattened_item)
        else:
            # Flatten the dict normally
            flattened_data.append(flatten_json_custom(json_data))
    else:
        return ""
    
    if not flattened_data:
        return ""
    
    # Get all unique keys from all flattened objects
    all_keys = set()
    for item in flattened_data:
        all_keys.update(item.keys())
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=sorted(all_keys), delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writeheader()
    
    for item in flattened_data:
        # Fill missing keys with empty strings
        row = {key: item.get(key, '') for key in sorted(all_keys)}
        writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    
    if output_path:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            f.write(csv_content)
    
    return csv_content

def main():
    # Test with your JSON
    test_json = {
        'books': [
            {
                'title': 'De rebus "gestis" Gallorum libri IX', 
                'year': '1555', 
                'authors': ['Ferron, Arnoldus']
            }, 
            {
                'title': 'Chronicon de regibus Francorum', 
                'year': '1551', 
                'authors': ['Tillet, Johannes', 'Aemilius Paullus, Lucius']
            }
        ]
    }
    
    print("Input JSON:")
    print(json.dumps(test_json, indent=2))
    print("\n" + "="*50 + "\n")
    
    # Test the conversion
    csv_result = json_to_csv_custom(test_json)
    print("CSV Output:")
    print(csv_result)
    
    # Save to file
    output_file = "test_output.csv"
    json_to_csv_custom(test_json, output_file)
    print(f"\nCSV saved to: {output_file}")
    
    # Test with different JSON structures
    print("\n" + "="*50 + "\n")
    print("Testing with different JSON structures:")
    
    # Test 1: Simple nested object
    simple_json = {
        "user": {
            "name": "John Doe",
            "age": 30,
            "hobbies": ["reading", "swimming", "coding"]
        }
    }
    
    print("\nSimple nested JSON:")
    print(json.dumps(simple_json, indent=2))
    print("CSV Output:")
    print(json_to_csv_custom(simple_json))
    
    # Test 2: Array of objects
    array_json = [
        {"id": 1, "name": "Item 1", "tags": ["tag1", "tag2"]},
        {"id": 2, "name": "Item 2", "tags": ["tag3"]}
    ]
    
    print("\nArray of objects JSON:")
    print(json.dumps(array_json, indent=2))
    print("CSV Output:")
    print(json_to_csv_custom(array_json))

if __name__ == "__main__":
    main()