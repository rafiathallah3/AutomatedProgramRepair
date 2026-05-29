import os
import csv
import json
import subprocess
import repair

# A mapping of challenge IDs to their base required concepts in the Q-Matrix
CHALLENGE_CONCEPTS = {
    "l8": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],   # Concepts 1-5 required
    "362": [1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
    "371": [0, 1, 1, 1, 1, 1, 1, 0, 0, 0],
}

def load_qmatrix(qmatrix_path):
    """Loads default Q-Matrix values from problemQmatrix.CSV if available."""
    if os.path.exists(qmatrix_path):
        with open(qmatrix_path, "r") as f:
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
        if len(buggy.get("program", [])) == len(correct.get("program", [])):
            for b_stmt, c_stmt in zip(buggy.get("program", []), correct.get("program", [])):
                failed_concepts.update(detect_ast_failures(b_stmt, c_stmt, context))
                
    elif t == "DictionaryNode":
        if len(buggy.get("variables", [])) == len(correct.get("variables", [])):
            for b_var, c_var in zip(buggy.get("variables", []), correct.get("variables", [])):
                failed_concepts.update(detect_ast_failures(b_var, c_var, "DictionaryNode"))
        else:
            failed_concepts.add(1)
            
    return failed_concepts

def generate_pmatrix_file(data_dir, qmatrix_path):
    """Automatically constructs P-matrix-out.CSV based on folder submissions and AST diffs."""
    load_qmatrix(qmatrix_path)
    
    pmatrix_rows = []
    
    # Scan all files in directory
    files = [f for f in os.listdir(data_dir) if f.endswith(".dap") and not f.startswith("repaired_")]
    
    for filename in files:
        # Determine challenge id
        parts = filename.split("_")
        challenge_id = parts[1] if len(parts) > 1 else filename.replace("b_", "").replace("c_", "").replace(".dap", "")
        
        # Get base concepts vector from Q-matrix
        base_vector = CHALLENGE_CONCEPTS.get(challenge_id, [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]).copy()
        
        if filename.startswith("c_"):
            # Correct files keep all base required concepts as 1
            pmatrix_rows.append([filename] + [str(x) for x in base_vector])
            print(f"[BUILD] {filename} is correct -> Base vector: {base_vector}")
            
        elif filename.startswith("b_"):
            # Buggy files need AST comparison to identify concept failures
            buggy_path = os.path.join(data_dir, filename)
            ref_path = repair.find_reference_file(filename, data_dir)
            
            if not ref_path:
                print(f"[WARN] No correct reference template found for buggy code {filename}. Defaulting to base vector.")
                pmatrix_rows.append([filename] + [str(x) for x in base_vector])
                continue
                
            try:
                buggy_ast = repair.get_ast(buggy_path)
                correct_ast = repair.get_ast(ref_path)
                
                # Detect which concept index (1-indexed) failed
                failures = detect_ast_failures(buggy_ast, correct_ast)
                
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
    pmatrix_path = os.path.join(data_dir, "P-matrix-out.CSV")
    with open(pmatrix_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(pmatrix_rows)
    print(f"\n[SUCCESS] Generated and saved P-Matrix to: {pmatrix_path}")

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(current_dir, "data")
    qmatrix_path = os.path.join(current_dir, "..", "HELP-DKT", "Code_HELP_DKT", "data", "ModelInput", "problemQmatrix.CSV")
    generate_pmatrix_file(data_dir, qmatrix_path)
