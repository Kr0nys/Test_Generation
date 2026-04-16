import ast
import os
from typing import List, Dict


class CodeAnalyzer:
    def detect_dependencies(self, file_paths: List[str]) -> List[str]:
        import_to_package = {
            'requests': 'requests', 'numpy': 'numpy', 'pandas': 'pandas',
            'flask': 'flask', 'django': 'django', 'pytest': 'pytest',
            'celery': 'celery', 'redis': 'redis', 'sqlalchemy': 'sqlalchemy',
        }
        dependencies = set()
        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            pkg = import_to_package.get(alias.name.split('.')[0])
                            if pkg:
                                dependencies.add(pkg)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            pkg = import_to_package.get(node.module.split('.')[0])
                            if pkg:
                                dependencies.add(pkg)
            except:
                continue
        return list(dependencies)

    def analyze_code(self, file_paths: List[str]) -> Dict:
        all_functions, all_classes, all_imports = [], [], set()
        total_lines, total_files = 0, 0

        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    total_lines += len(content.splitlines())
                tree = ast.parse(content)
                total_files += 1

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        all_functions.append({
                            'name': node.name, 'file': os.path.basename(file_path),
                            'line': node.lineno, 'async': isinstance(node, ast.AsyncFunctionDef)
                        })
                    elif isinstance(node, ast.ClassDef):
                        all_classes.append(
                            {'name': node.name, 'file': os.path.basename(file_path), 'line': node.lineno})
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            all_imports.add(alias.name)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        all_imports.add(node.module)
            except:
                continue

        async_count = len([f for f in all_functions if f['async']])
        report = f"=== ОТЧЕТ ===\nФайлов: {total_files}\nСтрок: {total_lines}\nФункций: {len(all_functions)}\nКлассов: {len(all_classes)}\nИмпортов: {len(all_imports)}"

        return {
            'metrics': {
                'files_count': total_files, 'lines_count': total_lines,
                'functions_count': len(all_functions), 'classes_count': len(all_classes),
                'imports_count': len(all_imports), 'async_functions': async_count,
                'functions': all_functions, 'classes': all_classes, 'imports': list(all_imports)
            },
            'report': report
        }