import sys
from pathlib import Path

# Add parent dir to path
sys.path.append(str(Path(__file__).resolve().parents[1]))
import pipeline.evaluate as evaluate

def main():
    test_file = Path("tests/test_utf16_dummy.md")
    
    # Write a UTF-16 encoded file with BOM
    text = "Hello, this is a UTF-16 encoded text to test self-healing."
    test_file.write_bytes(text.encode("utf-16"))
    
    print(f"Created UTF-16 file: {test_file}")
    
    # Call load_file from evaluate
    loaded_text = evaluate.load_file(test_file)
    print(f"Loaded text: '{loaded_text}'")
    
    # Verify file is now UTF-8
    try:
        new_text = test_file.read_text(encoding="utf-8")
        print(f"File successfully read as UTF-8: '{new_text}'")
        if new_text.lstrip("\ufeff") == text:
            print("SUCCESS: Encoding healed correctly!")
        else:
            print("FAILURE: Content mismatch after healing.")
    except UnicodeDecodeError as e:
        print(f"FAILURE: File still cannot be read as UTF-8: {e}")
        
    # Cleanup
    if test_file.exists():
        test_file.unlink()

if __name__ == "__main__":
    main()
