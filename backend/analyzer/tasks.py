# backend/analyzer/tasks.py

from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

from .models import AnalysisSession, TestGenerationTask, UploadedFile
from .utils.docker_runner import DockerRunner
from .utils.code_analyzer import CodeAnalyzer
from .utils.ai_generator import AITestGenerator

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def analyze_project(self, session_id: str):
    """
    Основная задача анализа проекта в изолированном Docker-контейнере.

    При неудаче с Docker автоматически переключается на локальный анализ (fallback).
    """
    try:
        session = AnalysisSession.objects.get(id=session_id)
        logger.info(f"🚀 Starting analysis for session {session_id}")

        # Обновляем статус сессии
        session.status = 'PROCESSING'
        session.save(update_fields=['status', 'updated_at'])

        # Собираем пути к загруженным файлам
        file_paths = [f.file.path for f in session.files.all()]
        if not file_paths:
            raise ValueError("No files uploaded for analysis")

        logger.info(f"📁 Found {len(file_paths)} files for analysis")

        # ✅ Пытаемся запустить анализ в Docker-контейнере
        runner = DockerRunner(timeout=60)
        result = runner.run_analysis_container(
            file_paths=file_paths,
            python_version=session.python_version,
            dependencies=session.dependencies or [],
            run_tests=False
        )

        if result['status'] == 'success':
            analysis_data = result.get('analysis', {})

            session.metrics = analysis_data.get('metrics', {})
            session.metrics['files_count'] = len(file_paths)

            if analysis_data.get('resources'):
                session.metrics['resources'] = analysis_data['resources']

            session.metrics['mode'] = 'docker'

            session.report_text = analysis_data.get('report', '')
            session.status = 'ANALYZED'
            logger.info(f"✅ Docker analysis completed")

        else:
            fallback_analyzer = CodeAnalyzer()
            fallback_result = fallback_analyzer.analyze_code(file_paths)

            # ✅ Добавьте mode в fallback тоже
            fallback_metrics = fallback_result.get('metrics', {})
            fallback_metrics['mode'] = 'fallback'
            fallback_metrics['fallback_reason'] = result.get('error', 'Unknown Docker error')

            session.metrics = fallback_metrics
            session.report_text = fallback_result.get('report', '')
            session.status = 'ANALYZED'

        session.save(update_fields=['metrics', 'report_text', 'status', 'updated_at'])

        return {
            'status': 'success',
            'session_id': session_id,
            'mode': 'docker' if result['status'] == 'success' else 'fallback',
            'metrics_summary': {
                'files': session.metrics.get('files_count', 0),
                'functions': session.metrics.get('functions_count', 0),
                'classes': session.metrics.get('classes_count', 0)
            }
        }

    except AnalysisSession.DoesNotExist:
        logger.error(f"❌ Session {session_id} not found")
        return {'status': 'failed', 'error': 'Session not found'}

    except Exception as exc:
        logger.error(f"❌ Analysis failed for session {session_id}: {exc}", exc_info=True)

        # Обновляем сессию с информацией об ошибке
        try:
            session = AnalysisSession.objects.get(id=session_id)
            session.status = 'FAILED'
            session.error_message = str(exc)
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception as e:
            logger.error(f"Could not update session status: {e}")

        # Повторяем задачу с экспоненциальной задержкой
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def generate_tests_task(self, task_id: str):
    """
    Задача генерации юнит-тестов с помощью локальной AI модели (Ollama).
    """
    try:
        task = TestGenerationTask.objects.get(id=task_id)
        logger.info(f"🧪 Starting test generation for task {task_id}")

        task.status = 'GENERATING'
        task.save(update_fields=['status', 'updated_at'])

        session = task.session
        generator = AITestGenerator()

        # Собираем код из всех файлов сессии
        code_content = ""
        for uploaded_file in session.files.all():
            try:
                with open(uploaded_file.file.path, 'r', encoding='utf-8') as f:
                    code_content += f"\n# File: {uploaded_file.original_name}\n{f.read()}"
            except Exception as e:
                logger.warning(f"⚠️ Could not read file {uploaded_file.original_name}: {e}")
                continue

        if not code_content.strip():
            raise ValueError("No code content available for test generation")

        # Ограничиваем длину кода для AI (чтобы не превысить контекст модели)
        code_content = code_content[:15000]

        logger.info(f"🤖 Generating tests with config: {task.config}")
        tests = generator.generate_tests(
            code=code_content,
            metrics=session.metrics,
            config=task.config
        )

        task.generated_tests = tests
        task.status = 'COMPLETED'
        task.save(update_fields=['generated_tests', 'status', 'updated_at'])

        # Обновляем статус сессии
        session.status = 'TESTS_GENERATED'
        session.save(update_fields=['status', 'updated_at'])

        logger.info(f"✅ Test generation completed: {len(tests)} characters generated")

        return {
            'status': 'success',
            'task_id': task_id,
            'tests_length': len(tests)
        }

    except TestGenerationTask.DoesNotExist:
        logger.error(f"❌ Task {task_id} not found")
        return {'status': 'failed', 'error': 'Task not found'}

    except Exception as exc:
        logger.error(f"❌ Test generation failed: {exc}", exc_info=True)

        try:
            task = TestGenerationTask.objects.get(id=task_id)
            task.status = 'FAILED'
            task.error_message = str(exc)
            task.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception as e:
            logger.error(f"Could not update task status: {e}")

        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@shared_task
def cleanup_expired_sessions():
    """
    Задача очистки устаревших сессий (запускается Celery Beat раз в сутки).
    Удаляет сессии и файлы старше 7 дней.
    """
    from django.utils import timezone
    from datetime import timedelta

    expiry_date = timezone.now() - timedelta(days=7)
    old_sessions = AnalysisSession.objects.filter(expires_at__lt=expiry_date)

    deleted_count = 0
    for session in old_sessions:
        # Удаляем связанные файлы с диска
        for uploaded_file in session.files.all():
            try:
                if uploaded_file.file:
                    uploaded_file.file.delete(save=False)
                    logger.debug(f"🗑️ Deleted file: {uploaded_file.file.path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not delete file for session {session.id}: {e}")

        # Удаляем запись сессии из БД
        session.delete()
        deleted_count += 1
        logger.info(f"🗑️ Cleaned up expired session: {session.id}")

    logger.info(f"🧹 Cleanup completed: {deleted_count} sessions removed")
    return {'deleted_sessions': deleted_count}