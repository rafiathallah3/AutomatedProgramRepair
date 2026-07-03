import csv
from pathlib import Path
from typing import Any
import repair_go

# A mapping of challenge IDs to their base required concepts in the Q-Matrix
CHALLENGE_CONCEPTS: dict[str, list[int]] = {
    "l8": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],   # Concepts 1-5 required
    "362": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
    "371": [0, 1, 1, 1, 1, 1, 1, 0, 0, 0],
}


def load_qmatrix(qmatrix_path: str | Path) -> None:
    """Loads default Q-Matrix values from problemQmatrix.CSV if available."""
    q_path = Path(qmatrix_path)
    if q_path.exists():
        with open(q_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                challenge_id = row[0]
                vector = [int(x) for x in row[1:]]
                CHALLENGE_CONCEPTS[challenge_id] = vector



def detect_ast_failures(buggy, correct, current_context=None):
    """
    Recursively diffs buggy AST with correct AST to determine which concept failed.
    Returns a set of failed concept indices (1-indexed).
    """
    failed_concepts = set()
    if not isinstance(buggy, dict) or not isinstance(correct, dict):
        return failed_concepts

    t = buggy.get("type")

    context = current_context
    if t in ["IfNode", "WhileNode", "ForNode", "DictionaryNode"]:
        context = t

    # 1. Structural type mismatch
    if buggy.get("type") != correct.get("type"):
        if context == "IfNode":
            failed_concepts.add(3) # Conditionals
        elif context == "WhileNode" or context == "ForNode":
            failed_concepts.add(4) # Loops/Iteration
        elif context == "DictionaryNode":
            failed_concepts.add(1) # Variables
        return failed_concepts

    # 2. Key value mismatches
    if t == "BinOpNode":
        if buggy.get("operator") != correct.get("operator"):
            if context == "IfNode":
                failed_concepts.add(3)
            elif context == "WhileNode":
                failed_concepts.add(4)
        failed_concepts.update(detect_ast_failures(buggy.get("left"), correct.get("left"), context))
        failed_concepts.update(detect_ast_failures(buggy.get("right"), correct.get("right"), context))

    elif t == "NumberNode" or t == "StringNode":
        if buggy.get("value") != correct.get("value"):
            if context == "IfNode":
                failed_concepts.add(3)
            elif context == "WhileNode":
                failed_concepts.add(4)

    elif t == "VarAccessToken":
        if buggy.get("name") != correct.get("name"):
            if context == "DictionaryNode":
                failed_concepts.add(1)

    elif t == "VarAssignNode":
        if buggy.get("name") != correct.get("name"):
            failed_concepts.add(1)
        failed_concepts.update(detect_ast_failures(buggy.get("value"), correct.get("value"), context))

    elif t == "CallNode":
        failed_concepts.update(detect_ast_failures(buggy.get("call"), correct.get("call"), context))
        if len(buggy.get("args", [])) == len(correct.get("args", [])):
            for b_arg, c_arg in zip(buggy.get("args", []), correct.get("args", [])):
                failed_concepts.update(detect_ast_failures(b_arg, c_arg, context))

    elif t == "IfNode":
        if len(buggy.get("cases", [])) == len(correct.get("cases", [])):
            for b_case, c_case in zip(buggy.get("cases", []), correct.get("cases", [])):
                failed_concepts.update(detect_ast_failures(b_case.get("condition"), c_case.get("condition"), "IfNode"))
                failed_concepts.update(detect_ast_failures(b_case.get("body"), c_case.get("body"), "IfNode"))
        else:
            failed_concepts.add(3)

    elif t == "WhileNode":
        failed_concepts.update(detect_ast_failures(buggy.get("condition"), correct.get("condition"), "WhileNode"))
        failed_concepts.update(detect_ast_failures(buggy.get("body"), correct.get("body"), "WhileNode"))

    elif t == "ListNode":
        b_list = buggy.get("program", [])
        c_list = correct.get("program", [])
        matches, _, _ = repair_go.align_lists(b_list, c_list)
        
        # Check matched statements
        matched_c = set()
        for b_idx, c_idx in matches:
            failed_concepts.update(detect_ast_failures(b_list[b_idx], c_list[c_idx], context))
            matched_c.add(c_idx)
            
        # Any missing statement maps to a general concept failure in context (e.g. Iteration/Loops)
        if len(matched_c) < len(c_list):
            if context == "IfNode":
                failed_concepts.add(3)
            elif context == "WhileNode" or context == "ForNode":
                failed_concepts.add(4)
            else:
                failed_concepts.add(1) # fallback

    elif t == "DictionaryNode":
        if len(buggy.get("variables", [])) == len(correct.get("variables", [])):
            for b_var, c_var in zip(buggy.get("variables", []), correct.get("variables", [])):
                failed_concepts.update(detect_ast_failures(b_var, c_var, "DictionaryNode"))
        else:
            failed_concepts.add(1)

    return failed_concepts


def generate_pmatrix_file(data_dir: str | Path, qmatrix_path: str | Path) -> None:
    """Automatically constructs P-matrix-out-go.CSV based on folder submissions and AST diffs."""
    load_qmatrix(qmatrix_path)

    pmatrix_rows = []
    data_path = Path(data_dir)

    # Scan all Go files in directory
    files = []
    if data_path.exists() and data_path.is_dir():
        for f in data_path.iterdir():
            if f.is_file() and f.name.endswith(".go") and not f.name.startswith("repaired_") and not f.name.startswith("temp_repaired_"):
                files.append(f.name)

    for filename in files:
        parts = filename.split("_")
        challenge_id = parts[1] if len(parts) > 1 else filename.replace("b_", "").replace("c_", "").replace(".go", "")

        base_vector = CHALLENGE_CONCEPTS.get(challenge_id, [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]).copy()

        if filename.startswith("c_"):
            pmatrix_rows.append([filename] + [str(x) for x in base_vector])
            print(f"[BUILD GO] {filename} is correct -> Base vector: {base_vector}")

        elif filename.startswith("b_"):
            buggy_path = data_path / filename
            ref_paths = repair_go.find_reference_files(filename, data_path)

            if not ref_paths:
                print(f"[WARN GO] No correct reference template found for buggy code {filename}. Defaulting to base vector.")
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                continue

            try:
                buggy_ast = repair_go.get_ast(buggy_path)

                best_failures = None
                best_ref_path = None

                for ref_path in ref_paths:
                    ref_path_obj = Path(ref_path)
                    correct_ast = repair_go.get_ast(ref_path_obj)
                    failures = detect_ast_failures(buggy_ast, correct_ast)
                    if best_failures is None or len(failures) < len(best_failures):
                        best_failures = failures
                        best_ref_path = ref_path_obj

                failures = best_failures
                print(f"[BUILD GO] {filename}: chose reference {best_ref_path.name} with {len(failures)} failures.")

                for f_concept in failures:
                    concept_index = f_concept - 1
                    if concept_index < len(base_vector):
                        base_vector[concept_index] = 0

                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                print(f"[BUILD GO] {filename} is buggy -> Derived vector: {base_vector} (failures: {list(failures)})")

            except Exception as e:
                print(f"[ERROR GO] Failed to compile AST for {filename}: {e}. Writing base vector.")
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])

    pmatrix_path = data_path / "P-matrix-out-go.CSV"
    with open(pmatrix_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(pmatrix_rows)
    print(f"\n[SUCCESS GO] Generated and saved Go P-Matrix to: {pmatrix_path}")


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    data_dir = current_dir / "data"
    qmatrix_path = current_dir.parent.parent / "HELP-DKT" / "Code_HELP_DKT" / "data" / "ModelInput" / "problemQmatrix.CSV"
    generate_pmatrix_file(data_dir, qmatrix_path)

