import copy
import csv
import functools
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any


@functools.cache
def get_go_parser_bin() -> str:
    """Compiles go_ast_parser.go to a binary if not already compiled, and returns its path."""
    current_dir = Path(__file__).parent
    parser_src = current_dir / "go_ast_parser.go"
    if not parser_src.exists():
        raise FileNotFoundError(f"Go AST parser source not found at {parser_src}")

    bin_name = "go_ast_parser.exe" if os.name == "nt" else "go_ast_parser"
    bin_path = current_dir / bin_name

    # Check modification time to rebuild if source is newer
    if not bin_path.exists() or parser_src.stat().st_mtime > bin_path.stat().st_mtime:
        cmd = ["go", "build", "-o", str(bin_path), str(parser_src)]
        subprocess.run(cmd, check=True, shell=True)

    return str(bin_path)


_reference_ast_cache: dict[tuple[str, bool], dict[str, Any]] = {}


def get_ast(filepath: str | Path, normalize: bool = True) -> dict[str, Any]:
    """Executes the Go AST parser and returns parsed JSON AST."""
    filepath_str = str(filepath)
    filename = Path(filepath_str).name
    cache_key = (filepath_str, normalize)
    if filename.startswith("c_") and cache_key in _reference_ast_cache:
        return copy.deepcopy(_reference_ast_cache[cache_key])

    try:
        parser_bin = get_go_parser_bin()
    except Exception as e:
        raise RuntimeError(f"Failed to compile/locate Go AST parser: {e}")

    cmd = [parser_bin, filepath_str]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to generate Go AST for {filepath_str}. Error:\n{result.stderr}"
        )

    stdout = result.stdout.strip()
    try:
        ast_data = json.loads(stdout)
        if normalize:
            ast_data = normalize_ast(ast_data)
            cleanup_dictionary_node(ast_data)
        if filename.startswith("c_"):
            _reference_ast_cache[cache_key] = copy.deepcopy(ast_data)
        return ast_data
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse Go AST output as JSON. Output was:\n{stdout}\nError: {e}"
        )





def count_var_references(node, name):
    """Recursively counts how many times the variable name is referenced."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            return sum(count_var_references(item, name) for item in node)
        return 0

    count = 0
    t = node.get("type")
    if t == "VarAccessToken" and node.get("name") == name:
        count += 1
    elif t == "VarAssignNode" and node.get("name") == name:
        count += 1

    for k, v in node.items():
        if k not in ["type", "name"]:
            count += count_var_references(v, name)
    return count


def replace_var_reference(node, name, replacement_value):
    """Recursively replaces VarAccessToken with replacement_value."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            for idx, item in enumerate(node):
                if isinstance(item, dict) and item.get("type") == "VarAccessToken" and item.get("name") == name:
                    import copy
                    node[idx] = copy.deepcopy(replacement_value)
                else:
                    replace_var_reference(item, name, replacement_value)
        return

    for k, v in list(node.items()):
        if isinstance(v, dict) and v.get("type") == "VarAccessToken" and v.get("name") == name:
            import copy
            node[k] = copy.deepcopy(replacement_value)
        else:
            replace_var_reference(v, name, replacement_value)


def normalize_ast(node):
    """Recursively normalizes the AST (e.g., inlining single-use variables)."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            for item in node:
                normalize_ast(item)
        return node

    for k, v in list(node.items()):
        if k != "type":
            normalize_ast(v)

    t = node.get("type")
    if t == "ListNode":
        program = node.get("program", [])
        new_program = []
        i = 0
        while i < len(program):
            stmt = program[i]
            if isinstance(stmt, dict) and stmt.get("type") == "VarAssignNode":
                var_name = stmt.get("name")
                var_value = stmt.get("value")
                remaining_stmts = [s for s in program[i+1:] if isinstance(s, dict) and s.get("type") != "DictionaryNode"]
                ref_count = sum(count_var_references(s, var_name) for s in remaining_stmts)
                prev_stmts = [s for s in program[:i] if isinstance(s, dict) and s.get("type") != "DictionaryNode"]
                prev_ref_count = sum(count_var_references(s, var_name) for s in prev_stmts)

                if ref_count == 1 and prev_ref_count == 0:
                    for s in remaining_stmts:
                        replace_var_reference(s, var_name, var_value)
                    i += 1
                    continue
            new_program.append(stmt)
            i += 1
        node["program"] = new_program

    return node


def cleanup_dictionary_node(root_node):
    """Removes variables from the DictionaryNode that are never used in the algorithm."""
    if not isinstance(root_node, dict) or root_node.get("type") != "ListNode":
        return

    dict_nodes = []
    algo_nodes = []
    for stmt in root_node.get("program", []):
        if isinstance(stmt, dict) and stmt.get("type") == "DictionaryNode":
            dict_nodes.append(stmt)
        else:
            algo_nodes.append(stmt)

    for dict_node in dict_nodes:
        variables = dict_node.get("variables", [])
        new_variables = []
        for var in variables:
            var_name = var.get("name")
            is_const = var.get("is_const", False)
            count = sum(count_var_references(node, var_name) for node in algo_nodes)
            if count > 0 or is_const:
                new_variables.append(var)
        dict_node["variables"] = new_variables


def ast_to_go_code(node, indent=""):
    """Translates the AST node dictionary back into clean Go code."""
    if not node:
        return ""
    t = node.get("type")

    if t == "ListNode":
        return "\n".join(
            ast_to_go_code(child, indent) for child in node.get("program", []) if child
        )

    elif t == "DictionaryNode":
        lines = []
        for var in node.get("variables", []):
            type_name = var.get("value", {}).get("name", "int")
            if type_name == "integer":
                type_name = "int"
            elif type_name == "string":
                type_name = "string"
            lines.append(f"{indent}var {var['name']} {type_name}")
        return "\n".join(lines)

    elif t == "VarAssignNode":
        return f"{indent}{node['name']} = {ast_to_go_code(node['value'])}"

    elif t == "BinOpNode":
        left = ast_to_go_code(node["left"])
        right = ast_to_go_code(node["right"])
        return f"({left} {node['operator']} {right})"

    elif t == "UnaryOpNode":
        return f"{node['operator']}{ast_to_go_code(node['node'])}"

    elif t == "NumberNode":
        return node["value"]

    elif t == "StringNode":
        val = node["value"]
        if not val.startswith('"'):
            val = f'"{val}"'
        return val

    elif t == "VarAccessToken":
        return node["name"]

    elif t == "CallNode":
        call_name = node["call"]["name"]
        if call_name == "write":
            args_str = ", ".join(ast_to_go_code(arg) for arg in node.get("args", []))
            return f"{indent}fmt.Println({args_str})"
        elif call_name == "read":
            args_str = ", ".join(f"&{ast_to_go_code(arg)}" if arg.get("type") == "VarAccessToken" else ast_to_go_code(arg) for arg in node.get("args", []))
            return f"{indent}fmt.Scan({args_str})"
        else:
            args_str = ", ".join(ast_to_go_code(arg) for arg in node.get("args", []))
            return f"{indent}{call_name}({args_str})"

    elif t == "IfNode":
        if_str = ""
        for idx, case in enumerate(node.get("cases", [])):
            cond = ast_to_go_code(case["condition"])
            body = ast_to_go_code(case["body"], indent + "    ")
            if idx == 0:
                if_str += f"{indent}if {cond} {{\n{body}\n{indent}}}"
            else:
                if_str += f" else if {cond} {{\n{body}\n{indent}}}"

        else_case = node.get("else_case")
        if else_case and else_case.get("body"):
            else_body = ast_to_go_code(else_case["body"], indent + "    ")
            if_str += f" else {{\n{else_body}\n{indent}}}"

        return if_str

    elif t == "WhileNode":
        cond = ast_to_go_code(node["condition"])
        body = ast_to_go_code(node["body"], indent + "    ")
        return f"{indent}for {cond} {{\n{body}\n{indent}}}"

    return ""


def ast_to_full_go(root_node):
    """Wraps AST statement nodes in a Go main function block."""
    dict_nodes = []
    algo_nodes = []

    for stmt in root_node.get("program", []):
        if not stmt:
            continue
        if stmt.get("type") == "DictionaryNode":
            dict_nodes.append(stmt)
        else:
            algo_nodes.append(stmt)

    code = "package main\n\nimport \"fmt\"\n\nfunc main() {\n"
    for d_node in dict_nodes:
        if d_node.get("variables"):
            code += ast_to_go_code(d_node, "    ") + "\n"
    for stmt in algo_nodes:
        code += ast_to_go_code(stmt, "    ") + "\n"
    code += "}"
    return code


def node_similarity(b_node, c_node):
    """Computes a structural similarity score between two AST nodes [0.0 to 1.0]."""
    if not isinstance(b_node, dict) or not isinstance(c_node, dict):
        return 1.0 if b_node == c_node else 0.0

    b_type = b_node.get("type")
    c_type = c_node.get("type")

    if b_type != c_type:
        if (b_type in ["IfNode", "WhileNode", "ForNode"]) and (c_type in ["IfNode", "WhileNode", "ForNode"]):
            return 0.3
        return 0.0

    if b_type == "VarAssignNode":
        return 1.0 if b_node.get("name") == c_node.get("name") else 0.5
    elif b_type == "CallNode":
        b_name = b_node.get("call", {}).get("name") if isinstance(b_node.get("call"), dict) else None
        c_name = c_node.get("call", {}).get("name") if isinstance(c_node.get("call"), dict) else None
        return 1.0 if b_name == c_name else 0.5
    elif b_type in ["IfNode", "WhileNode", "ForNode"]:
        return 0.8
    elif b_type in ["BinOpNode", "UnaryOpNode", "NumberNode", "StringNode", "VarAccessToken"]:
        return 1.0 if ast_to_go_code(b_node) == ast_to_go_code(c_node) else 0.5

    return 0.5


def align_lists(buggy_list, correct_list):
    """Greedily aligns buggy_list elements to correct_list elements.
    Returns: (matches, unmatched_buggy, unmatched_correct)
    """
    matched_b = set()
    matched_c = set()
    matches = []

    for c_idx, c_stmt in enumerate(correct_list):
        best_b_idx = -1
        best_score = -1.0
        for b_idx, b_stmt in enumerate(buggy_list):
            if b_idx in matched_b:
                continue
            score = node_similarity(b_stmt, c_stmt)
            if score > best_score:
                best_score = score
                best_b_idx = b_idx
        if best_score > 0.0:
            matches.append((best_b_idx, c_idx))
            matched_b.add(best_b_idx)
            matched_c.add(c_idx)

    unmatched_buggy = [idx for idx in range(len(buggy_list)) if idx not in matched_b]
    unmatched_correct = [idx for idx in range(len(correct_list)) if idx not in matched_c]

    return matches, unmatched_buggy, unmatched_correct


def get_dictionary_vars(root_node):
    """Helper to extract variable and constant declarations from correct solution dictionary."""
    if not isinstance(root_node, dict) or root_node.get("type") != "ListNode":
        return {}
    for stmt in root_node.get("program", []):
        if isinstance(stmt, dict) and stmt.get("type") == "DictionaryNode":
            return {var.get("name"): var for var in stmt.get("variables", []) if var.get("name")}
    return {}


def diff_and_repair(buggy, correct, edit_logs=None, in_condition=False, reference_dictionary=None):
    """Recursively diffs two JSON AST nodes and repairs buggy inplace to match correct."""
    if not isinstance(buggy, dict) or not isinstance(correct, dict):
        return False

    repaired = False

    # Extract correct dictionary variable details at the root program level
    if reference_dictionary is None and correct.get("type") == "ListNode":
        reference_dictionary = get_dictionary_vars(correct)

    # If structural types mismatch, replace buggy with copy of correct
    if buggy.get("type") != correct.get("type"):
        misconception_data = None
        
        # Check CD-13: Selection statements are iterative like loops
        if buggy.get("type") == "IfNode" and correct.get("type") == "WhileNode":
            misconception_data = {
                "code": "CD-13",
                "title": "If Statement Used Instead of Loop",
                "description": "Used an 'if' conditional branch where a repeating 'while' loop was required."
            }
        elif buggy.get("type") == "WhileNode" and correct.get("type") == "IfNode":
            misconception_data = {
                "code": "LO-10",
                "title": "Loop Used Instead of Conditional",
                "description": "Used a repeating 'while' loop where a single 'if' conditional branch was required."
            }
            
        # Check OP-1: Using = instead of == inside Go condition
        if in_condition and buggy.get("type") == "VarAssignNode":
            misconception_data = {
                "code": "OP-1",
                "title": "Using Assignment Instead of Comparison",
                "description": "Used the assignment operator (=) instead of the equality comparison operator (==) inside a condition."
            }

        if edit_logs is not None:
            log_entry = {
                "type": "structural_mismatch",
                "buggy_expr": ast_to_go_code(buggy),
                "correct_expr": ast_to_go_code(correct),
                "detail": f"Expected node type '{correct.get('type')}', but found '{buggy.get('type')}'",
            }
            if misconception_data:
                log_entry["misconception"] = misconception_data
            edit_logs.append(log_entry)
        for k in list(buggy.keys()):
            del buggy[k]
        for k, v in correct.items():
            buggy[k] = v
        return True

    t = buggy.get("type")

    if t == "BinOpNode":
        if buggy.get("operator") != correct.get("operator"):
            old_op = buggy.get("operator")
            new_op = correct.get("operator")
            
            misconception_data = None
            if in_condition and old_op == "=":
                misconception_data = {
                    "code": "OP-1",
                    "title": "Using Assignment Instead of Comparison",
                    "description": f"Used assignment operator '{old_op}' instead of comparison operator '{new_op}' inside a condition."
                }
                
            if edit_logs is not None:
                log_entry = {
                    "type": "operator_mismatch",
                    "buggy_expr": ast_to_go_code(buggy),
                    "correct_expr": ast_to_go_code(correct),
                    "detail": f"Comparison operator mismatch: expected '{new_op}', but found '{old_op}'",
                }
                if misconception_data:
                    log_entry["misconception"] = misconception_data
                edit_logs.append(log_entry)
            buggy["operator"] = correct["operator"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("left"), correct.get("left"), edit_logs, in_condition, reference_dictionary)
            or repaired
        )
        repaired = (
            diff_and_repair(buggy.get("right"), correct.get("right"), edit_logs, in_condition, reference_dictionary)
            or repaired
        )

    elif t == "UnaryOpNode":
        if buggy.get("operator") != correct.get("operator"):
            old_op = buggy.get("operator")
            new_op = correct.get("operator")
            if edit_logs is not None:
                edit_logs.append(
                    {
                        "type": "operator_mismatch",
                        "buggy_expr": ast_to_go_code(buggy),
                        "correct_expr": ast_to_go_code(correct),
                        "detail": f"Unary operator mismatch: expected '{new_op}', but found '{old_op}'",
                    }
                )
            buggy["operator"] = correct["operator"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("node"), correct.get("node"), edit_logs, in_condition, reference_dictionary)
            or repaired
        )

    elif t == "NumberNode" or t == "StringNode":
        if buggy.get("value") != correct.get("value"):
            old_val = buggy.get("value")
            new_val = correct.get("value")
            if edit_logs is not None:
                edit_logs.append(
                    {
                        "type": "value_mismatch",
                        "buggy_expr": ast_to_go_code(buggy),
                        "correct_expr": ast_to_go_code(correct),
                        "detail": f"Value mismatch: expected '{new_val}', but found '{old_val}'",
                    }
                )
            buggy["value"] = correct["value"]
            repaired = True

    elif t == "VarAccessToken":
        if buggy.get("name") != correct.get("name"):
            old_name = buggy.get("name")
            new_name = correct.get("name")
            
            misconception_data = None
            if old_name.lower() == new_name.lower():
                misconception_data = {
                    "code": "VA-7",
                    "title": "Case-Sensitivity Confusion",
                    "description": f"Variable names are case-sensitive. Used '{old_name}' instead of '{new_name}'."
                }
                
            if edit_logs is not None:
                log_entry = {
                    "type": "name_mismatch",
                    "buggy_expr": ast_to_go_code(buggy),
                    "correct_expr": ast_to_go_code(correct),
                    "detail": f"Variable name mismatch: expected '{new_name}', but found '{old_name}'",
                }
                if misconception_data:
                    log_entry["misconception"] = misconception_data
                edit_logs.append(log_entry)
            buggy["name"] = correct["name"]
            repaired = True

    elif t == "VarAssignNode":
        b_name = buggy.get("name")
        c_name = correct.get("name")
        
        misconception_data = None
        
        # Check CO-1: Using a literal value as variable name
        is_numeric = False
        try:
            float(b_name)
            is_numeric = True
        except ValueError:
            pass
            
        if is_numeric:
            misconception_data = {
                "code": "CO-1",
                "title": "Assigning to a Literal Value",
                "description": f"Used a literal value '{b_name}' as if it were a variable assignment target."
            }
        # Check CO-2: Confusing named constants with variables
        elif reference_dictionary and b_name in reference_dictionary:
            var_def = reference_dictionary[b_name]
            if var_def.get("is_const", False):
                misconception_data = {
                    "code": "CO-2",
                    "title": "Reassigning a Constant",
                    "description": f"Attempted to reassign or modify the constant variable '{b_name}'."
                }
                
        # Check VA-2: Swapped assignment
        if b_name != c_name:
            b_val = buggy.get("value", {})
            c_val = correct.get("value", {})
            if (isinstance(b_val, dict) and b_val.get("type") == "VarAccessToken" and
                isinstance(c_val, dict) and c_val.get("type") == "VarAccessToken" and
                b_val.get("name") == c_name and c_val.get("name") == b_name):
                misconception_data = {
                    "code": "VA-2",
                    "title": "Commutative Assignment Confusion",
                    "description": f"Assignment statement is reversed. Wrote '{b_name} = {c_name}' instead of '{c_name} = {b_name}'."
                }
            # Check VA-7: Case sensitivity
            elif b_name.lower() == c_name.lower():
                misconception_data = {
                    "code": "VA-7",
                    "title": "Case-Sensitivity Confusion",
                    "description": f"Variable names are case-sensitive. Used '{b_name}' instead of '{c_name}'."
                }
                
        if b_name != c_name:
            old_name = buggy.get("name")
            new_name = correct.get("name")
            if edit_logs is not None:
                log_entry = {
                    "type": "name_mismatch",
                    "buggy_expr": ast_to_go_code(buggy),
                    "correct_expr": ast_to_go_code(correct),
                    "detail": f"Assignment variable name mismatch: expected '{new_name}', but found '{old_name}'",
                }
                if misconception_data:
                    log_entry["misconception"] = misconception_data
                edit_logs.append(log_entry)
            buggy["name"] = correct["name"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("value"), correct.get("value"), edit_logs, in_condition, reference_dictionary)
            or repaired
        )

    elif t == "CallNode":
        repaired = (
            diff_and_repair(buggy.get("call"), correct.get("call"), edit_logs, in_condition, reference_dictionary)
            or repaired
        )
        if len(buggy.get("args", [])) == len(correct.get("args", [])):
            for b_arg, c_arg in zip(buggy.get("args", []), correct.get("args", [])):
                repaired = diff_and_repair(b_arg, c_arg, edit_logs, in_condition, reference_dictionary) or repaired

    elif t == "IfNode":
        if len(buggy.get("cases", [])) == len(correct.get("cases", [])):
            for b_case, c_case in zip(buggy.get("cases", []), correct.get("cases", [])):
                repaired = (
                    diff_and_repair(
                        b_case.get("condition"), c_case.get("condition"), edit_logs, in_condition=True, reference_dictionary=reference_dictionary
                    )
                    or repaired
                )
                repaired = (
                    diff_and_repair(b_case.get("body"), c_case.get("body"), edit_logs, in_condition=False, reference_dictionary=reference_dictionary)
                    or repaired
                )
        if buggy.get("else_case") and correct.get("else_case"):
            repaired = (
                diff_and_repair(
                    buggy["else_case"].get("body"),
                    correct["else_case"].get("body"),
                    edit_logs,
                    in_condition=False,
                    reference_dictionary=reference_dictionary,
                )
                or repaired
            )

    elif t == "WhileNode":
        repaired = (
            diff_and_repair(buggy.get("condition"), correct.get("condition"), edit_logs, in_condition=True, reference_dictionary=reference_dictionary)
            or repaired
        )
        repaired = (
            diff_and_repair(buggy.get("body"), correct.get("body"), edit_logs, in_condition=False, reference_dictionary=reference_dictionary)
            or repaired
        )

    elif t == "ListNode":
        b_list = buggy.get("program", [])
        c_list = correct.get("program", [])
        matches, unmatched_b, unmatched_c = align_lists(b_list, c_list)

        repaired_flag = False

        for b_idx, c_idx in matches:
            if diff_and_repair(b_list[b_idx], c_list[c_idx], edit_logs, in_condition, reference_dictionary):
                repaired_flag = True

        if unmatched_b or unmatched_c:
            repaired_flag = True

        for b_idx in unmatched_b:
            b_stmt = b_list[b_idx]
            if edit_logs is not None:
                edit_logs.append({
                    "type": "extra_statement",
                    "buggy_expr": ast_to_go_code(b_stmt),
                    "correct_expr": "",
                    "detail": f"Extra statement: found '{ast_to_go_code(b_stmt)}' which is not needed"
                })

        for c_idx in unmatched_c:
            c_stmt = c_list[c_idx]
            if edit_logs is not None:
                edit_logs.append({
                    "type": "missing_statement",
                    "buggy_expr": "",
                    "correct_expr": ast_to_go_code(c_stmt),
                    "detail": f"Missing statement: expected to find '{ast_to_go_code(c_stmt)}'"
                })

        import copy
        new_program = []
        matched_map = {c_idx: b_list[b_idx] for b_idx, c_idx in matches}
        for c_idx, c_stmt in enumerate(c_list):
            if c_idx in matched_map:
                new_program.append(matched_map[c_idx])
            else:
                new_program.append(copy.deepcopy(c_stmt))

        buggy["program"] = new_program
        repaired = repaired_flag or repaired

    elif t == "DictionaryNode":
        if len(buggy.get("variables", [])) == len(correct.get("variables", [])):
            for b_var, c_var in zip(
                buggy.get("variables", []), correct.get("variables", [])
            ):
                repaired = diff_and_repair(b_var, c_var, edit_logs, in_condition, reference_dictionary) or repaired

    return repaired


def find_reference_files(buggy_filename: str, directory: str | Path) -> list[str]:
    """Finds all corresponding correct reference Go files."""
    parts = buggy_filename.split("_")
    challenge_id = parts[1] if len(parts) > 1 else buggy_filename.replace("b_", "").replace(".go", "")

    directory_path = Path(directory)
    references = []
    if directory_path.exists() and directory_path.is_dir():
        for f in directory_path.iterdir():
            if f.is_file() and f.name.startswith("c_") and challenge_id in f.name and f.name.endswith(".go"):
                references.append(str(f))
    return references


def find_reference_file(buggy_filename: str, directory: str | Path) -> str | None:
    """Finds the corresponding correct reference solution Go file (returns first match)."""
    refs = find_reference_files(buggy_filename, directory)
    return refs[0] if refs else None


def execute_go_with_input(filepath: str | Path, inputs: list[str]) -> tuple[str, str]:
    """Executes a Go file with standard inputs and returns stdout."""
    filepath_str = str(filepath)
    try:
        process = subprocess.Popen(
            ["go", "run", filepath_str],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        stdout, stderr = process.communicate(input="\n".join(inputs) + "\n", timeout=120)
        return stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        process.kill()
        return "", "Timeout"


def run_unit_tests(filepath: str | Path, challenge_id: str) -> tuple[bool, str]:
    """Runs a hardcoded set of unit tests for Go programs based on challenge ID."""
    if "l8" in challenge_id:
        test_cases = [{"input": ["4 5"], "expected": "20\n18"}]

        for case in test_cases:
            output, err = execute_go_with_input(filepath, case["input"])

            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if "\n" in case["expected"]:
                val = "\n".join(lines)
            else:
                val = lines[-1] if lines else ""

            if val != case["expected"]:
                return (
                    False,
                    f"Test failed for inputs {case['input']}. Expected {case['expected']}, got {val}. Stderr: {err}",
                )
        return True, "All tests passed!"

    return False, "Unknown challenge"


def repair_buggy_submissions(data_dir: str | Path) -> None:
    """Reads P-Matrix, identifies buggy Go files, repairs them, and verifies with unit tests."""
    data_path = Path(data_dir)
    pmatrix_path = data_path / "P-matrix-out-go.CSV"
    if not pmatrix_path.exists():
        print(f"P-Matrix file not found at {pmatrix_path}")
        return

    buggy_files = []
    with open(pmatrix_path, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            filename = row[0]
            mastery = [int(x) for x in row[1:]]
            if filename.startswith("b_") or 0 in mastery:
                buggy_files.append(filename)

    for filename in buggy_files:
        buggy_path = data_path / filename
        if not buggy_path.exists():
            continue

        ref_paths = find_reference_files(filename, data_path)
        if not ref_paths:
            print(f"Could not find reference template for {filename}. Skipping.")
            continue

        print(f"\n[INFO] Starting Go repair for: {filename}")
        print(f"[INFO] Found {len(ref_paths)} candidate reference template(s).")

        best_repair = None

        for ref_path in ref_paths:
            ref_path_obj = Path(ref_path)
            print(f"[INFO] Trying reference: {ref_path_obj.name}")
            try:
                buggy_ast = get_ast(buggy_path)
                correct_ast = get_ast(ref_path_obj)

                edit_logs = []
                buggy_ast_copy = copy.deepcopy(buggy_ast)
                repaired = diff_and_repair(buggy_ast_copy, correct_ast, edit_logs)

                repaired_code = ""
                passed_tests = False
                test_msg = ""

                temp_filename = f"temp_repaired_{uuid.uuid4().hex}_{filename}"
                temp_path = data_path / temp_filename

                repaired_code = ast_to_full_go(buggy_ast_copy)

                with open(temp_path, "w") as f:
                    f.write(repaired_code)

                challenge_id = filename.split("_")[1] if "_" in filename else filename
                passed_tests, test_msg = run_unit_tests(temp_path, challenge_id)

                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

                candidate = {
                    "ref_path": ref_path_obj,
                    "repaired": repaired,
                    "repaired_code": repaired_code,
                    "edit_logs": edit_logs,
                    "passed_tests": passed_tests,
                    "test_msg": test_msg
                }

                if best_repair is None:
                    best_repair = candidate
                else:
                    if candidate["passed_tests"] and not best_repair["passed_tests"]:
                        best_repair = candidate
                    elif (candidate["passed_tests"] == best_repair["passed_tests"]) and (len(candidate["edit_logs"]) < len(best_repair["edit_logs"])):
                        best_repair = candidate

            except Exception as e:
                print(f"[ERROR] Failed trying reference {ref_path_obj.name}: {e}")

        if best_repair and best_repair["repaired"]:
            best_ref_path = best_repair["ref_path"]
            print(f"[SUCCESS] Best reference chosen: {best_ref_path.name}")
            corrected_path = data_path / ("repaired_" + filename)
            with open(corrected_path, "w") as f:
                f.write(best_repair["repaired_code"])
            print(f"[SUCCESS] Repaired code written to: {corrected_path.name}")

            with open(buggy_path, "r") as f:
                buggy_lines = f.readlines()

            print("\n" + "=" * 60)
            print("   AUTOMATED FEEDBACK - LOGIC HINTS FOR STUDENT (GO)")
            print("=" * 60)
            for log in best_repair["edit_logs"]:
                buggy_expr = log["buggy_expr"]
                correct_expr = log["correct_expr"]
                detail = log["detail"]

                line_num = None
                norm_expr = (
                    buggy_expr.lower()
                    .replace(" ", "")
                    .replace("(", "")
                    .replace(")", "")
                )
                if norm_expr:
                    for idx, line in enumerate(buggy_lines):
                        norm_line = (
                            line.lower()
                            .replace(" ", "")
                            .replace("(", "")
                            .replace(")", "")
                        )
                        if norm_expr in norm_line:
                            line_num = idx + 1
                            break

                if line_num:
                    print(f"* [Line {line_num}]: {detail}")
                    print(f"  -> Your code:       {buggy_lines[line_num - 1].strip()}")
                    print(f"  -> Expected logic:  Contains '{correct_expr}'")
                else:
                    print(f"* [General Hint]: {detail}")
                    print(f"  -> Original expression: '{buggy_expr}'")
                    print(f"  -> Expected logic:      '{correct_expr}'")
            print("=" * 60 + "\n")

            if best_repair["passed_tests"]:
                print(f"[VERIFIED] {corrected_path.name} passed all unit tests!")
            else:
                print(f"[FAILED VERIFICATION] {corrected_path.name}: {best_repair['test_msg']}")
        else:
            print(f"[INFO] No valid repair found for {filename}.")


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    data_dir = current_dir / "data"
    repair_buggy_submissions(data_dir)

