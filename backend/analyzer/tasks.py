from celery import shared_task
from django.utils import timezone
from .models import AnalysisSession, TestGenerationTask
from .utils.code_analyzer import CodeAnalyzer
from .utils.ai_generator import AITestGenerator


@shared_task(bind=True, max_retries=3)
def analyze_project(self, session_id):
    try:
        session = AnalysisSession.objects.get(id=session_id)
        session.status = 'PROCESSING'
        session.save(update_fields=['status', 'updated_at'])

        analyzer = CodeAnalyzer()
        file_paths = [f.file.path for f in session.files.all()]

        if not session.dependencies:
            session.dependencies = analyzer.detect_dependencies(file_paths)
            session.save(update_fields=['dependencies'])

        analysis_results = analyzer.analyze_code(file_paths)
        session.metrics = analysis_results.get('metrics', {})
        session.report_text = analysis_results.get('report', '')
        session.status = 'ANALYZED'
        session.save(update_fields=['metrics', 'report_text', 'status', 'updated_at'])

        return {'status': 'success', 'session_id': str(session_id)}

    except Exception as exc:
        session = AnalysisSession.objects.get(id=session_id)
        session.status = 'FAILED'
        session.error_message = str(exc)
        session.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=2)
def generate_tests_task(self, task_id):
    try:
        task = TestGenerationTask.objects.get(id=task_id)
        task.status = 'GENERATING'
        task.save(update_fields=['status', 'updated_at'])

        session = task.session
        generator = AITestGenerator()

        code_content = ""
        for uploaded_file in session.files.all():
            try:
                with open(uploaded_file.file.path, 'r', encoding='utf-8') as f:
                    code_content += f"\n# File: {uploaded_file.original_name}\n{f.read()}"
            except:
                continue

        tests = generator.generate_tests(code=code_content, metrics=session.metrics, config=task.config)

        task.generated_tests = tests
        task.status = 'COMPLETED'
        task.save(update_fields=['generated_tests', 'status', 'updated_at'])

        session.status = 'TESTS_GENERATED'
        session.save(update_fields=['status', 'updated_at'])

        return {'status': 'success', 'task_id': str(task_id)}

    except Exception as exc:
        task = TestGenerationTask.objects.get(id=task_id)
        task.status = 'FAILED'
        task.error_message = str(exc)
        task.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc, countdown=30)


@shared_task
def cleanup_expired_sessions():
    from .models import AnalysisSession
    expired = AnalysisSession.objects.filter(expires_at__lt=timezone.now())
    count = expired.count()
    expired.delete()
    return {'deleted_sessions': count}