import unittest
import os
import repair_go


class TestGoProgramRepair(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.current_dir = os.path.dirname(os.path.abspath(__file__))
        cls.data_dir = os.path.join(cls.current_dir, "data")
        cls.buggy_file = os.path.join(cls.data_dir, "b_l8_student.go")
        cls.ref_file = os.path.join(cls.data_dir, "c_l8_reference.go")
        cls.repaired_file = os.path.join(cls.data_dir, "repaired_b_l8_student.go")

    def tearDown(self):
        if os.path.exists(self.repaired_file):
            try:
                os.remove(self.repaired_file)
            except OSError:
                pass

    def test_go_ast_generation(self):
        """Checks if the Go AST can be retrieved and parsed into our unified JSON AST format."""
        ast_data = repair_go.get_ast(self.buggy_file, normalize=False)
        self.assertEqual(ast_data.get("type"), "ListNode")
        self.assertIn("program", ast_data)

    def test_go_ast_normalization(self):
        """Verifies that temporary variable inlining works for the Go AST."""
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
        normalized = repair_go.normalize_ast(dummy_ast)
        self.assertEqual(len(normalized["program"]), 1)
        write_call = normalized["program"][0]
        self.assertEqual(write_call["type"], "CallNode")
        self.assertEqual(write_call["args"][0]["type"], "NumberNode")
        self.assertEqual(write_call["args"][0]["value"], "42")

    def test_go_code_generation(self):
        """Checks if the Go code generator outputs correct package and main structures."""
        ast_data = repair_go.get_ast(self.ref_file, normalize=False)
        code = repair_go.ast_to_full_go(ast_data)
        self.assertTrue(code.startswith("package main"))
        self.assertIn("func main()", code)
        self.assertIn("fmt.Println", code)
        self.assertIn("fmt.Scan", code)

    def test_go_tree_repair_and_verification(self):
        """Tests end-to-end AST diffing, variable inlining, structural alignment, and Go program execution."""
        buggy_ast = repair_go.get_ast(self.buggy_file)
        correct_ast = repair_go.get_ast(self.ref_file)

        # Buggy and correct ASTs should normalize to inline variable assignments,
        # resulting in a simplified structure that aligns and repairs perfectly.
        edit_logs = []
        repaired = repair_go.diff_and_repair(buggy_ast, correct_ast, edit_logs)
        self.assertTrue(repaired)

        repaired_code = repair_go.ast_to_full_go(buggy_ast)
        with open(self.repaired_file, "w") as f:
            f.write(repaired_code)

        passed, msg = repair_go.run_unit_tests(self.repaired_file, "l8")
        self.assertTrue(passed, f"Repaired Go code failed tests: {msg}")


if __name__ == "__main__":
    unittest.main()
