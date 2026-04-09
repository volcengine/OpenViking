import os
import tempfile
import uuid


def create_test_file(content=None, suffix=".txt"):
    if content is None:
        content = f"测试文件内容 - {uuid.uuid4()}\n这是一个用于API测试的临时文件。\n包含一些测试数据。"
    
    temp_dir = tempfile.mkdtemp()
    test_file_path = os.path.join(temp_dir, f"test_file_{str(uuid.uuid4())[:8]}{suffix}")
    
    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    return test_file_path, temp_dir


def create_test_directory():
    temp_dir = tempfile.mkdtemp()
    
    for i in range(3):
        file_path = os.path.join(temp_dir, f"file_{i}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"测试文件 {i} 的内容\n一些测试数据 {uuid.uuid4()}")
    
    subdir = os.path.join(temp_dir, "subdir")
    os.makedirs(subdir)
    with open(os.path.join(subdir, "nested_file.txt"), "w", encoding="utf-8") as f:
        f.write("嵌套文件的内容")
    
    return temp_dir
