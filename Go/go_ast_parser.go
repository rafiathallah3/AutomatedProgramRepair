package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage: go run go_ast_parser.go <file.go>")
		os.Exit(1)
	}
	fset := token.NewFileSet()
	node, err := parser.ParseFile(fset, os.Args[1], nil, parser.ParseComments)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing file: %v\n", err)
		os.Exit(1)
	}

	// Find the main function
	var mainFunc *ast.FuncDecl
	for _, decl := range node.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == "main" {
			mainFunc = fn
			break
		}
	}

	if mainFunc == nil {
		fmt.Fprintf(os.Stderr, "No main function found\n")
		os.Exit(1)
	}

	simplified := translateBlock(mainFunc.Body)
	bytes, _ := json.MarshalIndent(simplified, "", "  ")
	fmt.Println(string(bytes))
}

func translateNode(n ast.Node) interface{} {
	if n == nil {
		return nil
	}
	switch expr := n.(type) {
	case *ast.BlockStmt:
		return translateBlock(expr)
	case *ast.DeclStmt:
		return translateDecl(expr)
	case *ast.AssignStmt:
		return translateAssign(expr)
	case *ast.ExprStmt:
		return translateNode(expr.X)
	case *ast.BinaryExpr:
		return map[string]interface{}{
			"type":     "BinOpNode",
			"left":     translateNode(expr.X),
			"operator": expr.Op.String(),
			"right":    translateNode(expr.Y),
		}
	case *ast.BasicLit:
		t := "NumberNode"
		if expr.Kind == token.STRING {
			t = "StringNode"
		}
		return map[string]interface{}{
			"type":  t,
			"value": expr.Value,
		}
	case *ast.Ident:
		return map[string]interface{}{
			"type": "VarAccessToken",
			"name": expr.Name,
		}
	case *ast.CallExpr:
		return translateCall(expr)
	case *ast.IfStmt:
		return translateIf(expr)
	case *ast.ForStmt:
		cond := translateNode(expr.Cond)
		if cond == nil {
			cond = map[string]interface{}{
				"type":  "NumberNode",
				"value": "true",
			}
		}
		return map[string]interface{}{
			"type":      "WhileNode",
			"condition": cond,
			"body":      translateNode(expr.Body),
		}
	case *ast.UnaryExpr:
		return map[string]interface{}{
			"type":     "UnaryOpNode",
			"operator": expr.Op.String(),
			"node":     translateNode(expr.X),
		}
	case *ast.ParenExpr:
		return translateNode(expr.X)
	}
	return nil
}

func translateBlock(block *ast.BlockStmt) interface{} {
	var program []interface{}
	for _, stmt := range block.List {
		node := translateNode(stmt)
		if node != nil {
			// If it's a list (e.g. from multiple assignments), flatten it
			if listNode, ok := node.(map[string]interface{}); ok && listNode["type"] == "ListNode" {
				if subProgram, ok := listNode["program"].([]interface{}); ok {
					program = append(program, subProgram...)
					continue
				}
			}
			program = append(program, node)
		}
	}
	return map[string]interface{}{
		"type":    "ListNode",
		"program": program,
	}
}

func translateDecl(stmt *ast.DeclStmt) interface{} {
	genDecl, ok := stmt.Decl.(*ast.GenDecl)
	if !ok || genDecl.Tok != token.VAR {
		return nil
	}
	var variables []interface{}
	for _, spec := range genDecl.Specs {
		valueSpec, ok := spec.(*ast.ValueSpec)
		if !ok {
			continue
		}
		typeName := ""
		if valueSpec.Type != nil {
			if ident, ok := valueSpec.Type.(*ast.Ident); ok {
				typeName = ident.Name
			}
		}
		if typeName == "" {
			typeName = "integer" // default
		}
		for _, name := range valueSpec.Names {
			variables = append(variables, map[string]interface{}{
				"name":     name.Name,
				"is_const": false,
				"value": map[string]interface{}{
					"name": typeName,
				},
			})
		}
	}
	return map[string]interface{}{
		"type":      "DictionaryNode",
		"variables": variables,
	}
}

func translateAssign(stmt *ast.AssignStmt) interface{} {
	if len(stmt.Lhs) == 1 && len(stmt.Rhs) == 1 {
		lhsIdent, ok := stmt.Lhs[0].(*ast.Ident)
		if ok {
			return map[string]interface{}{
				"type":  "VarAssignNode",
				"name":  lhsIdent.Name,
				"value": translateNode(stmt.Rhs[0]),
			}
		}
	}
	var program []interface{}
	for i := 0; i < len(stmt.Lhs); i++ {
		lhsIdent, ok := stmt.Lhs[i].(*ast.Ident)
		if ok {
			program = append(program, map[string]interface{}{
				"type":  "VarAssignNode",
				"name":  lhsIdent.Name,
				"value": translateNode(stmt.Rhs[i]),
			})
		}
	}
	return map[string]interface{}{
		"type":    "ListNode",
		"program": program,
	}
}

func translateCall(expr *ast.CallExpr) interface{} {
	funName := ""
	if selExpr, ok := expr.Fun.(*ast.SelectorExpr); ok {
		if pkgIdent, ok := selExpr.X.(*ast.Ident); ok && pkgIdent.Name == "fmt" {
			funName = selExpr.Sel.Name
		}
	} else if ident, ok := expr.Fun.(*ast.Ident); ok {
		funName = ident.Name
	}

	var args []interface{}
	for _, arg := range expr.Args {
		if unary, ok := arg.(*ast.UnaryExpr); ok && unary.Op == token.AND {
			args = append(args, translateNode(unary.X))
		} else {
			args = append(args, translateNode(arg))
		}
	}

	switch funName {
	case "Println", "Print":
		return map[string]interface{}{
			"type": "CallNode",
			"call": map[string]interface{}{
				"name": "write",
			},
			"args": args,
		}
	case "Scan", "Scanf":
		return map[string]interface{}{
			"type": "CallNode",
			"call": map[string]interface{}{
				"name": "read",
			},
			"args": args,
		}
	}

	return map[string]interface{}{
		"type": "CallNode",
		"call": map[string]interface{}{
			"name": funName,
		},
		"args": args,
	}
}

func translateIf(stmt *ast.IfStmt) interface{} {
	var cases []interface{}
	cases = append(cases, map[string]interface{}{
		"condition": translateNode(stmt.Cond),
		"body":      translateNode(stmt.Body),
	})

	var elseCase interface{}
	if stmt.Else != nil {
		elseBody := translateNode(stmt.Else)
		if elseIf, ok := stmt.Else.(*ast.IfStmt); ok {
			nestedIf := translateIf(elseIf).(map[string]interface{})
			nestedCases := nestedIf["cases"].([]interface{})
			cases = append(cases, nestedCases...)
			if nestedIf["else_case"] != nil {
				elseCase = nestedIf["else_case"]
			}
		} else {
			elseCase = map[string]interface{}{
				"body": elseBody,
			}
		}
	}

	result := map[string]interface{}{
		"type":  "IfNode",
		"cases": cases,
	}
	if elseCase != nil {
		result["else_case"] = elseCase
	}
	return result
}
