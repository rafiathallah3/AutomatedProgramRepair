# Automated Program Repair for DAP

This project implements an Automated Program Repair (APR) pipeline and analysis tool for the DAP programming language. It is designed to analyze student submissions, perform AST-based diffing against reference solutions to diagnose programming concept failures, generate automated hints, and rebuild personalized P-matrices for Deep Knowledge Tracing (DKT) models.

## Project Overview

The APR toolchain parses student submissions and correct solutions using the DAP compiler, compares their Abstract Syntax Trees (ASTs), and performs the following actions:
1. **Error Diagnostics**: Identifies structural, operator, variable, and value mismatches.
2. **Concept Mapping**: Maps detected errors back to 10 core cognitive concepts (Knowledge Components) defined in the syllabus.
3. **Automated Feedback**: Generates precise line-by-line logical hints indicating what was expected versus what was written.
4. **P-Matrix Generation**: Updates the `P-matrix-out.CSV` file used by the HELP-DKT neural model for continuous student mastery estimation.
5. **Web Interface**: Provides a Flask-based GUI to load buggy files, run analysis, view AST structures, and preview repaired code interactively.

## Prerequisites

Before running the project, you must install the DAP compiler and Python dependencies.

### 1. Download and Install the DAP Compiler

The APR pipeline depends on the DAP compiler to generate JSON representations of the program ASTs.

1. Clone or download the DAP compiler repository:
   https://github.com/rafiathallah3/dap

2. Build the DAP compiler according to its installation instructions.

3. Place the compiled binary (`dap.exe` on Windows or `dap` on macOS/Linux) in one of the following locations so the repair pipeline can detect it:
   - In your system PATH (so executing `dap` in a terminal runs the compiler).
   - At the path `~/.dap/bin/dap.exe` (where `~` is your user profile directory, e.g., `C:\Users\<username>\.dap\bin\dap.exe` on Windows).
   - At the explicit path `C:/Users/<username>/.dap/bin/dap.exe`.

### 2. Python Environment

Ensure Python 3.8 or higher is installed. The web interface requires Flask.

## Installation

1. Clone or navigate to the project directory:
   ```bash
   cd Automated_Program_Repair
   ```

2. Install the required Python packages:
   ```bash
   pip install Flask
   ```

## Project Directory Structure

- `app.py`: Flask web application serving the visualization interface and API endpoints.
- `repair.py`: Core logic for AST parsing, recursive AST diffing, program repair, hint generation, and unit testing.
- `build_pmatrix.py`: Logic to scan submissions, run AST diffs, detect failed concepts, and generate `P-matrix-out.CSV`.
- `test_repair.py`: Unittest suite verifying the AST compilation, diff-and-repair mechanisms, and code generation.
- `data/`: Contains student buggy submissions (`b_*.dap`), reference solutions (`c_*.dap`), the generated P-matrix file, and temporary repaired outputs.
- `templates/`: Contains HTML files for the web interface.
  - `templates/index.html`: Web interface for visual inspection and interactive repair.

## How to Run

### 1. Running the Flask Web Application

To launch the web interface where you can interactively select, analyze, and repair student submissions:

1. Execute the Flask app:
   ```bash
   python app.py
   ```

2. Open your web browser and navigate to:
   http://127.0.0.1:5000

3. Through the dashboard, you can:
   - Browse student submissions located in the `data/` folder.
   - Run live analysis to generate repaired code.
   - Inspect the original buggy AST versus the reference AST.
   - Read generated line-number-matched hints.
   - View the updated mastery score and personalized concept vector.

### 2. Running the P-Matrix Generator Script

To scan all submissions in the `data/` folder, run AST comparisons, and write the concept success/failure vectors to the P-matrix CSV:

1. Run the script:
   ```bash
   python build_pmatrix.py
   ```

2. This reads the base Q-Matrix mappings and outputs the result into `data/P-matrix-out.CSV`.

### 3. Running the Automated Repair Script

To run the repair pipeline directly in the terminal, logging feedback logs and testing corrected code:

1. Run the script:
   ```bash
   python repair.py
   ```

2. The script will output detailed feedback console messages for buggy files and attempt to verify them with unit tests.

### 4. Running the Tests

To verify that AST parsing, code generation, and repair routines are working properly:

1. Run the test suite:
   ```bash
   python -m unittest test_repair.py
   ```

## Concept Mapping System

The pipeline maps syntax differences to 10 key programming concepts (Knowledge Components) represented as columns in the Q-matrix and P-matrix:
1. Basic variables and outputs
2. Basic input and arithmetic operations
3. Conditionals and comparison branching (if-else statements)
4. Iteration and loops (while/for loops)
5. Modulo arithmetic (even-odd checks)
6. Arrays and lists (sequence data structures)
7. Tuples
8. Dictionaries (key-value maps)
9. Factorial calculation
10. Complex logic (sorting algorithms, functions)

During evaluation, any detected operator, variable, value, or structural mismatch within a specific AST node context (e.g., inside an IfNode or WhileNode) flips the student's mastery flag from 1 to 0 for the corresponding concept.
