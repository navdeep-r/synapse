"""AST Parser for extracting semantic entities from Python code."""

import ast
from typing import Any

def parse_python_file(content: str, file_path: str, repo_id: str, group_id: str) -> tuple[list[dict], list[dict]]:
    """Parse a python file into nodes and edges.
    
    Returns:
        nodes: list of dict representing Graphiti EntityNode attributes
        edges: list of dict representing Graphiti EntityEdge attributes
    """
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        issue_uuid = f"issue-{repo_id}-{file_path}-syntax"
        file_uuid = f"file-{repo_id}-{file_path}"
        issue_node = {
            "uuid": issue_uuid,
            "name": "Syntax Error",
            "labels": ["CodeIssue"],
            "summary": f"Syntax error at line {getattr(e, 'lineno', 'unknown')}: {e.msg}",
            "attributes": {
                "file": file_path,
                "type": "issue",
                "classification_subtype": "syntax_error",
                "severity": "high",
                "detection": "static_analysis",
                "external_id": issue_uuid
            }
        }
        issue_edge = {
            "source_uuid": file_uuid,
            "target_uuid": issue_uuid,
            "fact": f"{file_path} has a syntax error",
            "labels": ["HAS_ISSUE"]
        }
        return [issue_node], [issue_edge]
        
    nodes = []
    edges = []
    
    # We use a naming convention for UUIDs so we can reference them
    file_uuid = f"file-{repo_id}-{file_path}"
    
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.current_class = None
            
        def visit_Import(self, node: ast.Import):
            for alias in node.names:
                # We can map imports later, but for now we'll just track the name
                pass
            self.generic_visit(node)
            
        def visit_ImportFrom(self, node: ast.ImportFrom):
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef):
            class_uuid = f"class-{repo_id}-{file_path}-{node.name}"
            nodes.append({
                "uuid": class_uuid,
                "name": node.name,
                "labels": ["CodeClass"],
                "summary": f"Class {node.name} in {file_path}",
                "attributes": {
                    "file": file_path, 
                    "type": "symbol",
                    "classification_subtype": "class",
                    "external_id": f"{file_path}::{node.name}"
                }
            })
            
            edges.append({
                "source_uuid": file_uuid,
                "target_uuid": class_uuid,
                "fact": f"{file_path} contains class {node.name}",
                "labels": ["CONTAINS"]
            })
            
            # Visit methods inside class
            prev_class = self.current_class
            self.current_class = class_uuid
            self.generic_visit(node)
            self.current_class = prev_class

        def visit_FunctionDef(self, node: ast.FunctionDef):
            self._handle_func(node)
            
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self._handle_func(node)
            
        def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
            is_method = self.current_class is not None
            label = "CodeMethod" if is_method else "CodeFunction"
            func_uuid = f"func-{repo_id}-{file_path}-{node.name}"
            
            nodes.append({
                "uuid": func_uuid,
                "name": node.name,
                "labels": [label],
                "summary": f"{label} {node.name} in {file_path}",
                "attributes": {
                    "file": file_path, 
                    "type": "symbol",
                    "classification_subtype": "method" if is_method else "function",
                    "external_id": f"{file_path}::{node.name}"
                }
            })
            
            if is_method:
                edges.append({
                    "source_uuid": self.current_class,
                    "target_uuid": func_uuid,
                    "fact": f"class contains method {node.name}",
                    "labels": ["CONTAINS"]
                })
            else:
                edges.append({
                    "source_uuid": file_uuid,
                    "target_uuid": func_uuid,
                    "fact": f"{file_path} contains function {node.name}",
                    "labels": ["CONTAINS"]
                })
                
            self.generic_visit(node)
            
    visitor = Visitor()
    visitor.visit(tree)
    
    return nodes, edges

import re

def _regex_extract(content: str, file_path: str, repo_id: str, group_id: str, class_pattern: str, func_pattern: str, is_java: bool = False) -> tuple[list[dict], list[dict]]:
    nodes = []
    edges = []
    file_uuid = f"file-{repo_id}-{file_path}"
    
    classes = re.finditer(class_pattern, content, re.MULTILINE)
    for match in classes:
        class_name = match.group(1)
        if not class_name:
            continue
            
        class_uuid = f"class-{repo_id}-{file_path}-{class_name}"
        nodes.append({
            "uuid": class_uuid,
            "name": class_name,
            "labels": ["CodeClass"],
            "summary": f"Class {class_name} in {file_path}",
            "attributes": {
                "file": file_path, 
                "type": "symbol",
                "classification_subtype": "class",
                "external_id": f"{file_path}::{class_name}"
            }
        })
        
        edges.append({
            "source_uuid": file_uuid,
            "target_uuid": class_uuid,
            "fact": f"{file_path} contains class {class_name}",
            "labels": ["CONTAINS"]
        })

    funcs = re.finditer(func_pattern, content, re.MULTILINE)
    for match in funcs:
        func_name = match.group(1)
        if not func_name:
            continue
            
        func_uuid = f"func-{repo_id}-{file_path}-{func_name}"
        nodes.append({
            "uuid": func_uuid,
            "name": func_name,
            "labels": ["CodeFunction"],
            "summary": f"Function {func_name} in {file_path}",
            "attributes": {
                "file": file_path, 
                "type": "symbol",
                "classification_subtype": "function",
                "external_id": f"{file_path}::{func_name}"
            }
        })
        
        # If Java, we often have methods inside a single class, but for simplicity via regex
        # we will attach them to the file unless we do complex scope parsing.
        edges.append({
            "source_uuid": file_uuid,
            "target_uuid": func_uuid,
            "fact": f"{file_path} contains function {func_name}",
            "labels": ["CONTAINS"]
        })
        
    return nodes, edges

def parse_code_file(content: str, file_path: str, repo_id: str, group_id: str) -> tuple[list[dict], list[dict]]:
    if file_path.endswith(".py"):
        return parse_python_file(content, file_path, repo_id, group_id)
    elif file_path.endswith(".js") or file_path.endswith(".ts") or file_path.endswith(".jsx") or file_path.endswith(".tsx"):
        class_pattern = r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_]+)"
        func_pattern = r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)"
        return _regex_extract(content, file_path, repo_id, group_id, class_pattern, func_pattern)
    elif file_path.endswith(".go"):
        class_pattern = r"^\s*type\s+([A-Za-z0-9_]+)\s+struct"
        func_pattern = r"^\s*func\s+(?:\([^\)]+\)\s+)?([A-Za-z0-9_]+)"
        return _regex_extract(content, file_path, repo_id, group_id, class_pattern, func_pattern)
    elif file_path.endswith(".rs"):
        class_pattern = r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z0-9_]+)"
        func_pattern = r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)"
        return _regex_extract(content, file_path, repo_id, group_id, class_pattern, func_pattern)
    elif file_path.endswith(".java"):
        class_pattern = r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:final\s+)?(?:abstract\s+)?class\s+([A-Za-z0-9_]+)"
        func_pattern = r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:final\s+)?[<>\w\s\[\]]*\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?:throws\s+[^{]+)?{"
        return _regex_extract(content, file_path, repo_id, group_id, class_pattern, func_pattern, is_java=True)
    
    return [], []
