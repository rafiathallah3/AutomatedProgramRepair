import copy
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any
from flask import Flask, render_template, jsonify, request

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Add subdirectories to sys.path for importing DAP and Go engines
current_dir = Path(__file__).parent
dap_dir = current_dir / "DAP"
go_dir = current_dir / "Go"
sys.path.insert(0, str(dap_dir))
sys.path.insert(0, str(go_dir))

import repair
import build_pmatrix
import repair_go
import build_pmatrix_go

app = Flask(__name__)

# Make sure Q-Matrix and P-Matrix are loaded / configured
qmatrix_path = current_dir / ".." / "HELP-DKT" / "Code_HELP_DKT" / "data" / "ModelInput" / "problemQmatrix.CSV"
build_pmatrix.load_qmatrix(qmatrix_path)
build_pmatrix_go.load_qmatrix(qmatrix_path)


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/submissions")
def get_submissions() -> Any:
    """Scans data directory for student buggy files based on selected language."""
    lang = request.args.get("lang", "dap").lower()
    if lang == "go":
        lang_data_dir = go_dir / "data"
        ext = ".go"
    else:
        lang_data_dir = dap_dir / "data"
        ext = ".dap"

    try:
        submissions = []
        if lang_data_dir.exists() and lang_data_dir.is_dir():
            for f in lang_data_dir.iterdir():
                if f.is_file() and f.name.startswith("b_") and f.name.endswith(ext):
                    challenge_id = f.name.split("_")[1] if "_" in f.name else "l8"
                    submissions.append({
                        "filename": f.name,
                        "challenge_id": challenge_id
                    })
        return jsonify({"success": True, "submissions": submissions})
    except Exception as e:
        logger.error(f"Error listing submissions: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analyze")
def analyze() -> Any:
    """Runs repair and AST diff on the selected student submission."""
    filename = request.args.get("filename")
    lang = request.args.get("lang", "dap").lower()
    if not filename:
        return jsonify({"success": False, "error": "Missing filename parameter"}), 400
        
    if lang == "go":
        lang_data_dir = go_dir / "data"
        repair_mod = repair_go
        pmatrix_mod = build_pmatrix_go
    else:
        lang_data_dir = dap_dir / "data"
        repair_mod = repair
        pmatrix_mod = build_pmatrix
        
    buggy_path = lang_data_dir / filename
    if not buggy_path.exists():
        return jsonify({"success": False, "error": f"File {filename} not found"}), 404
        
    ref_path = repair_mod.find_reference_file(filename, lang_data_dir)
    if not ref_path:
        return jsonify({"success": False, "error": f"No reference solution found for {filename}"}), 404
        
    try:
        ref_path_obj = Path(ref_path)
        # Read raw code strings
        with open(buggy_path, "r", encoding="utf-8") as f:
            buggy_code = f.read()
        with open(ref_path_obj, "r", encoding="utf-8") as f:
            correct_code = f.read()
            
        # Parse ASTs
        buggy_ast = repair_mod.get_ast(buggy_path)
        correct_ast = repair_mod.get_ast(ref_path_obj)
        
        # Keep copy of original buggy AST for visualization
        buggy_ast_original = copy.deepcopy(buggy_ast)
        
        # Run diff and repair on a copy
        repaired_ast = copy.deepcopy(buggy_ast)
        edit_logs = []
        # Run diff_and_repair which updates repaired_ast in-place
        repaired = repair_mod.diff_and_repair(repaired_ast, correct_ast, edit_logs)

        
        # Pretty print repaired code
        challenge_id = filename.split("_")[1] if "_" in filename else "l8"
        if lang == "go":
            repaired_code = repair_go.ast_to_full_go(repaired_ast)
        else:
            repaired_code = repair.ast_to_full_dap(repaired_ast, program_name="Repaired_" + filename.replace(".dap", ""))
        
        # Calculate concept failures and DKT metrics
        failed_concepts = pmatrix_mod.detect_ast_failures(buggy_ast_original, correct_ast)
        
        # Fetch base Q-matrix concepts required for this challenge
        base_id = challenge_id.lower()
        if base_id not in pmatrix_mod.CHALLENGE_CONCEPTS:
            required_concepts = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
        else:
            required_concepts = pmatrix_mod.CHALLENGE_CONCEPTS[base_id]
            
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
        
        # Resolve edit logs to line numbers in the student's code and gather misconceptions
        resolved_hints = []
        detected_misconceptions = []
        seen_misconceptions = set()
        buggy_lines = buggy_code.splitlines()
        for log in edit_logs:
            buggy_expr = log["buggy_expr"]
            correct_expr = log["correct_expr"]
            detail = log["detail"]
            misconception = log.get("misconception")
            
            line_num = None
            norm_expr = buggy_expr.lower().replace(" ", "").replace("(", "").replace(")", "")
            if norm_expr:
                for idx, line in enumerate(buggy_lines):
                    norm_line = line.lower().replace(" ", "").replace("(", "").replace(")", "")
                    if norm_expr in norm_line:
                        line_num = idx + 1
                        break
            
            hint_entry = {
                "line_num": line_num,
                "detail": detail,
                "buggy_expr": buggy_expr,
                "correct_expr": correct_expr,
                "original_line": buggy_lines[line_num - 1].strip() if line_num else None
            }
            
            if misconception:
                hint_entry["misconception"] = misconception
                m_code = misconception["code"]
                if m_code not in seen_misconceptions:
                    seen_misconceptions.add(m_code)
                    detected_misconceptions.append({
                        "code": m_code,
                        "title": misconception["title"],
                        "description": misconception["description"],
                        "line_num": line_num
                    })
            
            resolved_hints.append(hint_entry)
            
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
            "misconceptions": detected_misconceptions,
            "required_concepts": required_concepts,
            "personalized_vector": personalized_vector,
            "failed_concepts": list(failed_concepts),
            "score": score
        })
        
    except Exception as e:
        logger.exception("Error during submission analysis")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)

