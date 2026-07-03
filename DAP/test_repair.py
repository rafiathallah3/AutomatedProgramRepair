import unittest
import os
import repair

class TestDAPProgramRepair(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        cls.current_dir = os.path.dirname(os.path.abspath(__file__))
        cls.data_dir = os.path.join(cls.current_dir, "data")
        cls.buggy_file = os.path.join(cls.data_dir, "b_l8_student.dap")
        cls.ref_file = os.path.join(cls.data_dir, "c_l8_reference.dap")
        cls.repaired_file = os.path.join(cls.data_dir, "repaired_b_l8_student.dap")

    def tearDown(self):
        # Clean up generated repaired file if it exists
        if os.path.exists(self.repaired_file):
            try:
                os.remove(self.repaired_file)
            except OSError:
                pass

    def test_dap_path_retrieval(self):
        """Verifies that the DAP compiler path can be located."""
        dap_bin = repair.get_dap_path()
        self.assertIsNotNone(dap_bin, "DAP compiler binary could not be found.")

    def test_ast_generation(self):
        """Checks if the DAP compiler can output and parse JSON AST successfully."""
        ast_data = repair.get_ast(self.buggy_file)
        self.assertEqual(ast_data.get("type"), "ListNode")
        self.assertIn("program", ast_data)
        
    def test_ast_code_generation(self):
        """Checks if code generator correctly maps AST back to executable DAP code."""
        ast_data = repair.get_ast(self.ref_file)
        code = repair.ast_to_full_dap(ast_data, "TestProgram")
        self.assertTrue(code.startswith("program TestProgram"))
        self.assertTrue(code.endswith("endprogram"))
        self.assertIn("dictionary", code)
        self.assertIn("algorithm", code)

    def test_tree_repair_and_verification(self):
        """Tests end-to-end AST tree diffing, operator repair, code reconstruction, and test case execution."""
        # 1. Parse ASTs
        buggy_ast = repair.get_ast(self.buggy_file)
        correct_ast = repair.get_ast(self.ref_file)
        
        # 2. Repair tree
        repaired = repair.diff_and_repair(buggy_ast, correct_ast)
        self.assertTrue(repaired, "Repair engine failed to detect any difference to patch.")
        
        # 3. Code Generation
        repaired_code = repair.ast_to_full_dap(buggy_ast, "RepairedL8")
        with open(self.repaired_file, "w") as f:
            f.write(repaired_code)
            
        # 4. Verify compiler output & unit tests
        passed, msg = repair.run_unit_tests(self.repaired_file, "l8")
        self.assertTrue(passed, f"Repaired code failed unit tests: {msg}")
        print("\n[TEST EXECUTION] Unit test successfully repaired buggy comparison operator and verified code behavior.")

    def test_ast_normalization(self):
        """Verifies that single-use variables are inlined during normalization."""
        dummy_ast = {
            "type": "ListNode",
            "program": [
                {
                    "type": "VarAssignNode",
                    "name": "result",
                    "value": {"type": "NumberNode", "value": "42"}
                },
                {
                    "type": "CallNode",
                    "call": {"name": "write"},
                    "args": [{"type": "VarAccessToken", "name": "result"}]
                }
            ]
        }
        normalized = repair.normalize_ast(dummy_ast)
        # Check that the assignment was removed (ListNode program length becomes 1)
        self.assertEqual(len(normalized["program"]), 1)
        # Check that the write call now directly contains the number 42
        write_call = normalized["program"][0]
        self.assertEqual(write_call["type"], "CallNode")
        self.assertEqual(write_call["args"][0]["type"], "NumberNode")
        self.assertEqual(write_call["args"][0]["value"], "42")

    def test_find_reference_files(self):
        """Verifies that finding multiple reference files works as expected."""
        refs = repair.find_reference_files("b_l8_student.dap", self.data_dir)
        self.assertGreaterEqual(len(refs), 1)
        self.assertTrue(any("c_l8" in os.path.basename(r) for r in refs))

if __name__ == "__main__":
    unittest.main()
