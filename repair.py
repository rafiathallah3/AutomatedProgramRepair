import csv
import json
import os
import subprocess


def get_dap_path():
    """Finds the absolute path to the DAP compiler executable."""
    home = os.path.expanduser("~")
    dap_exe = os.path.join(home, ".dap", "bin", "dap.exe")
    if os.path.exists(dap_exe):
        return dap_exe

    user_path = "C:/Users/rafia/.dap/bin/dap.exe"
    if os.path.exists(user_path):
        return user_path

    try:
        subprocess.run(
            ["dap", "-h"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1
        )
        return "dap"
    except Exception:
        pass

    return None


def get_ast(filepath):
    """Executes the DAP compiler on the file and returns parsed JSON AST."""
    dap_bin = get_dap_path()
    if not dap_bin:
        raise FileNotFoundError(
            "DAP compiler binary not found. Please install DAP first."
        )

    cmd = [dap_bin, filepath, "--show-ast-json"]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to generate AST for {filepath}. Error:\n{result.stderr}"
        )

    # Extract only the JSON portion from the stdout
    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse DAP AST output as JSON. Output was:\n{stdout}\nError: {e}"
        )


def ast_to_code(node, indent=""):
    """Translates the AST node dictionary back into clean DAP code."""
    if not node:
        return ""
    t = node.get("type")

    if t == "ListNode":
        return "\n".join(
            ast_to_code(child, indent) for child in node.get("program", []) if child
        )

    elif t == "DictionaryNode":
        type_groups = {}
        for var in node.get("variables", []):
            is_const = var.get("is_const", False)
            if is_const:
                val = ast_to_code(var.get("value"))
                type_groups[f"const {var['name']} = {val}"] = []
            else:
                type_name = var.get("value", {}).get("name", "integer")
                if type_name not in type_groups:
                    type_groups[type_name] = []
                type_groups[type_name].append(var["name"])

        lines = []
        for type_name, names in type_groups.items():
            if not names:
                lines.append(f"{indent}    {type_name}")
            else:
                lines.append(f"{indent}    {', '.join(names)} : {type_name}")
        return f"{indent}dictionary\n" + "\n".join(lines)

    elif t == "VarAssignNode":
        return f"{indent}{node['name']} <- {ast_to_code(node['value'])}"

    elif t == "BinOpNode":
        left = ast_to_code(node["left"])
        right = ast_to_code(node["right"])
        return f"({left} {node['operator']} {right})"

    elif t == "UnaryOpNode":
        return f"{node['operator']}{ast_to_code(node['node'])}"

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
        args_str = ", ".join(ast_to_code(arg) for arg in node.get("args", []))
        return f"{indent}{call_name} {args_str}"

    elif t == "IfNode":
        if_str = ""
        for idx, case in enumerate(node.get("cases", [])):
            cond = ast_to_code(case["condition"])
            body = ast_to_code(case["body"], indent + "    ")
            if idx == 0:
                if_str += f"{indent}if {cond} then\n{body}\n"
            else:
                if_str += f"{indent}elseif {cond} then\n{body}\n"

        else_case = node.get("else_case")
        if else_case and else_case.get("body"):
            else_body = ast_to_code(else_case["body"], indent + "    ")
            if_str += f"{indent}else\n{else_body}\n"

        if_str += f"{indent}endif"
        return if_str

    elif t == "WhileNode":
        cond = ast_to_code(node["condition"])
        body = ast_to_code(node["body"], indent + "    ")
        return f"{indent}while {cond} do\n{body}\n{indent}endwhile"

    return ""


def ast_to_full_dap(root_node, program_name="AutoRepaired"):
    """Wraps AST statement nodes in a program block."""
    dict_node = None
    algo_nodes = []

    for stmt in root_node.get("program", []):
        if not stmt:
            continue
        if stmt.get("type") == "DictionaryNode":
            dict_node = stmt
        else:
            algo_nodes.append(stmt)

    code = f"program {program_name}\n"
    if dict_node:
        code += ast_to_code(dict_node, "") + "\n"
    code += "algorithm\n"
    for stmt in algo_nodes:
        code += ast_to_code(stmt, "    ") + "\n"
    code += "endprogram"
    return code


def diff_and_repair(buggy, correct, edit_logs=None):
    """Recursively diffs two JSON AST nodes and repairs buggy inplace to match correct."""
    if not isinstance(buggy, dict) or not isinstance(correct, dict):
        return False

    repaired = False

    # If structural types mismatch, replace buggy with copy of correct
    if buggy.get("type") != correct.get("type"):
        if edit_logs is not None:
            edit_logs.append(
                {
                    "type": "structural_mismatch",
                    "buggy_expr": ast_to_code(buggy),
                    "correct_expr": ast_to_code(correct),
                    "detail": f"Expected node type '{correct.get('type')}', but found '{buggy.get('type')}'",
                }
            )
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
            if edit_logs is not None:
                edit_logs.append(
                    {
                        "type": "operator_mismatch",
                        "buggy_expr": ast_to_code(buggy),
                        "correct_expr": ast_to_code(correct),
                        "detail": f"Comparison operator mismatch: expected '{new_op}', but found '{old_op}'",
                    }
                )
            buggy["operator"] = correct["operator"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("left"), correct.get("left"), edit_logs)
            or repaired
        )
        repaired = (
            diff_and_repair(buggy.get("right"), correct.get("right"), edit_logs)
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
                        "buggy_expr": ast_to_code(buggy),
                        "correct_expr": ast_to_code(correct),
                        "detail": f"Unary operator mismatch: expected '{new_op}', but found '{old_op}'",
                    }
                )
            buggy["operator"] = correct["operator"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("node"), correct.get("node"), edit_logs)
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
                        "buggy_expr": ast_to_code(buggy),
                        "correct_expr": ast_to_code(correct),
                        "detail": f"Value mismatch: expected '{new_val}', but found '{old_val}'",
                    }
                )
            buggy["value"] = correct["value"]
            repaired = True

    elif t == "VarAccessToken":
        if buggy.get("name") != correct.get("name"):
            old_name = buggy.get("name")
            new_name = correct.get("name")
            if edit_logs is not None:
                edit_logs.append(
                    {
                        "type": "name_mismatch",
                        "buggy_expr": ast_to_code(buggy),
                        "correct_expr": ast_to_code(correct),
                        "detail": f"Variable name mismatch: expected '{new_name}', but found '{old_name}'",
                    }
                )
            buggy["name"] = correct["name"]
            repaired = True

    elif t == "VarAssignNode":
        if buggy.get("name") != correct.get("name"):
            old_name = buggy.get("name")
            new_name = correct.get("name")
            if edit_logs is not None:
                edit_logs.append(
                    {
                        "type": "name_mismatch",
                        "buggy_expr": ast_to_code(buggy),
                        "correct_expr": ast_to_code(correct),
                        "detail": f"Assignment variable name mismatch: expected '{new_name}', but found '{old_name}'",
                    }
                )
            buggy["name"] = correct["name"]
            repaired = True
        repaired = (
            diff_and_repair(buggy.get("value"), correct.get("value"), edit_logs)
            or repaired
        )

    elif t == "CallNode":
        repaired = (
            diff_and_repair(buggy.get("call"), correct.get("call"), edit_logs)
            or repaired
        )
        if len(buggy.get("args", [])) == len(correct.get("args", [])):
            for b_arg, c_arg in zip(buggy.get("args", []), correct.get("args", [])):
                repaired = diff_and_repair(b_arg, c_arg, edit_logs) or repaired

    elif t == "IfNode":
        if len(buggy.get("cases", [])) == len(correct.get("cases", [])):
            for b_case, c_case in zip(buggy.get("cases", []), correct.get("cases", [])):
                repaired = (
                    diff_and_repair(
                        b_case.get("condition"), c_case.get("condition"), edit_logs
                    )
                    or repaired
                )
                repaired = (
                    diff_and_repair(b_case.get("body"), c_case.get("body"), edit_logs)
                    or repaired
                )
        if buggy.get("else_case") and correct.get("else_case"):
            repaired = (
                diff_and_repair(
                    buggy["else_case"].get("body"),
                    correct["else_case"].get("body"),
                    edit_logs,
                )
                or repaired
            )

    elif t == "WhileNode":
        repaired = (
            diff_and_repair(buggy.get("condition"), correct.get("condition"), edit_logs)
            or repaired
        )
        repaired = (
            diff_and_repair(buggy.get("body"), correct.get("body"), edit_logs)
            or repaired
        )

    elif t == "ListNode":
        if len(buggy.get("program", [])) == len(correct.get("program", [])):
            for b_stmt, c_stmt in zip(
                buggy.get("program", []), correct.get("program", [])
            ):
                repaired = diff_and_repair(b_stmt, c_stmt, edit_logs) or repaired

    elif t == "DictionaryNode":
        if len(buggy.get("variables", [])) == len(correct.get("variables", [])):
            for b_var, c_var in zip(
                buggy.get("variables", []), correct.get("variables", [])
            ):
                repaired = diff_and_repair(b_var, c_var, edit_logs) or repaired

    return repaired


def find_reference_file(buggy_filename, directory):
    """Finds the corresponding correct reference solution file."""
    parts = buggy_filename.split("_")
    if len(parts) > 1:
        challenge_id = parts[1]
    else:
        challenge_id = buggy_filename.replace("b_", "").replace(".dap", "")

    for f in os.listdir(directory):
        if f.startswith("c_") and challenge_id in f:
            return os.path.join(directory, f)
    return None


def execute_dap_with_input(filepath, inputs):
    """Executes a DAP file with standard inputs and returns stdout."""
    dap_bin = get_dap_path()
    if not dap_bin:
        return "", "DAP bin missing"

    try:
        process = subprocess.Popen(
            [dap_bin, filepath],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        stdout, stderr = process.communicate(input="\n".join(inputs) + "\n", timeout=3)
        return stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        process.kill()
        return "", "Timeout"


def run_unit_tests(filepath, challenge_id):
    """Runs a hardcoded set of unit tests based on challenge ID."""
    if "l8" in challenge_id:
        # Challenge L8: Finding smallest number until -241231 is entered
        # test_cases = [
        #     {"input": ["10", "5", "8", "-241231"], "expected": "5"},
        #     {"input": ["-5", "-20", "-15", "-241231"], "expected": "-20"},
        #     {"input": ["-241231"], "expected": "NONE"}
        # ]
        test_cases = [{"input": ["4 5"], "expected": "20\n18"}]

        for case in test_cases:
            output, err = execute_dap_with_input(filepath, case["input"])

            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if "\n" in case["expected"]:
                val = "\n".join(lines)
            else:
                val = lines[-1] if lines else ""

            if val != case["expected"]:
                return (
                    False,
                    f"Test failed for inputs {case['input']}. Expected {case['expected']}, got {val}",
                )
        return True, "All tests passed!"

    return False, "Unknown challenge"


def repair_buggy_submissions(data_dir):
    """Reads P-Matrix, identifies buggy files, repairs them, and verifies with unit tests."""
    pmatrix_path = os.path.join(data_dir, "P-matrix-out.CSV")
    if not os.path.exists(pmatrix_path):
        print(f"P-Matrix file not found at {pmatrix_path}")
        return

    # Read buggy files from P-matrix
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
        buggy_path = os.path.join(data_dir, filename)
        if not os.path.exists(buggy_path):
            continue

        ref_path = find_reference_file(filename, data_dir)
        if not ref_path:
            print(f"Could not find reference template for {filename}. Skipping.")
            continue

        print(f"\n[INFO] Starting repair for: {filename}")
        print(f"[INFO] Using reference: {os.path.basename(ref_path)}")

        try:
            # Step 1: Parse ASTs
            buggy_ast = get_ast(buggy_path)
            correct_ast = get_ast(ref_path)

            # Step 2: In-place Repair on buggy AST
            edit_logs = []
            repaired = diff_and_repair(buggy_ast, correct_ast, edit_logs)

            if repaired:
                # Step 3: Pretty print AST back into DAP code
                repaired_code = ast_to_full_dap(
                    buggy_ast, program_name="Repaired_" + filename.replace(".dap", "")
                )

                # Write to corrected file
                corrected_path = os.path.join(data_dir, "repaired_" + filename)
                with open(corrected_path, "w") as f:
                    f.write(repaired_code)
                print(
                    f"[SUCCESS] Repaired code written to: {os.path.basename(corrected_path)}"
                )

                # Print helpful hints for student learning
                with open(buggy_path, "r") as f:
                    buggy_lines = f.readlines()

                print("\n" + "=" * 60)
                print("   AUTOMATED FEEDBACK - LOGIC HINTS FOR STUDENT")
                print("=" * 60)
                for log in edit_logs:
                    buggy_expr = log["buggy_expr"]
                    correct_expr = log["correct_expr"]
                    detail = log["detail"]

                    # Search for the buggy expression in the student's original lines
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
                        print(
                            f"  -> Your code:       {buggy_lines[line_num - 1].strip()}"
                        )
                        print(f"  -> Expected logic:  Contains '{correct_expr}'")
                    else:
                        print(f"* [General Hint]: {detail}")
                        print(f"  -> Original expression: '{buggy_expr}'")
                        print(f"  -> Expected logic:      '{correct_expr}'")
                print("=" * 60 + "\n")

                # Step 4: Verify with Unit Tests
                challenge_id = filename.split("_")[1] if "_" in filename else filename
                passed, msg = run_unit_tests(corrected_path, challenge_id)
                if passed:
                    print(
                        f"[VERIFIED] {os.path.basename(corrected_path)} passed all unit tests!"
                    )
                else:
                    print(
                        f"[FAILED VERIFICATION] {os.path.basename(corrected_path)}: {msg}"
                    )
            else:
                print(f"[INFO] No AST structural differences found for {filename}.")

        except Exception as e:
            print(f"[ERROR] Failed to repair {filename}: {e}")


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(current_dir, "data")
    repair_buggy_submissions(data_dir)
