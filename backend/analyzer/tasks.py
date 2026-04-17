# backend/analyzer/tasks.py

from celery import shared_task
from django.utils import timezone
import logging

from .models import AnalysisSession, TestGenerationTask
from .utils.docker_runner import DockerRunner
from .utils.code_analyzer import CodeAnalyzer  # Для fallback

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def analyze_project(self, session_id: str):
    """
    Основная задача анализа проекта в изолированном контейнере
    """
    try:
        session = AnalysisSession.objects.get(id=session_id)
        logger.info(f"🚀 Starting analysis for session {session_id}")

        # Обновляем статус
        session.status = 'PROCESSING'
        session.save(update_fields=['status', 'updated_at'])

        # Собираем пути к файлам
        file_paths = [f.file.path for f in session.files.all()]
        if not file_paths:
            raise ValueError("No files uploaded for analysis")

        # Инициализируем раннер
        runner = DockerRunner(timeout=60)

        # Запускаем анализ в контейнере
        logger.info(f"Running container analysis for {len(file_paths)} files")
        result = runner.run_analysis_container(
            file_paths=file_paths,
            python_version=session.python_version,
            dependencies=session.dependencies or [],
            run_tests=False  # Тесты запускаем отдельно при генерации
        )

        # Обработка результатов
        if result['status'] == 'success':
            session.metrics = result.get('analysis', {}).get('metrics', {})
            session.report_text = result.get('analysis', {}).get('report', '')
            session.status = 'ANALYZED'

        else:
            # Fallback на локальный анализ
            logger.warning(f"Container analysis failed, trying fallback: {result.get('error')}")

            from .utils.code_analyzer import CodeAnalyzer
            fallback_analyzer = CodeAnalyzer()
            fallback_result = fallback_analyzer.analyze_code(file_paths)  # ✅ Убедитесь что метод существует!

            session.metrics = fallback_result.get('metrics', {})
            session.report_text = fallback_result.get('report', '')
            session.metrics['fallback_mode'] = True
            session.status = 'ANALYZED'

        session.save(update_fields=['metrics', 'report_text', 'status', 'updated_at'])

        return {
            'status': 'success',
            'session_id': session_id,
            'metrics_summary': {
                'files': session.metrics.get('files_count', 0),
                'functions': session.metrics.get('functions_count', 0)
            }
        }

    except AnalysisSession.DoesNotExist:
        logger.error(f"Session {session_id} not found")
        return {'status': 'failed', 'error': 'Session not found'}

    except Exception as exc:
        logger.error(f"Analysis failed for session {session_id}: {exc}", exc_info=True)

        # Обновляем сессию с ошибкой
        try:
            session = AnalysisSession.objects.get(id=session_id)
            session.status = 'FAILED'
            session.error_message = str(exc)
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        except:
            pass

        # Повторяем задачу с экспоненциальной задержкой
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def generate_tests_task(self, task_id: str):
    """Задача генерации тестов с помощью AI"""
    from .utils.ai_generator import AITestGenerator

    try:
        task = TestGenerationTask.objects.get(id=task_id)
        logger.info(f"🧪 Starting test generation for task {task_id}")

        task.status = 'GENERATING'
        task.save(update_fields=['status', 'updated_at'])

        session = task.session
        generator = AITestGenerator()

        # Собираем код из файлов
        code_content = ""
        for uploaded_file in session.files.all():
            try:
                with open(uploaded_file.file.path, 'r', encoding='utf-8') as f:
                    code_content += f"\n# File: {uploaded_file.original_name}\n{f.read()}"
            except Exception as e:
                logger.warning(f"Could not read file {uploaded_file.original_name}: {e}")
                continue

        if not code_content.strip():
            raise ValueError("No code content available for test generation")

        # Генерация тестов
        logger.info(f"Generating tests with config: {task.config}")
        tests = generator.generate_tests(
            code=code_content[:15000],  # Ограничиваем длину для AI
            metrics=session.metrics,
            config=task.config
        )

        task.generated_tests = tests
        task.status = 'COMPLETED'
        task.save(update_fields=['generated_tests', 'status', 'updated_at'])

        # Обновляем сессию
        session.status = 'TESTS_GENERATED'
        session.save(update_fields=['status', 'updated_at'])

        logger.info(f"✅ Test generation completed: {len(tests)} characters")

        return {
            'status': 'success',
            'task_id': task_id,
            'tests_length': len(tests)
        }

    except TestGenerationTask.DoesNotExist:
        logger.error(f"Task {task_id} not found")
        return {'status': 'failed', 'error': 'Task not found'}

    except Exception as exc:
        logger.error(f"Test generation failed: {exc}", exc_info=True)

        try:
            task = TestGenerationTask.objects.get(id=task_id)
            task.status = 'FAILED'
            task.error_message = str(exc)
            task.save(update_fields=['status', 'error_message', 'updated_at'])
        except:
            pass

        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@shared_task
def cleanup_expired_sessions():
    """Очистка старых сессий (запускается Celery Beat)"""
    from .models import AnalysisSession
    from django.utils import timezone
    from datetime import timedelta

    expiry_date = timezone.now() - timedelta(days=7)
    old_sessions = AnalysisSession.objects.filter(expires_at__lt=expiry_date)

    deleted_count = 0
    for session in old_sessions:
        for uploaded_file in session.files.all():
            try:
                if uploaded_file.file:
                    uploaded_file.file.delete(save=False)
            except Exception as e:
                logger.warning(f"Could not delete file for session {session.id}: {e}")
        session.delete()
        deleted_count += 1

    logger.info(f"🧹 Cleaned up {deleted_count} expired sessions")
    return {'deleted_sessions': deleted_count}