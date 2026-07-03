import csv
from pathlib import Path
from typing import Any
import repair

# A mapping of challenge IDs to their base required concepts in the Q-Matrix
CHALLENGE_CONCEPTS: dict[str, list[int]] = {
    "l8": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],   # Concepts 1-5 required
    "362": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
    "371": [0, 1, 1, 1, 1, 1, 1, 0, 0, 0],
    "p1": [1, 1, 0, 1, 0, 0, 0, 0, 0, 0],   # Variables & Loop Concepts
    "p2": [1, 1, 1, 0, 0, 0, 0, 0, 0, 0],   # Variables & Conditional Concepts
    "p3": [1, 1, 0, 0, 0, 0, 0, 0, 0, 0],   # Variables & Calculation Concepts
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
    
    # Track syntax context to map to specific cognitive concepts
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
            # A comparison bug in an IF statement maps to Conditionals (Concept 3)
            if context == "IfNode":
                failed_concepts.add(3)
            # A comparison bug in a loop condition maps to Iteration (Concept 4)
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
        b_prog = buggy.get("program", [])
        c_prog = correct.get("program", [])
        # Align statements structurally so missing/extra statements are
        # detected instead of silently yielding a clean vector.
        matches, unmatched_b, unmatched_c = repair.align_lists(b_prog, c_prog)
        for b_idx, c_idx in matches:
            failed_concepts.update(detect_ast_failures(b_prog[b_idx], c_prog[c_idx], context))
        for idx_list, prog in ((unmatched_b, b_prog), (unmatched_c, c_prog)):
            for idx in idx_list:
                stmt = prog[idx]
                s_type = stmt.get("type") if isinstance(stmt, dict) else None
                if s_type == "IfNode":
                    failed_concepts.add(3)  # Conditionals
                elif s_type in ("WhileNode", "ForNode"):
                    failed_concepts.add(4)  # Loops/Iteration
                elif s_type == "VarAssignNode":
                    failed_concepts.add(1)  # Variables
                else:
                    failed_concepts.add(2)  # Sequence/Calculation
                
    elif t == "DictionaryNode":
        if len(buggy.get("variables", [])) == len(correct.get("variables", [])):
            for b_var, c_var in zip(buggy.get("variables", []), correct.get("variables", [])):
                failed_concepts.update(detect_ast_failures(b_var, c_var, "DictionaryNode"))
        else:
            failed_concepts.add(1)
            
    return failed_concepts

def generate_pmatrix_file(data_dir: str | Path, qmatrix_path: str | Path) -> None:
    """Automatically constructs P-matrix-out.CSV based on folder submissions and AST diffs."""
    load_qmatrix(qmatrix_path)
    
    pmatrix_rows = []
    data_path = Path(data_dir)
    
    # Scan all files in directory
    files = []
    if data_path.exists() and data_path.is_dir():
        for f in data_path.iterdir():
            if f.is_file() and f.name.endswith(".dap") and not f.name.startswith("repaired_") and not f.name.startswith("temp_repaired_"):
                files.append(f.name)
    
    for filename in files:
        challenge_id = repair.get_challenge_id(filename)
        
        # Get base concepts vector from Q-matrix
        base_vector = CHALLENGE_CONCEPTS.get(challenge_id, [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]).copy()
        
        if filename.startswith("c_"):
            # Correct files keep all base required concepts as 1
            pmatrix_rows.append([filename] + [str(x) for x in base_vector])
            print(f"[BUILD] {filename} is correct -> Base vector: {base_vector}")
            
        elif filename.startswith("b_"):
            # Buggy files need AST comparison to identify concept failures
            buggy_path = data_path / filename
            ref_paths = repair.find_reference_files(filename, data_path)
            
            if not ref_paths:
                print(f"[WARN] No correct reference template found for buggy code {filename}. Defaulting to base vector.")
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                continue
                
            try:
                buggy_ast = repair.get_ast(buggy_path)
                
                best_failures = None
                best_ref_path = None
                
                for ref_path in ref_paths:
                    ref_path_obj = Path(ref_path)
                    correct_ast = repair.get_ast(ref_path_obj)
                    failures = detect_ast_failures(buggy_ast, correct_ast)
                    if best_failures is None or len(failures) < len(best_failures):
                        best_failures = failures
                        best_ref_path = ref_path_obj
                
                failures = best_failures
                print(f"[BUILD] {filename}: chose reference {best_ref_path.name} with {len(failures)} failures.")
                
                # Flip mastery of failed concepts from 1 to 0
                for f_concept in failures:
                    concept_index = f_concept - 1  # convert to 0-indexed vector index
                    if concept_index < len(base_vector):
                        base_vector[concept_index] = 0
                        
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                print(f"[BUILD] {filename} is buggy -> Derived vector: {base_vector} (failures: {list(failures)})")
                
            except Exception as e:
                print(f"[ERROR] Failed to compile AST for {filename}: {e}. Writing base vector.")
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                
    # Save to P-matrix-out.CSV
    pmatrix_path = data_path / "P-matrix-out.CSV"
    with open(pmatrix_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(pmatrix_rows)
    print(f"\n[SUCCESS] Generated and saved P-Matrix to: {pmatrix_path}")


if __name__ == "__main__":
    current_dir = Path(__file__).parent
    data_dir = current_dir / "data"
    qmatrix_path = current_dir.parent.parent / "HELP-DKT" / "Code_HELP_DKT" / "data" / "ModelInput" / "problemQmatrix.CSV"
    generate_pmatrix_file(data_dir, qmatrix_path)

