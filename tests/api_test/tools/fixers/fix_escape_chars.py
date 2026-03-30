import os


def fix_escape_chars(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace all occurrences of \' with '
    fixed_content = content.replace(r"\'", "'")

    if fixed_content != content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fixed_content)
        print(f"Fixed {file_path}")
        return True
    else:
        print(f"No changes needed for {file_path}")
        return False


def main():
    test_dir = "tests"
    fixed_count = 0

    for root, _dirs, files in os.walk(test_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py" and file != "conftest.py":
                file_path = os.path.join(root, file)
                if fix_escape_chars(file_path):
                    fixed_count += 1

    print(f"\nTotal files fixed: {fixed_count}")


if __name__ == "__main__":
    main()
