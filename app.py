import os
import json
import csv
import copy
from flask import Flask, render_template, jsonify, request

import repair
import build_pmatrix

app = Flask(__name__)

# Make sure Q-Matrix and P-Matrix are loaded / configured
current_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(current_dir, "data")
qmatrix_path = os.path.join(current_dir, "..", "HELP-DKT", "Code_HELP_DKT", "data", "ModelInput", "problemQmatrix.CSV")
build_pmatrix.load_qmatrix(qmatrix_path)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/submissions")
def get_submissions():
    """Scans data directory for student buggy files."""
    try:
        submissions = []
        for f in os.listdir(data_dir):
            if f.startswith("b_") and f.endswith(".dap"):
                challenge_id = f.split("_")[1] if "_" in f else "l8"
                submissions.append({
                    "filename": f,
                    "challenge_id": challenge_id
                })
        return jsonify({"success": True, "submissions": submissions})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/analyze")
def analyze():
    """Runs repair and AST diff on the selected student submission."""
    filename = request.args.get("filename")
    if not filename:
        return jsonify({"success": False, "error": "Missing filename parameter"}), 400
        
    buggy_path = os.path.join(data_dir, filename)
    if not os.path.exists(buggy_path):
        return jsonify({"success": False, "error": f"File {filename} not found"}), 404
        
    ref_path = repair.find_reference_file(filename, data_dir)
    if not ref_path:
        return jsonify({"success": False, "error": f"No reference solution found for {filename}"}), 404
        
    try:
        # Read raw code strings
        with open(buggy_path, "r", encoding="utf-8") as f:
            buggy_code = f.read()
        with open(ref_path, "r", encoding="utf-8") as f:
            correct_code = f.read()
            
        # Parse ASTs
        buggy_ast = repair.get_ast(buggy_path)
        correct_ast = repair.get_ast(ref_path)
        
        # Keep copy of original buggy AST for visualization
        buggy_ast_original = copy.deepcopy(buggy_ast)
        
        # Run diff and repair on a copy
        repaired_ast = copy.deepcopy(buggy_ast)
        edit_logs = []
        # Run diff_and_repair which updates repaired_ast in-place
        repaired = repair.diff_and_repair(repaired_ast, correct_ast, edit_logs)
        
        # Pretty print repaired code
        challenge_id = filename.split("_")[1] if "_" in filename else "l8"
        repaired_code = repair.ast_to_full_dap(repaired_ast, program_name="Repaired_" + filename.replace(".dap", ""))
        
        # Calculate concept failures and DKT metrics
        failed_concepts = build_pmatrix.detect_ast_failures(buggy_ast_original, correct_ast)
        
        # Fetch base Q-matrix concepts required for this challenge
        base_id = challenge_id.lower()
        if base_id not in build_pmatrix.CHALLENGE_CONCEPTS:
            # Default fallback mapping
            required_concepts = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
        else:
            required_concepts = build_pmatrix.CHALLENGE_CONCEPTS[base_id]
            
        # Determine student personalized concept vector
        personalized_vector = []
        for idx, req in enumerate(required_concepts):
            if req == 0:
                personalized_vector.append(0)
            else:
                if (idx + 1) in failed_concepts:
                    personalized_vector.append(0)
                else:
                    personalized_vector.append(1)
                    
        # Compute Step A mastery score
        problemConNum = sum(required_concepts)
        personalizedConNum = sum(personalized_vector)
        score = float(personalizedConNum / problemConNum) if problemConNum > 0 else 0.0
        
        # Resolve edit logs to line numbers in the student's code
        resolved_hints = []
        buggy_lines = buggy_code.splitlines()
        for log in edit_logs:
            buggy_expr = log["buggy_expr"]
            correct_expr = log["correct_expr"]
            detail = log["detail"]
            
            line_num = None
            norm_expr = buggy_expr.lower().replace(" ", "").replace("(", "").replace(")", "")
            if norm_expr:
                for idx, line in enumerate(buggy_lines):
                    norm_line = line.lower().replace(" ", "").replace("(", "").replace(")", "")
                    if norm_expr in norm_line:
                        line_num = idx + 1
                        break
            
            resolved_hints.append({
                "line_num": line_num,
                "detail": detail,
                "buggy_expr": buggy_expr,
                "correct_expr": correct_expr,
                "original_line": buggy_lines[line_num - 1].strip() if line_num else None
            })
            
        return jsonify({
            "success": True,
            "filename": filename,
            "challenge_id": challenge_id,
            "buggy_code": buggy_code,
            "correct_code": correct_code,
            "repaired_code": repaired_code,
            "buggy_ast": buggy_ast_original,
            "correct_ast": correct_ast,
            "hints": resolved_hints,
            "required_concepts": required_concepts,
            "personalized_vector": personalized_vector,
            "failed_concepts": list(failed_concepts),
            "score": score
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
