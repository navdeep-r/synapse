import pytest
from github.ast_parsers import parse_python_file

def test_ast_parser():
    code = """
import os

class MyClass:
    def my_method(self):
        pass

def my_function():
    pass
"""
    nodes, edges = parse_python_file(code, "test.py", "repo1", "tenant1")
    
    assert len(nodes) == 3
    
    class_nodes = [n for n in nodes if "CodeClass" in n["labels"]]
    assert len(class_nodes) == 1
    assert class_nodes[0]["name"] == "MyClass"
    
    method_nodes = [n for n in nodes if "CodeMethod" in n["labels"]]
    assert len(method_nodes) == 1
    assert method_nodes[0]["name"] == "my_method"
    
    func_nodes = [n for n in nodes if "CodeFunction" in n["labels"]]
    assert len(func_nodes) == 1
    assert func_nodes[0]["name"] == "my_function"
    
    # Check edges
    # GitFile -> MyClass
    # GitFile -> my_function
    # MyClass -> my_method
    assert len(edges) == 3
    
    file_uuid = "file-repo1-test.py"
    class_uuid = class_nodes[0]["uuid"]
    method_uuid = method_nodes[0]["uuid"]
    func_uuid = func_nodes[0]["uuid"]
    
    assert any(e["source_uuid"] == file_uuid and e["target_uuid"] == class_uuid for e in edges)
    assert any(e["source_uuid"] == file_uuid and e["target_uuid"] == func_uuid for e in edges)
    assert any(e["source_uuid"] == class_uuid and e["target_uuid"] == method_uuid for e in edges)
