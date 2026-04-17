import docker
import tempfile
import os
import json
import logging
import shutil
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class DockerRunner:
    """Запуск пользовательского кода в изолированном Docker-контейнере"""

    def __init__(self, timeout: int = 60):
        self.client = docker.from_env()
        self.timeout = timeout  # Максимальное время выполнения в секундах

    def run_analysis_container(
            self,
            file_paths: List[str],
            python_version: str = '3.9',
            dependencies: List[str] = None,
            run_tests: bool = False
    ) -> Dict:
        """
        Запускает анализ кода в изолированном контейнере

        Args:
            file_paths: Пути к файлам для анализа
            python_version: Версия Python (3.7-3.11)
            dependencies: Список зависимостей для установки
            run_tests: Запускать ли тесты после анализа

        Returns:
            Dict с результатами: статус, метрики, логи, ошибки
        """
        work_dir = None

        try:
            # Создаём временную рабочую директорию
            work_dir = tempfile.mkdtemp(prefix='analyzer_')
            logger.info(f"Created work directory: {work_dir}")

            # 1. Копируем файлы пользователя
            for file_path in file_paths:
                dest = os.path.join(work_dir, os.path.basename(file_path))
                logger.info(f"📁 Copying: {file_path} → {dest}")
                logger.info(f"   Source exists: {os.path.exists(file_path)}")
                logger.info(f"   Source size: {os.path.getsize(file_path)} bytes")

                shutil.copy2(file_path, dest)

                logger.info(f"   Dest exists: {os.path.exists(dest)}")
                logger.info(f"   Dest size: {os.path.getsize(dest)} bytes")

            logger.info(f"✅ Copied {len(file_paths)} files to {work_dir}")

            # 3. Показываем содержимое рабочей директории
            logger.info(f"📂 Work dir contents: {os.listdir(work_dir)}")

            # 2. Создаём requirements.txt
            if dependencies:
                req_path = os.path.join(work_dir, 'requirements.txt')
                with open(req_path, 'w', encoding='utf-8') as f:
                    # Фильтруем пустые строки и комментарии
                    valid_deps = [d.strip() for d in dependencies if d.strip() and not d.startswith('#')]
                    f.write('\n'.join(valid_deps))
                logger.debug(f"Created requirements.txt with {len(valid_deps)} dependencies")

            # 3. Создаём скрипт анализатора
            analyzer_script = self._create_simple_analyzer()
            analyzer_path = os.path.join(work_dir, '_analyzer.py')
            with open(analyzer_path, 'w', encoding='utf-8') as f:
                f.write(analyzer_script)

            # 4. Конфигурация контейнера с ограничениями безопасности
            container_config = {
                'image': f'python:{python_version}-slim',
                'volumes': {
                    work_dir: {'bind': '/app', 'mode': 'rw'},
                },
                'network_mode': 'none',  # 🔒 Нет доступа к сети
                'mem_limit': '512m',  # 🔒 Лимит памяти
                'cpu_quota': 50000,  # 🔒 50% одного ядра CPU
                'pids_limit': 50,  # 🔒 Лимит процессов
                'working_dir': '/app',
                'command': f'python _analyzer.py',
                'detach': True,
                'tty': False,
                'user': '1000:1000',  # 🔒 Запуск от non-root пользователя
                'read_only': False,  # Разрешаем запись для отчётов
                'tmpfs': {  # 🔒 Временные файлы в RAM
                    '/tmp': 'rw,noexec,nosuid,size=64m'
                },
                'cap_drop': ['ALL'],  # 🔒 Убираем все capabilities
                'security_opt': ['no-new-privileges:true'],  # 🔒 Запрет escalation
            }

            # 5. Запускаем контейнер
            logger.info(f"Starting container with image python:{python_version}-slim")
            container = self.client.containers.run(**container_config)

            logs = container.logs().decode('utf-8', errors='ignore')
            logger.info(f"📋 Container stdout:\n{logs}")

            # 5. Проверим результат
            results_path = os.path.join(work_dir, '_result.json')
            logger.info(f" Looking for results at: {results_path}")
            logger.info(f"   File exists: {os.path.exists(results_path)}")

            if os.path.exists(results_path):
                with open(results_path, 'r', encoding='utf-8') as f:
                    analysis_data = json.load(f)
                logger.info(f"✅ Results loaded: {analysis_data.get('metrics', {})}")
            else:
                logger.warning(f"❌ Results file NOT found!")
                # Покажем что есть в директории
                logger.warning(f"   Work dir contents: {os.listdir(work_dir)}")

            # 6. Ожидаем завершения с таймаутом
            try:
                exit_result = container.wait(timeout=self.timeout)
                logs = container.logs().decode('utf-8', errors='ignore')

                # 7. Читаем результаты анализа
                results_path = os.path.join(work_dir, '_analysis_result.json')
                analysis_data = {}

                if os.path.exists(results_path):
                    with open(results_path, 'r', encoding='utf-8') as f:
                        analysis_data = json.load(f)
                    logger.info(f"Analysis results loaded: {results_path}")

                # 8. Читаем отчёт о покрытии (если есть)
                coverage_data = None
                coverage_path = os.path.join(work_dir, '_coverage_report.json')
                if os.path.exists(coverage_path):
                    with open(coverage_path, 'r', encoding='utf-8') as f:
                        coverage_data = json.load(f)

                container.remove()

                return {
                    'status': 'success',
                    'exit_code': exit_result.get('StatusCode', -1),
                    'logs': logs[-10000:],  # Последние 10KB логов
                    'analysis': analysis_data,
                    'coverage': coverage_data,
                    'resources': analysis_data.get('resources', {})
                }

            except docker.errors.APIError as e:
                container.kill()
                container.remove()
                logger.error(f"Docker API error: {e}")
                return {'status': 'failed', 'error': f'Docker error: {str(e)}'}

            except Exception as e:
                container.kill()
                container.remove()
                logger.error(f"Container execution error: {e}")
                return {'status': 'failed', 'error': f'Execution error: {str(e)}'}

        except docker.errors.DockerException as e:
            logger.error(f"Docker daemon error: {e}")
            return {'status': 'failed', 'error': f'Docker daemon: {str(e)}'}

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return {'status': 'failed', 'error': f'Unexpected: {str(e)}'}

        finally:
            # Очистка временной директории
            if work_dir and os.path.exists(work_dir):
                try:
                    shutil.rmtree(work_dir)
                    logger.debug(f"Cleaned up work directory: {work_dir}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {work_dir}: {e}")

    def _create_simple_analyzer(self) -> str:
        """Простой анализатор с обработкой ошибок для отладки"""
        return '''
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализатор кода с подробным логированием ошибок"""

import ast
import os
import sys
import json
import traceback
from pathlib import Path

APP_DIR = Path('/app')
RESULT_FILE = APP_DIR / '_result.json'

def log(msg):
    """Пишет в stderr чтобы видеть в логах Docker"""
    print(f"[ANALYZER] {msg}", file=sys.stderr, flush=True)

class CodeAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.functions = []
        self.classes = []
        self.imports = set()
        self.total_lines = 0

    def analyze_file(self, filepath):
        try:
            log(f"Reading file: {filepath}")
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                self.total_lines += len(content.splitlines())
                tree = ast.parse(content)
            self.visit(tree)
            log(f"✓ Parsed: {filepath.name}")
            return True
        except SyntaxError as e:
            log(f"✗ SyntaxError in {filepath.name}: {e}")
            return False
        except Exception as e:
            log(f"✗ Error parsing {filepath.name}: {type(e).__name__}: {e}")
            return False

    def visit_FunctionDef(self, node):
        self.functions.append({
            'name': node.name,
            'line': node.lineno,
            'args': [arg.arg for arg in node.args.args if arg.arg != 'self'],
            'is_async': False
        })
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.functions.append({
            'name': node.name,
            'line': node.lineno,
            'args': [arg.arg for arg in node.args.args if arg.arg != 'self'],
            'is_async': True
        })
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.classes.append({'name': node.name, 'line': node.lineno})
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.add(alias.name.split('.')[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.add(node.module.split('.')[0])
        self.generic_visit(node)

    def get_summary(self):
        return {
            'functions_count': len(self.functions),
            'classes_count': len(self.classes),
            'imports_count': len(self.imports),
            'async_functions': len([f for f in self.functions if f['is_async']]),
            'total_lines': self.total_lines,
            'functions': self.functions,
            'classes': self.classes,
            'imports': list(self.imports)
        }

def save_result(result: dict):
    """Сохраняет результат с обработкой ошибок записи"""
    try:
        log(f"Saving results to {RESULT_FILE}")
        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        log(f"✓ Results saved successfully")
        return True
    except Exception as e:
        log(f"✗ Failed to save results: {type(e).__name__}: {e}")
        # Пробуем сохранить в альтернативное место
        try:
            alt_path = Path('/tmp/_result.json')
            with open(alt_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)
            log(f"✓ Results saved to alternative path: {alt_path}")
            return True
        except:
            return False

def main():
    log("🚀 Analyzer starting...")
    log(f"📂 APP_DIR: {APP_DIR}, exists: {APP_DIR.exists()}")

    try:
        # Показать содержимое директории
        try:
            files = [f.name for f in APP_DIR.iterdir()]
            log(f"📁 Files in /app: {files}")
        except Exception as e:
            log(f"⚠️ Could not list /app: {e}")

        # Установка зависимостей (если есть requirements.txt)
        req_file = APP_DIR / 'requirements.txt'
        if req_file.exists():
            log(f"📦 Installing dependencies from {req_file}")
            os.system('pip install -q -r requirements.txt 2>&1')

        # Поиск Python файлов
        python_files = [f for f in APP_DIR.glob('*.py') if f.name not in ('_analyzer.py', '_result.json')]
        log(f"🐍 Found {len(python_files)} Python files: {[f.name for f in python_files]}")

        if not python_files:
            log("⚠️ No Python files to analyze!")
            result = {
                'metrics': {'files_count': 0, 'functions_count': 0, 'classes_count': 0, 'imports_count': 0, 'async_functions': 0, 'total_lines': 0},
                'report': '⚠️ Не найдено файлов для анализа',
                'resources': {'analyzed_files': 0},
                'error': 'No Python files found'
            }
            save_result(result)
            return 0

        # Анализ
        analyzer = CodeAnalyzer()
        for file_path in python_files:
            log(f"📝 Analyzing: {file_path.name}")
            try:
                analyzer.analyze_file(file_path)
            except Exception as e:
                log(f"✗ Exception during analysis: {type(e).__name__}: {e}")
                traceback.print_exc(file=sys.stderr)

        metrics = analyzer.get_summary()
        log(f"📊 Metrics collected: functions={metrics['functions_count']}, classes={metrics['classes_count']}")

        report = f"""=== ОТЧЁТ АНАЛИЗА ===
📁 Файлов: {len(python_files)}
📝 Строк: {metrics['total_lines']}
🔧 Функций: {metrics['functions_count']}
🏗️ Классов: {metrics['classes_count']}
📦 Импортов: {metrics['imports_count']}
⚡ Асинхронных: {metrics['async_functions']}
"""

        result = {
            'metrics': metrics,
            'report': report,
            'resources': {'analyzed_files': len(python_files)},
            'status': 'success'
        }

        # Сохранение
        if save_result(result):
            log("✅ Analysis completed successfully")
            # Вывод в stdout для логов Docker
            print(json.dumps({'status': 'success', 'summary': metrics}, indent=2))
            return 0
        else:
            log("❌ Failed to save results")
            return 1

    except Exception as e:
        log(f"💥 CRITICAL ERROR in main(): {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

        # Сохраняем ошибку в результат
        error_result = {
            'metrics': {},
            'report': f'❌ Ошибка анализа: {type(e).__name__}: {e}',
            'resources': {},
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        save_result(error_result)
        return 2

if __name__ == '__main__':
    sys.exit(main())
'''